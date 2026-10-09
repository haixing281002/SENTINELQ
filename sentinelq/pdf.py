"""IC scorecard PDF. Layout, fonts, colours and spacing replicate the reference 'QVM Portfolio Signal Scorecard'
(A4 portrait, ReportLab standard fonts). Measured from the reference file: see docs/PDF_SPEC.md."""
from __future__ import annotations
import re
import unicodedata
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, HRFlowable, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# ---- palette (exact values from the reference) ------------------------------------------------------------
NAVY, BLUE, MUTED, INK = "#1a2452", "#2c3a70", "#5a6b9e", "#1f2637"
RULE, TINT = "#c9d1e5", "#f3f6fd"
GREEN, GREEN2, AMBER, RED = "#2e7d5b", "#4a8f6d", "#a97528", "#9b2f35"
LABEL_FILL = {"Clean": "#e4f1ea", "Watch": "#fbf1de", "Flag": "#f7e4e5"}
LABEL_TEXT = {"Clean": GREEN, "Watch": AMBER, "Flag": RED}
CA_BAND_FILL = {"Log": "#e4f1ea", "Watch": "#fbf1de", "Review": "#fbf1de", "Act": "#f7e4e5", "Exit": "#f7e4e5"}
CA_BAND_TEXT = {"Log": GREEN, "Watch": AMBER, "Review": AMBER, "Act": RED, "Exit": RED}
SENT_TEXT = {2: GREEN, 1: GREEN2, 0: AMBER, -1: RED, -2: RED}
C = colors.HexColor

# ---- styles -----------------------------------------------------------------------------------------------
def S(name, **kw):
    kw.setdefault("textColor", C(INK))
    return ParagraphStyle(name, **kw)


TITLE = S("title", fontName="Times-Bold", fontSize=24, leading=28, textColor=C(NAVY), spaceAfter=2)
SUBTITLE = S("subtitle", fontName="Times-Italic", fontSize=10.5, leading=13, textColor=C(MUTED), spaceAfter=15.9)
H1 = S("h1", fontName="Times-Bold", fontSize=15, leading=18, textColor=C(NAVY), spaceBefore=0, spaceAfter=2)
H2 = S("h2", fontName="Helvetica-Bold", fontSize=9.5, leading=13, textColor=C(BLUE), spaceAfter=0)
BODY = S("body", fontName="Helvetica", fontSize=9.5, leading=13, alignment=TA_JUSTIFY, spaceAfter=5)
NOTE = S("note", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5, textColor=C(MUTED), spaceAfter=5)
SUBNOTE = S("subnote", fontName="Helvetica-Oblique", fontSize=8.5, leading=11.5, textColor=C(MUTED), spaceAfter=7.9)
DISC = S("disc", fontName="Helvetica-Oblique", fontSize=7.8, leading=10, textColor=C(MUTED))
CELL = S("cell", fontName="Helvetica", fontSize=8, leading=10)
CELLB = S("cellb", parent=CELL, fontName="Helvetica-Bold")
CELLC = S("cellc", parent=CELL, alignment=TA_CENTER)
CELLBC = S("cellbc", parent=CELLB, alignment=TA_CENTER)
HDR = S("hdr", fontName="Helvetica-Bold", fontSize=8.5, leading=10, textColor=colors.white, alignment=TA_CENTER)
HOLD = S("hold", fontName="Times-Bold", fontSize=12.5, leading=17, textColor=C(NAVY), spaceBefore=20)
SCORE = S("score", fontName="Helvetica-Bold", fontSize=8.5, leading=12, spaceAfter=3)
H2A = S("h2a", parent=H2, spaceBefore=1)

_SYMBOL = {"−": "−", "≥": "≥", "≤": "≤", "→": "→"}   # rendered via Symbol font


def rl(text) -> str:
    """Escape for ReportLab and route glyphs the standard fonts lack (minus, >=, ->) through Symbol; rupee -> Rs."""
    t = str(text)
    t = re.sub(r"(?<![\w)])-(?=\d)", "\u2212", t)          # -20 -> minus sign (not 30-Apr / FY26-27)
    t = t.replace(" - ", " \u2014 ").replace("(source:", "(")
    t = t.replace("\u20b9", "Rs ").replace("Rs  ", "Rs ").replace("\u2011", "-").replace("\u00a0", " ")
    out = []
    for ch in escape(t):
        if ch in _SYMBOL:
            out.append(f'<font name="Symbol">{ch}</font>')
            continue
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode() or "?")
    return "".join(out)


