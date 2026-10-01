---
name: sentinelq-step2-ingest
description: Step 2 of the Sentinel Q pipeline - fetch news and corporate actions for each stock, free sources only, nothing stored. Use to test or explain the news pull for one stock or to debug missing/odd articles.
---

# Step 2 - Ingest
**Document says:** three layers - news (queried per company, de-duplicated by URL, 12 months), disclosures/actions (dividends, splits, buybacks), prices (context only,
never a score). Every item carries its source URL and publication date from the moment it enters.

**Code:** `sentinelq/ingest/gnews.py` (default, Google News RSS), `gdelt.py` (`--news gdelt`), `yf.py` (actions + prices), `select.py` (selection),
`fulltext.py` (optional body text, memory only). Orchestration: `pipeline.py::_scrape_all`.

**Rules in force:** roll the window back newest-first until the latest N (default 100) articles whose TITLE names the company (<= 12 months); a separate
12-month governance-keyword search (<= 30 articles, governance scoring only); free sources only; articles are never written to disk; a health check runs first;
3 stocks failing in a row stops the run (no hammering a rate limit).

**Run / inspect one stock, in memory:**
`python -m sentinelq inspect news --portfolio portfolio/stocks_given.tsv --stock ANGELONE --source gnews --max-articles 100 --as-of 2026-07-03`
(prints every request it made, the kept articles, and the governance candidates).

**Manually verify:** `runs/<date>/audit/queries.jsonl` lists every request URL; `RUN_RECORD.md` Step 2 table shows requests, retrieved, governance-pass counts.
Google News links are redirect URLs (they open the publisher) - they cite the source but cannot be fetched for body text.

**Three passes per stock (Reconciliation s7):** latest-N sentiment pull; a 12-month **results** pass (so full-year prints are read); a 12-month **governance** pass. Past as-of date: `--news auto` uses the dated archive; a live index is refused or stamped. See `docs/RECONCILIATION_FIXES.md`.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.
