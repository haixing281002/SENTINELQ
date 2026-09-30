from __future__ import annotations
from datetime import date
from typing import Protocol
from ..models import Holding, RawItem


class NewsProvider(Protocol):
    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]: ...


class ActionsProvider(Protocol):
    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]: ...


class PriceProvider(Protocol):
    def closes(self, symbol: str, start: date, end: date) -> list[tuple[date, float]]: ...
    def benchmark(self, start: date, end: date) -> list[tuple[date, float]]: ...


def dedupe_by_url(items: list[RawItem]) -> list[RawItem]:
    seen, out = set(), []
    for it in items:
        k = it.url.strip().lower().rstrip("/")
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(it)   # items with no URL are kept so validation can drop + disclose them
    return out
