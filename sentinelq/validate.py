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
    return None
