"""Offline / bring-your-own-data providers (JSON fixtures, CSV prices)."""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from ..models import Holding, RawItem, parse_date
from .base import dedupe_by_url


class FileNews:
    """JSON: {"SYMBOL": [{"title","snippet","url","date","source"}, ...]}"""
    def __init__(self, path: str | Path):
        self.data = json.loads(Path(path).read_text())

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        rows = self.data.get(h.symbol, [])
        return dedupe_by_url([RawItem(h.symbol, "news", r.get("title", ""), r.get("snippet", ""),
                                      r.get("url", ""), r.get("date", ""), r.get("source", ""))
                              for r in rows])


class FileActions:
    """JSON: {"SYMBOL": [{"type","title","url","date","materiality","direction"}]}"""
    def __init__(self, path: str | Path):
        self.data = json.loads(Path(path).read_text())

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        out = []
        for r in self.data.get(h.symbol, []):
            sent = {"positive": 1, "negative": -1}.get(r.get("direction", ""), 0)
            out.append(RawItem(h.symbol, "action", r.get("title", r["type"]), "", r.get("url", ""),
                               r.get("date", ""), r.get("source", "exchange filing"),
                               prelabel={"event_type": r["type"], "sentiment": sent,
                                         "rationale": r.get("title", r["type"]),
                                         "governance_flag": False,
                                         "materiality": r.get("materiality", "medium")}))
        return dedupe_by_url(out)


class FilePrices:
    """CSV: date,symbol,close  (benchmark rows use symbol NIFTY)."""
    def __init__(self, path: str | Path, benchmark_symbol: str = "NIFTY"):
        self.by: dict[str, list[tuple[date, float]]] = defaultdict(list)
        with open(path) as f:
            for r in csv.DictReader(f):
                d = parse_date(r["date"])
                if d:
                    self.by[r["symbol"]].append((d, float(r["close"])))
        for v in self.by.values():
            v.sort()
        self.bench = benchmark_symbol

    def closes(self, symbol, start, end):
        return [(d, c) for d, c in self.by.get(symbol, []) if start <= d <= end]

    def benchmark(self, start, end):
        return self.closes(self.bench, start, end)
