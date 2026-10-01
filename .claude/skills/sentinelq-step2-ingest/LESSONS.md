# Lessons - Step 2 Ingest

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-ac9b7689 - A backdated live search is not a point-in-time backtest
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Re-running a live news index with a past cut-off loses the articles that dropped out of feeds; ~20 sentiment scores moved (Reconciliation cause 1).
- change: Past as-of date -> dated archive (GDELT) via --news auto; a live index with a past date is refused or stamped.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_backdated_live_search_is_refused_or_stamped_and_auto_uses_the_archive`, `tests/test_reconciliation.py::test_backdated_live_run_is_stamped_in_the_pdf`

## L-7b8d5dec - Sentiment needs the 12-month evidence window, not only the latest weeks
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- With only the last 2-6 weeks nothing can reach +2 and a 40% revenue fall cannot reach -1.
- change: Added a separate 12-month results pass and measured the evidence span per stock.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_fundamentals_pass_brings_the_full_year_results_prints_into_sentiment`
