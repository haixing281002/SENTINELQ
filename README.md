# Sentinel Q

Six-stage pipeline from the *Sentinel Q Architecture* document: **Input → Ingest → Classify → Validate → Score → Report**.
The LLM only labels text; all scores come from the fixed rubric in `rubric/rubric_v1.json`. Prices never feed a score.

| Stage | Code |
|---|---|
| 1 Input (`symbol,name,sector` CSV) | `pipeline.load_portfolio` |
| 2 Ingest (news 12m, actions 12m, prices 6m; URL-deduped) | `sentinelq/ingest/` |
| 3 Classify (forced tool-schema, temp 0, cache, raw log) | `classify.py`, `cache.py` |
| 4 Validate (retry once, then drop with reason) | `validate.py` |
| 5 Score (recency-weighted sentiment, sector, governance, corp actions) | `score.py` |
| 6 Report (IC scorecard PDF, xlsx, csv/json, audit trail) | `pdf.py`, `narrate.py`, `report.py` |

## Run
```bash
pip install -e ".[llm,prices]"
export ANTHROPIC_API_KEY=...
sentinelq --portfolio p.csv --title 'QVM Portfolio' --coverage '30 holdings'   # portfolio CSV: symbol,name,sector[,cap,weight]
# live: GDELT news + Yahoo actions/prices + Claude labels

# offline demo (keyword stub classifier, fixtures)
sentinelq --portfolio examples/portfolio.csv --news file --news-file examples/fixtures/news.json \
  --actions file --actions-file examples/fixtures/actions.json --prices file \
  --prices-file examples/fixtures/prices.csv --classifier keyword --as-of 2026-07-15
```
`--mode relative` adds ±3σ winsorised cross-sectional z-scores (default `absolute`). Model: `--model` or `SENTINELQ_MODEL`.

## Outputs (`runs/<date>/`)
`sentinelq_scorecard.pdf` (the IC report: how-to-read, rubric, sorted scorecard, observations, per-holding rationale + evidence trail), `narrative.json`, `sentinelq_report.xlsx` (Scorecard, Evidence sheets, Coverage, Price Context, Run Info), `scorecard.csv`, `scores.json`,
`evidence.json`, `dropped.jsonl`, `raw_model_responses.jsonl`, `run_meta.json` (rubric version + hash).

## Assumptions not in the document (edit in the rubric)
Sentiment shown as the nearest integer of the recency-weighted mean (unrounded kept in xlsx); pledge penalty (-10) and medium exchange fine (-7); historical out-of-window events get one flat -5 memory discount unless the same type is already penalised in-window; corporate actions are listed in the PDF (they do not feed sentiment/governance) and also scored in the xlsx; corporate-action base values/multipliers and ±2 clip;
governance counts each event type once per stock; sector sentiment pools URL-deduped constituent news;
half-life 60 trading days converted from calendar days at 252/365; low-confidence flag under 3 news items.

Prose in the PDF (rationales, one-line reads, observations) is written by the model from the evidence only, cached by evidence hash, and cannot alter scores; `--narrator template` gives a deterministic offline version.
