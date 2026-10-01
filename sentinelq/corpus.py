"""B1-B3 - corpus of record + replay.

Append-only tables under <corpus>/ (default `audit/`); nothing is ever deleted or rewritten:
  manifest/<run_id>.json                  one JSON per run (source_mode, universe_hash, prompt/rubric/model, counts, warnings)
  corpus/articles/<run_id>.parquet        every article seen: url_hash, domain, tier, date, headline, snippet (<= 300 chars), pass, window, drop reason
  corpus/events/<run_id>.parquet          the labelled events (A3 fields + prompt_version, model_label, labelled_at)
  corpus/verifications/<run_id>.parquet   the governance verification record per event
  scores/<run_id>.parquet                 the scores (rubric_version, penalties, coverage, insufficient, published)
  golden/governance_37.jsonl              the regression set (B5)
Parquet when pyarrow is installed, else JSONL with the same columns (both are read transparently). Body text is never stored.

Cache semantics (B2): an article is identified by url_hash. A later run that sees the same url_hash writes a new articles row but does
not re-label if an events row exists for (url_hash in members, prompt_version, model_label). prompt/model change -> labels invalid;
rubric change -> scores only.
Point-in-time rule (B3): an article is visible at as_of iff published_date <= as_of AND it exists in a run whose as_of <= as_of + 7 days."""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import CORPUS_DIR, PIT_GRACE_DAYS

ARTICLE_COLS = ["run_id", "as_of", "symbol", "url", "url_hash", "source_domain", "source_tier", "published_date", "headline", "snippet",
                "pass", "window", "fetched_at", "dropped_reason"]
EVENT_COLS = ["event_id", "symbol", "event_date", "pass", "n_sources", "n_members", "max_tier", "representative_url", "member_url_hashes",
              "event_type", "sentiment", "is_governance_flag", "governance_flag_type", "justification", "confidence", "window",
              "prompt_version", "model_label", "labelled_at", "run_id", "as_of", "headline", "snippet", "url_hash", "gate_json",
              "materiality", "historical", "verified", "penalty_override", "source_ref", "members_json", "purpose"]
VERIFICATION_COLS = ["event_id", "symbol", "subject_is_company", "event_at_company", "direction", "severity", "applied_penalty",
                     "model_verify", "verified_at", "run_id", "basis"]
SCORE_COLS = ["run_id", "as_of", "symbol", "rubric_version", "rubric_sha256", "sentiment_raw", "sentiment_bucket", "gov_score", "gov_label",
              "penalties_json", "coverage_json", "insufficient", "triage", "published", "source_mode", "prompt_version", "model_label"]
SOURCE_MODES = ("live-gnews", "archive-gdelt", "pit-replay", "backdated-live-NOT-PIT", "supplied", "merged-union")


def source_mode_from_retrieval(retrieval_mode: str, news_choice: str = "") -> str:
    if news_choice == "file":
        return "supplied"
    return {"live": "live-gnews" if news_choice in ("", "gnews") else "archive-gdelt" if news_choice == "gdelt" else "live-gnews",
            "dated_archive": "archive-gdelt", "live_index_backdated": "backdated-live-NOT-PIT", "supplied": "supplied",
            "merged_union": "merged-union"}.get(retrieval_mode, retrieval_mode)


def _have_arrow() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
        return True
    except Exception:
        return False


def write_table(path_no_ext: Path, rows: list[dict], cols: list[str]) -> Path:
    """Append-only: a table file is written once per run_id and never rewritten (refuses to overwrite)."""
    path_no_ext.parent.mkdir(parents=True, exist_ok=True)
    rows = [{c: _cell(r.get(c)) for c in cols} for r in rows]
    if _have_arrow():
        import pyarrow as pa
        import pyarrow.parquet as pq
        p = path_no_ext.with_suffix(".parquet")
        if p.exists():
            raise FileExistsError(f"{p} exists - the corpus is append-only; use a new run_id")
        table = pa.Table.from_pylist(rows) if rows else pa.table({c: pa.array([], type=pa.string()) for c in cols})
        pq.write_table(table, p)
        return p
    p = path_no_ext.with_suffix(".jsonl")
    if p.exists():
        raise FileExistsError(f"{p} exists - the corpus is append-only; use a new run_id")
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _cell(v):
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, date):
        return v.isoformat()
    return v


def read_table(path_any: Path) -> list[dict]:
    p = Path(path_any)
    if p.suffix == ".parquet":
        import pyarrow.parquet as pq
        return pq.read_table(p).to_pylist()
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def make_run_id(as_of: date, started: datetime | None = None) -> str:
    started = started or datetime.now()
    return f"{as_of.isoformat()}_{started:%Y%m%dT%H%M%S}{started.microsecond // 1000:03d}"


