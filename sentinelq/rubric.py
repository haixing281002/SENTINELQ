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


def load_rubric(path: str | Path | None = None) -> Rubric:
    p = Path(path) if path else DEFAULT_RUBRIC
    raw = p.read_bytes()
    return Rubric(json.loads(raw), hashlib.sha256(raw).hexdigest())
