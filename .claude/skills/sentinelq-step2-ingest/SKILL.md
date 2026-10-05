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

**Exchange data (MCP):** `.mcp.json` registers NSE India's `nse-bhavcopy` (end-of-day cash-market file) and `cm-market` servers; use them to cross-check a symbol, a close, or a dividend/split record against the exchange. Context only - prices never feed a score. See `docs/NSE_MCP.md`.

**Manually verify:** `runs/<date>/audit/queries.jsonl` lists every request URL; `RUN_RECORD.md` Step 2 table shows requests, retrieved, governance-pass counts.
Google News links are redirect URLs (they open the publisher) - they cite the source but cannot be fetched for body text.

**Three passes per stock (Reconciliation s7):** latest-N sentiment pull; a 12-month **results** pass (so full-year prints are read); a 12-month **governance** pass. Past as-of date: `--news auto` uses the dated archive; a live index is refused or stamped. See `docs/RECONCILIATION_FIXES.md`.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.

## v2.1 stratified sampler (default)
Fixed windows W1 0-30 d (40) / W2 31-90 (25) / W3 91-180 (20) / W4 181-365 (15), per-day cap 6, carry-forward; results and governance passes are anchors outside the budget. Articles cluster into EVENTS before labelling (`sentinelq/cluster.py`); source tiers in `sentinelq/config/source_tiers.yaml`. The run display prints `stratified sample: W1:40/40 ...` and `events: N articles -> M events` per stock. `--legacy-sampler` = the old latest-100. Details: `docs/UPGRADE_V2_1.md`.
