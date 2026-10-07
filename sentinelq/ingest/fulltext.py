"""Optional: fetch article body text so labelling isn't headline-only (the doc's 'full-text fetch' fix)."""
from __future__ import annotations
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-IN,en;q=0.9"}
_JUNK = re.compile(r"(?i)cookie|subscribe|sign up|log ?in\b|newsletter|download (the )?app|advertisement|all rights reserved|follow us|"
                   r"outdated browser|get notified|whatsapp|telegram|-->|›")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _extract(raw: str, limit: int) -> str:
    """Article body, best source first: the page's own structured articleBody (JSON-LD), then its real paragraphs
    (menus, cookie and subscribe lines dropped) led by the summary tag, then the summary tag alone."""
    for m in re.finditer(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", raw):
        try:
            j = json.loads(m.group(1).strip())
        except Exception:
            continue
        objs = j if isinstance(j, list) else (j.get("@graph", [j]) if isinstance(j, dict) else [])
        for o in objs:
            if isinstance(o, dict) and isinstance(o.get("articleBody"), str) and len(o["articleBody"]) > 200:
                return _clean(o["articleBody"])[:limit]
    m = (re.search(r'(?is)<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]+)"', raw)
         or re.search(r'(?is)<meta[^>]+content="([^"]+)"[^>]+(?:property="og:description"|name="description")', raw))
    desc = _clean(m.group(1)) if m else ""
    body = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|noscript)[^>]*>.*?</\1>", " ", raw)
    paras = [_clean(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)]
    paras = [p for p in paras if len(p) > 70 and not _JUNK.search(p) and p != desc]
    if sum(map(len, paras)) > 250:
        return ((desc + " ") if len(desc) > 80 else "") + " ".join(paras)[:limit]
    return desc[:limit] if len(desc) > 80 else ""


_GLINK = re.compile(r'href="(https?://(?!news\.google\.com|www\.google\.com|accounts\.google)[^"]+)"')


def _google_batchexecute(page: str, timeout: int) -> str:
    """Google News article pages no longer carry the publisher link; the page's id/timestamp/signature are exchanged for it
    at Google's public batchexecute endpoint (what the page's own script does). Free, no key."""
    import urllib.parse
    g = {k: re.search(rf'data-n-a-{k}="([^"]+)"', page) for k in ("id", "ts", "sg")}
    if not all(g.values()):
        return ""
    inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1],
                            1, 1, None, 0, 0, None, 0], g["id"].group(1), int(g["ts"].group(1)), g["sg"].group(1)]
    body = urllib.parse.urlencode({"f.req": json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]])}).encode()
    req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
                                 headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        t = r.read().decode("utf-8", "replace")
    arr = json.loads(t.split(chr(10) * 2, 1)[1])
    url = json.loads(arr[0][2])[1]
    return url if isinstance(url, str) and url.startswith("http") else ""


def resolve_google_link(url: str, timeout: int = 8) -> str:
    """Google News RSS link -> publisher URL (free, no API)."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            if "news.google.com" not in final:
                return final
            page = r.read(800_000).decode("utf-8", "replace")
        try:
            u = _google_batchexecute(page, timeout)
            if u:
                return u
        except Exception:
            pass
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


def fetch_text(url: str, limit: int = 2000, timeout: int = 8) -> str:
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
