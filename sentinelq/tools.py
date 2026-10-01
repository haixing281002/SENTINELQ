"""Manual-verification tools, one per concern:
  python -m sentinelq inspect input  --portfolio FILE | --pick "A,B"       step 1: what was read, with sector/cap/aliases
  python -m sentinelq inspect news   --stock SYMBOL (--portfolio|--pick) [--source gnews|gdelt] [--max-articles N] [--as-of D]
                                                                            step 2: fetch + select for ONE stock, in memory, nothing saved
  python -m sentinelq inspect label  --company NAME --headline TEXT [--classifier claude-code|keyword]
                                                                            steps 3+4: label one headline and show the validation verdict
  python -m sentinelq verify RUN_DIR                                        step 5: recompute every score from the saved evidence and compare
  python -m sentinelq render RUN_DIR                                        step 6: rebuild the PDF from the saved scores + (editable) narrative.json
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .models import Holding, Label, LabelledItem, RawItem, StockScore
from .rubric import Rubric, load_rubric
from .score import score_portfolio


def _holdings(a) -> list[Holding]:
    from .pipeline import load_portfolio, pick_holdings
    from .resolve import enrich
    h = pick_holdings(a.universe, [x.strip() for x in a.pick.split(",") if x.strip()]) if a.pick else load_portfolio(a.portfolio)
    return enrich(h, a.universe, mode="none")


def inspect_input(a) -> int:
    hs = _holdings(a)
    print(f"[STEP 1/6 INPUT] {len(hs)} stock(s)")
    print(f"  {'symbol':<14}{'company':<38}{'sector':<18}{'cap':<7}aliases used for news search")
    for h in hs:
        print(f"  {h.symbol:<14}{h.name:<38}{h.sector:<18}{h.cap:<7}{h.aliases or '(name, name Ltd, name Limited)'}")
    return 0


def inspect_news(a) -> int:
    from .ingest.select import count_usable, name_tokens, select_articles, select_governance
    hs = [h for h in _holdings(a) if h.symbol.lower() == a.stock.lower()]
    if not hs:
        print(f"stock {a.stock!r} not in the portfolio given", file=sys.stderr)
        return 2
    h, as_of = hs[0], date.fromisoformat(a.as_of) if a.as_of else date.today()
    if a.source == "gdelt":
        from .ingest.gdelt import GdeltNews
        src = GdeltNews()
    else:
        from .ingest.gnews import GoogleNewsRSS
        src = GoogleNewsRSS()
    src.on_event = lambda m: print("  " + m)
    toks = name_tokens(h)
    src.stop_when = (lambda its: count_usable(its, toks) >= a.max_articles) if a.max_articles else None
    start = as_of - timedelta(days=365)
    print(f"[STEP 2/6 INGEST] {h.symbol} ({h.name}) as-of {as_of}, newest first, titles must name: {', '.join(toks[:6])}")
    raw = src.fetch(h, start, as_of)
    sel = select_articles(raw, a.max_articles, toks)
    print(f"\n  retrieved {len(raw)}; kept {len([i for i in sel if i.kind == 'news'])} (latest {a.max_articles} whose title names the company)")
    for i in sel[: a.show]:
        print(f"  {i.published}  {i.source[:26]:<26} {i.title[:92]}")
    if hasattr(src, "fetch_governance"):
        gov = select_governance(src.fetch_governance(h, start, as_of), sel, 30, toks)
        print(f"\n  governance pass (12 months, separate search): {len(gov)} candidate(s)")
        for i in gov[: a.show]:
            print(f"  {i.published}  {i.source[:26]:<26} {i.title[:92]}")
    print(f"\n  {len(src.audit)} request(s) made; nothing was saved.")
    return 0


def inspect_label(a) -> int:
    from .classify import KeywordClassifier, to_label
    from .validate import validate_label
    r = load_rubric()
    item = RawItem("X", "news", a.headline, a.text or "", a.url, a.date, "inspect")
    if a.classifier == "keyword":
        clf = KeywordClassifier()
    else:
        from .claude_code import ClaudeCodeClassifier, preflight
        preflight()
        clf = ClaudeCodeClassifier(r, log_path="work/claude_batches.jsonl")
    print(f"[STEP 3/6 CLASSIFY] {clf.model_id}: {a.headline!r}  (company: {a.company})")
    lab, raw = clf.classify(item, a.company)
    print("  raw label:", json.dumps(lab, ensure_ascii=False))
    verdict = validate_label(item, lab, r, date.fromisoformat(a.date), 365)
    print(f"[STEP 4/6 VALIDATE] {'PASS' if verdict is None else 'DROP: ' + verdict}")
    if verdict is None:
        from .score import literal_rule_override
        why = literal_rule_override(LabelledItem(item, to_label(lab), raw))
        print(f"  governance: {'NOT penalised - ' + why if why else 'eligible for the rubric penalty for ' + lab['event_type'] if lab['event_type'] in r['governance']['penalties'] else 'no governance penalty for this type'}")
    return 0


def _load_run(run: Path):
    ev = json.loads((run / "evidence.json").read_text(encoding="utf-8"))
    scores = json.loads((run / "scores.json").read_text(encoding="utf-8"))
    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    return ev, scores, meta


def verify(a) -> int:
    run = Path(a.run_dir)
    ev, saved, meta = _load_run(run)
    rubric = Rubric(meta["rubric"], meta["rubric_sha256"])
    as_of = date.fromisoformat(meta["as_of"])
    holdings = [Holding(s["symbol"], s["name"], s["sector"], s.get("cap", ""), s.get("weight", "")) for s in saved]
    by: dict[str, list[LabelledItem]] = {h.symbol: [] for h in holdings}
    for r in ev:
        it = RawItem(r["symbol"], r.get("kind", "news"), r["headline"], "", r["url"], r["date"], r.get("source", ""), purpose=r.get("purpose", "sentiment"))
        lab = Label(r["event_type"], r["sentiment"], r.get("rationale", ""), bool(r["governance_flag"]), r.get("materiality"), bool(r.get("historical")))
        by[r["symbol"]].append(LabelledItem(it, lab))
    recomputed = score_portfolio(holdings, by, {s["symbol"]: s["n_dropped"] for s in saved}, as_of, rubric)
    fields = ["company_sentiment", "company_sentiment_raw", "sector_sentiment", "governance_score", "governance_label", "corporate_action_score"]
    bad = 0
    print(f"[STEP 5/6 SCORE] recomputing {len(recomputed)} stocks from {run}/evidence.json with the rubric embedded in run_meta.json "
          f"(v{rubric.version}, sha {rubric.sha256[:12]})\n")
    print(f"  {'symbol':<12}{'sent':>6}{'mean':>9}{'sector':>8}{'gov':>6}  {'label':<6} result")
    for rc in recomputed:
        sv = next(s for s in saved if s["symbol"] == rc.symbol)
        diffs = [f for f in fields if (sv[f] != getattr(rc, f) and not (isinstance(sv[f], float) and abs(sv[f] - getattr(rc, f)) < 1e-9))]
        bad += bool(diffs)
        print(f"  {rc.symbol:<12}{rc.company_sentiment!s:>6}{rc.company_sentiment_raw!s:>9}{rc.sector_sentiment!s:>8}{rc.governance_score:>6g}  "
              f"{rc.governance_label:<6} {'MATCH' if not diffs else 'MISMATCH ' + ', '.join(f'{d}: saved {sv[d]} vs recomputed {getattr(rc, d)}' for d in diffs)}")
    repo_rubric = Path(__file__).resolve().parent.parent / "rubric" / "rubric_v1.json"
    if repo_rubric.exists() and load_rubric(repo_rubric).sha256 != rubric.sha256:
        print("\n  NOTE: the rubric file in the repo has CHANGED since this run (verified against the rubric embedded in the run).")
    print(f"\n{'ALL SCORES REPRODUCED' if not bad else str(bad) + ' STOCK(S) DO NOT MATCH - investigate'}")
    return 1 if bad else 0


def render(a) -> int:
    from .pdf import build_pdf
    run = Path(a.run_dir)
    _, saved, meta = _load_run(run)
    nar = json.loads((run / "narrative.json").read_text(encoding="utf-8"))
    rubric = Rubric(meta["rubric"], meta["rubric_sha256"])
    scores = [StockScore(**s) for s in saved]
    res = {"run": meta, "scores": scores, "kept": {}}
    out = run / (a.out_name or "sentinelq_scorecard.pdf")
    build_pdf(out, res, rubric, nar["holdings"], nar["observations"],
              {"title": a.title or meta.get("title") or "Portfolio", "coverage": a.coverage if a.coverage is not None else meta.get("coverage", "")})
    print(f"[STEP 6/6 REPORT] rebuilt {out} from scores.json + narrative.json (edit narrative.json to change the commentary, then re-run)")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="sentinelq")
    sub = p.add_subparsers(dest="cmd", required=True)
    ins = sub.add_parser("inspect").add_subparsers(dest="what", required=True)
    for name in ("input", "news"):
        q = ins.add_parser(name)
        q.add_argument("--portfolio")
        q.add_argument("--pick")
        q.add_argument("--universe", default="portfolio/universe.csv")
        if name == "news":
            q.add_argument("--stock", required=True)
            q.add_argument("--source", choices=["gnews", "gdelt"], default="gnews")
            q.add_argument("--max-articles", type=int, default=100)
            q.add_argument("--as-of")
            q.add_argument("--show", type=int, default=25)
    q = ins.add_parser("label")
    q.add_argument("--company", required=True)
    q.add_argument("--headline", required=True)
    q.add_argument("--text", default="")
    q.add_argument("--url", default="https://example.com/inspect")
    q.add_argument("--date", default=date.today().isoformat())
    q.add_argument("--classifier", choices=["claude-code", "keyword"], default="claude-code")
    v = sub.add_parser("verify")
    v.add_argument("run_dir")
    r = sub.add_parser("render")
    r.add_argument("run_dir")
    r.add_argument("--title")
    r.add_argument("--coverage")
    r.add_argument("--out-name")
    a = p.parse_args(argv)
    if a.cmd == "inspect":
        return {"input": inspect_input, "news": inspect_news, "label": inspect_label}[a.what](a)
    return {"verify": verify, "render": render}[a.cmd](a)
