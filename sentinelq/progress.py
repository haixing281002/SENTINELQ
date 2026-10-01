"""Live run display. Shows stage, an overall bar with ETA, per-stock ingest summaries (article count, style mix,
parse mode) and a live line for every article as it is labelled. Always tees a plain-text copy to a log file and a
machine-readable snapshot, so a long run can be followed with `tail -f work/run.log` from another terminal."""
from __future__ import annotations
import json
import os
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

STAGES = ["Input", "Ingest", "Classify", "Validate", "Score", "Report"]
NAMES = {i + 1: n.upper() for i, n in enumerate(STAGES)}
WHAT = {1: "read the stock list", 2: "fetch news + corporate actions (source URL and date on every item)",
        3: "the model labels each article (fixed taxonomy, sentiment -2..+2, governance flag)",
        4: "schema / source / date checks; failures retried once, then dropped with a reason",
        5: "deterministic rubric arithmetic - no model", 6: "workbook, commentary, PDF, audit record"}
GLYPH = {".": "\u25cb", "~": "\u25d0", "+": "\u2714", "x": "\u2716"}
GLYPH_ASCII = {".": ".", "~": "~", "+": "+", "x": "x"}
C = {"g": "32", "r": "31", "y": "33", "b": "34", "c": "36", "m": "35", "d": "2", "B": "1"}


