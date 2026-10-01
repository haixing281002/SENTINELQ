"""Builds docs/NEWS_METHODOLOGY.pdf - a one-page description of how Sentinel Q gets its news. Run: python scripts/make_methodology_pdf.py"""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY, GREY, LIGHT = colors.HexColor("#1F3A5F"), colors.HexColor("#555555"), colors.HexColor("#EEF2F7")
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, leading=18)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=8, textColor=GREY, leading=10, spaceAfter=3)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9.5, textColor=NAVY, leading=12, spaceBefore=5, spaceAfter=1)
B = ParagraphStyle("b", fontName="Helvetica", fontSize=7.8, leading=10)
BL = ParagraphStyle("bl", parent=B, leftIndent=8, bulletIndent=0)
C = ParagraphStyle("c", parent=B, fontSize=7.2, leading=9)
CH = ParagraphStyle("ch", parent=C, fontName="Helvetica-Bold", textColor=colors.white)


def bullets(items):
    return [Paragraph(t, BL, bulletText="•") for t in items]


def build(path="docs/NEWS_METHODOLOGY.pdf"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    d = SimpleDocTemplate(path, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=10 * mm,
                          title="Sentinel Q - How we get the news", author="Sentinel Q")
    s = [Paragraph("How Sentinel Q gets its news", H1),
         Paragraph("Step 2 (Ingest) methodology &middot; free sources only &middot; nothing billed &middot; no API key &middot; articles read in memory, never saved", SUB)]

    s += [Paragraph("1. Principle", H2),
          Paragraph("For each stock we take the <b>latest 100 articles whose headline names the company</b>, newest first, looking back at most 12 months. "
                    "We keep scraping further back until we have 100 or the 12-month limit is reached. The model never searches, browses or recalls anything; it only labels what we hand it. "
                    "Every article keeps its URL and date, and anything dropped is listed with a reason.", B)]

    s += [Paragraph("2. Sources (free)", H2)]
    src = [[Paragraph(x, CH) for x in ("Source", "Used for", "Notes")],
           [Paragraph("<b>Google News RSS</b> (default, India edition)", C), Paragraph("Live news, latest-first", C),
            Paragraph("Query per company with <font face='Courier'>after:</font>/<font face='Courier'>before:</font> date operators. Index is live, so it is not a point-in-time archive.", C)],
           [Paragraph("<b>GDELT DOC 2.0</b>", C), Paragraph("Dated archive for past as-of dates", C),
            Paragraph("Free; each request only reaches ~3 months, so windows are rolled back. Chosen automatically (<font face='Courier'>--news auto</font>) when the as-of date is in the past.", C)],
           [Paragraph("<b>Verified events CSV</b>", C), Paragraph("Human-checked filings", C),
            Paragraph("Events a person confirmed against a filing are merged before scoring and flagged as verified.", C)]]
    t = Table(src, colWidths=[42 * mm, 38 * mm, 100 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C5CDD8")),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    s.append(t)

    s += [Paragraph("3. The rolling-window search (per stock)", H2)] + bullets([
        "<b>Query:</b> the company's name plus up to 3 aliases from <font face='Courier'>portfolio/universe.csv</font>, as an OR-query in quotes, e.g. <font face='Courier'>(\"Titan Company\" OR \"Titan\") after:2026-06-01 before:2026-07-04</font>.",
        "<b>Windows roll backwards, newest first:</b> last 30 days, then days 31-90, 91-180, 181-365. Each is a new, non-overlapping query; we stop as soon as 100 usable titles are in hand.",
        "<b>Usable = the headline names the company.</b> Loose hits (e.g. a ceiling-fan story for a similarly named firm) do not count towards the 100. Syndicated copies of the same headline are counted once.",
        "<b>Selection:</b> the latest 100 usable headlines, newest first. Other articles only fill the list if fewer than 100 usable ones exist.",
        "<b>Optional body text:</b> with <font face='Courier'>--fetch-text</font> the page is read in memory (25 s budget, first paragraphs only) for better labels; otherwise the model labels headline + snippet."])

    s += [Paragraph("4. Three passes, so that nothing important hides behind the latest 100", H2)] + bullets([
        "<b>Sentiment pass</b> - the latest-100 search above. This is the only pass that feeds company sentiment.",
        "<b>Results (fundamentals) pass</b> - a separate 12-month search for results, profit, revenue, guidance, order wins. It anchors sentiment so a quiet recent quarter cannot hide a year of results.",
        "<b>Governance pass</b> - a separate 12-month search for SEBI/RBI/penalty/probe/resignation/auditor terms (up to 30 per stock), because governance events are rare and often older than the latest 100."])

    s += [Paragraph("5. Reliability and honesty rules", H2)] + bullets([
        "<b>Rate limits:</b> flat 20 s waits and up to 3 retries; a health check before the run; the run stops after 3 consecutive stocks fail rather than silently scoring empty data.",
        "<b>Minimum evidence:</b> fewer than 8 relevant articles, or no results print, means sentiment is shown as <b>n/a (Insufficient Data)</b>, never a guessed number.",
        "<b>Time travel guard:</b> a live index with a past as-of date is refused unless explicitly allowed, and then stamped \"not a point-in-time backtest\".",
        "<b>Nothing stored:</b> articles stream through memory (scrape stock N while stock N-1 is labelled). Only the audit record is written: URL, date, headline, label, drop reason.",
        "<b>Prices never enter a score.</b> Share-price headlines are typed <font face='Courier'>price_move</font> and excluded from sentiment."])

    s += [Paragraph("6. How to check it yourself", H2),
          Paragraph("<font face='Courier'>python -m sentinelq inspect news --stock TITAN</font> shows what the search returns for one stock. "
                    "After a run, <font face='Courier'>audit/queries.jsonl</font> lists every query and window with the count returned, "
                    "<font face='Courier'>dropped.jsonl</font> every article dropped and why, and <font face='Courier'>work/run.log</font> the live trace. "
                    "Code: <font face='Courier'>sentinelq/ingest/gnews.py</font>, <font face='Courier'>gdelt.py</font>, <font face='Courier'>select.py</font>.", B)]
    d.build(s)
    return path


if __name__ == "__main__":
    print(build())
