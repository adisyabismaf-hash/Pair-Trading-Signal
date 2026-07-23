"""Z-score / OLS-hedge-ratio pair backtest.

This is a faithful port of v1's live logic (`1/cloud/core/pair_trading.py`), just
restructured to run as a vectorized-ish historical simulation instead of a single
"what's the signal right now" call. Same model:

    spread_t = log(A_t) - h * log(B_t)          h fit by OLS over a rolling lookback window
    z_t      = (spread_t - mean(spread_window)) / std(spread_window)

    entry:  |z| >= entry_zscore  and  |z| < stop_zscore
        z > 0  -> SHORT_SPREAD (short A, long h*B) -- spread expected to fall
        z < 0  -> LONG_SPREAD  (long A, short h*B) -- spread expected to rise
    exit:   |z| <= exit_zscore                      (mean reversion achieved)
    stop:   |z| >= stop_zscore                       (relationship broke down)
    expire: held >= max_holding_days                 (time stop)

    pnl_pct = side * (log(A_now/A_entry) - h*log(B_now/B_entry)) * 100
        side = +1 for LONG_SPREAD, -1 for SHORT_SPREAD

Validated against a real 90-day / 60-day-lookback run on 10 crypto majors during
development (2026-07-23) — see reports/ for that run's output.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PairTrade:
    pair: str
    direction: str          # LONG_SPREAD | SHORT_SPREAD
    entry_zscore: float
    entry_date: str
    exit_reason: str        # EXIT | STOP | EXPIRED | OPEN
    hold_days: int
    pnl_pct: float


def backtest_pair(prices: pd.DataFrame, symbol_a: str, symbol_b: str, *,
                  lookback_days: int, backtest_days: int,
                  entry_zscore: float, exit_zscore: float, stop_zscore: float,
                  max_holding_days: int) -> list[PairTrade]:
    """prices: DataFrame indexed by ms-epoch timestamp (ascending), one column per symbol.

    Simulates from `len(prices) - backtest_days - lookback_days` to the end. Requires
    `len(prices) >= lookback_days + backtest_days`; raises ValueError otherwise so a
    thin-history symbol fails loudly instead of silently running a shorter backtest.
    """
    n = len(prices)
    needed = lookback_days + backtest_days
    if n < needed:
        raise ValueError(f"{symbol_a}/{symbol_b}: need {needed} aligned days "
                         f"(lookback {lookback_days} + backtest {backtest_days}), have {n}")

    window_start = n - needed
    la = np.log(prices[symbol_a].values)
    lb = np.log(prices[symbol_b].values)
    dates = pd.to_datetime(prices.index, unit="ms")

    trades: list[PairTrade] = []
    position: dict | None = None
    pair_label = f"{symbol_a}/{symbol_b}"

    for i in range(window_start + lookback_days, n):
        wa = la[i - lookback_days:i]
        wb = lb[i - lookback_days:i]
        h, _intercept = np.polyfit(wb, wa, 1)
        spread_window = wa - h * wb
        mu, sigma = spread_window.mean(), spread_window.std(ddof=1)
        if sigma <= 1e-12:
            continue
        z = (la[i] - h * lb[i] - mu) / sigma

        if position is None:
            if abs(z) >= entry_zscore and abs(z) < stop_zscore:
                direction = "SHORT_SPREAD" if z > 0 else "LONG_SPREAD"
                position = dict(direction=direction, entry_i=i, h=h,
                                entry_a=prices[symbol_a].values[i],
                                entry_b=prices[symbol_b].values[i],
                                entry_z=z, entry_date=str(dates[i].date()))
            continue

        hold_days = i - position["entry_i"]
        reason = None
        if position["direction"] == "SHORT_SPREAD" and z <= exit_zscore:
            reason = "EXIT"
        elif position["direction"] == "LONG_SPREAD" and z >= -exit_zscore:
            reason = "EXIT"
        elif position["direction"] == "SHORT_SPREAD" and z >= stop_zscore:
            reason = "STOP"
        elif position["direction"] == "LONG_SPREAD" and z <= -stop_zscore:
            reason = "STOP"
        elif hold_days >= max_holding_days:
            reason = "EXPIRED"

        if reason:
            trades.append(_close_trade(pair_label, position, i, prices, symbol_a, symbol_b, reason))
            position = None

    if position is not None:
        i = n - 1
        trades.append(_close_trade(pair_label, position, i, prices, symbol_a, symbol_b, "OPEN"))

    return trades


def _close_trade(pair_label, position, i, prices, symbol_a, symbol_b, reason) -> PairTrade:
    side = 1.0 if position["direction"] == "LONG_SPREAD" else -1.0
    price_a_now = prices[symbol_a].values[i]
    price_b_now = prices[symbol_b].values[i]
    move = (np.log(price_a_now / position["entry_a"])
            - position["h"] * np.log(price_b_now / position["entry_b"]))
    pnl_pct = side * move * 100
    return PairTrade(
        pair=pair_label, direction=position["direction"],
        entry_zscore=round(float(position["entry_z"]), 3),
        entry_date=position["entry_date"], exit_reason=reason,
        hold_days=i - position["entry_i"], pnl_pct=round(float(pnl_pct), 3),
    )


def summarize_trades(trades: list[PairTrade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=["pair", "trades", "win_rate_pct", "avg_pnl_pct",
                                     "total_pnl_pct", "avg_hold_days"])
    df = pd.DataFrame([t.__dict__ for t in trades])
    summ = df.groupby("pair").agg(
        trades=("pnl_pct", "count"),
        win_rate_pct=("pnl_pct", lambda s: round((s > 0).mean() * 100, 1)),
        avg_pnl_pct=("pnl_pct", lambda s: round(s.mean(), 2)),
        total_pnl_pct=("pnl_pct", lambda s: round(s.sum(), 2)),
        avg_hold_days=("hold_days", lambda s: round(s.mean(), 1)),
    ).sort_values("total_pnl_pct", ascending=False)
    return summ.reset_index()
