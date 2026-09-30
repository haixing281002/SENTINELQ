from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path

from .pipeline import Pipeline, load_portfolio
from .rubric import load_rubric


def main(argv=None):
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
    p.add_argument("--news", choices=["gdelt", "file"], default="gdelt")
    p.add_argument("--news-file")
    p.add_argument("--actions", choices=["yahoo", "file", "none"], default="yahoo")
    p.add_argument("--actions-file")
    p.add_argument("--prices", choices=["yahoo", "file", "none"], default="yahoo")
    p.add_argument("--prices-file")
    p.add_argument("--max-articles", type=int, default=None, help="cap news items per company (newest first)")
    p.add_argument("--title", default="Portfolio", help="report title, e.g. 'QVM Portfolio'")
    p.add_argument("--coverage", default="", help="coverage blurb on page 1")
    p.add_argument("--narrator", choices=["claude-code", "file", "anthropic", "template"], default=None,
                   help="prose writer for the PDF (default: same as --classifier)")
    p.add_argument("--fetch-text", action="store_true", help="fetch article body text (better labels than headlines)")
    p.add_argument("--work", default="work", help="dir for hand-off files (file mode)")
    a = p.parse_args(argv)

    from .ingest.files import FileActions, FileNews, FilePrices
    r = load_rubric(a.rubric)
    as_of = date.fromisoformat(a.as_of) if a.as_of else date.today()

    if a.news == "file":
        news = FileNews(a.news_file)
    else:
        from .ingest.gdelt import GdeltNews
        news = GdeltNews(max_records=max(a.max_articles or 100, 1))
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
    if a.classifier == "keyword":
        from .classify import KeywordClassifier
        clf = KeywordClassifier()
    elif a.classifier == "claude-code":
        from .claude_code import ClaudeCodeClassifier
        clf = ClaudeCodeClassifier(r, a.model)
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
        inner, Path(a.cache).with_name("narrative.jsonl"), getattr(inner, "model_id", "template"))
    out = Path(a.out or f"runs/{as_of.isoformat()}")
    if a.pick:
        from .pipeline import pick_holdings
        holdings = pick_holdings(a.universe, [x.strip() for x in a.pick.split(",") if x.strip()])
    elif a.portfolio:
        holdings = load_portfolio(a.portfolio)
    else:
        p.error("give --portfolio or --pick")
    pipe = Pipeline(r, news, actions, prices, clf, out, a.cache, as_of, a.mode,
                    narrator, a.title, a.coverage, a.max_articles, a.fetch_text)
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
    print(f"Done. retrieved={t['retrieved']} kept={t['kept']} dropped={t['dropped']} -> {out}/sentinelq_scorecard.pdf (+ xlsx)")
    for s in res["scores"]:
        print(f"{s.symbol:<12} sent={s.company_sentiment!s:>5}  sector={s.sector_sentiment!s:>5}  "
              f"gov={s.governance_score:g}/{s.governance_label}  corp={s.corporate_action_score:g}"
              f"{'  [low confidence]' if s.low_confidence else ''}")
