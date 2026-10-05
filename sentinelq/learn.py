"""The learning loop: every step can learn from itself, with a person approving every change.

  propose  -> look at a run (and optionally another run / the verified-events file) and PROPOSE lessons. Nothing changes.
  accept   -> a person approves a lesson: it is written into the step's skill (LESSONS.md), a regression test is generated for it,
              and (optionally) a rubric patch is recorded in rubric/learned_patch.json (versioned, hashed, and visible in every run).
  reject   -> recorded with the reason, so the same proposal is not raised again.
The pipeline NEVER changes its own rules or scores. The rubric stays fixed and published; a lesson only changes it after approval, in a file you can read."""
from __future__ import annotations
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "lessons" / "registry.json"
PATCH = ROOT / "rubric" / "learned_patch.json"
SKILLS = ROOT / ".claude" / "skills"
STEP_DIR = {1: "sentinelq-step1-input", 2: "sentinelq-step2-ingest", 3: "sentinelq-step3-classify", 4: "sentinelq-step4-validate",
            5: "sentinelq-step5-score", 6: "sentinelq-step6-report"}
STEP_NAME = {1: "Input", 2: "Ingest", 3: "Classify", 4: "Validate", 5: "Score", 6: "Report"}


# ------------------------------------------------------------------------------------------------------ registry
def load(path: Path = REGISTRY) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save(items: list[dict], path: Path = REGISTRY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def lesson_id(kind: str, key: str) -> str:
    return "L-" + hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:8]


def _mk(kind: str, step: int, key: str, title: str, detail: str, evidence: list[dict], change: str, run: str = "", golden: dict | None = None) -> dict:
    return {"id": lesson_id(kind, key), "kind": kind, "step": step, "title": title, "detail": detail, "evidence": evidence[:8],
            "suggested_change": change, "golden": golden, "status": "proposed", "source_run": run, "created": date.today().isoformat(),
            "tests_ref": [], "note": ""}


