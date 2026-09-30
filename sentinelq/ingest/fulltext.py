"""Optional: fetch article body text so labelling isn't headline-only (the doc's 'full-text fetch' fix)."""
from __future__ import annotations
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (SentinelQ research)"}


def _extract(raw: str, limit: int) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", raw)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", raw)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)) for p in paras)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def fetch_text(url: str, limit: int = 1800, timeout: int = 12) -> str:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return ""
            return _extract(r.read(400_000).decode("utf-8", "replace"), limit)
    except Exception:
        return ""


def enrich(items, cache_path: str | Path, workers: int = 8):
    """Fill item.snippet with body text for news items lacking one. Cached by URL."""
    p = Path(cache_path)
    cache = {}
    if p.exists():
        cache = {json.loads(l)["url"]: json.loads(l)["text"] for l in p.read_text().splitlines() if l.strip()}
    todo = [i for i in items if i.kind == "news" and not i.snippet and i.url and i.url not in cache]
    with ThreadPoolExecutor(workers) as ex:
        for i, t in zip(todo, ex.map(lambda i: fetch_text(i.url), todo)):
            cache[i.url] = t
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps({"url": i.url, "text": t}) + "\n")
    for i in items:
        if i.kind == "news" and not i.snippet:
            i.snippet = cache.get(i.url, "")
