"""Turn a pasted stock table (CD_NSE Symbol | Accord Code | ISIN Code | # Stocks | Company Name) into Holdings,
and fill in sector + cap automatically: known stocks come from portfolio/universe.csv (so labels match the reference
report); unknown ones are classified by Claude (`claude -p`, no API key) and cached."""
from __future__ import annotations
import csv
import json
import re
from pathlib import Path

from .models import Holding

SECTORS = ["BFSI", "Bank", "Autos", "Consumption", "FMCG", "Healthcare", "Metals & Mining", "Capital Goods",
           "Infra", "Diversified", "Energy", "IT", "Telecom", "Real Estate", "Chemicals", "Media", "Utilities"]
_SUFFIX = re.compile(r"\s+(ltd\.?|limited|co\.?|corporation|corp\.?)$", re.I)
_ISIN = re.compile(r"^IN[A-Z0-9]{10}$")


def clean_name(n: str) -> str:
    n = n.strip()
    while True:
        m = _SUFFIX.sub("", n)
        if m == n:
            return n
        n = m


def looks_like_table(text: str) -> bool:
    head = text.lstrip().splitlines()[0].lower() if text.strip() else ""
    return "cd_nse" in head or "company name" in head or "isin" in head or any(
        _ISIN.match(t) for t in re.split(r"\t|\s{2,}", text.strip().splitlines()[0]))


def parse_stock_table(text: str) -> list[Holding]:
    """Tolerates tabs or 2+ spaces, optional header, NSE: prefixes, and blank lines."""
    out, seen = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in re.split(r"\t|\s{2,}", line.strip()) if p.strip()]
        if parts[0].lower().startswith(("cd_nse", "symbol")):
            continue
        sym = re.sub(r"^(NSE|BSE):", "", parts[0], flags=re.I).upper()
        isin = next((p for p in parts if _ISIN.match(p)), "")
        name = parts[-1] if len(parts) > 1 and not parts[-1].isdigit() and not _ISIN.match(parts[-1]) else sym
        if sym in seen:
            continue
        seen.add(sym)
        out.append(Holding(sym, clean_name(name), "", "", ""))
        out[-1].isin = isin  # type: ignore[attr-defined]
    return out


def _universe(path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, newline="", encoding="utf-8-sig") as f:
        return {r["symbol"].upper(): r for r in csv.DictReader(f)}


def _classify_with_claude(unknown: list[Holding], model: str | None):
    from .claude_code import extract_json, run_claude
    prompt = (
        "For each Indian listed company below, return its sector and market-cap bucket. Reply with ONLY a JSON array of "
        '{"symbol","sector","cap"} objects. sector MUST be one of: ' + json.dumps(SECTORS) +
        " (use the closest; use 'Bank' for banks, 'BFSI' for non-bank financials/brokers/AMCs/insurers). "
        "cap is Large, Mid or Small per SEBI/AMFI classification (top 100 by market cap = Large, 101-250 = Mid, rest = Small). "
        "Use only what you know about the company; do not guess a different company.\nCOMPANIES:\n" +
        "\n".join(json.dumps({"symbol": h.symbol, "name": h.name, "isin": getattr(h, "isin", "")}) for h in unknown))
    return {d["symbol"].upper(): d for d in extract_json(run_claude(prompt, model), "[")}


def enrich(holdings: list[Holding], universe="portfolio/universe.csv", cache=".cache/sector_cache.json",
           mode: str = "claude-code", model: str | None = None) -> list[Holding]:
    uni, cpath = _universe(universe), Path(cache)
    cached = json.loads(cpath.read_text()) if cpath.exists() else {}
    for h in holdings:
        if h.sector:
            continue
        row = uni.get(h.symbol) or cached.get(h.symbol)
        if row:
            h.sector, h.cap = row["sector"], row.get("cap", "")
            if h.symbol in uni and uni[h.symbol].get("name"):
                h.name = uni[h.symbol]["name"]
    unknown = [h for h in holdings if not h.sector]
    if unknown and mode == "claude-code":
        try:
            res = _classify_with_claude(unknown, model)
        except Exception as e:
            print(f"  [resolve] sector lookup failed: {e}")
            res = {}
        for h in unknown:
            d = res.get(h.symbol)
            if d and d.get("sector"):
                h.sector, h.cap = d["sector"], d.get("cap", "")
                cached[h.symbol] = {"sector": h.sector, "cap": h.cap}
        if cached:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(cached, indent=2))
    for h in holdings:
        if not h.sector:
            h.sector = "Unclassified"
            print(f"  [resolve] WARNING: no sector for {h.symbol}; sector sentiment will pool with other 'Unclassified' names")
    src = [h for h in holdings if h.symbol not in uni]
    if src:
        print("  [resolve] auto-classified (not in universe): " + ", ".join(f"{h.symbol}={h.sector}/{h.cap}" for h in src))
    return holdings
