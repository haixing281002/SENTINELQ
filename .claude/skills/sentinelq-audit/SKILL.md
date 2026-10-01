---
name: sentinelq-audit
description: Manual verification and documentation workflow for a Sentinel Q run - what to open, in what order, to check every step. Use when the user wants to verify, audit or document a run.
---

# Auditing a run (manual verification checklist)
Run folder: `runs/<as-of date>/`. Work through it in this order:
1. `audit/RUN_RECORD.md` - parameters, per-step tables, what was dropped and why, scores with penalties applied / not applied.
2. `python -m sentinelq verify runs/<date>` - every score recomputed from `evidence.json` with the rubric embedded in the run. Must say ALL SCORES REPRODUCED.
3. `audit/score_workings.csv` - recompute any one stock by hand (sum(weight x sentiment) / sum(weight); 100 + penalties).
4. `audit/queries.jsonl` - the exact news requests made. `python -m sentinelq inspect news --stock SYM ...` repeats one live.
5. `dropped.jsonl` / Coverage sheet - every item removed and the reason (`not_about_company`, `generic_market_headline`, `label_failed:...`, date/URL problems).
6. `raw_model_responses.jsonl` - what the model actually returned. `python -m sentinelq inspect label ...` re-labels one headline.
7. `audit/manifest.json` - sha256 of every output file, code version/commit, rubric hash.
Live view while running: the display tags every line `[STEP n/6 NAME]` and shows a per-stock step tracker; the same text goes to `work/run.log` (`tail -f`).
Conformance with the architecture document: `docs/PIPELINE_CONFORMANCE.md`.
