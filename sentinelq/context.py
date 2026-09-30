"""Price context layer. INTERPRETATION ONLY - this module is never imported by score.py."""
from __future__ import annotations
import math
from datetime import date, timedelta


def _ret(series, days, end):
    pts = [(d, c) for d, c in series if d <= end]
    if len(pts) < 2:
        return None
    last_d, last_c = pts[-1]
    base = [(d, c) for d, c in pts if d <= last_d - timedelta(days=days)]
    if not base:
        return None
    return last_c / base[-1][1] - 1


def _vol(series):
    cs = [c for _, c in series]
    if len(cs) < 3:
        return None
    rets = [math.log(b / a) for a, b in zip(cs, cs[1:]) if a > 0 and b > 0]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def quadrant(sentiment: float | None, excess_1m: float | None) -> str:
    if sentiment is None or excess_1m is None:
        return "n/a"
    if sentiment < 0:
        return "negative & underperformed: priced-in" if excess_1m < 0 else "negative & not sold off: potential unpriced risk"
    if sentiment > 0:
        return "positive & outperformed: confirmed" if excess_1m > 0 else "positive & lagging: not yet reflected"
    return "neutral sentiment"


def price_context(symbol: str, sentiment: float | None, prices, end: date, days: int) -> dict:
    start = end - timedelta(days=days)
    s, b = prices.closes(symbol, start, end), prices.benchmark(start, end)
    row = {"symbol": symbol, "ret_1m": _ret(s, 30, end), "ret_3m": _ret(s, 91, end),
           "ret_6m": _ret(s, 182, end), "realized_vol_ann": _vol(s),
           "bench_ret_1m": _ret(b, 30, end)}
    row["excess_1m"] = (row["ret_1m"] - row["bench_ret_1m"]
                        if row["ret_1m"] is not None and row["bench_ret_1m"] is not None else None)
    row["quadrant"] = quadrant(sentiment, row["excess_1m"])
    return row
