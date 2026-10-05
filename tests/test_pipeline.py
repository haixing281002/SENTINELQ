import json
from datetime import date
from pathlib import Path

from sentinelq.classify import KeywordClassifier
from sentinelq.ingest.files import FileActions, FileNews, FilePrices
import functools
from sentinelq.pipeline import Pipeline, load_portfolio
from sentinelq.rubric import load_rubric
from sentinelq.validate import validate_label

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "examples" / "fixtures"
AS_OF = date(2026, 7, 15)

LegacyPipeline = functools.partial(Pipeline, stratified=False)   # the latest-N sampler kept for comparison


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


def _li(et, gov=True, hist=False, mat=None, date_="2026-06-01", title=None):
    from sentinelq.models import Label, LabelledItem, RawItem
    title = title or ("Chief Financial Officer resigns with immediate effect" if et == "management_exit" else et)
    return LabelledItem(RawItem("X", "news", title, "", "https://a.b/" + et + (title if title != et else ""), date_),
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
    p = LegacyPipeline(r, FileNews(FX / "news.json"), None, None, KeywordClassifier(), tmp_path / "o",
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

    def fake(prompt, model=None, timeout=600, **kw):
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

    def fake(prompt, model=None, timeout=600, **kw):
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

    def fake(prompt, model=None, timeout=600, **kw):
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

    def fake(prompt, model=None, timeout=600, **kw):
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


# ---- GDELT rate limits & missing news (the "0 articles every 3rd stock" failure) --------------------------------
class _Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body.encode()


def _http429(retry_after=None):
    import urllib.error
    hdrs = {"Retry-After": str(retry_after)} if retry_after else {}
    return urllib.error.HTTPError("u", 429, "Too Many Requests", hdrs, None)


GOOD = json.dumps({"articles": [{"title": "Angel One posts record profit", "url": "https://x.com/a", "seendate": "20260601T000000Z", "domain": "x.com"}]})


def test_gdelt_backs_off_on_429_then_succeeds():
    from sentinelq.ingest.gdelt import GdeltNews
    from sentinelq.models import Holding
    seq = [_http429(), _http429(7), _Resp(GOOD)]
    naps, msgs = [], []
    g = GdeltNews(sleep=naps.append, opener=lambda url, timeout: (_ for _ in ()).throw(seq.pop(0)) if isinstance(seq[0], Exception) else seq.pop(0))
    g.on_event = msgs.append
    items = g.fetch(Holding("ANGELONE", "Angel One", "BFSI"), date(2026, 6, 20), date(2026, 7, 1))   # one window
    assert len(items) == 1 and items[0].published == "2026-06-01"
    assert len([m for m in msgs if "429" in m]) == 2
    assert 7 in naps or 7.0 in naps                       # honoured Retry-After


def test_gdelt_plain_text_rate_limit_is_not_read_as_zero_articles():
    import pytest
    from sentinelq.ingest.gdelt import GdeltNews, GdeltRateLimited
    from sentinelq.models import Holding
    g = GdeltNews(retries=2, sleep=lambda s: None, opener=lambda url, timeout: _Resp("Please limit requests to one every 5 seconds."))
    with pytest.raises(GdeltRateLimited):
        g.fetch(Holding("X", "Co X", "S"), date(2026, 6, 20), date(2026, 7, 1))


def _flaky_news(fail_times):
    class Flaky:
        calls = {}
        def fetch(self, h, s, e):
            Flaky.calls[h.symbol] = Flaky.calls.get(h.symbol, 0) + 1
            if h.symbol == "BPCL" and Flaky.calls[h.symbol] <= fail_times:
                raise RuntimeError("HTTP 429")
            return FileNews(FX / "news.json").fetch(h, s, e)
    return Flaky()


def test_failed_stock_is_retried_and_recovered(tmp_path):
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    p = Pipeline(r, _flaky_news(1), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
                 text_cache=tmp_path / "ft.jsonl", ingest_retry_wait=0)
    raw, errs = p.ingest(hold)
    assert not errs and len([i for i in raw["BPCL"] if i.kind == "news"]) == 1


def test_persistent_news_failure_blocks_report(tmp_path):
    import pytest
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    news = _flaky_news(99)
    p = Pipeline(r, news, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
                 text_cache=tmp_path / "ft.jsonl", ingest_retry_wait=0)
    with pytest.raises(RuntimeError, match="BPCL"):
        p.ingest(hold)
    assert not (tmp_path / "ingest").exists()                                       # no article cache is ever created
    p2 = Pipeline(r, news, None, None, KeywordClassifier(), tmp_path / "o2", tmp_path / "c.jsonl", AS_OF,
                  text_cache=tmp_path / "ft.jsonl", ingest_retry_wait=0, allow_missing_news=True)
    raw, errs = p2.ingest(hold)                     # override accepts the gap
    assert errs and "BPCL" in raw


def test_generic_market_headlines_are_prefiltered_and_disclosed(tmp_path):
    from sentinelq.models import Holding, RawItem
    r = load_rubric()
    h = Holding("BAJFINANCE", "Bajaj Finance", "BFSI")

    class Fixed:
        def fetch(self, hh, s, e):
            mk = lambda t, u: RawItem("BAJFINANCE", "news", t, "", u, "2026-06-01", "x.com")
            return [mk("Sensex ends 250 pts lower as banks drag", "https://x.com/1"),
                    mk("Sensex, Nifty rally; Bajaj Finance among top gainers", "https://x.com/2"),
                    mk("Bajaj Finance Q4 profit rises 22%", "https://x.com/3")]
    p = Pipeline(r, Fixed(), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
                 text_cache=tmp_path / "ft.jsonl")
    res = p.run([h])
    reasons = {d.headline: d.reason for d in res["dropped"]}
    assert reasons["Sensex ends 250 pts lower as banks drag"] in ("generic_market_headline", "title_does_not_name_company")
    assert reasons.get("Sensex, Nifty rally; Bajaj Finance among top gainers") == "boilerplate_headline"   # names the company, but a market wrap
    assert "Bajaj Finance Q4 profit rises 22%" not in reasons




# ---- rolling window: latest N usable articles, stop as soon as N is reached -------------------------------------
def _doc_opener(per_window):
    """Fake DOC API: each requested window returns `per_window` articles dated inside that window."""
    import urllib.parse as up
    log = []

    def opener(url, timeout=30):
        q = up.parse_qs(up.urlparse(url).query)
        if "startdatetime" not in q:                       # the health-check request
            return _Resp(json.dumps({"articles": []}))
        lo, hi = q["startdatetime"][0][:8], q["enddatetime"][0][:8]
        log.append((lo, hi))
        arts = [{"title": f"Angel One story {hi}-{k}", "url": f"https://x.com/{hi}/{k}", "seendate": hi + "T000000Z", "domain": "x.com"}
                for k in range(per_window)]
        return _Resp(json.dumps({"articles": arts}))
    return opener, log


def test_window_rolls_back_only_until_enough_latest_articles():
    from sentinelq.ingest.gdelt import GdeltNews
    from sentinelq.ingest.select import count_usable, name_tokens
    from sentinelq.models import Holding
    h = Holding("ANGELONE", "Angel One", "BFSI")
    opener, log = _doc_opener(per_window=20)
    g = GdeltNews(slice_days=30, sleep=lambda s: None, opener=opener)
    toks = name_tokens(h)
    g.stop_when = lambda its: count_usable(its, toks) >= 50
    items = g.fetch(h, date(2025, 7, 3), date(2026, 7, 3))
    assert len(log) == 3 and len(items) == 60                       # 20+20+20: stopped as soon as >= 50 was in hand
    assert log[0][1] == "20260703" and log[1][1] < log[0][0]          # newest window first, then rolled back
    assert all(lo >= "20260403" for lo, _ in log)                    # never went further back than needed


def test_window_covers_full_lookback_when_articles_are_scarce():
    from sentinelq.ingest.gdelt import GdeltNews
    from sentinelq.models import Holding
    opener, log = _doc_opener(per_window=1)
    g = GdeltNews(slice_days=30, sleep=lambda s: None, opener=opener)
    g.stop_when = lambda its: len(its) >= 50
    items = g.fetch(Holding("X", "Angel One", "S"), date(2025, 7, 3), date(2026, 7, 3))
    assert len(log) >= 12 and len(items) == len(log)                # kept rolling back across the whole 12 months


def test_select_keeps_the_latest_usable_articles():
    from sentinelq.ingest.select import name_tokens, select_articles
    from sentinelq.models import Holding, RawItem
    h = Holding("BAJFINANCE", "Bajaj Finance", "BFSI")
    mk = lambda t, d, k=0: RawItem("BAJFINANCE", "news", t, "", f"https://x.com/{d}/{k}", d, "x.com")
    items = [mk(f"Bajaj Finance update {d}", d) for d in ("2026-01-05", "2026-06-20", "2026-06-25", "2026-05-01", "2026-06-30")]
    items += [mk("Sensex ends lower", "2026-07-01"), mk("Nifty rallies today", "2026-07-02")]     # newest, but not usable
    out = select_articles(items, 3, name_tokens(h))
    assert [i.published for i in out] == ["2026-06-30", "2026-06-25", "2026-06-20"]               # latest 3 usable
    assert select_articles(items, None, name_tokens(h))[0].published == "2026-07-02"              # no cap: everything


def test_pipeline_wires_stop_condition_and_caps_to_latest(tmp_path):
    from sentinelq.ingest.gdelt import GdeltNews
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")[:1]        # ANGELONE
    opener, log = _doc_opener(per_window=30)
    g = GdeltNews(slice_days=30, sleep=lambda s: None, opener=opener)
    p = LegacyPipeline(r, g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3),
                 max_articles=50, governance_pass=False, fundamentals_pass=False, text_cache=tmp_path / "ft.jsonl")
    raw, _ = p.ingest(hold)
    news = [i for i in raw["ANGELONE"] if i.kind == "news"]
    assert len(news) == 50 and len(log) == 2                              # 30+30 collected, then trimmed to 50
    assert news[0].published >= news[-1].published and news[0].published.startswith("2026-07")   # latest first
    assert min(i.published for i in news) >= "2026-05-01"                 # did not reach back further than needed


# ---- streaming, in-memory: articles are read and scored, never stored ------------------------------------------
MARKER = "ZZ-UNIQUE-ARTICLE-BODY-TEXT-ZZ"


def test_no_article_text_is_written_anywhere(tmp_path, monkeypatch):
    from sentinelq.ingest import fulltext
    monkeypatch.setattr(fulltext, "fetch_text", lambda url, *a, **k: MARKER + " Angel One results were strong " + url)
    seen = []

    class Clf(KeywordClassifier):
        def classify(self, item, company):
            seen.append(item.snippet)                  # what the model would be shown
            return super().classify(item, company)
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    p = Pipeline(r, FileNews(FX / "news.json"), None, None, Clf(), tmp_path / "out", tmp_path / "cache" / "labels.jsonl",
                 AS_OF, fetch_text=True)
    res = p.run(hold)
    assert seen and all(MARKER in x for x in seen)     # the body WAS read (so the absence below is meaningful)
    written = [f for f in tmp_path.rglob("*") if f.is_file()]
    assert written, "the run must still write its report"
    for f in written:
        if f.suffix in (".xlsx", ".pdf"):
            continue                                   # binary outputs checked separately below
        assert MARKER.encode() not in f.read_bytes(), f"article body leaked into {f}"
    assert not any("ingest" in f.parts or f.name.startswith("fulltext") for f in written)
    import zipfile
    with zipfile.ZipFile(tmp_path / "out" / "sentinelq_report.xlsx") as z:                       # xlsx is a zip of xml
        assert all(MARKER.encode() not in z.read(n) for n in z.namelist())
    assert all(li.item.snippet == "" for v in res["kept"].values() for li in v)                   # released after reading


def test_no_disk_cache_writes_nothing_but_the_report(tmp_path):
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    cache_dir = tmp_path / "cache"
    Pipeline(r, FileNews(FX / "news.json"), None, None, KeywordClassifier(), tmp_path / "out", cache_dir / "labels.jsonl", AS_OF,
             disk_cache=False).run(hold)
    assert not cache_dir.exists()


def test_reading_overlaps_with_scraping_next_stock(tmp_path):
    """Stock 2's scrape must be able to run while stock 1 is being read: it waits for the labeller to start."""
    import threading
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")[:2]
    started, saw = threading.Event(), {}

    class Clf(KeywordClassifier):
        def classify(self, item, company):
            started.set()
            return super().classify(item, company)

    class News:
        def fetch(self, h, s, e):
            if h.symbol == hold[1].symbol:
                saw["labeller_already_started"] = started.wait(timeout=10)
            return FileNews(FX / "news.json").fetch(h, s, e)
    LegacyPipeline(r, News(), None, None, Clf(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF).run(hold)
    assert saw["labeller_already_started"] is True


def test_streaming_run_equals_batch_path(tmp_path):
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    news = FileNews(FX / "news.json")
    a = Pipeline(r, news, None, None, KeywordClassifier(), tmp_path / "a", tmp_path / "ca.jsonl", AS_OF).run(hold)
    pb = Pipeline(r, news, None, None, KeywordClassifier(), tmp_path / "b", tmp_path / "cb.jsonl", AS_OF)
    b = pb.run(hold, ingested=pb.ingest(hold))
    assert [x.to_dict() for x in a["scores"]] == [x.to_dict() for x in b["scores"]]


# ---- the simple version: one request per stock, newest first, title must name the company -----------------------
def _noisy_opener(n_title, n_noise):
    """180-style response: some titles name the company, most are unrelated pages that merely mention it in the body."""
    import urllib.parse as up
    log = []

    def opener(url, timeout=30):
        q = up.parse_qs(up.urlparse(url).query)
        if "enddatetime" not in q:                         # the health-check request
            return _Resp(json.dumps({"articles": []}))
        log.append(q)
        hi = q["enddatetime"][0][:8]
        arts = [{"title": f"Bajaj Finance news item {hi} {k}", "url": f"https://x.com/t/{hi}/{k}", "seendate": hi + "T000000Z", "domain": "x.com"}
                for k in range(n_title)]
        arts += [{"title": f"Best ceiling fans {k}", "url": f"https://x.com/n/{hi}/{k}", "seendate": hi + "T000000Z", "domain": "x.com"}
                 for k in range(n_noise)]
        return _Resp(json.dumps({"articles": arts}))
    return opener, log


def test_one_request_per_stock_when_enough_titles_name_the_company(tmp_path):
    from sentinelq.ingest.gdelt import GdeltNews
    r = load_rubric()
    from sentinelq.models import Holding
    h = Holding("BAJFINANCE", "Bajaj Finance", "BFSI")
    opener, log = _noisy_opener(n_title=60, n_noise=120)                       # 180 candidates, like the real run
    g = GdeltNews(sleep=lambda s: None, opener=opener)                         # defaults: 90-day window
    p = LegacyPipeline(r, g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3), max_articles=50, governance_pass=False, fundamentals_pass=False)
    items, found, errs = p._fetch_stock(h)
    assert len(log) == 1 and not errs                                          # ONE request, no rolling back needed
    assert log[0]["sort"] == ["datedesc"] and log[0]["maxrecords"] == ["250"]  # newest first, up to 250
    news = [i for i in items if i.kind == "news"]
    assert found == 180 and len(news) == 50
    assert all("Bajaj Finance" in i.title for i in news)                       # no ceiling fans / phones in the 50


def test_steps_back_a_window_only_when_titles_are_scarce(tmp_path):
    from sentinelq.ingest.gdelt import GdeltNews
    from sentinelq.models import Holding
    r = load_rubric()
    h = Holding("BAJFINANCE", "Bajaj Finance", "BFSI")
    opener, log = _noisy_opener(n_title=20, n_noise=50)                        # 20 good titles per window
    g = GdeltNews(sleep=lambda s: None, opener=opener)
    p = LegacyPipeline(r, g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3), max_articles=50, governance_pass=False, fundamentals_pass=False)
    items, _, _ = p._fetch_stock(h)
    assert len(log) == 3                                                       # 20 + 20 + 20 titles >= 50, then stop
    assert len([i for i in items if i.kind == "news"]) == 50


