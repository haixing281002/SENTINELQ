"""Stage 5 - deterministic scoring. No AI, no prices. Pure arithmetic on validated evidence."""
from __future__ import annotations
import math
import re
import statistics
from collections import defaultdict
from datetime import date

from .models import Holding, LabelledItem, StockScore, parse_date
from .rubric import Rubric


def recency_weight(published: str, as_of: date, r: Rubric) -> float:
    d = parse_date(published)
    age_cal = max((as_of - d).days, 0)
    age_td = age_cal * r["sentiment"]["trading_days_per_year"] / 365.0
    return 0.5 ** (age_td / r["sentiment"]["half_life_trading_days"])


def to_integer(x: float | None) -> int | None:
    """Displayed score: nearest integer on the -2..+2 scale, halves away from zero."""
    if x is None:
        return None
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def weighted_sentiment(items: list[LabelledItem], as_of: date, r: Rubric) -> float | None:
    """Recency-weighted mean of ordinal sentiments (news only)."""
    num = den = 0.0
    for li in items:
        if li.item.kind != "news" or li.item.purpose != "sentiment":
            continue
        w = recency_weight(li.item.published, as_of, r)
        num += w * li.label.sentiment
        den += w
    return num / den if den else None


def _penalty(pen, lab):
    p = pen.get(lab.event_type)
    if isinstance(p, dict):
        p = p.get(lab.materiality or "medium", p["medium"])
    return p


_EXIT_WORDS = re.compile(r"resign|quit|steps? down|stepped down|exit|terminat|sacked|removed|ousted|abrupt|dismiss|relieved|fired", re.I)
_BENIGN_PEOPLE = re.compile(r"elevat|promot|re-?designat|appointed as|appoints|appointment of|named (as )?(the )?head|takes? charge|"
                            r"assum(es|ed) charge|superannuat|retire|retirement|succession|completion of (his|her|the) (term|tenure)|"
                            r"transition|move[sd]? to|to head|new role|additional charge", re.I)
_C_SUITE = re.compile(r"\b(ceo|cfo|cto|coo|cio|cmo|cpo|md|chief [a-z&/ ,-]{2,40} officer|chief executive|managing director|"
                      r"company secretary|compliance officer)\b", re.I)


def literal_rule_override(li: LabelledItem) -> str | None:
    """Safety net behind the model: a 'management_exit' label is only a penalty if the item really is an UNPLANNED exit of
    a CXO / CS / CFO / compliance officer. Returns the reason to NOT penalise, else None."""
    lab = li.label
    text = f"{li.item.title}. {lab.rationale}"
    if lab.event_type != "management_exit":
        return None
    title = li.item.title
    if _BENIGN_PEOPLE.search(title) and not _EXIT_WORDS.search(title):
        return "elevation / appointment / planned change, not an exit"
    if re.search(r"superannuat|retire|completion of (his|her|the) (term|tenure)", text, re.I) and not re.search(r"resign|terminat|abrupt|sudden", title, re.I):
        return "planned retirement / superannuation, not an unplanned exit"
    if not _C_SUITE.search(text):
        return "below CXO / CS / CFO / compliance tier"
    return None


def governance(items: list[LabelledItem], r: Rubric) -> tuple[float, str, list[dict]]:
    """Start at 100; fixed penalties per event type (once per type). Background mentions of
    out-of-window events earn one flat memory discount, never double-counted with an in-window
    penalty of the same type."""
    g = r["governance"]
    pen = g["penalties"]
    score, applied, seen = float(g["start"]), [], set()
    historical = []
    ignored = governance.ignored = []      # (read by score_portfolio) items flagged but deliberately not penalised
    for li in sorted(items, key=lambda x: x.item.published):
        lab = li.label
        if not lab.governance_flag or _penalty(pen, lab) is None:
            if lab.governance_flag and lab.event_type in g.get("routine_types", {}):
                ignored.append({"event_type": lab.event_type, "date": li.item.published, "headline": li.item.title,
                                "url": li.item.url, "why": g["routine_types"][lab.event_type]})
            continue
        why_not = literal_rule_override(li)
        if why_not:
            ignored.append({"event_type": lab.event_type, "date": li.item.published, "headline": li.item.title,
                            "url": li.item.url, "why": why_not})
            continue
        if lab.historical:
            historical.append(li)
            continue
        if g["count_mode"] == "once_per_type" and lab.event_type in seen:
            continue
        seen.add(lab.event_type)
        p = _penalty(pen, lab)
        score += p
        applied.append({"event_type": lab.event_type, "penalty": p, "date": li.item.published,
                        "headline": li.item.title, "url": li.item.url})
    memory = [li for li in historical if li.label.event_type not in seen]
    if memory:
        li = memory[-1]
        score += g["historical_penalty"]
        applied.append({"event_type": "historical (" + li.label.event_type + ")",
                        "penalty": g["historical_penalty"], "date": li.item.published,
                        "headline": li.item.title, "url": li.item.url})
    label = next(x["label"] for x in g["labels"] if score >= x["min"])
    return score, label, applied


