"""Article selection. Accuracy rules: (1) rank by how much the article is ABOUT the company (name in the title, then
repeated mentions), (2) when capping to N articles, spread the picks across the months of the lookback so a 12-month
view is not secretly the latest few weeks."""
from __future__ import annotations
import re
from collections import defaultdict
from datetime import date

from ..models import Holding

_GENERIC = {"india", "indian", "ltd", "limited", "company", "co", "corp", "corporation", "industries", "industry", "the", "and",
            "of", "bank", "laboratories", "pharma", "pharmaceuticals", "motors", "finance", "financial", "technologies", "engineering",
            "asset", "management", "services", "small", "forgings", "precision", "automation", "life", "power"}


def aliases_of(h: Holding) -> list[str]:
    """Names the company is known by in news/GDELT. Explicit aliases (universe.csv, pipe-separated) win; otherwise the
    cleaned name plus Ltd/Limited variants."""
    if getattr(h, "aliases", ""):
        return [a.strip() for a in h.aliases.split("|") if a.strip()]
    return [h.name, f"{h.name} Ltd", f"{h.name} Limited"]


def name_tokens(h: Holding) -> list[str]:
    """Lower-case strings whose presence in a headline ties it to the company."""
    out = {re.sub(r"[^a-z0-9]", "", h.symbol.lower())}
    for a in aliases_of(h):
        out.add(a.lower())
    words = [w for w in re.findall(r"[a-z0-9&']+", h.name.lower()) if len(w) >= 2]
    distinct = [w for w in words if w not in _GENERIC] or words
    if not getattr(h, "aliases", ""):
        out.update(distinct)
    return sorted(t for t in out if t)


def title_hit(title: str, tokens: list[str]) -> bool:
    t = title.lower()
    return any(tok in t for tok in tokens)


def is_usable(item, tokens: list[str]) -> bool:
    """An article counts toward the 50 only if its TITLE names the company (precise, and needs no model call)."""
    return item.kind != "news" or (bool(item.title) and title_hit(item.title, tokens))


def count_usable(items: list, tokens: list[str]) -> int:
    seen, n = set(), 0
    for i in items:
        if i.kind == "news" and is_usable(i, tokens):
            k = re.sub(r"[^a-z0-9]", "", i.title.lower())
            if k not in seen:
                seen.add(k)
                n += 1
    return n


def select_articles(items: list, cap: int | None, tokens: list[str]) -> list:
    """The LATEST `cap` articles whose title names the company (syndicated duplicates removed, newest first).
    Other articles only fill the list if there are fewer than `cap` such titles. Actions are always kept."""
    news = [i for i in items if i.kind == "news"]
    rest = [i for i in items if i.kind != "news"]
    order = lambda i: (i.published, i.relevance)
    seen, uniq = set(), []
    for i in sorted(news, key=order, reverse=True):
        k = re.sub(r"[^a-z0-9]", "", (i.title or "").lower()) or i.url
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    if cap:
        good = [i for i in uniq if is_usable(i, tokens)]
        bad = [i for i in uniq if not is_usable(i, tokens)]
        uniq = (good + bad)[:cap]
    return sorted(uniq, key=order, reverse=True) + rest


def month_spark(items: list, start, end, ascii_only: bool = False) -> tuple[str, str]:
    """Per-month article counts across the lookback as a tiny bar string, plus the range label."""
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    cnt = {k: 0 for k in months}
    for i in items:
        if i.kind == "news" and i.published[:7] in cnt:
            cnt[i.published[:7]] += 1
    top = max(cnt.values()) or 1
    chars = "._-=+*#@" if ascii_only else "▁▂▃▄▅▆▇█"
    bar = "".join(" " if cnt[k] == 0 else chars[min(len(chars) - 1, int(cnt[k] / top * (len(chars) - 1)))] for k in months)
    dates = sorted(i.published for i in items if i.kind == "news" and i.published)
    span = f"latest {sum(cnt.values())} span {dates[0]}..{dates[-1]}" if dates else "no articles"
    if dates:
        span += f" ({(date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 1} days rolled back)"
    return bar, f"{months[0]}..{months[-1]}  {span}"



