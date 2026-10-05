"""Human-verified evidence (Reconciliation fix 7: union-then-rescore). A CSV of events someone has checked against primary sources is
merged into the labelled evidence and re-scored once under the one rubric - never averaged, never a side picked.

Columns: symbol, date, headline, event_type, sentiment, subject, severity, action_stage, people_direction, role_tier, amount_inr_cr,
event_key, penalty_override, status (verified|provisional|convention), valid_as_of, source_ref, url, note.
`date` blank = "in force at the as-of date": applied only when the run's as-of equals `valid_as_of` (so a July fact is never injected into a different date),
unless `standing` = yes (or status = convention): a structural convention such as the MNC-subsidiary discount applies at every as-of.
`penalty_override` = a weight a person set; it is recorded in the audit and not second-guessed by the gate."""
from __future__ import annotations
import csv
from datetime import date, timedelta
from pathlib import Path

from .models import Label, LabelledItem, RawItem


def load_verified(path: str | Path, as_of: date, lookback_days: int, symbols: set[str]) -> tuple[list[LabelledItem], list[dict]]:
    out, skipped = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sym = r["symbol"].strip().upper()
            if sym not in symbols:
                continue
            d = (r.get("date") or "").strip()
            if d:
                dd = date.fromisoformat(d)
                if dd > as_of or dd < as_of - timedelta(days=lookback_days):
                    skipped.append({"symbol": sym, "headline": r["headline"], "why": f"event date {d} is outside the lookback of {as_of}"})
                    continue
                published = d
            else:
                standing = (r.get("standing") or "").strip().lower() in ("yes", "true", "1") or (r.get("status") or "").strip() == "convention"
                if not standing and (r.get("valid_as_of") or "").strip() != as_of.isoformat():
                    skipped.append({"symbol": sym, "headline": r["headline"], "why": f"verified for as-of {r.get('valid_as_of') or '?'}, not {as_of} (in-force fact)"})
                    continue
                published = as_of.isoformat()
            num = lambda k: float(r[k]) if (r.get(k) or "").strip() else None
            it = RawItem(sym, "news", r["headline"].strip(), "", (r.get("url") or "").strip(), published, "human-verified",
                         source_ref=(r.get("source_ref") or "").strip(), verified=(r.get("status") or "verified").strip(),
                         penalty_override=num("penalty_override"), purpose="governance", origin="verified", style="verified")
            lab = Label(r["event_type"].strip(), int(r.get("sentiment") or 0), (r.get("note") or "Human-verified evidence.").strip(), True,
                        subject=(r.get("subject") or "company").strip(), occurred_at_company=True, action_stage=(r.get("action_stage") or "n/a").strip(),
                        severity=(r.get("severity") or "n/a").strip(), amount_inr_cr=num("amount_inr_cr"),
                        people_direction=(r.get("people_direction") or "n/a").strip(), role_tier=(r.get("role_tier") or "n/a").strip(),
                        event_key=(r.get("event_key") or "").strip() or None)
            out.append(LabelledItem(it, lab, raw_response="human-verified", from_cache=False))
    return out, skipped
