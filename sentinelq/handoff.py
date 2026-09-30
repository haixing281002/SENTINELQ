"""File hand-off mode: no model is called by the pipeline at all.

The pipeline writes work/pending_items.jsonl; a human or any Claude Code session labels them into
work/labels.jsonl (see docs/NO_API_KEY.md); re-running picks the labels up. Same schema, same validation."""
from __future__ import annotations
import json
from pathlib import Path

from .classify import item_id, item_payload
from .narrate import TemplateNarrator, evidence_payload


class FileClassifier:
    model_id = "file-handoff"

    def __init__(self, labels_path: str | Path):
        self.path = Path(labels_path)
        self.labels: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    self.labels[d.pop("id")] = d

    def pending(self, pairs):
        return [(i, c) for i, c in pairs if i.prelabel is None and i.url.strip() and item_id(i) not in self.labels]

    def write_pending(self, pairs, out: Path):
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for i, c in pairs:
                f.write(json.dumps(item_payload(i, c)) + "\n")

    def classify(self, item, company):
        d = self.labels.get(item_id(item))
        return (d, json.dumps(d)) if d else (None, "")


class FileNarrator:
    """Reads work/narratives.json {symbol: {...}}; falls back to template prose and records what is missing."""
    def __init__(self, path: str | Path, work: Path):
        self.path, self.work = Path(path), Path(work)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.missing: list[dict] = []
        self.tpl = TemplateNarrator()

    def holding(self, s, items):
        if s.symbol in self.data:
            return self.data[s.symbol]
        self.missing.append({"symbol": s.symbol, **evidence_payload(s, items)})
        return self.tpl.holding(s, items)

    def portfolio(self, facts):
        return self.data.get("_observations") or self.tpl.portfolio(facts)

    def write_missing(self):
        if self.missing:
            self.work.mkdir(parents=True, exist_ok=True)
            with (self.work / "pending_narratives.jsonl").open("w") as f:
                for m in self.missing:
                    f.write(json.dumps(m, default=str) + "\n")
