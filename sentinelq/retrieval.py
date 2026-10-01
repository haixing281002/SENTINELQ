"""Retrieval policy (Reconciliation fix 1): never present a backdated live search as a point-in-time backtest."""
from __future__ import annotations
from datetime import date


def resolve_news_source(choice: str, as_of: date, today: date, backdate_days: int, allow_live_backdate: bool = False) -> tuple[str, str]:
    """Returns (source, retrieval_mode). `auto` picks the dated archive (GDELT) for a past as-of date and the live index otherwise.
    A live index with a past as-of date is refused unless explicitly allowed - and is then stamped 'live_index_backdated'."""
    backdated = (today - as_of).days > backdate_days
    if choice == "auto":
        choice = "gdelt" if backdated else "gnews"
    elif choice == "gnews" and backdated and not allow_live_backdate:
        raise SystemExit(
            f"--news gnews queries the LIVE Google News index, but the as-of date {as_of} is {(today - as_of).days} days ago.\n"
            "A date filter on a live index does not reproduce the index as it stood then (rankings decay, older articles drop out): "
            "it is NOT a point-in-time backtest.\nUse --news gdelt (dated archive; chosen automatically by --news auto), "
            "or pass --allow-live-backdate to run it anyway (the report is stamped).")
    mode = {"file": "supplied", "gdelt": "dated_archive" if backdated else "live", "gnews": "live_index_backdated" if backdated else "live"}[choice]
    return choice, mode
