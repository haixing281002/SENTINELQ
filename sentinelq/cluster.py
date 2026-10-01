"""A3 - event clustering: articles -> events, before labelling.

Stage 1 (lexical pre-cluster, per stock): normalise the headline (lower-case, strip punctuation and the company's own aliases, drop
stop-words), union-find over pairs that are <= 2 days apart AND (token-Jaccard >= 0.5 OR share >= 3 distinctive tokens, distinctive =
not in the top-200 corpus-frequency list). Representative = highest source tier, then earliest publish time (the first report), then
longest snippet.
Stage 2: label the representative only (one model call per cluster).
Stage 3 (post-label merge): two clusters merge when they carry the same event_type, the same governance flag and event dates within
3 days - this is what removes 'notice on day 1 / probe on day 2' by construction.
Confidence (deterministic): conf = min(1, 0.40 + 0.15 ln(1 + n_sources)) * tier_weight[max_tier]."""
from __future__ import annotations
import hashlib
import math
import re
from collections import Counter
from datetime import date

from .config import (CLUSTER_DAYS, CLUSTER_JACCARD, CLUSTER_SHARED_DISTINCTIVE, CONF_BASE, CONF_SLOPE, MERGE_DAYS, TOP_FREQ_TOKENS)
from .models import LabelledItem, RawItem, parse_date
from .tiers import TIER_ORDER, best_tier, domain_of, tier_of, tier_weight

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "from", "as", "is", "are", "was", "were", "be",
         "its", "it", "this", "that", "has", "have", "had", "will", "says", "said", "say", "new", "over", "after", "amid", "into", "up",
         "down", "vs", "via", "per", "than", "about", "share", "shares", "stock", "stocks", "ltd", "limited", "india", "indian", "co",
         "company", "news", "today", "live", "update", "updates", "here", "what", "why", "how", "know", "key", "top", "latest", "report",
         "reports", "crore", "cr", "rs", "lakh", "pc", "yoy", "qoq", "fy", "q1", "q2", "q3", "q4", "ltd", "inc", "plc", "nse", "bse"}


def _norm_tokens(title: str, alias_tokens: set[str]) -> set[str]:
    t = re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower())
    toks = set()
    for w in t.split():
        if len(w) < 3 or w in _STOP or w in alias_tokens or w.isdigit() and len(w) < 4:
            continue
        toks.add(w)
    return toks


def alias_tokens(name: str, aliases: str, symbol: str) -> set[str]:
    out = set()
    for s in [name, symbol] + (aliases or "").split("|"):
        out.update(re.findall(r"[a-z0-9]{2,}", s.lower()))
    return out


def top_frequency(titles: list[str], alias_toks: set[str], n: int = TOP_FREQ_TOKENS) -> set[str]:
    c = Counter()
    for t in titles:
        c.update(_norm_tokens(t, alias_toks))
    return {w for w, _ in c.most_common(n)}


def _days_apart(a: str, b: str) -> int | None:
    da, db = parse_date(a), parse_date(b)
    return None if da is None or db is None else abs((da - db).days)


def precluster(items: list[RawItem], alias_toks: set[str], common: set[str], days: int = CLUSTER_DAYS,
               jaccard: float = CLUSTER_JACCARD, shared_min: int = CLUSTER_SHARED_DISTINCTIVE) -> list[list[RawItem]]:
    """Union-find over all pairs; returns clusters (lists of RawItem), deterministic order (by earliest date, then title)."""
    n = len(items)
    toks = [_norm_tokens(i.title, alias_toks) for i in items]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(n):
        for b in range(a + 1, n):
            gap = _days_apart(items[a].published, items[b].published)
            if gap is None or gap > days:
                continue
            ta, tb = toks[a], toks[b]
            if not ta or not tb:
                continue
            inter = ta & tb
            jac = len(inter) / len(ta | tb)
            distinct = {w for w in inter if w not in common}
            if jac >= jaccard or len(distinct) >= shared_min:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[RawItem]] = {}
    for k in range(n):
        groups.setdefault(find(k), []).append(items[k])
    out = list(groups.values())
    out.sort(key=lambda g: (min(i.published for i in g), min(i.title for i in g)))
    return out


def _tier(i: RawItem) -> str:
    if not i.max_tier:
        i.max_tier = tier_of(domain_of(i.url, i.source))
    return i.max_tier


def representative(members: list[RawItem]) -> RawItem:
    """Highest tier; ties -> earliest publish (first report); then longest snippet; then title for determinism."""
    return sorted(members, key=lambda i: (TIER_ORDER.get(_tier(i), 3), i.published, -len(i.snippet or ""), i.title))[0]


def url_hash(url: str) -> str:
    return hashlib.sha256((url or "").strip().lower().rstrip("/").encode()).hexdigest()[:16]


def confidence(n_sources: int, max_tier: str) -> float:
    return round(min(1.0, CONF_BASE + CONF_SLOPE * math.log(1 + max(n_sources, 0))) * tier_weight(max_tier), 3)


