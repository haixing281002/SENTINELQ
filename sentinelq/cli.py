from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path

from .pipeline import Pipeline, load_portfolio
from .rubric import load_rubric


def main(argv=None):
    import sys
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "run":                 # `python -m sentinelq run ...` (Upgrade v2.1 B4) is the normal weekly run
        args = args[1:]
        argv = args
    if args and args[0] in ("inspect", "verify", "render", "merge", "learn", "replay", "relabel", "backtest", "diff", "golden"):      # manual-verification tools (see tools.py)
        from .tools import main as tools_main
        raise SystemExit(tools_main(args))
    p = argparse.ArgumentParser(prog="sentinelq", description="Sentinel Q pipeline")
    p.add_argument("--portfolio", help="CSV with symbol,name,sector[,cap,weight]")
    p.add_argument("--pick", help="comma-separated names/tickers to look up in --universe")
    p.add_argument("--universe", default="portfolio/universe.csv")
    p.add_argument("--out", default=None, help="output dir (default runs/<as_of>)")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default today)")
    p.add_argument("--rubric", default=None)
    p.add_argument("--mode", choices=["absolute", "relative"], default="absolute")
    p.add_argument("--cache", default=".cache/labels.jsonl")
    p.add_argument("--classifier", choices=["claude-code", "file", "anthropic", "keyword"], default="claude-code",
                   help="claude-code: local `claude -p`, no API key (default); file: hand-off files; anthropic: API key; keyword: offline stub")
    p.add_argument("--model", default=None, help="Anthropic model id (or env SENTINELQ_MODEL)")
    p.add_argument("--news", choices=["auto", "gnews", "gdelt", "file"], default="auto",
                   help="auto (default) = gdelt for a past as-of date (dated archive) else gnews; gnews = Google News RSS (LIVE index); gdelt = GDELT DOC API; file = your own JSON")
    p.add_argument("--gdelt-slice-days", type=int, default=90, help="DOC API window size in days (max useful is 90)")
    p.add_argument("--news-file")
    p.add_argument("--actions", choices=["yahoo", "file", "none"], default="yahoo")
    p.add_argument("--actions-file")
    p.add_argument("--prices", choices=["yahoo", "file", "none"], default="yahoo")
    p.add_argument("--prices-file")
    p.add_argument("--max-articles", type=int, default=100, help="latest N articles per company whose title names it (default 100; 0 = all)")
    p.add_argument("--title", default="Portfolio", help="report title, e.g. 'QVM Portfolio'")
    p.add_argument("--coverage", default="", help="coverage blurb on page 1")
    p.add_argument("--narrator", choices=["claude-code", "file", "anthropic", "template"], default=None,
                   help="prose writer for the PDF (default: same as --classifier)")
    p.add_argument("--batch-size", type=int, default=8, help="items per claude call (claude-code mode)")
    p.add_argument("--workers", type=int, default=2, help="parallel claude calls (lower if you hit rate limits)")
    p.add_argument("--allow-partial-labels", action="store_true", help="write the report even if many items failed to label")
    p.add_argument("--progress", choices=["auto", "live", "plain", "off"], default="auto",
                   help="live = redrawing bar (real terminal); plain = line-by-line (logs, Claude Code); auto picks")
    p.add_argument("--log", default="work/run.log", help="plain-text copy of the run display (tail -f it)")
    p.add_argument("--ascii", action="store_true", help="ASCII bars (#---) instead of block characters")
    p.add_argument("--allow-missing-news", action="store_true", help="continue even if news fetch failed for some stocks")
    p.add_argument("--allow-live-backdate", action="store_true", help="allow a LIVE news index with a past as-of date (stamped 'not a point-in-time backtest')")
    p.add_argument("--confirm-universe", action="store_true", help="accept a changed stock list (otherwise a change since the last confirmed run refuses to run)")
    p.add_argument("--expect-count", type=int, default=None, help="refuse to run unless the stock list has exactly this many stocks")
    p.add_argument("--verified-events", default="portfolio/verified_events.csv", help="human-verified evidence merged before scoring (union-then-rescore)")
    p.add_argument("--no-verified-events", action="store_true")
    p.add_argument("--no-fundamentals-pass", action="store_true", help="skip the 12-month results-type search that anchors sentiment")
    p.add_argument("--fundamentals-cap", type=int, default=40, help="max results-type articles per stock from the 12-month pass")
    p.add_argument("--no-governance-pass", action="store_true", help="skip the separate 12-month governance-keyword search")
    p.add_argument("--governance-cap", type=int, default=30, help="max governance-candidate articles per stock from the 12-month pass")
    p.add_argument("--no-prefilter", action="store_true", help="send generic market-roundup headlines to the model too")
    p.add_argument("--no-disk-cache", action="store_true", help="keep even the label/prose caches in memory only (nothing cached on disk)")
    p.add_argument("--fetch-text", action="store_true", help="read the body text of EVERY article (slow); by default only results / governance / filing events are read")
    p.add_argument("--no-fetch-text", action="store_true", help="headline + snippet only, even for results / governance events")
    p.add_argument("--filings", choices=["bse", "none"], default="bse", help="free exchange announcements as a second source (default bse)")
    p.add_argument("--work", default="work", help="dir for hand-off files (file mode)")
    p.add_argument("--corpus-dir", default="audit", help="corpus of record (append-only tables: articles, events, verifications, scores, manifest)")
    p.add_argument("--no-corpus", action="store_true", help="do not append this run to the corpus of record")
    p.add_argument("--budget", type=int, default=None, help="sentiment-pass article budget (default 100 = 40/25/20/15 over W1..W4; the shape is kept)")
    p.add_argument("--window-quotas", default=None, help="explicit quotas for W1,W2,W3,W4 e.g. 60,40,30,20 (overrides --budget)")
    p.add_argument("--no-corpus-union", action="store_true", help="do not add governance events already on record (earlier runs) to this run")
    p.add_argument("--legacy-sampler", action="store_true", help="latest-N newest-first sampling (pre-v2.1) instead of the stratified windows")
    p.add_argument("--golden-file", default=None, help="regression set (default audit/golden/governance_37.jsonl); failing it stamps published=false")
    a = p.parse_args(argv)
    if a.max_articles == 0:
        a.max_articles = None

    from .ingest.files import FileActions, FileNews, FilePrices
    r = load_rubric(a.rubric)
    as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()

    from .retrieval import resolve_news_source
    a.news, retrieval_mode = resolve_news_source(a.news, as_of, date.today(), r["retrieval"]["backdate_days"], a.allow_live_backdate)
    if a.news == "file":
        news = FileNews(a.news_file)
    elif a.news == "gnews":
        from .ingest.gnews import GoogleNewsRSS
        news = GoogleNewsRSS()
    else:
        from .ingest.gdelt import GdeltNews
        news = GdeltNews(slice_days=a.gdelt_slice_days)
    if a.actions == "file":
        actions = FileActions(a.actions_file)
    elif a.actions == "yahoo":
        from .ingest.yf import YahooActions
        actions = YahooActions()
    else:
        actions = None
    if a.prices == "file":
        prices = FilePrices(a.prices_file)
    elif a.prices == "yahoo":
        from .ingest.yf import YahooPrices
        prices = YahooPrices()
    else:
        prices = None

    work = Path(a.work)
    from .progress import Display
    ui = Display(a.progress, a.log if a.progress != "off" else None, a.ascii, work / "progress.json")
    if a.classifier == "claude-code" or a.narrator == "claude-code":
        from .claude_code import preflight
        preflight(a.model)
    if a.classifier == "keyword":
        from .classify import KeywordClassifier
        clf = KeywordClassifier()
    elif a.classifier == "claude-code":
        from .claude_code import ClaudeCodeClassifier
        clf = ClaudeCodeClassifier(r, a.model, a.batch_size, a.workers, work / "claude_batches.jsonl")
    elif a.classifier == "file":
        from .handoff import FileClassifier
        clf = FileClassifier(work / "labels.jsonl")
    else:
        from .classify import AnthropicClassifier
        clf = AnthropicClassifier(r, a.model)

    from .narrate import AnthropicNarrator, CachedNarrator, TemplateNarrator
    nmode = a.narrator or {"keyword": "template"}.get(a.classifier, a.classifier)
    if nmode == "claude-code":
        from .claude_code import ClaudeCodeNarrator
        inner = ClaudeCodeNarrator(a.model)
    elif nmode == "file":
        from .handoff import FileNarrator
        inner = FileNarrator(work / "narratives.json", work)
    elif nmode == "anthropic":
        inner = AnthropicNarrator(a.model)
    else:
        inner = TemplateNarrator()
    narrator = inner if nmode == "file" else CachedNarrator(
        inner, None if a.no_disk_cache else Path(a.cache).with_name("narrative.jsonl"), getattr(inner, "model_id", "template"))
    out = Path(a.out or f"runs/{as_of.isoformat()}")
    if a.pick:
        from .pipeline import pick_holdings
        holdings = pick_holdings(a.universe, [x.strip() for x in a.pick.split(",") if x.strip()])
    elif a.portfolio:
        holdings = load_portfolio(a.portfolio)
    else:
        p.error("give --portfolio or --pick")
    from .resolve import enrich
    from .universe import check_universe
    holdings = enrich(holdings, a.universe, mode="none" if a.classifier in ("keyword", "file") else "claude-code", model=a.model)
    uhash, umsg = check_universe(str(a.portfolio or a.pick), [h.symbol for h in holdings], r["universe"]["lock_file"], a.confirm_universe, a.expect_count)
    print("  [universe] " + umsg)
    pipe = Pipeline(r, news, actions, prices, clf, out, a.cache, as_of, a.mode,
                    narrator, a.title, a.coverage, a.max_articles, a.fetch_text,
                    1.01 if a.allow_partial_labels else 0.10, ui=ui,
                    allow_missing_news=a.allow_missing_news, prefilter=not a.no_prefilter, retrieval_mode=retrieval_mode,
                    verified_events=None if a.no_verified_events else a.verified_events, universe_hash=uhash,
                    governance_pass=not a.no_governance_pass, governance_cap=a.governance_cap,
                    fundamentals_pass=not a.no_fundamentals_pass, fundamentals_cap=a.fundamentals_cap,
                    disk_cache=not a.no_disk_cache, stratified=not a.legacy_sampler, corpus_dir=None if a.no_corpus else a.corpus_dir,
                    news_choice=a.news, golden_file=a.golden_file, corpus_union=not a.no_corpus_union, budget=a.budget,
                    window_quotas=[int(x) for x in a.window_quotas.split(",")] if a.window_quotas else None,
                    filings=(__import__("sentinelq.ingest.filings", fromlist=["BseAnnouncements"]).BseAnnouncements() if a.filings == "bse" and a.news != "file" else None),
                    fetch_text_events=not a.no_fetch_text)
    ingested = None
    if a.classifier == "file":
        ingested = pipe.ingest(holdings)
        names = {h.symbol: h.name for h in holdings}
        pend = clf.pending([(i, names[s]) for s, v in ingested[0].items() for i in v])
        if pend:
            clf.write_pending(pend, work / "pending_items.jsonl")
            print(f"{len(pend)} items need labels. Wrote {work}/pending_items.jsonl - label them into "
                  f"{work}/labels.jsonl (see docs/NO_API_KEY.md), then re-run this same command.")
            return 2
    res = pipe.run(holdings, ingested)
    if nmode == "file":
        inner.write_missing()
        if inner.missing:
            print(f"NOTE: {len(inner.missing)} holdings used template prose; see {work}/pending_narratives.jsonl")
    t = res["run"]["totals"]
    if a.progress != "off":
        return 0
    print(f"Done. retrieved={t['retrieved']} kept={t['kept']} dropped={t['dropped']} -> {out}/sentinelq_scorecard.pdf (+ xlsx)")
    for s in res["scores"]:
        print(f"{s.symbol:<12} sent={s.company_sentiment!s:>5}  sector={s.sector_sentiment!s:>5}  "
              f"gov={s.governance_score:g}/{s.governance_label}  corp={s.corporate_action_score:g}"
              f"{'  [low confidence]' if s.low_confidence else ''}")
