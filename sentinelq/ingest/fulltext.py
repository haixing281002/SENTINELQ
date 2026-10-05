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


_GLINK = re.compile(r'href="(https?://(?!news\.google\.com|www\.google\.com|accounts\.google)[^"]+)"')


def resolve_google_link(url: str, timeout: int = 6) -> str:
    """Google News RSS links are redirect pages; the publisher URL is the first outbound link on them (free, no API)."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            if "news.google.com" not in final:
                return final
            page = r.read(200_000).decode("utf-8", "replace")
        m = _GLINK.search(page)
        return html.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def _pdf_text(raw: bytes, limit: int) -> str:
    try:
        import pymupdf
        doc = pymupdf.open(stream=raw, filetype="pdf")
        text = " ".join(doc[i].get_text() for i in range(min(3, doc.page_count)))
        return re.sub(r"\s+", " ", text).strip()[:limit]
    except Exception:
        return ""


def fetch_text(url: str, limit: int = 1800, timeout: int = 6) -> str:
    try:
        if "news.google.com" in url:
            url = resolve_google_link(url, timeout)
            if not url:
                return ""
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "html").lower()
            raw = r.read(1_500_000)
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                return _pdf_text(raw, limit)                   # exchange filings are PDFs: first pages only, in memory
            if "html" not in ctype:
                return ""
            return _extract(raw.decode("utf-8", "replace"), limit)
    except Exception:
        return ""


def enrich(items, cache_path=None, workers: int = 16, progress=None, budget: float = 25.0):
    """Read each news article's body into item.snippet - IN MEMORY ONLY, within a time budget per stock (default 25 s).
    Whatever has not arrived by then is read from its headline alone; the run never waits on slow or paywalled sites.
    `cache_path` is accepted for compatibility; nothing is written."""
    from concurrent.futures import wait
    todo = [i for i in items if i.kind == "news" and i.url and (not i.snippet or i.style == "filing")]
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
