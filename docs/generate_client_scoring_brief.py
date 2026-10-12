# -*- coding: utf-8 -*-
"""Generates SentinelQ_Scoring_Mechanism_Brief.pdf - a strict 3-page, client-presentable explainer of HOW
SentinelQ's three scores are produced, with flowcharts. Audience: clients and senior leadership in a live
presentation, not an internal build/investment decision (see generate_executive_brief.py for that one).
No stock-specific example; methodology only, kept deliberately short."""
import json
import sys

sys.path.insert(0, '.')
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon

NAVY, INK, MUTED, RULE, TINT = "#1a2452", "#1a1a2e", "#5a6b9e", "#c9d1e5", "#f3f6fd"
BLUE, GREEN, GREEN_FILL, AMBER, AMBER_FILL, RED, RED_FILL = (
    "#2c3a70", "#2e7d5b", "#e4f1ea", "#a97528", "#fbf1de", "#9b2f35", "#f7e4e5")


def C(h):
    return colors.HexColor(h)


def S(name, **kw):
    tc = kw.pop("textColor", INK)
    tc = C(tc) if isinstance(tc, str) else tc
    return ParagraphStyle(name, fontName=kw.pop("fontName", "Helvetica"), textColor=tc, alignment=kw.pop("alignment", TA_LEFT), **kw)


TITLE = S("title", fontName="Helvetica-Bold", fontSize=21, leading=25, textColor=NAVY, spaceAfter=3)
SUBTITLE = S("subtitle", fontSize=10.3, textColor=MUTED, spaceAfter=12)
H1 = S("h1", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=BLUE, spaceBefore=11, spaceAfter=5)
H2 = S("h2", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=NAVY, spaceBefore=6, spaceAfter=2)
BODY = S("body", fontSize=9.3, leading=13, spaceAfter=5)
LEAD = S("lead", fontSize=10, leading=14.5, spaceAfter=8, textColor=INK)
NOTE = S("note", fontSize=8.2, leading=11, textColor=MUTED, spaceAfter=4)
HDR = S("hdr", fontName="Helvetica-Bold", fontSize=8.3, textColor=colors.white, alignment=TA_CENTER)
CELL = S("cell", fontSize=8.5, leading=11.5)
CELLC = S("cellc", fontSize=8.5, leading=11.5, alignment=TA_CENTER)
CELLBC = S("cellbc", fontName="Helvetica-Bold", fontSize=8.5, leading=11.5, alignment=TA_CENTER)
CAPTION = S("caption", fontName="Helvetica-Oblique", fontSize=7.8, leading=10, textColor=MUTED, alignment=TA_CENTER, spaceBefore=2, spaceAfter=8)

W = A4[0] - 2 * 16 * mm


def P(t, style=BODY):
    return Paragraph(t, style)


def h1(t):
    return [P(t, H1), HRFlowable(width="100%", thickness=1, color=C(RULE), spaceAfter=6)]


def grid(extra=None):
    base = [("BACKGROUND", (0, 0), (-1, 0), C(BLUE)), ("GRID", (0, 0), (-1, -1), 0.4, C(RULE)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C(TINT)])]
    return TableStyle(base + (extra or []))


