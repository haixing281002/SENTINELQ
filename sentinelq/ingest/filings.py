"""BSE corporate announcements as a second, FREE evidence source (the filings the Reconciliation and the v2.1 doc both asked for).

Every governance event an Indian listed company has - a CFO exit, a SEBI order, a penalty, a related-party approval, a results print -
is first a filing on bseindia.com. The exchange publishes them through the JSON endpoint its own website uses; no key, no cost.
We read the announcement list only (subject, headline, category, date, attachment link): headline + snippet, exactly like a news item,
tiered T1 (bseindia.com). Nothing is stored beyond the corpus of record's headline + snippet rows.

  python -m sentinelq inspect filings --stock TITAN          # see what the exchange returns for one company

Scrip codes: `bse_code` in portfolio/universe.csv (the number in the bseindia.com URL for the company). Without a code the provider
asks the exchange's search endpoint; whatever the exchange returns is checked against the company's name before anything is used, so a
wrong code can never feed another company's filings into a score."""
from __future__ import annotations
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from ..models import Holding, RawItem

ANN = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
SEARCH = "https://api.bseindia.com/Msource/1D/getQouteSearch.aspx"
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
PAGE = "https://www.bseindia.com/corporates/ann.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) sentinelq-filings/1.0", "Referer": "https://www.bseindia.com/",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-IN,en;q=0.9"}

# Which pass a filing feeds. Everything else (press releases, investor presentations, AGM notices) is a sentiment item outside the budget.
RESULTS_CATS = re.compile(r"\bresults?\b|financial (results|statements)|unaudited|audited financial", re.I)   # not 'Chief Financial Officer'
GOV_CATS = re.compile(r"change in director|kmp|resign|appoint|cessation|auditor|penalt|fine|show cause|sebi|regulat|related party|"
                      r"investigation|litigation|insider|pledge|encumbr|disclosure of .*(penal|default)|non-compliance|order", re.I)
GOV_WORDS = re.compile(r"resign|cessation|step(s|ped)? down|penalt|fine[ds]?\b|show cause|sebi|rbi\b|order|settle|probe|investigat|raid|"
                       r"auditor|related party|pledge|encumbr|default|fraud|litigation|arbitration|notice", re.I)
_PR_CATS = re.compile(r"press release|media release|update|presentation|credit rating|investor|agm|egm|postal ballot|record date|dividend|"
                      r"buyback|bonus|split|merger|amalgamation|acquisition|capex|expansion|order|contract|award", re.I)


