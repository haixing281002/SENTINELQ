---
name: sentinelq-step1-input
description: Step 1 of the Sentinel Q pipeline - read and resolve the stock list (symbol, company, sector). Use when the user sends stock names or a CD_NSE/ISIN table, or asks what the pipeline read for Step 1.
---

# Step 1 - Input
**Document says:** a spreadsheet with three columns - symbol, company name, sector. Nothing else is required.

**Code:** `sentinelq/pipeline.py::load_portfolio` (CSV or the pasted `CD_NSE Symbol | Accord Code | ISIN | # | Company Name` table), `sentinelq/resolve.py`
(strips `NSE:` and Ltd/Limited, fills sector + cap from `portfolio/universe.csv`, else asks Claude and caches it), aliases for news search come from the
`aliases` column of `portfolio/universe.csv`.

**Run / inspect (nothing is fetched):**
`python -m sentinelq inspect input --portfolio portfolio/stocks_given.tsv`  or  `--pick "Angel One, Titan Company"`

**Manually verify:** every stock appears once; sector is right (it drives sector sentiment); aliases are specific (e.g. Titan -> "Titan Company", never bare "Titan").
Unknown stock? Add a row `symbol,name,sector,cap,weight,aliases` to `portfolio/universe.csv`; confirm ticker/sector with the user if unsure.
Weights and cap are display-only and never affect a score.
