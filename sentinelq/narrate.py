"""Stage 6 prose. Text only - the scores are already fixed. A narrator explains the evidence and
the penalties that were mechanically applied; it never changes or invents a number.

AnthropicNarrator writes the prose from the evidence rows; TemplateNarrator is a deterministic
fallback (offline/tests) that assembles the same fields from the rubric arithmetic."""
from __future__ import annotations
import json
import os
import pathlib
from collections import Counter
from typing import Protocol

from .models import LabelledItem, StockScore

CA_TYPES = {"dividend", "buyback", "split_bonus", "dilution", "merger_acquisition", "capacity_expansion"}

SYSTEM = (
    "You write the analyst commentary for an investment-committee scorecard (house style: plain, factual, precise, "
    "no hype, no advice). The scores and penalties are FINAL and were computed by fixed rules; you must not change, "
    "re-derive or contradict them. Use ONLY the evidence rows supplied (each has a date, headline, label and URL); "
    "add no facts from memory. Cite dates in the form 30-Apr-26 and figures exactly as they appear in the evidence. "
    "Write 'Rs' for rupees (never the rupee symbol). If evidence is thin or sources conflict, say so explicitly in the "
    "rationale itself, as a plain statement about coverage (for example 'only two dated items were retrieved'). "
    "PRICE RULE: never mention share-price moves, 52-week highs, rallies or sell-offs in the sentiment rationale (price is context only; if essential, wrap the sentence in [context]). "
    "STYLE RULES: write the finished report text, never commentary about your inputs - do not say 'the evidence', 'the facts "
    "supplied', 'rows', 'payload' or 'provided'. Never print URLs. Write event types as words ('regulatory action', not "
    "regulatory_action). Write dates without a leading zero (5-Jun-26). Scores are whole numbers (72, not 72.0); never mention "
    "unrounded values. Use a real minus sign or plain hyphen for negatives (-20). Answer by calling the tool."
)

TOOL = {
    "name": "write_commentary",
    "description": "Commentary fields for one holding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment_rationale": {"type": "string", "description": (
                "Two short paragraphs separated by a blank line. Paragraph 1: the positive case with dated, sourced facts "
                "and figures (results, guidance, broker views). Paragraph 2: the offsets or the absence of offsets in the "
                "twelve-month window, ending with a sentence that explains why the fixed sentiment score stands, citing the supplied net "
                "points and band (e.g. 'Net evidence is +71 of 100, inside the +2 band (>= +55): sustained beats with no counterweight.'). "
                "Use the supplied sentiment score and points verbatim; whole numbers only, never decimals. 'why_this_score' lists the "
                "events that moved the score most - lead with those.")},
            "governance_rationale": {"type": "string", "description": (
                "One to three short paragraphs. If penalties were applied: say which event and its date, then the arithmetic "
                "in words using ONLY the supplied penalties ('The scoring applies -20 for the disclosed regulatory action to reach 80, "
                "then a further -8 for the CPO resignation, arriving at 72 (Flag).'). If none: 'No adverse governance events are present "
                "in the lookback.' Mention routine or out-of-window items that were deliberately not scored, and any memory-discount. "
                "Use the supplied governance score and label verbatim.")},
            "one_line_read": {"type": "string", "description": "ALWAYS fill this (never empty): 2 to 5 words naming what drives the year, e.g. 'Confirmed compounder', 'Momentum intact', 'Regulatory scar; strong operations'."},
            "key_corporate_action": {"type": "string", "description": "Terse list of the key corporate actions in the evidence, e.g. 'Div Rs 46 final + Rs 20 interim' or 'Rs 5,633cr buyback @ Rs 12,000 (open 1-Jul)'. An em dash if none."},
            "coverage_note": {"type": "string", "description": "Only if coverage is thin or sources conflict AND the sentiment rationale does not already say so; otherwise return an empty string. Never repeat what the rationale states."},
        },
        "required": ["sentiment_rationale", "governance_rationale", "one_line_read", "key_corporate_action", "coverage_note"],
        "additionalProperties": False,
    },
}

OBS_GUIDE = (
    " Write exactly 4 portfolio-level observations in this order: (1) sentiment delivery across the book, with counts; "
    "(2) governance Flags and any shared pattern between them (name the companies, the events, dates); (3) corporate-action "
    "intensity (buybacks, bonuses, mergers, large raises) and what it means for position management; (4) coverage gaps per stock. "
    "Each observation has a 'title' that is a short bold lead-in sentence ending in a full stop (e.g. 'Broad delivery is strong.') and "
    "a 'body' that continues in the same paragraph. Use only the facts supplied."
)


def fmt_sent(x):
    return "n/a" if x is None else f"{x:+d}" if x else "0"


