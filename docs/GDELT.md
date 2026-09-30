# GDELT: how Sentinel Q gets the news (free, nothing stored)

## The rule
For every stock, **roll the search window back from the as-of date, newest first, and stop as soon as we hold 50 usable
articles.** Those 50 are the *latest* articles. The maximum lookback stays 12 months: if fewer than 50 exist in 12 months we
use what there is (and mark the stock low confidence). "Usable" = not a market roundup that never names the company.

## Free, and nothing billed
Only the free GDELT DOC 2.0 API is used (`api.gdeltproject.org`). No Google Cloud, no BigQuery, no API keys, no billing.

## Why month-sized windows
GDELT's article-list mode only considers the most recent ~3 months of whatever window is requested (verified from GDELT's own
posts via search), and returns at most 250 per request. A single 12-month request would silently return ~3 months. So the
client asks windows of `--gdelt-slice-days` (default 30) from the as-of date backwards and stops as soon as enough usable
articles are in hand - typically 1-3 requests for a large cap.
Politeness: ~8 s between requests, exponential backoff on 429/5xx or GDELT's plain-text "please limit requests" reply
(honouring `Retry-After`), and it **raises** rather than reading a rate limit as "no news".

## Nothing is stored
The pipeline streams: **scrape stock N -> read its articles -> label -> validate -> release**, while the scraper is already on
stock N+1 (the slow GDELT waits overlap with model time). Article text is held in memory only and discarded after labelling.
* **Never written to disk:** article bodies, the scraped article lists, GDELT responses. (`--fetch-text` reads bodies in memory.)
* **Written (needed for the audit trail):** the report outputs - each evidence row is headline + date + URL + the model's label and
  one-line rationale (`evidence.json`, xlsx, PDF, `dropped.jsonl`), plus `work/run.log` and `work/claude_batches.jsonl` (ids and the
  first 300 characters of model replies - labels, not articles).
* **Label cache (`.cache/labels.jsonl`):** hash -> label only. It makes re-runs repeatable and lets an interrupted run skip
  already-labelled items. It contains no article text. Use `--no-disk-cache` to keep even this in memory (then nothing is
  cached anywhere; a re-run re-scrapes and re-labels everything).

## Trade-off to know about
Because nothing is cached, an interrupted or failed run re-scrapes from GDELT next time (labels are still reused unless
`--no-disk-cache`). If GDELT keeps rate-limiting a stock, the run stops before scoring and names it; re-run later, or pass
`--allow-missing-news` to accept the gap.

## If GDELT answers 429 (rate limit)
* Before scraping, one tiny **health-check request** runs. If GDELT is refusing this machine, the run stops within about a minute
  with instructions - it does not grind through all the stocks.
* If 3 stocks in a row fail, it stops (hammering a throttled service only makes the limit last longer). `--allow-missing-news` overrides.
* **Check for leftover runs first.** Stopping a task can leave the Python process alive; each old run keeps calling GDELT from your
  IP. PowerShell: `Get-Process python*, sentinelq* | Stop-Process -Force`. Run only one instance at a time.
* Then wait 10-15 minutes and re-run the same command.
