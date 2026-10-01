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
    "about_company is false if the item is NOT actually about the named company (a different entity with a similar "
    "name, a generic market roundup that only lists it, an unrelated topic); when false, use event_type 'other', sentiment 0. "
    "VERIFICATION GATE - for every item with governance_flag true you MUST also answer: (1) subject = who is the event ABOUT? "
    "'company' / 'subsidiary' / 'promoter_or_insider' count; 'victim' (the company or a bank was defrauded / cheated), 'lender_or_counterparty', "
    "'peer_or_sector' (industry-wide rule), 'macro_policy' (customs duty, GST rate, budget, tariff), 'shareholders' (e.g. KYC non-compliant holders) "
    "NEVER count. (2) occurred_at_company = did it actually happen at THIS company? (3) severity: integrity (SEBI conduct/supervision failure, fraud, auditor "
    "integrity), material, minor, procedural (tax / customs / labelling / BRSR / disclosure lapses), immaterial (sum under Rs 10 crore and not integrity-type). "
    "Also give action_stage (order_or_settlement = a final order or settled adjudication; notice_or_demand = a notice / show-cause / demand with NO order yet; "
    "investigation = a live probe; resolved_or_quashed = withdrawn, quashed or decided in the company's favour; routine = a compliance statement or routine filing), "
    "amount_inr_cr if a sum is stated (convert lakh/million to crore), and event_key = a short kebab-case id of the underlying real-world event so the "
    "same event reported on several days gets the SAME key. For people items give people_direction (unplanned_exit only for resignation / termination / "
    "abrupt exit; appointment, promotion_or_elevation, reappointment and planned succession are NEVER exits) and role_tier. "
    "HARD RULES: an appointment, promotion or reappointment is never management_exit; macro or sector policy is never company regulatory_action; "
    "a bank or company that is the victim of fraud is not the subject. "
    "PRICE RULE: never reason from share-price moves, 52-week highs or sell-offs. An item that is only about the share price is event_type 'price_move' with sentiment 0. "
    "GOVERNANCE TYPES - the penalty depends on picking the right one: "
    "management_exit = an UNPLANNED departure (resignation, termination, abrupt exit) of a CEO, MD, CFO, any 'Chief ... Officer' "
    "(e.g. Chief Product Officer), the Company Secretary or the Compliance Officer. "
    "management_change_routine = every other people item: elevation, promotion, re-designation, new appointment, planned "
    "retirement/superannuation/succession, or an exit/appointment BELOW CXO tier (Head of a function, VP, director-level). "
    "Example: 'X appointed as Head - Sales and Marketing' is management_change_routine, never management_exit. "
    "rpt_concern = material or investor-contested related-party transaction; rpt_routine = routine, approved, arm's-length RPT. "
    "investigation = an unresolved probe/raid/inquiry; investigation_closed = concluded, quashed or cleared. "
    "board_independence = combined chair/MD roles or independent-director gaps; board_change_routine = ordinary board changes. "
    "auditor_resignation / auditor_restatement = adverse; auditor_rotation = scheduled rotation or routine reappointment. "
    "regulatory_action = a SEBI / exchange / RBI order, penalty or settlement. "
    "Calibration: SEBI/regulator order or settlement -> regulatory_action, -2, governance. CXO/CFO resignation -> management_exit, -1, "
    "governance. Record quarterly profit / revenue growth -> earnings_beat, +2. Profit collapse -> earnings_miss, -2. Routine broker "
    "target reiteration -> analyst_action, +1 or 0. Dividend declared -> dividend, +1. "
    "You must answer by calling the label_item tool."
)


def tool_schema(r: Rubric) -> dict:
    return {
        "name": "label_item",
        "description": "Record the label for the supplied news item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": [e for e in r.event_types if e not in r.data.get("reserved_event_types", [])]},
                "sentiment": {"type": "integer", "minimum": r.sent_min, "maximum": r.sent_max},
                "rationale": {"type": "string"},
                "governance_flag": {"type": "boolean"},
                "materiality": {"type": "string", "enum": r["materiality_levels"]},
                "historical": {"type": "boolean"},
                "about_company": {"type": "boolean"},
                "subject": {"type": "string", "enum": ["company", "subsidiary", "promoter_or_insider", "victim", "lender_or_counterparty",
                                                       "peer_or_sector", "macro_policy", "shareholders", "other"]},
                "occurred_at_company": {"type": "boolean"},
                "action_stage": {"type": "string", "enum": ["order_or_settlement", "notice_or_demand", "investigation", "resolved_or_quashed", "routine", "n/a"]},
                "severity": {"type": "string", "enum": ["integrity", "material", "minor", "procedural", "immaterial", "n/a"]},
                "amount_inr_cr": {"type": "number"},
                "people_direction": {"type": "string", "enum": ["unplanned_exit", "planned_exit_or_succession", "appointment", "promotion_or_elevation", "reappointment", "n/a"]},
                "role_tier": {"type": "string", "enum": ["cxo_cs_cfo_compliance", "senior_management", "below_cxo", "non_executive_director", "n/a"]},
                "event_key": {"type": "string"},
            },
            "required": ["event_type", "sentiment", "rationale", "governance_flag", "about_company"],
            "additionalProperties": False,
        },
    }


