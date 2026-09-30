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
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .classify import batch_prompt, item_id
from .models import RawItem
from .narrate import SYSTEM as NARR_SYSTEM, TOOL as NARR_TOOL, default_observations, evidence_payload
from .rubric import Rubric


INSTALL_HELP = (
    "The `claude` command (Claude Code CLI) was not found.\n"
    "  1. Install it:  npm install -g @anthropic-ai/claude-code   (then run `claude` once and log in)\n"
    "  2. If it is installed but not on PATH, set CLAUDE_BIN to its full path, e.g.\n"
    "       PowerShell:  $env:CLAUDE_BIN = (Get-Command claude.cmd).Source\n"
    "       or            $env:CLAUDE_BIN = \"$env:APPDATA\\npm\\claude.cmd\"\n"
    "  3. Or use another route: --classifier anthropic (needs ANTHROPIC_API_KEY) or --classifier file "
    "(see docs/NO_API_KEY.md)."
)


def find_claude() -> str | None:
    """Locate the CLI. On Windows the npm shim is claude.cmd, which subprocess cannot find by bare name."""
    import os
    import shutil
    from pathlib import Path
    env = os.environ.get("CLAUDE_BIN")
    if env and Path(env).exists():
        return env
    for name in ("claude", "claude.cmd", "claude.exe"):
        hit = shutil.which(name)
        if hit:
            return hit
    home = Path.home()
    cands = [Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd", home / ".claude" / "local" / "claude",
             home / ".claude" / "local" / "claude.exe", home / ".local" / "bin" / "claude",
             Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    return next((str(c) for c in cands if c.exists()), None)


def preflight(model: str | None = None) -> None:
    """Fail in seconds, not after a 10-minute run: the CLI must exist, start, and answer."""
    exe = find_claude()
    if not exe:
        raise SystemExit(INSTALL_HELP)
    try:
        out = run_claude("Reply with the single word OK.", model, timeout=120)
    except Exception as e:
        raise SystemExit(f"`claude` was found at {exe} but a test call failed:\n  {e}\n"
                         "Run `claude` once in a terminal to log in, then retry.")
    print(f"  [claude-code] preflight ok ({exe}) -> {out.strip()[:20]!r}")


def run_claude(prompt: str, model: str | None = None, timeout: int = 600) -> str:
    exe = find_claude()
    if not exe:
        raise FileNotFoundError(INSTALL_HELP)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]

    def go(c):
        with tempfile.TemporaryDirectory() as cwd:   # neutral cwd: don't load any project context
            return subprocess.run(c, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=cwd)
    p = go(cmd)
    if p.returncode != 0 and "no-session-persistence" in (p.stderr or ""):
        cmd.remove("--no-session-persistence")
        p = go(cmd)
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