def P(t, st=BODY):
    return Paragraph(rl(t), st)


def sent_txt(x) -> str:
    return "n/a" if x is None else "0" if x == 0 else f"{x:+d}".replace("-", "−")


_WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}


def num_word(n: int) -> str:
    if n < 20:
        return _WORDS[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _WORDS[n % 10] if n % 10 else "")
    return str(n)


def h1(text):
    return [Paragraph(rl(text), H1), HRFlowable(width="100%", thickness=1.2, color=C(BLUE), spaceBefore=0, spaceAfter=4)]


def paras(text, st=BODY):
    return [P(p.strip(), st) for p in re.split(r"\n\s*\n|\n", str(text)) if p.strip()]


def grid(extra=()):
    return TableStyle([("GRID", (0, 0), (-1, -1), 0.4, C(RULE)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                       ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                       ("BACKGROUND", (0, 0), (-1, 0), C(NAVY)),
                       ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C(TINT)]), *extra])


def build_pdf(path: Path, res: dict, rubric, narr: dict, observations: list[dict], meta: dict) -> None:
    title, as_of = meta["title"], date.fromisoformat(res["run"]["as_of"])
    datestr = f"{as_of.day} {as_of.strftime('%B %Y')}"
    foot = f"{title} Signal Scorecard  ·  Prototype  ·  {datestr}"

    def deco(c, doc):
        c.saveState()
        c.setStrokeColor(C(RULE))
        c.setLineWidth(0.4)
        c.line(45.354, 841.89 - 807.874, 549.921, 841.89 - 807.874)
        c.setFont("Helvetica", 7.5)
        c.setFillColor(C(MUTED))
        c.drawString(45.354, 841.89 - 816.4, foot)
        c.drawRightString(549.921, 841.89 - 816.4, f"Page {doc.page}")
        c.restoreState()

    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm + 2.9,
                          bottomMargin=18 * mm, title=f"{title} — Signal Scorecard",
                          author="Portfolio Signal Engine (prototype)", subject="IC pre-read")
    doc.addPageTemplates([PageTemplate("p", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=deco)])
    W = doc.width
    scores = sorted(res["scores"], key=lambda s: (-(s.company_sentiment if s.company_sentiment is not None else -9),
                                                  -s.governance_score))
    n = len(scores)
    g = rubric["governance"]
    st = []

    # ============ Page 1 ============
    st += [P(title, TITLE), P("Signal Scorecard — News-derived sentiment, governance and corporate-action assessment", SUBTITLE)]
    cw = [51.0, 85.1, 62.3, 69.9, 52.0, 62.4, 42.5, 73.7]
    info = Table([[P("Coverage", CELLB), P(meta.get("coverage") or f"{n} holdings", CELL), P("Research date", CELLB),
                   P(datestr, CELL), P("Lookback", CELLB), P("~12 months", CELL), P("Status", CELLB), P("Prototype", CELL)]],
                 colWidths=cw, hAlign="CENTER")
    info.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), C(TINT)), ("BOX", (0, 0), (-1, -1), 0.4, C(RULE)),
                              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    st += [info, Spacer(1, 26.1)] + h1("How to read this report")
    R = lambda s: s  # readability
    st += [P("Sentiment score", H2), Paragraph(
        rl("Sentiment is scored on a fixed −2 to +2 scale, anchored to a defined event taxonomy (earnings beats and misses, guidance changes, order wins, litigation, regulatory action, corporate actions). Each score is ")
        + "<b>absolute</b>" + rl(" — it reflects the stock on its own merits, not its rank within the portfolio — so a +2 means the same thing whether applied to a bank or a pharma name. "
                                 "Two means are shown: a ") + "<b>weighted</b>" + rl(" mean (recent news and higher-confidence sources count more) and a ") + "<b>raw</b>" + rl(" mean (a plain average of the same counted articles, no weighting). Both are banded directly — a mean of 1.6 or more is +2, 0.5 to 1.59 is +1, and the mirror for negative — with no further scaling. Price movement and stock returns never feed the sentiment score."), BODY)]
    st += [P("Governance score", H2), Paragraph(
        rl("Governance is scored on a 0–100 scale, starting at 100 with fixed penalties applied for specific event types (see rubric on next page). Scores translate to three labels — ")
        + rl("<b>Clean</b> (≥ 95), <b>Watch</b> (80–94), <b>Flag</b> (below 80)").replace("&lt;b&gt;", "<b>") .replace("&lt;/b&gt;", "</b>")
        + rl(". Flag names are highlighted in red on the summary table and warrant IC-level scrutiny before additional risk is added. The rubric is applied mechanically so the same event produces the same penalty across all holdings."), BODY)]
    st += [P("Corporate Actions score", H2), Paragraph(
        rl("Corporate Actions is ONE score on its own 0–10 scale: each event's severity (how dangerous that event type is) is multiplied by how advanced it is, how close or recent it is, and how large this instance is, capped at 10. Negative and unclear-direction events combine as the chance at least one bites — never summed or averaged, so one severe event is never diluted by several mild ones — and band to an action: Log, Watch, Review, Act or Exit (see rubric on next page). Positive-direction events (dividends, buybacks, clean mergers) are combined the same way into a separate ")
        + "<b>event-trade review</b>" + rl(" trigger, not a second score: a high reading there (≥ 6) flags the stock for a different kind of review — tender into a buyback, hold through an open offer — and never softens or offsets the Corporate Actions score above. Good news never cancels bad news. An event that is also sentiment-relevant (e.g. an auditor resignation) still feeds Sentiment, Governance and Corporate Actions from the same article — nothing is walled off to one score only."), BODY),
           P("Every score in this report can be traced back to specific, dated, sourced news in the appendix. Where retrieval coverage is thin, this is stated explicitly in the rationale.", NOTE)]
    retr = res["run"].get("retrieval") or {}
    if retr.get("mode") == "live_index_backdated":       # Reconciliation fix 1: never present a backdated live search as a backtest
        st.append(P("Retrospective run: " + retr["note"], NOTE))
    elif retr.get("mode") == "replay":
        st.append(P("Replay: " + retr["note"], NOTE))
    vsk = (res["run"].get("verified") or {}).get("skipped") or []
    if vsk:
        st.append(P(f"Human-verified evidence NOT applied at this as-of date ({len(vsk)}): " + "; ".join(f"{x['symbol']}: {x['headline'][:70]} ({x['why']})" for x in vsk[:8]) + ("; ..." if len(vsk) > 8 else "") + ". Re-verify these for the new date.", NOTE))
    cu = res["run"].get("corpus_union") or {}
    if cu.get("events"):
        st.append(P(f"Corpus union: {cu['events']} governance event(s) already on record from earlier run(s) ({', '.join(cu['runs'])}) were added to this run's evidence (same prompt and model).", NOTE))
    if res["run"].get("stamp"):                            # Upgrade v2.1 B6: the honesty stamp is printed, not optional
        stamp = res["run"]["stamp"]
        if res["run"].get("source_mode") == "backdated-live-NOT-PIT":
            st.append(Paragraph(f'<font color="{RED}"><b>{rl("NOT A POINT-IN-TIME BACKTEST")}</b></font>' + rl(" · " + stamp), NOTE))
        else:
            st.append(P("Run stamp: " + stamp + (" · published=false (golden regression set failed)" if res["run"].get("published") is False else ""), NOTE))
    st.append(PageBreak())

    # ============ Page 2: rubric ============
    st += h1("Scoring rubric") + [P("Sentiment anchors", H2)]
    present = {s.company_sentiment for s in scores}
    rows = [[P("Score", HDR), P("Meaning", HDR)]]
    for sc, txt in rubric["sentiment_anchors"]:
        txt = txt.rstrip(".")
        txt = txt + ("." if (sc != -2 or -2 in present) else "; not present in this portfolio.")
        rows.append([P(sent_txt(sc), CELLBC), P(txt, CELL)])
    t = Table(rows, colWidths=[56.6, W - 56.6], hAlign="CENTER")
    t.setStyle(grid())
    st += [t, Spacer(1, 16), P("Governance penalties", H2)]
    rows = [[P("Event category", HDR), P("Penalty", HDR), P("Notes", HDR)]]
    for k, (cat, note) in g["notes"].items():
        rows.append([P(cat, CELLB), P(g["display"][k], CELLC), P(note, CELL)])
    t = Table(rows, colWidths=[184.2, 70.9, W - 255.1], hAlign="CENTER")
    t.setStyle(grid())
    st += [t, Spacer(1, 16), P("Governance labels", H2)]
    rows = [[P("Label", HDR), P("Score band", HDR), P("Interpretation", HDR)]]
    for lab, (band, interp) in g["label_text"].items():
        rows.append([P(lab, CELLBC), P(band, CELLC), P(interp, CELL)])
    t = Table(rows, colWidths=[73.7, 85.0, W - 158.7], hAlign="CENTER")
    t.setStyle(grid([("BACKGROUND", (0, i), (0, i), C(LABEL_FILL[lab])) for i, lab in enumerate(g["label_text"], 1)]
                    + [("ROWBACKGROUNDS", (1, 1), (-1, -1), [colors.white])]))
    st += [t, Spacer(1, 16), P("Corporate Actions severity", H2)]
    ca = rubric["corporate_actions"]
    rows = [[P("Sev.", HDR), P("Event type", HDR), P("Damage", HDR)]]
    for et, sev in sorted(ca["severity"].items(), key=lambda x: -x[1]):
        rows.append([P(str(sev), CELLC), P(et.replace("_", " "), CELLB), P(ca["damage_type"].get(et, "").capitalize(), CELL)])
    t = Table(rows, colWidths=[36.9, 180.0, W - 216.9], hAlign="CENTER")
    t.setStyle(grid())
    st += [t, Spacer(1, 16), P("Corporate Actions bands", H2)]
    rows = [[P("Score", HDR), P("Band", HDR), P("Default action", HDR)]]
    act = {"Log": "Nothing.", "Watch": "Add to watchlist; no trading restriction.", "Review": "Block new buys; review before the next rebalance.",
           "Act": "Trim, hedge, or write down why you are holding; exclude from factor scoring until resolved.",
           "Exit": "Exit by default; holding requires explicit sign-off."}
    for lo, hi, lab in ca["bands"]:
        rows.append([P(f"{lo:g} – {hi:g}", CELLC), P(lab, CELLBC), P(act[lab], CELL)])
    t = Table(rows, colWidths=[73.7, 73.7, W - 147.4], hAlign="CENTER")
    t.setStyle(grid([("BACKGROUND", (1, i), (1, i), C(CA_BAND_FILL[lab])) for i, (_, _, lab) in enumerate(ca["bands"], 1)]
                    + [("ROWBACKGROUNDS", (0, 1), (0, -1), [colors.white]), ("ROWBACKGROUNDS", (2, 1), (2, -1), [colors.white])]))
    st += [t, P(f"Opportunity score ≥ {ca['opportunity_review_at']:g} triggers an event-trade review (a separate flag, not a second score). Gate A "
               "(insolvency, pledge invocation, an auditor resignation citing withheld information) overrides the arithmetic straight to Exit. Gate B "
               "(splits, bonuses, demergers, mergers, rights issues, special dividends, ISIN/symbol changes) is excluded from the score and quarantined "
               "for the adjustment-factor checks instead. A single lower-tier source for an event (noted in the Excel appendix) means its date and "
               "facts have not been corroborated — treat it as unconfirmed, as aggregator republish dates can lag the real event.", NOTE), PageBreak()]

    # ============ Scorecard table ============
    st += h1(f"Scorecard — all {n} holdings") + [P("Sorted by sentiment score (high to low), then by governance score.", SUBNOTE)]
    has_cap, has_wt = any(s.cap for s in scores), any(s.weight for s in scores)
    cols = [("Company", 96.3, lambda s, r: P(s.name, CELLB)),
            ("Cap", 32.0, lambda s, r: P(s.cap, CELLC)),
            ("Sector", 62.3, lambda s, r: P(s.sector, CELL)),
            ("Wt", 31.2, lambda s, r: P(s.weight, CELLC)),
            ("Sent.", 31.2, lambda s, r: P(sent_txt(s.company_sentiment), CELLBC)),
            ("Gov.", 28.3, lambda s, r: P(f"{s.governance_score:g}", CELLC)),
            ("Label", 36.9, lambda s, r: P("FLAG" if s.governance_label == "Flag" else s.governance_label, CELLBC)),
            ("Key corporate action", 109.8, lambda s, r: P(r["key_corporate_action"] or "—", CELL)),
            ("One-line read", 76.5, lambda s, r: Paragraph(rl(r["one_line_read"]) + (f'<br/><font color="{MUTED}" size="5.6">{rl(s.coverage_map)}</font>' if s.coverage_map else ""), CELL))]
    cols = [c for c in cols if not (c[0] == "Cap" and not has_cap) and not (c[0] == "Wt" and not has_wt)]
    scale = W / sum(c[1] for c in cols)
    li = [c[0] for c in cols].index("Label")
    data = [[P(c[0], HDR) for c in cols]] + [[c[2](s, narr[s.symbol]) for c in cols] for s in scores]
    extra = [("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
             ("GRID", (0, 0), (-1, -1), 0.35, C(RULE))]
    extra += [("BACKGROUND", (li, i), (li, i), C(LABEL_FILL[s.governance_label])) for i, s in enumerate(scores, 1)]
    t = Table(data, colWidths=[c[1] * scale for c in cols], repeatRows=1, hAlign="CENTER")
    t.setStyle(grid(extra))
    st += [t, Spacer(1, 12)]
    st += h1("Portfolio-level observations")
    for o in observations:
        lead = o["title"].strip()
        lead = lead if lead.endswith((".", "!", "?")) else lead + "."
        st.append(Paragraph(f"<b>{rl(lead)}</b> {rl(o['body'])}", BODY))
    st.append(PageBreak())

    # ============ Appendix ============
    st += [P("Appendix — Rationale by Holding", TITLE),
           P(f"For each of the {num_word(n)} holdings: the sentiment case, then the governance scoring with the specific penalty logic applied. Order follows the summary table.", SUBTITLE)]
    first = True
    for s in scores:
        nr = narr[s.symbol]
        lab = "FLAG" if s.governance_label == "Flag" else s.governance_label
        sc_col = SENT_TEXT.get(s.company_sentiment, AMBER)
        ca_seg = (f'<font color="{MUTED}"> · </font><font color="{CA_BAND_TEXT.get(s.ca_risk_band, AMBER)}">'
                  f'{rl(f"Corporate Actions {s.ca_risk_score:.1f} ({s.ca_risk_band})")}</font>') if (s.ca_events or s.ca_gate_a) else ""
        line = Paragraph(f'<font color="{sc_col}">{rl("Sentiment " + sent_txt(s.company_sentiment))}</font>'
                         f'<font color="{MUTED}"> · </font>'
                         f'<font color="{LABEL_TEXT[s.governance_label]}">{rl(f"Governance {s.governance_score:g} ({lab})")}</font>' + ca_seg, SCORE)
        sent = paras(nr["sentiment_rationale"]) + (paras(nr["coverage_note"]) if nr.get("coverage_note") else [])
        gov = paras(nr["governance_rationale"])
        ca_txt = paras(nr.get("ca_rationale") or "No corporate-action events scored in the lookback.")
        weak = [e for e in list(s.ca_events) + list(s.ca_gate_a) + list(s.ca_gate_b)
                if e.get("n_sources") == 1 and e.get("max_tier") in ("T3", "T4")]
        if weak:                                     # deterministic - never depends on the narrator remembering to say it
            names = ", ".join(dict.fromkeys(f"{e['event_type'].replace('_', ' ')} ({e['date']})" for e in weak))
            ca_txt += paras(f"Sourcing note: {names} relied on a single lower-tier outlet - its date and facts have not been "
                            f"corroborated elsewhere and should be treated as unconfirmed, not settled fact.")
        head = [Paragraph(rl(s.name), ParagraphStyle("hold0", parent=HOLD, spaceBefore=0 if first else 20)), line]
        if s.coverage_map:
            head.append(Paragraph(f'<font color="{MUTED}">{rl("Coverage: " + s.coverage_map)}</font>', SUBNOTE))
        head.append(P("Sentiment rationale", H2))
        st.append(KeepTogether(head + sent[:1]))
        st += sent[1:] + [P("Governance rationale", H2A)] + gov + [P("Corporate Actions rationale", H2A)] + ca_txt
        first = False
    st += [Spacer(1, 6), HRFlowable(width="100%", thickness=0.5, color=C(RULE), spaceBefore=4, spaceAfter=4),
           P("This report is a prototype research aid produced by the Portfolio Signal Engine. Sentiment and governance scores are built from retrieved, dated, linked news items only, using the fixed rubric shown on page 2. Direct BSE/NSE filings and Ministry-of-Corporate-Affairs registry integration is scheduled for the production build and will materially improve governance-signal coverage. This document is not investment advice; position weights and universe are as supplied in the portfolio file.", DISC)]
    doc.build(st)