def evidence_payload(s: StockScore, items: list[LabelledItem]) -> dict:
    return {
        "company": s.name, "sector": s.sector,
        "sentiment_status": s.sentiment_note or "ok", "evidence_span": f"{s.evidence_first}..{s.evidence_last}" if s.evidence_first else "none",
        "coverage_map": s.coverage_map,
        "why_this_score": s.rationale,
        "scores": {"sentiment": s.company_sentiment if s.company_sentiment is not None else "INSUFFICIENT DATA",
                   "net_points_of_100": s.sentiment_points, "governance": int(round(s.governance_score)),
                   "governance_label": s.governance_label.upper() if s.governance_label == "Flag" else s.governance_label},
        "penalties_applied": [{"event": p["event_type"].replace("_", " "), "penalty": p["penalty"], "date": p["date"],
                               "headline": p["headline"]} for p in s.governance_penalties],
        "governance_start": 100,
        "not_penalised": [{"event": p["event_type"].replace("_", " "), "date": p["date"], "headline": p["headline"], "why": p["why"]}
                          for p in s.governance_ignored],
        "low_confidence": s.low_confidence,
        "evidence": [{"date": li.item.published, "headline": li.item.title, "what_it_says": li.label.gist or (li.item.snippet or "")[:300],
                      "event": li.label.event_type.replace("_", " "), "sentiment": li.label.sentiment,
                      "governance_flag": li.label.governance_flag, "historical": li.label.historical,
                      "rationale": li.label.rationale}
                     for li in sorted(items, key=lambda x: x.item.published, reverse=True)],
    }


class Narrator(Protocol):
    def holding(self, s: StockScore, items: list[LabelledItem]) -> dict: ...
    def portfolio(self, facts: dict) -> list[dict]: ...


def key_actions(items: list[LabelledItem], limit: int = 3) -> str:
    from .lint import unique_actions
    from .rubric import load_rubric
    acts = [li for li in sorted(unique_actions(items, load_rubric()), key=lambda x: x.item.published, reverse=True)
            if li.label.event_type in CA_TYPES]
    return "; ".join(li.item.title.rstrip(".") for li in acts[:limit]) or "—"


class TemplateNarrator:
    """Deterministic fallback: restates evidence and arithmetic, no interpretation."""
    def holding(self, s, items):
        news = sorted((li for li in items if li.item.kind == "news"), key=lambda x: x.item.published, reverse=True)
        pos = [li for li in news if li.label.sentiment > 0][:3]
        neg = [li for li in news if li.label.sentiment < 0][:3]
        line = lambda li: f"{li.item.title.rstrip('.')} ({li.item.published})"
        sr = ([f"Insufficient data: {s.sentiment_note.replace('INSUFFICIENT DATA: ', '')}. No sentiment score is issued rather than a forced +1 or 0."]
              if s.company_sentiment is None else
              [s.rationale.split(" Governance ")[0]])
        if pos and not s.rationale:
            sr.append("Positive evidence: " + "; ".join(line(x) for x in pos) + ".")
        if neg and not s.rationale:
            sr.append("Negative evidence: " + "; ".join(line(x) for x in neg) + ".")
        pens = s.governance_penalties
        if pens:
            arith = " ".join(f"{p['penalty']:+g} ({p['event_type'].replace('_', ' ')}, {p['date']})" for p in pens)
            gr = f"Start 100 {arith} = {s.governance_score:g} ({s.governance_label})."
        else:
            gr = f"No penalised governance events in the lookback. Score {s.governance_score:g} ({s.governance_label})."
        return {"sentiment_rationale": " ".join(sr), "governance_rationale": gr,
                "one_line_read": "Low-confidence coverage" if s.low_confidence else "",
                "key_corporate_action": key_actions(items),
                "coverage_note": "Sparse news retrieval; treat scores as low confidence." if s.low_confidence else ""}

    def portfolio(self, facts):
        return default_observations(facts)


