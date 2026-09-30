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
