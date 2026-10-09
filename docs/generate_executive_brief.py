# -*- coding: utf-8 -*-
"""Generates SentinelQ_Executive_Brief.pdf - a <=5 page, formal, non-technical brief for
leadership on what SentinelQ is, why it can be trusted, and what moving it from prototype
to production would take. No stock-specific example; methodology only."""
import json
import sys

sys.path.insert(0, '.')
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                 HRFlowable, KeepTogether)

INK, MUTED, RULE, TINT = "#1a1a2e", "#5a6b9e", "#c9d1e5", "#f3f6fd"
BLUE, GREEN, AMBER = "#2c3a70", "#2e7d5b", "#a97528"


def C(h):
    return colors.HexColor(h)


def S(name, **kw):
    tc = kw.pop("textColor", INK)
    tc = C(tc) if isinstance(tc, str) else tc
    return ParagraphStyle(name, fontName=kw.pop("fontName", "Helvetica"), textColor=tc, alignment=kw.pop("alignment", TA_LEFT), **kw)


TITLE = S("title", fontName="Helvetica-Bold", fontSize=20, leading=25, spaceAfter=4)
SUBTITLE = S("subtitle", fontSize=10.5, textColor=MUTED, spaceAfter=16)
H1 = S("h1", fontName="Helvetica-Bold", fontSize=13.5, leading=17, textColor=BLUE, spaceBefore=16, spaceAfter=7)
H2 = S("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=BLUE, spaceBefore=9, spaceAfter=3)
BODY = S("body", fontSize=9.6, leading=14.2, spaceAfter=7)
LEAD = S("lead", fontSize=10.5, leading=16, spaceAfter=10, textColor=INK)
NOTE = S("note", fontSize=8.6, leading=12, textColor=MUTED, spaceAfter=6)
HDR = S("hdr", fontName="Helvetica-Bold", fontSize=8.6, textColor=colors.white)
CELL = S("cell", fontSize=8.8, leading=12.5)
CELLB = S("cellb", fontName="Helvetica-Bold", fontSize=8.8, leading=12.5)
BULLET = S("bullet", fontSize=9.6, leading=14, leftIndent=12, spaceAfter=5, bulletIndent=0)

W = A4[0] - 2 * 20 * mm


def P(t, style=BODY):
    return Paragraph(t, style)


def h1(t):
    return [P(t, H1), HRFlowable(width="100%", thickness=1, color=C(RULE), spaceAfter=8)]


def grid(extra=None):
    base = [("BACKGROUND", (0, 0), (-1, 0), C(BLUE)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 8.8),
            ("GRID", (0, 0), (-1, -1), 0.4, C(RULE)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C(TINT)])]
    return TableStyle(base + (extra or []))


