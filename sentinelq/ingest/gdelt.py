"""Global news index: GDELT DOC 2.0, queried per company, deduplicated by URL.

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
from datetime import date

from ..models import Holding, RawItem
from .base import dedupe_by_url

API = "https://api.gdeltproject.org/api/v2/doc/doc"
RETRY_CODES = (429, 500, 502, 503, 504)


class GdeltRateLimited(RuntimeError):
    pass


class GdeltNews:
    def __init__(self, max_records: int = 100, pause: float = 8.0, timeout: int = 30, retries: int = 5,
                 backoff: float = 15.0, sleep=time.sleep, opener=None):
        self.max_records, self.base_pause, self.timeout = max_records, pause, timeout
        self.retries, self.backoff = retries, backoff
        self.pause = pause                     # current pacing; grows after a 429, decays after successes
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.on_event = None                   # callback(message) for the run display
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

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        params = {
            "query": f'"{h.name}" sourcelang:english', "mode": "artlist", "format": "json",
            "maxrecords": self.max_records, "sort": "datedesc",
            "startdatetime": start.strftime("%Y%m%d000000"),
            "enddatetime": end.strftime("%Y%m%d235959"),
        }
        body = self._get(f"{API}?{urllib.parse.urlencode(params)}", h.symbol)
        try:
            arts = json.loads(body).get("articles", []) if body.strip() else []
        except json.JSONDecodeError as e:
            raise RuntimeError(f"GDELT returned unparseable data for {h.symbol}: {body[:80]!r}") from e
        items = []
        for a in arts:
            seen = a.get("seendate", "")  # 20260615T093000Z
            d = f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
            items.append(RawItem(h.symbol, "news", a.get("title", ""), "", a.get("url", ""), d, a.get("domain", "")))
        return dedupe_by_url(items)
