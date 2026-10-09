# -*- coding: utf-8 -*-
"""Generates the updated SentinelQ Scoring Pipeline reference PDF (v2.0.0), reusing the
house style already defined in sentinelq/pdf.py, with Bajaj Finance as the worked example
(real numbers from runs/2026-10-09_bajfinance)."""
import json
import sys

sys.path.insert(0, '.')
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                 PageBreak, HRFlowable, KeepTogether)
from reportlab.lib.enums import TA_LEFT

INK, MUTED, RULE, TINT = "#1a1a2e", "#5a6b9e", "#c9d1e5", "#f3f6fd"
BLUE, GREEN, AMBER, RED = "#2c3a70", "#2e7d5b", "#a97528", "#9b2f35"


def C(h):
    return colors.HexColor(h)


def S(name, **kw):
    tc = kw.pop("textColor", INK)
    tc = C(tc) if isinstance(tc, str) else tc
    return ParagraphStyle(name, fontName=kw.pop("fontName", "Helvetica"), textColor=tc, alignment=TA_LEFT, **kw)
TITLE = S("title", fontName="Helvetica-Bold", fontSize=22, leading=27, spaceAfter=6)
SUBTITLE = S("subtitle", fontSize=10, textColor=MUTED, spaceAfter=18)
H1 = S("h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=BLUE, spaceBefore=22, spaceAfter=8)
H2 = S("h2", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=BLUE, spaceBefore=12, spaceAfter=4)
BODY = S("body", fontSize=9.3, leading=13.5, spaceAfter=7)
WHY = S("why", fontName="Helvetica-Oblique", fontSize=9, leading=13, textColor=MUTED, spaceAfter=9)
NOTE = S("note", fontSize=8.3, leading=11.5, textColor=MUTED, spaceAfter=6)
CODE = S("code", fontName="Courier", fontSize=8.3, leading=12, textColor=BLUE, backColor=C(TINT), spaceAfter=9, leftIndent=8, borderPadding=6)
HDR = S("hdr", fontName="Helvetica-Bold", fontSize=8.3, textColor=colors.white)
CELL = S("cell", fontSize=8.3, leading=11.5)
CELLB = S("cellb", fontName="Helvetica-Bold", fontSize=8.3, leading=11.5)
CHIP = S("chip", fontName="Courier-Bold", fontSize=8.3, textColor=BLUE)

W = A4[0] - 2 * 22 * mm


def P(t, style=BODY):
    return Paragraph(t, style)


def h1(t):
    return [P(t, H1), HRFlowable(width="100%", thickness=1, color=C(RULE), spaceAfter=10)]


def why(t):
    return P("<b>Why:</b> " + t, WHY)


def grid(extra=None):
    base = [("BACKGROUND", (0, 0), (-1, 0), C(BLUE)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.3),
            ("GRID", (0, 0), (-1, -1), 0.4, C(RULE)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C(TINT)])]
    return TableStyle(base + (extra or []))


