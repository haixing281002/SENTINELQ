# Running Sentinel Q without an API key

There are two ways. Neither needs `ANTHROPIC_API_KEY`.

## A. `--classifier claude-code` (default, fully automatic)
Uses the `claude` CLI (Claude Code) you are already logged into. Install/login once: <https://claude.com/claude-code>.
```bash
sentinelq --portfolio portfolio/qvm_portfolio.csv --max-articles 50 --fetch-text \
  --title "QVM Portfolio" --coverage "30 holdings" --as-of 2026-07-03
```
* Items are labelled 12 per call, 3 calls in parallel; every item still gets its own label, validated and cached.
* Prose in the PDF (rationales, one-line reads, observations) is written by the same route.
* `--model sonnet|opus|<id>` chooses the model. Note: `claude -p` has no temperature setting, so repeatability comes from
  the label cache (`.cache/labels.jsonl`): once labelled, an article is never re-labelled. Keep that file between runs.
* Uses your Claude Code plan's usage allowance. 30 companies x 50 articles is ~1,500 items = ~125 calls.

## B. `--classifier file` (hand-off; you or any Claude Code session labels)
```bash
sentinelq ... --classifier file        # 1) ingests, writes work/pending_items.jsonl, exits with code 2
# 2) label the items (below) into work/labels.jsonl
sentinelq ... --classifier file        # 3) same command again: validates, scores, writes the PDF
```
To label, open the repo in Claude Code / VS Code and say:
> Read work/pending_items.jsonl. For each line write one JSON line to work/labels.jsonl:
> {"id": <copied>, "event_type", "sentiment", "rationale", "governance_flag", "about_company", "historical", "materiality"}
> following the schema and rules in `sentinelq/classify.py` (SYSTEM prompt + `tool_schema`). Use only the text given.

Prose is optional: without `work/narratives.json` the PDF uses deterministic template prose and lists the missing ones
in `work/pending_narratives.jsonl`. Fill `narratives.json` as `{SYMBOL: {sentiment_rationale, governance_rationale,
one_line_read, key_corporate_action, coverage_note}, "_observations": [{title, body}]}` to replace it.

## Quality controls (both modes)
* **Relevance gate:** each item carries `about_company`; items about a different entity are dropped and logged
  (`not_about_company`) - important for names like Titan or Bosch.
* **`--fetch-text`:** fetches article body text so labels are not headline-only. Off by default (slower).
* Score arithmetic, validation, and the audit trail are identical to the API path; only the labeller differs.

## Windows notes
* The npm install creates `claude.cmd`; the tool finds it automatically (also via `CLAUDE_BIN`, `%APPDATA%\npm`).
  If not found it stops within seconds with instructions - it no longer runs for 10 minutes first.
* Prompts are sent as UTF-8, so company names / symbols never break on the Windows code page.
* Fetched articles are cached in `.cache/ingest/` and `.cache/fulltext.jsonl`, so a failed or repeated run does not
  re-download them. Labels are cached in `.cache/labels.jsonl` only when the model actually returned one.

## If labelling fails (`label_failed`)
* Every claude call is logged to `work/claude_batches.jsonl` (ids, error text, first 300 chars of each reply).
* Failed batches are retried, then split in halves down to single items, so one bad item or reply cannot sink a batch.
  Rate-limit/usage errors back off (30s, 60s, 120s); if they persist the run stops early and keeps what it has.
* Labels are written to `.cache/labels.jsonl` **as they arrive**; re-running the same command only retries the rest.
* Items the model could not label are reported as `label_failed: <reason>` (never as "irrelevant"), listed in
  `label_failures.jsonl`, and counted on the Coverage sheet. If more than 10% fail the run refuses to write a report
  (override with `--allow-partial-labels`). Tune with `--batch-size 4 --workers 1` if you keep hitting limits.

## Following a long run (progress display)
The run prints six stage banners and, for every stock, what it found and how it will be read:
```
[ 3/29] DRREDDY  Dr. Reddy's Laboratories  (Healthcare - Large)
        news    ████████████████████ 50 articles (capped from 73)  [6.2s]
        styles  market-roundup 14 | general-news 12 | broker-note 9 | results 6 | press-release 5 | exchange-filing 4
Labelling ████████░░░░░░░░░░░░░░░░ 142/338  ok 140  failed 2  now: DRREDDY  ETA 6m12s
    DRREDDY  broker-note   full-text 1.8k > analyst_action  +1      "Dr Reddy's upgraded by Jefferies, target..."
    DRREDDY  exchange-filing headline    > regulatory_action -2 GOV  "USFDA issues observations at Srikakulam"
  ✔ DRREDDY finished labelling
```
* **Style** = what kind of article (exchange-filing, press-release, market-roundup, broker-note, results,
  corporate-action, opinion-feature, business-news, general-news). Descriptive only; it never feeds a score.
* **Parse** = how it was read: `full-text 1.8k` (body fetched with `--fetch-text`) or `headline` (title only).
* Then per stock: kept vs dropped with reasons, then the score table, commentary progress and output paths.
* `--progress auto|live|plain|off`: `live` redraws one bar line (real terminal); `plain` prints line by line (used
  automatically when output is captured, e.g. inside Claude Code). `--ascii` for terminals that garble block characters.
* Every run also writes `work/run.log` (plain text) and `work/progress.json` (machine-readable snapshot). From a second
  terminal: `tail -f work/run.log`. Inside Claude Code, ask it to run the command in the background and tail that log.
