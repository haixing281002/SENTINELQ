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


def enrich(items, cache_path=None, workers: int = 8, progress=None):
    """Read each news article's body text into item.snippet - IN MEMORY ONLY. `cache_path` is accepted for
    compatibility but nothing is ever written: article text is discarded once the item has been labelled."""
    todo = [i for i in items if i.kind == "news" and not i.snippet and i.url]
    with ThreadPoolExecutor(workers) as ex:
        for n, (i, text) in enumerate(zip(todo, ex.map(lambda i: fetch_text(i.url), todo)), 1):
            i.snippet = text
            i.parse = f"full-text {len(text) / 1000:.1f}k" if text else "headline-only (no text)"
            if progress:
                progress(n, len(todo))
    for i in items:
        if i.kind == "news" and i.snippet and not i.parse.startswith("full-text"):
            i.parse = f"full-text {len(i.snippet) / 1000:.1f}k"