def test_syndicated_duplicate_titles_are_collapsed():
    from sentinelq.ingest.select import name_tokens, select_articles
    from sentinelq.models import Holding, RawItem
    h = Holding("ANGELONE", "Angel One", "BFSI")
    mk = lambda u: RawItem("ANGELONE", "news", "Angel One posts record client base!", "", u, "2026-06-01", "x.com")
    out = select_articles([mk("https://a.com/1"), mk("https://b.com/2"), mk("https://c.com/3")], 50, name_tokens(h))
    assert len(out) == 1


def test_rate_limit_waits_are_short_and_flat():
    from sentinelq.ingest.gdelt import GdeltNews
    from sentinelq.models import Holding
    seq = [_http429(), _http429(), _http429(), _Resp(GOOD)]
    naps = []
    g = GdeltNews(sleep=naps.append, opener=lambda url, timeout: (_ for _ in ()).throw(seq.pop(0)) if isinstance(seq[0], Exception) else seq.pop(0))
    g.fetch(Holding("ANGELONE", "Angel One", "BFSI"), date(2026, 6, 20), date(2026, 7, 1))
    waits = [n for n in naps if n >= 15]
    assert waits == [20.0, 20.0, 20.0]                                         # flat 20 s, no doubling to minutes


def test_fulltext_never_blocks_past_its_time_budget(monkeypatch):
    import time
    from sentinelq.ingest import fulltext
    from sentinelq.models import RawItem

    def slow_or_fast(url, *a, **k):
        if "slow" in url:
            time.sleep(3)
        return "body " + url
    monkeypatch.setattr(fulltext, "fetch_text", slow_or_fast)
    items = [RawItem("X", "news", f"t{k}", "", f"https://x.com/{'slow' if k % 2 else 'fast'}/{k}", "2026-06-01") for k in range(6)]
    t0 = time.time()
    fulltext.enrich(items, budget=0.5)
    assert time.time() - t0 < 2.0                                              # did not wait for the slow ones
    assert sum(i.parse.startswith("full-text") for i in items) == 3
    assert sum("timed out" in i.parse for i in items) == 3                     # those fall back to the headline


