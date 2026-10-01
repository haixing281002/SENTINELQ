"""Google News RSS news source - the mechanism extracted from Harshil's Nifty-50 sentiment script (free, no key, no billing).

What the original does (google_news_rss_articles):
  * GET https://news.google.com/rss/search  with  q = "<company name>" (phrase) + a recency operator,
    hl=en-IN, gl=IN, ceid=IN:en  (India edition, English)
  * parse the RSS XML: channel/item -> title, link, pubDate, <source>publisher</source>
  * strip the trailing " - Publisher" from the title, keep the newest `limit` items, ~1 s between requests
  * the text scored is the TITLE only (RSS carries no body)
What this module adds for Sentinel Q: aliases (OR-queries), explicit after:/before: windows so an as-of date in the past works,
rolling back through 30/90/180/365-day windows until enough articles are in hand, and short flat waits on a rate limit.
Nothing is stored. Run it on its own:   python -m sentinelq.ingest.gnews "Angel One" --days 60 --limit 50
"""
from __future__ import annotations
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ..models import Holding, RawItem
from .base import dedupe_by_url
from .gdelt import GdeltRateLimited, GdeltUnreachable

BASE = "https://news.google.com/rss/search"
HEADERS = {"User-Agent": "sentinelq-news/1.0 (research tool)"}
RETRY_CODES = (429, 500, 502, 503, 504)
WINDOW_MARKS = (30, 90, 180, 365)          # days back from the as-of date; each step is a new, non-overlapping window
_SEPARATORS = (" - ", " — ", " – ")


def strip_source_suffix(title: str, source: str) -> str:
    """Google News appends ' - Publisher' to every title (same clean-up as the original script)."""
    for sep in _SEPARATORS:
        if source and title.endswith(f"{sep}{source}"):
            return title[: -len(sep + source)].rstrip()
    for sep in _SEPARATORS:                      # publisher name not given exactly: drop the last ' - xxx' chunk
        head, _, tail = title.rpartition(sep)
        if head and 0 < len(tail) <= 40:
            return head.rstrip()
    return title


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.findall("./channel/item"):
        title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        src = it.find("source")
        name = (src.text or "").strip() if src is not None else ""
        dom = urlparse(src.get("url", "")).netloc.lower().removeprefix("www.") if src is not None and src.get("url") else ""
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(timezone.utc).date()
        except (TypeError, ValueError):
            dt = None
        out.append({"title": strip_source_suffix(title, name), "url": link, "source": dom or name or "Google News",
                    "date": dt})
    return out