GOV_TITLE = re.compile(
    r"resign|quits?|steps? down|stepped down|terminat|appoint|elevat|promot|\bcfo\b|\bceo\b|\bmd\b|chief|company secretary|"
    r"compliance|sebi|\brbi\b|penalt|\bfine[ds]?\b|show.cause|settle|probe|investigat|raid|\bcbi\b|\bed\b|nclt|auditor|"
    r"related.party|independent director|\bboard\b|pledge|whistle|fraud|default|forensic|exchange|order|ban(ned)?\b|suspend", re.I)

GOV_QUERY_LEADERSHIP = ('(resigns OR resignation OR "steps down" OR quits OR appointed OR appoints OR "chief financial officer" OR CFO OR '
                        '"company secretary" OR "compliance officer" OR "chief executive" OR CEO OR "managing director" OR "chief product officer")')
GOV_QUERY_REGULATORY = ('(SEBI OR RBI OR penalty OR fined OR fine OR "show cause" OR settlement OR probe OR investigation OR raid OR CBI OR '
                        'NCLT OR auditor OR "related party" OR "independent director" OR pledge OR whistleblower OR fraud OR default)')


def select_governance(items: list, exclude: list, cap: int, tokens: list[str]) -> list:
    """Governance-candidate articles for the 12-month rubric: title carries a governance keyword, not already in the
    sentiment set, newest first, capped. They feed governance scoring only - never sentiment."""
    seen = {(i.url or "").strip().lower().rstrip("/") for i in exclude}
    seen_t = {re.sub(r"[^a-z0-9]", "", (i.title or "").lower()) for i in exclude}
    out = []
    for i in sorted((x for x in items if x.kind == "news"), key=lambda x: (x.published, title_hit(x.title, tokens)), reverse=True):
        k, kt = (i.url or "").strip().lower().rstrip("/"), re.sub(r"[^a-z0-9]", "", (i.title or "").lower())
        if k in seen or kt in seen_t or not GOV_TITLE.search(i.title or ""):
            continue
        seen.add(k)
        seen_t.add(kt)
        i.purpose = "governance"
        out.append(i)
        if len(out) >= cap:
            break
    return out


FUND_TITLE = re.compile(r"result|net profit|\bpat\b|profit (rises|jumps|falls|drops|surges|declines|up|down)|revenue|earnings|\bq[1-4]\b|quarter|"
                        r"guidance|outlook|order (win|book|inflow)|orders?\b|margin|ebitda|dividend|capex|market share|volume|sales", re.I)
FUND_QUERY = ('(results OR "net profit" OR PAT OR revenue OR earnings OR "Q1" OR "Q2" OR "Q3" OR "Q4" OR guidance OR "order win" OR '
              'orders OR dividend OR capex OR "market share" OR EBITDA)')


def select_fundamentals(items: list, exclude: list, per_window: int, cap: int, tokens: list[str], as_of, window_days: int = 90) -> list:
    """Results-type articles spread over the WHOLE lookback (<= per_window per 90-day block, <= cap in all), whose title names the company.
    The latest-N pull can collapse onto the last few weeks and miss the full-year results prints that anchor +2 / -1 (Reconciliation cause 1)."""
    seen = {(i.url or "").strip().lower().rstrip("/") for i in exclude}
    seen_t = {re.sub(r"[^a-z0-9]", "", (i.title or "").lower()) for i in exclude}
    buckets: dict[int, list] = {}
    for i in sorted((x for x in items if x.kind == "news"), key=lambda x: x.published, reverse=True):
        k, kt = (i.url or "").strip().lower().rstrip("/"), re.sub(r"[^a-z0-9]", "", (i.title or "").lower())
        if k in seen or kt in seen_t or not title_hit(i.title or "", tokens) or not FUND_TITLE.search(i.title or ""):
            continue
        try:
            b = (as_of - date.fromisoformat(i.published)).days // window_days
        except ValueError:
            continue
        if len(buckets.setdefault(b, [])) < per_window:
            buckets[b].append(i)
            seen.add(k)
            seen_t.add(kt)
    out = [i for b in sorted(buckets) for i in buckets[b]][:cap]
    for i in out:
        i.purpose, i.origin = "sentiment", "fundamentals"
    return out