def tbl(heads, rows, widths):
    data = [[P(h, HDR) for h in heads]] + [[P(str(c), CELL) if not isinstance(c, Paragraph) else c for c in r] for r in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(grid())
    return t


# ---- real Bajaj Finance data ----
run = json.load(open("runs/2026-10-09_bajfinance/run_meta.json", encoding="utf-8"))
sc = json.load(open("runs/2026-10-09_bajfinance/scores.json", encoding="utf-8"))[0]
cov = run["coverage"][0]
r = json.load(open("rubric/rubric_v1.json", encoding="utf-8"))
ca = r["corporate_actions"]
sent = r["sentiment"]

doc = SimpleDocTemplate("SentinelQ_Scoring_Pipeline_Reference.pdf", pagesize=A4,
                         topMargin=22 * mm, bottomMargin=20 * mm, leftMargin=22 * mm, rightMargin=22 * mm,
                         title="SentinelQ Scoring Pipeline - Complete Reference v2.0.0", author="SentinelQ")
st = []

# ============ Title ============
st += [P("SentinelQ Scoring Pipeline — Complete Reference", TITLE),
       P(f"v2.0.0 · {run['as_of']} · worked example: Bajaj Finance (BAJFINANCE), from the {run['as_of']} run", SUBTITLE)]

st += [P("Overview", H1),
       P("SentinelQ scores each stock three separate ways through six stages. Only one stage uses AI; everything else is "
         "deterministic arithmetic reading a fixed rubric file, so any number here can be recomputed by hand.", BODY),
       P("<b>What changed in v2.0.0:</b> Sentiment dropped its x50 scaling and now shows a Raw mean (plain average) and a "
         "Weighted mean (recency x confidence) side by side, both banded directly. Corporate Actions was rebuilt entirely on a "
         "severity x stage x timing x size model (replacing the old base x materiality clip), combined as a single 0-10 score — "
         "not two. An article can now feed Sentiment, Corporate Actions and Governance all at once, where before the search pass "
         "that found it gated which score(s) it could touch.", BODY)]

rows = [["1. Input", "Read the stock list, resolve name/sector/aliases", "No"],
        ["2. Ingest", "Fetch news + corporate actions, filter obvious junk, cluster duplicates into events", "No"],
        ["3. Classify", "Label each event: type, sentiment -2..+2, governance flag, and (new) Corporate-Action stage/size/timing", "Yes — the only AI step"],
        ["4. Validate", "Schema-check every label; retry once, then drop with a reason", "No"],
        ["5. Score", "Turn labels into Sentiment (raw+weighted), Governance, and Corporate Actions, via the rubric", "No"],
        ["6. Report", "Write the Scorecard, PDF, and supporting evidence sheets", "No"]]
st += [tbl(["Stage", "What happens", "AI involved?"], rows, [70, W - 70 - 110, 110]), Spacer(1, 10)]

# ============ Stage 1-2 (brief - mostly unchanged) ============
st += h1("Stage 1 — Input")
st += [P("Reads a 3-column list (symbol, company, sector); resolves sector/cap/aliases from a reference file. Unchanged in "
         "v2.0.0, with one fix: a company's own registered-name words now always feed the matching-token list, even when an "
         "alias list is also configured — previously an alias list silently replaced the name's own words, which could drop "
         "genuine articles before the model ever saw them.", BODY),
       why("every later search depends on knowing exactly which name/ticker/aliases identify the company.")]

st += h1("Stage 2 — Ingest")
st += [P("Three separate 12-month searches per stock — general news, a dedicated results search, a dedicated "
         "governance-keyword search — plus a structured corporate-actions feed. Unchanged in v2.0.0.", BODY),
       P("<b>The pre-AI filter bar is lighter in v2.0.0.</b> The title-match check is unchanged (a headline must still name the "
         "company). The templated-headline filter was trimmed: record-date/ex-dividend notices, “in focus”, “here's "
         "why”, “block/bulk deal”, “multibagger” and similar phrasing are no longer blind-filtered before "
         "the model reads them — a single-company block-trade notice or a dividend-date article can now reach the model instead "
         "of being discarded on a regex alone. What stays filtered: formats that are structurally multi-stock roundups "
         "(“Stocks to watch: X, Y, Z”, “Dividends: Last Day To Buy” listing several names) — these carry no "
         "single-company content by construction, so filtering them loses nothing.", BODY),
       why("a blanket regex can't tell a genuine single-company notice from a listicle that happens to share its wording; the "
           "model, now reliably good at judging substance, is a better filter for anything that's ambiguous. The clustering/dedupe "
           "step that merges duplicate coverage into one event is unchanged either way."),
       P("The per-day cap and the 40/25/20/15 window quotas are unchanged, and — as before — only apply when a run is explicitly "
         "started with <font name=\"Courier\">--budget</font> / <font name=\"Courier\">--window-quotas</font>. The Bajaj Finance "
         f"run below used neither: {cov['retrieved']} articles retrieved, all of them usable were sampled.", BODY)]

# ============ Stage 3 ============
st += h1("Stage 3 — Classify: the AI's rubric")
st += [P("The sentiment anchor table (-2..+2) is unchanged from the prior version. What's new in v2.0.0 is the taxonomy the "
         "model chooses from, and three optional fields it now fills for corporate-action events.", BODY)]
rows = [["+2", "Material, company-specific, hard-fact good news: growth ≥ ~20% or a clear beat, guidance raised, a large order/capacity add, a rating upgrade, a broker target raise ≥ ~15%, a clean regulatory chit"],
        ["+1", "Positive but modest/routine: single-digit growth, in-line quarter, small order, reiterated Buy, ordinary dividend"],
        ["0", "No value signal: passing mention, a list, price-only item, routine filing, neutral broker view"],
        ["-1", "Mildly negative: soft quarter, small miss, downgrade to neutral, modest fine/demand, unplanned non-CXO exit"],
        ["-2", "Material bad news: profit fall ≥ ~20% or a big miss, guidance cut, regulatory order/settlement, fraud/probe, auditor exit, CXO resignation"]]
st += [tbl(["Score", "Criteria"], rows, [36, W - 36]), Spacer(1, 8)]
st += [P("<b>Taxonomy expanded from 30 to 45 event types</b> (see Section 10 for the full list), adding the full Corporate "
         "Action Scoring Rules taxonomy: insolvency_admission, demerger, open_offer_takeover, rights_issue, pledge_invocation, "
         "special_dividend, lock_in_expiry, name_symbol_isin_change, fy_end_change, routine_agm_egm, admin_notice, noise_notice, "
         "index_inclusion, index_exclusion, exchange_surveillance.", BODY),
       P("<b>Three new optional fields</b> feed the Corporate Actions formula (Stage 5c): <font name=\"Courier\">ca_stage</font> "
         "(board_intimation / board_approved / approval_obtained / completed), <font name=\"Courier\">ca_size_pct</font> (dilution "
         "%, pledged % of promoter holding, or rights discount %, where stated), <font name=\"Courier\">ca_days_to_effective</font> "
         "(days from publish date to a stated future effective/record date). All optional — the formula has a sensible default "
         "for each when the text doesn't say.", BODY),
       why("the prompt revision that added these bumped PROMPT_VERSION to v3, so every article gets freshly labelled against "
           "the new schema rather than silently reusing a pre-v3 label that never had these fields.")]

st += [P("The four cage bars and Stage 4 — Validate are unchanged from the prior version: forced JSON schema, extraction only, "
         "a label cache standing in for true temperature-0, and full response logging; schema failures retry once then drop "
         "with a reason, and >10% label failures halts the run (<font name=\"Courier\">--allow-partial-labels</font> to override).", BODY)]

# ============ Stage 5a — Sentiment (rewritten) ============
st.append(PageBreak())
st += h1("Stage 5a — Sentiment scoring (rewritten in v2.0.0)")
st += [P("<b>No x50 scaling anywhere.</b> The prior version computed a -2..+2 mean, stretched it onto a -100..+100 “points” "
         "scale (mean x 50), and banded the points. v2.0.0 drops that step entirely: the mean is banded directly on the -2..+2 "
         "scale it's already on.", BODY),
       P("<b>Two means, not one.</b> The Weighted mean applies a recency weight and (optionally) a source-confidence weight, "
         "exactly as before. A new Raw mean sits beside it — the plain, unweighted average of the same counted articles, no "
         "recency, no confidence. Both are shown, both are banded the same way, and only the Weighted one is “the” "
         "headline Sentiment score.", BODY)]
st.append(P(f"weighted_mean = Σ(w_i × s_i) / Σ w_i          raw_mean = Σ s_i / n          "
            f"(w_i = recency weight; confidence is OFF by default, same as before)", CODE))
st += [P("Window weight (unchanged): age looked up against the same fixed boundaries as before.", BODY)]
rows = [[f"{lo}-{hi} days" if hi < 999 else f"{lo}+ days", f"W{i+1}", f"{wt:.2f}"] for i, ((hi, wt), lo) in
        enumerate(zip(sent["window_weights"], [0] + [h for h, _ in sent["window_weights"][:-1]]))]
st += [tbl(["Age", "Window", "Weight"], rows, [120, 60, 60]), Spacer(1, 6),
       why("fresh news dominates (W1 at full weight), but a genuinely bad year isn't erasable just because the bad news "
           "happened early — W4 still carries a quarter of full weight, never zero.")]

st += [P("<b>Band cutoffs (new) — both means use the same bands:</b>", H2)]
bc = sent["band_cutoffs"]
rows = [["+2", f"≥ {bc['plus2_at']:g}"], ["+1", f"{bc['plus1_at']:g} to {bc['plus2_at']-0.01:g}"],
        ["0", f"{bc['minus1_at']+0.01:g} to {bc['plus1_at']-0.01:g}"],
        ["-1", f"{bc['minus2_at']+0.01:g} to {bc['minus1_at']:g}"], ["-2", f"≤ {bc['minus2_at']:g}"]]
st += [tbl(["Band", "Mean on the -2..+2 scale"], rows, [60, W - 60]), Spacer(1, 6),
       why("reaching +2/-2 now needs real consensus (≥ 1.6, not a bare round-to-nearest) — a mean of 1.2 stays +1. This "
           "replaced the old points-based bands (≥50 pts → +2) when the x50 step was removed; the ratio is the same "
           "shape, just expressed directly on the native scale.")]

st += [P("<b>Which articles count — unchanged in spirit, widened in scope.</b> The exclusion reasons from the prior version "
         "(price-only items, passing mentions, a zero-sentiment label on a fixed administrative-type list) are all unchanged. "
         "What changed: in the prior version, only articles from the dedicated sentiment-sample search could feed the mean — "
         "an article found purely by the governance-keyword search was walled off from sentiment even if it described a real, "
         "sentiment-relevant event. v2.0.0 removes that wall: any news article with a real event about the company can feed "
         "the mean, regardless of which search found it.", BODY),
       why("an auditor resignation found only by the governance search is still genuine evidence of how the year went — "
           "it shouldn't be invisible to sentiment just because of which search happened to surface it. This is the same "
           "change that lets one article feed Sentiment, Corporate Actions and Governance all at once.")]

st += [P("<b>Worked example — Bajaj Finance, this run</b>", H2)]
rows = [["Weighted mean", f"{sc['sentiment_weighted_mean']:+.3f}", f"{sc['company_sentiment']:+d}"],
        ["Raw mean", f"{sc['sentiment_raw_mean']:+.3f}", f"{sc['sentiment_raw_score']:+d}"]]
st += [tbl(["", "Decimal mean", "Banded score"], rows, [120, 100, 100]),
       P(f"{cov['relevant_articles']} relevant articles counted, evidence span {cov['span_days']} days of the 12-month lookback. "
         f"Both means land in the same +1 band here ({bc['plus1_at']:g} to {bc['plus2_at']-0.01:g}) — when they diverge more, "
         "it's a signal that recent news is telling a different story than the full-year average.", NOTE)]

# ============ Stage 5b — Governance ============
st.append(PageBreak())
st += h1("Stage 5b — Governance scoring")
st += [P("Starts at 100. Every governance-flagged item passes the same verification gate as before (subject, "
         "occurred-at-company, severity, action-stage) before it can cost anything. Unchanged structurally in v2.0.0; two "
         "additions to the penalty table, both drawn from the new Corporate Actions taxonomy so the same event can be "
         "penalised under Governance and scored under Corporate Actions from the one article:", BODY)]
g = r["governance"]
rows = [["pledge_invocation", "-20", "Forced selling into the market — the classic mid/small-cap stress spiral"],
        ["pledge (creation)", "0 / -5 / -10", "Penalised only at medium/high materiality — pledging itself isn't necessarily adverse"],
        ["insolvency_admission", "-25", "Can go to near zero and may not be exitable"]]
st += [tbl(["Event", "Penalty", "Why"], rows, [110, 70, W - 180])]
st += [P(f"Labels: Clean ≥{g['labels'][0]['min']:g} · Watch ≥{g['labels'][1]['min']:g} · Flag below. "
         f"Age taper: full penalty for {g['age_taper']['full_days']} days, fading linearly to "
         f"{g['age_taper']['end_factor']*100:.0f}% by {g['age_taper']['end_days']} days. Both unchanged.", BODY)]

st += [P("<b>Worked example — Bajaj Finance, this run</b>", H2)]
st += [P(f"{len(sc['governance_ignored'])} items flagged, 0 penalised → governance stays at "
         f"{sc['governance_score']:g}, {sc['governance_label']}.", BODY)]
rows = [[g_['date'], g_['headline'][:58], g_['event_type'].replace('_', ' ')] for g_ in sc['governance_ignored'][:8]]
st += [tbl(["Date", "Headline", "Event type"], rows, [55, W - 55 - 110, 110])]

# ============ Stage 5c — Corporate Actions (full rewrite) ============
st.append(PageBreak())
st += h1("Stage 5c — Corporate Actions scoring (rebuilt in v2.0.0)")
st += [P("The prior version scored each corporate action on a fixed base value (dividend +1.0, buyback +1.5, split/bonus +0.5, "
         "dilution -1.5...) x a materiality multiplier, summed and clipped to -2..+2. v2.0.0 replaces this entirely with the "
         "severity x stage x timing x size model from the Corporate Action Scoring Rules document (8-Oct-2026), covering the "
         "full 20-type severity table rather than 5 types.", BODY),
       P(f"<b>{ca['model']}</b>", NOTE)]

st += [P("Step 1 — Severity (the event type's ceiling, 0-10)", H2)]
sev_rows = sorted(ca["severity"].items(), key=lambda x: -x[1])
rows = [[str(sev), et.replace("_", " "), ca["damage_type"][et]] for et, sev in sev_rows]
st += [tbl(["Severity", "Event type", "Damage"], rows, [60, 180, W - 240])]
st += [P("<b>Damage:</b> <i>Economic</i> = the company is genuinely worth less (or more) — real value or real risk changed. "
         "<i>Data</i> = nothing about value changed, but price/fundamentals data breaks if the adjustment factor isn't applied "
         "everywhere at once (a 1:4 bonus isn't a loss, but an unadjusted price series shows an 80% crash that never happened). "
         "<i>Both</i> = carries real economic consequence AND will corrupt unadjusted data (a demerger: the parent really is "
         "worth less, and the price history shows a cliff if not adjusted).", NOTE)]

st += [P("Step 2 — Stage multiplier (how certain is it)", H2)]
sm = ca["stage_multiplier"]
rows = [["board_intimation", f"{sm['board_intimation']:g}", "Board informed / just announced"],
        ["board_approved", f"{sm['board_approved']:g}", "Board has approved it"],
        ["approval_obtained", f"{sm['approval_obtained']:g}", "Shareholder or regulatory approval obtained"],
        ["completed", f"{sm['completed']:g}", "Record date fixed or already happened"]]
st += [tbl(["ca_stage", "Multiplier", "Meaning"], rows, [110, 70, W - 180]),
       P(f"If the label doesn't state a stage, it defaults to <font name=\"Courier\">{sm['_default_when_unstated']}</font> — "
         "a deliberate middle value.", NOTE)]

st += [P("Step 3 — Timing multiplier (how close or how stale)", H2)]
st += [P("<b>Upcoming</b> (the label states a future effective/record date): banded by how far out.", BODY)]
rows = [["< 15 days", f"{ca['timing']['upcoming_under_15d']:g}"], ["15-60 days", f"{ca['timing']['upcoming_15_to_60d']:g}"],
        ["> 60 days", f"{ca['timing']['upcoming_over_60d']:g}"]]
st += [tbl(["Days to effective", "Multiplier"], rows, [120, 100])]
st += [P("<b>Already happened:</b> decays as 0.5 ^ (days_old / half_life) — by definition, at exactly one half-life the "
         "multiplier is 0.5; it approaches zero but never reaches it.", BODY)]
hl = ca["timing"]["past_half_life_days"]
rows = [["30 days", "dividend, buyback, special_dividend", "A one-day cash event — a month later it's genuinely old news"],
        ["90 days", "merger_acquisition, demerger, open_offer_takeover (and the default for every other type)", "Matters longer, decays slower"],
        ["180 days", "pledge, pledge_invocation, auditor_resignation, management_exit", "Describes an ongoing state, not a single-day event — stays live longer"]]
st += [tbl(["Half-life", "Event types", "Why"], rows, [55, 165, W - 220])]

st += [P("Step 4 — Size multiplier (how big is this instance)", H2)]
rows = [[f"≤ {cut:g}%", f"{m:g}"] for cut, m in ca["size_bands"]]
st += [tbl(["ca_size_pct", "Multiplier"], rows, [100, 100]),
       P(f"Applies to dilution %, pledged % of promoter holding, or rights discount %, where the label states one. If not, "
         f"materiality stands in: low → {ca['size_fallback_from_materiality']['low']:g}, medium → "
         f"{ca['size_fallback_from_materiality']['medium']:g}, high → {ca['size_fallback_from_materiality']['high']:g}. "
         f"If neither applies to the event type (an admin notice, an auditor exit), the multiplier is "
         f"{ca['size_not_applicable_multiplier']:g} — no scaling, not a penalty for missing data.", NOTE)]

st += [P("Step 5 — Multiply, cap at 10", H2),
       P("event_score = min(10, severity × stage × timing × size). The cap exists because the raw product can "
         "overshoot (e.g. insolvency_admission 10 × completed 1.0 × fresh 1.0 × largest size 1.5 = 15) — once "
         "the arithmetic says “already maximal”, a larger number adds no information, so it's clamped to the scale's "
         "own ceiling.", BODY)]

st += [P("Step 6 — Direction (tracked separately, never multiplied in)", H2),
       P(f"{ca['direction_from_sentiment']}", BODY),
       why("the same real event can be read two ways by two different articles — e.g. a capital raise is dilutive (negative "
           "for existing holders) and promoter-backed (positive, signals confidence) at once. The severity x stage x timing x "
           "size arithmetic only answers “how big a deal, mechanically”; direction is a separate judgement applied "
           "after, to sort the event into the Risk or Opportunity pool.")]

st += [P("Step 7 — Combine: one score, not an average or a sum", H2),
       P("Risk pools every negative + unclear-direction event score; Opportunity pools every positive one. Each combines as:", BODY),
       P("score = 10 × [ 1 − ∏ (1 − event_score_i / 10) ]          — “the chance at least one bites”", CODE),
       P("Summing would let unrelated minor events stack past any sensible ceiling. Averaging would let weak events dilute a "
         "serious one. Treating each score as a probability out of 10 avoids both: one dominant event drives the number, "
         "minor ones nudge it up with diminishing effect, and it can never exceed 10 no matter how many events pile on.", BODY)]

st += [P("<b>The Corporate Actions score shown everywhere is the Risk pool's result</b> — not a Risk/Opportunity pair. "
         "Opportunity is still computed in full (from the positive-direction pool, same formula) but only ever surfaces as a "
         f"separate “event-trade review” flag when it crosses {ca['opportunity_review_at']:g}, and it never changes "
         "the score or band above it.", BODY),
       why("the rules document is explicit: “good news never cancels bad news”, and “when both scores are high, "
           "the Risk band governs.” A strong Opportunity reading is worth a look for a different kind of review (tender "
           "into a buyback, hold through an open offer) — it is never a reason to downgrade the Corporate Actions score.")]

st += [P("Step 8 — The two gates", H2)]
rows = [["Gate A — hard override", "insolvency_admission, pledge_invocation; auditor_resignation only when the label's "
        "severity is ‘integrity’ (withheld-information/irregularity, not a routine explained exit)",
        "Forces the Exit band regardless of the arithmetic — no weighted score should argue someone out of exiting a company "
        "that just admitted insolvency."],
        ["Gate B — data quarantine", ", ".join(e.replace("_", " ") for e in ca["gate_b_data_quarantine"]["event_types"]),
        "Excluded entirely before the combine step — these carry no real economic risk; their only effect is corrupting "
        "unadjusted price/fundamentals data, which the rules doc's adjustment-factor checks handle separately."]]
st += [tbl(["Gate", "Triggers on", "Effect"], rows, [90, 160, W - 250])]
st += [P(f"A third bucket, <b>tracked but not scored</b>: {', '.join(e.replace('_',' ') for e in ca['disclosure_tracked_not_scored']['event_types'])} "
         "— kept on the calendar and shown, but excluded from the arithmetic entirely; the rules doc treats these (and results "
         "announcements, already scored under Sentiment) as disclosure events, not formal corporate actions.", NOTE)]

st += [P("Step 9 — Band", H2)]
rows = [[f"{lo:g}–{hi:g}", lab] for lo, hi, lab in ca["bands"]]
st += [tbl(["Score", "Band"], rows, [80, 100])]

st += [P("Step 0 (applies throughout) — Dedupe", H2),
       P(f"Repeat headlines of one declaration are ONE action: dividends within {ca['dedupe']['dividend_days']} days, "
         f"everything else within {ca['dedupe']['other_days']} days, collapse to a single event before scoring. This is "
         "distinct from the ingest-stage clustering in Stage 2 — this dedupe runs specifically on labelled corporate-action "
         "events, catching cases the earlier clustering didn't fully merge.", BODY)]

# ---- Worked example: every Bajaj Finance CA event, full arithmetic ----
st.append(PageBreak())
st += [P("Worked example — Bajaj Finance, every Corporate Actions event this run", H2)]
all_ev = sorted(sc["ca_events"], key=lambda e: -e["score"])
rows = []
for e in all_ev:
    rows.append([e["event_type"].replace("_", " "), str(e["severity"]), f"{e['stage']} ({e['stage_mult']:g})",
                f"{e['timing_mult']:g}\n({e['timing_basis']})", f"{e['size_mult']:g}", f"{e['score']:.2f}", e["direction"]])
st += [tbl(["Event", "Sev.", "Stage (x)", "Timing (x)", "Size (x)", "Score", "Direction"], rows,
           [78, 32, 88, 100, 42, 42, 60])]
neg = [e["score"] for e in all_ev if e["direction"] != "positive"]
pos = [e["score"] for e in all_ev if e["direction"] == "positive"]
prod_n = 1.0
for x in neg:
    prod_n *= (1 - x / 10)
prod_p = 1.0
for x in pos:
    prod_p *= (1 - x / 10)
st += [P(f"Risk pool {neg}: 1 − {' × '.join(f'(1-{x:.2f}/10)' for x in neg)} = 1 − {prod_n:.3f} = "
         f"{1-prod_n:.3f} × 10 = <b>{sc['ca_risk_score']:.2f}</b> → {sc['ca_risk_band']}", BODY),
       P(f"Opportunity pool {pos}: 1 − {' × '.join(f'(1-{x:.2f}/10)' for x in pos)} = 1 − {prod_p:.3f} = "
         f"{1-prod_p:.3f} × 10 = {sc['ca_opportunity_score']:.2f}"
         + (f" ≥ {ca['opportunity_review_at']:g} → event-trade review triggered" if sc["ca_opportunity_review"]
            else f" < {ca['opportunity_review_at']:g} → no review flag"), BODY)]
if sc["ca_gate_b"]:
    gb = sc["ca_gate_b"][0]
    st += [P(f"Gate B quarantined: <b>{gb['event_type'].replace('_',' ')}</b> ({gb['date']}, score {gb['score']:.2f}) — excluded "
             "from the pools above entirely, contributes nothing to either number.", BODY)]
if sc["ca_gate_a"]:
    st += [P(f"Gate A fired: {', '.join(e['event_type'] for e in sc['ca_gate_a'])} — the arithmetic above is overridden; "
             "band is Exit regardless.", BODY)]
else:
    st += [P("Gate A: did not fire this run — nothing here was insolvency, pledge invocation, or a withheld-information "
             "auditor exit.", NOTE)]

# ============ Stage 6 — Report ============
st.append(PageBreak())
st += h1("Stage 6 — Report")
st += [P("Writes the Scorecard, PDF, and supporting Excel sheets — two new sheets and two new Scorecard columns in v2.0.0.", BODY)]
rows = [["Read me", "Plain-English glossary of every column and band"],
        ["Scorecard", "Per-stock Sentiment (Weighted + Raw, each with a decimal-mean column alongside the banded score), "
         "Governance score/label, Corporate Actions score/band/event-trade-review flag"],
        ["Articles by window", "One row per article ever found — ‘How it was treated’ spells out the exact reason, scored or not"],
        ["Window summary", "Per-window counts (+2..-2), weighted push added, full-text-read count"],
        ["Evidence - Governance", "Every flagged item: penalty applied, or why not, with n_sources/tier/confidence"],
        ["Corporate Actions (new)", "One row per stock: score, band, event-trade-review flag, Gate A/B hits, counts"],
        ["Corporate Actions - Events (new)", "Every scored/gated/tracked event: full severity×stage×timing×size "
         "arithmetic, direction, and a Sourcing column (source, tier, source count, confidence) flagging any single "
         "lower-tier source in red — treat its date and facts as unconfirmed"],
        ["Coverage", "Retrieved/kept/dropped counts per stock, plus the full dropped-items list with reasons"],
        ["Run Info", "Rubric version/hash, as-of date, retrieval mode"]]
st += [tbl(["Sheet", "What's there"], rows, [130, W - 130])]
st += [why("every number above should be checkable by hand against a real row, not taken on faith — the new Sourcing column "
           "exists specifically so a weakly-sourced date or fact is visible, not silently presented as settled.")]

# ============ Rubric field reference (delta only) ============
st.append(PageBreak())
st += h1("rubric_v1.json — what changed (field reference)")
st += [P(f"Version <b>{r['version']}</b> (was 1.9.1). The file's own words: “Single source of truth for every weight, "
         "penalty and taxonomy entry. Scores change only if this file changes.”", BODY)]
st += [P("Sentiment block — new/changed fields", H2)]
rows = [["display_scale", sent.get("display_scale", ""), "No x50 anywhere; both means banded on -2..+2 directly"],
        ["band_cutoffs", f"plus2_at {bc['plus2_at']:g}, plus1_at {bc['plus1_at']:g}, minus1_at {bc['minus1_at']:g}, minus2_at {bc['minus2_at']:g}", "The live banding rule"],
        ["points_scale, bands (old)", "50 / {plus2:50, plus1:15}", "Dormant — kept only for any code path still reading sentiment_points"]]
st += [tbl(["Field", "Value", "Note"], rows, [110, 160, W - 270])]

st += [P("Corporate actions block — entirely new structure", H2),
       P("Replaces: clip [-2,2], base {5 types}, materiality_multiplier. Now: severity {20 types}, damage_type, "
         "stage_multiplier, timing{upcoming bands, past_half_life_days}, size_bands, size_fallback_from_materiality, "
         "direction_from_sentiment, gate_a_hard_exit, gate_b_data_quarantine, bands {5 bands}, opportunity_review_at, "
         "disclosure_tracked_not_scored. Full values given stage by stage above (Stage 5c). dedupe is unchanged "
         f"(dividend {ca['dedupe']['dividend_days']}d / other {ca['dedupe']['other_days']}d).", BODY)]

st += [P("event_types — 45 (was 30)", H2),
       P("All 30 prior types retained; 15 added: insolvency_admission, demerger, open_offer_takeover, rights_issue, "
         "pledge_invocation, special_dividend, lock_in_expiry, name_symbol_isin_change, fy_end_change, routine_agm_egm, "
         "admin_notice, noise_notice, index_inclusion, index_exclusion, exchange_surveillance.", BODY)]

st += [P("Governance block", H2),
       P("Unchanged except the three new penalty entries in Stage 5b above (pledge, pledge_invocation, insolvency_admission).", BODY)]

st += h1("config/__init__.py — what changed")
rows = [["BOILERPLATE_PATTERNS", "~14 patterns (was 31)", "Trimmed to formats mechanically certain to carry no company event "
        "(live price trackers, 52-week markers, market-wide recaps, pure PR filler) or structurally multi-stock (listicles, "
        "dividend-date roundups). See Stage 2 for the full before/after reasoning."],
        ["NOISE_ZERO_TYPES", "unchanged", "{other, management_change_routine, board_change_routine, dilution, rpt_routine} — "
        "still excluded from the sentiment mean when labelled 0, regardless of which pass found them"],
        ["PER_DAY_CAP, SAMPLE_WINDOWS", "unchanged (6/day; 40/25/20/15)", "Still only enforced with --budget / --window-quotas"]]
st += [tbl(["Field", "Value", "Note"], rows, [120, 110, W - 230])]

st += h1("Multi-label articles — the architectural change underlying all of the above")
st += [P("Before v2.0.0, an article's eligibility for Sentiment vs Governance was gated by <font name=\"Courier\">item.purpose"
         "</font> — set once, before the AI ever ran, based on which search pass retrieved it. A governance-pass article "
         "could only ever affect the governance score, even if it was obviously also sentiment-relevant news.", BODY),
       P("v2.0.0 decouples this. Scoring eligibility for each of the three scores is now computed independently, from the "
         "article's own label content (event type, substance, sentiment, governance_flag) — never from which search found it. "
         "The practical effect: one article — say, an auditor resignation — can now feed Sentiment (it's bad news), Corporate "
         "Actions (auditor_resignation has severity 6), and Governance (a fixed -20 penalty) all from a single read.", BODY)]

doc.build(st)
print("PDF written")

