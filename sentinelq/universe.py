"""Universe lock (Reconciliation fix 4): hash and diff the stock list against the last confirmed run; refuse to run on an unconfirmed change,
so a holding (e.g. JB Chemicals) can never disappear silently."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from pathlib import Path


def universe_hash(symbols: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(s.upper() for s in symbols)).encode()).hexdigest()


def check_universe(key: str, symbols: list[str], lock_file: str | Path, confirm: bool = False, expect_count: int | None = None) -> tuple[str, str]:
    """Returns (hash, message). Raises SystemExit on an unconfirmed change or a wrong expected count."""
    syms = sorted({s.upper() for s in symbols})
    h = universe_hash(syms)
    if expect_count is not None and len(syms) != expect_count:
        raise SystemExit(f"Universe has {len(syms)} stocks but --expect-count is {expect_count}. Nothing was run.")
    path = Path(lock_file)
    lock = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    prev = lock.get(key)
    save = False
    if prev is None:
        msg, save = f"universe recorded for the first time: {len(syms)} stocks (hash {h[:12]})", True
    elif prev["hash"] == h:
        msg = f"universe unchanged since the last confirmed run: {len(syms)} stocks (hash {h[:12]})"
    else:
        added, removed = sorted(set(syms) - set(prev["symbols"])), sorted(set(prev["symbols"]) - set(syms))
        diff = f"ADDED {added or 'none'}; REMOVED {removed or 'none'} ({len(prev['symbols'])} -> {len(syms)} stocks)"
        if not confirm:
            raise SystemExit(f"The stock list changed since the last confirmed run: {diff}.\n"
                             "A holding must not appear or disappear silently. If this is intended, re-run with --confirm-universe. Nothing was run.")
        msg, save = f"universe change CONFIRMED: {diff}", True
    if save:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock[key] = {"hash": h, "symbols": syms, "confirmed_at": datetime.now().isoformat(timespec="seconds")}
        path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return h, msg
