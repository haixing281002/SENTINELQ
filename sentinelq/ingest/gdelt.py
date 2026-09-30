"""Global news index: GDELT DOC 2.0, queried per company, deduplicated by URL.

IMPORTANT (verified in GDELT's own posts): in article-list mode the DOC API only considers the most recent ~3 months of
the search window, however wide start/end are. A single 12-month request therefore silently returns ~3 months. This
client asks month-sized windows (slice_days) instead and merges them, so the whole lookback is really searched.

GDELT allows roughly one request per 5 seconds per IP and answers 429 (or a plain-text 'please limit requests'
body with HTTP 200) when exceeded. This client paces itself, backs off exponentially (honouring Retry-After), slows
down adaptively after a limit hit, and RAISES if it cannot get an answer - a rate limit must never look like
'this company has no news'."""
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
    pass


class GdeltNews:
    def __init__(self, max_records: int = 250, pause: float = 8.0, timeout: int = 30, retries: int = 5,
                 backoff: float = 15.0, sleep=time.sleep, opener=None, slice_days: int = 30):
        self.max_records, self.base_pause, self.timeout = min(int(max_records), 250), pause, timeout
        self.slice_days = max(7, min(int(slice_days), 90))
        self.retries, self.backoff = retries, backoff
        self.pause = pause                     # current pacing; grows after a 429, decays after successes
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.on_event = None                   # callback(message) for the run display
        self.stop_when = None                  # callable(items) -> True once enough usable articles are in hand
        self._last = 0.0

    def _say(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def _pace(self) -> None:
        wait = self.pause + random.uniform(0, 1.5) - (time.time() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = time.time()

    def _get(self, url: str, label: str) -> str:
        last = ""
        for attempt in range(self.retries + 1):
            self._pace()
            wait = self.backoff * (2 ** attempt)
            try:
                with self.opener(url, timeout=self.timeout) as r:
                    body = r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in RETRY_CODES:
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else wait
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = f"{type(e).__name__}: {e}"
            else:
                head = body.lstrip()[:1]
                if head in ("{", "[") or not body.strip():
                    self.pause = max(self.base_pause, self.pause * 0.9)       # healthy: speed back up
                    return body
                last = "rate-limit notice: " + body.strip()[:80]
            self.pause = min(self.pause * 1.5, 30.0)                          # unhealthy: slow down
            if attempt < self.retries:
                self._say(f"[GDELT] {label}: {last} - waiting {wait:.0f}s (retry {attempt + 1}/{self.retries}, pacing now {self.pause:.0f}s)")
                self.sleep(min(wait, 240))
        raise GdeltRateLimited(f"GDELT gave no usable answer for {label} after {self.retries + 1} attempts ({last})")

    def _query(self, h: Holding) -> str:
        from .select import aliases_of
        names = aliases_of(h)[:4]
        q = " OR ".join(f'"{n}"' for n in names)
        return (f"({q})" if len(names) > 1 else q) + " sourcelang:english"

    def _slices(self, start: date, end: date):
        """Newest-first windows of slice_days covering [start, end]: the window rolls BACK from the as-of date."""
        hi = end
        while hi >= start:
            lo = max(start, hi - timedelta(days=self.slice_days - 1))
            yield lo, hi
            hi = lo - timedelta(days=1)

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        items: list[RawItem] = []
        slices = list(self._slices(start, end))
        for k, (lo, hi) in enumerate(slices, 1):
            params = {"query": self._query(h), "mode": "artlist", "format": "json", "maxrecords": self.max_records,
                      "startdatetime": lo.strftime("%Y%m%d000000"), "enddatetime": hi.strftime("%Y%m%d235959")}
            body = self._get(f"{API}?{urllib.parse.urlencode(params)}", f"{h.symbol} {lo}..{hi}")
            try:
                arts = json.loads(body).get("articles", []) if body.strip() else []
            except json.JSONDecodeError as e:
                raise RuntimeError(f"GDELT returned unparseable data for {h.symbol} {lo}..{hi}: {body[:80]!r}") from e
            for a in arts:
                seen = a.get("seendate", "")  # 20260615T093000Z
                d = f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
                items.append(RawItem(h.symbol, "news", a.get("title", ""), "", a.get("url", ""), d, a.get("domain", "")))
            self._say(f"[GDELT] {h.symbol} window {k}/{len(slices)} {lo}..{hi}: {len(arts)} articles"
                      + ("  (hit the 250 cap - this window has more)" if len(arts) >= self.max_records else ""))
            if self.stop_when and self.stop_when(dedupe_by_url(items)):
                self._say(f"[GDELT] {h.symbol}: enough recent articles after rolling back to {lo} - stopping")
                break
        return dedupe_by_url(items)
