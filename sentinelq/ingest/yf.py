"""Yahoo Finance (optional dep `yfinance`): dividends/splits as actions, adjusted closes for prices."""
from __future__ import annotations
from datetime import date, timedelta

from ..models import Holding, RawItem
from .base import dedupe_by_url


def _ticker(sym: str) -> str:
    return sym if "." in sym or sym.startswith("^") else f"{sym}.NS"


class YahooActions:
    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        import yfinance as yf
        t = yf.Ticker(_ticker(h.symbol))
        url = f"https://finance.yahoo.com/quote/{_ticker(h.symbol)}/history"
        last = None
        try:
            hist = t.history(period="5d")
            last = float(hist["Close"].iloc[-1]) if len(hist) else None
        except Exception:
            pass
        out = []
        acts = t.actions
        for ts, row in acts.iterrows():
            d = ts.date()
            if not (start <= d <= end):
                continue
            div, split = float(row.get("Dividends", 0) or 0), float(row.get("Stock Splits", 0) or 0)
            if div > 0:
                yld = (div / last) if last else 0.0
                mat = "high" if yld >= 0.03 else "medium" if yld >= 0.01 else "low"
                out.append(RawItem(h.symbol, "action", f"Dividend {div:g}/share", "", f"{url}#div-{d}",
                                   d.isoformat(), "yahoo",
                                   prelabel={"event_type": "dividend", "sentiment": 1,
                                             "rationale": f"Dividend of {div:g}/share (~{yld:.1%} of price)",
                                             "governance_flag": False, "materiality": mat}))
            if split > 0:
                out.append(RawItem(h.symbol, "action", f"Stock split/bonus {split:g}:1", "", f"{url}#split-{d}",
                                   d.isoformat(), "yahoo",
                                   prelabel={"event_type": "split_bonus", "sentiment": 1,
                                             "rationale": f"Split/bonus ratio {split:g}:1",
                                             "governance_flag": False, "materiality": "low"}))
        return dedupe_by_url(out)


class YahooPrices:
    def __init__(self, benchmark: str = "^NSEI"):
        self.bm = benchmark

    def _get(self, tk, start, end):
        import yfinance as yf
        df = yf.Ticker(tk).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                   auto_adjust=True)
        return [(i.date(), float(c)) for i, c in df["Close"].items()]

    def closes(self, symbol, start, end):
        return self._get(_ticker(symbol), start, end)

    def benchmark(self, start, end):
        return self._get(self.bm, start, end)
