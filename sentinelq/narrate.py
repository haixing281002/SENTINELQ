"""Stage 6 prose. Text only - the scores are already fixed. A narrator explains the evidence and
the penalties that were mechanically applied; it never changes or invents a number.

AnthropicNarrator writes the prose from the evidence rows; TemplateNarrator is a deterministic
fallback (offline/tests) that assembles the same fields from the rubric arithmetic."""
from __future__ import annotations
import json
import os
import pathlib
import re
from collections import Counter
from typing import Protocol

from .models import LabelledItem, StockScore

CA_TYPES = {"dividend", "buyback", "dilution", "capacity_expansion", "open_offer_takeover", "lock_in_expiry", "pledge", "pledge_invocation", "insolvency_admission"}

# Calibrate how explicit/intense the written risk must be, by band - never printed, never named in the output (the
# rule against stating a score/label/band still holds), just steering so a Flag/Exit holding doesn't read in the
# same measured register as a Clean/Log one. Without this, severity got flattened: a severe item and a mild one
# ended up sounding equally matter-of-fact.
_GOV_TIER = {
    "Clean": "no material governance risk found here - keep this brief and matter-of-fact, do not manufacture concern where none exists",
    "Watch": "a real but contained concern - name it plainly and say what would make it worse, calibrated to 'worth monitoring', not alarmed",
    "Flag": ("the most severe governance tier this scorecard assigns - this is NOT a minor item. Be explicit about the actual "
             "downside for a holder (loss of confidence, forced selling, management distraction, a pattern likely to recur) and "
             "why it would warrant real caution before adding risk. Do not undersell this with soft, hedged language."),
}
_CA_TIER = {
    "Log": "negligible for an entry decision - keep this brief, do not dramatize",
    "Watch": "worth noting but not yet a reason to hesitate - name it plainly without dramatizing",
    "Review": "a real reason to pause before adding new money - be specific about what could go wrong and roughly how large the hit (dilution %, cost, timeline) could be",
    "Act": ("serious enough that a holder should already be reassessing position size or hedging - be explicit about the "
            "downside and the timeline, do not undersell it with soft language"),
    "Exit": ("the most severe tier this scorecard assigns - make clear this is a real reason to hold off entering entirely "
             "until it resolves, not just a line item to note in passing"),
}

SYSTEM = (
    "You write the analyst commentary for an investment-committee RISK-REVIEW scorecard covering 75+ holdings at once. "
    "The IC is scanning many stocks in one sitting, not reading an article about one. House style: plain, factual, precise, "
    "no hype, no advice. Scores are computed separately by fixed rules and shown in colour right next to your text; your job "
    "is to explain WHY the score landed where it did (the mechanism, grounded in the real events, not an abstraction and not "
    "a bare chronology), and you must NEVER state a score, band, label, mean, points or penalty number (that is already "
    "shown). Use ONLY the evidence rows supplied; add no facts from memory. "
    "EXPLAIN, DO NOT LIST: this is the most important rule and the one you are most likely to break. Write the way you would "
    "actually explain the stock out loud to a fund manager who has thirty seconds, not a research note stuffed with every "
    "number you have. A sentence that packs in three figures and two event names back to back is a LIST wearing a sentence's "
    "clothes, not an explanation, and it fails this task even if every fact in it is true. For each point: say what happened "
    "in plain words, then say what it means or why it matters, in a separate clause or sentence. Pick the one or two facts "
    "that actually carry the point and drop the rest; a reader should finish a sentence knowing what to think, not just what "
    "occurred. Never chain three or more distinct events together with commas or semicolons as a way to fit more in; give the "
    "two or three things that matter most their own sentence each, in order of how much they matter. "
    "DO NOT write a diary-style recap ('on 5-Jun the company announced..., then on 12-Jul...') and do not write a numbers "
    "dump ('AUM grew 26%, profit rose 22%, GNPA was 1.27%, and brokerages upgraded...'). Instead: 'the business is growing "
    "fast and profitably, which is what is driving the score up' - the kind of sentence a busy person actually absorbs - "
    "then, separately, the one number that best proves it, only if a number is needed at all. "
    "OMIT DATES by default; cite one only when the timing itself is the reason for the read (an event so recent it dominates, "
    "or a corporate action with a near-term effective date). Cite at most one figure per sentence, and only when the size of "
    "the number is itself the point, not as decoration or proof-by-volume. Write 'Rs' for rupees (never the rupee symbol). If "
    "evidence is thin, say so in one short clause. "
    "PRICE RULE: never mention share-price moves, 52-week highs, rallies or sell-offs (price is context only). "
    "PUNCTUATION RULE: never use an em dash, en dash, or a hyphen surrounded by spaces as a clause break; use a comma, "
    "semicolon or period instead. A plain hyphen inside a compound word (non-executive, two-wheeler, one-off) is fine; a "
    "dash standing in for 'and', 'but' or a pause is not. "
    "STYLE RULES: write the finished report text, never commentary about your inputs; do not say 'the evidence', 'the facts "
    "supplied', 'rows', 'payload' or 'provided'. Never print URLs. Write event types as words ('regulatory action', not "
    "regulatory_action). Use a real minus sign or plain hyphen for negatives (-20). "
    "LENGTH: sentiment, governance and corporate actions all get real room now (see each field's guidance below), but that "
    "room is for explaining clearly, not for citing more facts. If you are tempted to add another clause to an already-long "
    "sentence, stop and start a new sentence instead, or cut the fact. No filler, no hedging, no restating the same point "
    "twice. Answer by calling the tool."
)

