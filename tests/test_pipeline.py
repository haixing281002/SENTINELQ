import json
from datetime import date
from pathlib import Path

from sentinelq.classify import KeywordClassifier
from sentinelq.ingest.files import FileActions, FileNews, FilePrices
from sentinelq.pipeline import Pipeline, load_portfolio
from sentinelq.rubric import load_rubric
from sentinelq.validate import validate_label

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "examples" / "fixtures"
AS_OF = date(2026, 7, 15)


def run(tmp_path, cache=None):
    r = load_rubric()
    p = Pipeline(r, FileNews(FX / "news.json"), FileActions(FX / "actions.json"),
                 FilePrices(FX / "prices.csv"), KeywordClassifier(), tmp_path / "out",
                 cache or tmp_path / "c.jsonl", AS_OF)
    return p.run(load_portfolio(ROOT / "examples" / "portfolio.csv"))


def test_angel_one_worked_example(tmp_path):
    res = run(tmp_path)
    s = {x.symbol: x for x in res["scores"]}["ANGELONE"]
    assert s.governance_score == 72          # 100 - 20 (regulatory) - 8 (mgmt exit)
    assert s.governance_label == "Flag"
    assert {p["event_type"] for p in s.governance_penalties} == {"regulatory_action", "management_exit"}


def test_no_source_dropped_and_disclosed(tmp_path):
    res = run(tmp_path)
    assert any(d.reason == "missing_source_url" for d in res["dropped"])
    assert res["run"]["totals"]["dropped"] == len(res["dropped"]) >= 1
    assert (tmp_path / "out" / "sentinelq_report.xlsx").exists()


def test_deterministic_and_cached(tmp_path):
    a = run(tmp_path)
    b = run(tmp_path)   # second run reuses cache
    assert [x.to_dict() for x in a["scores"]] == [x.to_dict() for x in b["scores"]]
    assert all(li.from_cache for v in b["kept"].values() for li in v if li.item.kind == "news")


def test_prices_never_affect_scores(tmp_path):
    a = run(tmp_path)
    r = load_rubric()
    p = Pipeline(r, FileNews(FX / "news.json"), FileActions(FX / "actions.json"), None,
                 KeywordClassifier(), tmp_path / "o2", tmp_path / "c.jsonl", AS_OF)
    b = p.run(load_portfolio(ROOT / "examples" / "portfolio.csv"))
    assert [x.to_dict() for x in a["scores"]] == [x.to_dict() for x in b["scores"]]


def test_validation_rules():
    from sentinelq.models import RawItem
    r = load_rubric()
    it = RawItem("X", "news", "t", "", "https://a.b/c", "2026-06-01")
    ok = {"event_type": "other", "sentiment": 0, "rationale": "x", "governance_flag": False}
    assert validate_label(it, ok, r, AS_OF, 365) is None
    assert validate_label(it, {**ok, "sentiment": 3}, r, AS_OF, 365).startswith("sentiment_out_of_range")
    assert validate_label(it, {**ok, "event_type": "nope"}, r, AS_OF, 365).startswith("invalid_event_type")
    old = RawItem("X", "news", "t", "", "https://a.b/c", "2024-01-01")
    assert validate_label(old, ok, r, AS_OF, 365) == "date_outside_lookback"


def test_corporate_actions_and_sector(tmp_path):
    res = run(tmp_path)
    s = {x.symbol: x for x in res["scores"]}
    assert s["HDFCBANK"].corporate_action_score == 1.0     # dividend, medium
    assert s["BPCL"].corporate_action_score == 2.0         # 1.5 * 1.5 = 2.25 clipped to 2
    assert s["ANGELONE"].sector_sentiment == s["HDFCBANK"].sector_sentiment
    assert s["BPCL"].low_confidence


def _li(et, gov=True, hist=False, mat=None, date_="2026-06-01"):
    from sentinelq.models import Label, LabelledItem, RawItem
    return LabelledItem(RawItem("X", "news", et, "", "https://a.b/" + et, date_),
                        Label(et, -1, "r", gov, mat, hist))


