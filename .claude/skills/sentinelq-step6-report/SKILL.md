---
name: sentinelq-step6-report
description: Step 6 of the Sentinel Q pipeline - scorecard PDF, workbook, commentary and audit record. Use to rebuild the PDF, edit commentary, or find where each output lives.
---

# Step 6 - Report
**Document says:** a scorecard plus, for every score, the evidence rows behind it (date, headline, label, one-line rationale, source link) and a coverage sheet. No orphan scores.

**Code:** `sentinelq/pdf.py` (replicates the reference scorecard: A4 portrait, fixed palette), `report.py` (xlsx + csv/json), `narrate.py` (commentary from the evidence only),
`audit.py` (run record). Outputs in `runs/<date>/`: `sentinelq_scorecard.pdf`, `sentinelq_report.xlsx` (Scorecard, evidence sheets incl. governance 'NOT scored' rows, Coverage,
Run Info), `scores.json`, `evidence.json`, `dropped.jsonl`, `raw_model_responses.jsonl`, `narrative.json`, `run_meta.json`, `audit/`.

**Rebuild the PDF without re-running anything (e.g. after you edit commentary):** edit `runs/<date>/narrative.json`, then
`python -m sentinelq render runs/<date>`.
**Manually verify:** `audit/RUN_RECORD.md` (readable record of Steps 1-6), `audit/manifest.json` (sha256 of every output), `verify` for the numbers.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.
