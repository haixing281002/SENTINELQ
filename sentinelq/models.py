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
    aliases: str = ""      # pipe-separated names the company is known by (universe.csv)


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
    style: str = ""        # article style (descriptive only, see style.py)
    source_ref: str = ""         # citation for human-verified evidence that has no URL (e.g. "BSE Reg-30 filing, Bosch Ltd, 8-Apr-26")
    verified: str = ""           # "" | "verified" | "provisional" (human-verified evidence merged in)
    penalty_override: float | None = None   # human-set weight for verified evidence (recorded in the audit)
    origin: str = ""             # which pass found it: latest | fundamentals | governance | verified
    purpose: str = "sentiment"   # 'sentiment' (latest-N pass) or 'governance' (12-month governance-keyword pass)
    relevance: float = 0.0  # how much the article is ABOUT the company (title hit / mention count); ranking only
    parse: str = ""        # how the text was read: headline-only | full-text Nc | fetch-failed
    # --- Upgrade v2.1: sampling window + event fields (A1 / A3). An article that represents a cluster IS the event. ---
    window: int | None = None            # stratified window 1..4 (W1 0-30d ... W4 181-365d)
    sample_extra: bool = False           # per-day overflow: rides as an extra source, never counted against the quota
    pass_: str = ""                      # sentiment | results | governance | actions | verified
    event_id: str = ""
    event_date: str = ""                 # earliest member date
    n_sources: int = 1
    n_members: int = 1
    max_tier: str = ""                   # T1..T4 (best among members)
    confidence: float | None = None      # deterministic: min(1, 0.40 + 0.15 ln(1+n_sources)) * tier_weight
    member_url_hashes: list = field(default_factory=list)
    members: list = field(default_factory=list)   # other articles in the cluster: {url, source, date, headline, tier}
    boilerplate: bool = False


@dataclass
class Label:
    event_type: str
    sentiment: int
    rationale: str
    governance_flag: bool
    materiality: str | None = None   # only used for corporate actions / fines
    historical: bool = False         # event happened before the lookback window (background mention)
    relevant: bool = True            # item is actually about the company (else dropped)
    # --- governance verification gate (Reconciliation fix 2): who, where, how severe - answered before ANY penalty ---
    subject: str | None = None              # company | subsidiary | promoter_or_insider | victim | lender_or_counterparty | peer_or_sector | macro_policy | shareholders | other
    occurred_at_company: bool | None = None
    action_stage: str | None = None         # order_or_settlement | notice_or_demand | investigation | resolved_or_quashed | routine | n/a
    severity: str | None = None             # integrity | material | minor | procedural | immaterial | n/a
    amount_inr_cr: float | None = None      # sum at stake in Rs crore, if stated
    people_direction: str | None = None     # unplanned_exit | planned_exit_or_succession | appointment | promotion_or_elevation | reappointment | n/a
    role_tier: str | None = None            # cxo_cs_cfo_compliance | senior_management | below_cxo | non_executive_director | n/a
    event_key: str | None = None            # short id of the underlying real-world event, for same-event de-duplication
    substance: str = "primary"              # primary | passing (the company is a mention, not the subject) | boilerplate (templated, no event)


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
             "kind": self.item.kind, "purpose": self.item.purpose, "source": self.item.source,
             "style": self.item.style, "parse": self.item.parse, "window": self.item.window, "pass": self.item.pass_,
             "event_id": self.item.event_id, "event_date": self.item.event_date, "n_sources": self.item.n_sources,
             "n_members": self.item.n_members, "max_tier": self.item.max_tier, "confidence": self.item.confidence,
             "member_url_hashes": self.item.member_url_hashes, "members": self.item.members}
        if self.label:
            d.update(subject=self.label.subject, occurred_at_company=self.label.occurred_at_company, action_stage=self.label.action_stage,
                     severity=self.label.severity, amount_inr_cr=self.label.amount_inr_cr, people_direction=self.label.people_direction,
                     role_tier=self.label.role_tier, event_key=self.label.event_key, substance=self.label.substance, source_ref=self.item.source_ref,
                     verified=self.item.verified, penalty_override=self.item.penalty_override)
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
    sentiment_note: str = ""                       # why sentiment is n/a (Insufficient Data) when it is
    relevant_articles: int = 0
    evidence_first: str = ""
    evidence_last: str = ""
    evidence_span_days: int | None = None
    governance_ignored: list = field(default_factory=list)   # governance-flagged items deliberately NOT penalised, with why
    # --- Upgrade v2.1 (A4) coverage map, printed on every scorecard row ---
    coverage_map: str = ""                  # "W1:12 W2:5 W3:4 W4:3 · events 24 · results-type 9 (anchors 4/4) · T1/T2 share 61% · conf-weighted n 15.8"
    n_events: int = 0
    n_results_events: int = 0
    window_events: dict = field(default_factory=dict)
    tier12_share: float | None = None
    conf_weighted_n: float | None = None
    window_dominated: bool = False          # acceptance A5.1: one window holds > 70% of the events

    def to_dict(self):
        return asdict(self)


def parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
