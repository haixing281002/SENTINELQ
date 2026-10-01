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


def event_weight(li: LabelledItem, as_of: date, r: Rubric) -> float:
    """Recency weight x event confidence (A4). An item without event fields (legacy / tests) has confidence 1."""
    conf = li.item.confidence if li.item.confidence is not None else 1.0
    return recency_weight(li.item.published, as_of, r) * conf


def feeds_sentiment(li: LabelledItem) -> bool:
    return li.item.kind == "news" and li.item.purpose == "sentiment" and li.label.event_type != "price_move"


def weighted_sentiment(items: list[LabelledItem], as_of: date, r: Rubric) -> float | None:
    """Confidence-weighted, recency-weighted mean of ordinal sentiments over sentiment-pass and results-pass events (A4):
    sum(w_recency * conf * s) / sum(w_recency * conf). Price-only items never feed sentiment (Reconciliation fix 5)."""
    num = den = 0.0
    for li in items:
        if not feeds_sentiment(li):
            continue
        w = event_weight(li, as_of, r)
        num += w * li.label.sentiment
        den += w
    return num / den if den else None


def evidence_standard(items: list[LabelledItem], as_of: date, r: Rubric) -> dict:
    """Minimum evidence standard (Reconciliation fix 6): below N relevant articles, or with no results print in the window,
    sentiment is 'Insufficient Data' rather than a forced +1 or 0. Also measures how much of the lookback the evidence really covers."""
    c = r["sentiment"]
    rel = [li for li in items if li.item.kind == "news" and li.item.purpose == "sentiment" and li.label.event_type != "price_move"]
    results = [li for li in rel if li.label.event_type in c["results_event_types"]]
    dates = sorted(li.item.published for li in rel if li.item.published)
    span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days if dates else None
    why = ""
    event_mode = any(li.item.confidence is not None for li in rel)
    results_ev = [li for li in rel if li.label.event_type in c["results_event_types"] or li.item.pass_ == "results"]
    windows = {li.item.window for li in rel if li.item.window}
    if event_mode:            # A4 minimum evidence: >= 6 events, spanning >= 2 windows, >= 1 results event
        from .config import MIN_EVENTS, MIN_RESULTS_EVENTS, MIN_WINDOWS
        if len(rel) < MIN_EVENTS:
            why = f"only {len(rel)} event(s) (minimum {MIN_EVENTS})"
        elif len(windows) < MIN_WINDOWS:
            why = f"events fall in only {len(windows)} of the 4 windows (minimum {MIN_WINDOWS})"
        elif len(results_ev) < MIN_RESULTS_EVENTS:
            why = "no results event in the 12-month window"
    elif len(rel) < c["min_relevant_articles"]:
        why = f"only {len(rel)} relevant article(s) (minimum {c['min_relevant_articles']})"
    elif c["require_results_print"] and not results:
        why = "no results / earnings / guidance item in the 12-month window"
    return {"n": len(rel), "results": len(results_ev if event_mode else results), "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "span": span, "insufficient": why, "collapsed": span is not None and span < c["min_span_days"], "windows": sorted(windows),
            "event_mode": event_mode}


def coverage_map(items: list[LabelledItem], ev: dict) -> dict:
    """A4 coverage map for one stock: per-window event counts, results events, T1/T2 share, confidence-weighted n, dominance."""
    from .config import RESULTS_EVENTS_MAX, WINDOW_DOMINANCE
    from .sampler import window_counts
    news = [li for li in items if li.item.kind == "news"]
    rel = [li for li in news if feeds_sentiment(li)]
    wc = window_counts([li.item for li in rel])
    n = len(rel)
    t12 = sum(1 for li in news if li.item.max_tier in ("T1", "T2"))
    share = round(t12 / len(news), 2) if news else None
    cwn = round(sum((li.item.confidence if li.item.confidence is not None else 1.0) for li in rel), 1)
    dominated = n >= 6 and max(wc.values() or [0]) / n > WINDOW_DOMINANCE
    zero = [f"W{k}" for k, v in wc.items() if v == 0]
    text = (" ".join(f"W{k}:{v}" for k, v in wc.items()) + f" · events {n} · results {ev['results']}/{RESULTS_EVENTS_MAX}"
            + (f" · T1/T2 share {share:.0%}" if share is not None else "") + f" · conf-weighted n {cwn}")
    if zero:
        text += " · no events in " + ", ".join(zero)
    if dominated:
        text += f" · W{max(wc, key=wc.get)} dominates ({max(wc.values()) / n:.0%} of events)"
    return {"text": text, "windows": wc, "n_events": n, "n_results": ev["results"], "tier12_share": share, "conf_weighted_n": cwn,
            "dominated": dominated}


