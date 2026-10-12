# SentinelQ changelog

Human-readable history of the pipeline and its scoring rubric, so a run from months ago can be understood without
re-reading every commit. Entries are grouped by rubric/pipeline version where one exists; `rubric/rubric_v1.json`'s
own `"version"` field is the authoritative version for the *scoring* (what number a given run used is always
recorded in that run's `run_meta.json` → `rubric_version` / `rubric_sha256`, so two runs can be diffed exactly even
within the same version string). Dates are when the change was committed, not necessarily when it shipped in a run.

**Maintenance note for whoever (human or Claude) makes the next pipeline/rubric change**: add an entry here in the
same turn as the change, under a new dated bullet (or a new version section if the rubric's `version` field moved).
Bump `rubric/rubric_v1.json`'s `"version"` whenever a change alters what a score actually comes out to for some
real input, not just wording/formatting. A changelog that falls behind the code is worse than no changelog, because
it reads as authoritative while being wrong.

---

## v2.1.0 — 2026-10-11 to 2026-10-12 (rubric version bumped as part of this entry)

**Scoring rubric changes:**
- Corporate Actions: an action no longer gets scored once it has *completed* — removed the `completed: 1.0` stage
  multiplier entirely. A completed action carries no forward entry/exit decision, so there's nothing left to score;
  it's still logged (shown in the CA events list, excluded from the Risk/Opportunity arithmetic) rather than
  disappearing. Gate A event types (insolvency admission, pledge invocation, integrity-severity auditor
  resignation) are explicitly exempt — a *completed* insolvency or an executed pledge invocation is exactly when
  the hard-exit override matters most, so those still score/override regardless of stage. *(13333c7)*
- Governance: fixed a bug where `occurred_at_company is False` unconditionally vetoed a penalty, even for
  promoter/subsidiary-subject events that are *supposed* to be eligible (subject_ok already allows them — a
  promoter pledge, by definition, happens at the promoter, not "at" the company, so this check was silently voiding
  that carve-out for nearly every real case). Verified impact on the live 19-stock batch: zero score changes (the
  affected events were all independently low-materiality or hit a separate, correct "routine filing" exclusion
  anyway) — but a future case with a medium/high-materiality promoter pledge would have been wrongly skipped.
  *(36f8e92)*

**Report/PDF overhaul** (scores unchanged, presentation and commentary rewritten):
- Scorecard table: full column names, every score shown directly (sentiment raw *and* weighted, governance,
  corporate actions), colour-coded by band; dropped the old "Key corporate action"/"One-line read" columns.
- All scores now render to one consistent decimal place across PDF and Excel (previously a mix of rounded whole
  numbers, 2dp, and unformatted floats that could hide real differences between two close scores).
- Hardened the PDF's "Coverage" field so the per-stock retrieval-stats list can never render as raw Python output
  on the page (it belongs on the Excel Coverage sheet only) — this was the literal cause of a "gibberish" artifact
  a user spotted on an early report.
- Narration rewritten from a news-recap style to an "explain, not list" style: each rationale states the mechanism
  behind the score (what drove it, what offset it) in plain, formal language with a couple of well-chosen
  supporting figures, calibrated to how decisive the actual read is (a near-neutral sentiment score now reads very
  differently from a landslide one — previously every holding got the same "strong growth, offset by X" template
  regardless of how close the real balance was). Governance and Corporate Actions severity also calibrate language
  intensity to the band (a Flag/Exit-tier item is now written with real weight; a Clean/Log one stays brief).
- No dashes (em dash, en dash, or spaced hyphen) anywhere in generated commentary — both a prompt instruction and a
  deterministic regex cleanup, since the prompt alone wasn't reliable.
- Gate B events (splits/bonuses, mergers/acquisitions, demergers, rights issues, special dividends, ISIN changes)
  are now *flagged* in the CA commentary — positive, negative, or mixed — instead of being silently excluded just
  because they don't feed the Risk/Opportunity number. This went through three iterations after a user-reported
  "major bug": a first audit using literal keyword matching was itself wrong (flagged real coverage as missing
  because the model used accurate paraphrases like "joint venture" instead of "merger"); a looser headline-overlap
  check fixed that but introduced a *different* false-negative (a single coincidental generic word match produced
  a false "already covered"); the final version requires 2+ distinctive overlapping words and excludes generic
  finance vocabulary and the company's own name from counting as evidence. It's applied as a deterministic
  code-level backstop *after* the commentary word-cap, not a hope that the model remembers every time.
- Reliability fixes found while generating a 19-stock batch: `extract_json()` used to find the model's first `{`
  through the *last* `}` in the whole reply, so any trailing text after the real JSON broke `json.loads` and took
  the whole labelling/narration batch down with it — switched to parsing exactly one JSON value from the first
  opener. A word-cap safety net used to hard-cut mid-sentence, which once produced a holding's sentiment rationale
  ending "...but on." — fixed to only cut on sentence boundaries. The narrator now retries once if a reply comes
  back as valid JSON with required fields present but silently empty (caught live: one holding's CA/governance
  rationale, one-line read, and key corporate action all came back as `""` after a long sentiment rationale, with
  no error).

**New deliverable**: `SentinelQ_Scoring_Mechanism_Brief.pdf` (`docs/generate_client_scoring_brief.py`) — a 3-page
client/leadership-facing explainer (mechanism flowchart, the three scores with bands, an illustrative example,
boundaries) plus a rubric appendix (every governance penalty, every CA severity/stage/timing/size value, Gate A/B),
values read live from `rubric_v1.json` so it can't drift out of sync. Distinct from the older, internal
`SentinelQ_Executive_Brief.pdf` (prototype-to-production pilot pitch, not a methodology explainer).

**Known, documented gap (not fixed, left as-is by user decision 2026-10-12)**: runs default to
`filings_source: None` — news from Google News RSS, actions from Yahoo Finance only. A small/niche corporate
action (e.g. a sub-₹2-crore acquisition) disclosed only via a BSE exchange filing can be invisible to both and
never appear in a run's evidence at all. `sentinelq/ingest/filings.py` (free BSE announcements API) is a real,
working fix for this, just not the default — pass `--filings bse`.

---

## v2.0.0 — 2026-10-09 *(1282a93 and same-day follow-ups)*

- Sentiment: raw + weighted means, both banded directly on the −2..+2 scale with no ×50 scaling anywhere (earlier
  versions scaled to points internally; this version reads the same units the model's labels already use).
- Full Corporate Actions rubric: `severity × stage × timing × size`, capped at 10 per event; Risk and Opportunity
  combined separately via `1 − product(1 − score/10)` (chance at least one bites), never summed or averaged, so one
  severe event is never diluted by several mild ones. Bands: Log/Watch/Review/Act/Exit. Gate A (hard override to
  Exit) and Gate B (data quarantine for structure-changing events) introduced. Replaced the older materiality-clip
  model entirely.
- One article can now feed Sentiment + Corporate Actions + Governance at once — scoring eligibility is no longer
  gated by which search pass originally retrieved the article.
- `sentinelq rescore` added: the fast path from "rubric changed" to a fresh Excel + PDF, re-scoring from already-
  fetched evidence without re-fetching or re-labelling anything (minutes instead of the 10-20+ minutes a full
  per-stock run costs).
- Per-stock PDF commentary rule introduced: explain in words only, never restate a score; quarantined (Gate B)
  actions kept out of the written report entirely at this point (later reversed in v2.1.0 above, once the product
  need shifted from "don't double-count" to "don't hide").
- `SentinelQ_Executive_Brief.pdf` added (internal, leadership pilot-decision pitch).

## v1.6.0 — 2026-10-05

- Half-life changed to 126 trading days with a 0.25 recency floor (later re-tuned again, see sentiment's own
  `recency_note` in the rubric for the full before/after).
- Free BSE corporate announcements added as a second evidence source (results/governance anchors, press releases),
  keyed by `bse_code` in `portfolio/universe.csv`; `sentinelq inspect filings --stock X` to preview what the
  exchange returns for one company.
- Full article body text read by default for results/governance/filing-type events (previously headline+snippet
  only for most items).

## v1.5.0 – v1.5.2 — 2026-10-05

- v1.5.0: signal-event sentiment aggregation (zero-scored articles excluded from the mean, so +2/−2 can actually
  surface instead of being diluted toward 0 by routine noise).
- v1.5.1: results-negative dominance rule — sentiment capped at −1 when results-type evidence is negative on
  balance, mirroring the existing +2 anchor rule.
- v1.5.2: sentiment redefined as the net weighing of all events anchored at 0 (not a running score that drifts);
  both results-veto rules switched off by default, kept as an IC-configurable switch rather than a fixed rule.

## v1.1 and the initial build — 2026-09-30 to 2026-10-01

- First working six-stage pipeline (Input → Ingest → Classify → Validate → Score → Report), IC scorecard PDF
  matching a target reference layout, no-API-key labelling via the local `claude` CLI, GDELT/Google-News-RSS
  retrieval with retries and rate-limit backoff.
- Governance: 12-month search pass, routine-vs-adverse event taxonomy, deterministic guard against misreading an
  elevation or a planned exit as an adverse departure.
- The seven "Reconciliation" fixes (2026-10-01, c8ac8f7): governance verification gate + written subject/stage
  conventions, same-event de-duplication, a 12-month results-print pass, a backdating-honesty policy (a backdated
  live search is never presented as a point-in-time backtest), universe lock (an unconfirmed universe change
  refuses to run), price-language lint (share-price moves are stripped from sentiment reasoning), and a minimum
  evidence standard (below it, sentiment reports "insufficient data" rather than forcing a number).
- Workflow v2.1 upgrade (9865c1b): stratified event-level sampling, source-tier/cluster-aware confidence weighting,
  a coverage map per stock, "corpus of record" with replay/relabel/backtest/diff tooling, a 37-item golden
  regression gate, and honesty stamps on every run (so a run can't silently claim to be something it isn't).

---

*For the full commit-by-commit detail behind any entry above, `git log` on
`github.com/haixing281002/SENTINELQ` (branch `claude/adoring-carson-nbhgi1`) is the source of truth — this file is
a curated summary, not a replacement for it.*
