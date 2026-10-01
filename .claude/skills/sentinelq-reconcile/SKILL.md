---
name: sentinelq-reconcile
description: Reconcile two Sentinel Q runs or add human-verified evidence - union-then-rescore, universe lock, backdating policy. Use when the user compares two scorecards, adds a verified event, changes the stock list, or runs with a past as-of date.
---

# Reconciling and protecting a run (Reconciliation s7)
* **Two scorecards disagree?** Never average, never pick a side: `python -m sentinelq merge runs/A runs/B --out runs/merged` = union of evidence, scored once, with `MERGE_RECORD.md`.
* **A person verified an event against a filing?** Add a row to `portfolio/verified_events.csv` (source_ref is mandatory; `status` verified|provisional; a blank date means
  "in force at the as-of date" and applies only when `valid_as_of` equals the run's as-of). It is merged into every run and its weight is recorded as human-set.
* **Stock list changed?** The run refuses until `--confirm-universe` (it prints exactly which stocks were added / removed). Use `--expect-count N`.
* **Past as-of date?** `--news auto` uses the dated archive (GDELT). A live index with a past date is refused unless `--allow-live-backdate`, and the report is stamped
  "not a point-in-time backtest". Say so when presenting such a run.
* **Sentiment shows n/a?** That is the minimum-evidence standard (fewer than 8 relevant articles, or no results print) - report it as Insufficient Data; do not force a score.
Rules and tests: `docs/RECONCILIATION_FIXES.md`.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.
