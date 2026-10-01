---
name: sentinelq
description: Produce the Sentinel Q IC scorecard PDF (sentiment, governance, corporate actions, evidence trail) for any stock names the user sends. Use whenever the user gives one or more stock/company names and wants the Sentinel Q / QVM Signal Scorecard, a re-run, or an update.
---

# Sentinel Q scorecard

The repo's `sentinelq` package is a fixed six-stage pipeline (input -> ingest -> classify -> validate -> score -> report).
The model only LABELS text; sentiment/governance/corporate-action scores come from `rubric/rubric_v1.json`.
Never invent or hand-adjust a score. To change a score, change the rubric (and say so).

## Workflow for "names the user typed"
1. **Resolve names.** `portfolio/universe.csv` holds known stocks (symbol, name, sector, cap). Try
   `sentinelq --pick "Angel One, Titan Company" ...`. If it says "not in universe", work out the NSE ticker
   (Yahoo form = ticker + `.NS`), the company name as used in news, sector and cap, **confirm with the user if unsure**,
   append a row to `portfolio/universe.csv`, and retry. Weights are never needed (they don't affect any score); the Wt/Cap columns
   appear in the PDF only if the user supplies them in a `--portfolio` CSV.
2. **Run** (no API key needed; uses the logged-in `claude` CLI). News rule: free sources only (never anything billed): default `--news gnews` (Google News RSS), or `--news gdelt`; roll the window back from the as-of date and stop
   at the latest 100 articles whose title names the company (default `--max-articles 100`) plus a separate 12-month governance search (12-month max); articles are read in memory and never stored - see `docs/NEWS_MECHANISM.md`, `docs/GDELT.md`:
   ```bash
   pip install -e ".[prices]"      # first time only
   sentinelq --pick "<names>" --fetch-text \
     --title "<Title>" --coverage "<n> holdings" --as-of <YYYY-MM-DD> --out runs/<date>
   ```
   `--as-of` = the user's research date (default today). Add `--classifier file` for the hand-off mode
   (see `docs/NO_API_KEY.md`) when the `claude` CLI is unavailable.
3. **If GDELT/Yahoo are blocked** (403 / empty ingest errors on the Coverage sheet): collect dated, linked articles
   yourself with WebSearch/WebFetch (aim for 15-50 per company, last 12 months, real URLs only, never fabricate) into a
   JSON `{"SYMBOL":[{"title","snippet","url","date","source"}]}` and rerun with `--news file --news-file <json>`
   (optionally `--actions file --actions-file`, `--prices file`). State clearly in the reply that news came from web
   search rather than GDELT, and that coverage is therefore partial.
4. **Deliver** `runs/<date>/sentinelq_scorecard.pdf` (send it with SendUserFile) and the xlsx. Summarise: scores,
   flags, drops (`dropped.jsonl`), low-confidence names, and any failed ingest. Commit output only if asked.

## Output contract (must match the reference report)
Page 1 how-to-read; page 2 rubric; scorecard table sorted by sentiment desc then governance desc (Company, Cap, Sector,
Wt, Sent., Gov., Label, Key corporate action, One-line read; Flag rows red); portfolio-level observations; appendix per
holding (sentiment rationale, governance rationale with the penalty arithmetic, coverage note, evidence trail).
The PDF generator (`sentinelq/pdf.py`) already does this - do not hand-write a different layout.

## Rules
- Prices never feed a score. Corporate actions are listed, not fed into sentiment/governance in the PDF.
- Every claim needs a dated source URL; items without one are dropped and disclosed, never passed through.
- Thin coverage must be stated (`low_confidence`, coverage note). Do not pad with guesses.
- Reproducibility: keep `.cache/labels.jsonl`; state the rubric version (in Run Info / PDF page 2).

## The six steps are separate skills
`sentinelq-step1-input`, `sentinelq-step2-ingest`, `sentinelq-step3-classify`, `sentinelq-step4-validate`, `sentinelq-step5-score`, `sentinelq-step6-report`, plus `sentinelq-governance` and `sentinelq-audit` (manual verification). Use them to run, inspect or explain one step; use this skill for the whole run. Always tell the user which step is running and what it is doing, and after a run point them to `audit/RUN_RECORD.md` and `python -m sentinelq verify <run>`.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.
