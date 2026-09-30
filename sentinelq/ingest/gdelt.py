"""Free GDELT DOC 2.0 API. Simple on purpose: ONE request per stock for its newest articles (sort=datedesc, up to 250),
keep the ones whose TITLE names the company, take the latest 50. Only if that gives fewer than 50 does it step back one
more window (DOC article lists only see ~3 months per window, so windows are 90 days). Flat short waits on a rate
limit, and it raises rather than reading a rate limit as 'no news'."""
from __future__ import annotations
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from ..models import Holding, RawItem
from .base import dedupe_by_url

API = "https://api.gdeltproject.org/api/v2/doc/doc"
RETRY_CODES = (429, 500, 502, 503, 504)


class GdeltRateLimited(RuntimeError):
    """GDELT answered, but with a rate-limit / server-busy reply."""


class GdeltUnreachable(RuntimeError):
    """No answer at all (offline, DNS, proxy, timeout)."""


class GdeltNews:
    def __init__(self, max_records: int = 250, pause: float = 6.0, timeout: int = 30, retries: int = 3,
                 backoff: float = 20.0, sleep=time.sleep, opener=None, slice_days: int = 90):
        self.max_records, self.pause, self.timeout = min(int(max_records), 250), pause, timeout
        self.retries, self.backoff = retries, backoff
        self.slice_days = max(7, min(int(slice_days), 90))
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.on_event = None                   # callback(message) for the run display
        self.stop_when = None                  # callable(items) -> True once enough articles are in hand
        self._last = 0.0

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def _pace(self) -> None:                   # GDELT asks for at most one request per ~5 s
        wait = self.pause + random.uniform(0, 1.0) - (time.time() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = time.time()

    def _get(self, url: str, label: str) -> str:
        last, kind = "", "limit"
        for attempt in range(self.retries + 1):
            self._pace()
            wait = self.backoff
            try:
                with self.opener(url, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last, kind = f"HTTP {e.code}", "limit"
                if e.code not in RETRY_CODES:
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last, kind = f"{type(e).__name__}: {e}", "network"
            else:
                if body.lstrip()[:1] in ("{", "[") or not body.strip():
                    return body
                last, kind = "rate-limit notice: " + body.strip()[:80], "limit"
            if attempt < self.retries:
                self._say(f"[GDELT] {label}: {last} - waiting {wait:.0f}s (retry {attempt + 1}/{self.retries})")
                self.sleep(min(wait, 120))
        exc = GdeltRateLimited if kind == "limit" else GdeltUnreachable
        raise exc(f"GDELT gave no usable answer for {label} after {self.retries + 1} attempts ({last})")

    def probe(self) -> None:
        """One tiny request before the run starts: if GDELT is refusing this machine, say so now - not after 29 stocks."""
        url = f"{API}?{urllib.parse.urlencode({'query': 'economy sourcelang:english', 'mode': 'artlist', 'format': 'json', 'maxrecords': 1, 'timespan': '1h'})}"
        self._get(url, "health check")

    def _query(self, h: Holding) -> str:
        from .select import aliases_of
        names = aliases_of(h)[:4]
        q = " OR ".join(f'"{n}"' for n in names)
        return (f"({q})" if len(names) > 1 else q) + " sourcelang:english"

    def _windows(self, start: date, end: date):
        hi = end
        while hi >= start:
            lo = max(start, hi - timedelta(days=self.slice_days - 1))
            yield lo, hi
            hi = lo - timedelta(days=1)

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        items: list[RawItem] = []
        wins = list(self._windows(start, end))
        for k, (lo, hi) in enumerate(wins, 1):
            params = {"query": self._query(h), "mode": "artlist", "format": "json", "maxrecords": self.max_records,
                      "sort": "datedesc", "startdatetime": lo.strftime("%Y%m%d000000"), "enddatetime": hi.strftime("%Y%m%d235959")}
            body = self._get(f"{API}?{urllib.parse.urlencode(params)}", f"{h.symbol} {lo}..{hi}")
            try:
                arts = json.loads(body).get("articles", []) if body.strip() else []
            except json.JSONDecodeError as e:
                raise RuntimeError(f"GDELT returned unparseable data for {h.symbol}: {body[:80]!r}") from e
            for a in arts:
                seen = a.get("seendate", "")  # 20260615T093000Z
                d = f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
                items.append(RawItem(h.symbol, "news", a.get("title", ""), "", a.get("url", ""), d, a.get("domain", "")))
            self._say(f"[GDELT] {h.symbol} {lo}..{hi}: {len(arts)} articles")
            if self.stop_when and self.stop_when(dedupe_by_url(items)):
                break
        return dedupe_by_url(items)
