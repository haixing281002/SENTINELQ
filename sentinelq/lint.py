"""Lints that enforce the cardinal rules mechanically (Reconciliation fixes 3 and 5).

* Price language never feeds sentiment: a headline that is only about the share price becomes `price_move` (sentiment 0, excluded), and
  price sentences are stripped from sentiment commentary. Price lives in the context overlay only.
* Corporate actions: repeat headlines of one declaration are ONE action.
"""
from __future__ import annotations
import re

_FUNDAMENTAL = re.compile(r"result|profit|\bpat\b|revenue|earnings|guidance|order|dividend|buy-?back|bonus|upgrade|downgrade|target|rating|"
                          r"capex|expansion|acquisition|merger|approval|launch|contract|sebi|rbi|penalt|probe|resign|appoint", re.I)


def _patterns(r):
    return [re.compile(p, re.I) for p in r["lint"]["price_language"]]


def price_only(headline: str, r) -> bool:
    """True if the headline is about the share price and carries no fundamental event."""
    return any(p.search(headline) for p in _patterns(r)) and not _FUNDAMENTAL.search(headline)


def strip_price_sentences(text: str, r) -> tuple[str, list[str]]:
    """Remove sentences that reason from share-price moves (unless explicitly tagged '[context]'). Returns (clean text, removed)."""
    pats = _patterns(r)
    keep, removed = [], []
    for para in re.split(r"\n\s*\n", text or ""):
        sents = re.split(r"(?<=[.!?])\s+", para)
        good = [x for x in sents if "[context]" in x or not any(p.search(x) for p in pats)]
        removed += [x for x in sents if x not in good]
        if good:
            keep.append(" ".join(good).replace("[context]", "").strip())
    return "\n\n".join(keep), removed


def _numbers(title: str) -> tuple:
    return tuple(sorted(re.findall(r"\d+(?:\.\d+)?", title.replace(",", ""))))


def unique_actions(items, r):
    """Collapse repeat headlines of one corporate action: same type + same figures within `dividend_days` (dividends) / `other_days`."""
    from datetime import date
    cfg = r["corporate_actions"]["dedupe"]
    out, seen = [], []
    for li in sorted(items, key=lambda x: x.item.published):
        et = li.label.event_type
        if et not in r["corporate_actions"]["base"] and et != "capacity_expansion":
            continue
        nums = _numbers(li.item.title) or tuple(sorted(w for w in re.findall(r"[a-z]{4,}", li.item.title.lower()))[:6])
        days = cfg["dividend_days"] if et == "dividend" else cfg["other_days"]
        try:
            d = date.fromisoformat(li.item.published)
        except ValueError:
            d = None
        dup = any(k == (et, nums) and (d is None or dd is None or abs((d - dd).days) <= days) for k, dd in seen)
        seen.append(((et, nums), d))
        if not dup:
            out.append(li)
    return out
