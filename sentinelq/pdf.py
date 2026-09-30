"""Render the IC scorecard PDF: how-to-read, rubric, summary table, observations, per-holding appendix."""
from __future__ import annotations
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

NAVY, GREY = colors.HexColor("#1F3864"), colors.HexColor("#F2F4F8")
LABEL_BG = {"Clean": colors.HexColor("#DFF2E1"), "Watch": colors.HexColor("#FFF1C7"), "Flag": colors.HexColor("#F8CFCF")}

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], alignment=TA_LEFT, fontSize=20, textColor=NAVY, spaceAfter=2)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=10, textColor=colors.HexColor("#555555"), spaceAfter=10)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=NAVY, fontSize=13, spaceBefore=8, spaceAfter=4)
H3 = ParagraphStyle("H3", parent=ss["Heading3"], textColor=NAVY, fontSize=10.5, spaceBefore=6, spaceAfter=2)
B = ParagraphStyle("B", parent=ss["Normal"], fontSize=9, leading=12)
SM = ParagraphStyle("SM", parent=B, fontSize=7.5, leading=9.5)
CELL = ParagraphStyle("CELL", parent=B, fontSize=7.8, leading=9.5)
CELLB = ParagraphStyle("CELLB", parent=CELL, fontName="Helvetica-Bold")
FOOT = ParagraphStyle("FOOT", parent=SM, textColor=colors.HexColor("#666666"), spaceBefore=10)


def P(t, st=B):
    return Paragraph(escape(str(t)).replace("\n", "<br/>"), st)


def sent_txt(x):
    return "n/a" if x is None else "0" if x == 0 else f"{x:+d}".replace("-", "−")


