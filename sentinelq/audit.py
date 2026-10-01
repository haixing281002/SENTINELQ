"""Run documentation for manual verification. Written to <run>/audit/ :
  RUN_RECORD.md      human-readable record of what happened in each of the six steps
  score_workings.csv every number behind every score (per article: age, weight, contribution) - recompute by hand
  queries.jsonl      every news request made (exact URL / query / window / outcome)
  manifest.json      parameters, versions, rubric hash, git commit, and sha256 of every output file
Nothing here contains article text - only headlines, dates, URLs, labels and arithmetic."""
from __future__ import annotations
import csv
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from . import __version__
from .score import recency_weight


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def score_workings(result: dict, rubric) -> list[dict]:
    as_of = date.fromisoformat(result["run"]["as_of"])
    rows = []
    for s in result["scores"]:
        items = result["kept"].get(s.symbol, [])
        num = den = 0.0
        for li in sorted(items, key=lambda x: x.item.published, reverse=True):
            if li.item.kind != "news" or li.item.purpose != "sentiment":
                continue
            w = recency_weight(li.item.published, as_of, rubric)
            age = (as_of - date.fromisoformat(li.item.published)).days
            num += w * li.label.sentiment
            den += w
            rows.append({"symbol": s.symbol, "component": "sentiment", "date": li.item.published, "headline": li.item.title,
                         "url": li.item.url, "event_type": li.label.event_type, "label_value": li.label.sentiment,
                         "age_days": age, "weight": round(w, 6), "contribution": round(w * li.label.sentiment, 6), "note": ""})
        rows.append({"symbol": s.symbol, "component": "sentiment-total", "date": "", "headline": f"sum(w*s)={num:.6f} / sum(w)={den:.6f}",
                     "url": "", "event_type": "", "label_value": "", "age_days": "", "weight": round(den, 6),
                     "contribution": round(num / den, 6) if den else "", "note": f"= {s.company_sentiment_raw}; shown as integer {s.company_sentiment}"})
        run_total = float(rubric["governance"]["start"])
        for p in s.governance_penalties:
            run_total += p["penalty"]
            rows.append({"symbol": s.symbol, "component": "governance-penalty", "date": p["date"], "headline": p["headline"], "url": p["url"],
                         "event_type": p["event_type"], "label_value": p["penalty"], "age_days": "", "weight": "", "contribution": p["penalty"],
                         "note": f"running score {run_total:g}"})
        for p in s.governance_ignored:
            rows.append({"symbol": s.symbol, "component": "governance-NOT-scored", "date": p["date"], "headline": p["headline"], "url": p["url"],
                         "event_type": p["event_type"], "label_value": 0, "age_days": "", "weight": "", "contribution": 0, "note": p["why"]})
        rows.append({"symbol": s.symbol, "component": "governance-total", "date": "", "headline": f"start {rubric['governance']['start']} + penalties",
                     "url": "", "event_type": "", "label_value": "", "age_days": "", "weight": "", "contribution": s.governance_score,
                     "note": s.governance_label})
        for d in s.corporate_action_detail:
            rows.append({"symbol": s.symbol, "component": "corporate-action", "date": d["date"], "headline": d["headline"], "url": d["url"],
                         "event_type": d["event_type"], "label_value": d["materiality"], "age_days": "", "weight": "", "contribution": d["contribution"], "note": ""})
    return rows


