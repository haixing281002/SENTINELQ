# News mechanism extracted from Harshil's Nifty-50 sentiment script

Source file reviewed in full (1,583 lines). Only the news-pulling part was taken; its VADER scoring, price correlation and
long-only backtest are not used (Sentinel Q scores with the fixed rubric).

## What the original actually does
| Step | Original behaviour |
|---|---|
| Default source | **Google News RSS** (`--provider google_news`), not GDELT. GDELT, NewsAPI (needs a key) are options. |
| Query | `"<company name>"` as a phrase, plus a `when:<N>d` recency operator; `hl=en-IN&gl=IN&ceid=IN:en` |
| Parse | RSS XML `channel/item` -> `title`, `link`, `pubDate`, `<source>` publisher; drop the trailing ` - Publisher` from titles |
| Volume | newest 15 items per company (28-day lookback); ~1 s between companies |
| Text scored | title only (RSS has no body) |
| Failure handling | a failed company becomes a "NO DATA" row; the run continues |
| GDELT path (optional) | one request per company, `maxrecords=limit`, `sort=datedesc`, 4 tries, backoff `max(Retry-After, min(60, 8*2^n))`, >= 6 s between companies |

## What Sentinel Q took and what it added
`sentinelq/ingest/gnews.py` (self-contained; `python -m sentinelq.ingest.gnews "Angel One" --days 60 --limit 50`)
* same endpoint, phrase query, India edition, XML parsing and title clean-up as the original;
* **added:** company aliases as an OR query (`"Titan Company" OR "Titan Co"`), explicit `after:`/`before:` windows so a past as-of date
  works (the original's `when:Nd` is always relative to today), and **rolling back** through 30 / 90 / 180 / 365-day windows,
  newest first, stopping as soon as 100 articles whose title names the company are in hand;
* **added:** flat 20 s waits on a rate limit (max 3), a health check before the run, and it never reads a consent/block page as "no news";
* nothing is stored; Google News links are redirect pages, so those articles are read from headline + publisher + date only.

## Use
`sentinelq ... --news gnews` (default) or `--news gdelt`. Both roll back newest-first to the latest 100 usable articles.

## Not verified (no access to Google/GDELT from the build sandbox)
Live behaviour, and that Google honours `after:`/`before:` for this query (the code detects and reports it if it does not:
"date operator ignored?"). Google News RSS returns at most ~100 items per query.
