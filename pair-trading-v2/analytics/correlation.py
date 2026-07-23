"""Correlation screening.

Deliberately computes BOTH:
  - level correlation: corr(log(price_A), log(price_B))  -- what v1 (core/pair_trading.py)
    uses for its stored `last_correlation` field.
  - returns correlation: corr(diff(log(price_A)), diff(log(price_B)))

Two trending series (e.g. two large-caps in a bull leg, or gold grinding up) will show
high LEVEL correlation even when their day-to-day moves are weakly related — that's not a
tradeable statistical-arbitrage edge, it's just "both went up". Returns correlation is the
stricter, more honest measure of whether the two assets actually move together day to day.
Report both; don't silently pick one.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd


# A pair needs at least this many overlapping observations before its correlation is
# trusted; below this the entry stays NaN and the pair can't qualify for the watchlist.
MIN_OVERLAP_DAYS = 30


def correlation_report(aligned_log_prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """aligned_log_prices: DataFrame indexed by timestamp, one column per symbol. Leading
    NaNs (before a symbol listed) are fine — correlations are computed pairwise over each
    pair's own overlap, requiring MIN_OVERLAP_DAYS observations."""
    level_corr = aligned_log_prices.corr(min_periods=MIN_OVERLAP_DAYS)
    returns_corr = aligned_log_prices.diff().corr(min_periods=MIN_OVERLAP_DAYS)
    return {"level": level_corr, "returns": returns_corr}


def qualifying_pairs(aligned_log_prices: pd.DataFrame, threshold: float,
                     method: str = "level") -> pd.DataFrame:
    """Every symbol pair whose |correlation| (by `method`) is >= threshold, sorted desc."""
    report = correlation_report(aligned_log_prices)
    level = report["level"]
    returns = report["returns"]
    primary = level if method == "level" else returns

    rows = []
    for a, b in itertools.combinations(aligned_log_prices.columns, 2):
        c_primary = primary.loc[a, b]
        if abs(c_primary) >= threshold:
            rows.append({
                "symbol_a": a, "symbol_b": b,
                "corr_level": round(float(level.loc[a, b]), 4),
                "corr_returns": round(float(returns.loc[a, b]), 4),
            })
    df = pd.DataFrame(rows)
    if len(df):
        sort_col = "corr_level" if method == "level" else "corr_returns"
        df = df.reindex(df[sort_col].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return df