def tbl(heads, rows, widths, extra=None):
    data = [[P(h, HDR) for h in heads]] + [[c if isinstance(c, Paragraph) else P(str(c), CELL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(grid(extra))
    return t


# ============ Flowchart helper ============
def flow_horizontal(steps, total_width, box_h=40, gap=16, fill=TINT, stroke=BLUE, font_size=8.2, sub_font_size=6.8):
    """steps: list of (main_lines, sub_label_or_None). Draws left-to-right boxes with arrows between them."""
    n = len(steps)
    box_w = (total_width - gap * (n - 1)) / n
    d = Drawing(total_width, box_h + 14)
    x = 0.0
    for i, (lines, sub) in enumerate(steps):
        d.add(Rect(x, 12, box_w, box_h, fillColor=C(fill), strokeColor=C(stroke), strokeWidth=1.1, rx=5, ry=5))
        n_lines = len(lines)
        line_h = font_size + 2
        start_y = 12 + box_h / 2 + (n_lines - 1) * line_h / 2
        for j, ln in enumerate(lines):
            d.add(String(x + box_w / 2, start_y - j * line_h, ln, fontName="Helvetica-Bold", fontSize=font_size,
                        fillColor=C(NAVY), textAnchor="middle"))
        if sub:
            d.add(String(x + box_w / 2, 16, sub, fontName="Helvetica", fontSize=sub_font_size,
                        fillColor=C(MUTED), textAnchor="middle"))
        d.add(String(x + box_w / 2, box_h + 20.5, str(i + 1), fontName="Helvetica-Bold", fontSize=7.5,
                    fillColor=C(stroke), textAnchor="middle"))
        if i < n - 1:
            ax1, ax2, midy = x + box_w, x + box_w + gap, 12 + box_h / 2
            d.add(Line(ax1 + 2, midy, ax2 - 5, midy, strokeColor=C(stroke), strokeWidth=1.3))
            d.add(Polygon(points=[ax2 - 5, midy - 3.2, ax2 - 5, midy + 3.2, ax2, midy],
                          fillColor=C(stroke), strokeColor=C(stroke)))
        x += box_w + gap
    return d


# ============ Load live rubric values (so this stays in sync with the actual scoring code) ============
r = json.load(open("rubric/rubric_v1.json", encoding="utf-8"))
n_types = len(r["event_types"])
ca = r["corporate_actions"]
cuts = r["sentiment"].get("band_cutoffs") or {"plus2_at": 1.6, "plus1_at": 0.5, "minus1_at": -0.5, "minus2_at": -1.6}

doc = SimpleDocTemplate("SentinelQ_Scoring_Mechanism_Brief.pdf", pagesize=A4,
                        topMargin=15 * mm, bottomMargin=13 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
                        title="SentinelQ - Scoring Mechanism", author="SentinelQ")
st = []

# ================= PAGE 1 =================
st += [P("SentinelQ", TITLE), P("How the scores are made - a quick guide to the methodology", SUBTITLE)]

st += [P(
    "For every stock, SentinelQ reads public news, disclosures and corporate-action filings and turns them into "
    "three independent, dated scores: the market narrative, governance risk, and corporate-action risk. Every "
    "score is produced by a fixed, published rule, traces back to specific sourced articles, and is reproducible - "
    "the same inputs always give the same output.", LEAD)]

st += h1("The mechanism, end to end")
st += [flow_horizontal([
        (["News,", "disclosures &", "filings"], "live, dated"),
        (["AI reads &", "classifies", "each item"], f"{n_types} event types"),
        (["Fixed rules", "turn labels", "into scores"], "no judgement calls"),
        (["Three", "independent", "scores"], "+ a traceable report"),
    ], W), P("One pipeline, three outputs - every stock goes through exactly the same mechanical process.", CAPTION)]

st += [P(
    "Only the second step uses AI, and only to read and classify - it assigns each article a fixed label "
    "(what kind of event, how positive or negative, whether it concerns governance) using rules set in advance. "
    "It never decides a score itself. Every step after that - combining labels into numbers, applying bands, "
    "writing the report - is ordinary deterministic software: same rule, same inputs, same output, every time.", BODY)]

st += [P(
    "This separation is what makes the output auditable rather than a black box: the only subjective step is "
    "narrowly scoped and fully logged, and every number downstream of it can be traced back to the specific, "
    "dated article that produced it.", NOTE)]

st.append(Spacer(1, 8))

# ================= The three scores (flows naturally onto page 2) =================
st += h1("The three scores")

st += [P("Sentiment", H2), P(
    f"A -2 to +2 read of the year's news, weighted so recent, well-corroborated articles count more than old or "
    f"thinly-sourced ones. Reported two ways side by side - a weighted mean and a plain (raw) mean - so the "
    f"committee can see whether recent news is driving the read. Share-price moves never feed this score; only "
    f"reported events do.", BODY)]
rows = [["+2", "Sustained, broad-based good news with no material offset."],
        ["+1", "Positive on balance, with one specific offset."],
        ["0", "Genuinely two-sided, or evidence judged insufficient to call."],
        ["−1", "A material negative dominates the period."],
        ["−2", "Severe or compounding bad news."]]
st += [tbl(["Band", "Reading"], rows, [38, W - 38], extra=[
        ("BACKGROUND", (0, 1), (0, 1), C(GREEN_FILL)), ("BACKGROUND", (0, 2), (0, 2), C(GREEN_FILL)),
        ("BACKGROUND", (0, 3), (0, 3), C(AMBER_FILL)), ("BACKGROUND", (0, 4), (0, 4), C(RED_FILL)),
        ("BACKGROUND", (0, 5), (0, 5), C(RED_FILL)), ("FONTNAME", (0, 1), (0, 5), "Helvetica-Bold"),
        ("ALIGN", (0, 1), (0, 5), "CENTER")]), Spacer(1, 4)]

st += [P("Governance", H2), P(
    "Starts every stock at 100 and deducts fixed, published points only for specific, verified adverse events - "
    "a regulator's order, an auditor's exit, an unplanned senior departure, a promoter's pledged shares being "
    "sold under stress. Routine events (a scheduled retirement, a resolved inquiry) are checked and explicitly "
    "not penalised, so the score reflects real risk, not noise.", BODY)]
rows = [["Clean", "≥ 95", "No material adverse event in the window."],
        ["Watch", "80–94", "Something disclosed that warrants monitoring."],
        ["Flag", "< 80", "A live or recent event needing attention before adding risk."]]
st += [tbl(["Label", "Band", "Meaning"], rows, [48, 48, W - 96], extra=[
        ("BACKGROUND", (0, 1), (0, 1), C(GREEN_FILL)), ("BACKGROUND", (0, 2), (0, 2), C(AMBER_FILL)),
        ("BACKGROUND", (0, 3), (0, 3), C(RED_FILL)), ("FONTNAME", (0, 1), (0, 3), "Helvetica-Bold")]), Spacer(1, 4)]

st += [P("Corporate Actions", H2), P(
    "A single 0–10 risk read on what is live right now in a stock's capital or ownership structure - a "
    "dilutive raise, a change of control, a promoter pledge. Each event is scored on four factors, combined so "
    "one serious event is never diluted by several minor ones:", BODY)]
st.append(Spacer(1, 10))
st += [flow_horizontal([
        (["Severity", "(how dangerous", "this event type is)"], None),
        (["Stage", "(how far along", "it is)"], None),
        (["Timing", "(how close", "or recent)"], None),
        (["Size", "(how large", "this instance is)"], None),
    ], W, box_h=34, fill="#eef1fb"), P("Combined into ONE risk score, 0–10 - not summed or averaged.", CAPTION)]
rows = [["0–2.9", "Log", "Nothing to flag."], ["3–4.9", "Watch", "Add to the watchlist."],
        ["5–6.9", "Review", "Pause new buying; review before the next rebalance."],
        ["7–8.9", "Act", "Trim, hedge, or document why the position is being held."],
        ["9–10", "Exit", "Exit by default; holding requires explicit sign-off."]]
st += [tbl(["Score", "Band", "Default action"], rows, [42, 48, W - 90], extra=[
        ("BACKGROUND", (1, 1), (1, 1), C(GREEN_FILL)), ("BACKGROUND", (1, 2), (1, 2), C(AMBER_FILL)),
        ("BACKGROUND", (1, 3), (1, 3), C(AMBER_FILL)), ("BACKGROUND", (1, 4), (1, 4), C(RED_FILL)),
        ("BACKGROUND", (1, 5), (1, 5), C(RED_FILL)), ("FONTNAME", (1, 1), (1, 5), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, 5), "CENTER")])]
st += [P(
    "A small number of severe events (insolvency, a forced pledge sale) override this arithmetic entirely and "
    "send the score straight to Exit - no combination of minor events can do that, and no combination of good "
    "news can offset it either.", NOTE)]

st.append(Spacer(1, 6))

# ================= Why the output can be trusted =================
st += h1("Why the output can be trusted")
rows = [
    ["Fully traceable", "Every score opens down to the specific, dated articles that produced it, each with its "
     "own link."],
    ["Deterministic", "No randomness and no drift - the same evidence always produces the same score, every time "
     "it is checked."],
    ["Source-strength shown", "A claim resting on a single weaker outlet is flagged as such, not shown with the "
     "same confidence as a well-corroborated one."],
    ["Nothing walled off", "One article can feed all three scores at once (e.g. an auditor exit is bad sentiment "
     "news, a corporate-action event, and a governance penalty, together) - nothing is double-counted or hidden."],
]
st += [tbl(["", ""], rows, [95, W - 95], extra=[("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                                                 ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                                                 ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white)])]
st.append(Spacer(1, 6))

st += h1("Reading a score at a glance")
st += [P(
    "Each holding carries all three scores side by side, colour-coded the same way across the portfolio, so a "
    "reviewer can scan dozens of names and immediately see where the real risk is concentrated - without reading "
    "every underlying article. A name that is Clean on governance and Log on corporate actions needs no further "
    "look; a Flag or an Exit is where attention belongs.", BODY)]
st.append(Spacer(1, 6))

ex_rows = [
    ["Example A", P("+2", CELLBC), P("100 Clean", CELLBC), P("0.4 Log", CELLBC), "No action needed."],
    ["Example B", P("+1", CELLBC), P("82 Watch", CELLBC), P("3.1 Watch", CELLBC), "Add to the watchlist."],
    ["Example C", P("0", CELLBC), P("95 Clean", CELLBC), P("6.8 Review", CELLBC), "Pause new buying; review before rebalance."],
    ["Example D", P("−1", CELLBC), P("70 Flag", CELLBC), P("9.4 Exit", CELLBC), "Exit by default."],
]
ex_fill = [GREEN_FILL, GREEN_FILL, GREEN_FILL, None,
           GREEN_FILL, AMBER_FILL, AMBER_FILL, None,
           AMBER_FILL, GREEN_FILL, AMBER_FILL, None,
           RED_FILL, RED_FILL, RED_FILL, None]
extra = [("BACKGROUND", (0, 0), (-1, 0), C(BLUE))]
for idx, col in enumerate((1, 2, 3)):
    for row in range(4):
        fill = ex_fill[row * 4 + col - 1]
        if fill:
            extra.append(("BACKGROUND", (col, row + 1), (col, row + 1), C(fill)))
st += [tbl(["Illustrative holding", "Sentiment", "Governance", "Corp. Actions", "Read"],
          ex_rows, [68, 48, 62, 62, W - 240], extra=extra),
      P("Illustrative only - not live portfolio data.", CAPTION)]

st.append(PageBreak())
st += h1("What this does not do")
rows = [
    ["Not a buy/sell signal", "It is a research and risk-monitoring read, not a return forecast - it tells you "
     "what has happened and how material it is, not what the price will do next."],
    ["Not a replacement for judgement", "It applies one fixed rule consistently and surfaces the evidence; a "
     "person still decides what to do about a Flag or an Exit."],
    ["Not a forced opinion", "Where public coverage of a stock is too thin to call a direction, the score says so "
     "explicitly rather than guessing."],
    ["Not a snapshot of price", "Share-price moves never feed any of the three scores - only reported events do; "
     "the scores describe the company, not how the stock has traded."],
]
st += [tbl(["", ""], rows, [120, W - 120], extra=[("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                                                   ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                                                   ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white)])]

st.append(Spacer(1, 10))
st.append(HRFlowable(width="100%", thickness=0.5, color=C(RULE), spaceAfter=6))
st += [P(
    "This is a brief for a client and leadership audience, summarising the methodology only. The full scoring "
    "rubric - every category, penalty and multiplier - follows as an appendix.",
    S("disc", fontSize=7.6, leading=10, textColor=MUTED))]

# ================= APPENDIX: full scoring rubric =================
st.append(PageBreak())
g = r["governance"]
st += [P("Appendix", S("apptitle", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=NAVY, spaceAfter=2)),
      P("The full scoring rubric - every category, penalty and multiplier referenced above", SUBTITLE)]

st += h1("Sentiment anchors")


def band_txt(sc):
    return f"+{sc}" if sc > 0 else "0" if sc == 0 else f"−{abs(sc)}"


rows = [[band_txt(sc), txt] for sc, txt in r["sentiment_anchors"]]
st += [tbl(["Score", "Definition"], rows, [40, W - 40], extra=[
        ("BACKGROUND", (0, 1), (0, 1), C(GREEN_FILL)), ("BACKGROUND", (0, 2), (0, 2), C(GREEN_FILL)),
        ("BACKGROUND", (0, 3), (0, 3), C(AMBER_FILL)), ("BACKGROUND", (0, 4), (0, 4), C(RED_FILL)),
        ("BACKGROUND", (0, 5), (0, 5), C(RED_FILL)), ("FONTNAME", (0, 1), (0, 5), "Helvetica-Bold"),
        ("ALIGN", (0, 1), (0, 5), "CENTER")]), Spacer(1, 8)]

st += h1("Governance - every penalised category")
st += [P(f"Starts at {g['start']}. Each category counted once per stock (the most severe instance); penalties older "
        f"than 6 months fade linearly to half by 12 months.", NOTE)]
rows = [[cat, g["display"][k], note] for k, (cat, note) in g["notes"].items()]
st += [tbl(["Category", "Penalty", "Notes"], rows, [128, 55, W - 183]), Spacer(1, 4)]
st += [P("Routine items (scheduled succession, a resolved/quashed matter, an approved arm's-length related-party "
        "transaction, a scheduled auditor rotation) are reviewed and never penalised.", NOTE)]

st.append(PageBreak())
ca = r["corporate_actions"]
st += h1("Corporate Actions - severity by event type")
st += [P("Each event's severity (below) is multiplied by stage, timing and size - see the three tables that follow - "
        "capped at 10.", NOTE)]
rows = [[str(sev), et.replace("_", " "), ca["damage_type"].get(et, "").capitalize()]
       for et, sev in sorted(ca["severity"].items(), key=lambda x: -x[1])]
st += [tbl(["Sev.", "Event type", "Damage"], rows, [36, W - 146, 110]), Spacer(1, 8)]

st += h1("...multiplied by three more factors")
sm = ca["stage_multiplier"]
st += [P("Stage", H2),
      tbl(["Stage", "Multiplier"], [[s.replace("_", " ").title(), f"x{v:g}"] for s, v in sm.items() if not s.startswith("_")],
          [W - 70, 70])]
st.append(Spacer(1, 6))
st += [P("Timing", H2), P(
    "Upcoming: >60 days out x0.5, 15-60 days x0.75, under 15 days x1.0. Already happened: decays by half every "
    "N days from publication (30-180 days depending on event type, e.g. 30 for a dividend, 180 for a pledge).", BODY)]
sb = ca["size_bands"]
size_txt = ", ".join(f"above {sb[i - 1][0]:g}% -> x{mult:g}" if cutoff >= 900 else f"up to {cutoff:g}% -> x{mult:g}"
                     for i, (cutoff, mult) in enumerate(sb))
st += [P("Size", H2), P(
    "From the stated size (dilution %, pledged % of promoter holding, rights discount %), or from materiality "
    "(low/medium/high) when no percentage is given: " + size_txt + ".", BODY)]
st.append(Spacer(1, 8))

rows = [
    ["Gate A - forced to Exit", "Insolvency admission; a pledge invocation; an auditor resignation citing withheld "
     "information. Overrides the arithmetic entirely, regardless of score."],
    ["Gate B - data quarantine", "Splits/bonuses, demergers, mergers/acquisitions, rights issues, special dividends, "
     "ISIN/symbol changes. Excluded from the Risk/Opportunity number (a structure change needs its own data check), "
     "but still flagged in the write-up, positive or negative."],
]
overrides_table = tbl(["", ""], rows, [120, W - 120], extra=[("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                                                              ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                                                              ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white)])
st.append(KeepTogether(h1("The two overrides") + [overrides_table]))

doc.build(st)
print("wrote SentinelQ_Scoring_Mechanism_Brief.pdf")