# ------------------------------------------------------------------------------------------------------ detectors
def _read(run: Path):
    j = lambda n, d=None: json.loads((run / n).read_text(encoding="utf-8")) if (run / n).exists() else d
    drops = [json.loads(x) for x in (run / "dropped.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (run / "dropped.jsonl").exists() else []
    return j("evidence.json", []), j("scores.json", []), j("run_meta.json", {}), drops, j("lint.json", [])


_BENIGN = re.compile(r"elevat|promot|re-?designat|appointed|appoints|reappoint|succession|superannuat|retire|\bhires?\b|incoming|new role", re.I)
_EXIT = re.compile(r"resign|quit|steps? down|terminat|sacked|removed|ousted|abrupt", re.I)


def detect(run: Path, against: Path | None = None, verified: Path | None = None) -> list[dict]:
    """Rule-based problem finders over one run. They PROPOSE; a person decides."""
    ev, scores, meta, drops, lint = _read(run)
    rid = str(run)
    out: list[dict] = []
    by_row = {(e["symbol"], e["url"] or e["headline"]): e for e in ev}

    for s in scores:
        for p in s["governance_penalties"]:
            row = by_row.get((s["symbol"], p["url"] or p["headline"]), {})
            gate_fields = {k: row.get(k) for k in ("event_type", "subject", "occurred_at_company", "action_stage", "severity", "amount_inr_cr",
                                                   "people_direction", "role_tier", "event_key")}
            e = {"symbol": s["symbol"], "date": p["date"], "headline": p["headline"], "penalty": p["penalty"], "event_type": p["event_type"]}
            if p["event_type"] == "management_exit" and _BENIGN.search(p["headline"]) and not _EXIT.search(p["headline"]) and not row.get("penalty_override"):
                out.append(_mk("benign_people_penalised", 5, f"{s['symbol']}|{p['headline']}", f"{s['symbol']}: a people item that reads as an appointment / elevation was penalised as an exit",
                               f"'{p['headline']}' scored {p['penalty']:+g} as management_exit. Appointments, promotions, reappointments and planned succession are never an exit.",
                               [e], "Add this headline as a regression case; if the gate let it through, tighten the benign-people patterns or the classifier example.", rid,
                               {"headline": p["headline"], "label": gate_fields, "expect_penalty": 0}))
            amt = row.get("amount_inr_cr")
            if amt is not None and amt < 10 and row.get("severity") != "integrity" and p["penalty"] <= -10 and not row.get("penalty_override"):
                out.append(_mk("immaterial_heavy_penalty", 5, f"{s['symbol']}|{p['headline']}", f"{s['symbol']}: Rs {amt:g} cr matter carries a {p['penalty']:+g} penalty",
                               "Non-integrity sums under the Rs 10 cr materiality floor belong in the -5 procedural band.", [e],
                               "Add as a regression case; check the severity / amount the model reported.", rid,
                               {"headline": p["headline"], "label": gate_fields, "expect_penalty": -5}))
        if s.get("sentiment_note", "").startswith("EVIDENCE WINDOW COLLAPSED") or (s.get("evidence_span_days") is not None and s["evidence_span_days"] < 120 and s.get("company_sentiment") is not None):
            out.append(_mk("collapsed_window", 2, s["symbol"], f"{s['symbol']}: evidence covers only {s.get('evidence_span_days')} days of the 12-month lookback",
                           "A short window drops the full-year results prints that anchor +2 / -1 (Reconciliation cause 1).",
                           [{"symbol": s["symbol"], "first": s.get("evidence_first"), "last": s.get("evidence_last"), "span_days": s.get("evidence_span_days")}],
                           "Check the results pass found this company's results articles (aliases? query?); consider widening the pass.", rid))
        if s.get("company_sentiment") is None and s.get("sentiment_note"):
            out.append(_mk("insufficient_data", 2, s["symbol"], f"{s['symbol']}: sentiment is n/a - {s['sentiment_note'][:70]}", "Retrieval was too thin to score; add aliases or widen the search.",
                           [{"symbol": s["symbol"], "relevant_articles": s.get("relevant_articles"), "note": s["sentiment_note"]}],
                           "Add/adjust aliases in portfolio/universe.csv; re-check the news source for this company.", rid))

    n = len(scores)
    flags = [s for s in scores if s["governance_label"] == "Flag"]
    if n >= 10 and len(flags) / n >= 0.30:
        out.append(_mk("flag_inflation", 5, f"{len(flags)}of{n}", f"{len(flags)} of {n} names are Flags - check for over-penalising",
                       "The July v1 card had 14 of 29 Flags, most on a -20 regulatory action. A Flag rate this high usually means misapplied penalties.",
                       [{"symbol": s["symbol"], "score": s["governance_score"], "penalties": [f"{p['event_type']} {p['penalty']:+g}" for p in s["governance_penalties"]]} for s in flags[:8]],
                       "Review each Flag's penalties against the verification gate; add regression cases for any misapplied one.", rid))
    clean = [s for s in scores if s["governance_label"] == "Clean"]
    if n >= 10 and len(clean) / n >= 0.60:
        out.append(_mk("clean_inflation", 5, f"{len(clean)}of{n}", f"{len(clean)} of {n} names are Clean at 100/95 - check for muted or missed penalties",
                       "The reconciled 3-Jul card had 10 of 29 Clean. A Clean rate this high usually means governance events were not retrieved, were "
                       "listed-not-scored by a rule, or verified events expired (see 'NOT applied' on page 1).",
                       [{"symbol": s["symbol"], "score": s["governance_score"], "not_scored": len(s.get("governance_ignored") or [])} for s in clean[:10]],
                       "Open Evidence - Governance for the Clean names: every 'NOT scored' row is a candidate; add missed events to verified_events.csv.", rid))
    sc = [s["company_sentiment"] for s in scores if s.get("company_sentiment") is not None]
    if len(sc) >= 10 and max(sc) <= 1 and min(sc) >= 0:
        out.append(_mk("compressed_sentiment", 2, f"{n}", "Sentiment is compressed to 0 / +1 across the portfolio (no +2, no negative)",
                       "That is the signature of a collapsed evidence window: nothing can reach the +2 anchor without full-year results.",
                       [{"distribution": dict(Counter(sc))}], "Check the results pass and the evidence span per stock.", rid))

    per = {}
    for d in drops:
        per.setdefault(d["symbol"], Counter())[d["reason"].split(":")[0]] += 1
    for sym, cnt in per.items():
        tot = sum(cnt.values())
        if cnt.get("not_about_company", 0) >= 5 and cnt["not_about_company"] / max(tot, 1) >= 0.4:
            out.append(_mk("loose_query", 2, sym, f"{sym}: {cnt['not_about_company']} of {tot} drops were 'not about company' - the search is too loose",
                           "Many retrieved articles were about something else; the query / aliases are not specific enough.",
                           [{"symbol": sym, "drops": dict(cnt)}], "Tighten aliases in portfolio/universe.csv (e.g. 'Titan Company', not 'Titan').", rid))
        if cnt.get("label_failed", 0):
            out.append(_mk("label_failures", 3, sym, f"{sym}: {cnt['label_failed']} item(s) the model could not label", "See work/claude_batches.jsonl for the reason.",
                           [{"symbol": sym, "drops": dict(cnt)}], "Check batch size / workers / the model's reply in the call log.", rid))
    if lint:
        out.append(_mk("price_language", 3, f"{len(lint)}", f"{len(lint)} sentence(s) of share-price reasoning were removed from sentiment commentary",
                       "The writer used price moves as evidence; they were stripped automatically, but the prompt should prevent them.",
                       lint[:5], "Strengthen the PRICE RULE in the narrator/classifier prompt with these examples.", rid))

    if verified and Path(verified).exists():          # human-verified events the model did NOT find on its own
        from .verified import load_verified
        try:
            as_of = date.fromisoformat(meta["as_of"])
            vit, _ = load_verified(verified, as_of, meta["rubric"]["windows"]["news_days"], {s["symbol"] for s in scores})
        except Exception:
            vit = []
        fam = {"regulatory_action": "reg", "investigation": "reg", "exchange_fine": "reg", "management_exit": "people", "board_independence": "people", "rpt_concern": "rpt"}
        for v in vit:
            found = [e for e in ev if e["symbol"] == v.item.symbol and not e.get("verified") and e.get("governance_flag")
                     and fam.get(e["event_type"]) == fam.get(v.label.event_type) and fam.get(v.label.event_type)]
            if not found:
                out.append(_mk("verified_event_missed", 2, f"{v.item.symbol}|{v.label.event_key}", f"{v.item.symbol}: the model did not find the verified event '{v.item.title[:60]}'",
                               "A person verified this against a filing, but the governance search / labelling did not surface anything of the same kind.",
                               [{"symbol": v.item.symbol, "event": v.item.title, "type": v.label.event_type, "source": v.item.source_ref}],
                               "Improve recall: add aliases, keywords to the governance query, or a filings source.", rid))
    if against and Path(against).exists():            # disagreement with another run
        ev_b, sc_b, _, _, _ = _read(Path(against))
        sb = {s["symbol"]: s for s in sc_b}
        for s in scores:
            o = sb.get(s["symbol"])
            if o and o["governance_score"] != s["governance_score"]:
                out.append(_mk("runs_disagree", 5, f"{s['symbol']}|{s['governance_score']}|{o['governance_score']}",
                               f"{s['symbol']}: governance {s['governance_score']:g} here vs {o['governance_score']:g} in the other run",
                               "Union-then-rescore (sentinelq merge) shows the reconciled number; decide which penalties are genuine.",
                               [{"symbol": s["symbol"], "this": [f"{p['event_type']} {p['penalty']:+g}" for p in s["governance_penalties"]],
                                 "other": [f"{p['event_type']} {p['penalty']:+g}" for p in o["governance_penalties"]]}],
                               "Run `sentinelq merge`; add regression cases for the misapplied penalties.", rid))
    return out


# ------------------------------------------------------------------------------------------------------ actions
def propose(run: Path, against: Path | None = None, verified: Path | None = None) -> list[dict]:
    reg = load()
    known = {l["id"]: l for l in reg}
    new = []
    for l in detect(run, against, verified):
        if l["id"] in known:                  # already proposed / accepted / rejected: never raised twice
            continue
        reg.append(l)
        new.append(l)
    save(reg)
    return new


def _get(reg: list[dict], lid: str) -> dict:
    for l in reg:
        if l["id"] == lid:
            return l
    raise SystemExit(f"no lesson {lid!r}; `sentinelq learn review` lists them")


def accept(lid: str, note: str = "", expect_penalty: float | None = None, rubric_patch: dict | None = None, applied_in: str = "") -> dict:
    reg = load()
    l = _get(reg, lid)
    l.update(status="accepted", note=note, accepted=date.today().isoformat(), applied_in=applied_in)
    if l.get("golden") is not None and expect_penalty is not None:
        l["golden"]["expect_penalty"] = None if expect_penalty == 0 else expect_penalty
        l["golden"]["expect_none"] = expect_penalty == 0
    if l.get("golden"):
        path = ROOT / "tests" / "lessons" / f"test_lesson_{lid.lower().replace('-', '_')}.py"
        path.write_text(_golden_test(l), encoding="utf-8")
        l["tests_ref"] = [str(path.relative_to(ROOT))]
    if rubric_patch:
        patch = json.loads(PATCH.read_text(encoding="utf-8")) if PATCH.exists() else {}
        patch = _deep(patch, rubric_patch)
        patch["learned_from"] = sorted(set(patch.get("learned_from", [])) | {lid})
        PATCH.write_text(json.dumps(patch, indent=2) + "\n", encoding="utf-8")
        l["rubric_patch"] = rubric_patch
    save(reg)
    render()
    return l


def reject(lid: str, reason: str) -> dict:
    reg = load()
    l = _get(reg, lid)
    l.update(status="rejected", note=reason, rejected=date.today().isoformat())
    save(reg)
    render()
    return l


def _deep(a: dict, b: dict) -> dict:
    for k, v in b.items():
        a[k] = _deep(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a


def _golden_test(l: dict) -> str:
    g = l["golden"]
    exp = "None" if g.get("expect_none") else repr(g.get("expect_penalty"))
    return f'''"""Regression test generated from lesson {l['id']} ({l['title']}). Accepted {l.get('accepted', '')}. Do not weaken without a new lesson."""
from sentinelq.models import Label, LabelledItem, RawItem
from sentinelq.rubric import load_rubric
from sentinelq.score import gate

LABEL = {repr({k: v for k, v in g["label"].items() if v is not None})}
HEADLINE = {g["headline"]!r}


def test_lesson_{l['id'].lower().replace('-', '_')}():
    r = load_rubric()
    lab = Label(LABEL["event_type"], -1, "lesson", True, None, False, **{{k: v for k, v in LABEL.items() if k != "event_type"}})
    li = LabelledItem(RawItem("X", "news", HEADLINE, "", "https://example.com/lesson", "2026-06-01"), lab)
    penalty, why, _ = gate(li, r)
    expected = {exp}
    assert penalty == expected, (penalty, why)
'''.replace("LABEL = {", "LABEL = {", 1)


# ------------------------------------------------------------------------------------------------------ rendering
def render() -> None:
    """Regenerate each step skill's LESSONS.md and lessons/INDEX.md from the registry."""
    reg = load()
    for step, folder in STEP_DIR.items():
        d = SKILLS / folder
        if not d.exists():
            continue
        acc = [l for l in reg if l["step"] == step and l["status"] == "accepted"]
        pend = [l for l in reg if l["step"] == step and l["status"] == "proposed"]
        lines = [f"# Lessons - Step {step} {STEP_NAME[step]}", "",
                 "Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.", ""]
        for l in sorted(acc, key=lambda x: x.get("accepted", "")):
            lines += [f"## {l['id']} - {l['title']}", f"- accepted {l.get('accepted', '')}; source: {l.get('source_run') or l.get('source', '')}",
                      f"- {l['detail']}", f"- change: {l['suggested_change']}"]
            if l.get("note"):
                lines.append(f"- note: {l['note']}")
            if l.get("tests_ref"):
                lines.append("- tests: " + ", ".join(f"`{t}`" for t in l["tests_ref"]))
            lines.append("")
        if not acc:
            lines += ["(none yet)", ""]
        if pend:
            lines += [f"## Proposed, awaiting a person ({len(pend)})", ""] + [f"- {l['id']}: {l['title']}" for l in pend] + [""]
        (d / "LESSONS.md").write_text("\n".join(lines), encoding="utf-8")
    c = Counter(l["status"] for l in reg)
    idx = ["# Lessons index", "", f"accepted {c['accepted']} | proposed {c['proposed']} | rejected {c['rejected']}", "",
           "| id | step | status | title |", "|---|---|---|---|"]
    idx += [f"| {l['id']} | {l['step']} {STEP_NAME[l['step']]} | {l['status']} | {l['title']} |" for l in sorted(reg, key=lambda x: (x['step'], x['id']))]
    (ROOT / "lessons" / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------------------------------------ seed: what has already been learned
SEEDS = [
    ("backdated_live_search", 2, "A backdated live search is not a point-in-time backtest", "Re-running a live news index with a past cut-off loses the articles that dropped out of feeds; ~20 sentiment scores moved (Reconciliation cause 1).",
     "Past as-of date -> dated archive (GDELT) via --news auto; a live index with a past date is refused or stamped.", ["tests/test_reconciliation.py::test_backdated_live_search_is_refused_or_stamped_and_auto_uses_the_archive", "tests/test_reconciliation.py::test_backdated_live_run_is_stamped_in_the_pdf"]),
    ("collapsed_window", 2, "Sentiment needs the 12-month evidence window, not only the latest weeks", "With only the last 2-6 weeks nothing can reach +2 and a 40% revenue fall cannot reach -1.",
     "Added a separate 12-month results pass and measured the evidence span per stock.", ["tests/test_reconciliation.py::test_fundamentals_pass_brings_the_full_year_results_prints_into_sentiment"]),
    ("direction_errors", 5, "Appointments, promotions and succession are never a management exit", "Seven penalties were direction errors (AIA, 3M, Dr Reddy's, Titan, Nestle, AU SFB, Hindustan Copper).",
     "Verification gate + text safety net for people items.", ["tests/test_reconciliation.py::test_direction_error_is_not_penalised_when_the_gate_is_answered_correctly", "tests/test_reconciliation.py::test_direction_error_is_caught_even_if_the_model_gets_the_gate_wrong"]),
    ("subject_errors", 5, "The company must be the subject of a penalised event", "Macro duty hike, sector rule, compliance statement and a defrauded bank were penalised as company regulatory action / investigation.",
     "Subject / victim / macro rules in the gate.", ["tests/test_reconciliation.py::test_subject_error_is_not_penalised", "tests/test_reconciliation.py::test_subject_error_is_caught_by_text_rules_when_the_model_omits_the_subject"]),
    ("materiality", 5, "Immaterial sums sit in the -5 procedural band", "Rs 1.64 cr (Eicher) and Rs 64 lakh (Bank of Maharashtra) were scored at the -20 weight.",
     "Rs 10 cr materiality floor for non-integrity matters; integrity-type (SEBI conduct) exempt.", ["tests/test_reconciliation.py::test_eicher_rs_1_64_cr_customs_demand_is_procedural_not_minus_20", "tests/test_reconciliation.py::test_integrity_type_matters_ignore_the_floor_angel_one_sebi_settlement_rs_4_28_cr"]),
    ("same_event", 5, "One real-world event is one event, even when reported on several days / as several types", "Nestle's FSSAI notice (14-Jun) and 'probe' (15-Jun) were both penalised; the real model later gave them different wording and keys.",
     "Same-event de-duplication within a family (7 days, shared distinctive words or key); different families (fraud probe vs CFO termination) stay separate.", ["tests/test_reconciliation.py::test_nestle_double_count_survives_different_wording_and_different_keys", "tests/test_reconciliation.py::test_two_different_regulators_within_a_week_are_not_merged"]),
    ("price_language", 3, "Share-price moves never feed sentiment", "The July output cited 52-week highs, % moves and sell-offs as evidence.",
     "price_move event type, prompt rule, and a lint that strips price sentences from sentiment commentary.", ["tests/test_reconciliation.py::test_price_only_headlines_are_not_sentiment_evidence"]),
    ("insufficient_data", 4, "Report Insufficient Data rather than a forced +1 or 0", "Below 8 relevant articles, or with no results print in 12 months, sentiment was being forced.",
     "Minimum evidence standard in the rubric.", ["tests/test_reconciliation.py::test_insufficient_data_not_a_forced_score", "tests/test_reconciliation.py::test_pipeline_reports_n_a_not_plus_one"]),
    ("universe", 1, "A holding must never appear or disappear silently", "JB Chemicals vanished from the team run's universe (30 -> 29).",
     "Universe hash locked against the last confirmed run; --confirm-universe / --expect-count.", ["tests/test_reconciliation.py::test_universe_change_refuses_until_confirmed"]),
    ("memory_discount", 5, "The -5 memory discount applies only when nothing in the window was penalised", "Kajaria's memory discount was kept alongside in-window penalties.",
     "Convention in the rubric and code.", ["tests/test_reconciliation.py::test_memory_discount_only_when_no_in_window_penalty_exists"]),
    ("action_repeats", 5, "One corporate action per declaration", "375 headlines (141 dividend) were counted as separate actions.",
     "Collapse repeat headlines by type + figures within a window.", ["tests/test_reconciliation.py::test_corporate_action_repeat_headlines_count_once"]),
    ("union_rescore", 6, "When two implementations differ, take the union of verified evidence and re-score once", "Averaging or picking a side hides the real disagreement.",
     "`sentinelq merge` + portfolio/verified_events.csv; verified events reproduce the reconciled scores.", ["tests/test_reconciliation.py::test_merge_is_union_then_rescore_never_an_average", "tests/test_reconciliation.py::test_verified_events_reproduce_the_reconciled_governance_scores"]),
]


def seed() -> int:
    reg = load()
    have = {l["id"] for l in reg}
    n = 0
    for kind, step, title, detail, change, tests in SEEDS:
        lid = lesson_id("seed", kind)
        if lid in have:
            continue
        l = _mk("seed:" + kind, step, kind, title, detail, [], change, "Reconciliation 1-Oct-2026 s5-s7")
        l.update(id=lid, status="accepted", accepted="2026-10-01", tests_ref=tests, note="Seeded from the Reconciliation document.")
        reg.append(l)
        n += 1
    save(reg)
    render()
    return n
