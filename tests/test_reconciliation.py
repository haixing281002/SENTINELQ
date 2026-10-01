"""Regression tests built from the 1-Oct-2026 Reconciliation: its table of 15 misapplied penalties (s5), the seven fixes (s7) and the
reconciled governance scores (s6). Each test names the row / fix it guards."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from sentinelq.classify import KeywordClassifier
from sentinelq.ingest.files import FileNews
from sentinelq.models import Holding, Label, LabelledItem, RawItem
import functools
from sentinelq.pipeline import Pipeline
from sentinelq.rubric import load_rubric
from sentinelq.score import evidence_standard, governance

ROOT = Path(__file__).resolve().parent.parent
R = load_rubric()
_n = [0]

LegacyPipeline = functools.partial(Pipeline, stratified=False)   # the latest-N sampler kept for comparison


def gi(event_type, title, when="2026-05-01", flag=True, sent=-1, hist=False, **gate):
    _n[0] += 1
    it = RawItem("X", "news", title, "", f"https://x.com/{_n[0]}", when)
    return LabelledItem(it, Label(event_type, sent, "r", flag, None, hist, **gate))


def gov(*items):
    sc, lab, pens = governance(list(items), R)
    return sc, lab, pens, governance.ignored


# ---------------------------------------------------------------------------------------------------- s5: direction errors (7)
DIRECTION = [("AIA Engineering reappoints Managing Director", "reappointment", "AIA"),
             ("3M India announces orderly MD succession", "planned_exit_or_succession", "3M"),
             ("Dr. Reddy's elevates Kunwar Khurana as Head - Sales and Marketing", "promotion_or_elevation", "DRL"),
             ("Titan elevates head of international brands", "promotion_or_elevation", "Titan"),
             ("AU Small Finance Bank appoints Executive Director", "appointment", "AU SFB"),
             ("Hindustan Copper: new CMD appointed", "appointment", "HCL"),
             ("Nestle India names incoming Associate General Counsel", "appointment", "Nestle")]


@pytest.mark.parametrize("title,direction,who", DIRECTION)
def test_direction_error_is_not_penalised_when_the_gate_is_answered_correctly(title, direction, who):
    sc, _, pens, ign = gov(gi("management_exit", title, subject="company", severity="material", people_direction=direction, role_tier="senior_management"))
    assert sc == 100 and not pens and ign, who


@pytest.mark.parametrize("title,direction,who", DIRECTION)
def test_direction_error_is_caught_even_if_the_model_gets_the_gate_wrong(title, direction, who):
    """Safety net: the model says 'unplanned exit / CXO', but the headline is plainly an appointment / elevation / succession."""
    sc, _, pens, ign = gov(gi("management_exit", title, subject="company", severity="material", people_direction="unplanned_exit", role_tier="cxo_cs_cfo_compliance"))
    assert sc == 100 and not pens and ign, who


def test_real_unplanned_cxo_exits_are_still_penalised():
    for title in ("Angel One Chief Product Officer resigns, effective 31-Aug-26", "Zen Technologies CFO resigns", "Company Secretary and Compliance Officer quits"):
        sc, *_ = gov(gi("management_exit", title, subject="company", severity="material", people_direction="unplanned_exit", role_tier="cxo_cs_cfo_compliance"))
        assert sc == 92, title


# ---------------------------------------------------------------------------------------------------- s5: subject errors (4)
@pytest.mark.parametrize("title,gate,who", [
    ("Government hikes customs duty on gold and silver", dict(subject="macro_policy", action_stage="order_or_settlement", severity="material"), "Titan"),
    ("Cough-syrup sales rules tightened", dict(subject="peer_or_sector", action_stage="order_or_settlement", severity="material"), "Dr Reddy's"),
    ("NMDC took steps to address MoEF issues", dict(subject="company", action_stage="routine", severity="n/a"), "NMDC"),
    ("Gold appraisers arrested for cheating Canara Bank", dict(subject="victim", action_stage="investigation", severity="material"), "Canara")])
def test_subject_error_is_not_penalised(title, gate, who):
    et = "investigation" if "appraisers" in title else "regulatory_action"
    sc, _, pens, ign = gov(gi(et, title, **gate))
    assert sc == 100 and not pens and ign, who


@pytest.mark.parametrize("title,et", [("Government hikes customs duty on gold and silver", "regulatory_action"), ("Cough-syrup sales rules tightened", "regulatory_action"),
                                      ("Gold appraisers arrested for cheating Canara Bank", "investigation")])
def test_subject_error_is_caught_by_text_rules_when_the_model_omits_the_subject(title, et):
    assert gov(gi(et, title))[0] == 100


# ---------------------------------------------------------------------------------------------------- s5: materiality errors (2)
def test_eicher_rs_1_64_cr_customs_demand_is_procedural_not_minus_20():
    sc, _, pens, _ = gov(gi("regulatory_action", "Eicher Motors receives customs demand of Rs 1.64 crore", subject="company",
                            action_stage="notice_or_demand", severity="procedural", amount_inr_cr=1.64))
    assert sc == 95 and pens[0]["penalty"] == -5


def test_bank_of_maharashtra_rs_64_lakh_brsr_penalties_are_procedural():
    sc, *_ = gov(gi("regulatory_action", "Bank of Maharashtra BRSR discloses penalties of Rs 64 lakh", subject="company",
                    action_stage="order_or_settlement", severity="immaterial", amount_inr_cr=0.64))
    assert sc == 95


def test_materiality_floor_applies_even_if_the_model_gives_only_the_amount():
    assert gov(gi("regulatory_action", "Customs demand Rs 1.64 crore", subject="company", action_stage="order_or_settlement", severity="material", amount_inr_cr=1.64))[0] == 95
    assert gov(gi("regulatory_action", "Customs order Rs 40 crore", subject="company", action_stage="order_or_settlement", severity="material", amount_inr_cr=40))[0] == 80


def test_integrity_type_matters_ignore_the_floor_angel_one_sebi_settlement_rs_4_28_cr():
    sc, _, pens, _ = gov(gi("regulatory_action", "SEBI settlement order: Angel One pays Rs 4.28cr over supervision failures", subject="company",
                            action_stage="order_or_settlement", severity="integrity", amount_inr_cr=4.28))
    assert sc == 80 and pens[0]["penalty"] == -20


# ---------------------------------------------------------------------------------------------------- s5: double count (1)
def test_nestle_notice_and_probe_next_day_are_one_event_by_key():
    sc, _, pens, ign = gov(
        gi("regulatory_action", "FSSAI issues notice to Nestle India over Maggi", "2026-06-14", subject="company", action_stage="notice_or_demand", severity="material", event_key="fssai-maggi-notice"),
        gi("investigation", "FSSAI begins probe of Nestle Maggi", "2026-06-15", subject="company", action_stage="investigation", severity="material", event_key="fssai-maggi-notice"))
    assert len(pens) == 1 and sc == 90 and any("same event" in x["why"] for x in ign)


def test_same_event_within_7_days_is_one_event_even_without_a_key():
    sc, _, pens, ign = gov(
        gi("regulatory_action", "FSSAI notice to Nestle India on Maggi noodles", "2026-06-14", subject="company", action_stage="notice_or_demand", severity="material"),
        gi("investigation", "FSSAI probe on Nestle India Maggi noodles", "2026-06-15", subject="company", action_stage="investigation", severity="material"))
    assert len(pens) == 1 and ign


def test_nestle_double_count_survives_different_wording_and_different_keys():
    """Found by running the real model: notice (14-Jun) and 'probe' (15-Jun) came back with different event keys and different wording."""
    sc, _, pens, ign = gov(
        gi("regulatory_action", "FSSAI issues notice to Nestle India over Maggi noodles", "2026-06-14", subject="company", action_stage="notice_or_demand",
           severity="procedural", event_key="fssai-notice-nestle-india-maggi-2026-06"),
        gi("investigation", "FSSAI begins probe of Nestle India Maggi", "2026-06-15", subject="company", action_stage="investigation",
           severity="material", event_key="fssai-probe-nestle-india-maggi-2026-06"))
    assert len(pens) == 1 and any("same event" in x["why"] for x in ign)


def test_two_different_regulators_within_a_week_are_not_merged():
    sc, _, pens, _ = gov(gi("regulatory_action", "SEBI order on Angel One over supervision failures", "2026-06-10", subject="company", action_stage="order_or_settlement", severity="integrity"),
                         gi("investigation", "CBI searches Angel One premises over fraud complaint", "2026-06-12", subject="company", action_stage="investigation", severity="material"))
    assert len(pens) == 2 and sc == 70


def test_distinct_events_of_different_types_are_both_penalised():
    sc, _, pens, _ = gov(gi("regulatory_action", "SEBI order on Angel One", "2026-03-01", subject="company", action_stage="order_or_settlement", severity="integrity"),
                         gi("investigation", "CBI searches AU Bank premises over fraud", "2026-06-20", subject="company", action_stage="investigation", severity="material"))
    assert sc == 70 and len(pens) == 2


# ---------------------------------------------------------------------------------------------------- s5: genuine penalties stay
def test_genuine_penalties_remain():
    assert gov(gi("investigation", "Bajaj Auto ransomware investigation", subject="company", action_stage="investigation", severity="material"))[0] == 90
    assert gov(gi("exchange_fine", "Hindustan Copper fined Rs 19.11 lakh by exchange", subject="company", severity="procedural", amount_inr_cr=0.19))[0] == 95
    assert gov(gi("rpt_concern", "Rs 9,068 cr related-party acquisition from parent", subject="company", action_stage="n/a", severity="material", amount_inr_cr=9068))[0] == 88


def test_resolved_or_quashed_matters_are_not_a_live_overhang():
    sc, _, _, ign = gov(gi("regulatory_action", "Grindwell GST show cause notice dropped to nil", subject="company", action_stage="resolved_or_quashed", severity="procedural"))
    assert sc == 100 and "resolved" in ign[0]["why"]


def test_notice_without_order_is_a_minus_10_overhang_not_minus_20():
    sc, _, pens, _ = gov(gi("regulatory_action", "FSSAI notice to Nestle India", subject="company", action_stage="notice_or_demand", severity="material", amount_inr_cr=50))
    assert pens[0]["penalty"] == -10 and sc == 90


# ---------------------------------------------------------------------------------------------------- s7 fix 3: written conventions
def test_memory_discount_only_when_no_in_window_penalty_exists():
    hist = lambda: gi("regulatory_action", "RBI lifted curbs on Bajaj Finance products; 2023 episode", "2026-04-01", hist=True, subject="company", action_stage="order_or_settlement", severity="material")
    assert gov(hist())[0] == 95                                                                                 # nothing else -> -5 memory discount
    sc, _, pens, ign = gov(hist(), gi("management_exit", "Kajaria subsidiary CFO terminated", "2025-12-22", subject="company", severity="material",
                                      people_direction="unplanned_exit", role_tier="cxo_cs_cfo_compliance"))
    assert sc == 92 and all("historical" not in p["event_type"] for p in pens) and any("memory discount not applied" in x["why"] for x in ign)


def test_corporate_action_repeat_headlines_count_once():
    from sentinelq.lint import unique_actions
    mk = lambda t, d: LabelledItem(RawItem("X", "news", t, "", f"https://x/{t}{d}", d), Label("dividend", 1, "r", False))
    items = [mk("Nestle India special dividend Rs 2", "2026-06-01"), mk("Nestle special dividend of Rs 2 per share", "2026-06-02"),
             mk("Dividend Rs 2 for Nestle India shareholders", "2026-06-05"), mk("Nestle India dividend Rs 7 (Feb)", "2026-02-07")]
    assert len(unique_actions(items, R)) == 2                                                                   # Rs 2 once, Rs 7 once


# ---------------------------------------------------------------------------------------------------- s7 fix 5: price language
def test_price_only_headlines_are_not_sentiment_evidence():
    from sentinelq.lint import price_only, strip_price_sentences
    assert price_only("Grindwell Norton shares fall 3% in sell-off", R) and price_only("Bajaj Finance extends fifth straight gain", R)
    assert not price_only("AIA Engineering stock rose 9% to a 52-week high after strong Q4 profit", R)       # carries a fundamental event
    text = "Revenue rose 16.6% in Q4. The stock hit a 52-week high on 16-Jun-26. The share price also slipped on sell-off. [context] Shares trade at 30x."
    clean, removed = strip_price_sentences(text, R)
    assert "Revenue rose" in clean and "52-week" not in clean and "sell-off" not in clean and "[context]" not in clean and len(removed) == 2


def test_pipeline_relabels_a_price_only_headline_as_price_move_and_excludes_it(tmp_path):
    class News:
        def fetch(self, h, s, e):
            return [RawItem(h.symbol, "news", "Grindwell Norton shares fall 3% in sell-off", "", "https://x.com/a", "2026-06-20", "x.com"),
                    RawItem(h.symbol, "news", "Grindwell Norton Q4 net profit up 12.5%", "", "https://x.com/b", "2026-06-25", "x.com")]
    res = Pipeline(R, News(), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3), governance_pass=False,
                   fundamentals_pass=False).run([Holding("GRINDWELL", "Grindwell Norton", "Capital Goods")])
    kinds = {li.item.url: li.label.event_type for li in res["kept"]["GRINDWELL"]}
    assert kinds["https://x.com/a"] == "price_move" and res["scores"][0].relevant_articles == 1


# ---------------------------------------------------------------------------------------------------- s7 fix 6: minimum evidence standard
STD = load_rubric(overrides={"sentiment": {"min_relevant_articles": 8, "require_results_print": True}})


def _ev(n, results=False, etype="operating_update"):
    items = [LabelledItem(RawItem("X", "news", f"t{i}", "", f"https://x/{i}", (date(2026, 7, 1) - timedelta(days=20 * i)).isoformat()),
                          Label("earnings_beat" if (results and i == 0) else etype, 1, "r", False)) for i in range(n)]
    return evidence_standard(items, date(2026, 7, 3), STD)


def test_insufficient_data_not_a_forced_score():
    assert "only 5 relevant" in _ev(5)["insufficient"]
    assert _ev(8, results=False, etype="analyst_action")["insufficient"].startswith("no results")
    assert _ev(8, results=True, etype="analyst_action")["insufficient"] == ""
    price = [LabelledItem(RawItem("X", "news", f"p{i}", "", f"https://x/p{i}", "2026-06-01"), Label("price_move", 0, "r", False)) for i in range(10)]
    assert evidence_standard(price, date(2026, 7, 3), STD)["n"] == 0                                              # price items never count


def test_pipeline_reports_n_a_not_plus_one(tmp_path):
    class News:
        def fetch(self, h, s, e):
            return [RawItem(h.symbol, "news", f"Polycab India update {k}", "", f"https://x.com/{k}", "2026-06-2%d" % k, "x.com") for k in range(3)]
    r = load_rubric(overrides={"sentiment": {"min_relevant_articles": 8, "require_results_print": True}})
    res = LegacyPipeline(r, News(), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl", date(2026, 7, 3), governance_pass=False,
                   fundamentals_pass=False).run([Holding("POLYCAB", "Polycab India", "Consumption")])
    s = res["scores"][0]
    assert s.company_sentiment is None and "INSUFFICIENT DATA" in s.sentiment_note and s.low_confidence


# ---------------------------------------------------------------------------------------------------- s7 fix 1: retrieval policy
def test_backdated_live_search_is_refused_or_stamped_and_auto_uses_the_archive():
    from sentinelq.retrieval import resolve_news_source as rs
    today, past = date(2026, 10, 1), date(2026, 7, 3)
    assert rs("auto", past, today, 3) == ("gdelt", "dated_archive")
    assert rs("auto", today, today, 3) == ("gnews", "live")
    assert rs("auto", today - timedelta(days=3), today, 3) == ("gnews", "live")                                   # boundary
    with pytest.raises(SystemExit, match="NOT a point-in-time backtest"):
        rs("gnews", past, today, 3)
    assert rs("gnews", past, today, 3, allow_live_backdate=True) == ("gnews", "live_index_backdated")
    assert rs("file", past, today, 3)[1] == "supplied"


def test_backdated_live_run_is_stamped_in_the_pdf(tmp_path):
    import pymupdf
    res = Pipeline(R, FileNews(ROOT / "examples/fixtures/news.json"), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl",
                   date(2026, 7, 3), retrieval_mode="live_index_backdated", run_date=date(2026, 10, 1)).run([Holding("ANGELONE", "Angel One", "BFSI")])
    txt = " ".join(pymupdf.open(tmp_path / "o" / "sentinelq_scorecard.pdf")[0].get_text().split())
    assert "Retrospective run" in txt and "NOT a point-in-time backtest" in txt
    assert json.loads((tmp_path / "o" / "run_meta.json").read_text())["retrieval"]["mode"] == "live_index_backdated"


# ---------------------------------------------------------------------------------------------------- s7 fix 4: universe lock
def test_universe_change_refuses_until_confirmed(tmp_path):
    from sentinelq.universe import check_universe
    lock = tmp_path / "lock.json"
    syms = [f"S{i}" for i in range(30)]
    h0, m0 = check_universe("p.csv", syms, lock)
    assert "first time" in m0
    assert check_universe("p.csv", list(reversed(syms)), lock)[0] == h0                                             # order does not matter
    with pytest.raises(SystemExit, match="REMOVED \\['S29'\\]"):
        check_universe("p.csv", syms[:29], lock)                                                                   # JB Chemicals vanishes -> refused
    h1, m1 = check_universe("p.csv", syms[:29], lock, confirm=True)
    assert "CONFIRMED" in m1 and h1 != h0
    assert check_universe("p.csv", syms[:29], lock)[0] == h1                                                       # now the baseline
    with pytest.raises(SystemExit, match="--expect-count is 30"):
        check_universe("p.csv", syms[:29], lock, expect_count=30)


# ---------------------------------------------------------------------------------------------------- s7 fix 7 + s6: verified evidence, union-then-rescore
RECONCILED = {"NAM-INDIA": (65, "Flag"), "KAJARIACER": (82, "Watch"), "BOSCHLTD": (83, "Watch"), "HONAUT": (92, "Watch"),
              "MARICO": (87, "Watch"), "MAHABANK": (95, "Clean")}


def test_verified_events_reproduce_the_reconciled_governance_scores(tmp_path):
    """s6 of the Reconciliation: Nippon 65 FLAG; Kajaria 82; Bosch 83; Honeywell 92; Marico 87; Bank of Maharashtra 95 - from the verified evidence alone."""
    hold = [Holding(s, s, "X") for s in RECONCILED]
    res = Pipeline(R, FileNews(ROOT / "examples/fixtures/news.json"), None, None, KeywordClassifier(), tmp_path / "o", tmp_path / "c.jsonl",
                   date(2026, 7, 3), governance_pass=False, fundamentals_pass=False, verified_events=ROOT / "portfolio/verified_events.csv").run(hold)
    got = {s.symbol: (s.governance_score, s.governance_label) for s in res["scores"]}
    assert got == RECONCILED
    meta = json.loads((tmp_path / "o" / "run_meta.json").read_text())
    assert len(meta["verified"]["applied"]) == 11 and meta["verified"]["file"].endswith("verified_events.csv")


def test_verified_in_force_facts_apply_only_at_the_date_they_were_verified_for(tmp_path):
    from sentinelq.verified import load_verified
    items, skipped = load_verified(ROOT / "portfolio/verified_events.csv", date(2026, 9, 30), 365, {"NAM-INDIA", "MARICO"})
    assert len(items) == 1 and items[0].item.title.startswith("SEBI settlement ~Rs 96 cr")                                           # dated event only; in-force ones skipped
    assert any("verified for as-of 2026-07-03" in s["why"] for s in skipped)
    items2, _ = load_verified(ROOT / "portfolio/verified_events.csv", date(2024, 1, 1), 365, {"NAM-INDIA"})
    assert items2 == []                                                                                               # event after the as-of date is never injected


def test_model_found_event_is_superseded_not_double_counted_by_the_verified_one():
    model = gi("regulatory_action", "SEBI settles Nippon Life AMC case over Yes Bank AT1", "2026-04-23", subject="company", action_stage="order_or_settlement", severity="integrity")
    ver = LabelledItem(RawItem("X", "news", "SEBI settlement ~Rs 96 cr", "", "", "2026-04-15", "human-verified", source_ref="Reuters", verified="verified", penalty_override=-20.0),
                       Label("regulatory_action", -1, "v", True, subject="company", severity="integrity"))
    sc, _, pens, ign = gov(model, ver)
    assert sc == 80 and len(pens) == 1 and pens[0]["verified"] == "verified"


def test_merge_is_union_then_rescore_never_an_average(tmp_path, capsys):
    from sentinelq import tools
    r = load_rubric()
    base = [Holding("ANGELONE", "Angel One", "BFSI")]
    mk = lambda items: type("N", (), {"fetch": lambda self, h, s, e: items})()
    cpo = RawItem("ANGELONE", "news", "Angel One Chief Product Officer resigns", "", "https://x.com/cpo", "2026-07-02", "x.com")
    sebi = RawItem("ANGELONE", "news", "SEBI settlement order: Angel One pays Rs 4.28cr", "", "https://x.com/sebi", "2026-06-15", "x.com")
    for name, items in (("a", [sebi]), ("b", [cpo, sebi])):
        LegacyPipeline(r, mk(items), None, None, KeywordClassifier(), tmp_path / name, tmp_path / f"c{name}.jsonl", date(2026, 7, 3),
                 governance_pass=False, fundamentals_pass=False).run(base)
    assert tools.main(["merge", str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(tmp_path / "m"), "--verified", str(tmp_path / "none.csv")]) == 0
    sa = json.loads((tmp_path / "a" / "scores.json").read_text())[0]["governance_score"]
    sb = json.loads((tmp_path / "b" / "scores.json").read_text())[0]["governance_score"]
    sm = json.loads((tmp_path / "m" / "scores.json").read_text())[0]["governance_score"]
    assert (sa, sb, sm) == (80, 72, 72) and sm != (sa + sb) / 2                                                       # union -> 72, not the average 76
    assert "never averaged" in (tmp_path / "m" / "MERGE_RECORD.md").read_text()
    assert (tmp_path / "m" / "sentinelq_scorecard.pdf").exists()


# ---------------------------------------------------------------------------------------------------- s3 cause 1: the 12-month fundamentals pass
def test_fundamentals_pass_brings_the_full_year_results_prints_into_sentiment(tmp_path):
    import re, urllib.parse as up
    from sentinelq.ingest.gnews import GoogleNewsRSS
    sys_path = str(ROOT / "tests")
    import importlib.util
    spec = importlib.util.spec_from_file_location("t0", ROOT / "tests" / "test_pipeline.py")
    t0 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t0)

    def opener(req, timeout=30):
        q = up.parse_qs(up.urlparse(req.full_url).query)["q"][0]
        m = re.search(r"after:(\d{4}-\d\d-\d\d) before:(\d{4}-\d\d-\d\d)", q)
        if not m:
            return t0._RssResp(t0._rss([]))
        lo, hi = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)) - timedelta(days=1)
        rows = []
        if "net profit" in q:                                                                   # the results-type query: one print per quarter
            rows.append((f"Bajaj Finance Q net profit rises {hi} - ET", f"https://news.google.com/rss/articles/res{hi}", hi.strftime("%a, %d %b %Y 09:30:00 GMT"), "ET", "https://et.com"))
        elif "resigns" not in q:                                                                # latest-N: only the last 3 weeks
            for k in range(30):
                d = hi - timedelta(days=k % 20)
                if lo <= d <= hi:
                    rows.append((f"Bajaj Finance market update {d} {k} - ET", f"https://news.google.com/rss/articles/u{d}{k}", d.strftime("%a, %d %b %Y 09:30:00 GMT"), "ET", "https://et.com"))
        return t0._RssResp(t0._rss(rows))
    h = Holding("BAJFINANCE", "Bajaj Finance", "BFSI")
    run_ = lambda fp: LegacyPipeline(R, GoogleNewsRSS(sleep=lambda s: None, opener=opener), None, None, KeywordClassifier(), tmp_path / f"o{fp}", tmp_path / f"c{fp}.jsonl",
                               date(2026, 7, 3), max_articles=30, governance_pass=False, fundamentals_pass=fp).run([h])
    off, on = run_(False)["scores"][0], run_(True)["scores"][0]
    assert off.evidence_span_days is not None and off.evidence_span_days < 30                  # collapsed onto the last weeks
    assert on.evidence_span_days > 250                                                         # the year's results prints are now in the evidence
