#!/usr/bin/env python3
"""Pair Trading Tools v2 -- scanner CLI.

    python scanner.py scan --universe crypto,tradfi,external,spread \
        --corr-threshold 0.90 --backtest-days 90 --lookback 60

Pipeline: fetch every configured symbol from its source -> align to a common daily
index -> screen all pairs for |correlation| >= threshold -> backtest every qualifying
pair AND every named spread pair (universe.py::SPREAD_PAIRS) with the v1 z-score model ->
print a summary -> write CSV/JSON reports.

The pipeline itself lives in pipeline.py (also used by the v1 Streamlit dashboard);
this file only handles CLI args, printing, and report files.

A symbol that fails to fetch (source down, ticker doesn't exist, not enough history) is
logged and skipped -- one bad symbol should never kill the whole scan.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from config import settings
from pipeline import scan_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scanner")


def run_scan(args: argparse.Namespace) -> dict:
    universe_names = [u.strip() for u in args.universe.split(",") if u.strip()]

    result = scan_pipeline(
        universe_names,
        corr_threshold=args.corr_threshold, corr_method=args.corr_method,
        lookback=args.lookback, backtest_days=args.backtest_days,
        min_backtest_days=args.min_backtest_days,
        entry_zscore=args.entry_zscore, exit_zscore=args.exit_zscore,
        stop_zscore=args.stop_zscore, max_holding_days=args.max_holding_days,
        candle_limit=args.candle_limit,
    )
    if not result.ok:
        logger.error("Scan failed: %s", result.reason)
        return {"ok": False, "reason": result.reason}

    aligned = result.aligned
    qualified = result.qualified
    trade_summary = result.trade_summary

    print("\n=== Correlation matrix (log-price levels) ===")
    print(result.corr_level.round(3).to_string())
    print(f"\n=== Pairs with |{args.corr_method}-correlation| >= {args.corr_threshold} ===")
    print(qualified.to_string(index=False) if len(qualified) else "(none)")
    print(f"\n=== Backtest: {args.backtest_days}d window, {args.lookback}d lookback, "
          f"entry/exit/stop z = {args.entry_zscore}/{args.exit_zscore}/{args.stop_zscore} ===")
    print(trade_summary.to_string(index=False) if len(trade_summary) else "(no trades)")

    os.makedirs(args.reports_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result.corr_level.to_csv(os.path.join(args.reports_dir, f"scan_{stamp}_correlation_level.csv"))
    result.corr_returns.to_csv(os.path.join(args.reports_dir, f"scan_{stamp}_correlation_returns.csv"))
    qualified.to_csv(os.path.join(args.reports_dir, f"scan_{stamp}_qualifying_pairs.csv"), index=False)
    trade_summary.to_csv(os.path.join(args.reports_dir, f"scan_{stamp}_backtest_summary.csv"), index=False)
    if result.trades:
        pd.DataFrame([t.__dict__ for t in result.trades]).to_csv(
            os.path.join(args.reports_dir, f"scan_{stamp}_trades.csv"), index=False)

    meta = {
        "run_at_utc": stamp,
        "universe": universe_names,
        "symbols_fetched": result.symbols_fetched,
        "symbols_requested_but_failed": result.symbols_failed,
        "aligned_window": {
            "start": str(pd.to_datetime(aligned.index.min(), unit="ms").date()),
            "end": str(pd.to_datetime(aligned.index.max(), unit="ms").date()),
            "days": len(aligned),
        },
        "corr_threshold": args.corr_threshold,
        "corr_method": args.corr_method,
        "qualifying_pairs": len(qualified),
        "spread_pairs_backtested": "spread" in universe_names,
        "total_trades": len(result.trades),
        "backtest_shortened_windows": result.shortened_windows,
        "backtest_skipped_short_history": result.skipped_short_history,
    }
    with open(os.path.join(args.reports_dir, f"scan_{stamp}_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    logger.info("Reports written to %s/scan_%s_*", args.reports_dir, stamp)

    return {"ok": True, **meta}


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Fetch, screen, backtest, and report.")
    scan.add_argument("--universe", default="crypto,tradfi,external,spread",
                      help="Comma-separated: crypto,tradfi,external,spread (default: all)")
    scan.add_argument("--corr-threshold", type=float, default=settings.corr_threshold)
    scan.add_argument("--corr-method", choices=["level", "returns"], default=settings.corr_method)
    scan.add_argument("--lookback", type=int, default=settings.lookback_days,
                      help="Rolling window (days) for hedge-ratio / z-score fit")
    scan.add_argument("--backtest-days", type=int, default=settings.backtest_days,
                      help="How far back to simulate entries (default 90 = ~3 months)")
    scan.add_argument("--min-backtest-days", type=int, default=settings.min_backtest_days,
                      help="Floor for the shortened backtest window used when a pair's "
                           "overlapping history is thinner than lookback + backtest-days; "
                           "pairs below the floor are skipped")
    scan.add_argument("--entry-zscore", type=float, default=settings.entry_zscore)
    scan.add_argument("--exit-zscore", type=float, default=settings.exit_zscore)
    scan.add_argument("--stop-zscore", type=float, default=settings.stop_zscore)
    scan.add_argument("--max-holding-days", type=int, default=settings.max_holding_days)
    scan.add_argument("--candle-limit", type=int, default=settings.candle_limit)
    scan.add_argument("--reports-dir", default=settings.reports_dir)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        result = run_scan(args)
        return 0 if result.get("ok") else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
