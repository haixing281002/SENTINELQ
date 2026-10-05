"""Upgrade Workflow v2.1 configuration (Section A1 / A3 / A4 / B3). Numbers here shape EVIDENCE COLLECTION, never a score weight:
rubric weights stay in rubric/rubric_v1.json. `sentinelq/config/source_tiers.yaml` sits next to this file."""
from __future__ import annotations
from pathlib import Path

# A1 - stratified sampler: (days_from, days_to, article quota), back from as_of. W1 can never fill the whole budget.
SAMPLE_WINDOWS = [(0, 30, 40), (31, 90, 25), (91, 180, 20), (181, 365, 15)]
PER_DAY_CAP = 6                 # articles per stock per calendar day counted against the quota; the rest become extra sources
RESULTS_EVENTS_MAX = 4          # at most four results PRINTS (one per quarter) ...
RESULTS_OTHER_PER_QUARTER = 3   # ... plus up to three other results-type events per quarter (guidance, order wins, margin notes)
GOV_PASS_MAX = 30               # unchanged in total ...
GOV_PASS_WINDOWS = [9, 8, 7, 6] # ... but filled per window W1..W4 (newest first inside each), shortfalls carried to the next older window
                                # then back to the newest, so a burst of recent appointment headlines cannot push an older order out

# A3 - clustering
CLUSTER_DAYS = 2                # two articles may join a cluster if published within this many days ...
CLUSTER_JACCARD = 0.5           # ... and token-Jaccard >= this, or
CLUSTER_SHARED_DISTINCTIVE = 3  # ... they share >= this many distinctive tokens
NEAR_DUPLICATE_JACCARD = 0.7    # near-identical headlines (a re-run of the same story) cluster within NEAR_DUPLICATE_DAYS whatever the label
NEAR_DUPLICATE_DAYS = 7
TOP_FREQ_TOKENS = 200           # distinctive = not in the top-N corpus-frequency list
MERGE_DAYS = 3                  # post-label merge: same event_type + same governance flag within this many days = one event
CONF_BASE, CONF_SLOPE = 0.40, 0.15      # conf = min(1, 0.40 + 0.15 * ln(1 + n_sources)) * tier_weight
CONF_UNPENALISED_BELOW = 0.4    # a governance event with conf < this and no T1/T2 source is listed, not penalised

# Boilerplate headlines: templated price / listicle items that carry no event. Dropped BEFORE the model sees them (disclosed as
# 'boilerplate_headline'); editable. The model's own `substance` answer catches the rest ('model_boilerplate').
BOILERPLATE_PATTERNS = [
    r"share price (today|live|update)", r"stock price (today|live)", r"\bbuy,? sell or hold\b", r"should you (buy|sell|hold)",
    r"stocks? to (watch|buy|track)( today)?", r"top (stocks|picks|gainers|losers)", r"live updates?", r"\bin focus\b", r"shares? in (focus|news)",
    r"record date (today|alert|tomorrow)", r"ex-?dividend (today|date alert)", r"trading (ideas?|strategy)", r"technical (view|analysis|chart)",
    r"\bintraday\b", r"52-week (high|low)", r"stocks? (rally|surge|jump|fall|drop|slip|crash)e?s? \d+%", r"market (wrap|highlights|roundup)",
    r"\bhere'?s why\b", r"what (should|do) investors", r"\bF&O\b", r"\bweekly wrap\b", r"(nifty|sensex) (today|ends|opens|closes)",
    r"last day to buy", r"\bex-?date\b", r"dividends?: ", r"block (trade|deal)", r"bulk deal", r"brand film", r"hosts? (a |an )?(delegation|summit|conclave|webinar)",
    r"shares? (jump|rise|gain|fall|slip|drop|tank|surge)s? \d+%", r"stocks? in (news|action)", r"multibagger", r"wealth creator",
]
MERGE_RESULTS_SAME_QUARTER = True   # results-type events of the same type in the same quarter are ONE event (sources pooled)

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
