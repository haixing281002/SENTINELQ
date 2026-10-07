"""A1 - stratified sampler. Replaces 'latest 100, stop when full'.

Fixed windows back from as_of (W1 0-30 / W2 31-90 / W3 91-180 / W4 181-365) with quotas 40 / 25 / 20 / 15; usable headlines only
(the title names the company); a per-day cap of 6 at the sampling stage (the 7th and later are not dropped - they ride along as extra
sources for that day's cluster, outside the quota); carry-forward of shortfalls to the next older window, and back to the newest window
if the oldest is short. Total budget stays 100. Every stock is therefore scored on the SAME shape of evidence window."""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from datetime import date

from .config import BOILERPLATE_PATTERNS, PER_DAY_CAP, SAMPLE_WINDOWS

_BOILER = None


def is_boilerplate(title: str) -> bool:
    """Templated price / listicle headline (config.BOILERPLATE_PATTERNS): no event, no model call."""
    global _BOILER
    if _BOILER is None:
        _BOILER = re.compile("|".join(BOILERPLATE_PATTERNS), re.I)
    return bool(title) and bool(_BOILER.search(title))
from .ingest.select import is_usable
from .models import RawItem, parse_date


def window_of(published: str, as_of: date, windows=SAMPLE_WINDOWS) -> int | None:
    d = parse_date(published)
    if d is None:
        return None
    age = (as_of - d).days
    for k, (lo, hi, _q) in enumerate(windows, 1):
        if lo <= age <= hi:
            return k
    return None


def _tkey(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def stratified_sample(items: list[RawItem], as_of: date, tokens: list[str], windows=SAMPLE_WINDOWS,
                      per_day_cap: int = PER_DAY_CAP, cap: bool = True) -> tuple[list[RawItem], list[RawItem], dict]:
    """Returns (selected, extras, report). `selected` carries .window (1..4) and counts against the budget; `extras` are the
    per-day overflow (also .window set, .sample_extra = True) to be attached to that day's cluster as extra sources.
    report = {"windows": {1: n, ...}, "quota": {1: q, ...}, "carry": {...}, "usable": N, "syndicated": M, "per_day_extra": E}."""
    news = [i for i in items if i.kind == "news"]
    # identical (syndicated) headlines: keep the newest copy, remember the others as extra sources of that copy
    by_title: dict[str, list[RawItem]] = defaultdict(list)
    for i in sorted(news, key=lambda x: (x.published, x.relevance), reverse=True):
        by_title[_tkey(i.title) or i.url].append(i)
    uniq, syndicated = [], 0
    for copies in by_title.values():
        head = copies[0]
        for c in copies[1:]:
            head.members.append({"url": c.url, "source": c.source, "date": c.published, "headline": c.title})
            syndicated += 1
        uniq.append(head)
    boiler = [i for i in uniq if is_usable(i, tokens) and is_boilerplate(i.title)]
    for i in boiler:
        i.sample_extra = False
        i.boilerplate = True
    usable = [i for i in uniq if is_usable(i, tokens) and not getattr(i, "boilerplate", False)]
    for i in usable:
        i.window = window_of(i.published, as_of, windows)
    pools: dict[int, list[RawItem]] = {k: [] for k in range(1, len(windows) + 1)}
    for i in usable:
        if i.window:
            pools[i.window].append(i)
    for k in pools:
        pools[k].sort(key=lambda x: (x.published, x.relevance), reverse=True)

    selected, extras, day_count = [], [], Counter()
    picked: set[int] = set()
    taken = {k: 0 for k in pools}
    quota = {k: windows[k - 1][2] for k in pools}
    carry_log = {}

    def take_from(k: int, want: int) -> int:
        got = 0
        for i in pools[k]:
            if got >= want:
                break
            if id(i) in picked:
                continue
            dk = (i.symbol, i.published)
            if day_count[dk] >= per_day_cap:
                if not getattr(i, "sample_extra", False):
                    i.sample_extra = True
                    extras.append(i)
                    picked.add(id(i))
                continue
            day_count[dk] += 1
            picked.add(id(i))
            selected.append(i)
            taken[k] += 1
            got += 1
        return got

    carry = 0
    for k in pools:                                   # newest window first; shortfall rolls to the next older window
        want = quota[k] + carry
        got = take_from(k, want)
        carry = want - got
        carry_log[k] = {"wanted": want, "got": got}
    if carry > 0:                                     # the oldest window was short: the remainder rolls back to the newest
        for k in pools:
            if carry <= 0:
                break
            got = take_from(k, carry)
            carry -= got
            carry_log[k]["rolled_back"] = carry_log[k].get("rolled_back", 0) + got
    # anything usable but never selected (over budget): with cap=False (the default run) it is scored too - the windows then only
    # set the recency weight; with cap=True (--budget / --window-quotas) it is left out and counted so the audit can show it
    over = [i for i in usable if id(i) not in picked]
    if not cap and over:
        selected += over
        for i in over:
            picked.add(id(i))
        over = []
    report = {"windows": {k: taken[k] for k in pools}, "quota": quota, "carry": carry_log, "usable": len(usable),
              "unique_titles": len(uniq), "syndicated": syndicated, "per_day_extra": len(extras), "over_budget": len(over),
              "budget": sum(quota.values()) if cap else len(usable), "capped": cap, "selected": len(selected), "boilerplate": len(boiler)}
    return sorted(selected, key=lambda x: (x.published, x.relevance), reverse=True), extras, report


def window_counts(items: list, n_windows: int = 4) -> dict[int, int]:
    c = Counter(getattr(i, "window", None) for i in items)
    return {k: c.get(k, 0) for k in range(1, n_windows + 1)}


def scaled_windows(budget: int | None = None, quotas: list[int] | None = None, base=None) -> list[tuple[int, int, int]]:
    """The four windows with quotas scaled to `budget` (shape kept) or set explicitly; default is the config (40/25/20/15 = 100)."""
    base = list(base or SAMPLE_WINDOWS)
    if quotas:
        if len(quotas) != len(base):
            raise SystemExit(f"--window-quotas needs {len(base)} numbers (one per window W1..W4)")
        return [(lo, hi, int(q)) for (lo, hi, _), q in zip(base, quotas)]
    if budget and budget != sum(q for _, _, q in base):
        tot = sum(q for _, _, q in base)
        out = [(lo, hi, max(1, round(q * budget / tot))) for lo, hi, q in base]
        diff = budget - sum(q for _, _, q in out)                      # rounding: settle the difference on W1
        lo, hi, q = out[0]
        out[0] = (lo, hi, q + diff)
        return out
    return base
