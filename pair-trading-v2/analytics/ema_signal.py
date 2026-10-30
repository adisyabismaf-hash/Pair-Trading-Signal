"""EMA-185 break signal on the 4H timeframe.

Rule (per user spec):
  LONG  — price breaks above EMA185, then holds with `confirm_long` consecutive closes
          above EMA185 (the break candle counts as close #1). Signal fires on the candle
          that completes the run (default 3rd close above).
  SHORT — price breaks below EMA185, then `confirm_short` consecutive closes below
          (default 1, i.e. the breakdown candle itself). Signal fires on that candle.

Short confirms faster than long on purpose. All logic here is pure (operates on a list of
closes) so it can be unit-tested without the network; `analyze_symbol` in ema_scanner.py
wires in the live 4H candles.
"""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first `period` values.
    Returns a list aligned to `values`; entries before the seed are None."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _trailing_run(flags: list[bool]) -> int:
    """How many True values are at the tail of `flags` (0 if the last is False)."""
    n = 0
    for f in reversed(flags):
        if f:
            n += 1
        else:
            break
    return n


def ema_break_signal(closes: list[float], *, period: int = 185,
                     confirm_long: int = 3, confirm_short: int = 1) -> dict:
    """Current EMA185 break state for one series of closes (oldest -> newest).

    Returns a dict with:
      signal        -- "LONG" / "SHORT" / None (fires only on the candle that first
                       completes the confirmation run, so a 15-min poller dedups by the
                       latest candle timestamp)
      ema, price    -- latest EMA185 and latest close
      side          -- "above" / "below" / None (where price sits vs EMA now)
      consec_above  -- consecutive closes above EMA185 ending at the latest candle
      consec_below  -- consecutive closes below
      dist_pct      -- latest close distance from EMA185, in %
    """
    e = ema(closes, period)
    # Keep only candles where the EMA is defined, paired with their close.
    pairs = [(c, m) for c, m in zip(closes, e) if m is not None]
    if len(pairs) < max(confirm_long, confirm_short) + 1:
        return {"signal": None, "ema": None, "price": closes[-1] if closes else None,
                "side": None, "consec_above": 0, "consec_below": 0, "dist_pct": None}

    above = [c > m for c, m in pairs]
    below = [c < m for c, m in pairs]
    consec_above = _trailing_run(above)
    consec_below = _trailing_run(below)

    price, ema_now = pairs[-1]
    side = "above" if price > ema_now else ("below" if price < ema_now else None)
    dist_pct = (price - ema_now) / ema_now * 100 if ema_now else None

    signal = None
    # Fire exactly when the run first reaches the confirmation count on the latest candle.
    if consec_above == confirm_long:
        signal = "LONG"
    elif consec_below == confirm_short:
        signal = "SHORT"

    return {"signal": signal, "ema": round(ema_now, 6), "price": price, "side": side,
            "consec_above": consec_above, "consec_below": consec_below,
            "dist_pct": round(dist_pct, 2) if dist_pct is not None else None}