# ---- GDELT throttling: fail fast instead of hammering for half an hour ------------------------------------------
def test_health_check_stops_the_run_in_seconds_when_gdelt_is_refusing(tmp_path):
    import pytest
    from sentinelq.ingest.gdelt import GdeltNews
    calls = []
    naps = []

    def always_429(url, timeout=30):
        calls.append(url)
        raise _http429()
    g = GdeltNews(sleep=naps.append, opener=always_429)
    r = load_rubric()
    p = Pipeline(r, g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF, max_articles=50)
    with pytest.raises(RuntimeError, match="Get-Process python"):
        p.run(load_portfolio(ROOT / "portfolio" / "stocks_given.tsv"))
    assert len(calls) == 4                                   # 1 try + 3 retries of the probe only - no per-stock requests
    assert sum(n for n in naps if n >= 15) == 60             # about a minute of waiting, not half an hour


def test_three_stocks_failing_in_a_row_aborts_early(tmp_path):
    import pytest
    r = load_rubric()
    hold = load_portfolio(ROOT / "portfolio" / "stocks_given.tsv")

    class Refusing:
        n = 0
        def fetch(self, h, s, e):
            Refusing.n += 1
            raise RuntimeError("HTTP 429")
    p = Pipeline(r, Refusing(), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF, ingest_retry_wait=0)
    with pytest.raises(RuntimeError, match="3 stocks in a row"):
        p.ingest(hold)
    assert Refusing.n == 3                                   # stopped after 3 of 29, not all 29


