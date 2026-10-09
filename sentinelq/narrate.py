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

CA_TYPES = {"dividend", "buyback", "dilution", "capacity_expansion", "open_offer_takeover", "lock_in_expiry", "pledge", "pledge_invocation", "insolvency_admission"}

SYSTEM = (
    "You write the analyst commentary for an investment-committee scorecard (house style: plain, factual, precise, "
    "no hype, no advice). Scores are computed separately by fixed rules and shown elsewhere in the report; your text "
    "explains the evidence in words and NEVER states a score, band, label, mean, points or penalty number. Use ONLY the evidence rows supplied (each has a date, headline, label and URL); "
    "add no facts from memory. Cite dates in the form 30-Apr-26 and figures exactly as they appear in the evidence. "
    "Write 'Rs' for rupees (never the rupee symbol). If evidence is thin or sources conflict, say so explicitly in the "
    "rationale itself, as a plain statement about coverage (for example 'only two dated items were retrieved'). "
    "PRICE RULE: never mention share-price moves, 52-week highs, rallies or sell-offs in the sentiment rationale (price is context only; if essential, wrap the sentence in [context]). "
    "STYLE RULES: write the finished report text, never commentary about your inputs - do not say 'the evidence', 'the facts "
    "supplied', 'rows', 'payload' or 'provided'. Never print URLs. Write event types as words ('regulatory action', not "
    "regulatory_action). Write dates without a leading zero (5-Jun-26). Use a real minus sign or plain hyphen for negatives (-20). Answer by calling the tool."
)

TOOL = {
    "name": "write_commentary",
    "description": "Commentary fields for one holding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment_rationale": {"type": "string", "description": (
                "Two short paragraphs separated by a blank line. Paragraph 1: the positive case with dated, sourced facts and the "
                "company's own figures (growth %, Rs crore, guidance, broker views). Paragraph 2: the offsets, or the absence of "
                "offsets, in the twelve-month window, ending with one sentence on why the year reads the way it does. NEVER state "
                "any SentinelQ score, mean, band, rating, points or label - no '+1', no 'weighted mean', no 'band'. Explain in words.")},
            "ca_rationale": {"type": "string", "description": (
                "One to two short paragraphs on the corporate actions that matter, SEPARATE from sentiment and governance even where "
                "an event also appears there: what happened, how advanced it is (announced / board-approved / approved / completed), "
                "how recent, and how large, in words. NEVER state any score, band, severity number or multiplier. Only discuss the "
                "corporate-action events supplied; do not mention anything else. If any event has 'single_lower_tier_source' true, say "
                "it was reported by a single lower-tier outlet, so its date is unconfirmed. If there are no corporate-action events, "
                "say so plainly.")},
            "governance_rationale": {"type": "string", "description": (
                "One to three short paragraphs. If adverse governance events were found, say which events and their dates, and why "
                "they matter. If none: 'No adverse governance events are present in the lookback.' Mention routine items that were "
                "reviewed and deliberately not treated as adverse, and why. NEVER state any score, penalty number or label.")},
            "one_line_read": {"type": "string", "description": "ALWAYS fill this (never empty): 2 to 5 words naming what drives the year, e.g. 'Confirmed compounder', 'Momentum intact', 'Regulatory scar; strong operations'."},
            "key_corporate_action": {"type": "string", "description": "Terse list of the key corporate actions among the supplied corporate-action events only, e.g. 'Div Rs 46 final + Rs 20 interim' or 'Rs 5,633cr buyback @ Rs 12,000 (open 1-Jul)'. An em dash if none."},
            "coverage_note": {"type": "string", "description": "Only if coverage is thin or sources conflict AND the sentiment rationale does not already say so; otherwise return an empty string. Never repeat what the rationale states."},
        },
        "required": ["sentiment_rationale", "governance_rationale", "ca_rationale", "one_line_read", "key_corporate_action", "coverage_note"],
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
    """What the commentary writer sees. No scores (they are shown elsewhere, not narrated), and nothing about data-quarantined
    corporate actions (splits, bonuses, ISIN changes...): those carry no economic signal and are kept out of the written report."""
    hidden = {e["url"] for e in s.ca_gate_b}
    return {
        "company": s.name, "sector": s.sector,
        "sentiment_status": "insufficient data" if s.company_sentiment is None else "ok",
        "evidence_span": f"{s.evidence_first}..{s.evidence_last}" if s.evidence_first else "none",
        "ca_events": [{"event": e["event_type"].replace("_", " "), "date": e["date"], "headline": e["headline"],
                       "direction": e["direction"], "stage": e["stage"],
                       "single_lower_tier_source": e["n_sources"] == 1 and e["max_tier"] in ("T3", "T4")}
                      for e in sorted(s.ca_events, key=lambda x: -x["score"])],
        "ca_hard_override": [{"event": e["event_type"].replace("_", " "), "date": e["date"], "headline": e["headline"]} for e in s.ca_gate_a],
        "adverse_governance_events": [{"event": p["event_type"].replace("_", " "), "date": p["date"], "headline": p["headline"]}
                                      for p in s.governance_penalties],
        "reviewed_not_adverse": [{"event": p["event_type"].replace("_", " "), "date": p["date"], "headline": p["headline"], "why": p["why"]}
                                 for p in s.governance_ignored if p.get("url") not in hidden],
        "low_coverage": s.low_confidence,
        "evidence": [{"date": li.item.published, "headline": li.item.title, "what_it_says": li.label.gist or (li.item.snippet or "")[:300],
                      "event": li.label.event_type.replace("_", " "), "sentiment": li.label.sentiment,
                      "governance_flag": li.label.governance_flag, "historical": li.label.historical}
                     for li in sorted(items, key=lambda x: x.item.published, reverse=True) if li.item.url not in hidden],
    }