TOOL = {
    "name": "write_commentary",
    "description": "Commentary fields for one holding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment_rationale": {"type": "string", "description": (
                "4-5 full sentences (~130-160 words) on WHY the sentiment score landed where it did, written as an "
                "EXPLANATION, not a list of facts, in a FORMAL institutional register (this is IC material, not a casual "
                "briefing note - avoid conversational phrasing like 'big enough to weigh on the stock' or 'the growth "
                "story'; write the way a credit or equity research note would). Use 'sentiment_how_scored' (the same "
                "figures behind the Excel 'Sentiment - why' column) to find the real top_events_pushing_up and "
                "top_events_pushing_down, and translate them into prose: state what kind of year the company had and why "
                "(sustained demand growth, a one-off setback, a structural concern), THEN substantiate it with a figure "
                "(a growth rate, a profit number, a deal size). Open directly with the substantive point, never with a "
                "generic topic sentence ('Business momentum is the dominant theme here', 'This was a strong year') that "
                "says nothing a figure could not say better. Cite a supporting figure for the main driver, for the main "
                "offset, and for at least one secondary point too (three to four figures total across the rationale is "
                "right here, more than the other two fields, since sentiment carries the most evidence); still never "
                "stack more than one figure into the same clause. STRUCTURE FOLLOWS 'sentiment_how_scored.read_balance', "
                "NOT a fixed template - there is no rule that the positive side always goes first. If read_balance says "
                "the read is dominant positive, lead with the positive driver. If it says the read is closely contested "
                "or weighed toward neutral, LEAD with what is dragging the score down and say outright that the positive "
                "case is not enough to clearly outweigh it (e.g. 'the positive case here is real but not decisive' or "
                "'what is holding this back matters more than what is working') - do not open with the positive story "
                "and only mention the drag as an afterthought when the actual balance is this close. Every holding's "
                "opening sentence should read differently depending on where it actually sits, not identically "
                "structured 'strong growth, offset by X' regardless of how close the real balance is. Whichever side "
                "leads, give the other its own sentence, and close with how they net out and HOW DECISIVELY (a landslide "
                "versus a close call are different endings). A sentence that names three events and two numbers in one "
                "breath has failed the task; split it. No dates unless the timing itself is the reason. NEVER state any "
                "score, mean, band, rating, points or label (no '+1', no 'weighted mean', no 'band'). Use commas and "
                "periods, never a dash.")},
            "ca_rationale": {"type": "string", "description": (
                "2-4 full sentences (~85-130 words), written as an EXPLANATION for the FM, not a news recap or a list of "
                "events. Match your intensity to 'ca_risk_emphasis' - a Log/Watch-tier item gets a plain, brief mention, "
                "but a Review/Act/Exit-tier item needs you to actually spell out the risk: what could go wrong, roughly "
                "how large the hit could be (dilution %, cost, lost optionality), and over what timeframe, not just 'this "
                "is live, factor it in.' For each live SCORED corporate action: say plainly what it is and why it matters "
                "for an entry decision today, in its own sentence, not chained to other events with commas. If there is "
                "more than one live event, give each its moment, in order of how much it matters. "
                "ALSO: if 'ca_quarantined_not_scored' is non-empty, add ONE short sentence per entry (not one per article - "
                "these are already grouped by type) simply flagging it: what kind of action it is and whether it reads "
                "positive, negative, or mixed for shareholders (from its 'direction'), e.g. 'Separately, the company has "
                "also been acquisitive this year, picking up a handful of smaller businesses, a reasonable growth read.' "
                "Do NOT explain that it is excluded from the score, why, or that it 'does not count toward the number "
                "above' - that methodology is already covered once on page 2 of the report; repeating it in every "
                "holding's write-up is just noise that crowds out the actual point. State the event and its read, nothing "
                "more. Do not invent a severity or an entry-blocking read for it the way you would for a scored event. "
                "If there is nothing live AND nothing quarantined, say plainly 'No corporate-action concern for entry.' "
                "NEVER state any score, band, severity number or multiplier for a SCORED event, and never mention sourcing "
                "or outlet reliability (that lives in the Excel appendix, not here). Use commas and periods, never a dash.")},
            "governance_rationale": {"type": "string", "description": (
                "2-3 full sentences (~55-70 words), written as an EXPLANATION of the RISK this stock's governance history "
                "poses GOING FORWARD, not a list of events. Match your intensity to 'governance_risk_emphasis' - a Clean "
                "holding gets a brief, matter-of-fact line, but a Flag-tier holding needs you to actually spell out the "
                "consequence for a holder (forced selling, management credibility, a pattern likely to recur), not just "
                "name the event and move on. Say plainly what kind of risk it is, if any, and why that matters for a "
                "holder, in everyday language, before naming the specific item. NEVER state any score, penalty number or "
                "label. If nothing adverse: 'No governance risk identified in the lookback.' Mention routine items "
                "reviewed and deliberately not treated as adverse only if relevant, and explain briefly why they did not "
                "count. Use commas and periods, never a dash.")},
            "one_line_read": {"type": "string", "description": "ALWAYS fill this (never empty): 2 to 5 words naming what drives the year, e.g. 'Confirmed compounder', 'Momentum intact', 'Regulatory scar; strong operations'."},
            "key_corporate_action": {"type": "string", "description": "Terse list of the key corporate actions among the supplied corporate-action events only, e.g. 'Div Rs 46 final + Rs 20 interim' or 'Rs 5,633cr buyback @ Rs 12,000 (open 1-Jul)'. Write 'None' if there are none."},
            "coverage_note": {"type": "string", "description": "Only if coverage is thin or sources conflict AND the sentiment rationale does not already say so; otherwise return an empty string. Never repeat what the rationale states. Keep to one short clause if used."},
        },
        "required": ["sentiment_rationale", "governance_rationale", "ca_rationale", "one_line_read", "key_corporate_action", "coverage_note"],
        "additionalProperties": False,
    },
}

