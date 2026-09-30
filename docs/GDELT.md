# GDELT: where the articles are, and how Sentinel Q gets them

## The rule we follow
For every stock, **roll the search window back from the as-of date, newest first, and stop as soon as we hold 50 usable
articles.** Those 50 are the *latest* articles. The maximum lookback stays 12 months: if fewer than 50 exist in 12 months
we use what there is (and say so - `low confidence`). "Usable" = not a market roundup that never names the company.

## Where GDELT keeps things (verified from GDELT's own posts via search; the blog itself is blocked in the build sandbox)
| Place | What | Limits that matter |
|---|---|---|
| **DOC 2.0 API** `api.gdeltproject.org/api/v2/doc/doc` | live article search (title, URL, domain, date) | **article-list mode only considers the most recent ~3 months of whatever window you ask for**; max 250 results per request; ~1 request / 5 s / IP, else 429 |
| **BigQuery public dataset** `gdelt-bq.gdeltv2` | full history; table `gkg_partitioned` = one row per article: `DocumentIdentifier` (URL), `SourceCommonName`, `V2Organizations` (entities + char offsets), `V2Tone`, `Extras` (holds `<PAGE_TITLE>` since Sep 2019 and `<PAGE_PRECISEPUBTIMESTAMP>`), `DATE` | billed per bytes scanned; use the `_PARTITIONTIME` filter to prune; you need a Google Cloud project + login |
| Raw files `data.gdeltproject.org/gdeltv2/` | the same GKG as 15-minute CSVs | far too heavy for this use |

### Consequence for accuracy
A single 12-month DOC API request silently returns ~3 months. Sentinel Q therefore asks **month-sized windows** (`--gdelt-slice-days`,
default 30), newest first, merges them, and stops once 50 usable articles are collected. Typical large caps need 1-3 windows.

## Two routes
1. `--news gdelt` (default): DOC API, rolling back month by month. No login. Paced (~8 s/request), backs off on 429, retries,
   and **raises** rather than reading a rate limit as "no news".
2. `--news gdelt-bq` (recommended for accuracy): BigQuery, one query for the whole portfolio and the full 12 months.
   * An article is kept for a company only if the company (exact name/alias, from `portfolio/universe.csv` `aliases`) is in its
     organisation list **and** (it is in the page title **or** it is mentioned >= 2 times). A roundup listing 30 stocks once never
     qualifies. Title-in-headline and mention count become the relevance rank.
   * Cost safety: a **dry run** prints GB scanned and an estimated $ first; it stops if above `--bq-max-gb` (default 300);
     the real job is hard-capped with `maximum_bytes_billed`; the result is cached in `.cache/bq/` so re-runs cost nothing.
   * Setup (once): `pip install -e ".[bq]"`, install the Google Cloud SDK, `gcloud auth application-default login`,
     then `--bq-project <your-project-id>` (or env `GOOGLE_CLOUD_PROJECT`). In Google Colab: `from google.colab import auth; auth.authenticate_user()`.

```bash
sentinelq --portfolio portfolio/stocks_given.tsv --news gdelt-bq --bq-project my-project --max-articles 50 --fetch-text ...
```

## What is verified vs assumed
* Verified (GDELT posts, via search): the ~3-month artlist behaviour, 250-per-request max, `gkg_partitioned` and its columns, `_PARTITIONTIME`
  pruning, `PAGE_TITLE` in `Extras` since Sept 2019.
* Not verified here (no network to GDELT/Google from the build sandbox): live query behaviour, exact bytes a 12-month scan costs
  (the dry run tells you before anything is billed), and the BigQuery free-tier allowance on your account.
