"""GDELT on Google BigQuery: the full 12-month history for every company in ONE query.

Where the data lives: Google's public project `gdelt-bq`, dataset `gdeltv2`, table `gkg_partitioned` (Global Knowledge
Graph 2.x, one row per article). Columns used: DocumentIdentifier (URL), SourceCommonName (publisher), V2Organizations
(entities with character offsets: 'name,offset;name,offset'), Extras (contains <PAGE_TITLE> since Sept 2019 and
<PAGE_PRECISEPUBTIMESTAMP>), DATE. Partition filter `_PARTITIONTIME` keeps the scan (and cost) to the lookback window.

Accuracy: an article is kept for a company if its organisation list contains the company (exact name/alias, not a
substring of another firm) AND (the company is in the page title OR it is mentioned at least `min_mentions` times).
A market roundup that lists 30 stocks once therefore never qualifies. Mention counts become the relevance rank.

Cost safety: a dry run reports bytes scanned first; the job is hard-capped with maximum_bytes_billed."""
from __future__ import annotations
import hashlib
import html
import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from ..models import Holding, RawItem
from .select import aliases_of, dedupe_key

TABLE = "gdelt-bq.gdeltv2.gkg_partitioned"
PRICE_PER_TB_USD = 6.25          # on-demand list price at time of writing; check your own billing


class BigQueryTooExpensive(RuntimeError):
    pass


def _lit(s: str) -> str:
    """BigQuery single-quoted string literal (regex kept as data, not code)."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _re_escape(s: str) -> str:
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", s)


def _org_pattern(aliases: list[str]) -> str:
    names = sorted({_re_escape(a.lower()) for a in aliases}, key=len, reverse=True)
    return r"(?i)(?:^|;)(?:" + "|".join(names) + r"),\d+"


def _title_pattern(aliases: list[str]) -> str:
    return r"(?i)(?:" + "|".join(sorted({_re_escape(a) for a in aliases}, key=len, reverse=True)) + ")"


class GdeltBigQuery:
    def __init__(self, project: str | None = None, max_gb: float = 300.0, min_mentions: int = 2, client=None,
                 cache_dir: str | Path = ".cache/bq", table: str = TABLE):
        self.project, self.max_gb, self.min_mentions, self.client = project, max_gb, min_mentions, client
        self.cache_dir, self.table = Path(cache_dir), table
        self.by_symbol: dict[str, list[RawItem]] = {}
        self.on_event = None
        self.stats: dict = {}

    def _say(self, m: str) -> None:
        if self.on_event:
            self.on_event(m)

    # ---- SQL ---------------------------------------------------------------------------------------------
    def build_sql(self, holdings: list[Holding], start: date, end: date) -> str:
        structs, allpat = [], []
        for h in holdings:
            al = aliases_of(h)
            op = _org_pattern(al)
            structs.append(f"STRUCT({_lit(h.symbol)} AS symbol, {_lit(op)} AS org_pat, {_lit(_title_pattern(al))} AS title_pat)")
            allpat.append(op[len("(?i)"):])
        combined = "(?i)" + "|".join(allpat)
        return f"""
WITH cos AS (SELECT * FROM UNNEST([{", ".join(structs)}])),
hits AS (
  SELECT DocumentIdentifier AS url, SourceCommonName AS source, `DATE` AS gkg_date, V2Organizations AS orgs,
         REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title,
         REGEXP_EXTRACT(Extras, r'<PAGE_PRECISEPUBTIMESTAMP>(\\d{{14}})</PAGE_PRECISEPUBTIMESTAMP>') AS pub
  FROM `{self.table}`
  WHERE _PARTITIONTIME >= TIMESTAMP('{start.isoformat()}') AND _PARTITIONTIME < TIMESTAMP('{(end + timedelta(days=1)).isoformat()}')
    AND REGEXP_CONTAINS(V2Organizations, {_lit(combined)})
)
SELECT c.symbol AS symbol, h.url AS url, h.source AS source, h.title AS title,
       COALESCE(h.pub, CAST(h.gkg_date AS STRING)) AS published,
       ARRAY_LENGTH(REGEXP_EXTRACT_ALL(h.orgs, c.org_pat)) AS mentions,
       REGEXP_CONTAINS(IFNULL(h.title, ''), c.title_pat) AS in_title
