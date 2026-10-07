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
from .classify import PROMPT_REVISION, PROMPT_VERSION, Classifier, to_label
from .context import price_context
from .models import Dropped, Holding, Label, LabelledItem, RawItem
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .narrate import TemplateNarrator, portfolio_facts
from .progress import Display
from .style import classify_style
from .ingest.select import count_usable, month_spark, name_tokens, select_articles, select_fundamentals, select_governance
from .sampler import stratified_sample
from .cluster import alias_tokens, attach_extras, build_event, post_label_merge, precluster, top_frequency, url_hash
from .config import RESULTS_EVENTS_MAX
import re
import re as _re
_PRINT = _re.compile(r"result|net profit|\bpat\b|profit (rises|jumps|falls|drops|surges|declines|up|down)|revenue|earnings|\bq[1-4]\b|quarter", _re.I)
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
    "The news source (GDELT / Google News) is refusing requests from this machine (HTTP 429 / rate limit). Nothing was scored. What to do:\n"
    "  1. Make sure no earlier run is still going - stopping a task can leave the Python process alive and every old run keeps\n"
    "     hitting the news source from your IP. Windows PowerShell:  Get-Process python*, sentinelq* | Stop-Process -Force\n"
    "     (or Task Manager -> Details -> end python.exe / sentinelq.exe).\n"
    "  2. Wait 10-15 minutes so GDELT's limit clears, then re-run the same command (labels already obtained are reused).\n"
    "  3. Only ever run one instance at a time; a different network (e.g. phone hotspot) also works if your IP stays limited."
)


def _has_gist(row: dict) -> bool:
    """Prompt v2 labels always carry a gist. The tag 'v2' was also used by an earlier (substance) prompt whose labels are in the
    corpus and the label cache; those have no gist and must be re-labelled, never reused as current."""
    gate = row.get("gate_json")
    gate = json.loads(gate) if isinstance(gate, str) and gate else (gate or {})
    return bool(gate.get("gist"))


def _cache_ok(hit) -> bool:
    return bool(hit) and (not isinstance(hit.get("label"), dict) or "gist" in hit["label"] or hit["label"].get("about_company") is False)


def _label_from_event_row(row: dict) -> dict:
    gate = row.get("gate_json")
    gate = json.loads(gate) if isinstance(gate, str) and gate else (gate or {})
    d = {"event_type": row["event_type"], "sentiment": int(row["sentiment"]), "rationale": row.get("justification") or "",
         "governance_flag": bool(row.get("is_governance_flag")), "about_company": True, "materiality": row.get("materiality"),
         "historical": bool(row.get("historical"))}
    d.update({k: v for k, v in gate.items() if v is not None})
    return d


