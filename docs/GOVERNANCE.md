# Governance scoring: what counts, and how events are found

Governance is a 12-month rubric (start 100, fixed penalties, Clean >= 95 / Watch 80-94 / Flag < 80). Sentiment is unchanged and is
computed only from the latest-N pass.

## 1. Finding the events (the 12-month governance pass)
The latest 100 headlines for a busy stock can span only a few weeks, so an event from months ago (e.g. Angel One's Chief Product
Officer resignation) would never be read. Each stock therefore gets a **second, independent search over the whole 12 months** using
leadership and regulatory keywords (resigns / steps down / CFO / company secretary / SEBI / RBI / penalty / probe / raid / auditor /
related party / independent director / pledge / fraud / default ...). Up to `--governance-cap` (30) new articles whose title carries
a governance keyword are kept, de-duplicated against the sentiment set, labelled like any other article, and used for **governance
scoring only** (never sentiment). Skip with `--no-governance-pass`.

## 2. Telling a penalty from a routine item (the literal-rule misfires)
Penalties apply only to the **adverse** event types. Routine look-alikes have their own types with **no penalty**:

| Penalised (rubric) | Routine look-alike (no penalty) |
|---|---|
| `management_exit` = UNPLANNED exit of a CEO / MD / CFO / any "Chief ... Officer" / Company Secretary / Compliance Officer (-8) | `management_change_routine` = elevation, promotion, re-designation, new appointment, planned retirement / succession, exits below CXO tier (Head of function, VP, director) |
| `rpt_concern` = material or investor-contested related-party transaction (-12) | `rpt_routine` = routine / approved / arm's-length RPT |
| `investigation` = unresolved probe (-10) | `investigation_closed` = concluded / quashed / cleared |
| `board_independence` = combined roles, independent-director gaps (-8) | `board_change_routine` = ordinary board changes |
| `auditor_resignation` / `auditor_restatement` (-20 / -25) | `auditor_rotation` = scheduled rotation / reappointment |

Two layers enforce this:
1. **The classifier prompt** defines each type with examples ("X appointed as Head - Sales and Marketing" is *never* a management exit).
2. **A deterministic safety net in scoring**: even if the model labels something `management_exit`, no penalty is applied when the
   headline is an elevation / appointment / planned retirement with no exit wording, or when no CEO/MD/CFO/Chief...Officer/CS/compliance
   role is named. The item is **kept in the report as "NOT scored: <reason>"** (xlsx Governance sheet and the commentary), so nothing is
   hidden - a person can overrule it.

Known cases this fixes: Dr. Reddy's "Kunwar Khurana as Head - Sales and Marketing" (elevation), and the same pattern at AIA Engineering
and Nestle. Angel One's CPO exit **is** an unplanned CXO exit and is penalised (-8).
