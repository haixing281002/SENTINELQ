---
name: sentinelq-governance
description: Compute the Sentinel Q governance score (0-100, Clean/Watch/Flag) for a stock from dated events, showing the penalty arithmetic. Use when the user asks for a governance score, wants a governance check on a name, or asks why a stock is Watch/Flag.
---

# Sentinel Q governance scoring

Governance is arithmetic on labelled events, not judgement. Source of truth: `rubric/rubric_v1.json` -> `governance`.

| Event category (event_type) | Penalty |
|---|---|
| Auditor restatement (`auditor_restatement`) | -25 |
| Auditor resignation (`auditor_resignation`) | -20 |
| Regulatory action or settlement (`regulatory_action`) | -20 |
| Related-party-transaction concern (`rpt_concern`) | -12 |
| Live investigation overhang (`investigation`) | -10 |
| Promoter pledge (`pledge`) - assumed | -10 |
| Exchange fine / disclosure lapse (`exchange_fine`) | -5 low / -7 medium / -10 high |
| Management or compliance churn (`management_exit`) | -8 |
| Board-independence change (`board_independence`) | -8 |
| Historical (out-of-window) event | -5 once (memory discount; not if the same type is already penalised in-window) |

Start 100. Each event type is penalised **once** per stock (same story in many outlets must not stack).
Labels: **Clean >= 95, Watch 80-94, Flag < 80**. Only events with a dated source URL count.

## Penalty only for the adverse type (not look-alikes)
`management_exit` = UNPLANNED exit of a CEO / MD / CFO / any "Chief ... Officer" / Company Secretary / Compliance Officer.
Elevations, promotions, appointments, planned retirements and exits below CXO tier are `management_change_routine` (no penalty).
Likewise `rpt_routine`, `investigation_closed`, `board_change_routine`, `auditor_rotation` carry no penalty. List what was *not*
scored and why. Full rules: `docs/GOVERNANCE.md`. Governance is searched over the full 12 months, separately from the latest-N pull.

## Procedure
1. Gather the stock's events for the 12-month window (from a Sentinel Q run's `evidence.json`, or research them with
   WebSearch/WebFetch - real dated URLs only). Label each: `event_type` from the table, `governance_flag`, `historical`
   (event older than 12 months, mentioned as background), `materiality` (fines only). Extraction only - use the text.
2. Write them to a JSON list `[{"date","headline","event_type","governance_flag","historical","materiality","url"}]`.
3. Run `python scripts/score_governance.py events.json` - it prints each penalty and the final score/label. Never do the
   sum by hand or nudge it; if the result looks wrong, fix the labels or (visibly) the rubric.
4. Reply with: score + label, the arithmetic line (`100 -20 (regulatory action, 15-Jun-26) -8 (management exit) = 72 Flag`),
   each event with date and link, and what was *not* scored and why (routine items, out-of-window, no source).

Example: Angel One - SEBI settlement 15-Jun-26 (-20) + CPO resignation (-8) = **72, Flag**.

## Lessons (this step learns)
Read `LESSONS.md` in this folder first if it exists (accepted, human-approved lessons for this step). After a run, use the `sentinelq-learn` skill: `python -m sentinelq learn propose runs/<date>`. Never apply a lesson without the person accepting it.
