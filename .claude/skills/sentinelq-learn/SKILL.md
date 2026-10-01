---
name: sentinelq-learn
description: Sentinel Q learning loop - after a run, propose lessons per pipeline step, let a person accept or reject each, and turn accepted ones into a step-skill note, a regression test and (optionally) a versioned rubric patch. Use after any run, after a reconciliation, or when a score looks wrong.
---

# Learning loop (human-approved; the pipeline never edits its own rules)
Every step skill has a `LESSONS.md` (what that step has learned). Read it before running or explaining the step. Registry: `lessons/registry.json`, overview `lessons/INDEX.md`.

**1. Propose (changes nothing):** `python -m sentinelq learn propose runs/<date> [--against runs/<other>] [--verified portfolio/verified_events.csv]`
Finds: appointments penalised as exits, small sums with heavy penalties, collapsed evidence windows, insufficient data, Flag inflation, compressed sentiment, loose queries, label failures, price language, verified events the model missed, disagreement between runs.

**2. Review:** `python -m sentinelq learn review [--status all]` - show each proposal with evidence to the person. Never accept on their behalf.

**3. Accept / reject (only when the person says so):**
`python -m sentinelq learn accept <id> --note "why" [--expect-penalty 0|-5|...] [--rubric-patch '{"governance":{...}}']`
-> records the lesson in the right step's LESSONS.md, generates `tests/lessons/test_lesson_<id>.py` (when the lesson has a golden case) and, if given, merges the patch into `rubric/learned_patch.json` (hash-covered; `learned_from` lists the lessons; every run records it).
`python -m sentinelq learn reject <id> --reason "..."` -> never proposed again.

**4. Prove it:** run `pytest -q`; the new regression test must pass. Scores change only through an accepted rubric patch, visibly.
Other: `learn status`, `learn render`, `learn seed` (the 12 reconciliation lessons).

Rules: proposals are suggestions, not changes; never edit `learned_patch.json` by hand; sentiment scoring is not touched by a lesson unless the person explicitly approves a patch.
