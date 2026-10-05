# Upgrade Workflow v2.1 - what was built and how to check it

Source: `Sentinel_Q_UPGRADE_WORKFLOW_v2.1.pdf` (1 Oct 2026). Two structural changes: **A** event-level, stratified evidence;
**B** corpus of record with replay. Rubric weights, taxonomy, output sheets and the cardinal rule (the model labels; Python scores)
are unchanged. Everything below is deterministic Python except the labelling call itself.

## Change A - event-level, stratified evidence

| Item | Where | What it does |
|---|---|---|
| A1 stratified sampler | `sentinelq/sampler.py`, `sentinelq/config/__init__.py` | Fixed windows back from as-of: W1 0-30 d (40), W2 31-90 (25), W3 91-180 (20), W4 181-365 (15). Usable headlines only (title names the company). Per-day cap 6 - the 7th and later ride along as extra sources of that day's cluster. Shortfalls roll to the next older window, then back to the newest. Budget stays 100. Results-pass and governance-pass hits are mandatory anchors outside the budget; results hits collapse to at most 4 results events (one per quarter). |
| A2 source tiers | `sentinelq/config/source_tiers.yaml`, `sentinelq/tiers.py` | T1 filings / wires / Reuters / Bloomberg (1.0), T2 national business press (0.9), T3 default (0.7), T4 aggregators (0.5). By domain only; edit the file to move a domain. Google News links carry the publisher in `<source>`, which is what is tiered. |
| A3 clustering | `sentinelq/cluster.py` | Stage 1 lexical union-find (<= 2 days apart and Jaccard >= 0.5 or >= 3 shared distinctive tokens). Representative = highest tier, then first report, then longest snippet. Stage 2 one model call per cluster. Stage 3 post-label merge: same event_type + same governance flag within 3 days = one event. `conf = min(1, 0.40 + 0.15 ln(1 + n_sources)) * tier_weight`. |
| A4 event scoring | `sentinelq/score.py` | Sentiment = sum(w_recency * conf * s) / sum(w_recency * conf) over sentiment + results events (price_move excluded, half-life 60 trading days unchanged). Governance: one penalty per verified event through the existing gate; an event with conf < 0.4 and no T1/T2 source is listed, not penalised, with the reason. Minimum evidence: >= 6 events, >= 2 windows, >= 1 results event, else Insufficient Data. Coverage map on every row: `W1:12 W2:5 W3:4 W4:3 · events 24 · results-type 9 (anchors 4/4) · T1/T2 share 61% · conf-weighted n 15.8`; a window with zero events is named in words. |

## Changes after the first live run (5 Oct 2026 card)

| Problem seen | Fix |
|---|---|
| The conf < 0.4 rule muted genuine penalties (Angel One CPO, HindCopper fines, BoM CCO, Nestle FSSAI): Google News collapses syndication so one link per event is normal | A gate-verified event is always penalised; low confidence is printed on the penalty ("single lower-tier source, conf 0.35 - verify against the filing"). Golden penalise items now carry single-T3-source fields so this cannot regress. |
| "In-force" verified events tied to 3-Jul were skipped silently in October | `standing` column (Bosch MNC discount = yes; status `convention` is standing); skipped verified rows printed on PDF page 1 and in RUN_RECORD |
| Sentiment compressed to 0 / +1 (16 / 13, no +2) | Rubric v1.5.0 `sentiment.aggregation = signal_events`: 0 = no signal, the mean runs over non-zero events (>= 3, else all-event fallback); +2 only with no material results-type offset. Weights unchanged. |
| Orderly successions scored as exits (Nestle CFO, Cummins MD) | "to step down / successor named / to take over" without abrupt wording = planned exit, not penalised, flagged to verify |
| One results event per quarter dropped guidance / order-win events | One results print per quarter + up to 3 other results-type events per quarter |
| Governance events found in one run and lost in the next (Angel One CPO, BoM RBI penalty) | Corpus union: governance events on record from earlier runs, visible at this as-of (B3), same prompt + model, join the run (`origin corpus:<run_id>`, disclosed on page 1). `--no-corpus-union` turns it off. |