OBS_GUIDE = (
    " Write exactly 4 portfolio-level observations in this order: (1) sentiment delivery across the book, with counts; "
    "(2) governance Flags and any shared pattern between them (name the companies, the events, dates); (3) corporate-action "
    "intensity (buybacks, bonuses, mergers, large raises) and what it means for position management; (4) coverage gaps per stock. "
    "Each observation has a 'title' that is a short bold lead-in sentence ending in a full stop (e.g. 'Broad delivery is strong.') and "
    "a 'body' that continues in the same paragraph. Use only the facts supplied."
)


def fmt_sent(x):
    return "n/a" if x is None else f"{x:+d}" if x else "0"


def evidence_payload(s: StockScore, items: list[LabelledItem], as_of=None, rubric=None) -> dict:
    """What the commentary writer sees. No scores (they are shown elsewhere, not narrated). Gate-B events (splits, bonuses,
    mergers/acquisitions, demergers, rights issues, special dividends, ISIN changes) are excluded from the Risk/Opportunity
    ARITHMETIC - share-count/structure changes need an adjustment-factor check before price/fundamentals are comparable
    again - but they are NOT hidden from the reader: 'ca_quarantined_not_scored' groups them by type (direction, how many
    articles, the most recent headline) so the CA rationale can flag them, positive or negative, in one short sentence -
    without re-explaining the exclusion methodology every time (that is covered once, on page 2 of the report).
    Includes 'governance_risk_emphasis' and 'ca_risk_emphasis' - plain-English calibration (derived from, but never equal to,
    the band) so a Flag/Exit holding is written with real weight instead of the same measured register as a Clean/Log one.

    When as_of/rubric are supplied, also includes 'sentiment_how_scored' - the same top-driver arithmetic (drivers()) that
    feeds the Excel 'Sentiment - why' column, so the written rationale names the real events behind the score rather than
    re-deriving (and possibly disagreeing with) its own reading of the raw evidence list."""
    hidden = {e["url"] for e in s.ca_gate_b}
    gate_a_band = bool(s.ca_gate_a)   # Gate A overrides straight to Exit regardless of the arithmetic band
    gate_b_by_type: dict[str, list] = {}
    for e in s.ca_gate_b:
        gate_b_by_type.setdefault(e["event_type"], []).append(e)
    quarantined = []
    for et, events in gate_b_by_type.items():
        latest = sorted(events, key=lambda x: x["date"], reverse=True)
        dirs = {e["direction"] for e in latest}
        quarantined.append({"event": et.replace("_", " "), "direction": dirs.pop() if len(dirs) == 1 else "mixed",
                            "article_count": len(latest), "most_recent_headline": latest[0]["headline"], "most_recent_date": latest[0]["date"]})
    payload = {
        "company": s.name, "sector": s.sector,
        "sentiment_status": "insufficient data" if s.company_sentiment is None else "ok",
        "evidence_span": f"{s.evidence_first}..{s.evidence_last}" if s.evidence_first else "none",
        "governance_risk_emphasis": _GOV_TIER.get(s.governance_label, ""),
        "ca_risk_emphasis": _CA_TIER.get("Exit" if gate_a_band else s.ca_risk_band, ""),
        "ca_events": [{"event": e["event_type"].replace("_", " "), "date": e["date"], "headline": e["headline"],
                       "direction": e["direction"], "stage": e["stage"]}
                      for e in sorted(s.ca_events, key=lambda x: -x["score"])],
        "ca_hard_override": [{"event": e["event_type"].replace("_", " "), "date": e["date"], "headline": e["headline"]} for e in s.ca_gate_a],
        "ca_quarantined_not_scored": quarantined,
        "adverse_governance_events": [{"event": p["event_type"].replace("_", " "), "date": p["date"], "headline": p["headline"]}
                                      for p in s.governance_penalties],
        "reviewed_not_adverse": [{"event": p["event_type"].replace("_", " "), "date": p["date"], "headline": p["headline"], "why": p["why"]}
                                 for p in s.governance_ignored if p.get("url") not in hidden],
        "low_coverage": s.low_confidence,
        "evidence": [{"date": li.item.published, "headline": li.item.title, "what_it_says": li.label.gist or (li.item.snippet or "")[:300],
                      "event": li.label.event_type.replace("_", " "), "sentiment": li.label.sentiment,
                      "governance_flag": li.label.governance_flag, "historical": li.label.historical}
                     for li in sorted(items, key=lambda x: x.item.published, reverse=True) if li.item.url not in hidden],
    }
    if as_of is not None and rubric is not None and s.company_sentiment is not None:
        from .score import drivers
        pos, neg, facts = drivers(items, as_of, rubric)
        ev_line = lambda li: {"event": li.label.event_type.replace("_", " "),
                              "what": (li.label.gist or li.item.snippet or li.item.title or "")[:160]}
        pos_total = sum(p for _, p in pos)
        neg_total = sum(-p for _, p in neg)
        denom = pos_total + neg_total
        pos_share = pos_total / denom if denom > 0 else 0.5
        if pos_share >= 0.75:
            balance = "clearly dominant positive: lead with the positive driver"
        elif pos_share >= 0.58:
            balance = "net positive but meaningfully offset: lead with the positive driver, give the offset real weight"
        elif pos_share >= 0.42:
            balance = ("closely contested, weighed toward neutral: lead with what is dragging the score down, not "
                       "the positive case, and be explicit that the positive side does not clearly dominate")
        elif pos_share >= 0.25:
            balance = "net negative with a real positive case: lead with the negative/offsetting side"
        else:
            balance = "clearly dominant negative: lead with the negative side"
        payload["sentiment_how_scored"] = {
            "positive_articles": facts.get("n_pos", 0), "neutral_articles": facts.get("n_zero", 0),
            "negative_articles": facts.get("n_neg", 0),
            "recent_3m_share_of_weight": round(facts["recent"], 2) if facts.get("recent") is not None else None,
            "top_events_pushing_up": [ev_line(li) for li, _ in pos[:4]],
            "top_events_pushing_down": [ev_line(li) for li, _ in neg[:4]],
            "read_balance": balance,
        }
    return payload


