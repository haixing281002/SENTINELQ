# Does the pipeline follow the six steps in "Sentinel Q Architecture - How It Works"?

Short answer: **yes, same six stages in the same order for every stock** - with the deviations below, most of them changes you asked for and a few
limits we cannot remove. Anything marked **GAP** is not what the document describes.

| # | Document | What the code does | Status |
|---|---|---|---|
| 1 Input | spreadsheet: symbol, name, sector | CSV or pasted CD_NSE/ISIN table; sector/cap auto-resolved; aliases for search | OK (extended) |
| 2 Ingest - news | global news index queried per company, de-duplicated by URL, 12 months | Google News RSS (default) or GDELT, per company, de-duplicated; **latest 100 articles whose title names the company, rolling back to 12 months** | **CHANGED by you**: the sentiment now reflects the latest period (often weeks for busy stocks), not the whole year; GDELT -> Google News |
| 2 - governance | (news-derived) | extra **12-month governance-keyword search** (<=30 articles) feeding governance only | Added (fixes missed events) |
| 2 - actions | dividends, splits, buybacks, announcements, 12 months | Yahoo Finance corporate actions (`--actions yahoo`, needs `yfinance`) | OK |
| 2 - prices | daily adjusted closes + Nifty, 6 months, context only | Yahoo Finance, context sheet only, never in a score | OK |
| 2 - provenance | every item carries source URL + date | yes; Google News URLs are redirect links to the publisher | OK (note) |
| 3 Classify | fixed taxonomy, sentiment -2..+2, one-line justification, governance flag | yes; taxonomy extended (routine governance types, `about_company`, `historical`, `materiality`) | OK (extended) |
| 3 cage (i) forced structure | rigid template or rejected | JSON schema enforced via `claude --json-schema`; falls back to validated text if unavailable (e.g. Windows `claude.cmd`) | OK / fallback |
| 3 cage (ii) extraction not recall | labels only text handed over | yes (headline, optionally body) | OK |
| 3 cage (iii) zero temperature | randomness off | **GAP**: the Claude Code CLI has no temperature setting. Repeatability relies on the label cache. API route (`--classifier anthropic`, key needed) does use temperature 0 | **GAP** |
| 3 cage (iv) full logging | every raw reply stored | `raw_model_responses.jsonl` + `work/claude_batches.jsonl` | OK |
| 4 Validate | valid type, sentiment range, URL, date in lookback; retry once; drop with reason; disclosed | yes + extra gates (`not_about_company`, `generic_market_headline`, `label_failed`) | OK (extended) |
| 5 Score - company sentiment | recency-weighted mean, half-life ~3 trading months | 60 trading days, shown as integer; unrounded kept | OK |
| 5 - sector sentiment | same aggregation per sector | yes | OK |
| 5 - governance | 100 minus fixed penalties; Clean/Watch/Flag | rubric from your reference report (+ routine-type no-penalty guard) | OK (refined) |
| 5 - corporate actions | rule table by type/direction/materiality | computed (assumed constants, xlsx only); the PDF lists actions factually as in your reference | OK / assumed constants |
| 5 - frozen rules | one versioned rubric file | `rubric/rubric_v1.json`, version + sha256 recorded per run | OK |
| 6 Report | scorecard + evidence rows per score + coverage sheet; no orphan scores | PDF (reference layout), xlsx evidence/coverage sheets, `audit/score_workings.csv`, `verify` command | OK |
| Repeatability | fixed route, deterministic labels, frozen rules, caching, audit trail | fixed route yes (stages overlap in time across stocks, never in order per stock); labels cached (disk) unless `--no-disk-cache`; audit trail + manifest | OK, except temperature |
| Quant notes | prices excluded from scores; relative mode winsorised +/-3 sigma z-score | yes (`--mode relative`) | OK |

## Things to decide
1. **Sentiment window.** The document's sentiment is a 12-month view. "Latest 100 articles" can be a few weeks for heavily covered names. Options: keep (your call),
   or raise `--max-articles`, or add a monthly-stratified pull back for sentiment.
2. **Temperature.** Only the API route can set it. Everything else about the cage is in place.
3. **Corporate-action score.** Constants are assumptions; the PDF does not use the score.
