# Pair Trading Tools v2

Upgrade of the v1 pair-trading scanner (`1/cloud/core/pair_trading.py`). v1 only screened
markets available on Extended Exchange (crypto perps + a handful of synthetic tradfi
indices). v2 adds a **multi-source data layer** so the universe can include real gold/oil
markets and cross-venue "same asset, different wrapper" spreads that v1 could not see.

## What's new vs v1

1. **Multi-source universe** — `data_sources/` has one adapter per venue. v1 hardcoded
   Extended Exchange. v2 adds Yahoo Finance (futures/FX/spot: Brent `BZ=F`, WTI `CL=F`,
   spot gold `GC=F`, silver `SI=F`) and Binance public REST (PAXG, XAUT, and any other
   crypto not listed on Extended). Adding a new venue = one new class in `data_sources/`.
2. **Automatic correlation screening** — v1 required a human to manually add a `WatchedPair`
   row per pair. v2 pulls the whole configured universe, builds the full pairwise
   correlation matrix (on log-price levels *and* on log-returns — see caveat below), and
   surfaces every pair at or above `--corr-threshold` (default 0.90) as a watchlist
   candidate.
3. **Named spread pairs** — some pairs matter regardless of measured correlation because
   they are economically the *same* asset traded on different venues/wrappers (Brent vs
   WTI, PAXG vs XAUT, XAU vs XAUT, XAU vs PAXG). These are defined explicitly in
   `universe.py::SPREAD_PAIRS` and always get backtested even if the trailing-window
   correlation dips under the threshold.
4. **Backtest engine reused, not reinvented** — `analytics/backtest.py` is a faithful port
   of v1's z-score / OLS-hedge-ratio logic (`spread = log(A) - h*log(B)`, entry at
   `|z| >= entry_z`, exit at `|z| <= exit_z`, stop at `|z| >= stop_z`, time-stop at
   `max_holding_days`), so a signal that would have fired in v1 fires identically here.
   It's just decoupled from the FastAPI/Postgres backend so it can run as a plain script.
5. **Reports, not just live signals** — `scanner.py` writes a CSV/JSON report per run
   (`reports/scan_<timestamp>.*`) with the correlation matrix, qualifying pairs, and
   full backtest trade log, so results are diffable across runs instead of only living in
   a live-signals table.

## Validation status (updated 2026-07-24, first run outside the sandbox)

The Cowork sandbox limitations were re-checked with full internet access:

- **Yahoo Finance adapter: validated live.** `BZ=F` (Brent) returns clean daily candles
  (504 days pulled, current through the run date).
- **Binance adapter: validated live via the `data-api.binance.vision` mirror.** The main
  `api.binance.com` host times out from this network (likely geo-blocked), so the default
  `binance_base_url` now points at Binance's official public market-data mirror
  (override with `BINANCE_BASE_URL` if needed). `PAXGUSDT` has 600+ days of history;
  `XAUTUSDT` only listed on Binance around 2026-03-26 (~120 days) — that is a hard data
  limit, not a sandbox artifact.
- **Cross-venue alignment fixed.** Venues stamp daily candles at different times
  (Extended 00:00 UTC, Binance 23:59:59.999 close-time, Yahoo the exchange's open);
  `align_closes` now floors every timestamp to its UTC day before joining.
- **Per-pair windows.** Alignment is an outer join: a late-listed symbol (XAUT, WTI,
  USDJPY) no longer truncates the whole universe. Correlations are computed pairwise over
  each pair's own overlap (min 30 observations), and each pair is backtested on its own
  overlap — if that is thinner than `lookback + backtest-days`, the backtest window is
  shortened (floor: `--min-backtest-days`, default 30) with the lookback kept intact,
  and the shortening is logged and recorded in the run's `meta.json`. Note this means
  correlation figures in one report are not all measured over identical windows.
- PAXG-USD on **Extended** remains unusable (1 candle of history) — PAXG is sourced from
  Binance, as configured in `universe.py`.

## Quickstart

**Via the v1 dashboard (recommended)** — the Streamlit app in `../cloud/` has a
"🔎 Scanner v2" page that runs this pipeline with a form UI, shows results in tabs, and
can push qualifying Extended-only pairs straight into the v1 live watchlist:

```bash
cd "../cloud" && venv/Scripts/streamlit run streamlit_app.py
```

The page imports this folder via `pipeline.py` (`scan_pipeline()`); it locates it as a
sibling of `cloud/`, overridable with the `V2_DIR` env var. To use it on Streamlit
Community Cloud, copy this folder into the deployed repo and set `V2_DIR` accordingly.

**Via CLI:**

```bash
pip install -r requirements.txt
python scanner.py scan --universe crypto,tradfi,external,spread --corr-threshold 0.90 \
    --backtest-days 90 --lookback 60
```

CLI outputs land in `reports/`. See `python scanner.py scan --help` for all flags.
`scanner.py` is a thin CLI wrapper; the shared pipeline lives in `pipeline.py`.

## Caveat baked into the analytics on purpose

Correlation on raw log-price **levels** is what v1 used for its `last_correlation` field,
and is what's shown first in the report for continuity — but two assets that are both just
trending up over the window (e.g. most of crypto in a bull leg, or gold in 2026) will show
high level-correlation even if their day-to-day moves are only loosely related. The report
always prints **both** the level-correlation and the returns-correlation side by side so
you can sanity-check before adding a pair to the watchlist — a pair with high level-corr
but low returns-corr is not a real trading edge, it's two things going up at the same time.
