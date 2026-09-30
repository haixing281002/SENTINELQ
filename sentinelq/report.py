"""Stage 6 - scorecard + evidence trail for every score + coverage sheet + run log."""
from __future__ import annotations
import csv
import json
from dataclasses import asdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

HDR = PatternFill("solid", fgColor="1F3864")
FILLS = {"Clean": "C6EFCE", "Watch": "FFEB9C", "Flag": "FFC7CE"}


def _sheet(wb, title, headers, rows, link_col=None, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HDR
    for r in rows:
        ws.append(r)
    if link_col is not None:
        for row in ws.iter_rows(min_row=2, min_col=link_col + 1, max_col=link_col + 1):
            for c in row:
                if c.value and str(c.value).startswith("http"):
                    c.hyperlink = c.value
                    c.font = Font(color="0563C1", underline="single")
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(12, len(h) + 2))
    ws.freeze_panes = "A2"
    return ws


def write_reports(out: Path, res: dict, rubric) -> None:
    out.mkdir(parents=True, exist_ok=True)
    scores, kept, run = res["scores"], res["kept"], res["run"]
    wb = Workbook()
    wb.remove(wb.active)

    # Scorecard
    heads = ["Symbol", "Company", "Cap", "Sector", "Weight", "Sentiment (-2..+2)", "Sentiment (unrounded)", "Sector Sentiment (raw)",
             "Governance Score", "Governance Label", "Corporate Action Score", "Items", "Dropped",
             "Low Confidence", "Rationale"]
    rows = [[s.symbol, s.name, s.cap, s.sector, s.weight, s.company_sentiment, s.company_sentiment_raw, s.sector_sentiment, s.governance_score,
             s.governance_label, s.corporate_action_score, s.n_items, s.n_dropped,
             "YES" if s.low_confidence else "", s.rationale] for s in scores]
    ws = _sheet(wb, "Scorecard", heads, rows, widths={"Company": 26, "Rationale": 90})
    for row in ws.iter_rows(min_row=2):
        row[9].fill = PatternFill("solid", fgColor=FILLS.get(row[9].value, "FFFFFF"))
        row[14].alignment = Alignment(wrap_text=True, vertical="top")

    ev_head = ["Symbol", "Date", "Headline", "Event Type", "Sentiment", "Governance Flag",
               "Rationale", "Source URL"]
    W = {"Headline": 70, "Rationale": 70, "Source URL": 50, "Event Type": 20}

    def ev(li):
        l = li.label
        return [li.item.symbol, li.item.published, li.item.title, l.event_type, l.sentiment,
                "Y" if l.governance_flag else "", l.rationale, li.item.url]

    news = [li for v in kept.values() for li in v if li.item.kind == "news"]
    acts = [li for v in kept.values() for li in v if li.item.kind == "action"]
    news.sort(key=lambda x: (x.item.symbol, x.item.published), reverse=False)
    _sheet(wb, "Evidence - Sentiment", ev_head, [ev(x) for x in news], link_col=7, widths=W)

    gov = [[s.symbol, p["date"], p["headline"], p["event_type"], p["penalty"], p["url"]]
           for s in scores for p in s.governance_penalties]
    _sheet(wb, "Evidence - Governance", ["Symbol", "Date", "Headline", "Event Type", "Penalty", "Source URL"],
           gov, link_col=5, widths=W)

    ca = [[s.symbol, d["date"], d["headline"], d["event_type"], d["materiality"], d["contribution"], d["url"]]
          for s in scores for d in s.corporate_action_detail]
    _sheet(wb, "Evidence - Corp Actions",
           ["Symbol", "Date", "Headline", "Event Type", "Materiality", "Contribution", "Source URL"],
           ca, link_col=6, widths=W)

    cov = [[c["symbol"], c["retrieved"], c["news_kept"], c["actions_kept"], c["kept"], c["dropped"], c["label_failed"],
            "YES" if c["low_confidence"] else ""] for c in res["coverage"]]
    t = run["totals"]
    cov.append(["PORTFOLIO", t["retrieved"], "", "", t["kept"], t["dropped"], t["label_failed"], ""])
    _sheet(wb, "Coverage", ["Symbol", "Retrieved", "News Kept", "Actions Kept", "Kept", "Dropped",
                            "Of which: model failed to label", "Low Confidence"], cov)
    ws = wb["Coverage"]
    ws.append([])
    ws.append(["Dropped items (stage, reason)"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for d in res["dropped"]:
        ws.append([d.symbol, d.date, d.headline, d.stage, d.reason, d.url])
    for e in res["ingest_errors"]:
        ws.append([e["symbol"], "INGEST ERROR", e["provider"], e["error"]])

    if res["price_context"]:
        pc = res["price_context"]
        keys = ["symbol", "ret_1m", "ret_3m", "ret_6m", "excess_1m", "realized_vol_ann", "quadrant"]
        _sheet(wb, "Price Context (info only)",
               ["Symbol", "1M Ret", "3M Ret", "6M Ret", "1M Excess vs Nifty", "Realized Vol (ann.)",
                "Sentiment x Excess Quadrant"],
               [[p[k] if not isinstance(p[k], float) else round(p[k], 4) for k in keys] for p in pc],
               widths={"Sentiment x Excess Quadrant": 50})
    if res["relative"]:
        keys = list(res["relative"][0])
        _sheet(wb, "Relative (z-scores)", keys, [[r[k] for k in keys] for r in res["relative"]])

    _sheet(wb, "Run Info", ["Key", "Value"], [[k, json.dumps(v) if isinstance(v, dict) else v]
                                              for k, v in run.items()])
    wb.save(out / "sentinelq_report.xlsx")

    # Machine-readable outputs
    with open(out / "scorecard.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(heads)
        w.writerows(rows)
    (out / "scores.json").write_text(json.dumps([s.to_dict() for s in scores], indent=2))
    (out / "evidence.json").write_text(json.dumps(
        [li.to_row() for v in kept.values() for li in v], indent=2))
    # Audit trail
    (out / "run_meta.json").write_text(json.dumps(
        dict(run, coverage=res["coverage"], ingest_errors=res["ingest_errors"],
             rubric=rubric.data), indent=2))
    with open(out / "dropped.jsonl", "w") as f:
        for d in res["dropped"]:
            f.write(json.dumps(asdict(d)) + "\n")
    with open(out / "raw_model_responses.jsonl", "w") as f:
        for r in res["raw_log"]:
            f.write(json.dumps(r) + "\n")