class GoogleNewsRSS:
    def __init__(self, max_items: int = 100, pause: float = 1.0, timeout: int = 30, retries: int = 3, backoff: float = 20.0,
                 sleep=time.sleep, opener=None, marks=WINDOW_MARKS):
        self.max_items, self.pause, self.timeout = max_items, pause, timeout
        self.retries, self.backoff, self.marks = retries, backoff, tuple(marks)
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.on_event = None                    # callback(message) for the run display
        self.stop_when = None                   # callable(items) -> True once enough articles are in hand
        self._last = 0.0

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def _pace(self) -> None:
        wait = self.pause + random.uniform(0, 0.5) - (time.time() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = time.time()

    def _get(self, url: str, label: str) -> bytes:
        last, kind = "", "limit"
        for attempt in range(self.retries + 1):
            self._pace()
            wait = self.backoff
            try:
                with self.opener(urllib.request.Request(url, headers=HEADERS), timeout=self.timeout) as r:
                    body = r.read()
                if body.lstrip()[:5] in (b"<?xml", b"<rss ") or b"<rss" in body[:200]:
                    return body
                last, kind = "non-RSS reply (likely a consent / block page)", "limit"
            except urllib.error.HTTPError as e:
                last, kind = f"HTTP {e.code}", "limit"
                if e.code not in RETRY_CODES:
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last, kind = f"{type(e).__name__}: {e}", "network"
            if attempt < self.retries:
                self._say(f"[GoogleNews] {label}: {last} - waiting {wait:.0f}s (retry {attempt + 1}/{self.retries})")
                self.sleep(min(wait, 120))
        raise (GdeltRateLimited if kind == "limit" else GdeltUnreachable)(
            f"Google News gave no usable answer for {label} after {self.retries + 1} attempts ({last})")

    def _url(self, query: str) -> str:
        return f"{BASE}?{urllib.parse.urlencode({'q': query, 'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'})}"

    def probe(self) -> None:
        self._get(self._url("India economy when:1d"), "health check")

    def _query(self, h: Holding, lo: date, hi: date, extra: str = "") -> str:
        from .select import aliases_of
        names = aliases_of(h)[:4]
        q = " OR ".join(f'"{n}"' for n in names)
        q = f"({q})" if len(names) > 1 else q
        return f"{q} {extra} after:{lo.isoformat()} before:{(hi + timedelta(days=1)).isoformat()}".replace("  ", " ")

    def _windows(self, start: date, end: date):
        prev = 0
        for m in self.marks:
            hi, lo = end - timedelta(days=prev), max(start, end - timedelta(days=m - 1))
            if hi < start:
                return
            yield lo, hi
            prev = m
            if lo <= start:
                return

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        items: list[RawItem] = []
        for k, (lo, hi) in enumerate(self._windows(start, end), 1):
            rows = parse_feed(self._get(self._url(self._query(h, lo, hi)), f"{h.symbol} {lo}..{hi}"))[: self.max_items]
            inside = [r for r in rows if r["date"] and lo <= r["date"] <= hi]
            if rows and not inside:
                self._say(f"[GoogleNews] {h.symbol} {lo}..{hi}: {len(rows)} items but none inside the window (date operator ignored?)")
            for r in inside:
                items.append(RawItem(h.symbol, "news", r["title"], "", r["url"], r["date"].isoformat(), r["source"]))
            self._say(f"[GoogleNews] {h.symbol} {lo}..{hi}: {len(inside)} articles")
            if self.stop_when and self.stop_when(dedupe_by_url(items)):
                break
        return dedupe_by_url(items)

    def fetch_governance(self, h: Holding, start: date, end: date) -> list[RawItem]:
        """Second, independent search across the WHOLE lookback with governance keywords (leadership + regulatory), so a CXO exit
        or regulatory order from months ago is found even when the latest-N headlines are all about something else."""
        from .select import GOV_QUERY_LEADERSHIP, GOV_QUERY_REGULATORY
        items: list[RawItem] = []
        saved = self.marks
        self.marks = (180, 365)
        try:
            wins = list(self._windows(start, end))
        finally:
            self.marks = saved
        for extra in (GOV_QUERY_LEADERSHIP, GOV_QUERY_REGULATORY):
            for lo, hi in wins:
                rows = parse_feed(self._get(self._url(self._query(h, lo, hi, extra)), f"{h.symbol} governance {lo}..{hi}"))[: self.max_items]
                for r in rows:
                    if r["date"] and lo <= r["date"] <= hi:
                        items.append(RawItem(h.symbol, "news", r["title"], "", r["url"], r["date"].isoformat(), r["source"], purpose="governance"))
        self._say(f"[GoogleNews] {h.symbol} governance pass: {len(items)} candidate articles over {start}..{end}")
        return dedupe_by_url(items)


if __name__ == "__main__":      # isolated run:  python -m sentinelq.ingest.gnews "Angel One" --days 60 --limit 50
    import argparse
    ap = argparse.ArgumentParser(description="Pull the latest Google News RSS headlines for one company")
    ap.add_argument("company")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args()
    from .select import name_tokens, select_articles
    h = Holding(a.company.upper().replace(" ", ""), a.company, "")
    g = GoogleNewsRSS()
    g.on_event = print
    end = date.today()
    got = select_articles(g.fetch(h, end - timedelta(days=a.days), end), a.limit, name_tokens(h))
    print(f"\n{len(got)} articles for {a.company!r} (latest first):")
    for i in got:
        print(f"  {i.published}  {i.source[:28]:<28} {i.title[:95]}")
