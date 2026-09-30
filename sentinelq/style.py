"""Deterministic article-style detector (no model): tells you what kind of article each item is, so the run display
and the audit trail show how each was treated. Purely descriptive - it never feeds a score."""
from __future__ import annotations
import re
from urllib.parse import urlparse

FILING_DOMAINS = ("bseindia.com", "nseindia.com", "sebi.gov.in", "rbi.org.in", "mca.gov.in", "cci.gov.in")
PR_DOMAINS = ("prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com", "accesswire.com")
PRESS_DOMAINS = ("reuters.com", "bloomberg.com", "economictimes.indiatimes.com", "business-standard.com", "livemint.com",
                 "moneycontrol.com", "thehindubusinessline.com", "financialexpress.com", "ndtvprofit.com", "cnbctv18.com",
                 "thehindu.com", "indianexpress.com", "hindustantimes.com", "ft.com", "wsj.com", "timesofindia.indiatimes.com")

_RULES = [
    ("exchange-filing", r"regulation 30|disclosure under|outcome of board meeting|intimation (of|to)|sebi (order|settlement)|"
                        r"filing with (bse|nse)|stock exchange (filing|disclosure)"),
    ("press-release", r"press release|\bannounces\b.*\b(launch|partnership|appointment)|\bpr newswire\b"),
    ("market-roundup", r"\bsensex\b|\bnifty\b|stocks? to (watch|buy)|top (gainers|losers)|market (wrap|close|update)|"
                       r"stocks? in news|buzzing|stock market today|closing bell|opening bell|shares? (in focus|to watch)"),
    ("broker-note", r"target price|price target|\bupgrades?\b|\bdowngrades?\b|initiates? coverage|\b(buy|sell|hold|accumulate|"
                    r"outperform|overweight|underweight) (rating|call)\b|maintains? (buy|sell|hold)|brokerage|analysts? (say|see|expect)|"
                    r"motilal|jefferies|nomura|kotak institutional|icici securities|jm financial|cll?sa|goldman sachs|morgan stanley"),
    ("results", r"\bq[1-4]\b|quarterly (results|earnings)|net profit|\bpat\b|revenue (rises|jumps|falls|up|down)|earnings|"
                r"\bresults?\b|profit (rises|jumps|falls|drops|surges|declines)|\bebitda\b|order book"),
    ("corporate-action", r"dividend|buy-?back|\bbonus\b|stock split|rights issue|\bqip\b|acquisition|acquires|merger|demerger|delist"),
    ("opinion-feature", r"\bopinion\b|editorial|\banalysis\b|explained|\bwhy\b|what (next|it means)|column|deep dive"),
]


def _domain(item) -> str:
    d = (item.source or "").lower() or (urlparse(item.url or "").netloc.lower())
    return d[4:] if d.startswith("www.") else d


def classify_style(item) -> str:
    t = f"{item.title} {item.snippet or ''}".lower()
    dom = _domain(item)
    if item.kind == "action":
        return "corporate-action"
    if any(dom.endswith(x) for x in FILING_DOMAINS):
        return "exchange-filing"
    if any(dom.endswith(x) for x in PR_DOMAINS):
        return "press-release"
    for name, pat in _RULES:
        if re.search(pat, t):
            return name
    if any(dom.endswith(x) for x in PRESS_DOMAINS):
        return "business-news"
    return "general-news"
