# Lessons - Step 3 Classify

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-70e10b18 - Share-price moves never feed sentiment
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- The July output cited 52-week highs, % moves and sell-offs as evidence.
- change: price_move event type, prompt rule, and a lint that strips price sentences from sentiment commentary.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_price_only_headlines_are_not_sentiment_evidence`
