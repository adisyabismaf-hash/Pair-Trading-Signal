"""Market technicals for the daily outlook: Stochastic oscillator + market-structure
trend across 4H / 1D / 1W, computed from Extended candles.

Pure functions on candle lists (dicts with o/h/l/c/t_ms) so they can be unit-tested
without network. `analyze_asset` ties fetch + compute together.

Market-structure trend rule (per user spec):
  - Detect swing highs/lows (fractal pivots).
  - UPTREND  = the last two swings make higher highs AND higher lows.
  - DOWNTREND = the last two swings make lower highs AND lower lows.
  - Otherwise SIDEWAYS (structure unclear / consolidating).
Weekly candles: Extended's P1W endpoint returns empty, so 1W bars are aggregated from
daily candles (7-day buckets).
"""
from __future__ import annotations

import logging
from typing import Any

from app.fetchers.extended import fetch_candles

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000


# ----------------------------------------------------------------- Stochastic

def stochastic(candles: list[dict], k_period: int = 14, d_period: int = 3,
               smooth_k: int = 3) -> tuple[float | None, float | None]:
    """Slow Stochastic (%K, %D) at the latest candle. Returns (None, None) if there
    isn't enough history."""
    if len(candles) < k_period + smooth_k + d_period:
        return None, None
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]
    closes = [c["c"] for c in candles]

    raw_k: list[float] = []
    for i in range(k_period - 1, len(candles)):
        hh = max(highs[i - k_period + 1:i + 1])
        ll = min(lows[i - k_period + 1:i + 1])
        rng = hh - ll
        raw_k.append(50.0 if rng <= 0 else (closes[i] - ll) / rng * 100.0)

    def _sma(series: list[float], n: int) -> list[float]:
        return [sum(series[i - n + 1:i + 1]) / n for i in range(n - 1, len(series))]

    slow_k = _sma(raw_k, smooth_k)          # smoothed %K
    slow_d = _sma(slow_k, d_period)         # %D = SMA of %K
    if not slow_k or not slow_d:
        return None, None
    return round(slow_k[-1], 1), round(slow_d[-1], 1)


def stoch_label(k: float | None) -> str:
    if k is None:
        return "—"
    if k >= 80:
        return "overbought"
    if k <= 20:
        return "oversold"
    return "netral"


# ----------------------------------------------------------------- Weekly bars

def aggregate_weekly(daily: list[dict]) -> list[dict]:
    """Group daily candles into 7-day OHLC buckets (oldest->newest)."""
    if not daily:
        return []
    weekly: list[dict] = []
    for i in range(0, len(daily), 7):
        chunk = daily[i:i + 7]
        if not chunk:
            continue
        weekly.append({
            "t_ms": chunk[0]["t_ms"],
            "o": chunk[0]["o"],
            "h": max(c["h"] for c in chunk),
            "l": min(c["l"] for c in chunk),
            "c": chunk[-1]["c"],
            "v": sum(c.get("v", 0) for c in chunk),
        })
    return weekly


# ------------------------------------------------------- Market-structure trend

def _swings(candles: list[dict], left: int = 2, right: int = 2
            ) -> list[tuple[int, str, float]]:
    """Fractal swing points: a swing high is a candle whose high is >= the `left`/`right`
    neighbours; swing low the mirror. Returns [(index, 'H'|'L', price), …] in order."""
    pts = []
    for i in range(left, len(candles) - right):
        h = candles[i]["h"]
        l = candles[i]["l"]
        window = candles[i - left:i + right + 1]
        if h >= max(c["h"] for c in window):
            pts.append((i, "H", h))
        elif l <= min(c["l"] for c in window):
            pts.append((i, "L", l))
    return pts


def market_structure_trend(candles: list[dict]) -> str:
    """UP / DOWN / SIDEWAYS from the last two swing highs and two swing lows."""
    sw = _swings(candles)
    highs = [p[2] for p in sw if p[1] == "H"]
    lows = [p[2] for p in sw if p[1] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return "SIDEWAYS"
    higher_high = highs[-1] > highs[-2]
    higher_low = lows[-1] > lows[-2]
    lower_high = highs[-1] < highs[-2]
    lower_low = lows[-1] < lows[-2]
    if higher_high and higher_low:
        return "UP"
    if lower_high and lower_low:
        return "DOWN"
    return "SIDEWAYS"


TREND_ARROW = {"UP": "▲", "DOWN": "▼", "SIDEWAYS": "→"}


# ----------------------------------------------------------------- Per-asset

def analyze_asset(market: str) -> dict[str, Any]:
    """Fetch candles and compute Stochastic (on 1D) + trend on 4H/1D/1W for one Extended
    market (e.g. 'BTC-USD'). Network failures degrade to None rather than raising."""
    result: dict[str, Any] = {"market": market, "stoch_k": None, "stoch_d": None,
                              "trend_4h": "—", "trend_1d": "—", "trend_1w": "—"}
    try:
        d4 = fetch_candles(market, "PT4H", 200)
        daily = fetch_candles(market, "P1D", 400)
    except Exception as exc:
        logger.warning("technicals fetch failed for %s: %s", market, exc)
        return result

    weekly = aggregate_weekly(daily)
    k, d = stochastic(daily)
    result.update({
        "stoch_k": k, "stoch_d": d, "stoch_label": stoch_label(k),
        "trend_4h": market_structure_trend(d4) if d4 else "—",
        "trend_1d": market_structure_trend(daily) if daily else "—",
        "trend_1w": market_structure_trend(weekly) if weekly else "—",
    })
    return result