Also: the governance search cap (30) is stratified over W1..W4 (9/8/7/6 with carry-forward) so a burst of recent appointment headlines
cannot push an older regulatory order out; `sentinelq learn propose` raises `clean_inflation` at >= 60 % Clean (mirror of `flag_inflation`);
and every card carries a deterministic "Label split vs the previous run" observation written by code, not the model.

## Holistic year, free filings, body text (rubric v1.6.0)

- **Weights:** half-life 60 -> 126 trading days with a 0.25 floor inside the lookback, so a January item carries ~0.3 of a September item's
  weight instead of ~0.05. Every scorecard row's coverage map now prints the sentiment WEIGHT share per window (`weight W1:34% W2:30% ...`).
- **Budget:** default 150 (60/40/30/20); the v2.1 document's 100 is `--budget 100`. Governance search cap 40 (12/11/9/8).
- **BSE announcements** (`sentinelq/ingest/filings.py`, free, no key): results and governance filings join the run as anchors, press releases
  as extra sentiment evidence, all tiered T1. `bse_code` in `portfolio/universe.csv`; the exchange's own company name is checked before
  any row is used. `--filings none` to switch off; `python -m sentinelq inspect filings --stock TITAN` to look.
- **Body text** is read (in memory, free) for results / governance / filing event representatives by default; Google News links are
  resolved to the publisher page; exchange PDFs are read from their first pages. `--fetch-text` reads everything; `--no-fetch-text` nothing.
- Sentiment starts at 0 and is the net weighing of every positive against every negative (v1.5.2), unchanged.

## Budget and stricter dropping (after the 5-Oct review)

- `--budget N` scales the four quotas keeping the 40/25/20/15 shape (200 -> 80/50/40/30); `--window-quotas a,b,c,d` sets the shape; the
  governance cap scales the same way with `--governance-cap`. The budget and shape are in the run stamp.
- **Boilerplate headlines** (`config.BOILERPLATE_PATTERNS`: share-price-today, buy/sell/hold, stocks-to-watch, live updates, record-date
  alerts, technical charts ...) are dropped before the model sees them (`boilerplate_headline`).
- **Same thing conveyed**: results-type events of the same type in the same quarter merge into one event (sources pooled), on top of the
  3-day post-label merge.
- **Model relevance** (prompt **v2**): the labeller answers `substance` = primary / passing / boilerplate. `boilerplate` is dropped
  (`model_boilerplate`); `passing` is kept for coverage but excluded from the sentiment mean. Prompt v2 invalidates cached v1 labels (B2):
  the next run re-labels every event once.

Rubric v1.5.2: both veto rules are OFF by default. Sentiment is anchored at 0 and is the net weighing of every positive against every
negative event (recency x confidence); a 0 label is no signal. The switches `plus_two_requires_no_results_offset` and
`minus_one_when_results_negative` stay in the rubric for the IC to turn on if results prints should override the net weighing.

Rubric v1.5.1 added the mirror of the +2 rule: when the results-type evidence (prints, guidance) is negative on balance (weighted mean
<= -0.5), sentiment is capped at -1 whatever the launches and broker notes say - the rubric's own "-1 = material deterioration dominates
the year even if partial offsets exist" (the Natco case). Without it an averaging method lets seven small positives outvote two bad prints.

Two gate refinements the golden set forced, both conventions rather than weights: a stated `minor` severity keeps the minor band
(-10) even under the Rs 10 cr floor (Natco NPPA, reconciled 82), and a notice on day 1 / probe on day 2-3 that shares two
distinctive words is one event even when the two headlines were typed differently (Nestle). Raise both with the IC if you disagree.

## Change B - corpus of record + replay

