"""Binance public REST adapter — used for tokenized-gold pairs Extended doesn't list
liquidly (PAXG, XAUT). No API key needed for public klines.

NOT smoke-tested live in this sandbox (api.binance.com was outside the network allowlist
here). Request shape is Binance's standard public klines endpoint:

    GET https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=1d&limit=N
    -> [[openTime, open, high, low, close, volume, closeTime, ...], ...]  oldest -> newest

If Binance is geo-blocked or rate-limited from wherever this runs, Binance also has a
`binance.us` and `binance.vision` mirror with the same schema, or fall back to
CoinGecko's `/coins/{id}/market_chart` endpoint (different schema, would need its own
small adapter).
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import Candle, DataSource, DataSourceError
from config import settings

logger = logging.getLogger(__name__)


class BinanceSource(DataSource):
    name = "binance"

    def fetch_daily_closes(self, ticker: str, limit: int) -> list[Candle]:
        url = f"{settings.binance_base_url}/api/v3/klines"
        params = {"symbol": ticker, "interval": "1d", "limit": min(limit, 1000)}
        last_err: Exception | None = None
        for attempt in range(settings.request_retries):
            try:
                resp = httpx.get(url, params=params, timeout=settings.request_timeout_s)
                resp.raise_for_status()
                rows = resp.json()
                if not isinstance(rows, list):
                    raise DataSourceError(f"Binance returned non-list payload for {ticker}: "
                                          f"{str(rows)[:200]}")
                candles = [Candle(t_ms=int(row[6]), close=float(row[4])) for row in rows]
                candles.sort(key=lambda c: c.t_ms)
                if not candles:
                    raise DataSourceError(f"Binance returned zero candles for {ticker}")
                return candles
            except (httpx.HTTPError, ValueError, KeyError, IndexError, DataSourceError) as exc:
                last_err = exc
                logger.warning("Binance fetch failed (attempt %d/%d) %s: %s",
                               attempt + 1, settings.request_retries, ticker, exc)
                time.sleep(1.0 * (attempt + 1))
        raise DataSourceError(f"Binance unreachable for {ticker}: {last_err}")
