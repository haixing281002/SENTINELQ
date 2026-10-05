# NSE India MCP servers

`.mcp.json` at the repo root registers two remote MCP servers published by NSE India. Claude Code loads
project-scoped `.mcp.json` servers automatically when opened in this repo (it asks once to approve them).

| Name | URL | Transport | What it covers |
|---|---|---|---|
| `nse-bhavcopy` | `https://mcp.nseindia.in/bhavcopy/cm/mcp` | streamable HTTP | Cash-market (CM) bhavcopy: the exchange's official end-of-day file per trading day (symbol, series, OHLC, close, volume, turnover, delivery data). |
| `cm-market` | `https://mcp.nseindia.in/cmmkt/mcp` | streamable HTTP | Capital-market data from the exchange itself (quotes, indices, market status and related reference data). |

Claude Code's `.mcp.json` uses `"type": "http"` for streamable-HTTP servers; the equivalent for other clients is
`"transport": "streamable-http"` with the same URLs.

## How they fit the pipeline

The *Sentinel Q Architecture* rules are unchanged: **prices never feed a score**, and corporate actions are listed in the
PDF (and scored only in the xlsx). The NSE servers are a source of record for data the pipeline currently takes from
Yahoo Finance (`sentinelq/ingest/yf.py`), so they are useful in three places:

1. **Step 1 (input)** - confirm that a symbol is a live NSE cash-market symbol (series `EQ`) before it enters the run,
   instead of guessing the `.NS` Yahoo form.
2. **Step 2 (ingest, prices context)** - official closes from the bhavcopy for the 6-month price-context panel and the
   `Price Context` sheet. Exchange data is preferable to a third-party feed when both are available, and it is free.
3. **Step 2 (ingest, actions)** - cross-check dividend/split/bonus records (the exchange is the primary source; Yahoo is derived).

Everything the skills already say still applies: every item carries a dated source URL, nothing is stored outside
`runs/<date>/`, and free sources only.

## Status

The CLI does not yet have an `--prices nse` / `--actions nse` provider; `yf.py` remains the code path. The servers are
registered so that the Claude Code skills (`sentinelq-step1-input`, `sentinelq-step2-ingest`) can call them
interactively for verification, and so that a provider can be written against a known endpoint.

The tool list was not captured in this document because the environment that added the config could not reach
`mcp.nseindia.in` (network policy). Run `/mcp` in Claude Code inside this repo to see the tools each server exposes.
