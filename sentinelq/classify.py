"""Stage 3 - the caged AI step. The model labels text we hand it; it never produces a score.

Cage: (i) forced tool-use structure, (ii) extraction only (text is supplied),
(iii) temperature 0, (iv) every raw response logged and cached.
"""
from __future__ import annotations
import json
import os
import re
from typing import Protocol

from .models import Label, RawItem
from .rubric import Rubric

PROMPT_VERSION = "v1"

SYSTEM = (
    "You are a labelling function for an equity research pipeline. You are given ONE news item "
    "(headline and optional snippet) about a named company. Label ONLY what the supplied text says. "
    "Do not use outside knowledge, do not speculate, do not infer events that are not stated. "
    "Choose the single best event_type from the enumerated list (use 'other' if none fits). "
    "sentiment is an integer from -2 (very negative for the company) to +2 (very positive). "
    "rationale is ONE sentence grounded in the supplied text. "
    "governance_flag is true only if the item concerns regulation, investigations, auditors, "
    "promoter pledges, related-party transactions, litigation, or senior management changes. "
    "materiality (low/medium/high) is the economic size of a corporate action or fine, else omit. "
    "historical is true only if the text describes an event that took place more than 12 months before the publication date "
    "(background mention); otherwise false. "
    "You must answer by calling the label_item tool."
)


def tool_schema(r: Rubric) -> dict:
    return {
        "name": "label_item",
        "description": "Record the label for the supplied news item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": r.event_types},
                "sentiment": {"type": "integer", "minimum": r.sent_min, "maximum": r.sent_max},
                "rationale": {"type": "string"},
                "governance_flag": {"type": "boolean"},
                "materiality": {"type": "string", "enum": r["materiality_levels"]},
                "historical": {"type": "boolean"},
            },
            "required": ["event_type", "sentiment", "rationale", "governance_flag"],
            "additionalProperties": False,
        },
    }


def user_message(item: RawItem, company: str) -> str:
    return (f"Company: {company}\nPublished: {item.published}\nSource: {item.source}\n"
            f"Headline: {item.title}\nSnippet: {item.snippet or '(none)'}")


class Classifier(Protocol):
    model_id: str
    def classify(self, item: RawItem, company: str) -> tuple[dict | None, str]:
        """Return (label_dict_or_None, raw_response_text)."""


class AnthropicClassifier:
    def __init__(self, rubric: Rubric, model: str | None = None, api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model_id = model or os.environ.get("SENTINELQ_MODEL", "claude-sonnet-5-5")
        self.tool = tool_schema(rubric)
        self._temp_ok = True

    def classify(self, item, company):
        kwargs = dict(model=self.model_id, max_tokens=400, system=SYSTEM, tools=[self.tool],
                      tool_choice={"type": "tool", "name": "label_item"},
                      messages=[{"role": "user", "content": user_message(item, company)}])
        if self._temp_ok:
            kwargs["temperature"] = 0
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as e:  # some models reject explicit temperature; fall back and remember
            if self._temp_ok and "temperature" in str(e).lower():
                self._temp_ok = False
                kwargs.pop("temperature")
                resp = self.client.messages.create(**kwargs)
            else:
                raise
        raw = resp.model_dump_json()
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input), raw
        return None, raw


_RULES = [  # (regex, event_type, sentiment, governance)
    (r"sebi|regulator|rbi (penalt|order)|penalty|settlement order", "regulatory_action", -2, True),
    (r"investigat|probe|raid|show[- ]cause", "investigation", -1, True),
    (r"auditor.*(resign)", "auditor_resignation", -2, True),
    (r"restate|restatement|qualified opinion", "auditor_restatement", -2, True),
    (r"exchange fine|fined by (bse|nse)|disclosure lapse", "exchange_fine", -1, True),
    (r"independent director|board independence|combined (roles|chair)", "board_independence", -1, True),
    (r"pledge", "pledge", -1, True),
    (r"related[- ]party", "rpt_concern", -1, True),
    (r"resign|steps down|exit of|quits", "management_exit", -1, True),
    (r"lawsuit|litigation|court", "litigation", -1, True),
    (r"beat|record profit|profit (up|jumps|rises)", "earnings_beat", 2, False),
    (r"miss|profit (falls|drops|declines)|loss widens", "earnings_miss", -2, False),
    (r"guidance", "guidance_change", 0, False),
    (r"order win|bags order|wins order|contract", "order_win", 1, False),
    (r"capacity|new plant|expansion", "capacity_expansion", 1, False),
    (r"upgrade|target price|downgrade", "analyst_action", 0, False),
    (r"client base|business update|record .*book|yoy", "operating_update", 2, False),
    (r"buyback", "buyback", 1, False),
    (r"dividend", "dividend", 1, False),
    (r"rights issue|qip|preferential|dilution", "dilution", -1, False),
    (r"acquisition|acquires|merger", "merger_acquisition", 1, False),
]


class KeywordClassifier:
    """OFFLINE STUB for demos/tests only. Not a substitute for the LLM classifier."""
    model_id = "keyword-stub"

    def classify(self, item, company):
        text = f"{item.title} {item.snippet}".lower()
        for pat, et, s, g in _RULES:
            if re.search(pat, text):
                if et == "analyst_action":
                    s = -1 if "downgrade" in text else 1 if "upgrade" in text else 0
                lab = {"event_type": et, "sentiment": s,
                       "rationale": f"Keyword match on '{pat.split('|')[0]}' in headline.",
                       "governance_flag": g}
                return lab, json.dumps(lab)
        lab = {"event_type": "other", "sentiment": 0, "rationale": "No taxonomy match in text.",
               "governance_flag": False}
        return lab, json.dumps(lab)


def to_label(d: dict) -> Label:
    return Label(d["event_type"], d["sentiment"], d["rationale"], bool(d["governance_flag"]),
                 d.get("materiality"), bool(d.get("historical", False)))
