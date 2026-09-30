"""Fixed six-stage route: input -> ingest -> classify -> validate -> score -> report."""
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
                        opt(r, "cap"), opt(r, "weight"))
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
    return [Holding(h.symbol, h.name, h.sector, h.cap, "") for h in out]


class Pipeline:
    def __init__(self, rubric: Rubric, news, actions, prices, classifier: Classifier,
                 out_dir: str | Path, cache_path: str | Path = ".cache/labels.jsonl",
                 as_of: date | None = None, mode: str = "absolute",
                 narrator=None, title: str = "Portfolio", coverage: str = "",
                 max_articles: int | None = None, fetch_text: bool = False,
                 max_label_failure: float = 0.10,
                 text_cache: str | Path = ".cache/fulltext.jsonl", ui: Display | None = None):
        self.r, self.news, self.actions, self.prices, self.clf = rubric, news, actions, prices, classifier
        self.out = Path(out_dir)
        self.cache = LabelCache(cache_path)
        self.as_of = as_of or date.today()
        self.mode = mode
        self.max_articles = max_articles
        self.max_label_failure = max_label_failure
        self.fetch_text, self.text_cache = fetch_text, text_cache
        self.narrator = narrator or TemplateNarrator()
        self.meta = {"title": title, "coverage": coverage}
        self.ui = ui or Display("off")

    # -- Stage 2 --------------------------------------------------------------------
    def ingest(self, holdings):
        ui = self.ui
        ui.stage("Ingest", "news + corporate actions per stock, deduplicated by URL")
        w = self.r["windows"]
        raw: dict[str, list[RawItem]] = {}
        errors = []
        icache = Path(self.text_cache).parent / "ingest"
        n = len(holdings)
        for idx, h in enumerate(holdings, 1):
            t0 = time.time()
            ck = icache / f"{self.as_of}_{h.symbol}_{self.max_articles}.json"
            from_cache, found, err_txt = False, 0, []
            if ck.exists():                      # already fetched for this date/cap: don't hit GDELT again
                items = [RawItem(**d) for d in json.loads(ck.read_text(encoding="utf-8"))]
                found, from_cache = len([i for i in items if i.kind == "news"]), True
            else:
                n_err = len(errors)
                items = []
                ui.status(f"Ingest {idx}/{n}  {h.symbol}: fetching news ...", "ingest", idx - 1, n)
                for prov, days in ((self.news, w["news_days"]), (self.actions, w["actions_days"])):
                    if prov is None:
                        continue
                    try:
                        items += prov.fetch(h, self.as_of - timedelta(days=days), self.as_of)
                    except Exception as e:  # a provider failure must be visible, not fatal
                        errors.append({"symbol": h.symbol, "provider": type(prov).__name__, "error": repr(e)})
                        err_txt.append(f"{type(prov).__name__}: {e!r}"[:160])
                found = len([i for i in items if i.kind == "news"])
                if self.max_articles:   # cap news per company, newest first; actions are never capped
                    news = sorted((i for i in items if i.kind == "news"), key=lambda i: i.published, reverse=True)
                    items = news[:self.max_articles] + [i for i in items if i.kind != "news"]
                if len(errors) == n_err and items:   # only cache clean fetches
                    icache.mkdir(parents=True, exist_ok=True)
                    for i in items:
                        i.style = classify_style(i)
                    ck.write_text(json.dumps([asdict(i) for i in items]), encoding="utf-8")
            for i in items:
                i.style = classify_style(i)
                if i.kind == "news" and not i.parse:
                    i.parse = "headline-only"
            raw[h.symbol] = items
            nn = [i for i in items if i.kind == "news"]
            ui.ingest_stock(idx, n, h, found, len(nn), len(items) - len(nn), Counter(i.style for i in nn),
                            from_cache, err_txt, time.time() - t0, self.max_articles)
        if self.fetch_text:
            from .ingest.fulltext import enrich
            ui.note("  fetching article body text for better labels (cached per URL) ...", "d")
            enrich([i for v in raw.values() for i in v], self.text_cache, progress=ui.fulltext)
            ui.clear_status()
            for h in holdings:
                ui.parse_summary(h, Counter(i.parse.split(" ")[0] + (" " + i.parse.split(" ")[1] if i.parse.startswith("headline") and len(i.parse.split(" ")) > 1 else "")
                                            for i in raw[h.symbol] if i.kind == "news"), None)
        return raw, errors

    # -- Stage 3 --------------------------------------------------------------------
    def _classify_one(self, item: RawItem, company: str, bypass_cache=False):
        if item.prelabel is not None:
            return item.prelabel, json.dumps(item.prelabel), False
        if not item.url or not item.url.strip():   # nothing to cite -> don't spend a model call
            return None, "", False
        key = item_key(item.url, item.title, item.snippet, self.clf.model_id, PROMPT_VERSION)
        hit = None if bypass_cache else self.cache.get(key)
        if hit:
            return hit["label"], hit["raw"], True
        label, raw = self.clf.classify(item, company)
        if label is not None:
            self.cache.put(key, label, raw)
        return label, raw, False

    # -- Stages 3+4 -----------------------------------------------------------------
    def classify_and_validate(self, holdings, raw):
        ui = self.ui
        ui.stage("Classify", f"model labels each item; schema-forced, validated ({self.clf.model_id})")
        names = {h.symbol: h.name for h in holdings}
        key_of = lambda i: item_key(i.url, i.title, i.snippet, self.clf.model_id, PROMPT_VERSION)
        need = [(i, names[sym]) for sym, its in raw.items() for i in its if i.prelabel is None and i.url.strip()]
        todo = [(i, c) for i, c in need if not self.cache.get(key_of(i))]
        ui.label_start(len(todo), len(need) - len(todo), Counter(i.symbol for i, _ in todo))
        if hasattr(self.clf, "prefetch"):   # batch labelling (claude-code mode)
            def persist(it, d, raw_):       # persist immediately: an aborted run keeps its work
                self.cache.put(key_of(it), d, raw_)
                ui.item_labelled(it, d)
            self.clf.on_label = persist
            self.clf.on_fail = ui.item_failed
            self.clf.say = ui.batch_note
            self.clf.prefetch(todo)
        lookback = self.r["windows"]["news_days"]
        kept: dict[str, list[LabelledItem]] = {h.symbol: [] for h in holdings}
        dropped: list[Dropped] = []
        raw_log = []
        sequential = not hasattr(self.clf, "prefetch")
        if sequential:
            ui.note("  labelling item by item (no batching for this classifier)", "d")
        ui.stage("Validate", "schema check; failures retried once, then dropped with a logged reason")
        hmap = {h.symbol: h for h in holdings}
        for sym, items in raw.items():
            drop0, ev = len(dropped), Counter()
            for it in items:
                label_d, resp, cached = self._classify_one(it, names[sym])
                if sequential and not cached and label_d is not None and it.prelabel is None:
                    ui.item_labelled(it, label_d)
                attempts = 1
                reason = validate_label(it, label_d, self.r, self.as_of, lookback)
                errs = getattr(self.clf, "errors", {})
                if reason == "no_structured_output":
                    from .classify import item_id
                    reason = "label_failed: " + errs.get(item_id(it), "model returned no label")
                raw_log.append({"symbol": sym, "url": it.url, "attempt": 1, "cached": cached,
                                "reason": reason, "raw": resp})
                if reason and not reason.startswith(("missing_", "invalid_source", "date_", "not_about_company", "label_failed")):
                    # model-side failure: retry exactly once, bypassing the cache
                    label_d, resp, cached = self._classify_one(it, names[sym], bypass_cache=True)
                    attempts = 2
                    reason = validate_label(it, label_d, self.r, self.as_of, lookback)
                    raw_log.append({"symbol": sym, "url": it.url, "attempt": 2, "cached": False,
                                    "reason": reason, "raw": resp})
                if reason:
                    dropped.append(Dropped(sym, it.title, it.url, it.published, "validate", reason))
                    continue
                kept[sym].append(LabelledItem(it, to_label(label_d), resp, cached, attempts))
                ev[label_d["event_type"]] += 1
            ui.stock_validated(hmap[sym], len(kept[sym]), len(items), Counter(d.reason.split(":")[0] for d in dropped[drop0:]), ev, None)
        return kept, dropped, raw_log

    # -- Full run -------------------------------------------------------------------
    def run(self, holdings: list[Holding], ingested=None) -> dict:
        ui = self.ui
        ui.start({"as of": self.as_of, "classifier": self.clf.model_id, "max articles": self.max_articles or "all",
                  "full text": "on" if self.fetch_text else "off (headline only)", "output": self.out}, holdings)
        raw, ingest_errors = ingested or self.ingest(holdings)
        kept, dropped, raw_log = self.classify_and_validate(holdings, raw)
        failed = [d for d in dropped if d.reason.startswith("label_failed")]
        labelable = sum(1 for v in raw.values() for i in v if i.url.strip()) or 1
        if failed:
            self.out.mkdir(parents=True, exist_ok=True)
            (self.out / "label_failures.jsonl").write_text("\n".join(json.dumps(asdict(d)) for d in failed))
            from collections import Counter
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
        narr, done = {}, 0
        with ThreadPoolExecutor(2) as ex:
            for sc, out in zip(scores, ex.map(lambda sc: self.narrator.holding(sc, kept[sc.symbol]), scores)):
                narr[sc.symbol] = out
                done += 1
                ui.narrate(done, len(scores), sc.name)
        ui.clear_status()
        ui.note("  writing portfolio-level observations ...", "d")
        obs = self.narrator.portfolio(portfolio_facts(scores, kept))
        result.update(narrative=narr, observations=obs)
        (self.out / "narrative.json").write_text(json.dumps({"holdings": narr, "observations": obs}, indent=2))
        build_pdf(self.out / "sentinelq_scorecard.pdf", result, self.r, narr, obs, self.meta)
        t = run["totals"]
        ui.finish({"retrieved": t["retrieved"], "kept": t["kept"], "dropped": t["dropped"],
                   "label failures": t["label_failed"], "stocks": len(scores),
                   "flags": ", ".join(x.symbol for x in scores if x.governance_label == "Flag") or "none"},
                  [str(self.out / "sentinelq_scorecard.pdf"), str(self.out / "sentinelq_report.xlsx")])
        return result
