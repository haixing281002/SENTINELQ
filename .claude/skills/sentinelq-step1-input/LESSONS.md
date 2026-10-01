# Lessons - Step 1 Input

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-e694e2dd - A holding must never appear or disappear silently
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- JB Chemicals vanished from the team run's universe (30 -> 29).
- change: Universe hash locked against the last confirmed run; --confirm-universe / --expect-count.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_universe_change_refuses_until_confirmed`