def test_non_throttle_probe_failure_is_reported_as_a_connection_problem(tmp_path):
    import pytest
    from sentinelq.ingest.gdelt import GdeltNews
    def offline(url, timeout=30):
        raise ConnectionError("network unreachable")
    g = GdeltNews(sleep=lambda s: None, opener=offline, retries=1)
    p = Pipeline(load_rubric(), g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF)
    with pytest.raises(RuntimeError, match="health check failed"):
        p.ingest(load_portfolio(ROOT / "examples" / "portfolio.csv"))


# ---- Google News RSS (mechanism extracted from Harshil's script) ------------------------------------------------
def _rss(entries):
    """entries: (title, link, 'Mon, 15 Jun 2026 09:30:00 GMT', publisher, publisher_url)"""
    from xml.sax.saxutils import escape
    items = "".join(f"<item><title>{escape(t)}</title><link>{escape(l)}</link><pubDate>{d}</pubDate>"
                    f'<source url="{u}">{escape(p)}</source></item>' for t, l, d, p, u in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>' + items + "</channel></rss>").encode()


class _RssResp(_Resp):
    def __init__(self, body): self.body = body
    def read(self): return self.body


def _gn_opener(per_window, title="Angel One update", log=None):
    """Fake Google News: each after:/before: window returns `per_window` items dated inside it."""
    import re, urllib.parse as up
    from datetime import timedelta as td
    log = log if log is not None else []

    def opener(req, timeout=30):
        q = up.parse_qs(up.urlparse(req.full_url).query)["q"][0]
        m = re.search(r"after:(\d{4}-\d\d-\d\d) before:(\d{4}-\d\d-\d\d)", q)
        if not m:                                                     # health check
            return _RssResp(_rss([]))
        lo, hi = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)) - td(days=1)
        log.append((lo, hi, q))
        span = max((hi - lo).days, 1)
        rows = []
        for k in range(per_window):
            d = hi - td(days=(k * span) // max(per_window, 1))
            rows.append((f"{title} {d} {k} - Economic Times", f"https://news.google.com/rss/articles/{d}-{k}",
                         d.strftime("%a, %d %b %Y 09:30:00 GMT"), "Economic Times", "https://economictimes.indiatimes.com"))
        return _RssResp(_rss(rows))
    return opener, log


def test_feed_parsing_matches_the_original_script():
    from sentinelq.ingest.gnews import parse_feed, strip_source_suffix
    xml = _rss([("Angel One posts record client base - Economic Times", "https://news.google.com/rss/articles/abc",
                 "Mon, 15 Jun 2026 09:30:00 GMT", "Economic Times", "https://www.economictimes.indiatimes.com")])
    (row,) = parse_feed(xml)
    assert row["title"] == "Angel One posts record client base"          # ' - Publisher' stripped
    assert row["source"] == "economictimes.indiatimes.com" and str(row["date"]) == "2026-06-15"
    assert strip_source_suffix("A - B - Mint", "Mint") == "A - B"
    assert strip_source_suffix("Plain headline", "Mint") == "Plain headline"
    assert strip_source_suffix("Angel One wins order – Moneycontrol", "") == "Angel One wins order"


def test_google_news_rolls_back_only_until_enough_and_uses_windows():
    from sentinelq.ingest.gnews import GoogleNewsRSS
    from sentinelq.ingest.select import count_usable, name_tokens
    from sentinelq.models import Holding
    h = Holding("ANGELONE", "Angel One", "BFSI", aliases="Angel One|Angel One Ltd")
    opener, log = _gn_opener(per_window=30)
    g = GoogleNewsRSS(sleep=lambda s: None, opener=opener)
    toks = name_tokens(h)
    g.stop_when = lambda its: count_usable(its, toks) >= 50
    items = g.fetch(h, date(2025, 7, 3), date(2026, 7, 3))
    assert len(log) == 2 and len(items) == 60                              # 30 + 30 >= 50, stopped before the 3rd window
    assert '"Angel One" OR "Angel One Ltd"' in log[0][2] and "after:2026-06-04 before:2026-07-04" in log[0][2]
    assert log[1][1] < log[0][0]                                           # second window is strictly older


def test_google_news_covers_the_year_when_articles_are_scarce_and_drops_out_of_window_items():
    from sentinelq.ingest.gnews import GoogleNewsRSS
    from sentinelq.models import Holding
    h = Holding("X", "Angel One", "S")
    opener, log = _gn_opener(per_window=3)
    g = GoogleNewsRSS(sleep=lambda s: None, opener=opener)
    g.stop_when = lambda its: len(its) >= 50
    items = g.fetch(h, date(2025, 7, 3), date(2026, 7, 3))
    assert len(log) == 4 and len(items) == 12                              # 30/90/180/365-day windows all used
    assert min(i.published for i in items) >= "2025-07-03"

    def ignores_operator(req, timeout=30):                                 # server that ignores after:/before:
        q = __import__("urllib.parse", fromlist=["x"]).parse_qs(__import__("urllib.parse", fromlist=["x"]).urlparse(req.full_url).query)["q"][0]
        if "after:" not in q:
            return _RssResp(_rss([]))
        return _RssResp(_rss([("Angel One old story - ET", "https://news.google.com/rss/articles/old", "Mon, 01 Jan 2024 00:00:00 GMT", "ET", "")]))
    msgs = []
    g2 = GoogleNewsRSS(sleep=lambda s: None, opener=ignores_operator, marks=(30,))
    g2.on_event = msgs.append
    assert g2.fetch(h, date(2026, 6, 1), date(2026, 7, 3)) == []          # out-of-window items are not used
    assert any("date operator ignored" in m for m in msgs)                 # ...and it says so


def test_google_news_retries_flat_and_never_reads_a_block_page_as_no_news():
    import pytest
    from sentinelq.ingest.gdelt import GdeltRateLimited, GdeltUnreachable
    from sentinelq.ingest.gnews import GoogleNewsRSS
    from sentinelq.models import Holding
    h = Holding("X", "Angel One", "S")
    seq = [_http429(), _RssResp(b"<html>consent page</html>"), _RssResp(_rss([]))]
    naps = []

    def flaky(req, timeout=30):
        r = seq.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    g = GoogleNewsRSS(sleep=naps.append, opener=flaky, marks=(30,))
    assert g.fetch(h, date(2026, 6, 1), date(2026, 7, 3)) == []
    assert [n for n in naps if n >= 15] == [20.0, 20.0]                    # flat 20 s waits
    always = GoogleNewsRSS(sleep=lambda s: None, retries=1, opener=lambda req, timeout=30: _RssResp(b"<html>blocked</html>"))
    with pytest.raises(GdeltRateLimited):
        always.fetch(h, date(2026, 6, 1), date(2026, 7, 3))
    def offline(req, timeout=30):
        raise ConnectionError("down")
    with pytest.raises(GdeltUnreachable):
        GoogleNewsRSS(sleep=lambda s: None, retries=1, opener=offline).fetch(h, date(2026, 6, 1), date(2026, 7, 3))


def test_pipeline_with_google_news_keeps_latest_50_titles_that_name_the_company(tmp_path):
    from sentinelq.ingest.gnews import GoogleNewsRSS
    from sentinelq.models import Holding
    r = load_rubric()
    opener, log = _gn_opener(per_window=40, title="Bajaj Finance")
    g = GoogleNewsRSS(sleep=lambda s: None, opener=opener)
    p = LegacyPipeline(r, g, None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3), max_articles=50, governance_pass=False, fundamentals_pass=False)
    items, found, errs = p._fetch_stock(Holding("BAJFINANCE", "Bajaj Finance", "BFSI"))
    news = [i for i in items if i.kind == "news"]
    assert not errs and len(news) == 50 and len(log) == 2                  # 40 + 40 collected, trimmed to the latest 50
    assert news == sorted(news, key=lambda i: i.published, reverse=True) and news[0].published.startswith("2026-07")
    assert all(i.source == "economictimes.indiatimes.com" for i in news)


