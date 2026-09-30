"""Label cache: once an article is labelled, re-runs reuse the label."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def item_key(url: str, title: str, snippet: str, model: str, prompt_version: str) -> str:
    return hashlib.sha256("\x1f".join([url, title, snippet, model, prompt_version]).encode()).hexdigest()


class LabelCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._d: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._d[r["key"]] = r

    def get(self, key: str):
        return self._d.get(key)

    def put(self, key: str, label: dict, raw: str):
        rec = {"key": key, "label": label, "raw": raw}
        self._d[key] = rec
        with self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
