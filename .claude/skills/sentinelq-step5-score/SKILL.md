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
