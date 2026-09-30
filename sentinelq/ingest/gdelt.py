"""Global news index: GDELT DOC 2.0, queried per company, deduplicated by URL."""
from __future__ import annotations
import json
import time
import urllib.parse
import urllib.request
from datetime import date

from ..models import Holding, RawItem
from .base import dedupe_by_url

API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GdeltNews:
    def __init__(self, max_records: int = 100, pause: float = 5.5, timeout: int = 30):
        self.max_records, self.pause, self.timeout = max_records, pause, timeout

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        q = f'"{h.name}" sourcelang:english'
        params = {
            "query": q, "mode": "artlist", "format": "json",
            "maxrecords": self.max_records, "sort": "datedesc",
            "startdatetime": start.strftime("%Y%m%d000000"),
            "enddatetime": end.strftime("%Y%m%d235959"),
        }
        url = f"{API}?{urllib.parse.urlencode(params)}"
        time.sleep(self.pause)  # GDELT rate limit
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            arts = json.loads(body).get("articles", [])
        except json.JSONDecodeError:
            arts = []
        items = []
        for a in arts:
            seen = a.get("seendate", "")  # 20260615T093000Z
            d = f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
            items.append(RawItem(h.symbol, "news", a.get("title", ""), "",
                                 a.get("url", ""), d, a.get("domain", "")))
        return dedupe_by_url(items)
