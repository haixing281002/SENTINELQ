---
name: sentinelq-report
description: Produce or refresh SentinelQ's two deliverables - sentinelq_report.xlsx and sentinelq_scorecard.pdf - for a named list of stocks. Use whenever the user asks for the SentinelQ Excel/scorecard, a re-run, or wants the numbers/PDF updated after a rubric or scoring change. Covers both a full run (fetch + label from scratch) and the fast rescore-only path (same evidence, new rubric) - pick the right one before running anything.
---

# SentinelQ report generation

Two different jobs; picking the wrong one wastes minutes to hours. Decide first, then run.

| Situation | Command |
|---|---|
| New stocks, or the news/evidence itself needs refreshing | **Full run** |
| Only `rubric/rubric_v1.json` or scoring code changed; the stocks already have a run folder | **Rescore** |

If it's unclear which one the user wants, ask rather than guess - a full run on a stock that's already been run today mostly just burns the labelling budget relabelling the same articles.

## Before running anything
- `git status` - know what rubric/code changes are actually live.
- Confirm the exact stock list with the user. **Never** add a stock they didn't name, and never reuse an old/example/default portfolio file. Build a fresh `portfolio/<name>.csv` (header: `symbol,name,sector,cap,weight`) with only the named stocks, and pass `--expect-count <N>` matching the row count - this is a hard safety check, not decoration.
- `ls ~/.vscode/extensions | grep claude-code` to get the **current** native-binary path. The extension auto-updates its version folder mid-session (seen `2.1.289` -> `2.1.292` on this project) - a path remembered from earlier in the conversation, or from memory, can be stale. Always re-check.

## Full run

```bash
export CLAUDE_BIN="C:/Users/harshil.pahuja/.vscode/extensions/anthropic.claude-code-<version>-win32-x64/resources/native-binary/claude.exe"
.venv/Scripts/sentinelq.exe --portfolio portfolio/<name>.csv --title "<title>" \
  --coverage "<N> holdings" --expect-count <N> --out runs/<date>_<name>
```

Fetches news, reads article text, labels every article with AI, scores, writes commentary, and builds both files. This is the slow path - minutes for one stock, an hour or more for dozens, since every uncached article costs a model call. Always launch with `run_in_background: true` and watch the log with Monitor (grep for `EXIT|wrote|Traceback|not found`) rather than blocking on it.

## Rescore only (fast)

```bash
.venv/Scripts/sentinelq.exe rescore runs/<existing_run_folder>
```

Re-reads the saved evidence and labels from that run folder, recomputes Sentiment/Governance/Corporate Actions **deterministically** under whatever rubric is on disk right now (no news fetch, no article relabelled), refreshes the short per-stock and portfolio commentary (a couple of fast model calls, not a full relabel pass), and rewrites both `sentinelq_report.xlsx` and `sentinelq_scorecard.pdf` in place. A one-stock rescore takes roughly a minute, not the 10-20 minutes a full run needs.

Add `--no-narrate` to skip even the commentary refresh (pure numbers, instant) - use this only if the commentary text itself doesn't need to reflect the change (e.g. you're just sanity-checking a score).

**CLAUDE_BIN still has to be set** even for rescore, since commentary generation still goes through the `claude` CLI - only the labelling step is skipped.

## Known gotchas

- **Locked Excel file.** If `sentinelq_report.xlsx` is open in Excel when a run tries to write it, the pipeline writes a timestamped sidestep copy instead (e.g. `sentinelq_report_135855.xlsx`) and prints a warning rather than failing the run. Tell the user to close the file before the next run, and delete the stray timestamped copy once you've confirmed the real file wrote correctly.
- **Two different "coverage" fields - do not conflate them.** `run_meta.json`'s `"coverage"` key is a *list* of per-stock retrieval stats (retrieved/kept/dropped counts) - it exists only to feed the Excel Coverage sheet. The PDF title page wants a short *string* like `"8 holdings"` (or nothing - it falls back to `"N holdings"` automatically). Passing the stats list where the PDF expects the string renders the raw Python list on page 1 - this happened once before the `rescore` command existed to handle it correctly. If you ever write a one-off script instead of using `rescore`, keep these two apart exactly the way `sentinelq/tools.py::rescore()` does.
- **Prompt version bumps invalidate the label cache on purpose.** If `PROMPT_VERSION` in `sentinelq/classify.py` changed recently, a "rescore" won't be enough - the saved labels don't have the new fields the rubric now needs, and a full run is required to relabel. Check `sentinelq/classify.py`'s `PROMPT_VERSION`/`PROMPT_REVISION` against what's recorded in the run's `run_meta.json` (`prompt_version`) if a rescore's numbers look wrong for a reason that shouldn't be possible from pure rubric arithmetic.

## Reference docs

- `SentinelQ_Scoring_Pipeline_Reference.pdf` - the full technical reference (every rubric field, every formula, a real worked example). Regenerate it after a rubric change with `docs/generate_reference_pdf.py` (reads `rubric/rubric_v1.json` and whichever run folder it's pointed at for the worked example - edit the `run =` path at the top of the script first).
- `docs/generate_reference_pdf.py` - also a usable template for building other reportlab-based PDF documents in this project's house style (same colour palette, table helpers, heading styles).

## The six pipeline steps are separate skills

`sentinelq-step1-input` through `sentinelq-step6-report`, plus `sentinelq-governance` and `sentinelq-audit` for manual verification, and the top-level `sentinelq` skill for a full end-to-end run. This skill is specifically about the **output step** - producing or refreshing the two deliverable files - not the earlier pipeline stages.