def build_pdf(path: Path, res: dict, rubric, narr: dict, observations: list[dict], meta: dict) -> None:
    title, as_of = meta["title"], date.fromisoformat(res["run"]["as_of"])
    foot = f"{title} Signal Scorecard  ·  Prototype  ·  {as_of.day} {as_of.strftime('%B %Y')}"

    def deco(c, doc):
        c.saveState()
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(doc.leftMargin, 8 * mm, foot)
        c.drawRightString(doc.pagesize[0] - doc.rightMargin, 8 * mm, f"Page {doc.page}")
        c.restoreState()

    ps = landscape(A4)
    doc = BaseDocTemplate(str(path), pagesize=ps, leftMargin=14 * mm, rightMargin=14 * mm,
                          topMargin=13 * mm, bottomMargin=16 * mm, title=f"{title} Signal Scorecard")
    W = ps[0] - 28 * mm
    doc.addPageTemplates([PageTemplate("p", frames=[Frame(doc.leftMargin, doc.bottomMargin, W, ps[1] - 29 * mm, id="f")], onPage=deco)])

    scores = sorted(res["scores"], key=lambda s: (-(s.company_sentiment if s.company_sentiment is not None else -9), -s.governance_score))
    kept, g = res["kept"], rubric["governance"]
    n = len(scores)
    st = []

    # ---- Page 1
    st += [P(title, H1), P("Signal Scorecard — News-derived sentiment, governance and corporate-action assessment", SUB)]
    info = [[P("Coverage", CELLB), P(meta.get("coverage") or f"{n} holdings", CELL), P("Research date", CELLB),
             P(f"{as_of.day} {as_of.strftime('%B %Y')}", CELL), P("Lookback", CELLB), P("~12 months", CELL),
             P("Status", CELLB), P("Prototype", CELL)]]
    t = Table(info, colWidths=[W * f for f in (.08, .2, .1, .12, .08, .12, .07, .23)])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREY), ("BOX", (0, 0), (-1, -1), .5, colors.lightgrey),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    st += [t, P("How to read this report", H2)]
    for h, body in [
        ("Sentiment score", "Sentiment is scored on a fixed −2 to +2 scale, anchored to a defined event taxonomy (earnings beats and misses, guidance changes, order wins, litigation, regulatory action, corporate actions). Each score is absolute — it reflects the stock on its own merits, not its rank within the portfolio — so a +2 means the same thing whether applied to a bank or a pharma name. News items are recency-weighted so recent developments carry more weight than older ones. Price movement and stock returns never feed the sentiment score."),
        ("Governance score", "Governance is scored on a 0–100 scale, starting at 100 with fixed penalties applied for specific event types (see rubric on next page). Scores translate to three labels — Clean (≥ 95), Watch (80–94), Flag (below 80). Flag names are highlighted in red on the summary table and warrant IC-level scrutiny before additional risk is added. The rubric is applied mechanically so the same event produces the same penalty across all holdings."),
        ("Corporate actions", "Listed factually — dividends, buybacks, splits, bonuses, rights, M&A, delistings, capex commitments. They inform context but do not feed into either the sentiment or the governance score.")]:
        st += [P(h, H3), P(body)]
    st += [Spacer(1, 6), P("Every score in this report can be traced back to specific, dated, sourced news in the appendix. Where retrieval coverage is thin, this is stated explicitly in the rationale.", B), PageBreak()]

    # ---- Page 2: rubric (rendered from the rubric file, so it can never drift from the arithmetic)
    st += [P("Scoring rubric", H1), P("Sentiment anchors", H2)]
    present = {s.company_sentiment for s in scores}
    rows = [[P("Score", CELLB), P("Meaning", CELLB)]]
    for sc, txt in rubric["sentiment_anchors"]:
        if sc == -2 and -2 not in present:
            txt += " Not present in this portfolio."
        rows.append([P(sent_txt(sc), CELLB), P(txt, CELL)])
    t = Table(rows, colWidths=[W * .08, W * .92])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREY), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st += [t, P("Governance penalties", H2)]
    rows = [[P("Event category", CELLB), P("Penalty", CELLB), P("Notes", CELLB)]]
    pen = g["penalties"]

    def pen_txt(k):
        v = pen.get(k)
        if k == "historical":
            return f"{g['historical_penalty']:g}"
        if isinstance(v, dict):
            vals = sorted(v.values())
            return f"{vals[0]:g} to {vals[-1]:g}"
        return f"{v:g}"
    for k, (cat, note) in g["notes"].items():
        rows.append([P(cat, CELL), P(pen_txt(k).replace("-", "−"), CELL), P(note, CELL)])
    t = Table(rows, colWidths=[W * .35, W * .12, W * .53])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREY), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st += [t, P("Governance labels", H2)]
    rows = [[P("Label", CELLB), P("Score band", CELLB), P("Interpretation", CELLB)]]
    for lab, (band, interp) in g["label_text"].items():
        rows.append([P(lab, CELLB), P(band, CELL), P(interp, CELL)])
    t = Table(rows, colWidths=[W * .12, W * .15, W * .73])
    ts = [("BACKGROUND", (0, 0), (-1, 0), GREY), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.lightgrey)]
    for i, lab in enumerate(g["label_text"], 1):
        ts.append(("BACKGROUND", (0, i), (0, i), LABEL_BG[lab]))
    t.setStyle(TableStyle(ts))
    st += [t, P(f"Rubric version {rubric.version} (sha256 {rubric.sha256[:12]}…). Sentiment half-life {rubric['sentiment']['half_life_trading_days']} trading days.", SM), PageBreak()]

    # ---- Scorecard table
    st += [P(f"Scorecard — all {n} holdings", H1), P("Sorted by sentiment score (high to low), then by governance score.", SUB)]
    hdr = ["Company", "Cap", "Sector", "Wt", "Sent.", "Gov.", "Label", "Key corporate action", "One-line read"]
    data = [[P(h, CELLB) for h in hdr]]
    style = [("BACKGROUND", (0, 0), (-1, 0), GREY), ("LINEBELOW", (0, 0), (-1, -1), .25, colors.lightgrey),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i, s in enumerate(scores, 1):
        nr = narr[s.symbol]
        lab = s.governance_label.upper() if s.governance_label == "Flag" else s.governance_label
        data.append([P(s.name, CELLB), P(s.cap, CELL), P(s.sector, CELL), P(s.weight, CELL), P(sent_txt(s.company_sentiment), CELLB),
                     P(f"{s.governance_score:g}", CELL), P(lab, CELLB), P(nr["key_corporate_action"], CELL), P(nr["one_line_read"], CELL)])
        style.append(("BACKGROUND", (6, i), (6, i), LABEL_BG[s.governance_label]))
        if s.governance_label == "Flag":
            style.append(("BACKGROUND", (0, i), (5, i), LABEL_BG["Flag"]))
    t = Table(data, repeatRows=1, colWidths=[W * f for f in (.14, .06, .1, .05, .05, .05, .06, .29, .20)])
    t.setStyle(TableStyle(style))
    st += [t, Spacer(1, 8), P("Portfolio-level observations", H2)]
    for o in observations:
        st += [KeepTogether([P(o["title"], H3), P(o["body"])])]
    st.append(PageBreak())

    # ---- Appendix
    st += [P("Appendix — Rationale by Holding", H1),
           P(f"For each of the {n} holdings: the sentiment case, then the governance scoring with the specific penalty logic applied. Order follows the summary table.", SUB)]
    for s in scores:
        nr = narr[s.symbol]
        block = [P(s.name, H2), P(f"Sentiment {sent_txt(s.company_sentiment)} · Governance {s.governance_score:g} ({s.governance_label.upper() if s.governance_label == 'Flag' else s.governance_label})", CELLB),
                 P("Sentiment rationale", H3), P(nr["sentiment_rationale"]), P("Governance rationale", H3), P(nr["governance_rationale"])]
        if nr.get("coverage_note"):
            block += [P("Coverage note", H3), P(nr["coverage_note"])]
        items = sorted(kept.get(s.symbol, []), key=lambda x: x.item.published, reverse=True)
        if items:
            rows = [[P(h, SM) for h in ("Date", "Headline", "Label", "Sent.", "Source")]]
            for li in items[:12]:
                rows.append([P(li.item.published, SM), P(li.item.title, SM),
                             P(li.label.event_type.replace("_", " ") + (" (hist.)" if li.label.historical else ""), SM),
                             P(sent_txt(li.label.sentiment), SM),
                             Paragraph(f'<link href="{escape(li.item.url)}" color="#0563C1">{escape(li.item.source or "link")}</link>', SM)])
            tt = Table(rows, colWidths=[W * f for f in (.09, .55, .16, .05, .15)], repeatRows=1)
            tt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREY), ("LINEBELOW", (0, 0), (-1, -1), .2, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            block += [P("Evidence trail", H3), tt]
        st += [KeepTogether(block[:4]), *block[4:], Spacer(1, 6)]
    st.append(P("This report is a prototype research aid. Sentiment and governance scores are built from retrieved, dated, linked news items only, using the fixed rubric shown on page 2. Direct BSE/NSE filings and Ministry-of-Corporate-Affairs registry integration is scheduled for the production build and will materially improve governance-signal coverage. This document is not investment advice; position weights and universe are as supplied in the portfolio file.", FOOT))
    doc.build(st)
