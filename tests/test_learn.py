import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sentinelq import learn as L
from sentinelq.rubric import load_rubric

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "REGISTRY", tmp_path / "reg.json")
    monkeypatch.setattr(L, "PATCH", tmp_path / "patch.json")
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "SKILLS", tmp_path / "skills")
    (tmp_path / "tests" / "lessons").mkdir(parents=True)
    (tmp_path / "lessons").mkdir()
    for d in L.STEP_DIR.values():
        (tmp_path / "skills" / d).mkdir(parents=True)
    # load/save take their default at definition time, so route through the patched constants
    monkeypatch.setattr(L, "load", lambda path=tmp_path / "reg.json", _o=L.load: _o(path))
    monkeypatch.setattr(L, "save", lambda items, path=tmp_path / "reg.json", _o=L.save: _o(items, path))
    return tmp_path


def _run(tmp, penalised=True):
    run = tmp / "run"
    run.mkdir()
    pen = [{"event_type": "management_exit", "penalty": -8, "date": "2026-05-01", "url": "u1",
            "headline": "Dr Reddy's elevates CFO to Chief Operating Officer"}]
    sc = [{"symbol": "DRREDDY", "governance_score": 92, "governance_label": "Watch", "governance_penalties": pen if penalised else [],
           "company_sentiment": 1, "sentiment_note": "", "relevant_articles": 40, "evidence_span_days": 300}]
    ev = [{"symbol": "DRREDDY", "url": "u1", "headline": pen[0]["headline"], "event_type": "management_exit", "governance_flag": True,
           "subject": "company", "occurred_at_company": True, "action_stage": "final", "severity": "minor", "amount_inr_cr": None,
           "people_direction": "exit", "role_tier": "cxo", "event_key": "k"}]
    (run / "scores.json").write_text(json.dumps(sc))
    (run / "evidence.json").write_text(json.dumps(ev))
    (run / "run_meta.json").write_text(json.dumps({"as_of": "2026-07-03"}))
    (run / "lint.json").write_text(json.dumps([{"symbol": "X", "removed_sentence": "shares rose 3%"}]))
    return run


def test_seed_and_render(sandbox):
    assert L.seed() == 12
    L.render()
    for d in L.STEP_DIR.values():
        assert (sandbox / "skills" / d / "LESSONS.md").exists()


def test_propose_never_changes_rubric_and_is_idempotent(sandbox):
    before = load_rubric().sha256
    run = _run(sandbox)
    new = L.propose(run)
    assert {l["kind"] for l in new} >= {"benign_people_penalised", "price_language"}
    assert all(l["status"] == "proposed" for l in new)
    assert load_rubric().sha256 == before
    assert L.propose(run) == []


def test_accept_generates_passing_test_and_patch_merges(sandbox):
    new = L.propose(_run(sandbox))
    lid = next(l["id"] for l in new if l["kind"] == "benign_people_penalised")
    l = L.accept(lid, "appointment is not an exit", expect_penalty=0, rubric_patch={"governance": {"x_test": 1}})
    t = sandbox / l["tests_ref"][0]
    assert t.exists()
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(t)], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert r.returncode == 0, r.stdout + r.stderr
    patch = json.loads((sandbox / "patch.json").read_text())
    assert lid in patch["learned_from"]


def test_reject_prevents_reproposal(sandbox):
    run = _run(sandbox)
    new = L.propose(run)
    L.reject(new[0]["id"], "not an issue")
    assert new[0]["id"] not in {l["id"] for l in L.propose(run)}


def test_patch_changes_rubric_hash(tmp_path, monkeypatch):
    import sentinelq.rubric as R
    fake = tmp_path / "rubric_v1.json"
    fake.write_text((ROOT / "rubric" / "rubric_v1.json").read_text())
    (tmp_path / "learned_patch.json").write_text(json.dumps({"sentiment": {"min_relevant_articles": 3}, "learned_from": ["L-x"]}))
    monkeypatch.setattr(R, "DEFAULT_RUBRIC", fake)
    monkeypatch.delenv("SENTINELQ_RUBRIC_OVERRIDES", raising=False)
    a = R.load_rubric()
    assert a["sentiment"]["min_relevant_articles"] == 3 and a["learned_from"] == ["L-x"]
    monkeypatch.setenv("SENTINELQ_NO_LEARNED_PATCH", "1")
    assert R.load_rubric().sha256 != a.sha256


def test_every_accepted_lesson_has_resolvable_tests():
    for l in L.load():
        if l["status"] == "accepted":
            assert l["tests_ref"], l["id"]
            for t in l["tests_ref"]:
                assert (ROOT / t.split("::")[0]).exists(), t