def _penalty(pen, lab):
    p = pen.get(lab.event_type)
    if isinstance(p, dict):
        p = p.get(lab.materiality or "medium", p["medium"])
    return p


_EXIT_WORDS = re.compile(r"resign|quit|steps? down|stepped down|exit|terminat|sacked|removed|ousted|abrupt|dismiss|relieved|fired", re.I)
_BENIGN_PEOPLE = re.compile(r"elevat|promot|re-?designat|appointed as|appoints|appointment of|named (as )?(the )?head|takes? charge|"
                            r"assum(es|ed) charge|superannuat|retire|retirement|succession|completion of (his|her|the) (term|tenure)|"
                            r"transition|move[sd]? to|to head|new role|additional charge|appointed|reappoint|\\bhires?\\b|\\bhired\\b|incoming|joins as|succe(ed|ssor)", re.I)
_C_SUITE = re.compile(r"\b(ceo|cfo|cto|coo|cio|cmo|cpo|md|chief [a-z&/ ,-]{2,40} officer|chief executive|managing director|"
                      r"company secretary|compliance officer)\b", re.I)


def literal_rule_override(li: LabelledItem) -> str | None:
    """Text-level safety net behind the model for people items: a 'management_exit' is only a penalty if it really is an UNPLANNED exit of
    a CXO / CS / CFO / compliance officer. Returns the reason NOT to penalise, else None."""
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


_MACRO = re.compile(r"customs duty|import duty|duty hike|gst (rate|council|cut|hike)|union budget|\bbudget\b|tariff|rbi (repo|rate)|monetary policy|"
                    r"sector[- ]wide|industry[- ]wide|all (companies|manufacturers|banks)|new rules? for|tightens? (rules|norms)|(rules|norms) (tightened|tighten)|cough[- ]syrup", re.I)
_VICTIM = re.compile(r"(arrest|booked|held|nabbed).{0,60}(cheat|defraud|fraud).{0,60}\b(bank|company)\b|\bbank (was )?(cheated|defrauded)|appraisers? (arrested|cheat)", re.I)


def _norm_key(k: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (k or "").lower())


