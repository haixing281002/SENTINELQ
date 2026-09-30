#!/usr/bin/env python
"""Score governance for ONE stock from labelled events, using rubric/rubric_v1.json.

Input JSON: [{"date","headline","event_type","governance_flag","historical","materiality","url"}, ...]
Usage: python scripts/score_governance.py events.json [--rubric path]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sentinelq.models import Label, LabelledItem, RawItem  # noqa: E402
from sentinelq.rubric import load_rubric  # noqa: E402
from sentinelq.score import governance  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("events")
ap.add_argument("--rubric")
a = ap.parse_args()
r = load_rubric(a.rubric)
items = []
for e in json.loads(Path(a.events).read_text()):
    items.append(LabelledItem(RawItem("X", "news", e.get("headline", ""), "", e.get("url", ""), e.get("date", "")),
                              Label(e["event_type"], e.get("sentiment", -1), e.get("rationale", "-"),
                                    bool(e.get("governance_flag", True)), e.get("materiality"),
                                    bool(e.get("historical", False)))))
score, label, pens = governance(items, r)
print(f"Start {r['governance']['start']}")
for p in pens:
    print(f"  {p['penalty']:+g}  {p['event_type']:<28} {p['date']}  {p['headline'][:70]}")
print(f"= {score:g} ({label.upper() if label == 'Flag' else label})   [rubric v{r.version}]")