def test_governance_rubric_variants():
    from sentinelq.score import governance, to_integer
    r = load_rubric()
    assert governance([_li("regulatory_action"), _li("management_exit")], r)[:2] == (72, "Flag")
    assert governance([_li("auditor_restatement")], r)[0] == 75
    assert governance([_li("exchange_fine", mat="high")], r)[0] == 90
    # historical background mention -> flat -5 memory discount, once
    s, lab, _ = governance([_li("regulatory_action", hist=True), _li("regulatory_action", hist=True, date_="2026-06-02")], r)
    assert (s, lab) == (95, "Clean")
    # not double counted with an in-window penalty of the same type
    assert governance([_li("regulatory_action"), _li("regulatory_action", hist=True)], r)[0] == 80
    assert [to_integer(x) for x in (-0.42, 0.5, 1.6, -0.5, 1.49)] == [0, 1, 2, -1, 1]


def test_pdf_written(tmp_path):
    run(tmp_path)
    assert (tmp_path / "out" / "sentinelq_scorecard.pdf").stat().st_size > 5000


def test_max_articles_cap(tmp_path):
    r = load_rubric()
    p = Pipeline(r, FileNews(FX / "news.json"), None, None, KeywordClassifier(), tmp_path / "o",
                 tmp_path / "c.jsonl", AS_OF, max_articles=1)
    res = p.run(load_portfolio(ROOT / "examples" / "portfolio.csv"))
    assert all(sum(li.item.kind == "news" for li in v) <= 1 for v in res["kept"].values())
    assert res["run"]["totals"]["retrieved"] == 3   # one per company


def test_file_handoff_roundtrip(tmp_path):
    import json
    from sentinelq.classify import KeywordClassifier, item_id
    from sentinelq.handoff import FileClassifier
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    names = {h.symbol: h.name for h in hold}
    clf = FileClassifier(tmp_path / "labels.jsonl")
    p = Pipeline(r, FileNews(FX / "news.json"), None, None, clf, tmp_path / "o", tmp_path / "c.jsonl", AS_OF)
    raw, _ = p.ingest(hold)
    pend = clf.pending([(i, names[s]) for s, v in raw.items() for i in v])
    assert len(pend) == 6                      # the no-URL item is never sent for labelling
    kw = KeywordClassifier()                   # stand-in for a human/Claude labeller
    with open(tmp_path / "labels.jsonl", "w") as f:
        for i, c in pend:
            d, _ = kw.classify(i, c)
            f.write(json.dumps({"id": item_id(i), **d}) + "\n")
    clf2 = FileClassifier(tmp_path / "labels.jsonl")
    assert not clf2.pending([(i, names[s]) for s, v in raw.items() for i in v])
    res = Pipeline(r, FileNews(FX / "news.json"), None, None, clf2, tmp_path / "o2", tmp_path / "c2.jsonl", AS_OF).run(hold)
    assert {x.symbol: x.governance_score for x in res["scores"]}["ANGELONE"] == 72


def test_irrelevant_items_dropped(tmp_path):
    from sentinelq.models import RawItem
    r = load_rubric()
    it = RawItem("X", "news", "t", "", "https://a.b/c", "2026-06-01")
    lab = {"event_type": "other", "sentiment": 0, "rationale": "x", "governance_flag": False, "about_company": False}
    assert validate_label(it, lab, r, AS_OF, 365) == "not_about_company"


def test_pick_by_name(tmp_path):
    import pytest
    from sentinelq.pipeline import pick_holdings
    u = ROOT / "portfolio" / "universe.csv"
    h = pick_holdings(u, ["angel one", "TITAN"])
    assert [x.symbol for x in h] == ["ANGELONE", "TITAN"] and h[0].weight == ""
    with pytest.raises(SystemExit):
        pick_holdings(u, ["Nonexistent Corp"])


def test_given_table_parses_and_resolves():
    from sentinelq.resolve import enrich
    h = enrich(load_portfolio(ROOT / "portfolio" / "stocks_given.tsv"), mode="none")
    assert len(h) == 29
    assert all(x.sector != "Unclassified" for x in h)
    m = {x.symbol: x for x in h}
    assert "TITAN" in m and "NSE:TITAN" not in m          # exchange prefix stripped
    assert m["NATIONALUM"].name == "National Aluminium"    # legal suffixes cleaned
    assert m["ANGELONE"].sector == "BFSI" and m["ANGELONE"].cap == "Small"


