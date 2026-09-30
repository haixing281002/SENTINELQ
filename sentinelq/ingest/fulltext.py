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


def fetch_text(url: str, limit: int = 1800, timeout: int = 6) -> str:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "html" not in (r.headers.get("Content-Type") or "html"):
                return ""
            return _extract(r.read(400_000).decode("utf-8", "replace"), limit)
    except Exception:
        return ""


def enrich(items, cache_path=None, workers: int = 16, progress=None, budget: float = 25.0):
    """Read each news article's body into item.snippet - IN MEMORY ONLY, within a time budget per stock (default 25 s).
    Whatever has not arrived by then is read from its headline alone; the run never waits on slow or paywalled sites.
    `cache_path` is accepted for compatibility; nothing is written."""
    from concurrent.futures import wait
    todo = [i for i in items if i.kind == "news" and not i.snippet and i.url]
    ex = ThreadPoolExecutor(workers)
    futs = {ex.submit(fetch_text, i.url): i for i in todo}
    done, _pending = wait(list(futs), timeout=budget)
    ex.shutdown(wait=False, cancel_futures=True)
    for n, (fu, i) in enumerate(futs.items(), 1):
        text = ""
        if fu in done and not fu.cancelled():
            try:
                text = fu.result()
            except Exception:
                text = ""
        i.snippet = text
        i.parse = f"full-text {len(text) / 1000:.1f}k" if text else ("headline-only (no text)" if fu in done else "headline-only (timed out)")
        if progress:
            progress(n, len(todo))
    for i in items:
        if i.kind == "news" and i.snippet and not i.parse.startswith("full-text"):
            i.parse = f"full-text {len(i.snippet) / 1000:.1f}k"
