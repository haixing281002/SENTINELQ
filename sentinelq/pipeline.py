"""Fixed six-stage route: input -> ingest -> classify -> validate -> score -> report.

Streaming and in-memory: each stock is scraped (window rolled back newest-first until 50 usable articles), its text is
read, labelled and validated - then dropped from memory. Nothing about the articles is written to disk except the audit
outputs (headline + URL + label). While stock N is being read, the scraper is already fetching stock N+1."""
from __future__ import annotations
import csv
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__
from .cache import LabelCache, item_key
from .classify import PROMPT_VERSION, Classifier, to_label
from .context import price_context
from .models import Dropped, Holding, Label, LabelledItem, RawItem
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .narrate import TemplateNarrator, portfolio_facts
from .progress import Display
from .style import classify_style
from .ingest.select import count_usable, month_spark, name_tokens, select_articles
from .pdf import build_pdf
from .report import write_reports
from .rubric import Rubric
from .score import relative_mode, score_portfolio
from .validate import validate_label


def load_portfolio(path: str | Path) -> list[Holding]:
    from .resolve import looks_like_table, parse_stock_table
    txt = Path(path).read_text(encoding="utf-8-sig")
    if looks_like_table(txt) and "sector" not in txt.splitlines()[0].lower():
        return parse_stock_table(txt)   # pasted CD_NSE Symbol / ISIN / Company Name table
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        cols = {c.strip().lower(): c for c in rd.fieldnames or []}
        need = {"symbol", "name", "sector"}
        if not need <= set(cols):
            raise ValueError(f"portfolio needs columns {sorted(need)}; got {rd.fieldnames}")
        opt = lambda r, k: (r.get(cols[k]) or "").strip() if k in cols else ""
        return [Holding(r[cols["symbol"]].strip(), r[cols["name"]].strip(), r[cols["sector"]].strip(),
                        opt(r, "cap"), opt(r, "weight"), opt(r, "aliases"))
                for r in rd if r[cols["symbol"]].strip()]


