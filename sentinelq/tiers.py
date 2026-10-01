"""A2 - source tiers by domain, read from sentinelq/config/source_tiers.yaml. Deterministic; unknown = T3."""
from __future__ import annotations
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from .config import TIERS_FILE

TIER_ORDER = {"T1": 1, "T2": 2, "T3": 3, "T4": 4}


def _parse_simple_yaml(text: str) -> dict:
    """Enough YAML for source_tiers.yaml (lists, 'default', and one flat mapping) when PyYAML is not installed."""
    out, key, buf = {}, None, ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line)
        if m and not line.startswith(" "):
            if key is not None:
                out[key] = _value(buf)
            key, buf = m.group(1), m.group(2)
        else:
            buf += " " + line.strip()
    if key is not None:
        out[key] = _value(buf)
    return out


def _value(s: str):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        return [x.strip().strip("'\"") for x in s[1:-1].split(",") if x.strip()]
    if s.startswith("{") and s.endswith("}"):
        d = {}
        for part in s[1:-1].split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                d[k.strip().strip("'\"")] = float(v.strip())
        return d
    return s


@lru_cache(maxsize=4)
def load_tiers(path: str | Path | None = None) -> dict:
    p = Path(path) if path else TIERS_FILE
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except Exception:
        data = _parse_simple_yaml(text)
    tiers: dict[str, str] = {}
    for t in ("T1", "T2", "T4"):
        for dom in data.get(t) or []:
            tiers[str(dom).lower().removeprefix("www.")] = t
    import json
    import os
    for dom, t in (json.loads(os.environ["SENTINELQ_TIER_OVERRIDES"]) if os.environ.get("SENTINELQ_TIER_OVERRIDES") else {}).items():
        tiers[dom.lower()] = t                      # tests / what-ifs only; production tiers live in the yaml
    return {"domains": tiers, "weight": {k: float(v) for k, v in (data.get("tier_weight") or {}).items()},
            "default": "T3", "sha": __import__("hashlib").sha256(text.encode()).hexdigest()[:12]}


def domain_of(url: str, source: str = "") -> str:
    """The publisher domain. Google News RSS links point at news.google.com, so the <source> domain (kept in item.source) wins."""
    s = (source or "").strip().lower()
    if s and "." in s and " " not in s:
        return s.removeprefix("www.")
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    if host in ("news.google.com", ""):
        return s or host
    return host


def tier_of(domain: str, tiers: dict | None = None) -> str:
    t = tiers or load_tiers()
    d = (domain or "").lower().removeprefix("www.")
    if d in t["domains"]:
        return t["domains"][d]
    parts = d.split(".")
    for i in range(1, len(parts) - 1):          # sub.domain.com -> domain.com
        if ".".join(parts[i:]) in t["domains"]:
            return t["domains"][".".join(parts[i:])]
    return t["default"]


def tier_weight(tier: str, tiers: dict | None = None) -> float:
    t = tiers or load_tiers()
    return t["weight"].get(tier, t["weight"].get("T3", 0.7))


def best_tier(tiers_seen: list[str]) -> str:
    return min(tiers_seen or ["T3"], key=lambda x: TIER_ORDER.get(x, 3))
