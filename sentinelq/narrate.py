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
    "You write the analyst commentary for an investment-committee scorecard. The scores and penalties "
    "are FINAL and were computed by fixed rules; you must not change, re-derive or contradict them. "
    "Use ONLY the evidence rows supplied (each has a date, headline, label and URL); do not add facts "
    "from memory. Cite dates and figures that appear in the evidence. Plain, factual, IC-ready tone. "
    "If evidence is thin or sources conflict, say so explicitly. Answer by calling the tool."
)

TOOL = {
    "name": "write_commentary",
    "description": "Commentary fields for one holding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment_rationale": {"type": "string", "description": "1-2 short paragraphs: the sentiment case, offsets, and why the fixed score stands."},
            "governance_rationale": {"type": "string", "description": "Which penalties were applied (from the list given), the arithmetic, and any non-scored context."},
            "one_line_read": {"type": "string", "description": "At most 8 words."},
            "key_corporate_action": {"type": "string", "description": "Short factual list of the key corporate actions in the evidence, or an em dash if none."},
            "coverage_note": {"type": "string", "description": "Empty unless coverage is thin or sources conflict."},
        },
        "required": ["sentiment_rationale", "governance_rationale", "one_line_read", "key_corporate_action", "coverage_note"],
        "additionalProperties": False,
    },
}


def fmt_sent(x):
    return "n/a" if x is None else f"{x:+d}" if x else "0"


def evidence_payload(s: StockScore, items: list[LabelledItem]) -> dict:
    return {
        "company": s.name, "sector": s.sector,
        "scores": {"sentiment": s.company_sentiment, "sentiment_unrounded": s.company_sentiment_raw,
                   "governance": s.governance_score, "governance_label": s.governance_label},
        "penalties_applied": [{k: p[k] for k in ("event_type", "penalty", "date", "headline")} for p in s.governance_penalties],
        "low_confidence": s.low_confidence,
        "evidence": [{"date": li.item.published, "headline": li.item.title, "snippet": li.item.snippet,
                      "event_type": li.label.event_type, "sentiment": li.label.sentiment,
                      "governance_flag": li.label.governance_flag, "historical": li.label.historical,
                      "rationale": li.label.rationale, "url": li.item.url}
                     for li in sorted(items, key=lambda x: x.item.published, reverse=True)],
    }


class Narrator(Protocol):
    def holding(self, s: StockScore, items: list[LabelledItem]) -> dict: ...
    def portfolio(self, facts: dict) -> list[dict]: ...


def key_actions(items: list[LabelledItem], limit: int = 3) -> str:
    acts = [li for li in sorted(items, key=lambda x: x.item.published, reverse=True)
            if li.label.event_type in CA_TYPES]
    return "; ".join(li.item.title.rstrip(".") for li in acts[:limit]) or "—"


class TemplateNarrator:
    """Deterministic fallback: restates evidence and arithmetic, no interpretation."""
    def holding(self, s, items):
        news = sorted((li for li in items if li.item.kind == "news"), key=lambda x: x.item.published, reverse=True)
        pos = [li for li in news if li.label.sentiment > 0][:3]
        neg = [li for li in news if li.label.sentiment < 0][:3]
        line = lambda li: f"{li.item.title.rstrip('.')} ({li.item.published})"
        sr = [f"Sentiment {fmt_sent(s.company_sentiment)} (recency-weighted mean {s.company_sentiment_raw}) from {len(news)} dated news item(s)."]
        if pos:
            sr.append("Positive evidence: " + "; ".join(line(x) for x in pos) + ".")
        if neg:
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
            return self._call(SYSTEM + " Write 3-5 portfolio-level observations from the facts (distribution, "
                              "governance flags and patterns, corporate-action intensity, coverage gaps).",
                              json.dumps(facts, default=str), tool)["observations"]
        except Exception:
            return default_observations(facts)


def portfolio_facts(scores: list[StockScore], kept: dict[str, list[LabelledItem]]) -> dict:
    dist = Counter(s.company_sentiment for s in scores if s.company_sentiment is not None)
    flags = [s for s in scores if s.governance_label == "Flag"]
    ca = Counter()
    for s in scores:
        for li in kept.get(s.symbol, []):
            if li.label.event_type in CA_TYPES:
                ca[li.label.event_type] += 1
    return {"n": len(scores), "sentiment_distribution": {str(k): v for k, v in sorted(dist.items(), reverse=True)},
            "flags": [{"company": s.name, "sector": s.sector, "score": s.governance_score,
                       "penalties": [(p["event_type"], p["penalty"], p["date"]) for p in s.governance_penalties]} for s in flags],
            "watch": [s.name for s in scores if s.governance_label == "Watch"],
            "corporate_action_counts": dict(ca),
            "low_confidence": [s.name for s in scores if s.low_confidence]}


def default_observations(f: dict) -> list[dict]:
    d = f["sentiment_distribution"]
    obs = [{"title": "Sentiment distribution",
            "body": f"Of {f['n']} holdings: " + ", ".join(f"{v} at {int(k):+d}" if int(k) else f"{v} at 0" for k, v in d.items()) + "."}]
    if f["flags"]:
        obs.append({"title": f"{len(f['flags'])} governance Flag(s)",
                    "body": "; ".join(f"{x['company']} ({x['sector']}) at {x['score']:g} from " +
                                      ", ".join(f"{t.replace('_', ' ')} {p:+g}" for t, p, _ in x["penalties"]) for x in f["flags"]) + "."})
    else:
        obs.append({"title": "No governance Flags", "body": "No holding scores below 80."})
    if f["corporate_action_counts"]:
        obs.append({"title": "Corporate-action activity",
                    "body": ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in f["corporate_action_counts"].items()) + " identified in the window."})
    if f["low_confidence"]:
        obs.append({"title": "Coverage gaps", "body": "Sparse retrieval (low confidence): " + ", ".join(f["low_confidence"]) + "."})
    return obs


class CachedNarrator:
    """Reuses prose for identical evidence + scores, so re-runs are stable and free."""
    def __init__(self, inner, path, model_id="template"):
        import hashlib
        self.inner, self.model_id, self._h = inner, model_id, hashlib
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._d = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._d[r["key"]] = r["value"]

    def _wrap(self, kind, payload, fn):
        key = self._h.sha256(f"{kind}|{self.model_id}|{json.dumps(payload, sort_keys=True, default=str)}".encode()).hexdigest()
        if key not in self._d:
            self._d[key] = fn()
            with self.path.open("a") as f:
                f.write(json.dumps({"key": key, "value": self._d[key]}) + "\n")
        return self._d[key]

    def holding(self, s, items):
        return self._wrap("h", evidence_payload(s, items), lambda: self.inner.holding(s, items))

    def portfolio(self, facts):
        return self._wrap("p", facts, lambda: self.inner.portfolio(facts))
