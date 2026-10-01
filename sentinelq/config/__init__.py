"""Upgrade Workflow v2.1 configuration (Section A1 / A3 / A4 / B3). Numbers here shape EVIDENCE COLLECTION, never a score weight:
rubric weights stay in rubric/rubric_v1.json. `sentinelq/config/source_tiers.yaml` sits next to this file."""
from __future__ import annotations
from pathlib import Path

# A1 - stratified sampler: (days_from, days_to, article quota), back from as_of. W1 can never fill the whole budget.
SAMPLE_WINDOWS = [(0, 30, 40), (31, 90, 25), (91, 180, 20), (181, 365, 15)]
PER_DAY_CAP = 6                 # articles per stock per calendar day counted against the quota; the rest become extra sources
RESULTS_EVENTS_MAX = 4          # results-pass hits cluster into at most four results events (one per quarter)
GOV_PASS_MAX = 30               # unchanged

# A3 - clustering
CLUSTER_DAYS = 2                # two articles may join a cluster if published within this many days ...
CLUSTER_JACCARD = 0.5           # ... and token-Jaccard >= this, or
CLUSTER_SHARED_DISTINCTIVE = 3  # ... they share >= this many distinctive tokens
TOP_FREQ_TOKENS = 200           # distinctive = not in the top-N corpus-frequency list
MERGE_DAYS = 3                  # post-label merge: same event_type + same governance flag within this many days = one event
CONF_BASE, CONF_SLOPE = 0.40, 0.15      # conf = min(1, 0.40 + 0.15 * ln(1 + n_sources)) * tier_weight
CONF_UNPENALISED_BELOW = 0.4    # a governance event with conf < this and no T1/T2 source is listed, not penalised

# A4 - minimum evidence (replaces 'fewer than 8 articles')
MIN_EVENTS = 6
MIN_WINDOWS = 2
MIN_RESULTS_EVENTS = 1
WINDOW_DOMINANCE = 0.70         # acceptance A5.1: no single window may hold more than this share of a stock's events (if >= MIN_EVENTS)

# B3 - point-in-time rule
PIT_GRACE_DAYS = 7              # an article is visible at as_of if it exists in a run whose as_of <= as_of + 7 days

# B1 - storage root (append-only); override with --corpus-dir
CORPUS_DIR = "audit"
TIERS_FILE = Path(__file__).resolve().parent / "source_tiers.yaml"
GOLDEN_FILE = "audit/golden/governance_37.jsonl"


# Test / what-if overrides: SENTINELQ_CONFIG_OVERRIDES='{"MIN_EVENTS": 1}' (evidence-collection knobs only; never a rubric weight).
import json as _json
import os as _os
for _k, _v in (_json.loads(_os.environ["SENTINELQ_CONFIG_OVERRIDES"]) if _os.environ.get("SENTINELQ_CONFIG_OVERRIDES") else {}).items():
    if _k in globals() and not _k.startswith("_"):
        globals()[_k] = _v