FROM hits h JOIN cos c ON REGEXP_CONTAINS(h.orgs, c.org_pat)
WHERE h.title IS NOT NULL AND h.url IS NOT NULL
  AND (REGEXP_CONTAINS(IFNULL(h.title, ''), c.title_pat) OR ARRAY_LENGTH(REGEXP_EXTRACT_ALL(h.orgs, c.org_pat)) >= {int(self.min_mentions)})
""".strip()

    # ---- run ---------------------------------------------------------------------------------------------
    def _config(self, **kw):
        try:
            from google.cloud import bigquery
            return bigquery.QueryJobConfig(**kw)
        except ImportError:
            return SimpleNamespace(**kw)

    def _client(self):
        if self.client is None:
            try:
                from google.cloud import bigquery
            except ImportError as e:
                raise SystemExit("BigQuery route needs the client library:  pip install google-cloud-bigquery\n"
                                 "and a login:  gcloud auth application-default login   (see docs/GDELT.md)") from e
            self.client = bigquery.Client(project=self.project)
        return self.client

    def prefetch(self, holdings: list[Holding], start: date, end: date) -> None:
        key = hashlib.sha256((self.build_sql(holdings, start, end) + self.table).encode()).hexdigest()[:16]
        cp = self.cache_dir / f"gkg_{key}.json"
        if cp.exists():
            rows = json.loads(cp.read_text(encoding="utf-8"))
            self._say(f"[BigQuery] using cached result {cp} ({len(rows)} rows) - no new query, no cost")
        else:
            sql = self.build_sql(holdings, start, end)
            cl = self._client()
            dry = cl.query(sql, job_config=self._config(dry_run=True, use_query_cache=False))
            gb = (dry.total_bytes_processed or 0) / 1e9
            est = gb / 1000 * PRICE_PER_TB_USD
            self.stats = {"gb_scanned": round(gb, 1), "est_usd": round(est, 2)}
            self._say(f"[BigQuery] dry run: this query scans {gb:,.0f} GB (about ${est:,.2f} at on-demand list price)")
            if gb > self.max_gb:
                raise BigQueryTooExpensive(
                    f"The query would scan {gb:,.0f} GB, above the --bq-max-gb limit of {self.max_gb:,.0f} GB "
                    f"(about ${est:,.2f}). Nothing was run or billed. Shorten the window, or raise --bq-max-gb deliberately.")
            job = cl.query(sql, job_config=self._config(maximum_bytes_billed=int(self.max_gb * 1e9) + 1))
            rows = [{k: r[k] for k in ("symbol", "url", "source", "title", "published", "mentions", "in_title")} for r in job.result()]
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            self._say(f"[BigQuery] {len(rows)} article rows returned and cached at {cp}")
        self.by_symbol = {}
        seen: set[tuple] = set()
        for r in rows:
            pub = str(r["published"] or "")
            d = f"{pub[0:4]}-{pub[4:6]}-{pub[6:8]}" if len(pub) >= 8 else ""
            k = (r["symbol"], dedupe_key(r["url"]))
            if k in seen:
                continue
            seen.add(k)
            it = RawItem(r["symbol"], "news", html.unescape(r["title"] or ""), "", r["url"], d, r["source"] or "")
            it.relevance = float(r["mentions"] or 0) + (5.0 if r["in_title"] else 0.0)
            self.by_symbol.setdefault(r["symbol"], []).append(it)

    def fetch(self, h: Holding, start: date, end: date) -> list[RawItem]:
        return list(self.by_symbol.get(h.symbol, []))