def test_fulltext_skips_google_redirect_links(monkeypatch):
    from sentinelq.ingest import fulltext
    from sentinelq.models import RawItem
    calls = []
    monkeypatch.setattr(fulltext, "fetch_text", lambda url, *a, **k: calls.append(url) or "body")
    items = [RawItem("X", "news", "t", "", "https://news.google.com/rss/articles/abc", "2026-06-01"),
             RawItem("X", "news", "t2", "", "https://example.com/story", "2026-06-01")]
    fulltext.enrich(items)
    assert calls == ["https://example.com/story"] and items[0].parse == "headline-only (google link)"


# ---- governance: literal-rule misfires (elevations scored as exits) and the 12-month governance pass -------------
def _score_gov(*items):
    from sentinelq.score import governance
    sc, lab, pens = governance(list(items), load_rubric())
    return sc, lab, pens, governance.ignored


def test_unplanned_cxo_exit_is_penalised_angel_one_cpo():
    sc, lab, pens, ign = _score_gov(
        _li("regulatory_action", title="SEBI settlement order: Angel One pays Rs 4.28cr"),
        _li("management_exit", title="Angel One Chief Product Officer resigns, effective 31-Aug-26", date_="2026-07-02"))
    assert (sc, lab) == (72, "Flag") and {p["event_type"] for p in pens} == {"regulatory_action", "management_exit"} and not ign