| Item | Where | What it does |
|---|---|---|
| B1 storage | `sentinelq/corpus.py`; tables under `audit/` | Append-only, never rewritten: `manifest/<run_id>.json`, `corpus/articles/`, `corpus/events/`, `corpus/verifications/`, `scores/` (parquet with pyarrow, else JSONL with the same columns). Headline + snippet (<= 300 chars) only; no body text. `--no-corpus` switches it off; `--corpus-dir` moves it. |
| B2 cache | `Corpus.label_index`, `Pipeline._classify_one` | An article is a `url_hash`. Seen again under the same `prompt_version` + `model_label` -> stored label, no model call. A prompt or model change invalidates labels, not articles; a rubric change invalidates scores only. |
| B3 point-in-time | `Corpus.visible_events`, `replay._visible` | Visible at as-of iff event_date <= as-of and stored by a run whose as-of <= as-of + 7 days. `backtest` is strict: a run *fetched* after that window is look-ahead and refused. Modes stamped on every output: `pit-replay`, `archive-gdelt`, `backdated-live-NOT-PIT` (red on the scorecard), `live-gnews`, `supplied`. |
| B4 commands | `sentinelq/replay.py`, `tools.py`, `cli.py` | `run`, `replay --as-of --rubric`, `relabel --as-of --prompt`, `backtest --from --to --weekly`, `diff --run A --run B`, `golden`. |
| B5 golden set | `audit/golden/governance_37.jsonl`, `sentinelq/golden.py` | 13 penalise + 15 reject + 9 recall from the Reconciliation. Runs inside every run and stamps `published=false` when it fails; `python -m sentinelq golden` exits non-zero. Recall items are checked against the corpus once it holds articles for that stock and date. |
| B6 stamps | `pdf.py`, `report.py`, `audit.py` | Scorecard header: source_mode · as_of · universe hash · prompt v · rubric v+hash · model_label · model_verify. Every row: coverage map + event count. Every evidence row: n_sources, max_tier, confidence. Every penalty: its verification record; every unpenalised governance event: why. `Delta` sheet computed from `scores/` of the previous run, never from news. |

## Commands

```
python -m sentinelq run --portfolio portfolio/stocks_given.tsv --as-of 2026-10-06 --news auto     # weekly run; writes all tables
python -m sentinelq replay   --as-of 2026-07-03 --rubric rubric/rubric_v1.json                 # rescore from stored events; no network, no LLM
python -m sentinelq relabel  --as-of 2026-07-03 --prompt v1                                    # re-label stored headline+snippet; LLM only
python -m sentinelq backtest --from 2026-10-06 --to 2027-03-30 --weekly                        # pit-replay at each weekly as-of
python -m sentinelq diff     --run runs/2026-10-06 --run runs/replay_2026-07-03                # score + label diff, drivers listed
python -m sentinelq golden                                                                     # 37/37 or non-zero exit
```

## Acceptance tests (tests/test_upgrade_v21.py)

A5.1 no window > 70 % · A5.2 same-day flood -> one event, n_sources >= 10 · A5.3 notice + probe -> one event · A5.4 results anchor /
absence reported · A5.5 determinism · A5.6 model calls fall >= 50 % vs latest-N · B7.1 run == replay · B7.2 a changed weight moves
only rows carrying it · B7.3 no look-ahead in backtest · B7.4 golden gates publication · B7.5 second run same day = zero model calls ·
B7.6 never pit-replay before the corpus start. A5.2 / A5.3 run on synthetic feeds here; on the live universe they are checked by
`diff` against the reconciled 3-July card after the first full run.

## Still open

- The first full 30-name live run and its `diff` against the reconciled 3-July card have to be done on a machine with news access.
- BSE/NSE filings are not a source yet (recall items G29-G37 rely on press coverage of filings).
- `relabel --prompt vX` records the version label; the prompt text itself lives in `classify.py` (`PROMPT_VERSION`).
