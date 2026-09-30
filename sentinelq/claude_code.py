"""No-API-key model access.

ClaudeCodeClassifier / ClaudeCodeNarrator call the local `claude -p` (Claude Code headless) CLI, which uses the
login you already have in Claude Code - no ANTHROPIC_API_KEY. Items are labelled in batches; every item is still
labelled independently against the same forced schema, validated by stage 4, cached and logged. Caveat vs the API
path: the CLI exposes no temperature setting, so repeatability comes from the label cache (labels are stored on
first sight and reused) rather than from temperature 0.
"""
from __future__ import annotations
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor

from .classify import batch_prompt, item_id
from .models import RawItem
from .narrate import SYSTEM as NARR_SYSTEM, TOOL as NARR_TOOL, default_observations, evidence_payload
from .rubric import Rubric


def run_claude(prompt: str, model: str | None = None, timeout: int = 600) -> str:
    cmd = ["claude", "-p", "--output-format", "json", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    with tempfile.TemporaryDirectory() as cwd:   # neutral cwd: don't load any project context
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode != 0 and "no-session-persistence" in (p.stderr or ""):
        cmd.remove("--no-session-persistence")
        with tempfile.TemporaryDirectory() as cwd:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({p.returncode}): {(p.stderr or p.stdout)[:400]}")
    out = json.loads(p.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude CLI error: {out.get('result')}")
    return out["result"]


def extract_json(text: str, opener: str):
    close = "]" if opener == "[" else "}"
    a, b = text.find(opener), text.rfind(close)
    if a < 0 or b < a:
        raise ValueError("no JSON found in model reply")
    return json.loads(text[a:b + 1])


class ClaudeCodeClassifier:
    def __init__(self, rubric: Rubric, model: str | None = None, batch_size: int = 12, workers: int = 3):
        self.r, self.model, self.bs, self.workers = rubric, model, batch_size, workers
        self.model_id = f"claude-code:{model or 'default'}"
        self._res: dict[str, tuple[dict, str]] = {}

    def _label_batch(self, pairs):
        try:
            txt = run_claude(batch_prompt(pairs, self.r), self.model)
            for d in extract_json(txt, "["):
                if isinstance(d, dict) and "id" in d:
                    self._res[d.pop("id")] = (d, json.dumps(d))
        except Exception as e:   # a failed batch just leaves items unlabelled -> validate retries -> drops w/ reason
            print(f"  [claude-code] batch failed: {e}")

    def prefetch(self, pairs: list[tuple[RawItem, str]]):
        todo = [(i, c) for i, c in pairs if item_id(i) not in self._res]
        batches = [todo[k:k + self.bs] for k in range(0, len(todo), self.bs)]
        print(f"  [claude-code] labelling {len(todo)} items in {len(batches)} batches")
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(self._label_batch, batches))

    def classify(self, item, company):
        if item_id(item) not in self._res:      # retry path / uncached single item
            self._label_batch([(item, company)])
        d = self._res.get(item_id(item))
        return d if d else (None, "")


class ClaudeCodeNarrator:
    def __init__(self, model: str | None = None):
        self.model = model
        self.model_id = f"claude-code:{model or 'default'}"

    def holding(self, s, items):
        keys = list(NARR_TOOL["input_schema"]["properties"])
        prompt = (NARR_SYSTEM.replace("Answer by calling the tool.", "") +
                  "\nReply with ONLY a JSON object with keys " + ", ".join(keys) + " (field guidance: " +
                  json.dumps({k: v.get("description", "") for k, v in NARR_TOOL["input_schema"]["properties"].items()}) +
                  ").\n\nDATA:\n" + json.dumps(evidence_payload(s, items), default=str))
        out = extract_json(run_claude(prompt, self.model), "{")
        for k in keys:
            out.setdefault(k, "")
        return out

    def portfolio(self, facts):
        prompt = (NARR_SYSTEM + " Write 3-5 portfolio-level observations from the facts (distribution, governance flags and "
                  "patterns, corporate-action intensity, coverage gaps). Reply with ONLY a JSON array of "
                  '{"title","body"} objects.\n\nFACTS:\n' + json.dumps(facts, default=str))
        try:
            return extract_json(run_claude(prompt, self.model), "[")
        except Exception:
            return default_observations(facts)
