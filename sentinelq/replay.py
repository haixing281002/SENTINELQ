"""B4 - commands over the corpus of record: replay / relabel / backtest / diff / golden.

  replay   rescore from stored events at an as_of: no network, no LLM. Point-in-time rule (B3) decides what is visible.
  relabel  re-label stored headline + snippet under a new prompt / model label: LLM only, no scraping.
  backtest pit-replay at each weekly as_of between two dates; asserts in code that no article fetched after as_of + 7 days is used.
  diff     name-by-name score and label diff between two runs, with a driver code per divergence (R retrieval window, C classifier
           false positive / missed event, M materiality / weight, P price leakage, U universe, L label disagreement).
  golden   the B5 regression set; exit non-zero on failure."""
from __future__ import annotations
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .classify import PROMPT_VERSION, to_label
from .corpus import Corpus, _loads, make_run_id
from .models import Holding, Label, LabelledItem, RawItem, StockScore
from .rubric import Rubric, load_rubric
from .score import score_portfolio


# ------------------------------------------------------------------------------------------------------ helpers
def event_row_to_item(e: dict) -> LabelledItem:
    gate = _loads(e.get("gate_json")) or {}
    it = RawItem(e["symbol"], "news", e.get("headline") or "", e.get("snippet") or "", e.get("representative_url") or "", (e.get("event_date") or "")[:10],
                 "", purpose=e.get("purpose") or ("sentiment" if e.get("pass") in ("sentiment", "results") else "governance"),
                 source_ref=e.get("source_ref") or "", verified=e.get("verified") or "", penalty_override=e.get("penalty_override"),
                 origin=e.get("pass") or "", window=e.get("window"), pass_=e.get("pass") or "", event_id=e.get("event_id") or "",
                 event_date=(e.get("event_date") or "")[:10], n_sources=int(e.get("n_sources") or 1), n_members=int(e.get("n_members") or 1),
                 max_tier=e.get("max_tier") or "", confidence=e.get("confidence"), member_url_hashes=_loads(e.get("member_url_hashes")) or [],
                 members=_loads(e.get("members_json")) or [])
    if it.window is not None:
        it.window = int(it.window)
    lab = Label(e["event_type"], int(e["sentiment"]), e.get("justification") or "", bool(e.get("is_governance_flag")), e.get("materiality"),
                bool(e.get("historical")), True, subject=gate.get("subject"), occurred_at_company=gate.get("occurred_at_company"),
                action_stage=gate.get("action_stage"), severity=gate.get("severity"), amount_inr_cr=gate.get("amount_inr_cr"),
                people_direction=gate.get("people_direction"), role_tier=gate.get("role_tier"), event_key=gate.get("event_key"))
    return LabelledItem(it, lab, from_cache=True)


def _rubric_arg(x: str | None) -> Rubric:
    if not x:
        return load_rubric()
    p = Path(x)
    if p.exists():
        return load_rubric(p)
    cand = Path(__file__).resolve().parent.parent / "rubric" / f"rubric_{x.lstrip('v').replace('.', '_')}.json"
    if cand.exists():
        return load_rubric(cand)
    raise SystemExit(f"rubric {x!r}: not a file and rubric/{cand.name} does not exist")


def _holdings_from(corpus: Corpus, run_ids: list[str], symbols: set[str]) -> list[Holding]:
    out, seen = [], set()
    for m in reversed(corpus.manifests()):
        if m["run_id"] not in run_ids:
            continue
        for h in m.get("holdings") or []:
            if h["symbol"] not in seen:
                seen.add(h["symbol"])
                out.append(Holding(h["symbol"], h.get("name") or h["symbol"], h.get("sector") or "", h.get("cap") or "", h.get("weight") or "", h.get("aliases") or ""))
    for s in sorted(symbols - seen):
        out.append(Holding(s, s, ""))
    return out


