import json
import os

# The demo fixtures hold 1-4 articles per stock, far below the production minimum-evidence standard (8 relevant articles + a results
# print). Tests that are about other things relax it; tests of the standard itself pass their own overrides.
os.environ.setdefault("SENTINELQ_RUBRIC_OVERRIDES", json.dumps({"sentiment": {"min_relevant_articles": 1, "require_results_print": False}}))
# Upgrade v2.1: the fixtures hold a handful of single-source example.com articles. Relax the event-level minimum evidence and treat
# example.com as a T2 publisher so a single fixture article can still carry a governance penalty; tests of these rules set their own values.
os.environ.setdefault("SENTINELQ_CONFIG_OVERRIDES", json.dumps({"MIN_EVENTS": 1, "MIN_WINDOWS": 1, "MIN_RESULTS_EVENTS": 0}))
os.environ.setdefault("SENTINELQ_TIER_OVERRIDES", json.dumps({"example.com": "T2", "example.org": "T2", "news.example": "T2"}))
