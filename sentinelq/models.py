"""Data contracts passed between stages."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Holding:
    symbol: str
    name: str
    sector: str
    cap: str = ""
    weight: str = ""


@dataclass
class RawItem:
    """An ingested item. Source URL and date travel with it from the start."""
    symbol: str
    kind: str                 # "news" | "action"
    title: str
    snippet: str
    url: str
    published: str            # ISO date (YYYY-MM-DD) or "" if unknown
    source: str = ""
    # Pre-labelled structured actions (dividends/splits) skip the LLM.
    prelabel: dict | None = None


@dataclass
class Label:
    event_type: str
    sentiment: int
    rationale: str
    governance_flag: bool
    materiality: str | None = None   # only used for corporate actions / fines
    historical: bool = False         # event happened before the lookback window (background mention)


@dataclass
class LabelledItem:
    item: RawItem
    label: Label | None
    raw_response: str = ""
    from_cache: bool = False
    attempts: int = 1

    def to_row(self) -> dict:
        d = {"symbol": self.item.symbol, "date": self.item.published,
             "headline": self.item.title, "url": self.item.url,
             "kind": self.item.kind}
        if self.label:
            d.update(event_type=self.label.event_type, sentiment=self.label.sentiment,
                     rationale=self.label.rationale,
                     governance_flag=self.label.governance_flag,
                     materiality=self.label.materiality, historical=self.label.historical)
        return d


@dataclass
class Dropped:
    symbol: str
    headline: str
    url: str
    date: str
    stage: str
    reason: str


@dataclass
class StockScore:
    symbol: str
    name: str
    sector: str
    company_sentiment: int | None          # displayed integer score, -2..+2
    company_sentiment_raw: float | None    # recency-weighted mean before rounding
    sector_sentiment: float | None
    governance_score: float
    governance_label: str
    governance_penalties: list[dict] = field(default_factory=list)
    corporate_action_score: float = 0.0
    corporate_action_detail: list[dict] = field(default_factory=list)
    n_items: int = 0
    n_dropped: int = 0
    low_confidence: bool = False
    rationale: str = ""
    cap: str = ""
    weight: str = ""

    def to_dict(self):
        return asdict(self)


def parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