def write_audit(out: Path, result: dict, rubric, params: dict, queries: list[dict]) -> None:
    ad = Path(out) / "audit"
    ad.mkdir(parents=True, exist_ok=True)
    run, scores, dropped = result["run"], result["scores"], result["dropped"]

    rows = score_workings(result, rubric)
    with open(ad / "score_workings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["symbol"])
        w.writeheader()
        w.writerows(rows)
    with open(ad / "queries.jsonl", "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    by_reason: dict[str, Counter] = {}
    for d in dropped:
        by_reason.setdefault(d.symbol, Counter())[d.reason.split(":")[0]] += 1
    gov_cands = {s.symbol: sum(1 for li in result["kept"].get(s.symbol, []) if li.item.purpose == "governance") for s in scores}
    reqs = Counter(q["label"].split(" ")[0] for q in queries)
    md = [f"# Sentinel Q run record", "",
          f"- Generated: {run['generated_at']}   |   as-of date: **{run['as_of']}**   |   code {__version__} @ git `{_git_commit()}`",
          f"- Rubric: version **{run['rubric_version']}**, sha256 `{run['rubric_sha256'][:16]}...` (full rubric is embedded in run_meta.json)",
          f"- Labeller: `{run['classifier_model']}`   |   prompt `{run['prompt_version']}`   |   scoring mode: {run['mode']}",
          f"- Python {sys.version.split()[0]} on {platform.system()} {platform.release()}", "",
          "## Parameters (everything that shaped this run)", "", "| setting | value |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in params.items()]
    retr = run.get("retrieval", {})
    md += ["", f"- **Retrieval: {retr.get('mode', '?')}** - {retr.get('note', '')}", f"- Universe hash `{run.get('universe_hash', '')[:12]}` (diffed against the last confirmed run before this run started)"]
    if run.get("stamp"):
        g = run.get("golden") or {}
        md += [f"- **Stamp (v2.1 B6):** {run['stamp']}", f"- Run id `{run.get('run_id', '')}` · sampler {run.get('sampler', '')} · items sent to the model {run.get('n_llm_items', '?')} · "
               f"golden {g.get('total', 0) - g.get('failed', 0)}/{g.get('total', 0)} · published={run.get('published')}"]
    md += ["", "## Step 1 - Input", "", f"{len(scores)} stocks read (symbol, company, sector; cap/weight only for display).", "",
           "| symbol | company | sector | cap |", "|---|---|---|---|"]
    md += [f"| {s.symbol} | {s.name} | {s.sector} | {s.cap} |" for s in scores]
    md += ["", "## Step 2 - Ingest", "",
           f"News source `{params.get('news_source')}`: {len(queries)} request(s) logged in `audit/queries.jsonl` "
           f"(failed: {sum(1 for q in queries if q.get('status') != 'ok')}). Each item carries its source URL and date from the moment it enters.", "",
           "| symbol | requests | retrieved (kept after selection) | of which governance-pass | corporate actions |", "|---|---|---|---|---|"]
    for c in result["coverage"]:
        md.append(f"| {c['symbol']} | {reqs.get(c['symbol'], 0)} | {c['retrieved']} | {gov_cands.get(c['symbol'], 0)} | {c['actions_kept']} |")
    md += ["", "Evidence window actually covered (sentiment needs the 12-month lookback, not only the latest weeks):", "",
           "| symbol | relevant articles | first | last | span (days) | status |", "|---|---|---|---|---|---|"]
    md += [f"| {s.symbol} | {s.relevant_articles} | {s.evidence_first} | {s.evidence_last} | {s.evidence_span_days if s.evidence_span_days is not None else ''} | {s.sentiment_note or 'ok'} |" for s in scores]
    ver = run.get("verified", {})
    if ver.get("file"):
        md += ["", f"Human-verified evidence (`{ver['file']}`), merged before scoring - union-then-rescore: **{len(ver.get('applied', []))} applied**, "
                   f"{len(ver.get('skipped', []))} not applicable at this as-of date.", ""]
        md += [f"- {v['symbol']}: {v['headline'][:110]} - weight {v['weight']}, {v['status']} ({v['source']})" for v in ver.get("applied", [])]
        md += [f"- (skipped) {v['symbol']}: {v['headline'][:90]} - {v['why']}" for v in ver.get("skipped", [])]
    md += ["", "## Step 3 - Classify (the only AI step)", "",
           f"Model `{run['classifier_model']}` labels text it is handed: event type from a fixed taxonomy, sentiment -2..+2, one-line rationale, "
           f"governance flag. Reply structure enforced by JSON schema: **{params.get('schema_enforced')}**. Temperature: not controllable via the Claude Code CLI "
           "(repeatability comes from the label cache, on disk: " + str(params.get("label_cache_on_disk")) + "). "
           "Every raw reply is in `raw_model_responses.jsonl`.", "",
           f"Items labelled: {run['totals']['kept'] + run['totals']['dropped'] - sum(1 for d in dropped if d.stage == 'prefilter')}; label failures: {run['totals']['label_failed']}.",
           "", "## Step 4 - Validate", "", "Schema, source URL, date-in-lookback, relevance gate. Failures retried once, then dropped with a logged reason "
           "(`dropped.jsonl`).", "", "| symbol | retrieved | kept | dropped (reasons) |", "|---|---|---|---|"]
    for c in result["coverage"]:
        rs = ", ".join(f"{v} {k}" for k, v in by_reason.get(c["symbol"], Counter()).most_common()) or "none"
        md.append(f"| {c['symbol']} | {c['retrieved']} | {c['kept']} | {rs} |")
    md += ["", "## Step 5 - Score (deterministic, no model)", "",
           "Recompute any number by hand from `audit/score_workings.csv`, or run `python -m sentinelq verify " + str(out) + "`.", "",
           "| symbol | sentiment (int / weighted mean) | governance | penalties applied | NOT penalised (and why) |", "|---|---|---|---|---|"]
    for s in scores:
        pens = "; ".join(f"{p['penalty']:+g} {p['event_type']} ({p['date']})" for p in s.governance_penalties) or "none"
        ign = "; ".join(f"{p['event_type']} ({p['date']}): {p['why']}" for p in s.governance_ignored) or "none"
        md.append(f"| {s.symbol} | {s.company_sentiment} / {s.company_sentiment_raw} | {s.governance_score:g} {s.governance_label} | {pens} | {ign} |")
    lint = result.get("lint", [])
    md += ["", f"Price-language lint (share-price moves never feed sentiment): {len(lint)} sentence(s) removed from sentiment commentary." +
           ("" if not lint else " " + "; ".join(f"{x['symbol']}: \"{x['removed_sentence'][:80]}\"" for x in lint[:8]))]
    md += ["", "## Step 6 - Report", "", "Files and checksums are in `audit/manifest.json`.", ""]
    (ad / "RUN_RECORD.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    files = sorted(p for p in Path(out).rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {"generated_at": datetime.now().isoformat(timespec="seconds"), "code_version": __version__, "git_commit": _git_commit(),
                "rubric_version": run["rubric_version"], "rubric_sha256": run["rubric_sha256"], "parameters": params,
                "files": {str(p.relative_to(out)): {"sha256": _sha(p), "bytes": p.stat().st_size} for p in files}}
    (ad / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