def _visible(corpus: Corpus, as_of: date, strict: bool) -> tuple[list[dict], list[str], str]:
    """Events visible at as_of and the runs they came from. strict = genuine point-in-time (fetched no later than as_of + grace)."""
    from .config import PIT_GRACE_DAYS
    limit = as_of + timedelta(days=PIT_GRACE_DAYS)
    runs, modes = [], []
    for m in corpus.manifests():
        if not m.get("as_of") or date.fromisoformat(m["as_of"]) > limit or m.get("source_mode") == "pit-replay" or not m.get("n_events"):
            continue
        fetched = m.get("started_at") or m.get("finished_at") or ""
        if strict and fetched and fetched[:10] > limit.isoformat():
            continue                                  # look-ahead: fetched after the as_of window -> refused
        runs.append(m["run_id"])
        modes.append(m.get("source_mode", ""))
    rids = set(runs)
    seen: dict[str, dict] = {}
    for e in corpus.events():
        if e.get("run_id") in rids and (e.get("event_date") or "")[:10] <= as_of.isoformat() and (e.get("event_date") or ""):
            seen[e["event_id"]] = e
    start = corpus.start()
    if "backdated-live-NOT-PIT" in modes:
        mode = "backdated-live-NOT-PIT"
    elif start is not None and as_of >= start:
        mode = "pit-replay"
    else:
        mode = "archive-gdelt"
    return list(seen.values()), runs, mode


def _write_run_dir(out: Path, scores: list[StockScore], by: dict, meta: dict, title: str, coverage: str) -> None:
    from .narrate import TemplateNarrator, portfolio_facts
    from .pdf import build_pdf
    out.mkdir(parents=True, exist_ok=True)
    (out / "scores.json").write_text(json.dumps([s.to_dict() for s in scores], indent=2), encoding="utf-8")
    (out / "evidence.json").write_text(json.dumps([li.to_row() for v in by.values() for li in v], indent=2), encoding="utf-8")
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    nar = {s.symbol: TemplateNarrator().holding(s, by[s.symbol]) for s in scores}
    obs = TemplateNarrator().portfolio(portfolio_facts(scores, by))
    (out / "narrative.json").write_text(json.dumps({"holdings": nar, "observations": obs}, indent=2), encoding="utf-8")
    build_pdf(out / "sentinelq_scorecard.pdf", {"run": meta, "scores": scores, "kept": by}, Rubric(meta["rubric"], meta["rubric_sha256"]), nar, obs,
              {"title": title, "coverage": coverage})