def fmt_t(sec: float) -> str:
    sec = int(max(sec, 0))
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m" if sec >= 3600 else f"{sec // 60}m{sec % 60:02d}s"


class Display:
    def __init__(self, mode: str = "auto", log_path: str | Path | None = None, ascii_only: bool = False,
                 json_path: str | Path | None = None, stream=None):
        self.stream = stream or sys.stdout
        self.mode = mode                                  # auto | live | plain | off
        tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.live = mode == "live" or (mode == "auto" and tty)
        self.off = mode == "off"
        self.color = self.live
        self.ascii = ascii_only
        self.t0 = time.time()
        self.lock = threading.RLock()
        self._status = ""
        self._last_pct = {}
        self._json_t = 0.0
        self.snapshot = {"stage": "", "stage_index": 0, "done": 0, "total": 0, "current": "", "counts": {}}
        self.json_path = Path(json_path) if json_path else None
        self.log = None
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            self.log = open(log_path, "w", encoding="utf-8", buffering=1)
        if os.name == "nt":
            os.system("")                                 # enable ANSI escape codes on Windows 10+ consoles
        try:
            self.stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        self.stage_t: dict[str, float] = {}
        self.track: dict[str, list[str]] = {}
        self.current = "-"
        self.stock_left, self.stock_bad = Counter(), Counter()
        self.label_total = self.label_done = self.label_fail = 0
        self.label_t0 = time.time()
        self._stage = ""
        self._stage_start = self.t0
        self.counts = Counter()

    # ---- low level ----------------------------------------------------------------------------------------
    @property
    def width(self) -> int:
        return max(60, min(shutil.get_terminal_size((150, 20)).columns, 170))

    def _c(self, text: str, *codes: str) -> str:
        return f"\033[{';'.join(C[c] for c in codes)}m{text}\033[0m" if self.color and codes else text

    def _bar(self, done: int, total: int, w: int = 24) -> str:
        f = int(w * done / total) if total else 0
        full, empty = ("#", "-") if self.ascii else ("█", "░")
        return full * f + empty * (w - f)

    def line(self, text: str = "", *codes: str, raw: str | None = None) -> None:
        if self.off:
            return
        with self.lock:
            plain = raw if raw is not None else text
            if self.log:
                self.log.write(plain + "\n")
            if self.live and self._status:
                self.stream.write("\r\033[K")
            self.stream.write(self._c(text, *codes) + "\n")
            if self.live and self._status:
                self.stream.write(self._status)
            self.stream.flush()

    def status(self, text: str, key: str = "", done: int = 0, total: int = 0) -> None:
        """Transient bar line (live mode) or a throttled line (plain mode, ~every 5%)."""
        if self.off:
            return
        with self.lock:
            if self.live:
                self._status = text[: self.width - 1]
                self.stream.write("\r\033[K" + self._status)
                self.stream.flush()
            elif key != "ingest":                        # plain mode: the per-stock lines already tell the story
                pct = int(100 * done / total) if total else 100
                if done == total or pct >= self._last_pct.get(key, -5) + 5:
                    self._last_pct[key] = pct
                    self.line(text)
            if self.log and self.live:
                pass
            self._snap(done=done, total=total)

    def clear_status(self) -> None:
        with self.lock:
            if self.live and self._status:
                self.stream.write("\r\033[K")
            self._status = ""

    def _snap(self, **kw) -> None:
        self.snapshot.update(kw)
        self.snapshot.update(elapsed=int(time.time() - self.t0), counts=dict(self.counts))
        if self.json_path and time.time() - self._json_t > 1.0:
            self._json_t = time.time()
            try:
                self.json_path.parent.mkdir(parents=True, exist_ok=True)
                self.json_path.write_text(json.dumps(self.snapshot))
            except OSError:
                pass

    def _eta(self, done: int, total: int, since: float) -> str:
        if done <= 0 or done >= total:
            return ""
        rate = (time.time() - since) / done
        return f" ETA {fmt_t(rate * (total - done))}"

    # ---- step tracker: which of the six steps each stock is at --------------------------------------------
    def track_init(self, symbols) -> None:
        self.track = {sym: ["."] * 6 for sym in symbols}

    def track_set(self, sym: str, step: int, state: str) -> None:
        if sym in self.track:
            self.track[sym][step - 1] = state

    def track_summary(self) -> str:
        n = len(self.track) or 1
        return " | ".join(f"{i} {NAMES[i].title()} {sum(1 for v in self.track.values() if v[i - 1] == '+')}/{n}" for i in range(1, 7))

    def tracker(self) -> None:
        g = GLYPH_ASCII if self.ascii else GLYPH
        self.line("  STEP TRACKER   " + "  ".join(f"{i} {NAMES[i].title():<8}" for i in range(1, 7)), "B")
        for sym, st in self.track.items():
            self.line(f"  {sym:<14}" + "  ".join(f"{g[x]:<10}" for x in st))
        self.line("  legend: " + f"{g['.']} pending  {g['~']} running  {g['+']} done  {g['x']} failed", "d")

    def step(self, n: int, msg: str, *codes: str) -> None:
        self.line(f"[STEP {n}/6 {NAMES[n]}] {msg}", *codes)

    # ---- stages -------------------------------------------------------------------------------------------
    def stage(self, name: str, detail: str = "") -> None:
        with self.lock:
            now = time.time()
            if self._stage:
                self.stage_t[self._stage] = now - self._stage_start
            self._stage, self._stage_start = name, now
            i = STAGES.index(name) + 1
            self.clear_status()
            self.line()
            self.line(f"== Stage {i}/6  {name.upper():<9} {detail}   [elapsed {fmt_t(now - self.t0)}]", "B", "c")
            self._snap(stage=name, stage_index=i, done=0, total=0)

    def start(self, cfg: dict, holdings: list) -> None:
        self.stage("Input", "")
        self.line("Sentinel Q run", "B")
        for k, v in cfg.items():
            self.line(f"  {k:<14} {v}")
        self.line(f"  {'stocks':<14} {len(holdings)}: " + ", ".join(h.symbol for h in holdings[:12]) + (" ..." if len(holdings) > 12 else ""))
        if self.log:
            self.line(f"  {'live log':<14} tail -f {self.log.name}")
        self.track_init([h.symbol for h in holdings])
        for h in holdings:
            self.track_set(h.symbol, 1, "+")
        self.step(1, f"{WHAT[1]}: {len(holdings)} stock(s) (symbol, company, sector) - nothing else is required", "B")
        for h in holdings:
            self.line(f"        {h.symbol:<14} {h.name:<38} {h.sector}{' / ' + h.cap if h.cap else ''}", "d")
        self.line("")
        self.line("  The six steps every stock passes, in order:", "B")
        for i in range(1, 7):
            self.line(f"    {i}. {NAMES[i].title():<9} {WHAT[i]}", "d")

    # ---- ingest -------------------------------------------------------------------------------------------
    def ingest_stock(self, idx: int, n: int, h, found: int, kept: int, actions: int, styles: Counter,
                     from_cache: bool, errors: list[str], secs: float, cap: int | None = None, spark=None) -> None:
        self.counts["articles"] += kept
        self.track_set(h.symbol, 2, "x" if errors else "+")
        head = f"{idx:>2}/{n} {h.symbol:<12} {h.name}  ({h.sector}{' - ' + h.cap if h.cap else ''}) - {WHAT[2]}"
        self.step(2, head, "B")
        cap_note = f" (capped from {found})" if found > kept else ""
        src = "cache" if from_cache else f"{secs:.1f}s"
        self.line(f"        news    {self._bar(kept, cap or max(found, kept, 1), 20)} {kept} articles{cap_note}  [{src}]")
        if spark:
            self.line(f"        months  [{spark[0]}]  {spark[1]}", "d")
        if styles:
            self.line("        styles  " + " | ".join(f"{k} {v}" for k, v in styles.most_common()), "d")
        if actions:
            self.line(f"        actions {actions} corporate-action item(s)", "d")
        for e in errors:
            self.line(f"        ERROR   {e}", "r")
        self.status(f"Ingest {idx}/{n}  {h.symbol}", "ingest", idx, n)

    def begin(self, sym: str, step: int) -> None:
        self.track_set(sym, step, "~")

    def fulltext(self, done: int, total: int) -> None:
        self.status(f"Fetching article text  {self._bar(done, total)} {done}/{total}{self._eta(done, total, self._stage_start)}",
                    "fulltext", done, total)

    def parse_summary(self, h, parse: Counter, styles: Counter) -> None:
        if not parse:
            return
        self.line(f"        {h.symbol:<12} parse: " + " | ".join(f"{k} {v}" for k, v in parse.most_common()), "d")

    # ---- classify -----------------------------------------------------------------------------------------
    def label_start(self, total: int, cached: int, per_stock: Counter | None = None, sym: str = "") -> None:
        self.label_total, self.label_done, self.label_fail = total, 0, 0
        self.stock_left = Counter(per_stock or {})
        self.stock_bad = Counter()
        self.label_t0 = time.time()
        for k in list(self.stock_left) + ([sym] if sym else []):
            self.track_set(k, 3, "~")
        who = f"{sym}: " if sym else ""
        self.step(3, f"{who}model reads {total} article(s) ({cached} already labelled in cache) - {WHAT[3]}", "B")
        self.status(f"Labelling {self._bar(0, max(total, 1))} 0/{total}", "label", 0, max(total, 1))

    def _label_bar(self) -> None:
        d, t = self.label_done + self.label_fail, max(self.label_total, 1)
        self.status(f"Labelling {self._bar(d, t)} {d}/{self.label_total}  ok {self.label_done}  failed {self.label_fail}"
                    f"  now: {self.current}{self._eta(d, t, self.label_t0)}", "label", d, t)

    def _stock_tick(self, sym: str, failed: bool) -> None:
        self.current = sym
        if sym in self.stock_left:
            self.stock_left[sym] -= 1
            self.stock_bad[sym] += failed
            if self.stock_left[sym] == 0:
                bad = self.stock_bad[sym]
                self.track_set(sym, 3, "x" if bad else "+")
                self.line(f"  \u2714 {sym} finished labelling" + (f" ({bad} failed)" if bad else ""), "y" if bad else "g")

    def item_labelled(self, item, label: dict) -> None:
        with self.lock:
            self.label_done += 1
            self.counts["labelled"] += 1
            ev = str(label.get("event_type", "?"))
            if label.get("about_company") is False:
                res, col = "not about company -> dropped", "y"
            else:
                s = label.get("sentiment", 0)
                gov = " GOV" if label.get("governance_flag") else ""
                hist = " hist" if label.get("historical") else ""
                res = f"{ev:<19} {s:+d}{gov}{hist}"
                col = "g" if isinstance(s, int) and s > 0 else "r" if isinstance(s, int) and s < 0 else None
            self._feed(item, res, col)
            self._stock_tick(item.symbol, False)
            self._label_bar()

    def item_failed(self, item, err: str) -> None:
        with self.lock:
            self.label_fail += 1
            self.counts["label_failed"] += 1
            self._feed(item, f"LABEL FAILED: {err[:60]}", "r")
            self._stock_tick(item.symbol, True)
            self._label_bar()

    def _feed(self, item, res: str, col: str | None) -> None:
        w = self.width
        style = (item.style or "?")[:15]
        parse = (item.parse or "headline-only").replace("headline-only", "headline")[:14]
        prefix = f"    {item.symbol:<11} {style:<15} {parse:<14} > {res:<29} "
        room = max(20, w - len(prefix) - 2)
        t = item.title.replace("\n", " ")
        self.line(prefix + '"' + (t[: room - 1] + "…" if len(t) > room else t) + '"', *( (col,) if col else ()))

    def batch_note(self, msg: str, bad: bool = False) -> None:
        self.line("  [labeller] " + msg, "y" if bad else "d")

    # ---- validate / score / report -----------------------------------------------------------------------
    def stock_validated(self, h, kept: int, total: int, dropped: Counter, events: Counter, mean: float | None) -> None:
        drop = ", ".join(f"{v} {k}" for k, v in dropped.most_common()) or "none"
        ev = ", ".join(f"{k} {v}" for k, v in events.most_common(4)) or "-"
        self.track_set(h.symbol, 3, "+" if self.track.get(h.symbol, ["."] * 6)[2] != "x" else "x")
        self.track_set(h.symbol, 4, "+")
        self.step(4, f"{h.symbol}: kept {kept}/{total}   dropped: {drop}", "g" if not dropped else "y")
        self.line(f"        events kept: {ev}", "d")
        self.line("        progress: " + self.track_summary(), "d")

    def scored(self, scores: list) -> None:
        self.stage("Score", WHAT[5])
        for sc in scores:
            col = "r" if sc.governance_label == "Flag" else "y" if sc.governance_label == "Watch" else "g"
            sent = "n/a" if sc.company_sentiment is None else (f"{sc.company_sentiment:+d}" if sc.company_sentiment else "0")
            pens = " ".join(f"{p['penalty']:+g} {p['event_type'].replace('_', ' ')}" for p in sc.governance_penalties) or "no penalties"
            ign = f", {len(sc.governance_ignored)} item(s) deliberately NOT penalised" if sc.governance_ignored else ""
            self.track_set(sc.symbol, 5, "+")
            self.step(5, f"{sc.symbol}: sentiment {sent if sc.company_sentiment is not None else 'n/a - ' + sc.sentiment_note.lower()}"
                         f"{'' if sc.company_sentiment is None else f' (weighted mean {sc.company_sentiment_raw} over {sc.relevant_articles} relevant articles, {sc.evidence_first}..{sc.evidence_last})'}  |  "
                         f"governance 100 {pens} = {sc.governance_score:g} {sc.governance_label.upper() if sc.governance_label == 'Flag' else sc.governance_label}"
                         f"{ign}" + ("  |  LOW CONFIDENCE" if sc.low_confidence else ""), col)
        self.line("")
        self.tracker()

    def narrate(self, done: int, total: int, name: str, sym: str = "") -> None:
        if sym:
            self.track_set(sym, 6, "~")
        self.status(f"Writing commentary {self._bar(done, total)} {done}/{total}  {name[:28]}{self._eta(done, total, self._stage_start)}",
                    "narr", done, total)

    def note(self, msg: str, *codes: str) -> None:
        self.line(msg, *codes)

    def finish(self, summary: dict, paths: list[str]) -> None:
        with self.lock:
            self.clear_status()
            self.stage_t[self._stage] = time.time() - self._stage_start
            for sym in self.track:
                self.track_set(sym, 6, "+")
            self.line()
            self.step(6, "files written (see list below); every number is reproducible with:  python -m sentinelq verify <run folder>", "B", "g")
            self.tracker()
            self.line("== DONE", "B", "g")
            for k, v in summary.items():
                self.line(f"  {k:<16} {v}")
            self.line("  stage times      " + "  ".join(f"{k} {fmt_t(v)}" for k, v in self.stage_t.items()))
            self.line(f"  total            {fmt_t(time.time() - self.t0)}")
            for p in paths:
                self.line(f"  wrote            {p}", "c")
            self._snap(stage="done")
            if self.json_path:
                self.json_path.write_text(json.dumps(self.snapshot))