def test_elevation_is_not_a_management_exit_dr_reddys_aia_nestle_pattern():
    for title in ("Dr. Reddy's elevates Kunwar Khurana as Head - Sales and Marketing",
                  "Kunwar Khurana appointed as Head - Sales and Marketing at Dr Reddy's",
                  "AIA Engineering board transition: Director moves to non-executive role",
                  "Nestle India promotes finance head to Chief Financial Officer"):
        sc, lab, pens, ign = _score_gov(_li("management_exit", title=title))     # even if the model mislabels it as an exit
        assert sc == 100 and not pens, title
        assert ign and ign[0]["event_type"] == "management_exit" and ign[0]["why"], title      # and the reason is recorded


def test_planned_or_sub_cxo_exits_are_not_penalised_but_real_ones_are():
    assert _score_gov(_li("management_exit", title="VP Operations superannuates effective 30-Jun-26"))[0] == 100
    assert _score_gov(_li("management_exit", title="Head of Regional Sales resigns"))[0] == 100                 # below CXO tier
    assert _score_gov(_li("management_exit", title="Company Secretary and Compliance Officer resigns"))[0] == 92
    assert _score_gov(_li("management_exit", title="Zen Technologies CFO resigns"))[0] == 92
    assert _score_gov(_li("management_exit", title="CEO steps down amid probe"))[0] == 92


def test_routine_event_types_carry_no_penalty_and_are_reported_as_ignored():
    sc, lab, pens, ign = _score_gov(_li("management_change_routine", title="New CFO appointed"),
                                    _li("rpt_routine", title="Board approves royalty payments to parent"),
                                    _li("investigation_closed", title="Court quashes probe"),
                                    _li("board_change_routine", title="Board reconstituted"),
                                    _li("auditor_rotation", title="Scheduled auditor rotation"))
    assert sc == 100 and not pens and len(ign) == 5


def test_classifier_prompt_defines_the_governance_types():
    from sentinelq.classify import SYSTEM, tool_schema
    r = load_rubric()
    assert "management_change_routine" in SYSTEM and "Head - Sales and Marketing" in SYSTEM and "below cxo tier" in SYSTEM.lower()
    enum = tool_schema(r)["input_schema"]["properties"]["event_type"]["enum"]
    assert {"management_change_routine", "rpt_routine", "investigation_closed", "auditor_rotation"} <= set(enum)


def test_keyword_stub_separates_exits_from_elevations():
    from sentinelq.models import RawItem
    kc = KeywordClassifier()
    lab = lambda t: kc.classify(RawItem("X", "news", t, "", "https://x/y", "2026-06-01"), "Angel One")[0]["event_type"]
    assert lab("Angel One Chief Product Officer resigns") == "management_exit"
    assert lab("Angel One appoints Kunwar Khurana as Head - Sales and Marketing") == "management_change_routine"


# ---- 12-month governance pass: finds events the latest-N headlines miss; never touches sentiment -----------------
def _gov_feed_opener():
    """Fake Google News: the plain query returns only recent, generic stories; the governance-keyword query also surfaces an
    older CPO exit and an unrelated promotion."""
    import re, urllib.parse as up
    from datetime import timedelta as td
    calls = {"sentiment": 0, "governance": 0}

    def opener(req, timeout=30):
        q = up.parse_qs(up.urlparse(req.full_url).query)["q"][0]
        m = re.search(r"after:(\d{4}-\d\d-\d\d) before:(\d{4}-\d\d-\d\d)", q)
        if not m:
            return _RssResp(_rss([]))
        lo, hi = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)) - td(days=1)
        rows = []
        if "resigns" in q:                                           # governance-keyword query
            calls["governance"] += 1
            if lo <= date(2026, 1, 20) <= hi:
                rows.append(("Angel One Chief Product Officer resigns - Economic Times", "https://news.google.com/rss/articles/cpo",
                             "Tue, 20 Jan 2026 09:30:00 GMT", "Economic Times", "https://economictimes.indiatimes.com"))
            if lo <= date(2026, 3, 2) <= hi:
                rows.append(("Angel One elevates Kunwar Khurana as Head - Sales and Marketing - Mint", "https://news.google.com/rss/articles/promo",
                             "Mon, 02 Mar 2026 09:30:00 GMT", "Mint", "https://livemint.com"))
        else:                                                        # normal query: only recent generic news
            calls["sentiment"] += 1
            for k in range(60):
                d = hi - td(days=k % 25)
                if lo <= d <= hi:
                    rows.append((f"Angel One business update {d} {k} - ET", f"https://news.google.com/rss/articles/s{d}{k}",
                                 d.strftime("%a, %d %b %Y 09:30:00 GMT"), "ET", "https://economictimes.indiatimes.com"))
        return _RssResp(_rss(rows))
    return opener, calls