_HIDE = ("quarantin", "gate b")   # internal mechanism jargon only - NOT the event types themselves (bonus/split/merger
                                   # mentions are now wanted in the CA rationale; this used to blacklist those words too
                                   # and would have deleted every sentence this feature exists to produce)

# Hard backstop for the per-holding budget (sentiment gets the most room since it draws on the most evidence) - word
# count, not char count, so it cuts at a sentence boundary rather than mid-sentence.
_MAX_WORDS = {"sentiment_rationale": 170, "governance_rationale": 85, "ca_rationale": 140}


def _cap_words(text: str, n: int) -> str:
    """Trim to whole sentences totalling at most n words. Never cuts mid-sentence: if even the first sentence alone
    is over the cap, that one sentence is kept whole rather than mangled (a slightly long sentence beats a broken
    one - this already cost a real holding a nonsense 'but on.' ending once)."""
    if len(text.split()) <= n:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept, count = [], 0
    for s in sentences:
        wc = len(s.split())
        if kept and count + wc > n:
            break
        kept.append(s)
        count += wc
    return " ".join(kept) if kept else text


_DASH_SEP = re.compile(r"\s*[–—]\s*|\s+-\s+")   # em dash, en dash, or a hyphen used as a spaced clause break


def _delink_dashes(text: str) -> str:
    """The prompt asks for no dashes, but this is the backstop: any em dash, en dash, or spaced hyphen used as a
    clause break becomes a comma. A plain hyphen inside a word (non-executive, two-wheeler) is untouched."""
    return _DASH_SEP.sub(", ", text)


