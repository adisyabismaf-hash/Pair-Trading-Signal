"""Traditional strategy: daily MA50 double-retest.

Setup (long side; short side is the mirror image):
1. Price crosses above the daily MA50 and the MA is rising -> uptrend established.
2. Price pulls back and *touches* the MA50 (candle low within tolerance of the MA)
   but closes back above it. Each distinct touch episode counts as one retest.
3. On the SECOND retest, when the latest candle confirms the bounce
   (closes above the MA50 and above its open), a LONG signal fires.
   Entry = last close, SL = below the retest low, TP = entry + RR * risk.
"""
from core.config import settings


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < window:
        return out
    acc = sum(values[:window])
    out[window - 1] = acc / window
    for i in range(window, len(values)):
        acc += values[i] - values[i - window]
        out[i] = acc / window
    return out


def _touch_groups(candles: list[dict], ma: list[float | None], start: int, end: int,
                  side: str, tol: float) -> list[dict]:
    """Group consecutive MA-touch bars into retest episodes within [start, end]."""
    groups: list[dict] = []
    current: dict | None = None
    for i in range(start, end + 1):
        m = ma[i]
        if m is None:
            continue
        c = candles[i]
        if side == "long":
            touched = c["l"] <= m * (1 + tol) and c["c"] > m
        else:
            touched = c["h"] >= m * (1 - tol) and c["c"] < m
        if touched:
            if current is None:
                current = {"start": i, "end": i,
                           "extreme": c["l"] if side == "long" else c["h"]}
            else:
                current["end"] = i
                if side == "long":
                    current["extreme"] = min(current["extreme"], c["l"])
                else:
                    current["extreme"] = max(current["extreme"], c["h"])
        else:
            if current is not None:
                groups.append(current)
                current = None
    if current is not None:
        groups.append(current)
    return groups


def analyze(candles: list[dict]) -> dict:
    """Analyze daily candles. Returns
    {"state": {...}, "signal": {...} | None} where signal is a ready-to-trade setup."""
    period = settings.ma_period
    tol = settings.retest_tolerance_pct / 100.0
    n = len(candles)
    state: dict = {"price": None, "ma50": None, "trend": "FLAT", "retest_count": 0}
    if n < period + 10:
        return {"state": state, "signal": None}

    closes = [c["c"] for c in candles]
    ma = sma(closes, period)
    last = n - 1
    ma_now = ma[last]
    if ma_now is None:
        return {"state": state, "signal": None}

    price = closes[last]
    ma_prev = ma[last - 5] if ma[last - 5] is not None else ma_now
    if price > ma_now and ma_now >= ma_prev:
        trend, side = "UP", "long"
    elif price < ma_now and ma_now <= ma_prev:
        trend, side = "DOWN", "short"
    else:
        state.update(price=price, ma50=ma_now, trend="FLAT")
        return {"state": state, "signal": None}

    # Find the most recent crossover that established the current trend.
    cross_idx = None
    for i in range(last, max(period, last - settings.retest_window) - 1, -1):
        if ma[i] is None or ma[i - 1] is None:
            break
        above_now = closes[i] > ma[i]
        above_prev = closes[i - 1] > ma[i - 1]
        if side == "long" and above_now and not above_prev:
            cross_idx = i
            break
        if side == "short" and not above_now and above_prev:
            cross_idx = i
            break

    search_start = cross_idx + 1 if cross_idx is not None else max(period, last - settings.retest_window)
    if search_start > last:
        state.update(price=price, ma50=ma_now, trend=trend, retest_count=0)
        return {"state": state, "signal": None}

    groups = _touch_groups(candles, ma, search_start, last, side, tol)
    retest_count = len(groups)
    state.update(price=price, ma50=ma_now, trend=trend, retest_count=retest_count)

    if retest_count < 2:
        return {"state": state, "signal": None}

    # The 2nd (or later) retest must be happening now: the last candle belongs to the
    # latest touch group, or is the immediate bounce bar right after it.
    latest = groups[-1]
    if last - latest["end"] > 1:
        return {"state": state, "signal": None}

    last_candle = candles[last]
    if side == "long":
        confirmed = last_candle["c"] > ma_now and last_candle["c"] >= last_candle["o"]
    else:
        confirmed = last_candle["c"] < ma_now and last_candle["c"] <= last_candle["o"]
    if not confirmed:
        return {"state": state, "signal": None}

    entry = price
    if side == "long":
        stop = min(latest["extreme"], ma_now) * (1 - 0.005)
        risk = entry - stop
        if risk <= 0:
            return {"state": state, "signal": None}
        target = entry + settings.risk_reward * risk
        direction = "LONG"
    else:
        stop = max(latest["extreme"], ma_now) * (1 + 0.005)
        risk = stop - entry
        if risk <= 0:
            return {"state": state, "signal": None}
        target = entry - settings.risk_reward * risk
        direction = "SHORT"

    return {
        "state": state,
        "signal": {
            "direction": direction,
            "entry": round(entry, 8),
            "stop_loss": round(stop, 8),
            "take_profit": round(target, 8),
            "ma50": round(ma_now, 8),
            "retest_count": retest_count,
            "reason": (
                f"{'Uptrend' if side == 'long' else 'Downtrend'}: harga {'di atas' if side == 'long' else 'di bawah'} "
                f"MA50 daily, retest MA50 ke-{retest_count} terkonfirmasi dengan candle "
                f"{'bullish' if side == 'long' else 'bearish'} (close {'di atas' if side == 'long' else 'di bawah'} MA50)."
            ),
        },
    }


def check_exit(signal_direction: str, entry: float, stop: float, target: float,
               candles_since_entry: list[dict]) -> dict | None:
    """Check whether SL or TP was hit by any candle after entry.
    Conservative: if a candle spans both SL and TP, assume SL hit first."""
    for c in candles_since_entry:
        if signal_direction == "LONG":
            if c["l"] <= stop:
                return {"status": "CLOSED_SL", "exit_price": stop,
                        "pnl_pct": (stop - entry) / entry * 100}
            if c["h"] >= target:
                return {"status": "CLOSED_TP", "exit_price": target,
                        "pnl_pct": (target - entry) / entry * 100}
        else:  # SHORT
            if c["h"] >= stop:
                return {"status": "CLOSED_SL", "exit_price": stop,
                        "pnl_pct": (entry - stop) / entry * 100}
            if c["l"] <= target:
                return {"status": "CLOSED_TP", "exit_price": target,
                        "pnl_pct": (entry - target) / entry * 100}
    return None

