# Lessons - Step 4 Validate

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-93cc945a - Report Insufficient Data rather than a forced +1 or 0
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Below 8 relevant articles, or with no results print in 12 months, sentiment was being forced.
- change: Minimum evidence standard in the rubric.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_insufficient_data_not_a_forced_score`, `tests/test_reconciliation.py::test_pipeline_reports_n_a_not_plus_one`