class Corpus:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or CORPUS_DIR)

    # ---- paths
    def _dir(self, name: str) -> Path:
        return self.root / name

    def manifest_path(self, run_id: str) -> Path:
        return self._dir("manifest") / f"{run_id}.json"

    def _files(self, sub: str) -> list[Path]:
        d = self.root / sub
        if not d.exists():
            return []
        return sorted([p for p in d.iterdir() if p.suffix in (".parquet", ".jsonl")], key=lambda p: p.stem)

    # ---- writing (append-only)
    def write_run(self, run_id: str, manifest: dict, articles: list[dict], events: list[dict], verifications: list[dict],
                  scores: list[dict]) -> dict[str, str]:
        out = {}
        mp = self.manifest_path(run_id)
        if mp.exists():
            raise FileExistsError(f"{mp} exists - the corpus is append-only")
        out["articles"] = str(write_table(self._dir("corpus/articles") / run_id, articles, ARTICLE_COLS))
        out["events"] = str(write_table(self._dir("corpus/events") / run_id, events, EVENT_COLS))
        out["verifications"] = str(write_table(self._dir("corpus/verifications") / run_id, verifications, VERIFICATION_COLS))
        out["scores"] = str(write_table(self._dir("scores") / run_id, scores, SCORE_COLS))
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps({**manifest, "run_id": run_id, "tables": out}, indent=2, ensure_ascii=False), encoding="utf-8")
        out["manifest"] = str(mp)
        return out

    # ---- reading
    def manifests(self) -> list[dict]:
        d = self._dir("manifest")
        if not d.exists():
            return []
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in d.glob("*.json")), key=lambda m: m["run_id"])

    def run_ids(self) -> list[str]:
        return [m["run_id"] for m in self.manifests()]

    def load(self, sub: str, run_ids: list[str] | None = None) -> list[dict]:
        rows = []
        for p in self._files(sub):
            if run_ids is None or p.stem in run_ids:
                rows += read_table(p)
        return rows

    def articles(self, run_ids=None) -> list[dict]:
        return self.load("corpus/articles", run_ids)

    def events(self, run_ids=None) -> list[dict]:
        return self.load("corpus/events", run_ids)

    def scores(self, run_ids=None) -> list[dict]:
        return self.load("scores", run_ids)

    def start(self) -> date | None:
        """The corpus start: the earliest as_of of any stored run (pit-replay is only genuine on/after it)."""
        ms = [m for m in self.manifests() if m.get("as_of")]
        return min(date.fromisoformat(m["as_of"]) for m in ms) if ms else None

    # ---- B2 cache
    def label_index(self, prompt_version: str, model_label: str) -> dict[str, dict]:
        """url_hash (any member) -> stored event row, for this exact prompt + model. Anything else is a cache miss."""
        idx: dict[str, dict] = {}
        for e in self.events():
            if e.get("prompt_version") != prompt_version or e.get("model_label") != model_label:
                continue
            for h in _loads(e.get("member_url_hashes")) or []:
                idx.setdefault(h, e)
            if e.get("url_hash"):
                idx.setdefault(e["url_hash"], e)
        return idx

    # ---- B3 point-in-time
    def visible_runs(self, as_of: date, grace_days: int = PIT_GRACE_DAYS) -> list[str]:
        return [m["run_id"] for m in self.manifests() if m.get("as_of") and date.fromisoformat(m["as_of"]) <= as_of + timedelta(days=grace_days)
                and m.get("source_mode") != "pit-replay"]

    def visible_events(self, as_of: date, grace_days: int = PIT_GRACE_DAYS) -> list[dict]:
        """Events visible at as_of: event_date <= as_of and stored by a run whose as_of <= as_of + grace. Later fetches are look-ahead
        and refused. The newest stored copy of each event_id wins (identical labels under one prompt/model anyway)."""
        rids = set(self.visible_runs(as_of, grace_days))
        seen: dict[str, dict] = {}
        for e in self.events():
            if e.get("run_id") not in rids:
                continue
            d = e.get("event_date") or ""
            if not d or d[:10] > as_of.isoformat():
                continue
            seen[e["event_id"]] = e                   # files are read in run_id order, so the latest run wins
        return list(seen.values())

    def source_mode_for_replay(self, as_of: date) -> str:
        s = self.start()
        return "pit-replay" if s is not None and as_of >= s else "archive-gdelt"

    def last_two_score_runs(self) -> list[str]:
        ids = [m["run_id"] for m in self.manifests() if m.get("n_scores")]
        return ids[-2:]


def _loads(v):
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None