def test_governance_pass_finds_old_cpo_exit_but_leaves_sentiment_alone(tmp_path):
    from sentinelq.ingest.gnews import GoogleNewsRSS
    from sentinelq.models import Holding
    r = load_rubric()
    opener, calls = _gov_feed_opener()
    h = Holding("ANGELONE", "Angel One", "BFSI")
    run_ = lambda gp: Pipeline(r, GoogleNewsRSS(sleep=lambda s: None, opener=opener), None, None, KeywordClassifier(), tmp_path / f"o{gp}",
                               tmp_path / f"c{gp}.jsonl", date(2026, 7, 3), max_articles=40, governance_pass=gp).run([h])
    off, on = run_(False), run_(True)
    assert calls["governance"] > 0
    s_off, s_on = off["scores"][0], on["scores"][0]
    assert s_off.governance_score == 100                                  # latest-40 headlines never show the January exit
    assert s_on.governance_score == 92 and [p["event_type"] for p in s_on.governance_penalties] == ["management_exit"]
    assert "Chief Product Officer" in s_on.governance_penalties[0]["headline"]
    assert [x["why"] for x in s_on.governance_ignored if "Kunwar" in x["headline"]]       # the promotion: seen, reported, NOT penalised
    assert (s_off.company_sentiment, s_off.company_sentiment_raw) == (s_on.company_sentiment, s_on.company_sentiment_raw)   # sentiment identical
    assert all(li.item.purpose == "governance" for li in on["kept"]["ANGELONE"] if "Chief Product" in li.item.title)


def test_governance_selection_dedupes_against_sentiment_set_and_caps():
    from sentinelq.ingest.select import name_tokens, select_governance
    from sentinelq.models import Holding, RawItem
    h = Holding("X", "Angel One", "S")
    mk = lambda t, u, d: RawItem("X", "news", t, "", u, d, "x.com")
    sent = [mk("Angel One Chief Product Officer resigns", "https://a/1", "2026-07-02")]
    cands = [mk("Angel One Chief Product Officer resigns", "https://a/1", "2026-07-02"),            # already in sentiment set
             mk("Angel One SEBI order on brokers", "https://a/2", "2026-05-01"),
             mk("Angel One shares rally on strong volumes", "https://a/3", "2026-06-01"),            # no governance keyword
             *[mk(f"Angel One penalty news {k}", f"https://a/p{k}", f"2026-04-{k + 1:02d}") for k in range(10)]]
    out = select_governance(cands, sent, 4, name_tokens(h))
    assert len(out) == 4 and all(i.purpose == "governance" for i in out)
    assert all("rally" not in i.title and i.url != "https://a/1" for i in out)


def test_default_is_100_articles_per_stock():
    import argparse
    from sentinelq import cli
    src = (ROOT / "sentinelq" / "cli.py").read_text()
    assert 'default=100, help="latest N articles per company' in src


# ---- enforced schema, step tracker, audit record, verify/render tools -------------------------------------------
def test_labeller_requests_enforced_schema_and_reads_structured_reply(tmp_path, monkeypatch):
    from sentinelq import claude_code as cc
    from sentinelq.classify import batch_schema
    seen = {}

    def fake(prompt, model=None, timeout=600, schema=None):
        seen["schema"] = schema
        return _fake_reply(prompt).replace("[{", '{"labels":[{', 1)[:-1] + "]}" if False else json.dumps({"labels": json.loads(_fake_reply(prompt))})
    monkeypatch.setattr(cc, "run_claude", fake)
    monkeypatch.setattr(cc, "schema_supported", lambda: True)
    clf = cc.ClaudeCodeClassifier(load_rubric(), batch_size=4, workers=1, log_path=tmp_path / "l.jsonl")
    clf.prefetch(_items(3))
    assert len(clf._res) == 3 and seen["schema"] == batch_schema(load_rubric())
    props = seen["schema"]["properties"]["labels"]["items"]["properties"]
    assert set(props["event_type"]["enum"]) == set(load_rubric().event_types) - set(load_rubric().data["reserved_event_types"]) and props["sentiment"]["maximum"] == 2


def test_schema_flag_failure_falls_back_to_validated_text(tmp_path, monkeypatch):
    from sentinelq import claude_code as cc
    calls = []

    def fake(prompt, model=None, timeout=600, schema=None):
        calls.append(schema is not None)
        if schema is not None:
            raise RuntimeError("claude CLI failed (1): unknown option --json-schema")
        return _fake_reply(prompt)
    monkeypatch.setattr(cc, "run_claude", fake)
    monkeypatch.setattr(cc, "schema_supported", lambda: True)
    clf = cc.ClaudeCodeClassifier(load_rubric(), batch_size=4, workers=1, log_path=tmp_path / "l.jsonl")
    clf.prefetch(_items(3))
    assert len(clf._res) == 3 and calls == [True, False] and clf.use_schema is False       # tried, fell back, carried on