_HIDE = ("quarantin", "gate b", "bonus", "stock split", "share split", "split and bonus", "bonus and split")


def scrub(out: dict) -> dict:
    """Last line of defence for the per-stock text: drop any sentence that still mentions a quarantined corporate action."""
    import re
    clean = {}
    for k, v in out.items():
        if isinstance(v, str) and k != "one_line_read":
            paras = []
            for para in v.split("\n\n"):
                sents = re.split(r"(?<=[.!?])\s+", para)
                kept = [x for x in sents if not any(h in x.lower() for h in _HIDE)]
                if kept:
                    paras.append(" ".join(kept))
            v = "\n\n".join(paras)
        clean[k] = v
    return clean



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
        hidden = {e["url"] for e in s.ca_gate_b}
        pos = [li for li in pos if li.item.url not in hidden]
        neg = [li for li in neg if li.item.url not in hidden]
        sr = ([f"Insufficient data: {s.sentiment_note.replace('INSUFFICIENT DATA: ', '')}."] if s.company_sentiment is None else [])
        if pos:
            sr.append("Positive evidence: " + "; ".join(line(x) for x in pos) + ".")
        if neg:
            sr.append("Negative evidence: " + "; ".join(line(x) for x in neg) + ".")
        if not pos and not neg and s.company_sentiment is not None:
            sr.append("No clearly positive or negative company-specific news in the lookback.")
        pens = s.governance_penalties
        if pens:
            gr = "Adverse governance events: " + "; ".join(f"{p['event_type'].replace('_', ' ')} ({p['date']})" for p in pens) + "."
        else:
            gr = "No adverse governance events are present in the lookback."
        return {"sentiment_rationale": " ".join(sr), "governance_rationale": gr,
                "one_line_read": "Low-confidence coverage" if s.low_confidence else "",
                "ca_rationale": self._ca_fallback(s),
                "key_corporate_action": key_actions(items),
                "coverage_note": "Sparse news retrieval; treat this read as low confidence." if s.low_confidence else ""}

    @staticmethod
    def _ca_fallback(s):
        if not s.ca_events and not s.ca_gate_a:
            return "No corporate-action events in the lookback."
        parts = []
        top = sorted(s.ca_events, key=lambda e: -e["score"])[:2]
        if top:
            parts.append("Main corporate actions: " + "; ".join(f"{e['event_type'].replace('_', ' ')} ({e['date']})" for e in top) + ".")
            weak = [e for e in top if e["n_sources"] == 1 and e["max_tier"] in ("T3", "T4")]
            if weak:
                parts.append("Reported by a single lower-tier source (" + ", ".join(e["event_type"].replace("_", " ") for e in weak) + "); treat the date as unconfirmed.")
        if s.ca_gate_a:
            parts.append("Severe event requiring exit review: " + ", ".join(e["event_type"].replace("_", " ") for e in s.ca_gate_a) + ".")
        return " ".join(parts)

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
        for k in ("sentiment_rationale", "governance_rationale", "ca_rationale", "one_line_read", "key_corporate_action", "coverage_note"):
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
