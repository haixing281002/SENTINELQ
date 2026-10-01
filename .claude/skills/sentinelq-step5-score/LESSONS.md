# Lessons - Step 5 Score

Accepted lessons: read these before working on this step. Each has a regression test; they were approved by a person.

## L-829639d7 - Appointments, promotions and succession are never a management exit
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Seven penalties were direction errors (AIA, 3M, Dr Reddy's, Titan, Nestle, AU SFB, Hindustan Copper).
- change: Verification gate + text safety net for people items.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_direction_error_is_not_penalised_when_the_gate_is_answered_correctly`, `tests/test_reconciliation.py::test_direction_error_is_caught_even_if_the_model_gets_the_gate_wrong`

## L-b803828c - The company must be the subject of a penalised event
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Macro duty hike, sector rule, compliance statement and a defrauded bank were penalised as company regulatory action / investigation.
- change: Subject / victim / macro rules in the gate.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_subject_error_is_not_penalised`, `tests/test_reconciliation.py::test_subject_error_is_caught_by_text_rules_when_the_model_omits_the_subject`

## L-9ab965ca - Immaterial sums sit in the -5 procedural band
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Rs 1.64 cr (Eicher) and Rs 64 lakh (Bank of Maharashtra) were scored at the -20 weight.
- change: Rs 10 cr materiality floor for non-integrity matters; integrity-type (SEBI conduct) exempt.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_eicher_rs_1_64_cr_customs_demand_is_procedural_not_minus_20`, `tests/test_reconciliation.py::test_integrity_type_matters_ignore_the_floor_angel_one_sebi_settlement_rs_4_28_cr`

## L-af382057 - One real-world event is one event, even when reported on several days / as several types
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Nestle's FSSAI notice (14-Jun) and 'probe' (15-Jun) were both penalised; the real model later gave them different wording and keys.
- change: Same-event de-duplication within a family (7 days, shared distinctive words or key); different families (fraud probe vs CFO termination) stay separate.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_nestle_double_count_survives_different_wording_and_different_keys`, `tests/test_reconciliation.py::test_two_different_regulators_within_a_week_are_not_merged`

## L-daa85cd1 - The -5 memory discount applies only when nothing in the window was penalised
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- Kajaria's memory discount was kept alongside in-window penalties.
- change: Convention in the rubric and code.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_memory_discount_only_when_no_in_window_penalty_exists`

## L-c16786c9 - One corporate action per declaration
- accepted 2026-10-01; source: Reconciliation 1-Oct-2026 s5-s7
- 375 headlines (141 dividend) were counted as separate actions.
- change: Collapse repeat headlines by type + figures within a window.
- note: Seeded from the Reconciliation document.
- tests: `tests/test_reconciliation.py::test_corporate_action_repeat_headlines_count_once`
