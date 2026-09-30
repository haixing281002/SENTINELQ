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
    """Cheap, deterministic screen used both to decide when enough articles are in hand and to rank: a market
    roundup that never names the company is not usable evidence (the same rule the prefilter applies later)."""
    from ..style import classify_style
    return item.kind != "news" or bool(item.title) and (title_hit(item.title, tokens) or classify_style(item) != "market-roundup")


def count_usable(items: list, tokens: list[str]) -> int:
    return sum(1 for i in items if i.kind == "news" and is_usable(i, tokens))


def select_articles(items: list, cap: int | None, tokens: list[str]) -> list:
    """The LATEST `cap` usable news articles (newest first; on the same day, company-in-title first). Unusable items
    only fill the list if there are fewer than `cap` usable ones. Actions are always kept."""
    news = [i for i in items if i.kind == "news"]
    rest = [i for i in items if i.kind != "news"]
    order = lambda i: (i.published, title_hit(i.title, tokens), i.relevance)
    if cap:
        good = sorted((i for i in news if is_usable(i, tokens)), key=order, reverse=True)
        bad = sorted((i for i in news if not is_usable(i, tokens)), key=order, reverse=True)
        news = (good + bad)[:cap]
    return sorted(news, key=order, reverse=True) + rest


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


def dedupe_key(url: str) -> str:
    return (url or "").strip().lower().rstrip("/")