def test_pdf_pages_1_2_match_reference_geometry(tmp_path):
    """Pages 1-2 are static text: fonts, sizes, colours and x/y positions must equal the reference scorecard."""
    import pymupdf
    ref = json.loads((ROOT / "tests" / "data" / "reference_pages_1_2.json").read_text())
    run(tmp_path)
    pdf = pymupdf.open(tmp_path / "out" / "sentinelq_scorecard.pdf")
    assert pdf[0].rect.width == 595.2755737304688 and pdf.metadata["author"] == "Portfolio Signal Engine (prototype)"
    for pn in ("1", "2"):
        mine = {}
        for b in pdf[int(pn) - 1].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                s = l["spans"][0]
                mine.setdefault(s["text"][:28], [round(s["bbox"][0], 1), round(s["bbox"][1], 1), s["font"], round(s["size"], 1), f'{s["color"]:06x}'])
        for text, x, y, font, size, colr in ref[pn]:
            if text in mine and not text.startswith(("Coverage", "30 holdings", "50 subset")):
                assert mine[text][2:] == [font, size, colr], text
                assert abs(mine[text][0] - x) <= 0.7 and abs(mine[text][1] - y) <= 0.7, (text, mine[text], x, y)


def test_find_claude_respects_env_and_reports_missing(tmp_path, monkeypatch):
    import pytest
    from sentinelq import claude_code as cc
    fake = tmp_path / "claude.cmd"
    fake.write_text("x")
    monkeypatch.setenv("CLAUDE_BIN", str(fake))
    assert cc.find_claude() == str(fake)
    monkeypatch.delenv("CLAUDE_BIN")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(cc.Path if hasattr(cc, "Path") else __import__("pathlib").Path, "exists", lambda self: False, raising=False)
    with pytest.raises(SystemExit):
        cc.preflight()


def test_ingest_is_cached(tmp_path):
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")

    class Once:
        n = 0
        def fetch(self, h, s, e):
            Once.n += 1
            return FileNews(FX / "news.json").fetch(h, s, e)
    p = Pipeline(r, Once(), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
                 text_cache=tmp_path / "ft.jsonl")
    p.ingest(hold)
    first = Once.n
    p.ingest(hold)
    assert Once.n == first == 3            # second ingest reads the cache, no provider calls


# ---- labelling resilience (the "285 of 338 unlabelled" failure) -----------------------------------------------
def _items(n):
    from sentinelq.models import RawItem
    return [(RawItem("X", "news", f"Co X headline {k}", "", f"https://a.b/{k}", "2026-06-01"), "Co X") for k in range(n)]


def _fake_reply(prompt):
    import re
    ids = re.findall(r'"id": "([0-9a-f]{16})"', prompt)
    return json.dumps([{"id": i, "event_type": "other", "sentiment": 0, "rationale": "r", "governance_flag": False,
                        "about_company": True} for i in ids])


def test_parse_labels_tolerates_messy_replies():
    from sentinelq.claude_code import parse_labels
    good = '[{"id":"a","sentiment":1},{"id":"b","sentiment":2}]'
    assert len(parse_labels(good)) == 2
    assert len(parse_labels("Here you go [as requested]:\n```json\n" + good + "\n```\nDone.")) == 2   # stray bracket + fence
    assert len(parse_labels('{"labels": ' + good + "}")) == 2
    assert len(parse_labels('[{"id":"a","x":1},{"id":"b","x":')) == 1                                # truncated: salvage
    assert parse_labels("") == [] and parse_labels("Sorry, I can't.") == []