# ------------------------------------------------------------------------------------------------------ replay
def replay(as_of: date, rubric: str | None = None, corpus_dir: str | Path | None = None, out: str | Path | None = None, verified: str | None = "portfolio/verified_events.csv",
           title: str = "Portfolio", strict: bool = False, record: bool = True, quiet: bool = False) -> dict:
    corpus = Corpus(corpus_dir)
    r = _rubric_arg(rubric)
    rows, runs, mode = _visible(corpus, as_of, strict)
    if not rows:
        raise SystemExit(f"replay: no stored events are visible at {as_of} in {corpus.root} (corpus start: {corpus.start()}). "
                         "Run the pipeline first; a replay never fetches.")
    by: dict[str, list[LabelledItem]] = {}
    for e in rows:
        if e.get("pass") == "verified":
            continue                                  # human-verified evidence is re-read from the file so edits apply
        by.setdefault(e["symbol"], []).append(event_row_to_item(e))
    holdings = _holdings_from(corpus, runs, set(by))
    for h in holdings:
        by.setdefault(h.symbol, [])
    n_v = 0
    if verified and Path(verified).exists():
        from .verified import load_verified
        vit, _ = load_verified(verified, as_of, r["windows"]["news_days"], set(by))
        for li in vit:
            by[li.item.symbol].append(li)
        n_v = len(vit)
    dropped = {}
    for m in corpus.manifests():                      # the run's own drop counts, so a replay reproduces the scores table exactly
        if m["run_id"] in runs:
            dropped.update(m.get("dropped_counts") or {})
    scores = score_portfolio(holdings, by, dropped, as_of, r)
    run_id = make_run_id(as_of, datetime.now()).replace("_", "_replay_", 1)
    from .golden import run_golden
    golden = run_golden(r, None, corpus=corpus)
    stamp = (f"{mode} · as_of {as_of.isoformat()} · replay of {len(runs)} run(s) · prompt {PROMPT_VERSION} · rubric v{r.version} ({r.sha256[:8]}) · "
             f"model_label stored · model_verify python-gate")
    meta = {"as_of": as_of.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "source_mode": mode,
            "rubric_version": r.version, "rubric_sha256": r.sha256, "rubric": r.data, "classifier_model": "stored events (no model call)",
            "prompt_version": PROMPT_VERSION, "mode": "absolute", "retrieval": {"mode": "replay", "as_of": as_of.isoformat(), "run_date": date.today().isoformat(),
            "note": f"Replayed from the corpus of record ({mode}): {len(rows)} stored event(s) from run(s) {', '.join(runs)}; no network, no model call."},
            "replay_of": runs, "n_events": len(rows), "n_verified": n_v, "stamp": stamp, "golden": golden, "published": golden["passed"],
            "title": title, "coverage": f"{len(holdings)} holdings", "universe_hash": "", "verified": {"file": verified or "", "applied": n_v},
            "totals": {"retrieved": 0, "kept": sum(len(v) for v in by.values()), "dropped": 0, "label_failed": 0}, "n_llm_items": 0,
            "sampler": "replay"}
    out = Path(out or f"runs/replay_{as_of.isoformat()}")
    _write_run_dir(out, scores, by, meta, title, meta["coverage"])
    if record:
        srows = [{"run_id": run_id, "as_of": as_of.isoformat(), "symbol": s.symbol, "rubric_version": r.version, "rubric_sha256": r.sha256,
                  "sentiment_raw": s.company_sentiment_raw, "sentiment_bucket": s.company_sentiment, "gov_score": s.governance_score, "gov_label": s.governance_label,
                  "penalties_json": s.governance_penalties, "coverage_json": {"map": s.coverage_map, "windows": s.window_events, "n_events": s.n_events},
                  "insufficient": s.sentiment_note.startswith("INSUFFICIENT"), "triage": s.sentiment_note, "published": golden["passed"], "source_mode": mode,
                  "prompt_version": PROMPT_VERSION, "model_label": "stored"} for s in scores]
        corpus.write_run(run_id, {"as_of": as_of.isoformat(), "started_at": meta["generated_at"], "finished_at": meta["generated_at"], "source_mode": "pit-replay" if mode == "pit-replay" else mode,
                                  "replay": True, "replay_of": runs, "rubric_version": r.version, "rubric_sha256": r.sha256, "prompt_version": PROMPT_VERSION,
                                  "model_label": "stored", "model_verify": "python-gate", "n_articles": 0, "n_events": 0, "n_llm_calls": 0, "est_cost_inr": 0.0,
                                  "n_scores": len(srows), "published": golden["passed"], "warnings": [] if golden["passed"] else ["golden failed"],
                                  "holdings": [h.__dict__ for h in holdings], "out_dir": str(out)}, [], [], [], srows)
    if not quiet:
        print(f"replay {as_of} [{mode}] from {len(runs)} stored run(s): {len(rows)} events, {n_v} verified -> {out}/ (rubric v{r.version} {r.sha256[:8]})")
        for s in scores:
            print(f"  {s.symbol:<12} sent={s.company_sentiment!s:>5}  gov={s.governance_score:g}/{s.governance_label:<5}  {s.coverage_map}")
    return {"scores": scores, "mode": mode, "runs": runs, "out": out, "meta": meta}


# ------------------------------------------------------------------------------------------------------ relabel
def relabel(as_of: date, prompt: str | None, classifier, corpus_dir=None, model_label: str | None = None, quiet: bool = False) -> dict:
    """Re-label the stored headline + snippet of every event visible at as_of (representatives only). No scraping."""
    corpus = Corpus(corpus_dir)
    rows, runs, mode = _visible(corpus, as_of, strict=False)
    if not rows:
        raise SystemExit(f"relabel: no stored events are visible at {as_of} in {corpus.root}")
    pv = prompt or PROMPT_VERSION
    if pv != PROMPT_VERSION and not quiet:
        print(f"NOTE: prompt_version is recorded as {pv!r}; the labelling prompt text in classify.py is {PROMPT_VERSION} (edit classify.py to change the words).")
    ml = model_label or classifier.model_id
    names = {}
    for m in corpus.manifests():
        for h in m.get("holdings") or []:
            names[h["symbol"]] = h.get("name") or h["symbol"]
    todo = [(event_row_to_item(e).item, names.get(e["symbol"], e["symbol"]), e) for e in rows if e.get("pass") != "verified"]
    if hasattr(classifier, "prefetch"):
        classifier.prefetch([(it, c) for it, c, _ in todo])
    now = datetime.now().isoformat(timespec="seconds")
    out_rows, failed = [], 0
    for it, company, e in todo:
        d, raw = classifier.classify(it, company)
        if d is None:
            failed += 1
            continue
        lab = to_label(d)
        gate = {k: getattr(lab, k) for k in ("subject", "occurred_at_company", "action_stage", "severity", "amount_inr_cr", "people_direction", "role_tier", "event_key")}
        out_rows.append({**e, "event_type": lab.event_type, "sentiment": lab.sentiment, "is_governance_flag": lab.governance_flag,
                         "governance_flag_type": lab.event_type if lab.governance_flag else None, "justification": lab.rationale, "gate_json": gate,
                         "materiality": lab.materiality, "historical": lab.historical, "prompt_version": pv, "model_label": ml, "labelled_at": now})
    run_id = make_run_id(as_of, datetime.now()).replace("_", "_relabel_", 1)
    for x in out_rows:
        x["run_id"] = run_id
    corpus.write_run(run_id, {"as_of": as_of.isoformat(), "started_at": now, "finished_at": datetime.now().isoformat(timespec="seconds"), "source_mode": mode,
                              "relabel": True, "relabel_of": runs, "prompt_version": pv, "model_label": ml, "model_verify": "python-gate", "n_articles": 0,
                              "n_events": len(out_rows), "n_llm_calls": len(todo), "est_cost_inr": 0.0, "n_scores": 0, "published": False,
                              "warnings": [f"{failed} item(s) failed to label"] if failed else [], "holdings": [{"symbol": s, "name": n} for s, n in names.items()]},
                     [], out_rows, [], [])
    if not quiet:
        print(f"relabel {as_of}: {len(out_rows)} event(s) re-labelled under prompt {pv} / model {ml} ({failed} failed) -> corpus run {run_id}")
    return {"run_id": run_id, "n": len(out_rows), "failed": failed}