def scrub(out: dict) -> dict:
    """Last line of defence for the per-stock text: drop any sentence that still mentions a quarantined corporate action,
    strip any dash used as a clause break, and hard-cap each rationale's length so 75+ holdings of commentary stays a
    quick review, not a read."""
    clean = {}
    for k, v in out.items():
        if isinstance(v, str) and k != "one_line_read":
            paras = []
            for para in v.split("\n\n"):
                sents = re.split(r"(?<=[.!?])\s+", para)
                kept = [x for x in sents if not any(h in x.lower() for h in _HIDE)]
                if kept:
                    paras.append(" ".join(kept))
            v = "\n\n".join(paras)
            v = _delink_dashes(v)
            if k in _MAX_WORDS:
                v = _cap_words(v, _MAX_WORDS[k])
        clean[k] = v
    return clean



class Narrator(Protocol):
    def holding(self, s: StockScore, items: list[LabelledItem], as_of=None, rubric=None) -> dict: ...
    def portfolio(self, facts: dict) -> list[dict]: ...


def key_actions(items: list[LabelledItem], limit: int = 3) -> str:
    from .lint import unique_actions
    from .rubric import load_rubric
    acts = [li for li in sorted(unique_actions(items, load_rubric()), key=lambda x: x.item.published, reverse=True)
            if li.label.event_type in CA_TYPES]
    return "; ".join(li.item.title.rstrip(".") for li in acts[:limit]) or "—"


