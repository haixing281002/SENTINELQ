"""Label cache: once an article is labelled, re-runs reuse the label."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def item_key(url: str, title: str, snippet: str, model: str, prompt_version: str) -> str:
    return hashlib.sha256("\x1f".join([url, title, snippet, model, prompt_version]).encode()).hexdigest()


class LabelCache:
    """key (hash) -> label + the model's short JSON reply. No article text is ever stored. path=None -> memory only."""
    def __init__(self, path: str | Path | None):
        import threading
        self._lock = threading.Lock()
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._d: dict[str, dict] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self._d[r["key"]] = r

    def get(self, key: str):
        return self._d.get(key)

    def put(self, key: str, label: dict, raw: str):
        rec = {"key": key, "label": label, "raw": raw}
        with self._lock:
            self._d[key] = rec
            if self.path:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
