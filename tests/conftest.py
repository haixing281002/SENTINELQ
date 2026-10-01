import json
import os

# The demo fixtures hold 1-4 articles per stock, far below the production minimum-evidence standard (8 relevant articles + a results
# print). Tests that are about other things relax it; tests of the standard itself pass their own overrides.
os.environ.setdefault("SENTINELQ_RUBRIC_OVERRIDES", json.dumps({"sentiment": {"min_relevant_articles": 1, "require_results_print": False}}))