def tbl(heads, rows, widths):
    data = [[P(h, HDR) for h in heads]] + [[P(str(c), CELL) if not isinstance(c, Paragraph) else c for c in r] for r in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(grid())
    return t


r = json.load(open("rubric/rubric_v1.json", encoding="utf-8"))
n_types = len(r["event_types"])

doc = SimpleDocTemplate("SentinelQ_Executive_Brief.pdf", pagesize=A4,
                         topMargin=20 * mm, bottomMargin=16 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
                         title="SentinelQ - Executive Brief", author="SentinelQ")
st = []

# ============ Header ============
st += [P("SentinelQ", TITLE),
       P("An automated signal engine for portfolio monitoring — executive brief on methodology, reliability and production readiness", SUBTITLE)]

st += [P(
    "SentinelQ reads the public news, disclosures and corporate-action history of every stock in a portfolio and turns "
    "them into three independent, dated, source-linked scores: how the market narrative is trending, whether anything "
    "adverse has happened in governance, and how much risk is sitting in recent corporate actions. It is built to be "
    "checked, not trusted blindly — every number traces back to a specific article and a fixed, published rule, and "
    "the same inputs always produce the same output. This brief summarises what it does, why its output can be relied "
    "on, what has been built and tested so far, and what remains before it is a production system of record rather "
    "than a prototype.", LEAD)]

# ============ What it produces ============
st += h1("What it produces")
st += [P("For every stock in a portfolio, SentinelQ scores three separate dimensions, each on its own fixed scale:", BODY)]
rows = [
    ["Sentiment", "-2 to +2", "How the past year of reported news reads for the company — results, guidance, orders, "
     "analyst views — weighted so recent news counts more than old news. Price moves never feed this score; only "
     "reported, dated events do."],
    ["Governance", "0 to 100", "Starts clean and loses fixed points only for specific, verified adverse events "
     "(a regulator's order, an auditor's exit, an unplanned senior departure, a promoter pledge being called). "
     "Routine events — a scheduled retirement, a resolved inquiry — are reviewed and explicitly not penalised."],
    ["Corporate Actions", "0 to 10", "How much risk is concentrated in a stock's recent capital, ownership or "
     "structural activity — a dilutive raise, a change of control, a promoter pledge — combined so one serious event "
     "is never diluted by several minor ones."],
]
st += [tbl(["Dimension", "Scale", "What it captures"], rows, [85, 50, W - 135]), Spacer(1, 4)]
st += [P("A fourth, read-only layer — dividends, buybacks, splits and similar mechanical actions — is listed for "
         "context but deliberately kept out of all three scores: these do not change what a company is worth, and "
         "including them would distort the signal rather than inform it.", NOTE)]

# ============ How it works ============
st += h1("How it works")
st += [P(
    "The system follows a fixed, six-stage pipeline. Only one stage uses AI, and its role is deliberately narrow: it "
    "reads one news article at a time and assigns it a label from a closed, pre-defined list — an event type, a "
    "sentiment value on a fixed scale, and whether it concerns governance. It never computes a score, never sees the "
    "scoring rules, and never aggregates anything across articles. Every other stage — retrieving the news, filtering "
    "out noise, turning labels into scores, writing the report — is ordinary, deterministic software with no AI "
    "involved and no randomness.", BODY),
    P(f"The labelling step recognises {n_types} distinct event types spanning results, governance, and the full range "
      "of formal corporate actions (capital raises, mergers, buybacks, pledges, insolvency and more), each mapped to "
      "a documented severity and treatment.", BODY)]

rows = [["1. Input", "Resolve the exact company identity (name, ticker, sector) so every later search is unambiguous."],
        ["2. Collect", "Retrieve news, results coverage and corporate-action history for the lookback period; discard "
         "templated and off-topic items before anything is read in depth; merge duplicate coverage of the same story "
         "into one event so repetition is never counted as repeated evidence."],
        ["3. Label", "The one AI step: read each distinct event and assign it a fixed-vocabulary label — nothing else."],
        ["4. Validate", "Check every label against the expected structure; anything malformed is retried once, then "
         "dropped with a recorded reason — never silently kept or silently discarded."],
        ["5. Score", "Deterministic arithmetic only. Turn labels into the three scores using fixed, published rules — "
         "no AI, no judgement calls, fully reproducible by hand from the same inputs."],
        ["6. Report", "Produce the scorecard and the full supporting evidence — every score traceable to the specific, "
         "dated, linked articles behind it."]]
st += [tbl(["Stage", "What happens"], rows, [62, W - 62])]

# ============ Why it can be trusted ============
st.append(KeepTogether(h1("Why the output can be trusted")))
rows = [
    ["Deterministic scoring", "The scoring rules live in one published file, separate from the AI. Given the same "
     "labelled evidence, the system produces the identical score every time — nothing is re-derived by a model at "
     "report time, and nothing drifts between runs."],
    ["Full traceability", "Every score is backed by a visible list of the specific articles that drove it, each with "
     "its date, headline, individual assessment, and link. Nothing is a black-box average — an analyst can open any "
     "score and see exactly why it is what it is."],
    ["Source-strength transparency", "Each piece of evidence is weighted by how many independent outlets reported it "
     "and how reliable those outlets are, on a fixed, editable scale. A claim backed by a single low-reliability "
     "source is explicitly flagged as such in the output, rather than presented with the same confidence as a "
     "well-corroborated one."],
    ["Nothing proceeds unverified", "Adverse governance events pass a verification check before they can cost any "
     "points — confirming the event genuinely concerns the company itself, not a peer, a counterparty, or an "
     "industry-wide rule that happens to share a keyword. Items that fail this check are visibly logged as reviewed "
     "and excluded, not silently dropped."],
    ["Nothing silently fails", "An article that cannot be read, a label that doesn't parse, a stock that cannot be "
     "resolved — every one of these produces a recorded reason in the output, never a quiet omission. If more than a "
     "small fraction of a run fails, the whole run stops rather than publishing on unreliable foundations."],
]
for name, body in rows:
    st.append(P(f"<b>{name}.</b> {body}", BULLET))

# ============ What exists today ============
st += h1("What has been built and tested")
st += [P(
    "The current system is a working prototype, not a paper design: it runs end to end against live, public news "
    "sources and produces the full scorecard and supporting workbook for any named list of stocks today. The "
    "deterministic scoring logic — the part of the system that turns evidence into a number, and the part that "
    "matters most for consistency and auditability — is covered by an automated test suite of 166 checks that run "
    "before any change is accepted, each one pinned to a specific, hand-verified expected result. The system has "
    "been run and reviewed against real portfolios of varying size, with its output checked line by line against the "
    "underlying articles.", BODY)]

# ============ Path to production ============
st.append(KeepTogether(h1("What production readiness would add")))
st += [P("The prototype demonstrates the methodology works. Moving it to a system the firm relies on day to day "
         "would mean addressing the following, in roughly the order they matter:", BODY)]
rows = [
    ["Primary-source data", "Today's evidence is drawn from public news coverage. Direct integration with exchange "
     "filings and regulatory disclosures would reduce dependence on how well a given event happened to be reported, "
     "and is the single highest-value addition for governance and corporate-action coverage specifically."],
    ["Human sign-off workflow", "A defined point at which a person reviews and approves scores before they reach "
     "decision-makers — particularly for the lowest-scoring names and anything a verification check could not fully "
     "resolve on its own."],
    ["Scheduled, monitored operation", "Running on a fixed schedule with alerting if a run fails or produces an "
     "unexpected shift, rather than being run on request."],
    ["Coverage breadth and calibration", "Testing across a wider and more varied set of names than has been "
     "exercised so far, to confirm the scoring thresholds hold up across sectors and company sizes, with a feedback "
     "loop to tune them as real outcomes come in."],
    ["Access and distribution", "A defined way for the people who need these scores to receive them — a shared "
     "location, a scheduled distribution, or integration into an existing review process."],
]
st += [tbl(["Area", "What it would mean"], rows, [110, W - 110])]

# ============ Recommendation ============
st += h1("Recommendation")
st += [P(
    "The methodology is sound, deterministic, and fully auditable, and the prototype already produces usable output "
    "against live data. The most efficient next step is a short pilot against a defined, representative portfolio, "
    "with a named reviewer validating a sample of scores against source filings before anything from the system "
    "reaches a wider audience. That pilot would surface any remaining gaps directly, rather than theorising about "
    "them, and gives a concrete basis for deciding the investment in the production items above.", BODY)]

st += [Spacer(1, 10), HRFlowable(width="100%", thickness=0.5, color=C(RULE), spaceAfter=6),
       P("This is a summary for decision-making purposes. The full technical reference — every scoring rule, "
         "formula and worked example — is available on request.", NOTE)]

doc.build(st)
print("PDF written")