def build_event(members: list[RawItem], pass_: str) -> RawItem:
    """Collapse a cluster into its representative, carrying the event fields (n_sources, n_members, max_tier, confidence, members)."""
    rep = representative(members)
    rows, tiers_seen, domains = [], [], set()
    for m in members:
        d = domain_of(m.url, m.source)
        t = tier_of(d)
        tiers_seen.append(t)
        domains.add(d)
        if m is not rep:
            rows.append({"url": m.url, "source": d, "date": m.published, "headline": m.title, "tier": t})
        for extra in m.members:                        # syndicated copies / per-day overflow already attached to a member
            dd = domain_of(extra.get("url", ""), extra.get("source", ""))
            domains.add(dd)
            tiers_seen.append(tier_of(dd))
            rows.append({**extra, "source": dd, "tier": tier_of(dd)})
    rep.members = rows
    rep.n_members = 1 + len(rows)
    rep.n_sources = max(1, len({d for d in domains if d}))
    rep.max_tier = best_tier(tiers_seen)
    rep.confidence = confidence(rep.n_sources, rep.max_tier)
    rep.member_url_hashes = sorted({url_hash(m.url) for m in members} | {url_hash(r.get("url", "")) for r in rows})
    rep.pass_ = pass_
    rep.event_date = min(m.published for m in members if m.published) if any(m.published for m in members) else rep.published
    rep.event_id = hashlib.sha1(f"{rep.symbol}|{rep.event_date}|{','.join(rep.member_url_hashes)}".encode()).hexdigest()[:12]
    if rep.window is None:
        rep.window = next((m.window for m in members if m.window), None)
    return rep


def attach_extras(events: list[RawItem], extras: list[RawItem], alias_toks: set[str]) -> list[RawItem]:
    """Per-day overflow (beyond the cap of 6) joins the same-day cluster with the best token overlap as an extra source;
    an extra with no same-day cluster is returned so it can be disclosed as not used."""
    unplaced = []
    for x in extras:
        tx = _norm_tokens(x.title, alias_toks)
        best, score = None, 0.0
        for e in events:
            if e.published != x.published and (_days_apart(e.published, x.published) or 99) > CLUSTER_DAYS:
                continue
            te = _norm_tokens(e.title, alias_toks)
            s = len(tx & te) / len(tx | te) if tx and te else 0.0
            if s > score:
                best, score = e, s
        if best is not None and score >= 0.3:
            d = domain_of(x.url, x.source)
            best.members.append({"url": x.url, "source": d, "date": x.published, "headline": x.title, "tier": tier_of(d)})
            best.n_members += 1
            doms = {m["source"] for m in best.members} | {domain_of(best.url, best.source)}
            best.n_sources = max(1, len({d for d in doms if d}))
            best.max_tier = best_tier([best.max_tier] + [m["tier"] for m in best.members])
            best.confidence = confidence(best.n_sources, best.max_tier)
            best.member_url_hashes = sorted(set(best.member_url_hashes) | {url_hash(x.url)})
        else:
            unplaced.append(x)
    return unplaced


def post_label_merge(labelled: list[LabelledItem], days: int = MERGE_DAYS) -> tuple[list[LabelledItem], list[tuple[LabelledItem, LabelledItem]]]:
    """Stage 3: same event_type + same governance flag + event dates within `days` -> ONE event (sources pooled, confidence recomputed).
    The survivor is the higher-tier (then earlier) representative. Returns (events, merged_pairs) for the audit."""
    out: list[LabelledItem] = []
    merged = []
    for li in sorted(labelled, key=lambda x: (x.item.published, x.item.title)):
        if li.item.kind != "news" or li.label is None:
            out.append(li)
            continue
        target = None
        for o in out:
            if o.item.kind != "news" or o.label is None or o.item.symbol != li.item.symbol:
                continue
            if o.label.event_type != li.label.event_type or o.label.governance_flag != li.label.governance_flag:
                continue
            gap = _days_apart(o.item.event_date or o.item.published, li.item.event_date or li.item.published)
            if gap is not None and gap <= days:
                target = o
                break
        if target is None:
            out.append(li)
            continue
        keep, drop = (target, li) if (TIER_ORDER.get(target.item.max_tier, 3), target.item.published) <= (TIER_ORDER.get(li.item.max_tier, 3), li.item.published) else (li, target)
        d = domain_of(drop.item.url, drop.item.source)
        keep.item.members = keep.item.members + [{"url": drop.item.url, "source": d, "date": drop.item.published, "headline": drop.item.title,
                                                  "tier": tier_of(d), "merged_event": drop.item.event_id}] + drop.item.members
        keep.item.n_members = 1 + len(keep.item.members)
        doms = {m["source"] for m in keep.item.members} | {domain_of(keep.item.url, keep.item.source)}
        keep.item.n_sources = max(1, len({x for x in doms if x}))
        keep.item.max_tier = best_tier([keep.item.max_tier, drop.item.max_tier] + [m.get("tier", "T3") for m in keep.item.members])
        keep.item.confidence = confidence(keep.item.n_sources, keep.item.max_tier)
        keep.item.member_url_hashes = sorted(set(keep.item.member_url_hashes) | set(drop.item.member_url_hashes))
        keep.item.event_date = min(x for x in (keep.item.event_date, drop.item.event_date, keep.item.published, drop.item.published) if x)
        if drop.item.purpose == "sentiment":
            keep.item.purpose = "sentiment"
        if keep is li:                                   # the newcomer survived: swap it into the list
            out[out.index(target)] = li
        merged.append((keep, drop))
    return out, merged
