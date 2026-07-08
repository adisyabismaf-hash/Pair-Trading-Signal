"""Statistical-arbitrage pair trading on daily closes.

Model: spread = log(A) - h * log(B), with hedge ratio h estimated by OLS over a
rolling lookback window. The z-score of the current spread against the same
window decides entries and exits:

  z >= +entry  -> SHORT_SPREAD (short A, long h*B): spread expected to fall
  z <= -entry  -> LONG_SPREAD  (long A, short h*B): spread expected to rise
  |z| <= exit  -> mean reversion done, close with profit
  |z| >= stop  -> relationship broke down, stop out

P&L is measured on the base-leg notional:
  pnl% = side * (log(A_now/A_entry) - h * log(B_now/B_entry)) * 100
where side = +1 for LONG_SPREAD and -1 for SHORT_SPREAD.
"""
import math

import numpy as np

from core.config import settings


def _aligned_log_closes(candles_a: list[dict], candles_b: list[dict]):
    """Intersect the two candle series on timestamp, return (ts, logA, logB, closeA, closeB)."""
    by_t_b = {c["t"]: c["c"] for c in candles_b}
    ts, la, lb, ca, cb = [], [], [], [], []
    for c in candles_a:
        pb = by_t_b.get(c["t"])
        if pb is None or pb <= 0 or c["c"] <= 0:
            continue
        ts.append(c["t"])
        ca.append(c["c"])
        cb.append(pb)
        la.append(math.log(c["c"]))
        lb.append(math.log(pb))
    return ts, np.array(la), np.array(lb), ca, cb


def analyze(candles_a: list[dict], candles_b: list[dict],
            lookback: int | None = None) -> dict | None:
    """Compute the current pair statistics. Returns None if not enough data."""
    lookback = lookback or settings.pair_lookback
    ts, la, lb, ca, cb = _aligned_log_closes(candles_a, candles_b)
    if len(ts) < lookback + 5:
        return None

    wa = la[-lookback:]
    wb = lb[-lookback:]

    # OLS hedge ratio: la = h * lb + const
    h, intercept = np.polyfit(wb, wa, 1)
    spread_window = wa - h * wb
    mu = float(np.mean(spread_window))
    sigma = float(np.std(spread_window, ddof=1))
    if sigma <= 1e-12:
        return None

    spread_now = float(la[-1] - h * lb[-1])
    zscore = (spread_now - mu) / sigma
    corr = float(np.corrcoef(wa, wb)[0, 1])

    return {
        "zscore": round(zscore, 4),
        "hedge_ratio": round(float(h), 6),
        "correlation": round(corr, 4),
        "spread": spread_now,
        "spread_mean": mu,
        "spread_std": sigma,
        "price_a": ca[-1],
        "price_b": cb[-1],
        "last_ts": ts[-1],
        "n_obs": len(ts),
    }


def entry_signal(stats: dict, entry_z: float, exit_z: float) -> dict | None:
    """Decide whether the current z-score is an actionable entry."""
    z = stats["zscore"]
    if abs(z) < entry_z or abs(z) >= settings.pair_stop_zscore:
        return None
    if z > 0:
        direction = "SHORT_SPREAD"
        action = "SHORT base / LONG quote"
    else:
        direction = "LONG_SPREAD"
        action = "LONG base / SHORT quote"
    return {
        "direction": direction,
        "entry_zscore": z,
        "hedge_ratio": stats["hedge_ratio"],
        "spread": stats["spread"],
        "price_a": stats["price_a"],
        "price_b": stats["price_b"],
        "reason": (
            f"Z-score spread = {z:+.2f} (ambang Â±{entry_z:.1f}). Spread melebar jauh dari rata-rata, "
            f"ekspektasi mean reversion. Aksi: {action} dengan hedge ratio {stats['hedge_ratio']:.3f}. "
            f"Target exit |z| â‰¤ {exit_z:.1f}, stop |z| â‰¥ {settings.pair_stop_zscore:.1f}."
        ),
    }


def open_pnl_pct(direction: str, hedge_ratio: float,
                 entry_price_a: float, entry_price_b: float,
                 price_a: float, price_b: float) -> float:
    side = 1.0 if direction == "LONG_SPREAD" else -1.0
    move = math.log(price_a / entry_price_a) - hedge_ratio * math.log(price_b / entry_price_b)
    return side * move * 100


def check_exit(direction: str, zscore_now: float, exit_z: float,
               holding_days: float) -> str | None:
    """Returns CLOSED_EXIT / CLOSED_SL / EXPIRED or None to keep holding."""
    # Profitable exit: spread reverted to the mean.
    if direction == "SHORT_SPREAD" and zscore_now <= exit_z:
        return "CLOSED_EXIT"
    if direction == "LONG_SPREAD" and zscore_now >= -exit_z:
        return "CLOSED_EXIT"
    # Stop: divergence kept widening.
    if direction == "SHORT_SPREAD" and zscore_now >= settings.pair_stop_zscore:
        return "CLOSED_SL"
    if direction == "LONG_SPREAD" and zscore_now <= -settings.pair_stop_zscore:
        return "CLOSED_SL"
    if holding_days >= settings.pair_max_holding_days:
        return "EXPIRED"
    return None