def _norm(x: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", x.lower()).strip()


def pick_holdings(universe: str | Path, queries: list[str]) -> list[Holding]:
    """Resolve typed names/tickers against the universe file; unknown names raise with instructions."""
    uni = load_portfolio(universe)
    extra = {}
    with open(universe, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            extra[r["symbol"]] = [a for a in (r.get("aliases") or "").split("|") if a]
    out, missing = [], []
    for q in queries:
        nq = _norm(q)
        hit = [h for h in uni if nq in (_norm(h.symbol), _norm(h.name)) or nq in [_norm(a) for a in extra[h.symbol]]]
        hit = hit or [h for h in uni if nq and (nq in _norm(h.name) or _norm(h.name) in nq)]
        if len(hit) == 1:
            out.append(hit[0])
        else:
            missing.append(f"{q!r} ({'ambiguous: ' + ', '.join(h.symbol for h in hit) if hit else 'not in universe'})")
    if missing:
        raise SystemExit("Cannot resolve: " + "; ".join(missing) +
                         f". Add a row (symbol,name,sector,cap,weight,aliases) to {universe} and retry.")
    return [Holding(h.symbol, h.name, h.sector, h.cap, "", h.aliases) for h in out]

THROTTLE_HELP = (
    "GDELT is refusing requests from this machine (HTTP 429 / rate limit). Nothing was scored. What to do:\n"
    "  1. Make sure no earlier run is still going - stopping a task can leave the Python process alive and every old run keeps\n"
    "     hitting GDELT from your IP. Windows PowerShell:  Get-Process python*, sentinelq* | Stop-Process -Force\n"
    "     (or Task Manager -> Details -> end python.exe / sentinelq.exe).\n"
    "  2. Wait 10-15 minutes so GDELT's limit clears, then re-run the same command (labels already obtained are reused).\n"
    "  3. Only ever run one instance at a time; a different network (e.g. phone hotspot) also works if your IP stays limited."
)


class Pipeline:
    def __init__(self, rubric: Rubric, news, actions, prices, classifier: Classifier,
                 out_dir: str | Path, cache_path: str | Path = ".cache/labels.jsonl",
                 as_of: date | None = None, mode: str = "absolute",
                 narrator=None, title: str = "Portfolio", coverage: str = "",
                 max_articles: int | None = None, fetch_text: bool = False,
                 max_label_failure: float = 0.10,
                 text_cache=None, ui: Display | None = None,          # text_cache: ignored (articles are never cached)
                 allow_missing_news: bool = False, ingest_retry_wait: float = 60.0, prefilter: bool = True,
                 disk_cache: bool = True):
        self.r, self.news, self.actions, self.prices, self.clf = rubric, news, actions, prices, classifier
        self.out = Path(out_dir)
        self.cache = LabelCache(cache_path if disk_cache else None)   # label cache holds hash -> label only, no article text
        self.as_of = as_of or date.today()
        self.mode = mode
        self.max_articles = max_articles
        self.max_label_failure = max_label_failure
        self.fetch_text = fetch_text
        self.narrator = narrator or TemplateNarrator()
        self.meta = {"title": title, "coverage": coverage}
        self.ui = ui or Display("off")
        self.allow_missing_news, self.ingest_retry_wait, self.prefilter = allow_missing_news, ingest_retry_wait, prefilter
        if hasattr(self.news, "on_event"):
            self.news.on_event = lambda m: self.ui.note("  " + m, "y")
        self._key = lambda i: item_key(i.url, i.title, i.snippet, self.clf.model_id, PROMPT_VERSION)

    # ---- scraping (Stage 2) --------------------------------------------------------------------------------
    def _fetch_stock(self, h):
        w = self.r["windows"]
        items, errs = [], []
        toks = name_tokens(h)
        if hasattr(self.news, "stop_when"):   # roll the window back only until we hold enough usable, latest articles
            self.news.stop_when = (lambda its: count_usable(its, toks) >= self.max_articles) if self.max_articles else None
        for prov, days in ((self.news, w["news_days"]), (self.actions, w["actions_days"])):
            if prov is None:
                continue
            try:
                items += prov.fetch(h, self.as_of - timedelta(days=days), self.as_of)
            except Exception as e:  # a provider failure must be visible, not fatal
                errs.append({"symbol": h.symbol, "provider": type(prov).__name__, "error": repr(e)})
        found = len([i for i in items if i.kind == "news"])
        items = select_articles(items, self.max_articles, toks)      # the LATEST max_articles usable ones
        return items, found, errs

    def _prep(self, h, items):
        """Style + parse mode; optionally read the article body (in memory only)."""
        for i in items:
            i.style = classify_style(i)
            i.parse = "headline-only" if i.kind == "news" else i.parse
        if self.fetch_text:
            from .ingest.fulltext import enrich
            enrich(items, None, progress=self.ui.fulltext)
            self.ui.clear_status()
        return items

    def _scrape_all(self, holdings, on_ready=None):
        """Scrape every stock newest-first until enough articles; hand each finished stock to `on_ready` immediately
        (so reading/labelling overlaps with the next scrape). Failed stocks get one retry after a cool-down."""
        ui = self.ui
        raw: dict[str, list[RawItem]] = {}
        errors: list[dict] = []
        failed: list = []
        n, w_days = len(holdings), self.r["windows"]["news_days"]
        if hasattr(self.news, "probe"):
            ui.status("Checking that GDELT is answering ...", "ingest", 0, n)
            try:
                self.news.probe()
            except Exception as e:
                ui.clear_status()
                from .ingest.gdelt import GdeltRateLimited
                if isinstance(e, GdeltRateLimited):
                    raise RuntimeError(f"{THROTTLE_HELP}\n(health check: {e})") from e
                raise RuntimeError(f"GDELT health check failed: {e!r}. Check your internet connection / proxy, then re-run.") from e
            ui.clear_status()
        consecutive = 0

        def show(idx, h, items, found, err_txt, secs):
            nn = [i for i in items if i.kind == "news"]
            ui.ingest_stock(idx, n, h, found, len(nn), len(items) - len(nn), Counter(i.style for i in nn), False, err_txt,
                            secs, self.max_articles, month_spark(items, self.as_of - timedelta(days=w_days), self.as_of, ui.ascii))

        for idx, h in enumerate(holdings, 1):
            t0 = time.time()
            ui.status(f"Scraping {idx}/{n}  {h.symbol}: rolling the window back for the latest {self.max_articles or 'all'} articles ...",
                      "ingest", idx - 1, n)
            items, found, errs = self._fetch_stock(h)
            if errs:
                failed.append(h)
                errors += errs
                raw[h.symbol] = items
                consecutive += 1
                show(idx, h, self._prep_light(items), found, [f"{e['provider']}: {e['error']}"[:160] for e in errs], time.time() - t0)
                if consecutive >= 3 and not self.allow_missing_news:      # hammering a throttled service only prolongs the limit
                    raise RuntimeError(f"{THROTTLE_HELP}\n(3 stocks in a row failed: {', '.join(x.symbol for x in failed[-3:])}; "
                                       f"last error: {errs[0]['error'][:140]})")
                continue
            consecutive = 0
            items = self._prep(h, items)
            raw[h.symbol] = items
            show(idx, h, items, found, [], time.time() - t0)
            if on_ready:
                on_ready(h, items)

        if failed:   # second pass after a cooldown, before anything is scored on missing news
            ui.note(f"  {len(failed)} stock(s) had fetch errors ({', '.join(h.symbol for h in failed)}); "
                    f"cooling down {self.ingest_retry_wait:.0f}s then retrying once ...", "y")
            if self.ingest_retry_wait:
                time.sleep(self.ingest_retry_wait)
            still = []
            for h in failed:
                items, found, errs = self._fetch_stock(h)
                if errs:
                    still.append(h)
                    ui.note(f"  retry {h.symbol}: still failing - {errs[0]['error'][:120]}", "r")
                    continue
                items = self._prep(h, items)
                raw[h.symbol] = items
                errors[:] = [e for e in errors if e["symbol"] != h.symbol]
                ui.note(f"  retry {h.symbol}: recovered {len([i for i in items if i.kind == 'news'])} articles", "g")
                if on_ready:
                    on_ready(h, items)
            if still:
                msg = (f"News could not be fetched for {len(still)} stock(s): {', '.join(h.symbol for h in still)}. "
                       "Scoring them on corporate actions alone would understate coverage. Re-run the same command later "
                       "(or pass --allow-missing-news to accept the gap).")
                if not self.allow_missing_news:
                    raise RuntimeError(msg)
                ui.note("WARNING: " + msg, "y")
                for h in still:
                    if on_ready:
                        on_ready(h, self._prep(h, raw[h.symbol]))
        return raw, errors

    def _prep_light(self, items):
        for i in items:
            i.style = classify_style(i)
        return items

    def ingest(self, holdings):
        """Non-streaming scrape of all stocks (used by the file hand-off mode and tests). Nothing is stored."""
        self.ui.stage("Ingest", "news + corporate actions per stock, latest articles first, nothing stored")
        return self._scrape_all(holdings, None)

    # ---- labelling + validation, one stock at a time (Stages 3-4) ----------------------------------------------
    def _classify_one(self, item: RawItem, company: str, bypass_cache=False):
        if item.prelabel is not None:
            return item.prelabel, json.dumps(item.prelabel), False
        if not item.url or not item.url.strip():   # nothing to cite -> don't spend a model call
            return None, "", False
        key = self._key(item)
        hit = None if bypass_cache else self.cache.get(key)
        if hit:
            return hit["label"], hit["raw"], True
        label, raw = self.clf.classify(item, company)
        if label is not None:
            self.cache.put(key, label, raw)
        return label, raw, False

    def _wire_classifier(self):
        if hasattr(self.clf, "prefetch"):
            def persist(it, d, raw_):       # persist the LABEL immediately: an aborted run keeps its work
                self.cache.put(self._key(it), d, raw_)
                self.ui.item_labelled(it, d)
            self.clf.on_label, self.clf.on_fail, self.clf.say = persist, self.ui.item_failed, self.ui.batch_note

    def _label_stock(self, h, items):
        """Read, label and validate one stock's articles; returns (kept, dropped, raw_log)."""
        ui = self.ui
        lookback = self.r["windows"]["news_days"]
        dropped: list[Dropped] = []
        if self.prefilter:   # market roundups that never name the company: cheap, deterministic, and disclosed
            toks, keep = name_tokens(h), []
            for i in items:
                if i.kind == "news" and i.style == "market-roundup" and not any(t in i.title.lower() for t in toks):
                    dropped.append(Dropped(h.symbol, i.title, i.url, i.published, "prefilter", "generic_market_headline"))
                else:
                    keep.append(i)
            items = keep
        need = [(i, h.name) for i in items if i.prelabel is None and i.url.strip()]
        todo = [(i, c) for i, c in need if not self.cache.get(self._key(i))]
        ui.label_start(len(todo), len(need) - len(todo), Counter(i.symbol for i, _ in todo))
        sequential = not hasattr(self.clf, "prefetch")
        if not sequential:
            self.clf.prefetch(todo)
        kept, raw_log, ev = [], [], Counter()
        errs = getattr(self.clf, "errors", {})
        for it in items:
            label_d, resp, cached = self._classify_one(it, h.name)
            if sequential and not cached and label_d is not None and it.prelabel is None:
                ui.item_labelled(it, label_d)
            attempts = 1
            reason = validate_label(it, label_d, self.r, self.as_of, lookback)
            if reason == "no_structured_output":
                from .classify import item_id
                reason = "label_failed: " + errs.get(item_id(it), "model returned no label")
            raw_log.append({"symbol": h.symbol, "url": it.url, "attempt": 1, "cached": cached, "reason": reason, "raw": resp})
            if reason and not reason.startswith(("missing_", "invalid_source", "date_", "not_about_company", "label_failed")):
                # model-side failure: retry exactly once, bypassing the cache
                label_d, resp, cached = self._classify_one(it, h.name, bypass_cache=True)
                attempts = 2
                reason = validate_label(it, label_d, self.r, self.as_of, lookback)
                raw_log.append({"symbol": h.symbol, "url": it.url, "attempt": 2, "cached": False, "reason": reason, "raw": resp})
            if reason:
                dropped.append(Dropped(h.symbol, it.title, it.url, it.published, "validate", reason))
                continue
            kept.append(LabelledItem(it, to_label(label_d), resp, cached, attempts))
            ev[label_d["event_type"]] += 1
        ui.stock_validated(h, len(kept), len(items), Counter(d.reason.split(":")[0] for d in dropped), ev, None)
        for it in items:                      # the article text has served its purpose: release it
            it.snippet = ""
        return kept, dropped, raw_log

    def classify_and_validate(self, holdings, raw):
        """Batch form (file hand-off mode): label every stock in `raw`, one after another."""
        self._wire_classifier()
        self.ui.stage("Classify", f"model reads and labels each item ({self.clf.model_id}); schema-forced, validated")
        kept, dropped, raw_log = {}, [], []
        for h in holdings:
            k, d, r_ = self._label_stock(h, raw[h.symbol])
            kept[h.symbol] = k
            dropped += d
            raw_log += r_
        return kept, dropped, raw_log

    # ---- full run ---------------------------------------------------------------------------------------
    def run(self, holdings: list[Holding], ingested=None) -> dict:
        ui = self.ui
        ui.start({"as of": self.as_of, "classifier": self.clf.model_id, "max articles": self.max_articles or "all",
                  "full text": "on (read in memory, never saved)" if self.fetch_text else "off (headline only)",
                  "articles stored": "none (headline+URL kept only in the report)", "output": self.out}, holdings)
        if ingested is not None:
            raw, ingest_errors = ingested
            kept, dropped, raw_log = self.classify_and_validate(holdings, raw)
        else:
            self._wire_classifier()
            ui.stage("Ingest", "streaming per stock: scrape newest-first -> read -> label -> validate (in memory, nothing stored)")
            done: dict[str, tuple] = {}
            with ThreadPoolExecutor(1) as labeller:      # reads stock N while the scraper is already on stock N+1
                futs = {}
                raw, ingest_errors = self._scrape_all(holdings, lambda h, items: futs.__setitem__(h.symbol, labeller.submit(self._label_stock, h, items)))
                for sym, f in futs.items():
                    done[sym] = f.result()
            kept, dropped, raw_log = {}, [], []
            for h in holdings:
                k, d, r_ = done.get(h.symbol, ([], [], []))
                kept[h.symbol] = k
                dropped += d
                raw_log += r_
        failed = [d for d in dropped if d.reason.startswith("label_failed")]
        labelable = sum(1 for v in raw.values() for i in v if i.url.strip()) or 1
        if failed:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "label_failures.jsonl").write_text("\n".join(json.dumps(asdict(d)) for d in failed), encoding="utf-8")
            top = Counter(d.reason for d in failed).most_common(3)
            msg = (f"{len(failed)}/{labelable} items could not be labelled by the model "
                   f"({len(failed) / labelable:.0%}). Top reasons: {top}. Details: {self.out}/label_failures.jsonl")
            if len(failed) / labelable > self.max_label_failure:
                raise RuntimeError("Refusing to write a report built on failed labelling. " + msg +
                                   "\nLabels already obtained are cached - re-run the same command to retry only the failures "
                                   "(or pass --allow-partial-labels to accept the gap).")
            ui.note("WARNING: " + msg, "y")
        dcount: dict[str, int] = {}
        for d in dropped:
            dcount[d.symbol] = dcount.get(d.symbol, 0) + 1
        scores = score_portfolio(holdings, kept, dcount, self.as_of, self.r)
        ui.scored(scores)

        ctx = []
        if self.prices is not None:   # context only; scores were already computed above
            for s in scores:
                try:
                    ctx.append(price_context(s.symbol, s.company_sentiment, self.prices,
                                             self.as_of, self.r["windows"]["prices_days"]))
                except Exception as e:
                    ingest_errors.append({"symbol": s.symbol, "provider": "prices", "error": repr(e)})
        rel = relative_mode(scores, self.r) if self.mode == "relative" else None

        coverage = [{"symbol": h.symbol, "retrieved": len(raw[h.symbol]),
                     "kept": len(kept[h.symbol]), "dropped": dcount.get(h.symbol, 0),
                     "news_kept": sum(1 for i in kept[h.symbol] if i.item.kind == "news"),
                     "label_failed": sum(1 for d in dropped if d.symbol == h.symbol and d.reason.startswith("label_failed")),
                     "actions_kept": sum(1 for i in kept[h.symbol] if i.item.kind == "action"),
                     "low_confidence": next(s.low_confidence for s in scores if s.symbol == h.symbol)}
                    for h in holdings]
        run = {"as_of": self.as_of.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
               "sentinelq_version": __version__, "rubric_version": self.r.version,
               "rubric_sha256": self.r.sha256, "classifier_model": self.clf.model_id,
               "prompt_version": PROMPT_VERSION, "mode": self.mode,
               "totals": {"label_failed": len(failed), "retrieved": sum(len(v) for v in raw.values()),
                          "kept": sum(len(v) for v in kept.values()), "dropped": len(dropped)}}
        result = dict(run=run, scores=scores, kept=kept, dropped=dropped, coverage=coverage,
                      price_context=ctx, relative=rel, ingest_errors=ingest_errors, raw_log=raw_log)
        ui.stage("Report", "workbook, commentary per stock, portfolio observations, PDF")
        write_reports(self.out, result, self.r)
        narr, done_n = {}, 0
        with ThreadPoolExecutor(2) as ex:
            for sc, out in zip(scores, ex.map(lambda sc: self.narrator.holding(sc, kept[sc.symbol]), scores)):
                narr[sc.symbol] = out
                done_n += 1
                ui.narrate(done_n, len(scores), sc.name)
        ui.clear_status()
        ui.note("  writing portfolio-level observations ...", "d")
        obs = self.narrator.portfolio(portfolio_facts(scores, kept))
        result.update(narrative=narr, observations=obs)
        (self.out / "narrative.json").write_text(json.dumps({"holdings": narr, "observations": obs}, indent=2), encoding="utf-8")
        build_pdf(self.out / "sentinelq_scorecard.pdf", result, self.r, narr, obs, self.meta)
        t = run["totals"]
        ui.finish({"retrieved": t["retrieved"], "kept": t["kept"], "dropped": t["dropped"],
                   "label failures": t["label_failed"], "stocks": len(scores),
                   "flags": ", ".join(x.symbol for x in scores if x.governance_label == "Flag") or "none"},
                  [str(self.out / "sentinelq_scorecard.pdf"), str(self.out / "sentinelq_report.xlsx")])
        return result
