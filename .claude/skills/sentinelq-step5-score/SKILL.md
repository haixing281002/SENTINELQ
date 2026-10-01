---
name: sentinelq-step5-score
description: Step 5 of the Sentinel Q pipeline - deterministic scoring (company/sector sentiment, governance, corporate actions) from the fixed rubric; no AI. Use to explain, recompute or verify any score.
---

# Step 5 - Score (deterministic, no AI)
**Document says:** company sentiment = recency-weighted mean of per-article sentiments (half-life ~60 trading days) on -2..+2; sector sentiment = same aggregation once per sector;
governance = 100 minus fixed penalties (Clean >= 95, Watch 80-94, Flag < 80); corporate actions = rule table scaled by materiality. All weights live in ONE versioned rubric file.

**Code:** `sentinelq/score.py`, rubric `rubric/rubric_v1.json` (version + sha256 recorded in every run). Sentiment uses the latest-N pass only; governance uses both passes;
sentiment is shown as an integer, the unrounded mean is kept. Prices never enter any score.

**Recompute everything from the saved evidence (the manual-verification command):**
`python -m sentinelq verify runs/<date>`  -> recomputes every stock with the rubric embedded in that run and prints MATCH / MISMATCH.
**Per-number workings:** `runs/<date>/audit/score_workings.csv` (each article: age, weight, contribution; governance running score; penalties NOT applied and why).
Governance for one stock from a hand-made event list: `python scripts/score_governance.py events.json`.
**Never** nudge a score; change labels (visibly) or the rubric file.

**Conventions (rubric `governance.conventions`):** verification gate, Rs 10 cr materiality floor, notice-without-order = -10, 7-day same-event dedupe within a family, memory discount only with no in-window penalty, one corporate action per declaration, human-verified weights. **Minimum evidence:** < 8 relevant articles or no results print -> sentiment n/a (Insufficient Data). Details: `docs/RECONCILIATION_FIXES.md`.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.

## v2.1 event scoring
Sentiment is confidence-weighted (`conf = min(1, 0.40 + 0.15 ln(1+n_sources)) * tier_weight`), governance penalises once per verified event, a governance event with conf < 0.4 and no T1/T2 source is listed not penalised, minimum evidence = >= 6 events over >= 2 windows with >= 1 results event. `python -m sentinelq replay --as-of DATE` rescores from stored events with no network and no model; `golden` must be 37/37 or the run is stamped published=false.