class AnthropicNarrator:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model_id = model or os.environ.get("SENTINELQ_MODEL", "claude-sonnet-5-5")

    def _call(self, system, user, tool):
        resp = self.client.messages.create(model=self.model_id, max_tokens=1800, system=system, tools=[tool],
                                           tool_choice={"type": "tool", "name": tool["name"]},
                                           messages=[{"role": "user", "content": user}])
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use":
                return dict(b.input)
        raise RuntimeError("narrator returned no structured output")

    def holding(self, s, items):
        out = self._call(SYSTEM, json.dumps(evidence_payload(s, items), default=str), TOOL)
        for k in ("sentiment_rationale", "governance_rationale", "one_line_read", "key_corporate_action", "coverage_note"):
            out.setdefault(k, "")
        return out

    def portfolio(self, facts):
        tool = {"name": "write_observations", "description": "Portfolio-level observations.",
                "input_schema": {"type": "object", "properties": {"observations": {"type": "array", "items": {
                    "type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["title", "body"]}}}, "required": ["observations"]}}
        try:
            return self._call(SYSTEM + OBS_GUIDE,
                              json.dumps(facts, default=str), tool)["observations"]
        except Exception:
            return default_observations(facts)


def portfolio_facts(scores: list[StockScore], kept: dict[str, list[LabelledItem]]) -> dict:
    dist = Counter(s.company_sentiment for s in scores if s.company_sentiment is not None)
    flags = [s for s in scores if s.governance_label == "Flag"]
    ca = Counter()
    from .lint import unique_actions
    from .rubric import load_rubric
    rub = load_rubric()
    for s in scores:
        for li in unique_actions(kept.get(s.symbol, []), rub):          # repeats of one declaration count once
            if li.label.event_type in CA_TYPES:
                ca[li.label.event_type] += 1
    companies = [{"company": s.name, "sector": s.sector, "sentiment": s.company_sentiment,
                  "governance": int(round(s.governance_score)), "label": s.governance_label,
                  "low_confidence": s.low_confidence,
                  "penalties": [{"event": p["event_type"].replace("_", " "), "penalty": p["penalty"], "date": p["date"]} for p in s.governance_penalties],
                  "corporate_actions": [li.item.title for li in unique_actions(kept.get(s.symbol, []), rub) if li.label.event_type in CA_TYPES][:4]}
                 for s in scores]
    return {"n": len(scores), "companies": companies, "sentiment_distribution": {str(k): v for k, v in sorted(dist.items(), reverse=True)},
            "flags": [{"company": s.name, "sector": s.sector, "score": s.governance_score,
                       "penalties": [(p["event_type"], p["penalty"], p["date"]) for p in s.governance_penalties]} for s in flags],
            "watch": [s.name for s in scores if s.governance_label == "Watch"],
            "corporate_action_counts": dict(ca),
            "low_confidence": [s.name for s in scores if s.low_confidence],
            "insufficient_data": [s.name for s in scores if s.company_sentiment is None],
            "window_collapsed": [s.name for s in scores if s.sentiment_note.startswith("EVIDENCE WINDOW COLLAPSED")]}


def default_observations(f: dict) -> list[dict]:
    d = f["sentiment_distribution"]
    dist = ", ".join(f"{v} at {int(k):+d}" if int(k) else f"{v} at 0" for k, v in d.items())
    obs = [{"title": "Sentiment distribution.", "body": f"Of {f['n']} holdings: {dist}."}]
    if f["flags"]:
        obs.append({"title": f"{len(f['flags'])} governance Flag(s).",
                    "body": "; ".join(f"{x['company']} ({x['sector']}) scores {x['score']:g} from " +
                                      ", ".join(f"{t.replace('_', ' ')} {p:+g}" for t, p, _ in x["penalties"]) for x in f["flags"]) + "."})
    else:
        obs.append({"title": "No governance Flags.", "body": "No holding scores below 80."})
    if f["corporate_action_counts"]:
        obs.append({"title": "Corporate-action activity.",
                    "body": ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in f["corporate_action_counts"].items()) + " identified in the window."})
    if f.get("insufficient_data"):
        obs.append({"title": "Insufficient data is reported, not forced.", "body": "No sentiment score is issued for " + ", ".join(f["insufficient_data"]) +
                    " because the minimum evidence standard (at least 8 relevant articles including a results print) was not met."})
    if f.get("window_collapsed"):
        obs.append({"title": "Evidence window.", "body": "For " + ", ".join(f["window_collapsed"]) + " the retrieved articles cover only a short part of the 12-month lookback; read those scores with caution."})
    if f["low_confidence"]:
        obs.append({"title": "Coverage gaps are stated per stock.", "body": "Sparse retrieval (low confidence): " + ", ".join(f["low_confidence"]) + "."})
    return obs


class CachedNarrator:
    """Reuses prose for identical evidence + scores, so re-runs are stable and free."""
    def __init__(self, inner, path, model_id="template"):
        import hashlib
        self.inner, self.model_id, self._h = inner, model_id, hashlib
        self.path = pathlib.Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        import threading
        self._lock = threading.Lock()
        self._d = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._d[r["key"]] = r["value"]

    def _wrap(self, kind, payload, fn):
        key = self._h.sha256(f"{kind}|{self.model_id}|{json.dumps(payload, sort_keys=True, default=str)}".encode()).hexdigest()
        if key not in self._d:
            val = fn()
            with self._lock:
                self._d[key] = val
                if self.path:
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"key": key, "value": val}, ensure_ascii=False) + "\n")
        return self._d[key]

    def holding(self, s, items):
        return self._wrap("h", evidence_payload(s, items), lambda: self.inner.holding(s, items))

    def portfolio(self, facts):
        return self._wrap("p", facts, lambda: self.inner.portfolio(facts))
