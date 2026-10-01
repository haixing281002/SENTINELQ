---
name: sentinelq-step4-validate
description: Step 4 of the Sentinel Q pipeline - schema validation of every labelled item (valid type, sentiment range, source URL, date in lookback); failures retried once then dropped with a reason.
---

# Step 4 - Validate
**Document says:** every labelled item is checked - valid event type, sentiment in range, non-empty source URL, date inside the lookback. Failures retried once, then dropped
with a logged reason - never silent, never passed through. Dropped counts appear in the coverage disclosure.

**Code:** `sentinelq/validate.py::validate_label`; the retry/drop loop is `pipeline.py::_label_stock`. Extra, disclosed gates: `not_about_company` (model judges relevance),
`generic_market_headline` (roundup that never names the company, set aside before any model call), `label_failed:<reason>` (model gave nothing - never mislabelled as irrelevant).

**Failure gates:** >10% label failures stops the run before any report (override `--allow-partial-labels`).
**Inspect:** `python -m sentinelq inspect label ...` prints PASS or `DROP: reason`.
**Manually verify:** `runs/<date>/dropped.jsonl` (every drop + reason), Coverage sheet of the xlsx, `RUN_RECORD.md` Step 4 table, `label_failures.jsonl` if any.