class BseAnnouncements:
    def __init__(self, pause: float = 1.0, timeout: int = 20, retries: int = 2, backoff: float = 20.0, sleep=time.sleep, opener=None,
                 page_days: int = 60, max_pages: int = 6):
        self.pause, self.timeout, self.retries, self.backoff, self.sleep = pause, timeout, retries, backoff, sleep
        self.opener = opener or urllib.request.urlopen
        self.page_days, self.max_pages = page_days, max_pages
        self.audit: list[dict] = []
        self.on_event = None
        self._last = 0.0
        self._codes: dict[str, str] = {}

    def _say(self, m):
        if self.on_event:
            self.on_event(m)

    def _get(self, url: str, label: str) -> bytes:
        last = ""
        for attempt in range(self.retries + 1):
            wait = self.pause - (time.time() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.time()
            try:
                with self.opener(urllib.request.Request(url, headers=HEADERS), timeout=self.timeout) as r:
                    body = r.read()
                self.audit.append({"label": label, "url": url, "status": "ok", "attempts": attempt + 1, "bytes": len(body)})
                return body
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            if attempt < self.retries:
                self._say(f"[BSE] {label}: {last} - waiting {self.backoff:.0f}s")
                self.sleep(self.backoff)
        self.audit.append({"label": label, "url": url, "status": "failed", "error": last})
        raise RuntimeError(f"BSE gave no answer for {label} ({last})")

    # ---- scrip code
    def scrip_code(self, h: Holding) -> str | None:
        code = (getattr(h, "bse_code", "") or "").strip()
        if code:
            return code
        if h.symbol in self._codes:
            return self._codes[h.symbol]
        try:
            q = urllib.parse.urlencode({"Type": "EQ", "text": h.name, "flag": "site"})
            body = self._get(f"{SEARCH}?{q}", f"{h.symbol} scrip search").decode("utf-8", "replace")
        except Exception as e:
            self._say(f"[BSE] {h.symbol}: scrip search failed ({e}); add bse_code to universe.csv")
            return None
        # the search returns <li ... > rows like "Titan Company Ltd   500114   TITAN"; take the first row whose text names the company
        toks = _name_tokens(h)
        for m in re.finditer(r"<li[^>]*>(.*?)</li>", body, re.S | re.I):
            row = re.sub(r"<[^>]+>", " ", m.group(1))
            if any(t in row.lower() for t in toks):
                mm = re.search(r"\b(\d{6})\b", row)
                if mm:
                    self._codes[h.symbol] = mm.group(1)
                    return mm.group(1)
        self._say(f"[BSE] {h.symbol}: no scrip code found for '{h.name}'; add bse_code to universe.csv")
        return None

    # ---- announcements
    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        code = self.scrip_code(h)
        if not code:
            return []
        toks = _name_tokens(h)
        items, hi, pages = [], end, 0
        while hi >= start and pages < self.max_pages:
            lo = max(start, hi - timedelta(days=self.page_days - 1))
            q = urllib.parse.urlencode({"pageno": 1, "strCat": -1, "strPrevDate": lo.strftime("%Y%m%d"), "strScrip": code, "strSearch": "P",
                                        "strToDate": hi.strftime("%Y%m%d"), "strType": "C", "subcategory": -1})
            try:
                data = json.loads(self._get(f"{ANN}?{q}", f"{h.symbol} filings {lo}..{hi}").decode("utf-8", "replace"))
            except Exception as e:
                self._say(f"[BSE] {h.symbol} {lo}..{hi}: {e}")
                break
            rows = data.get("Table") or []
            for r in rows:
                it = _row_to_item(h, r, toks)
                if it:
                    items.append(it)
            pages += 1
            hi = lo - timedelta(days=1)
        self._say(f"[BSE] {h.symbol}: {len(items)} filing(s) over {start}..{end} (scrip {code})")
        return _dedupe(items)


def _name_tokens(h: Holding) -> list[str]:
    from .select import name_tokens
    return [t for t in name_tokens(h) if len(t) >= 4]


def _row_to_item(h: Holding, r: dict, toks: list[str]) -> RawItem | None:
    company = (r.get("SLONGNAME") or r.get("SCRIP_NAME") or "").strip()
    if company and not any(t in company.lower() for t in toks):
        return None                                           # the exchange's own company name must match: a wrong code feeds nothing
    subject = (r.get("NEWSSUB") or r.get("NEWS_SUBJECT") or "").strip()
    head = (r.get("HEADLINE") or "").strip()
    cat = (r.get("CATEGORYNAME") or "").strip()
    raw_dt = (r.get("NEWS_DT") or r.get("DT_TM") or "")[:10]
    try:
        d = date.fromisoformat(raw_dt.replace("/", "-")) if "-" in raw_dt or "/" in raw_dt else None
    except ValueError:
        d = None
    if d is None or not (subject or head):
        return None
    att = (r.get("ATTACHMENTNAME") or "").strip()
    url = ATTACH + att if att else (r.get("NSURL") or f"{PAGE}#{r.get('NEWSID', '')}")
    title = subject if subject else head[:160]
    if not any(t in title.lower() for t in toks):           # exchange subjects often omit the name ("Outcome of Board Meeting"): prefix it
        title = f"{h.name}: {title}"
    text = f"{cat}. {head}".strip(". ")
    if RESULTS_CATS.search(cat) or RESULTS_CATS.search(subject):
        pass_, purpose, origin = "results", "sentiment", "filings"
    elif GOV_CATS.search(cat) or GOV_WORDS.search(subject) or GOV_WORDS.search(head[:200]):
        pass_, purpose, origin = "governance", "governance", "filings"
    else:
        pass_, purpose, origin = "sentiment", "sentiment", "filings"
    return RawItem(h.symbol, "news", title, text[:300], url, d.isoformat(), "bseindia.com", origin=origin, purpose=purpose, pass_=pass_, style="filing")


def _dedupe(items: list[RawItem]) -> list[RawItem]:
    seen, out = set(), []
    for i in items:
        k = (i.published, re.sub(r"[^a-z0-9]", "", i.title.lower())[:80])
        if k in seen:
            continue
        seen.add(k)
        out.append(i)
    return out
