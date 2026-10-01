"""Stage 4 - schema validation. Failures retry once, then are dropped with a logged reason."""
from __future__ import annotations
from datetime import date, timedelta

from .models import Label, RawItem, parse_date
from .rubric import Rubric


def validate_label(item: RawItem, label_dict: dict | None, r: Rubric,
                   as_of: date, lookback_days: int) -> str | None:
    """Return None if valid, else a reason string."""
    if not item.url or not item.url.strip():
        return "missing_source_url"
    if not item.url.lower().startswith(("http://", "https://")):
        return "invalid_source_url"
    d = parse_date(item.published)
    if d is None:
        return "missing_or_invalid_date"
    if d > as_of:
        return "date_in_future"
    if d < as_of - timedelta(days=lookback_days):
        return "date_outside_lookback"
    if label_dict is None:
        return "no_structured_output"
    ev = label_dict.get("event_type")
    if ev not in r.event_types:
        return f"invalid_event_type:{ev}"
    s = label_dict.get("sentiment")
    if isinstance(s, bool) or not isinstance(s, int) or not (r.sent_min <= s <= r.sent_max):
        return f"sentiment_out_of_range:{s}"
    if not isinstance(label_dict.get("rationale"), str) or not label_dict["rationale"].strip():
        return "empty_rationale"
    if not isinstance(label_dict.get("governance_flag"), bool):
        return "invalid_governance_flag"
    m = label_dict.get("materiality")
    if m is not None and m not in r["materiality_levels"]:
        return f"invalid_materiality:{m}"
    if "about_company" in label_dict and not isinstance(label_dict["about_company"], bool):
        return "invalid_about_company"
    if label_dict.get("about_company") is False:
        return "not_about_company"
    if "historical" in label_dict and not isinstance(label_dict["historical"], bool):
        return "invalid_historical"
    bad = _check_gate(label_dict, r)
    if bad:
        return bad
    return None


_GATE_ENUMS = {
    "subject": {"company", "subsidiary", "promoter_or_insider", "victim", "lender_or_counterparty", "peer_or_sector", "macro_policy", "shareholders", "other"},
    "action_stage": {"order_or_settlement", "notice_or_demand", "investigation", "resolved_or_quashed", "routine", "n/a"},
    "severity": {"integrity", "material", "minor", "procedural", "immaterial", "n/a"},
    "people_direction": {"unplanned_exit", "planned_exit_or_succession", "appointment", "promotion_or_elevation", "reappointment", "n/a"},
    "role_tier": {"cxo_cs_cfo_compliance", "senior_management", "below_cxo", "non_executive_director", "n/a"},
}
_PEOPLE = {"management_exit", "management_change_routine"}
_STAGED = {"regulatory_action", "investigation", "exchange_fine", "rpt_concern", "litigation"}


def _check_gate(d: dict, r: Rubric) -> str | None:
    """Governance verification gate (Reconciliation fix 2): a governance-flagged item must say WHO the event is about, whether it happened
    at the company, and how severe it is - otherwise no penalty can be justified, so it is retried once and then dropped with this reason."""
    for f, allowed in _GATE_ENUMS.items():
        if d.get(f) is not None and d[f] not in allowed:
            return f"invalid_{f}:{d[f]}"
    if d.get("amount_inr_cr") is not None and (isinstance(d["amount_inr_cr"], bool) or not isinstance(d["amount_inr_cr"], (int, float))):
        return "invalid_amount_inr_cr"
    g = r["governance"]
    if d.get("governance_flag") and (d["event_type"] in g["penalties"] or d["event_type"] in g.get("routine_types", {})):
        need = ["subject", "severity"]
        if d["event_type"] in _PEOPLE:
            need += ["people_direction", "role_tier"]
        if d["event_type"] in _STAGED:
            need += ["action_stage"]
        for f in need:
            if d.get(f) is None:
                return f"incomplete_governance_verification:{f}"
    return None