class TemplateNarrator:
    """Deterministic fallback: restates evidence and arithmetic, no interpretation."""
    def holding(self, s, items, as_of=None, rubric=None):   # as_of/rubric accepted (unused) so CachedNarrator can wrap this too
        news = sorted((li for li in items if li.item.kind == "news"), key=lambda x: x.item.published, reverse=True)
        pos = [li for li in news if li.label.sentiment > 0][:3]
        neg = [li for li in news if li.label.sentiment < 0][:3]
        line = lambda li: f"{li.item.title.rstrip('.')} ({li.item.published})"
        hidden = {e["url"] for e in s.ca_gate_b}
        pos = [li for li in pos if li.item.url not in hidden]
        neg = [li for li in neg if li.item.url not in hidden]
        sr = ([f"Insufficient data: {s.sentiment_note.replace('INSUFFICIENT DATA: ', '')}."] if s.company_sentiment is None else [])
        if pos:
            sr.append("Positive evidence: " + "; ".join(line(x) for x in pos) + ".")
        if neg:
            sr.append("Negative evidence: " + "; ".join(line(x) for x in neg) + ".")
        if not pos and not neg and s.company_sentiment is not None:
            sr.append("No clearly positive or negative company-specific news in the lookback.")
        pens = s.governance_penalties
        if pens:
            gr = "Adverse governance events: " + "; ".join(f"{p['event_type'].replace('_', ' ')} ({p['date']})" for p in pens) + "."
        else:
            gr = "No adverse governance events are present in the lookback."
        return {"sentiment_rationale": " ".join(sr), "governance_rationale": gr,
                "one_line_read": "Low-confidence coverage" if s.low_confidence else "",
                "ca_rationale": self._ca_fallback(s),
                "key_corporate_action": key_actions(items),
                "coverage_note": "Sparse news retrieval; treat this read as low confidence." if s.low_confidence else ""}

    @staticmethod
    def _ca_fallback(s):
        if not s.ca_events and not s.ca_gate_a:
            return "No corporate-action events in the lookback."
        parts = []
        top = sorted(s.ca_events, key=lambda e: -e["score"])[:2]
        if top:
            parts.append("Main corporate actions: " + "; ".join(f"{e['event_type'].replace('_', ' ')} ({e['date']})" for e in top) + ".")
            weak = [e for e in top if e["n_sources"] == 1 and e["max_tier"] in ("T3", "T4")]
            if weak:
                parts.append("Reported by a single lower-tier source (" + ", ".join(e["event_type"].replace("_", " ") for e in weak) + "); treat the date as unconfirmed.")
        if s.ca_gate_a:
            parts.append("Severe event requiring exit review: " + ", ".join(e["event_type"].replace("_", " ") for e in s.ca_gate_a) + ".")
        return " ".join(parts)

    def portfolio(self, facts):
        return default_observations(facts)