def test_windows_cmd_shim_skips_schema(monkeypatch):
    from sentinelq import claude_code as cc
    monkeypatch.setattr(cc, "find_claude", lambda: r"C:\Users\x\AppData\Roaming\npm\claude.cmd")
    monkeypatch.setattr("os.name", "nt", raising=False)
    assert cc.schema_supported() is False


def test_display_tags_every_step_and_tracks_each_stock(tmp_path):
    import io
    from sentinelq.progress import Display
    buf = io.StringIO()
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    Pipeline(r, FileNews(FX / "news.json"), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", AS_OF,
             ui=Display("plain", tmp_path / "run.log", stream=buf)).run(hold)
    out = buf.getvalue()
    for n, name in enumerate(["INPUT", "INGEST", "CLASSIFY", "VALIDATE", "SCORE", "REPORT"], 1):
        assert f"[STEP {n}/6 {name}]" in out, name
    assert "STEP TRACKER" in out and "progress: 1 Input" in out and "governance 100 -20 regulatory action -8 management exit = 72 FLAG" in out
    log = (tmp_path / "run.log").read_text()                                              # the same record goes to the log file
    assert all(f"[STEP {n}/6 {name}]" in log for n, name in enumerate(["INPUT", "INGEST", "CLASSIFY", "VALIDATE", "SCORE", "REPORT"], 1))
    assert log.count("[STEP 2/6 INGEST]") == len(hold) and log.count("[STEP 5/6 SCORE]") == len(hold)   # once per stock per step


def test_audit_record_is_complete_and_checksums_match(tmp_path):
    import hashlib
    from sentinelq.ingest.gnews import GoogleNewsRSS
    r = load_rubric()
    opener, _ = _gn_opener(per_window=5, title="Angel One")
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")[:1]
    Pipeline(r, GoogleNewsRSS(sleep=lambda s: None, opener=opener), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl",
             date(2026, 7, 3), max_articles=10, governance_pass=False, fundamentals_pass=False).run(hold)
    ad = tmp_path / "o" / "audit"
    rec = (ad / "RUN_RECORD.md").read_text()
    for h in ("## Parameters", "## Step 1 - Input", "## Step 2 - Ingest", "## Step 3 - Classify", "## Step 4 - Validate", "## Step 5 - Score", "## Step 6 - Report"):
        assert h in rec, h
    qs = [json.loads(l) for l in (ad / "queries.jsonl").read_text().splitlines()]
    assert qs and all(q["status"] == "ok" and "news.google.com/rss/search" in q["url"] for q in qs)
    man = json.loads((ad / "manifest.json").read_text())
    assert man["rubric_sha256"] == r.sha256 and "sentinelq_scorecard.pdf" in man["files"]
    for rel, info in man["files"].items():
        assert hashlib.sha256((tmp_path / "o" / rel).read_bytes()).hexdigest() == info["sha256"], rel
    assert "sentiment-total" in (ad / "score_workings.csv").read_text()


def _quick_run(tmp_path, name="o"):
    r = load_rubric()
    hold = load_portfolio(ROOT / "examples" / "portfolio.csv")
    Pipeline(r, FileNews(FX / "news.json"), FileActions(FX / "actions.json"), None, KeywordClassifier(), tmp_path / name,
             tmp_path / "c.jsonl", AS_OF).run(hold)
    return tmp_path / name


def test_verify_reproduces_scores_and_catches_tampering(tmp_path, capsys):
    from sentinelq import tools
    run = _quick_run(tmp_path)
    assert tools.main(["verify", str(run)]) == 0 and "ALL SCORES REPRODUCED" in capsys.readouterr().out
    p = run / "scores.json"
    d = json.loads(p.read_text())
    d[0]["governance_score"] = 95.0
    p.write_text(json.dumps(d))
    assert tools.main(["verify", str(run)]) == 1 and "MISMATCH" in capsys.readouterr().out
    ev = run / "evidence.json"                                                       # also catches an edited label
    d[0]["governance_score"] = 72.0
    p.write_text(json.dumps(d))
    rows = json.loads(ev.read_text())
    next(x for x in rows if x["event_type"] == "regulatory_action")["event_type"] = "other"
    ev.write_text(json.dumps(rows))
    assert tools.main(["verify", str(run)]) == 1


def test_render_rebuilds_pdf_from_saved_files_and_picks_up_edited_commentary(tmp_path, capsys):
    import pymupdf
    from sentinelq import tools
    run = _quick_run(tmp_path)
    nar = json.loads((run / "narrative.json").read_text())
    nar["holdings"]["ANGELONE"]["one_line_read"] = "EDITED BY A HUMAN"
    (run / "narrative.json").write_text(json.dumps(nar))
    assert tools.main(["render", str(run), "--out-name", "rebuilt.pdf"]) == 0
    text = " ".join(" ".join(pg.get_text().split()) for pg in pymupdf.open(run / "rebuilt.pdf"))     # PDF wraps cell text
    assert "EDITED BY A HUMAN" in text and "Scoring rubric" in text


def test_inspect_input_lists_aliases(capsys):
    from sentinelq import tools
    assert tools.main(["inspect", "input", "--pick", "Titan Company"]) == 0
    assert "Titan Company|Titan Co|Titan Industries" in capsys.readouterr().out


def test_every_step_has_a_skill():
    for n in ("step1-input", "step2-ingest", "step3-classify", "step4-validate", "step5-score", "step6-report"):
        f = ROOT / ".claude" / "skills" / f"sentinelq-{n}" / "SKILL.md"
        assert f.exists() and f.read_text().startswith("---\nname: sentinelq-" + n)
    assert (ROOT / ".claude" / "skills" / "sentinelq-audit" / "SKILL.md").exists()
