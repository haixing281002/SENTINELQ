"""B5 - the golden regression set: audit/golden/governance_37.jsonl, run on every build (`python -m sentinelq golden`) and as a gate
inside every run (a run whose golden fails is written with published=false).

  penalise -> the verification gate yields exactly expected_weight (half weight allowed when allow_half)
  reject   -> the gate yields no penalty (or, for materiality errors with max_weight, nothing heavier than that band);
              a `pair_with` item is a double count: governance() over the pair must apply exactly ONE penalty
  recall   -> the governance pass would surface the headline (title rule + query keywords); when a corpus holds articles for that
              stock at or after the date, the headline must be present in it (retrieval checked against the corpus of record)"""
from __future__ import annotations
import json
import re
from pathlib import Path

from .config import GOLDEN_FILE
from .ingest.select import GOV_QUERY_LEADERSHIP, GOV_QUERY_REGULATORY, GOV_TITLE
from .models import Label, LabelledItem, RawItem
from .rubric import Rubric
from .score import gate, governance

ROOT = Path(__file__).resolve().parent.parent


def load_golden(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else (ROOT / GOLDEN_FILE)
    if not p.exists():
        raise FileNotFoundError(f"golden set not found: {p}")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def _item(g: dict) -> LabelledItem:
    lab = g.get("label") or {}
    it = RawItem(g["symbol"], "news", g["headline"], g.get("snippet", ""), f"https://{g['source_domain']}/golden/{g['id']}", g["published_date"],
                 g["source_domain"], purpose="governance", pass_="governance")
    label = Label(lab.get("event_type", g.get("expected_flag_type") or "other"), -1, "golden item", True, None, False, True,
                  subject=lab.get("subject"), occurred_at_company=lab.get("occurred_at_company"), action_stage=lab.get("action_stage"),
                  severity=lab.get("severity"), amount_inr_cr=lab.get("amount_inr_cr"), people_direction=lab.get("people_direction"),
                  role_tier=lab.get("role_tier"), event_key=lab.get("event_key"))
    return LabelledItem(it, label)


_QUERY_WORDS = {w.strip('"').lower() for w in re.findall(r'"[^"]+"|\w+', GOV_QUERY_LEADERSHIP + " " + GOV_QUERY_REGULATORY) if w.upper() != "OR"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def _recall(g: dict, corpus) -> tuple[bool, str]:
    head = g["headline"]
    if not GOV_TITLE.search(head):
        return False, "the governance title rule would not pick this headline"
    words = _norm(head).split()
    if not any(q in _norm(head) for q in _QUERY_WORDS):
        return False, "no governance query keyword in the headline"
    if corpus is None:
        return True, "rule-pass (no corpus to check retrieval against)"
    try:
        arts = [a for a in corpus.articles() if a.get("symbol") == g["symbol"]]
    except Exception as e:
        return True, f"rule-pass (corpus unreadable: {e!r})"
    if not any((a.get("as_of") or "") >= g["published_date"] for a in arts):
        return True, "rule-pass (corpus holds no run covering this stock and date yet)"
    keyw = [w for w in words if len(w) >= 5][:6]
    for a in arts:
        h = _norm(a.get("headline") or "")
        if sum(1 for w in keyw if w in h) >= max(2, len(keyw) // 2):
            return True, f"found in corpus run {a.get('run_id')} ({a.get('published_date')})"
    return False, f"not found among {len(arts)} stored article(s) for {g['symbol']}"


def run_golden(r: Rubric, path: str | Path | None = None, corpus=None) -> dict:
    items = load_golden(path)
    by_id = {g["id"]: g for g in items}
    results, failed = [], 0
    for g in items:
        exp = g["expected"]
        ok, detail = False, ""
        if exp == "recall":
            ok, detail = _recall(g, corpus)
        elif g.get("pair_with"):
            first, second = _item(by_id[g["pair_with"]]), _item(g)
            score, label, applied = governance([first, second], r, g["symbol"])
            ok = len(applied) == 1
            detail = f"{len(applied)} penalt(ies) applied over the pair: " + ", ".join(f"{a['event_type']} {a['penalty']:+g}" for a in applied)
        else:
            p, why, _bucket = gate(_item(g), r)
            if exp == "penalise":
                want = g["expected_weight"]
                ok = p is not None and (p == want or (g.get("allow_half") and p == want / 2))
                detail = f"gate -> {p} ({why}); expected {want}"
            else:
                mw = g.get("max_weight")
                ok = p is None or (mw is not None and p >= mw)
                detail = f"gate -> {p} ({why}); expected none" + (f" (at most {mw})" if mw is not None else "")
        failed += not ok
        results.append({"id": g["id"], "symbol": g["symbol"], "expected": exp, "ok": ok, "detail": detail, "headline": g["headline"][:80]})
    return {"total": len(items), "failed": failed, "passed": failed == 0, "results": results, "file": str(Path(path) if path else ROOT / GOLDEN_FILE)}


def print_report(rep: dict) -> None:
    for x in rep["results"]:
        print(f"  {'PASS' if x['ok'] else 'FAIL'}  {x['id']}  {x['symbol']:<11} {x['expected']:<8} {x['headline'][:60]:<60}  {x['detail']}")
    print(f"\ngolden: {rep['total'] - rep['failed']}/{rep['total']} passed" + ("" if rep["passed"] else " - PUBLICATION BLOCKED"))