# ------------------------------------------------------------------------------------------------------ backtest
def backtest(start: date, end: date, corpus_dir=None, out: str | Path | None = None, rubric: str | None = None, weekly: bool = True, quiet: bool = False) -> list[dict]:
    """pit-replay at each weekly as_of. Asserts (B7.3) that no article fetched later than as_of + 7 days is used: strict visibility."""
    from .config import PIT_GRACE_DAYS
    corpus = Corpus(corpus_dir)
    out = Path(out or f"runs/backtest_{start.isoformat()}_{end.isoformat()}")
    out.mkdir(parents=True, exist_ok=True)
    rows, d = [], start
    while d <= end:
        try:
            res = replay(d, rubric, corpus_dir, out / d.isoformat(), strict=True, record=False, quiet=True)
        except SystemExit as e:
            rows.append({"as_of": d.isoformat(), "mode": "no-data", "note": str(e)[:120]})
            d += timedelta(days=7 if weekly else 1)
            continue
        limit = (d + timedelta(days=PIT_GRACE_DAYS)).isoformat()
        for m in corpus.manifests():               # assert in code: nothing used was fetched after as_of + grace
            if m["run_id"] in res["runs"]:
                assert (m.get("started_at") or "")[:10] <= limit, f"look-ahead: run {m['run_id']} fetched {m.get('started_at')} > {limit}"
        assert res["mode"] != "pit-replay" or corpus.start() <= d
        for s in res["scores"]:
            rows.append({"as_of": d.isoformat(), "mode": res["mode"], "symbol": s.symbol, "sentiment": s.company_sentiment, "sentiment_raw": s.company_sentiment_raw,
                         "governance": s.governance_score, "label": s.governance_label, "events": s.n_events, "coverage": s.coverage_map})
        d += timedelta(days=7 if weekly else 1)
    with (out / "backtest.csv").open("w", newline="", encoding="utf-8") as f:
        keys = ["as_of", "mode", "symbol", "sentiment", "sentiment_raw", "governance", "label", "events", "coverage", "note"]
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in keys} for r in rows])
    if not quiet:
        print(f"backtest {start}..{end}: {len({r['as_of'] for r in rows})} as_of date(s) -> {out}/backtest.csv")
    return rows


# ------------------------------------------------------------------------------------------------------ diff
def _load_scores(ref: str, corpus_dir=None) -> tuple[list[dict], list[dict], dict]:
    p = Path(ref)
    if p.is_dir() and (p / "scores.json").exists():
        ev = json.loads((p / "evidence.json").read_text(encoding="utf-8")) if (p / "evidence.json").exists() else []
        meta = json.loads((p / "run_meta.json").read_text(encoding="utf-8")) if (p / "run_meta.json").exists() else {}
        return json.loads((p / "scores.json").read_text(encoding="utf-8")), ev, meta
    c = Corpus(corpus_dir)
    rows = c.scores([ref])
    if not rows:
        raise SystemExit(f"diff: {ref!r} is neither a run directory with scores.json nor a corpus run_id")
    sc = [{"symbol": x["symbol"], "company_sentiment": x["sentiment_bucket"], "company_sentiment_raw": x["sentiment_raw"], "governance_score": x["gov_score"],
           "governance_label": x["gov_label"], "governance_penalties": _loads(x["penalties_json"]) or [], "sentiment_note": x.get("triage") or "",
           "evidence_span_days": None, "coverage_map": (_loads(x.get("coverage_json")) or {}).get("map", "")} for x in rows]
    return sc, [], next((m for m in c.manifests() if m["run_id"] == ref), {})