class Pipeline:
    def __init__(self, rubric: Rubric, news, actions, prices, classifier: Classifier,
                 out_dir: str | Path, cache_path: str | Path = ".cache/labels.jsonl",
                 as_of: date | None = None, mode: str = "absolute",
                 narrator=None, title: str = "Portfolio", coverage: str = "",
                 max_articles: int | None = None, fetch_text: bool = False,
                 max_label_failure: float = 0.10,
                 text_cache=None, ui: Display | None = None,          # text_cache: ignored (articles are never cached)
                 allow_missing_news: bool = False, ingest_retry_wait: float = 60.0, prefilter: bool = True,
                 disk_cache: bool = True, governance_pass: bool = True, governance_cap: int = 30,
                 fundamentals_pass: bool = True, fundamentals_cap: int = 40, retrieval_mode: str = "live", run_date: date | None = None,
                 verified_events: str | Path | None = None, universe_hash: str = "", stratified: bool = True,
                 corpus_dir: str | Path | None = None, news_choice: str = "", golden_file: str | Path | None = None, corpus_union: bool = True,
                 budget: int | None = None, window_quotas: list[int] | None = None, filings=None, fetch_text_events: bool = False):
        self.r, self.news, self.actions, self.prices, self.clf = rubric, news, actions, prices, classifier
        self.out = Path(out_dir)
        self.cache = LabelCache(cache_path if disk_cache else None)   # label cache holds hash -> label only, no article text
        self.as_of = as_of or date.today()
        self.mode = mode
        self.max_articles = max_articles
        self.max_label_failure = max_label_failure
        self.fetch_text, self.fetch_text_events, self.filings = fetch_text, fetch_text_events, filings
        self.governance_pass, self.governance_cap = governance_pass, governance_cap
        self.fundamentals_pass, self.fundamentals_cap = fundamentals_pass, fundamentals_cap
        self.retrieval_mode, self.run_date, self.verified_events, self.universe_hash = retrieval_mode, run_date or date.today(), verified_events, universe_hash
        self.narrator = narrator or TemplateNarrator()
        self.meta = {"title": title, "coverage": coverage}
        self.ui = ui or Display("off")
        self.allow_missing_news, self.ingest_retry_wait, self.prefilter = allow_missing_news, ingest_retry_wait, prefilter
        if hasattr(self.news, "on_event"):
            self.news.on_event = lambda m: self.ui.note("  " + m, "y")
        self._key = lambda i: item_key(i.url, i.title, i.snippet, self.clf.model_id, PROMPT_VERSION)
        # ---- Upgrade v2.1: stratified sampler + event clustering (A1/A3) and the corpus of record (B1-B3)
        self.stratified, self.news_choice, self.golden_file, self.corpus_union = stratified, news_choice, golden_file, corpus_union
        from .sampler import scaled_windows
        self.windows = scaled_windows(budget, window_quotas)
        self.cap_budget = budget is not None or window_quotas is not None      # default: no cap - every usable article is scored
        self.budget = sum(q for _, _, q in self.windows) if self.cap_budget else None
        self.started = datetime.now()
        self._sample_reports: dict[str, dict] = {}
        self._extras: dict[str, list] = {}
        self._articles_seen: dict[str, list[dict]] = {}
        self._sample_drops: dict[str, list] = {}
        self._filings_count: dict[str, int] = {}
        self._merges: list[dict] = []
        self._cluster_drops: list[dict] = []
        self.llm_items = 0                       # items actually sent to the model this run (A5.6 / B7.5)
        self.corpus = None
        self._corpus_idx: dict = {}
        if corpus_dir:
            from .corpus import Corpus
            self.corpus = Corpus(corpus_dir)
            try:
                self._corpus_idx = {h: e for h, e in self.corpus.label_index(PROMPT_VERSION, self.clf.model_id).items() if _has_gist(e)}
            except Exception as e:               # a damaged table must not stop a run; it is reported
                self.ui.note(f"  corpus label index unavailable ({e!r}); labels will be fetched again", "y")

    # ---- scraping (Stage 2) --------------------------------------------------------------------------------
    def _fetch_stock(self, h):
        w = self.r["windows"]
        items, errs = [], []
        toks = name_tokens(h)
        if hasattr(self.news, "stop_when"):   # roll the window back only until we hold enough usable, latest articles
            self.news.stop_when = None if self.stratified else ((lambda its: count_usable(its, toks) >= self.max_articles) if self.max_articles else None)
        for prov, days in ((self.news, w["news_days"]), (self.actions, w["actions_days"])):
            if prov is None:
                continue
            try:
                items += prov.fetch(h, self.as_of - timedelta(days=days), self.as_of)
            except Exception as e:  # a provider failure must be visible, not fatal
                errs.append({"symbol": h.symbol, "provider": type(prov).__name__, "error": repr(e)})
        found = len([i for i in items if i.kind == "news"])
        if self.stratified:                                           # A1: fixed windows W1..W4 with quotas, per-day cap, carry-forward
            news_all = [i for i in items if i.kind == "news"]
            acts = [i for i in items if i.kind != "news"]
            for a in acts:
                a.pass_ = "actions"
            selected, extras, rep = stratified_sample(news_all, self.as_of, toks, windows=self.windows, cap=self.cap_budget)
            for i in selected + extras:
                i.pass_, i.purpose = "sentiment", "sentiment"
            self._sample_reports[h.symbol], self._extras[h.symbol] = rep, extras
            chosen = {id(i) for i in selected} | {id(i) for i in extras}
            seen_rows, sdrops = [], []
            for i in news_all:
                why = "" if id(i) in chosen else ("syndicated_duplicate" if any(m["url"] == i.url for sel in selected + extras for m in sel.members)
                                                  else "title_does_not_name_company" if not any(t in (i.title or "").lower() for t in toks)
                                                  else "boilerplate_headline" if i.boilerplate
                                                  else "outside_lookback" if i.window is None else "over_budget")
                seen_rows.append(self._article_row(i, why))
                if why and why != "syndicated_duplicate":
                    sdrops.append(Dropped(h.symbol, i.title, i.url, i.published, "sample", why))
            self._articles_seen[h.symbol], self._sample_drops[h.symbol] = seen_rows, sdrops
            items = selected + acts
        else:
            items = select_articles(items, self.max_articles, toks)      # legacy: the LATEST max_articles usable ones (sentiment pass)
        if self.fundamentals_pass and hasattr(self.news, "fetch_fundamentals") and not errs:
            try:    # sentiment must rest on the whole lookback incl. results prints, not only the latest weeks (Reconciliation cause 1)
                fund = self.news.fetch_fundamentals(h, self.as_of - timedelta(days=w["news_days"]), self.as_of)
                picked = select_fundamentals(fund, items, 10, self.fundamentals_cap, toks, self.as_of)
                if self.stratified:                                   # mandatory anchors, outside the budget (A1)
                    from .sampler import window_of
                    for i in picked:
                        i.pass_, i.window = "results", window_of(i.published, self.as_of, self.windows)
                    self._articles_seen.setdefault(h.symbol, []).extend(self._article_row(i, "") for i in picked)
                items += picked
            except Exception as e:
                errs.append({"symbol": h.symbol, "provider": type(self.news).__name__ + ".fundamentals", "error": repr(e)})
        if self.governance_pass and hasattr(self.news, "fetch_governance") and not errs:
            try:    # governance is a 12-month rubric: search the whole year for governance events, independent of the latest-N pull
                gov = self.news.fetch_governance(h, self.as_of - timedelta(days=w["news_days"]), self.as_of)
                picked = select_governance(gov, items, self.governance_cap, toks, self.as_of if self.stratified else None)
                if self.stratified:
                    from .sampler import window_of
                    for i in picked:
                        i.pass_, i.window = "governance", window_of(i.published, self.as_of, self.windows)
                    self._articles_seen.setdefault(h.symbol, []).extend(self._article_row(i, "") for i in picked)
                items += picked
            except Exception as e:
                errs.append({"symbol": h.symbol, "provider": type(self.news).__name__ + ".governance", "error": repr(e)})
        if self.filings is not None and self.stratified:        # free exchange filings: results + governance anchors, press releases as extra evidence
            try:
                fl = self.filings.fetch(h, self.as_of - timedelta(days=w["news_days"]), self.as_of)
                from .sampler import window_of
                seen_t = {re.sub(r"[^a-z0-9]", "", (i.title or "").lower()) for i in items}
                kept_f = []
                for i in fl:
                    if re.sub(r"[^a-z0-9]", "", i.title.lower()) in seen_t:
                        continue
                    i.window = window_of(i.published, self.as_of, self.windows)
                    if i.window is not None:
                        kept_f.append(i)
                self._articles_seen.setdefault(h.symbol, []).extend(self._article_row(i, "") for i in kept_f)
                items += kept_f
                self._filings_count[h.symbol] = len(kept_f)
            except Exception as e:
                errs_f = {"symbol": h.symbol, "provider": "BseAnnouncements", "error": repr(e)[:160]}
                self._filings_count[h.symbol] = 0
                self.ui.note(f"        filings: {errs_f['error']} (run continues on news alone)", "y")
        return items, found, errs

    def _article_row(self, i, dropped_reason: str) -> dict:
        from .tiers import domain_of, tier_of
        d = domain_of(i.url, i.source)
        return {"symbol": i.symbol, "url": i.url, "url_hash": url_hash(i.url), "source_domain": d, "source_tier": tier_of(d),
                "published_date": i.published, "headline": i.title, "snippet": (i.snippet or "")[:300], "pass": i.pass_ or "sentiment",
                "window": i.window, "dropped_reason": dropped_reason or None}

    def _to_events(self, h, items):
        """A3 stage 1: cluster this stock's articles into events; label the representative only. Returns (items, dropped)."""
        news = [i for i in items if i.kind == "news"]
        rest = [i for i in items if i.kind != "news"]
        extras = self._extras.get(h.symbol, [])
        alias = alias_tokens(h.name, h.aliases, h.symbol)
        common = top_frequency([i.title for i in news] + [x.title for x in extras], alias)
        events, dropped = [], []
        # No per-day cap on scoring: the day's overflow joins the same content clustering as everything else. A repeat of a story
        # (same content in other words / other outlets) becomes an extra source of that event; anything new is its own scored event.
        seen = {id(i) for i in news}
        news = news + [x for x in extras if id(x) not in seen]
        for members in precluster(news, alias, common):
            passes = {m.pass_ for m in members}
            pass_ = "sentiment" if "sentiment" in passes else "results" if "results" in passes else "governance"
            ev = build_event(members, pass_)
            ev.purpose = "sentiment" if pass_ in ("sentiment", "results") else "governance"
            events.append(ev)
        # results anchors: at most RESULTS_EVENTS_MAX results events, one per quarter (the best-sourced one)
        res = [e for e in events if e.pass_ == "results"]
        if len(res) > 0:                       # one results PRINT per quarter + up to N other results-type events per quarter, newest 4 quarters
            from .config import RESULTS_OTHER_PER_QUARTER
            byq: dict[str, list] = {}
            for e in res:
                d = e.event_date or e.published
                byq.setdefault(f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}", []).append(e)
            keep = []
            for q in sorted(byq, reverse=True)[:RESULTS_EVENTS_MAX]:
                prints = [e for e in byq[q] if _PRINT.search(e.title or "")]
                others = [e for e in byq[q] if e not in prints]
                if prints:
                    keep.append(max(prints, key=lambda e: (e.n_sources, e.published)))
                keep += sorted(others, key=lambda e: (e.n_sources, e.published), reverse=True)[:RESULTS_OTHER_PER_QUARTER]
            for e in res:
                if e not in keep:
                    dropped.append(Dropped(h.symbol, e.title, e.url, e.published, "sample",
                                           "results_quota_one_print_per_quarter" if _PRINT.search(e.title or "") else "results_quota_other_per_quarter"))
            events = [e for e in events if e.pass_ != "results" or e in keep]
        n_in = len(news) + len(extras)
        self.ui.note(f"        events: {n_in} article(s) -> {len(events)} event(s) ({sum(1 for e in events if e.n_members > 1)} multi-source); "
                     f"only representatives are labelled", "d")
        if self.fetch_text_events and not self.fetch_text:          # read the body of every labelled event (in memory), so labels rest on the article
            try:
                from .ingest.fulltext import enrich
                reps = [e for e in events if e.kind == "news"]
                if reps:
                    enrich(reps, None, progress=self.ui.fulltext, budget=45.0)
                    self.ui.clear_status()
                    got = sum(1 for e in reps if e.parse.startswith("full-text"))
                    self.ui.note(f"        article text read for {got}/{len(reps)} event(s) (headline-only for the rest: paywalled / blocked / slow)", "d")
            except Exception as e:                                    # never let a body fetch stop a run
                self.ui.note(f"        full text skipped ({e!r})", "y")
        return events + rest, dropped

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
            nn = [i for i in items if i.kind == "news" and i.purpose == "sentiment"]
            gv = sum(1 for i in items if i.purpose == "governance")
            if gv:
                ui.note(f"        governance pass: {gv} candidate article(s) from the full 12 months (feed governance scoring only)", "d")
            if self._filings_count.get(h.symbol):
                ui.note(f"        exchange filings (BSE, free): {self._filings_count[h.symbol]} announcement(s) in the window", "d")
            rep = self._sample_reports.get(h.symbol)
            if rep:
                ws = " ".join(f"W{k}:{rep['windows'][k]}/{rep['quota'][k]}" for k in rep["windows"])
                back = sum(c.get("rolled_back", 0) for c in rep["carry"].values())
                ui.note(f"        {'stratified sample' if rep.get('capped', True) else 'all usable articles (no budget cap)'}: {ws} = {rep['selected']}/{rep['budget']} "
                        f"({rep['usable']} usable titles; {rep.get('boilerplate', 0)} boilerplate dropped; "
                        f"{rep['per_day_extra']} per-day extras ride as sources; {rep['syndicated']} syndicated copies"
                        + (f"; {back} carried back to the newest windows" if back else "") + ")", "d")
            ui.ingest_stock(idx, n, h, found, len(nn), len(items) - len(nn) - gv, Counter(i.style for i in nn), False, err_txt,
                            secs, self.max_articles, month_spark(items, self.as_of - timedelta(days=w_days), self.as_of, ui.ascii))

        for idx, h in enumerate(holdings, 1):
            t0 = time.time()
            ui.begin(h.symbol, 2)
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

    def _union_corpus_governance(self, holdings, kept) -> dict:
        """Governance-flagged events stored by earlier runs and visible at this as-of (B3) join this run's evidence when this run did not
        retrieve them (same prompt + model only; sentiment is left to this run's stratified sample). Retrieval variance between runs is
        the single biggest cause of governance scores moving; the corpus of record is the cure the Reconciliation asked for."""
        from .replay import _visible, event_row_to_item
        try:
            rows, runs, _mode = _visible(self.corpus, self.as_of, strict=False)
        except Exception:
            return {"events": 0, "runs": []}
        syms = {h.symbol for h in holdings}
        have: dict[str, set] = {s: set() for s in syms}
        for s_, v in kept.items():
            for li in v:
                have.setdefault(s_, set()).update([url_hash(li.item.url)] + list(li.item.member_url_hashes))
        lo = (self.as_of - timedelta(days=self.r["windows"]["news_days"])).isoformat()
        added, used = 0, set()
        for e in rows:
            if e.get("symbol") not in syms or not e.get("is_governance_flag") or e.get("pass") == "verified":
                continue
            if e.get("prompt_version") != PROMPT_VERSION or e.get("model_label") != self.clf.model_id or not _has_gist(e):
                continue
            if (e.get("event_date") or "")[:10] < lo:
                continue
            hashes = set(json.loads(e["member_url_hashes"]) if isinstance(e.get("member_url_hashes"), str) else (e.get("member_url_hashes") or [])) | {e.get("url_hash")}
            if hashes & have[e["symbol"]]:
                continue
            li = event_row_to_item(e)
            li.item.origin, li.item.purpose, li.item.pass_ = f"corpus:{e.get('run_id', '')}", "governance", "governance"
            kept[e["symbol"]].append(li)
            have[e["symbol"]] |= hashes
            added += 1
            used.add(e.get("run_id", ""))
        return {"events": added, "runs": sorted(used)}

    @staticmethod
    def _split_observation(scores, delta: list[dict]) -> dict:
        """Label and sentiment split, against the previous stored run when there is one. A lopsided split is a prompt to look, not an error:
        the rubric is absolute, so a genuinely clean set should look lopsided."""
        n = len(scores) or 1
        lab = Counter(s.governance_label for s in scores)
        sent = Counter("n/a" if s.company_sentiment is None else f"{s.company_sentiment:+d}" for s in scores)
        body = (f"Governance: Clean {lab.get('Clean', 0)} / Watch {lab.get('Watch', 0)} / Flag {lab.get('Flag', 0)} of {len(scores)}. "
                f"Sentiment: " + ", ".join(f"{k} x{v}" for k, v in sorted(sent.items(), reverse=True)) + ".")
        prev = [d for d in delta if d.get("prev_label")]
        if prev:
            pl = Counter(d["prev_label"] for d in prev)
            ps = Counter("n/a" if d["prev_sentiment"] is None else f"{d['prev_sentiment']:+d}" for d in prev)
            moved = [d for d in prev if d["change"] not in ("unchanged", "new name")]
            body += (f" Previous run ({prev[0]['prev_run']}): Clean {pl.get('Clean', 0)} / Watch {pl.get('Watch', 0)} / Flag {pl.get('Flag', 0)}; "
                     f"sentiment " + ", ".join(f"{k} x{v}" for k, v in sorted(ps.items(), reverse=True)) + f". {len(moved)} name(s) moved"
                     + (": " + "; ".join(f"{d['symbol']} ({d['change']})" for d in moved[:8]) + ("; ..." if len(moved) > 8 else "") if moved else "") + ".")
        else:
            body += " No previous stored run to compare against."
        if lab.get("Clean", 0) / n >= 0.60:
            body += (f" {lab['Clean']} of {len(scores)} Clean is a high share: check the 'NOT scored' rows and any verified events not applied before "
                     "reading it as a clean year.")
        if lab.get("Flag", 0) / n >= 0.30:
            body += f" {lab['Flag']} of {len(scores)} Flags is a high share: check each Flag's penalties against the verification gate."
        return {"title": "Label split vs the previous run.", "body": body}

    def _delta(self, scores) -> list[dict]:
        """B6 Delta: this run vs the previous stored scores/ table (the last non-replay run), never re-derived from news."""
        if self.corpus is None:
            return []
        try:
            ids = [m["run_id"] for m in self.corpus.manifests() if m.get("n_scores") and not m.get("replay")]
            if not ids:
                return []
            prev = {r_["symbol"]: r_ for r_ in self.corpus.scores([ids[-1]])}
        except Exception:
            return []
        out = []
        for s in scores:
            p = prev.get(s.symbol)
            if not p:
                out.append({"symbol": s.symbol, "prev_run": ids[-1], "prev_sentiment": None, "prev_governance": None, "prev_label": None,
                            "sentiment": s.company_sentiment, "governance": s.governance_score, "label": s.governance_label, "change": "new name"})
                continue
            ch = []
            if p.get("sentiment_bucket") != s.company_sentiment:
                ch.append(f"sentiment {p.get('sentiment_bucket')} -> {s.company_sentiment}")
            if p.get("gov_score") != s.governance_score:
                ch.append(f"governance {p.get('gov_score')} -> {s.governance_score:g}")
            out.append({"symbol": s.symbol, "prev_run": ids[-1], "prev_sentiment": p.get("sentiment_bucket"), "prev_governance": p.get("gov_score"),
                        "prev_label": p.get("gov_label"), "sentiment": s.company_sentiment, "governance": s.governance_score, "label": s.governance_label,
                        "change": "; ".join(ch) or "unchanged"})
        return out

    def _stamp(self, source_mode: str) -> str:
        """B6 honesty stamp for the scorecard header."""
        shape = "/".join(str(q) for _, _, q in self.windows) if self.cap_budget else "no cap, weights 1/.75/.5/.25"
        return (f"{source_mode} · as_of {self.as_of.isoformat()} · budget {self.budget if self.cap_budget else 'all'} ({shape}) · universe {self.universe_hash[:8] or 'n/a'} · prompt {PROMPT_VERSION} ({PROMPT_REVISION}) · "
                f"rubric v{self.r.version} ({self.r.sha256[:8]}) · model_label {self.clf.model_id} · model_verify python-gate")

    def _golden_gate(self) -> dict:
        from .golden import run_golden
        try:
            return run_golden(self.r, self.golden_file, corpus=self.corpus)
        except FileNotFoundError as e:
            return {"passed": False, "total": 0, "failed": 0, "error": str(e)}

    def _write_corpus(self, run_id: str, result: dict, holdings, raw) -> dict:
        """B1: append this run's tables to the corpus of record. Headline + snippet (<= 300 chars) only; never body text."""
        from .tiers import domain_of, tier_of
        now = datetime.now().isoformat(timespec="seconds")
        kept, scores, run = result["kept"], result["scores"], result["run"]
        drop_by_hash = {url_hash(d.url): d.reason for d in result["dropped"]}
        articles = []
        for h in holdings:
            rows = self._articles_seen.get(h.symbol)
            if rows is None:                                      # legacy sampler: record what was kept
                rows = [self._article_row(i, "") for i in raw.get(h.symbol, []) if i.kind == "news"]
            for r_ in rows:
                dr = r_["dropped_reason"] or drop_by_hash.get(r_["url_hash"])
                articles.append({**r_, "run_id": run_id, "as_of": self.as_of.isoformat(), "fetched_at": now, "dropped_reason": dr})
        events, verifs = [], []
        for h in holdings:
            for li in kept.get(h.symbol, []):
                if li.item.kind != "news":
                    continue
                it, lab = li.item, li.label
                gate = {k: getattr(lab, k) for k in ("subject", "occurred_at_company", "action_stage", "severity", "amount_inr_cr",
                                                    "people_direction", "role_tier", "event_key", "substance", "gist")}
                events.append({"event_id": it.event_id or url_hash(it.url), "symbol": it.symbol, "event_date": it.event_date or it.published,
                               "pass": it.pass_ or ("verified" if it.verified else "sentiment"), "n_sources": it.n_sources, "n_members": it.n_members,
                               "max_tier": it.max_tier or tier_of(domain_of(it.url, it.source)), "representative_url": it.url,
                               "member_url_hashes": it.member_url_hashes or [url_hash(it.url)], "event_type": lab.event_type,
                               "sentiment": lab.sentiment, "is_governance_flag": lab.governance_flag,
                               "governance_flag_type": lab.event_type if lab.governance_flag else None, "justification": lab.rationale,
                               "confidence": it.confidence, "window": it.window, "prompt_version": PROMPT_VERSION, "model_label": self.clf.model_id,
                               "labelled_at": now, "run_id": run_id, "as_of": self.as_of.isoformat(), "headline": it.title,
                               "snippet": (it.snippet or "")[:300], "url_hash": url_hash(it.url), "gate_json": gate, "materiality": lab.materiality,
                               "historical": lab.historical, "verified": it.verified, "penalty_override": it.penalty_override,
                               "source_ref": it.source_ref, "members_json": it.members, "purpose": it.purpose, "published_date": it.published})
        for s in scores:
            for p in s.governance_penalties:
                v = p.get("verification", {})
                verifs.append({"event_id": p.get("event_id") or url_hash(p["url"]), "symbol": s.symbol, "subject_is_company": v.get("subject_is_company"),
                               "event_at_company": v.get("event_at_company"), "direction": v.get("direction"), "severity": v.get("severity"),
                               "applied_penalty": p["penalty"], "model_verify": "python-gate", "verified_at": now, "run_id": run_id, "basis": p.get("basis")})
            for p in s.governance_ignored:
                verifs.append({"event_id": url_hash(p["url"]), "symbol": s.symbol, "subject_is_company": None, "event_at_company": None, "direction": None,
                               "severity": None, "applied_penalty": 0, "model_verify": "python-gate", "verified_at": now, "run_id": run_id,
                               "basis": "NOT penalised: " + p["why"]})
        srows = [{"run_id": run_id, "as_of": self.as_of.isoformat(), "symbol": s.symbol, "rubric_version": self.r.version, "rubric_sha256": self.r.sha256,
                  "sentiment_raw": s.company_sentiment_raw, "sentiment_bucket": s.company_sentiment, "gov_score": s.governance_score,
                  "gov_label": s.governance_label, "penalties_json": s.governance_penalties, "coverage_json": {"map": s.coverage_map, "windows": s.window_events,
                  "n_events": s.n_events, "n_results": s.n_results_events, "tier12_share": s.tier12_share, "conf_weighted_n": s.conf_weighted_n},
                  "insufficient": bool(s.sentiment_note.startswith("INSUFFICIENT")), "triage": s.sentiment_note, "published": run["published"],
                  "source_mode": run["source_mode"], "prompt_version": PROMPT_VERSION, "model_label": self.clf.model_id} for s in scores]
        manifest = {"as_of": self.as_of.isoformat(), "started_at": run["started_at"], "finished_at": now, "source_mode": run["source_mode"],
                    "universe_hash": self.universe_hash, "universe_n": len(holdings), "prompt_version": PROMPT_VERSION, "prompt_revision": PROMPT_REVISION, "rubric_version": self.r.version,
                    "rubric_sha256": self.r.sha256, "model_label": self.clf.model_id, "model_verify": "python-gate", "n_articles": len(articles),
                    "n_events": len(events), "n_llm_calls": self.llm_items, "n_llm_items": self.llm_items, "est_cost_inr": 0.0,
                    "n_scores": len(srows), "published": run["published"], "golden": run["golden"], "sampler": run["sampler"],
                    "warnings": ([] if run["published"] else ["golden regression set failed; scores not publishable"])
                                + ([f"{run['totals']['label_failed']} item(s) failed to label"] if run["totals"]["label_failed"] else [])
                                + (["backdated live index: NOT a point-in-time backtest"] if run["source_mode"] == "backdated-live-NOT-PIT" else []),
                    "merges": self._merges, "sample_reports": self._sample_reports, "out_dir": str(self.out),
                    "holdings": [{"symbol": h.symbol, "name": h.name, "sector": h.sector, "cap": h.cap, "weight": h.weight, "aliases": h.aliases} for h in holdings],
                    "lint": result.get("lint", []), "dropped_counts": {s.symbol: s.n_dropped for s in scores}}
        return self.corpus.write_run(run_id, manifest, articles, events, verifs, srows)

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
        if _cache_ok(hit):
            return hit["label"], hit["raw"], True
        if not bypass_cache and self._corpus_idx:               # B2: same url_hash + prompt + model -> the stored label, no model call
            row = self._corpus_idx.get(url_hash(item.url)) or next((self._corpus_idx[hh] for hh in item.member_url_hashes if hh in self._corpus_idx), None)
            if row:
                return _label_from_event_row(row), "corpus:" + str(row.get("run_id", "")), True
        label, raw = self.clf.classify(item, company)
        if label is not None:
            self.cache.put(key, label, raw)
        return label, raw, False

    def _params(self) -> dict:
        return {"news_source": type(self.news).__name__ if self.news else None, "actions_source": type(self.actions).__name__ if self.actions else None,
                "prices_source": type(self.prices).__name__ if self.prices else None, "max_articles_per_stock": self.max_articles,
                "governance_pass": self.governance_pass, "governance_cap": self.governance_cap, "full_text": self.fetch_text,
                "budget": self.budget, "windows": self.windows, "filings_source": type(self.filings).__name__ if self.filings else None,
                "fetch_text_events": self.fetch_text_events,
                "generic_headline_prefilter": self.prefilter, "as_of": self.as_of.isoformat(), "mode": self.mode,
                "retrieval_mode": self.retrieval_mode, "run_date": self.run_date.isoformat(), "universe_hash": self.universe_hash[:12],
                "fundamentals_pass": self.fundamentals_pass, "fundamentals_cap": self.fundamentals_cap,
                "verified_events_file": str(self.verified_events) if self.verified_events else "",
                "classifier": self.clf.model_id, "schema_enforced": bool(getattr(self.clf, "use_schema", False)),
                "max_label_failure_rate": self.max_label_failure, "allow_missing_news": self.allow_missing_news,
                "label_cache_on_disk": bool(self.cache.path)}

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
        if self.stratified:
            dropped += self._sample_drops.get(h.symbol, [])
            items, cdrops = self._to_events(h, items)
            dropped += cdrops
        need = [(i, h.name) for i in items if i.prelabel is None and i.url.strip()]
        todo = [(i, c) for i, c in need if not _cache_ok(self.cache.get(self._key(i))) and not (self._corpus_idx and (
            url_hash(i.url) in self._corpus_idx or any(hh in self._corpus_idx for hh in i.member_url_hashes)))]
        self.llm_items += len(todo)
        ui.label_start(len(todo), len(need) - len(todo), Counter(i.symbol for i, _ in todo), h.symbol)
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
            li = LabelledItem(it, to_label(label_d), resp, cached, attempts)
            from .lint import price_only
            if it.kind == "news" and li.label.event_type != "price_move" and price_only(it.title, self.r):
                # Reconciliation fix 5: an item that is only about the share price is not sentiment evidence
                li.label.event_type, li.label.sentiment, li.label.governance_flag = "price_move", 0, False
                li.label.rationale = "Price-language lint: headline is only about the share price - excluded from sentiment."
            kept.append(li)
            ev[li.label.event_type] += 1
        if self.stratified:                      # A3 stage 3: post-label merge (same type, same flag, within 3 days = one event)
            kept, merged = post_label_merge(kept, results_types=set(self.r["sentiment"]["results_event_types"]))
            for keep_, drop_ in merged:
                self._merges.append({"symbol": h.symbol, "kept": keep_.item.title, "kept_date": keep_.item.published, "merged": drop_.item.title,
                                     "merged_date": drop_.item.published, "event_type": keep_.label.event_type, "event_id": keep_.item.event_id})
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
                  "articles stored": "none (headline+URL kept only in the report)",
                  "retrieval": self.retrieval_mode + (" - NOT a point-in-time backtest" if self.retrieval_mode == "live_index_backdated" else ""),
                  "output": self.out}, holdings)
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
        verified_info = {"file": str(self.verified_events) if self.verified_events else "", "applied": [], "skipped": []}
        if self.verified_events and Path(self.verified_events).exists():     # union-then-rescore (Reconciliation fix 7)
            from .verified import load_verified
            vit, vskip = load_verified(self.verified_events, self.as_of, self.r["windows"]["news_days"], {h.symbol for h in holdings})
            for li in vit:
                kept[li.item.symbol].append(li)
            verified_info["applied"] = [{"symbol": li.item.symbol, "headline": li.item.title, "status": li.item.verified, "weight": li.item.penalty_override,
                                         "source": li.item.source_ref} for li in vit]
            verified_info["skipped"] = vskip
            ui.note(f"  human-verified evidence merged: {len(vit)} event(s) applied, {len(vskip)} not applicable at this as-of date "
                    f"({sum(1 for v in vit if v.item.verified == 'provisional')} provisional) - re-scored once under the one rubric", "d")
        union_info = {"events": 0, "runs": []}
        if self.corpus is not None and self.corpus_union:          # Reconciliation fix 7 across runs: governance events already on record
            union_info = self._union_corpus_governance(holdings, kept)
            if union_info["events"]:
                ui.note(f"  corpus union: {union_info['events']} governance event(s) from earlier run(s) {', '.join(union_info['runs'])} "
                        "added to this run's evidence (same prompt + model; disclosed as origin corpus:<run_id>)", "d")
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
                     "low_confidence": next(s.low_confidence for s in scores if s.symbol == h.symbol),
                     "relevant_articles": next(s.relevant_articles for s in scores if s.symbol == h.symbol),
                     "span_days": next(s.evidence_span_days for s in scores if s.symbol == h.symbol),
                     "sentiment_status": next((s.sentiment_note or "ok") for s in scores if s.symbol == h.symbol)}
                    for h in holdings]
        from .corpus import make_run_id, source_mode_from_retrieval
        run_id = make_run_id(self.as_of, self.started)
        source_mode = source_mode_from_retrieval(self.retrieval_mode, self.news_choice or {"GoogleNewsRSS": "gnews", "GdeltNews": "gdelt"}.get(type(self.news).__name__, "file"))
        golden = self._golden_gate()
        run = {"as_of": self.as_of.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
               "run_id": run_id, "source_mode": source_mode, "started_at": self.started.isoformat(timespec="seconds"),
               "sampler": "stratified-v2.1" if self.stratified else "latest-N", "n_llm_items": self.llm_items,
               "golden": golden, "published": bool(golden.get("passed")),
               "stamp": self._stamp(source_mode),
               "sentinelq_version": __version__, "rubric_version": self.r.version,
               "rubric_sha256": self.r.sha256, "classifier_model": self.clf.model_id,
               "prompt_version": PROMPT_VERSION, "mode": self.mode,
               "retrieval": {"mode": self.retrieval_mode, "as_of": self.as_of.isoformat(), "run_date": self.run_date.isoformat(),
                             "note": {"live_index_backdated": f"LIVE news index queried on {self.run_date:%d-%b-%Y} with a cut-off of {self.as_of:%d-%b-%Y}: "
                                                              "NOT a point-in-time backtest (search rankings decay; older articles drop out of feeds).",
                                      "dated_archive": "Retrieved from a dated archive (GDELT history) for the as-of window.",
                                      "supplied": "News supplied from a file.", "live": "Live run: as-of date is current."}.get(self.retrieval_mode, "")},
               "universe_hash": self.universe_hash, "verified": verified_info, "corpus_union": union_info, "title": self.meta["title"], "coverage": self.meta["coverage"],
               "totals": {"label_failed": len(failed), "retrieved": sum(len(v) for v in raw.values()),
                          "kept": sum(len(v) for v in kept.values()), "dropped": len(dropped)}}
        result = dict(run=run, scores=scores, kept=kept, dropped=dropped, coverage=coverage,
                      price_context=ctx, relative=rel, ingest_errors=ingest_errors, raw_log=raw_log, delta=self._delta(scores))
        ui.stage("Report", "workbook, commentary per stock, portfolio observations, PDF")
        narr, done_n, lint = {}, 0, []
        from .lint import strip_price_sentences
        with ThreadPoolExecutor(2) as ex:
            for sc, out in zip(scores, ex.map(lambda sc: self.narrator.holding(sc, kept[sc.symbol]), scores)):
                clean, removed = strip_price_sentences(out.get("sentiment_rationale", ""), self.r)     # Reconciliation fix 5
                if removed:
                    out = {**out, "sentiment_rationale": clean}
                    lint += [{"symbol": sc.symbol, "removed_sentence": x} for x in removed]
                narr[sc.symbol] = out
                done_n += 1
                ui.narrate(done_n, len(scores), sc.name, sc.symbol)
        ui.clear_status()
        ui.note("  writing portfolio-level observations ...", "d")
        obs = self.narrator.portfolio(portfolio_facts(scores, kept))
        obs.append(self._split_observation(scores, result.get("delta") or []))      # deterministic; never written by the model
        result.update(narrative=narr, observations=obs, lint=lint)
        write_reports(self.out, result, self.r)                          # after the commentary: the scorecard carries its one-line read
        if lint:
            ui.note(f"  price-language lint: removed {len(lint)} sentence(s) from sentiment commentary (share-price moves never feed sentiment)", "y")
        (self.out / "narrative.json").write_text(json.dumps({"holdings": narr, "observations": obs}, indent=2), encoding="utf-8")
        (self.out / "lint.json").write_text(json.dumps(lint, indent=2), encoding="utf-8")
        from .report import save_or_sidestep
        save_or_sidestep(lambda f: build_pdf(f, result, self.r, narr, obs, self.meta), self.out / "sentinelq_scorecard.pdf")
        from .audit import write_audit
        write_audit(self.out, result, self.r, self._params(), list(getattr(self.news, "audit", [])))
        ui.note("[STEP 6/6 REPORT] audit record written: " + str(self.out / "audit" / "RUN_RECORD.md"), "g")
        if self.corpus is not None:
            try:
                paths = self._write_corpus(run_id, result, holdings, raw)
                ui.note(f"[STEP 6/6 REPORT] corpus of record appended: run {run_id} -> {paths['manifest']}", "g")
            except Exception as e:
                ui.note(f"WARNING: corpus tables not written ({e!r}); the run outputs above are complete", "y")
        if not run["published"]:
            ui.note(f"WARNING: golden regression set FAILED ({golden.get('failed')} of {golden.get('total')}); scores are stamped published=false", "r")
        t = run["totals"]
        ui.finish({"retrieved": t["retrieved"], "kept": t["kept"], "dropped": t["dropped"],
                   "label failures": t["label_failed"], "stocks": len(scores),
                   "flags": ", ".join(x.symbol for x in scores if x.governance_label == "Flag") or "none"},
                  [str(self.out / "sentinelq_scorecard.pdf"), str(self.out / "sentinelq_report.xlsx")])
        return result