def _tokens(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", t.lower())}


_STOP = {"over", "with", "from", "that", "this", "into", "after", "have", "will", "been", "company", "limited", "india", "indian", "news", "issues",
         "announces", "says", "report", "reports", "notice", "probe", "begins", "launches"}


def same_event(a: LabelledItem, b: LabelledItem, own_tokens: set[str], days: int) -> bool:
    """Same real-world event? Same family, within `days`, and the same key - or at least two distinctive words in common (the company's
    own name and generic words are ignored), covering at least half of the shorter description. Catches Nestle: 'FSSAI notice ... Maggi' on
    14-Jun and 'FSSAI probe ... Maggi' on 15-Jun are ONE event."""
    if _family(a.label.event_type) != _family(b.label.event_type):
        return False
    try:
        if abs((date.fromisoformat(a.item.published) - date.fromisoformat(b.item.published)).days) > days:
            return False
    except ValueError:
        return False
    ka, kb = _norm_key(a.label.event_key), _norm_key(b.label.event_key)
    if ka and ka == kb:
        return True
    def words(li):
        return (_tokens(li.item.title) | _tokens((li.label.event_key or "").replace("-", " "))) - own_tokens - _STOP
    wa, wb = words(a), words(b)
    shared = wa & wb
    gap = abs((date.fromisoformat(a.item.published) - date.fromisoformat(b.item.published)).days)
    return len(shared) >= 2 and (len(shared) / max(min(len(wa), len(wb)), 1) >= 0.5 or gap <= 3)   # notice day 1 / probe day 2-3 = one event


_FAMILY = {"regulatory_action": "regulatory", "investigation": "regulatory", "exchange_fine": "regulatory", "litigation": "regulatory",
           "management_exit": "people", "board_independence": "people", "auditor_resignation": "auditor", "auditor_restatement": "auditor",
           "rpt_concern": "rpt"}


def _family(et: str) -> str:
    return _FAMILY.get(et, et)


def gate(li: LabelledItem, r: Rubric) -> tuple[float | None, str, str]:
    """The governance VERIFICATION GATE (Reconciliation fix 2). Returns (penalty or None, why-not or basis, bucket).
    Answers, before any penalty: is the company the subject? did it happen there? how severe? - and applies the written-down conventions."""
    g = r["governance"]
    c = g["conventions"]
    lab, it = li.label, li.item
    base = _penalty(g["penalties"], lab)
    if it.penalty_override is not None:                          # a human set the weight on verified evidence: recorded, not second-guessed
        return float(it.penalty_override), f"human-set weight ({it.verified or 'verified'}; {it.source_ref or 'see evidence'})", "override"
    if base is None:
        return None, g.get("routine_types", {}).get(lab.event_type, "no penalty for this type"), "routine"
    from .config import CONF_UNPENALISED_BELOW
    if it.confidence is not None and it.confidence < CONF_UNPENALISED_BELOW and it.max_tier not in ("T1", "T2"):
        return None, (f"confidence {it.confidence:.2f} < {CONF_UNPENALISED_BELOW} with no T1/T2 source ({it.n_sources} source(s), best tier "
                      f"{it.max_tier or 'T3'}) - listed, not penalised"), "confidence"
    text = f"{it.title}. {lab.rationale}"
    # 1. subject: the company must be the subject (not victim / lender / peer / sector / macro / shareholders)
    if lab.subject and lab.subject not in c["subject_ok"]:
        return None, f"subject is {lab.subject.replace('_', ' ')}, not the company - " + c["subject_never_penalised"].get(lab.subject, ""), "subject"
    if lab.occurred_at_company is False:
        return None, "the event did not occur at the company", "subject"
    if lab.event_type in ("regulatory_action", "investigation", "exchange_fine") and _VICTIM.search(it.title):
        return None, "the company is the victim, not the subject", "subject"
    if lab.event_type == "regulatory_action" and _MACRO.search(it.title) and not re.search(r"\b(sebi|rbi|nse|bse|order|penalt|fined?|settle)", it.title, re.I):
        return None, "macro / sector policy is never company regulatory action", "subject"
    if lab.event_type == "regulatory_action" and lab.subject in ("peer_or_sector", "macro_policy"):
        return None, "macro / sector policy is never company regulatory action", "subject"
    # 2. people items: direction and tier
    if lab.event_type == "management_exit":
        why = literal_rule_override(li)
        if why:
            return None, why, "people"
        if lab.people_direction in c["people_never_an_exit"]:
            return None, f"{lab.people_direction.replace('_', ' ')} is never a management exit", "people"
        if lab.role_tier and lab.role_tier != c["exit_requires_role_tier"]:
            return None, f"{lab.role_tier.replace('_', ' ')}: below CXO / CS / CFO / compliance tier", "people"
    # 3. stage: resolved / routine matters are not a live overhang
    if lab.action_stage == "resolved_or_quashed":
        return None, c["resolved_never_penalised"], "resolved"
    if lab.action_stage == "routine" and lab.event_type != "management_exit":
        return None, "a routine compliance statement or filing, not an adverse event", "routine"
    p = base
    basis = f"{lab.event_type} base {base:+g}"
    # 4. a notice / demand with no order is an overhang (-10), not an order or settlement (-20)
    if lab.event_type == "regulatory_action" and lab.action_stage == "notice_or_demand":
        p = max(p, c["notice_without_order_penalty"])
        basis = f"notice or demand with no order -> overhang {p:+g}"
    # 5. severity caps and the materiality floor (integrity-type matters are exempt)
    if lab.event_type not in c["severity_caps_exempt_types"]:
        cap = c["severity_caps"].get(lab.severity or "")
        if cap is not None:
            p = max(p, cap)
            basis += f"; severity {lab.severity} caps at {cap:+g}"
        exempt = c.get("floor_exempt_severities", ["integrity", "minor"])   # a stated 'minor' is the labeller's judgement that it is more than procedural
        if lab.severity not in exempt and lab.amount_inr_cr is not None and lab.amount_inr_cr < c["materiality_floor_cr"]:
            p = max(p, c["procedural_band_penalty"])
            basis += f"; Rs {lab.amount_inr_cr:g} cr is under the Rs {c['materiality_floor_cr']:g} cr floor -> procedural band {c['procedural_band_penalty']:+g}"
    return p, basis, "penalty"


def governance(items: list[LabelledItem], r: Rubric, company_name: str = "") -> tuple[float, str, list[dict]]:
    """Start at 100. Every governance-flagged item passes the verification gate; survivors are de-duplicated by real-world event
    (same event_key, or the same event reported within `dedupe_days`), then fixed penalties apply once per type. The -5 memory discount
    for out-of-window events applies only if NO in-window penalty exists. Items deliberately not penalised are kept, with the reason."""
    g = r["governance"]
    c = g["conventions"]
    score, applied = float(g["start"]), []
    historical = []
    ignored = governance.ignored = []      # read by score_portfolio: flagged but deliberately not penalised, with the reason

    def skip(li, why):
        ignored.append({"event_type": li.label.event_type, "date": li.item.published, "headline": li.item.title, "url": li.item.url,
                        "source_ref": li.item.source_ref, "why": why})

    cands = []
    for li in sorted(items, key=lambda x: x.item.published):
        lab = li.label
        if not lab.governance_flag:
            continue
        p, why, bucket = gate(li, r)
        if p is None:
            skip(li, why)               # flagged but not penalised: always recorded with the reason
            continue
        if lab.historical and li.item.penalty_override is None:
            historical.append(li)
            continue
        cands.append((li, p, why))

    # same-event de-duplication: one real-world event is ONE event, whatever number of days / types it was reported as
    cands.sort(key=lambda x: x[1])                                            # most severe first
    kept: list[tuple] = []
    own = _tokens(company_name) | {w for w in _tokens(" ".join(x.item.symbol for x, _, _ in cands))}
    for li, p, why in cands:
        dup = None
        for kli, kp, _ in kept:
            if same_event(li, kli, own, c["dedupe_days"]):
                dup = kli
                break
        if dup:
            skip(li, f"same event as '{dup.item.title[:70]}' ({dup.item.published}) - counted once, at the most severe weight")
            continue
        kept.append((li, p, why))

    seen_types = set()
    for li, p, why in sorted(kept, key=lambda x: (x[0].item.penalty_override is None, x[0].item.published)):   # human-weighted first
        et = li.label.event_type
        if g["count_mode"] == "once_per_type" and et in seen_types and li.item.penalty_override is None:
            skip(li, f"a second {et.replace('_', ' ')} - each event type is penalised once")
            continue
        seen_types.add(et)
        score += p
        applied.append({"event_type": et, "penalty": p, "date": li.item.published, "headline": li.item.title, "url": li.item.url,
                        "source_ref": li.item.source_ref, "verified": li.item.verified, "basis": why, "event_id": li.item.event_id,
                        "verification": {"subject_is_company": li.label.subject in (None, "company", "subsidiary", "promoter_or_insider"),
                                         "event_at_company": li.label.occurred_at_company, "direction": li.label.people_direction,
                                         "severity": li.label.severity, "stage": li.label.action_stage, "amount_inr_cr": li.label.amount_inr_cr},
                        "n_sources": li.item.n_sources, "max_tier": li.item.max_tier, "confidence": li.item.confidence})

    # memory discount: only when nothing in the window was penalised (Reconciliation fix 3)
    memory = [li for li in historical if li.label.event_type not in {a["event_type"] for a in applied}]
    if memory and (not c.get("memory_discount_only_if_no_in_window_penalty") or not any(a["event_type"] != "mnc_structural_discount" for a in applied)):
        li = memory[-1]
        score += g["historical_penalty"]
        applied.append({"event_type": "historical (" + li.label.event_type + ")", "penalty": g["historical_penalty"], "date": li.item.published,
                        "headline": li.item.title, "url": li.item.url, "source_ref": "", "verified": "", "basis": "memory discount - no in-window penalty exists"})
    elif memory:
        for li in memory:
            skip(li, "out-of-window event: memory discount not applied because an in-window penalty exists")
    label = next(x["label"] for x in g["labels"] if score >= x["min"])
    return score, label, applied


def corporate_actions(items: list[LabelledItem], r: Rubric) -> tuple[float, list[dict]]:
    ca = r["corporate_actions"]
    from .lint import unique_actions
    total, detail = 0.0, []
    for li in unique_actions(items, r):          # repeat headlines of one declaration are ONE action
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
        ev = evidence_standard(items, as_of, r)
        if ev["insufficient"]:
            cs = None                                                     # Insufficient Data - never a forced +1 / 0
        g_score, g_label, pens = governance(items, r, h.name + " " + h.aliases.replace("|", " "))
        g_ignored = list(governance.ignored)
        ca_score, ca_detail = corporate_actions(items, r)
        n_news = sum(1 for i in items if i.item.kind == "news" and i.item.purpose == "sentiment")
        low = n_news < r["confidence"]["low_below_items"]
        pen_txt = ", ".join(f"{p['event_type']} {p['penalty']:+g}" for p in pens) or "none"
        note = ("INSUFFICIENT DATA: " + ev["insufficient"]) if ev["insufficient"] else ""
        if ev["collapsed"] and not ev["insufficient"]:
            note = f"EVIDENCE WINDOW COLLAPSED: articles span only {ev['span']} days ({ev['first']}..{ev['last']}) of the 12-month lookback"
        low = low or bool(ev["insufficient"]) or ev["collapsed"]
        cm = coverage_map(items, ev)
        rat = (f"Governance = {r['governance']['start']} with penalties [{pen_txt}] = {g_score:g} ({g_label}). "
               f"Sentiment from {ev['n']} relevant news item(s), half-life {r['sentiment']['half_life_trading_days']} trading days."
               + (f" {note}." if note else "") + (" LOW CONFIDENCE." if low else ""))
        out.append(StockScore(
            symbol=h.symbol, name=h.name, sector=h.sector, company_sentiment=to_integer(cs),
            company_sentiment_raw=None if cs is None else round(cs, 3),
            sector_sentiment=None if sector_sent.get(h.sector) is None else round(sector_sent[h.sector], 2),
            governance_score=g_score, governance_label=g_label, governance_penalties=pens, corporate_action_score=round(ca_score, 2),
            corporate_action_detail=ca_detail, n_items=len(items), n_dropped=dropped_counts.get(h.symbol, 0), low_confidence=low,
            rationale=rat, cap=h.cap, weight=h.weight, sentiment_note=note, relevant_articles=ev["n"], evidence_first=ev["first"],
            evidence_last=ev["last"], evidence_span_days=ev["span"], governance_ignored=g_ignored,
            coverage_map=cm["text"], n_events=cm["n_events"], n_results_events=cm["n_results"], window_events=cm["windows"],
            tier12_share=cm["tier12_share"], conf_weighted_n=cm["conf_weighted_n"], window_dominated=cm["dominated"]))
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
