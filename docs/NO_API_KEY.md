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