def parse_labels(text: str) -> list[dict]:
    """Pull a list of label dicts (each with an 'id') out of a model reply, tolerating code fences, prose around
    the JSON, stray brackets before the array, a wrapping {"labels": [...]}, and truncated/invalid arrays (in which
    case whole objects are salvaged one by one)."""
    import re
    t = re.sub(r"```(?:json)?", "", text or "")
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\[{]", t):
        try:
            obj, _ = dec.raw_decode(t[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict):
            obj = next((v for v in obj.values() if isinstance(v, list)), [obj] if "id" in obj else [])
        if isinstance(obj, list) and obj and all(isinstance(o, dict) for o in obj) and any("id" in o for o in obj):
            return [o for o in obj if "id" in o]
    out = []
    for m in re.finditer(r"\{[^{}]*\"id\"[^{}]*\}", t):
        try:
            out.append(json.loads(m.group(0)))
        except ValueError:
            pass
    return out


RATE_HINTS = ("rate", "limit", "overload", "429", "529", "quota", "too many", "capacity", "usage")
BACKOFF = (30, 60, 120)          # seconds between retries of a rate-limited call


class LabellingAborted(RuntimeError):
    pass


class ClaudeCodeClassifier:
    def __init__(self, rubric: Rubric, model: str | None = None, batch_size: int = 8, workers: int = 2,
                 log_path: str | Path = "work/claude_batches.jsonl"):
        self.r, self.model, self.bs, self.workers = rubric, model, max(1, batch_size), max(1, workers)
        self.model_id = f"claude-code:{model or 'default'}"
        self._res: dict[str, tuple[dict, str]] = {}
        self.errors: dict[str, str] = {}       # item id -> why it could not be labelled
        self.on_label = None                   # callback(item, label_dict, raw): persists labels as they arrive
        self.on_fail = None                    # callback(item, error_text)
        self.say = lambda msg, bad=False: print(msg)
        self.abort = False
        self.log_path = Path(log_path)
        self._lock = threading.Lock()

    def _log(self, **rec):
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _ask(self, pairs) -> tuple[set[str], str]:
        """One claude call for `pairs`. Returns (ids labelled, error text). Rate limits back off and retry."""
        ids = [item_id(i) for i, _ in pairs]
        err = ""
        for attempt in range(len(BACKOFF) + 1):
            if self.abort:
                return set(), "aborted after repeated rate-limit/usage errors"
            try:
                txt = run_claude(batch_prompt(pairs, self.r), self.model)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"[:500]
                self._log(ids=ids, attempt=attempt, error=err)
                if any(h in err.lower() for h in RATE_HINTS) and attempt < len(BACKOFF):
                    time.sleep(BACKOFF[attempt])
                    continue
                if any(h in err.lower() for h in RATE_HINTS):
                    self.abort = True
                return set(), err
            got = parse_labels(txt)
            self._log(ids=ids, attempt=attempt, reply_head=(txt or "")[:300], parsed=len(got))
            done = set()
            by_id = {d["id"]: d for d in got if isinstance(d.get("id"), str)}
            for it, _c in pairs:
                d = by_id.get(item_id(it))
                if d is None:
                    continue
                d = {k: v for k, v in d.items() if k != "id"}
                with self._lock:
                    self._res[item_id(it)] = (d, json.dumps(d))
                if self.on_label:
                    self.on_label(it, d, json.dumps(d))
                done.add(item_id(it))
            if done:
                return done, ""
            err = "reply contained no parseable labels: " + (txt or "(empty)")[:200].replace("\n", " ")
            return set(), err
        return set(), err

    def _label_batch(self, pairs, depth: int = 0):
        """Label a batch; on failure retry the missing items in halves, down to single items, so one bad item or
        one bad reply can never take a whole batch with it."""
        if not pairs or self.abort:
            return
        done, err = self._ask(pairs)
        missing = [(i, c) for i, c in pairs if item_id(i) not in done]
        if not missing:
            return
        if len(missing) == 1:
            self.errors[item_id(missing[0][0])] = err or "model returned no label"
            if self.on_fail:
                self.on_fail(missing[0][0], self.errors[item_id(missing[0][0])])
            return
        if len(missing) == len(pairs):            # nothing came back: split
            mid = len(missing) // 2
            self._label_batch(missing[:mid], depth + 1)
            self._label_batch(missing[mid:], depth + 1)
        else:                                     # partial reply: re-ask only for the missing items
            self._label_batch(missing, depth + 1)

    def prefetch(self, pairs: list[tuple[RawItem, str]]):
        todo = [(i, c) for i, c in pairs if item_id(i) not in self._res]
        batches = [todo[k:k + self.bs] for k in range(0, len(todo), self.bs)]
        self.say(f"{len(todo)} items in {len(batches)} batches (batch={self.bs}, workers={self.workers}); call log: {self.log_path}")
        with ThreadPoolExecutor(self.workers) as ex:
            list(ex.map(self._label_batch, batches))
        if self.abort:
            raise LabellingAborted("Claude Code kept returning rate-limit/usage errors, so labelling stopped. "
                                   f"{len(self._res)} labels are saved in the cache; re-run the same command later to resume "
                                   f"(only unlabelled items are retried). Details: {self.log_path}")
        if batches and not self._res:
            first = next(iter(self.errors.values()), "unknown")
            raise RuntimeError("Model labelling failed for every item - refusing to write an empty report.\n"
                               f"First error: {first}\nFull per-batch log: {self.log_path}")
        if self.errors:
            self.say(f"WARNING: {len(self.errors)}/{len(todo)} items could not be labelled (reasons in {self.log_path})", True)

    def classify(self, item, company):
        iid = item_id(item)
        if iid not in self._res and iid not in self.errors:     # not prefetched (single-item use)
            self._label_batch([(item, company)])
        d = self._res.get(iid)
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
