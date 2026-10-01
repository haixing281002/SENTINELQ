---
name: sentinelq-step3-classify
description: Step 3 of the Sentinel Q pipeline - the caged AI step where the model labels each article. Use to test how a headline is labelled, or to explain/inspect the labelling rules and their limits.
---

# Step 3 - Classify (the only AI step)
**Document says:** the model labels text it is handed: fixed event taxonomy, ordinal sentiment -2..+2, one-line justification grounded in the text, governance flag.
Four cage bars: forced structure, extraction not recall, zero temperature, full logging.

**Code:** `sentinelq/classify.py` (system prompt, taxonomy, `batch_schema`), `sentinelq/claude_code.py` (no API key: local `claude -p`, 8 per call, schema enforced with
`--json-schema`), `sentinelq/handoff.py` (file hand-off mode), cache in `sentinelq/cache.py`.

**Cage bars - honest status:** structure ENFORCED by JSON schema (falls back to validated text if the flag is unavailable, e.g. the Windows `claude.cmd` shim);
extraction only - YES; **temperature 0 - NOT possible through the Claude Code CLI** (repeatability comes from the label cache instead); logging - every raw reply in `raw_model_responses.jsonl`.

**Governance types matter:** `management_exit` = unplanned exit of CEO/MD/CFO/any Chief...Officer/CS/compliance officer; elevations, appointments, planned retirements and sub-CXO
changes are `management_change_routine` (no penalty). See `docs/GOVERNANCE.md`.

**Run / inspect one headline:** `python -m sentinelq inspect label --company "Dr. Reddy's" --headline "..." --date 2026-06-01`  (shows the label and the Step 4 verdict).
**Manually verify:** `runs/<date>/raw_model_responses.jsonl`, `dropped.jsonl`; labels in `evidence.json`. Never edit a label to change a score - fix the rubric or the prompt, visibly.
