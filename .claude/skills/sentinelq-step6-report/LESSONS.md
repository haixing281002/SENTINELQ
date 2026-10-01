# Lessons - Step 6 Report

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-7a44b8b2 - When two implementations differ, take the union of verified evidence and re-score once
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Averaging or picking a side hides the real disagreement.
- change: `sentinelq merge` + portfolio/verified_events.csv; verified events reproduce the reconciled scores.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_merge_is_union_then_rescore_never_an_average`, `tests/test_reconciliation.py::test_verified_events_reproduce_the_reconciled_governance_scores`
