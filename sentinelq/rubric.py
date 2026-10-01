"""Rubric loading. All weights/penalties/taxonomy live in one versioned JSON file."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RUBRIC = Path(__file__).resolve().parent.parent / "rubric" / "rubric_v1.json"


@dataclass(frozen=True)
class Rubric:
    data: dict
    sha256: str

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def event_types(self) -> list[str]:
        return self.data["event_types"]

    @property
    def sent_min(self) -> int:
        return self.data["sentiment_range"][0]

    @property
    def sent_max(self) -> int:
        return self.data["sentiment_range"][1]

    def __getitem__(self, k):
        return self.data[k]


def _merge(a: dict, b: dict) -> dict:
    for k, v in b.items():
        a[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a


def load_rubric(path: str | Path | None = None, overrides: dict | None = None) -> Rubric:
    """Load the versioned rubric. `overrides` (or env SENTINELQ_RUBRIC_OVERRIDES, a JSON object) deep-merges what-if values;
    when used, the hash covers the merged result so a run can never silently differ from its recorded rubric."""
    import os
    p = Path(path) if path else DEFAULT_RUBRIC
    raw = p.read_bytes()
    data = json.loads(raw)
    ov = overrides or (json.loads(os.environ["SENTINELQ_RUBRIC_OVERRIDES"]) if os.environ.get("SENTINELQ_RUBRIC_OVERRIDES") else None)
    if ov:
        data = _merge(data, ov)
        raw = json.dumps(data, sort_keys=True).encode()
    return Rubric(data, hashlib.sha256(raw).hexdigest())