def test_batch_failure_is_split_down_and_recovered(tmp_path, monkeypatch):
    """Big batches fail (timeout/garbage); the classifier must halve them until they succeed - nothing lost."""
    from sentinelq import claude_code as cc
    from sentinelq.classify import item_id

    def fake(prompt, model=None, timeout=600):
        n = prompt.count('"id": "')
        if n > 2:
            raise RuntimeError("claude CLI failed (1): request timed out")
        return _fake_reply(prompt)
    monkeypatch.setattr(cc, "run_claude", fake)
    clf = cc.ClaudeCodeClassifier(load_rubric(), batch_size=8, workers=2, log_path=tmp_path / "log.jsonl")
    saved = []
    clf.on_label = lambda it, d, raw: saved.append(item_id(it))          # persisted as they arrive
    pairs = _items(11)
    clf.prefetch(pairs)
    assert len(clf._res) == 11 and not clf.errors and len(saved) == 11
    assert (tmp_path / "log.jsonl").read_text().count("timed out") >= 1     # the reason is recorded, not swallowed


def test_single_bad_item_is_isolated_and_reported(tmp_path, monkeypatch):
    from sentinelq import claude_code as cc
    from sentinelq.classify import item_id
    pairs = _items(6)
    bad = item_id(pairs[3][0])

    def fake(prompt, model=None, timeout=600):
        if bad in prompt:
            return "I cannot label this."
        return _fake_reply(prompt)
    monkeypatch.setattr(cc, "run_claude", fake)
    clf = cc.ClaudeCodeClassifier(load_rubric(), batch_size=6, workers=1, log_path=tmp_path / "l.jsonl")
    clf.prefetch(pairs)
    assert len(clf._res) == 5 and list(clf.errors) == [bad]
    assert "no parseable labels" in clf.errors[bad]


def test_rate_limit_aborts_early_and_keeps_saved_labels(tmp_path, monkeypatch):
    import pytest
    from sentinelq import claude_code as cc
    monkeypatch.setattr(cc, "BACKOFF", (0, 0))
    calls = {"n": 0}

    def fake(prompt, model=None, timeout=600):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_reply(prompt)
        raise RuntimeError("claude CLI error: usage limit reached")
    monkeypatch.setattr(cc, "run_claude", fake)
    clf = cc.ClaudeCodeClassifier(load_rubric(), batch_size=4, workers=1, log_path=tmp_path / "l.jsonl")
    with pytest.raises(cc.LabellingAborted):
        clf.prefetch(_items(20))
    assert len(clf._res) == 4                   # the first batch is kept
    assert calls["n"] < 12                      # gave up quickly instead of grinding through all batches


def test_total_label_failure_refuses_to_write_report(tmp_path, monkeypatch):
    import pytest
    from sentinelq import claude_code as cc
    monkeypatch.setattr(cc, "run_claude", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = load_rubric()
    clf = cc.ClaudeCodeClassifier(r, log_path=tmp_path / "l.jsonl")
    p = Pipeline(r, FileNews(FX / "news.json"), None, None, clf, tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
                 text_cache=tmp_path / "ft.jsonl")
    with pytest.raises(RuntimeError, match="failed for every item|Refusing"):
        p.run(load_portfolio(ROOT / "examples" / "portfolio.csv"))
    assert not (tmp_path / "o" / "sentinelq_scorecard.pdf").exists()


def test_partial_failure_is_reported_as_label_failed_not_irrelevant(tmp_path, monkeypatch):
    from sentinelq import claude_code as cc
    from sentinelq.classify import item_id
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    news = FileNews(FX / "news.json")
    victim = news.fetch(hold[0], AS_OF, AS_OF)[0]

    def fake(prompt, model=None, timeout=600):
        if item_id(victim) in prompt:
            raise RuntimeError("claude CLI failed (1): weird")
        return _fake_reply(prompt)
    monkeypatch.setattr(cc, "run_claude", fake)
    clf = cc.ClaudeCodeClassifier(r, batch_size=4, workers=1, log_path=tmp_path / "l.jsonl")
    p = Pipeline(r, news, None, None, clf, tmp_path / "o", tmp_path / "c.jsonl", AS_OF, text_cache=tmp_path / "ft.jsonl",
                 max_label_failure=0.5)
    res = p.run(hold)
    reasons = [d.reason for d in res["dropped"] if d.url == victim.url]
    assert reasons and reasons[0].startswith("label_failed") and "weird" in reasons[0]
    assert res["run"]["totals"]["label_failed"] == 1
    assert (tmp_path / "o" / "label_failures.jsonl").exists()