class AnthropicNarrator:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model_id = model or os.environ.get("SENTINELQ_MODEL", "claude-sonnet-5-5")

    def _call(self, system, user, tool):
        resp = self.client.messages.create(model=self.model_id, max_tokens=1800, system=system, tools=[tool],
                                           tool_choice={"type": "tool", "name": tool["name"]},
                                           messages=[{"role": "user", "content": user}])
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use":
                return dict(b.input)
        raise RuntimeError("narrator returned no structured output")

    def holding(self, s, items, as_of=None, rubric=None):
        out = self._call(SYSTEM, json.dumps(evidence_payload(s, items, as_of, rubric), default=str), TOOL)
        for k in ("sentiment_rationale", "governance_rationale", "ca_rationale", "one_line_read", "key_corporate_action", "coverage_note"):
            out.setdefault(k, "")
        return out

    def portfolio(self, facts):
        tool = {"name": "write_observations", "description": "Portfolio-level observations.",
                "input_schema": {"type": "object", "properties": {"observations": {"type": "array", "items": {
                    "type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["title", "body"]}}}, "required": ["observations"]}}
        try:
            return self._call(SYSTEM + OBS_GUIDE,
                              json.dumps(facts, default=str), tool)["observations"]
        except Exception:
            return default_observations(facts)


def portfolio_facts(scores: list[StockScore], kept: dict[str, list[LabelledItem]]) -> dict:
    dist = Counter(s.company_sentiment for s in scores if s.company_sentiment is not None)
    flags = [s for s in scores if s.governance_label == "Flag"]
    ca = Counter()
    from .lint import unique_actions
    from .rubric import load_rubric
    rub = load_rubric()
    for s in scores:
        for li in unique_actions(kept.get(s.symbol, []), rub):          # repeats of one declaration count once
            if li.label.event_type in CA_TYPES:
                ca[li.label.event_type] += 1
    companies = [{"company": s.name, "sector": s.sector, "sentiment": s.company_sentiment,
                  "governance": int(round(s.governance_score)), "label": s.governance_label,
                  "low_confidence": s.low_confidence,
                  "penalties": [{"event": p["event_type"].replace("_", " "), "penalty": p["penalty"], "date": p["date"]} for p in s.governance_penalties],
                  "corporate_actions": [li.item.title for li in unique_actions(kept.get(s.symbol, []), rub) if li.label.event_type in CA_TYPES][:4]}
                 for s in scores]
    return {"n": len(scores), "companies": companies, "sentiment_distribution": {str(k): v for k, v in sorted(dist.items(), reverse=True)},
            "flags": [{"company": s.name, "sector": s.sector, "score": s.governance_score,
                       "penalties": [(p["event_type"], p["penalty"], p["date"]) for p in s.governance_penalties]} for s in flags],
            "watch": [s.name for s in scores if s.governance_label == "Watch"],
            "corporate_action_counts": dict(ca),
            "low_confidence": [s.name for s in scores if s.low_confidence],
            "insufficient_data": [s.name for s in scores if s.company_sentiment is None],
            "window_collapsed": [s.name for s in scores if s.sentiment_note.startswith("EVIDENCE WINDOW COLLAPSED")]}


def default_observations(f: dict) -> list[dict]:
    d = f["sentiment_distribution"]
    dist = ", ".join(f"{v} at {int(k):+d}" if int(k) else f"{v} at 0" for k, v in d.items())
    obs = [{"title": "Sentiment distribution.", "body": f"Of {f['n']} holdings: {dist}."}]
    if f["flags"]:
        obs.append({"title": f"{len(f['flags'])} governance Flag(s).",
                    "body": "; ".join(f"{x['company']} ({x['sector']}) scores {x['score']:g} from " +
                                      ", ".join(f"{t.replace('_', ' ')} {p:+g}" for t, p, _ in x["penalties"]) for x in f["flags"]) + "."})
    else:
        obs.append({"title": "No governance Flags.", "body": "No holding scores below 80."})
    if f["corporate_action_counts"]:
        obs.append({"title": "Corporate-action activity.",
                    "body": ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in f["corporate_action_counts"].items()) + " identified in the window."})
    if f.get("insufficient_data"):
        obs.append({"title": "Insufficient data is reported, not forced.", "body": "No sentiment score is issued for " + ", ".join(f["insufficient_data"]) +
                    " because the minimum evidence standard (at least 8 relevant articles including a results print) was not met."})
    if f.get("window_collapsed"):
        obs.append({"title": "Evidence window.", "body": "For " + ", ".join(f["window_collapsed"]) + " the retrieved articles cover only a short part of the 12-month lookback; read those scores with caution."})
    if f["low_confidence"]:
        obs.append({"title": "Coverage gaps are stated per stock.", "body": "Sparse retrieval (low confidence): " + ", ".join(f["low_confidence"]) + "."})
    return obs


class CachedNarrator:
    """Reuses prose for identical evidence + scores, so re-runs are stable and free."""
    def __init__(self, inner, path, model_id="template"):
        import hashlib
        self.inner, self.model_id, self._h = inner, model_id, hashlib
        self.path = pathlib.Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        import threading
        self._lock = threading.Lock()
        self._d = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._d[r["key"]] = r["value"]

    def _wrap(self, kind, payload, fn):
        key = self._h.sha256(f"{kind}|{self.model_id}|{json.dumps(payload, sort_keys=True, default=str)}".encode()).hexdigest()
        if key not in self._d:
            val = fn()
            with self._lock:
                self._d[key] = val
                if self.path:
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"key": key, "value": val}, ensure_ascii=False) + "\n")
        return self._d[key]

    def holding(self, s, items, as_of=None, rubric=None):
        return self._wrap("h", evidence_payload(s, items, as_of, rubric), lambda: self.inner.holding(s, items, as_of, rubric))

    def portfolio(self, facts):
        return self._wrap("p", facts, lambda: self.inner.portfolio(facts))