def corporate_actions(items: list[LabelledItem], r: Rubric) -> tuple[float, list[dict]]:
    ca = r["corporate_actions"]
    total, detail = 0.0, []
    for li in items:
        lab = li.label
        if lab.event_type not in ca["base"]:
            continue
        base = ca["base"][lab.event_type]
        if base == "by_sentiment_sign":
            base = float((lab.sentiment > 0) - (lab.sentiment < 0))
        mat = lab.materiality or "medium"
        contrib = base * ca["materiality_multiplier"][mat]
        total += contrib
        detail.append({"event_type": lab.event_type, "materiality": mat, "contribution": contrib,
                       "date": li.item.published, "headline": li.item.title, "url": li.item.url})
    lo, hi = ca["clip"]
    return max(lo, min(hi, total)), detail


def score_portfolio(holdings: list[Holding], by_symbol: dict[str, list[LabelledItem]],
                    dropped_counts: dict[str, int], as_of: date, r: Rubric) -> list[StockScore]:
    # Sector sentiment: computed once per sector over the pooled (URL-deduped) constituents' news.
    sector_items: dict[str, list[LabelledItem]] = defaultdict(list)
    seen_urls: dict[str, set] = defaultdict(set)
    for h in holdings:
        for li in by_symbol.get(h.symbol, []):
            u = li.item.url.strip().lower()
            if li.item.kind == "news" and u not in seen_urls[h.sector]:
                seen_urls[h.sector].add(u)
                sector_items[h.sector].append(li)
    sector_sent = {s: weighted_sentiment(v, as_of, r) for s, v in sector_items.items()}

    out = []
    for h in holdings:
        items = by_symbol.get(h.symbol, [])
        cs = weighted_sentiment(items, as_of, r)
        g_score, g_label, pens = governance(items, r)
        g_ignored = list(governance.ignored)
        ca_score, ca_detail = corporate_actions(items, r)
        n_news = sum(1 for i in items if i.item.kind == "news" and i.item.purpose == "sentiment")
        low = n_news < r["confidence"]["low_below_items"]
        pen_txt = ", ".join(f"{p['event_type']} {p['penalty']:+g}" for p in pens) or "none"
        rat = (f"Governance = {r['governance']['start']} with penalties [{pen_txt}] = {g_score:g} ({g_label}). "
               f"Sentiment from {n_news} news item(s), half-life {r['sentiment']['half_life_trading_days']} trading days."
               + (" LOW CONFIDENCE: sparse coverage." if low else ""))
        out.append(StockScore(h.symbol, h.name, h.sector, to_integer(cs),
                              None if cs is None else round(cs, 3),
                              None if sector_sent.get(h.sector) is None else round(sector_sent[h.sector], 2),
                              g_score, g_label, pens, round(ca_score, 2), ca_detail,
                              len(items), dropped_counts.get(h.symbol, 0), low, rat, h.cap, h.weight, g_ignored))
    return out


def relative_mode(scores: list[StockScore], r: Rubric) -> list[dict]:
    """Portfolio-relative: winsorize at +/-k sigma then cross-sectionally z-score."""
    k = r["portfolio_relative"]["winsor_sigma"]
    cols = {"company_sentiment": lambda s: s.company_sentiment_raw,
            "sector_sentiment": lambda s: s.sector_sentiment,
            "governance_score": lambda s: s.governance_score,
            "corporate_action_score": lambda s: s.corporate_action_score}
    rows = [{"symbol": s.symbol} for s in scores]
    for name, get in cols.items():
        vals = [get(s) for s in scores]
        present = [v for v in vals if v is not None]
        if len(present) < 2:
            for row in rows:
                row[name + "_z"] = None
            continue
        mu, sd = statistics.fmean(present), statistics.pstdev(present)
        for row, v in zip(rows, vals):
            if v is None or sd == 0:
                row[name + "_z"] = None if v is None else 0.0
                continue
            v = max(mu - k * sd, min(mu + k * sd, v))
            row[name + "_z"] = round((v - mu) / sd, 3)
    return rows