def item_id(item: RawItem) -> str:
    import hashlib
    return hashlib.sha256(f"{item.url}\x1f{item.title}".encode()).hexdigest()[:16]


def item_payload(item: RawItem, company: str) -> dict:
    return {"id": item_id(item), "company": company, "published": item.published, "source": item.source,
            "headline": item.title, "text": item.snippet or "(headline only)"}


def batch_schema(r: Rubric) -> dict:
    """JSON schema for a batch reply: {"labels": [{"id", ...label fields}]}. Enforced by `claude --json-schema`, so the
    event type is always from the fixed taxonomy and sentiment is always an integer in range (the doc's 'forced structure')."""
    props = dict(tool_schema(r)["input_schema"]["properties"])
    item = {"type": "object", "properties": {"id": {"type": "string"}, **props},
            "required": ["id", "event_type", "sentiment", "rationale", "governance_flag", "about_company"],
            "additionalProperties": False}
    return {"type": "object", "properties": {"labels": {"type": "array", "items": item}}, "required": ["labels"], "additionalProperties": False}


def batch_prompt(pairs: list[tuple[RawItem, str]], r: Rubric) -> str:
    """One prompt labelling many independent items; used by the no-API-key paths."""
    schema = tool_schema(r)["input_schema"]
    return (SYSTEM.replace("You must answer by calling the label_item tool.", "") +
            "\n\nLabel EACH item below independently. Reply with a JSON object {\"labels\": [...]} (no prose, no code fence), one "
            "object per item, each with an \"id\" field copied from the item plus the fields of this JSON schema:\n" +
            json.dumps(schema) + "\n\nITEMS:\n" + "\n".join(json.dumps(item_payload(i, c)) for i, c in pairs))


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
    (r"(chief|cfo|ceo|\bmd\b|managing director|company secretary|compliance officer).{0,60}(resign|quits?|steps? down|exit|terminat)|"
     r"(resign|quits?|steps? down|terminat).{0,60}(chief|cfo|ceo|\bmd\b|managing director|company secretary|compliance officer)", "management_exit", -1, True),
    (r"appointed|appoints|elevat|promot|re-?designat|superannuat|retire|takes? charge|named (as )?head", "management_change_routine", 0, True),
    (r"resign|steps? down|quits?", "management_change_routine", 0, True),
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
        if company.split()[0].lower() not in text:
            lab = {"event_type": "other", "sentiment": 0, "rationale": "Company not named in text.",
                   "governance_flag": False, "about_company": False}
            return lab, json.dumps(lab)
        for pat, et, s, g in _RULES:
            if re.search(pat, text):
                if et == "analyst_action":
                    s = -1 if "downgrade" in text else 1 if "upgrade" in text else 0
                lab = {"event_type": et, "sentiment": s,
                       "rationale": f"Keyword match on '{pat.split('|')[0]}' in headline.",
                       "governance_flag": g, "about_company": True}
                if g:      # offline stand-in for the verification gate: assume the company is the subject and the matter is material
                    lab.update(subject="company", occurred_at_company=True, severity="integrity" if et in ("regulatory_action", "auditor_resignation", "auditor_restatement") else "material")
                    if et in ("management_exit", "management_change_routine"):
                        lab.update(people_direction="unplanned_exit" if et == "management_exit" else "appointment",
                                   role_tier="cxo_cs_cfo_compliance" if et == "management_exit" else "senior_management")
                    lab["action_stage"] = {"regulatory_action": "order_or_settlement", "investigation": "investigation"}.get(et, "routine" if et == "management_change_routine" else "n/a")
                return lab, json.dumps(lab)
        lab = {"event_type": "other", "sentiment": 0, "rationale": "No taxonomy match in text.",
               "governance_flag": False, "about_company": True}
        return lab, json.dumps(lab)


def to_label(d: dict) -> Label:
    return Label(d["event_type"], d["sentiment"], d["rationale"], bool(d["governance_flag"]),
                 d.get("materiality"), historical=bool(d.get("historical", False)),
                 relevant=bool(d.get("about_company", True)),
                 subject=d.get("subject"), occurred_at_company=d.get("occurred_at_company"), action_stage=d.get("action_stage"),
                 severity=d.get("severity"), amount_inr_cr=d.get("amount_inr_cr"), people_direction=d.get("people_direction"),
                 role_tier=d.get("role_tier"), event_key=d.get("event_key"))