def diff(run_a: str, run_b: str, corpus_dir=None, out: str | Path | None = None, quiet: bool = False) -> dict:
    sa, ea, ma = _load_scores(run_a, corpus_dir)
    sb, eb, mb = _load_scores(run_b, corpus_dir)
    A, B = {s["symbol"]: s for s in sa}, {s["symbol"]: s for s in sb}
    lint_a, lint_b = bool((ma.get("lint") or [])), bool((mb.get("lint") or []))
    rows, agree_s, agree_g, agree_l, n = [], 0, 0, 0, 0
    for sym in sorted(set(A) | set(B)):
        a, b = A.get(sym), B.get(sym)
        if a is None or b is None:
            rows.append({"symbol": sym, "A": _fmt(a), "B": _fmt(b), "drivers": "U (universe: only in " + ("A" if a else "B") + ")"})
            continue
        n += 1
        drivers = []
        if a["governance_score"] != b["governance_score"]:
            pa = {p["event_type"]: p["penalty"] for p in a["governance_penalties"]}
            pb = {p["event_type"]: p["penalty"] for p in b["governance_penalties"]}
            only_a = [f"{t} {pa[t]:+g}" for t in pa if t not in pb]
            only_b = [f"{t} {pb[t]:+g}" for t in pb if t not in pa]
            both = [f"{t} {pa[t]:+g}->{pb[t]:+g}" for t in pa if t in pb and pa[t] != pb[t]]
            if only_a:
                drivers.append("C (only in A: " + ", ".join(only_a) + ")")
            if only_b:
                drivers.append("C (only in B: " + ", ".join(only_b) + ")")
            if both:
                drivers.append("M (" + ", ".join(both) + ")")
        else:
            agree_g += 1
        if a["governance_label"] == b["governance_label"]:
            agree_l += 1
        if a["company_sentiment"] != b["company_sentiment"]:
            sp_a, sp_b = a.get("evidence_span_days"), b.get("evidence_span_days")
            if (a.get("sentiment_note") or "").startswith(("INSUFFICIENT", "EVIDENCE WINDOW")) or (b.get("sentiment_note") or "").startswith(("INSUFFICIENT", "EVIDENCE WINDOW")) \
                    or (sp_a is not None and sp_b is not None and abs(sp_a - sp_b) > 90):
                drivers.append(f"R (window A {sp_a} d vs B {sp_b} d)")
            elif lint_a or lint_b:
                drivers.append("P (price language removed from a rationale)")
            else:
                drivers.append(f"L (labels: raw {a.get('company_sentiment_raw')} vs {b.get('company_sentiment_raw')})")
        else:
            agree_s += 1
        rows.append({"symbol": sym, "A": _fmt(a), "B": _fmt(b), "drivers": "; ".join(drivers) or "agree"})
    summary = {"n": n, "sentiment_agreement": round(agree_s / n, 3) if n else None, "governance_agreement": round(agree_g / n, 3) if n else None,
               "label_agreement": round(agree_l / n, 3) if n else None, "rows": rows}
    md = ["# Sentinel Q diff", "", f"- A: `{run_a}`", f"- B: `{run_b}`",
          f"- Agreement over {n} common names: sentiment {summary['sentiment_agreement']}, governance score {summary['governance_agreement']}, label {summary['label_agreement']}",
          "- Driver codes: R retrieval window · C classifier (false positive / missed event, side noted) · M materiality / weight · P price leakage · U universe · L label disagreement",
          "", "| symbol | A: S / G / label | B: S / G / label | drivers |", "|---|---|---|---|"]
    md += [f"| {r['symbol']} | {r['A']} | {r['B']} | {r['drivers']} |" for r in rows]
    text = "\n".join(md) + "\n"
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    if not quiet:
        print(text)
    return summary


def _fmt(s: dict | None) -> str:
    if s is None:
        return "-"
    cs = s["company_sentiment"]
    return f"{cs if cs is not None else 'n/a'} / {s['governance_score']:g} / {s['governance_label']}"
