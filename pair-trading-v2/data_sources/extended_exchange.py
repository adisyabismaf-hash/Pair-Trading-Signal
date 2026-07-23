"""Extended Exchange (Starknet perp DEX) adapter.

This is the only adapter that was hit against the live API during development. Confirmed
response shape (2026-07-23):

    GET {base}/api/v1/info/candles/{ticker}/trades?interval=P1D&limit=N
    -> {"status": "OK", "data": [{"o","l","h","c","v","T"}, ...]}  # newest-first, ms epoch

Confirmed tickers that exist and have deep history: BTC-USD, ETH-USD, SOL-USD, XRP-USD,
LTC-USD, AVAX-USD, SUI-USD, DOGE-USD, ADA-USD, LINK-USD, XAU-USD, XAG-USD, WTI-USD,
XPT-USD, EUR-USD, USDJPY-USD, SPX-USD. Confirmed NOT to exist: BRENT-USD, OIL-USD,
XAUT-USD, NATGAS-USD. PAXG-USD exists but returned only 1 candle at probe time — treat as
unusable and source PAXG from Binance instead (see binance.py).
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import Candle, DataSource, DataSourceError
from config import settings

logger = logging.getLogger(__name__)


class ExtendedExchangeSource(DataSource):
    name = "extended"

    def fetch_daily_closes(self, ticker: str, limit: int) -> list[Candle]:
        url = f"{settings.extended_base_url}/api/v1/info/candles/{ticker}/trades"
        params = {"interval": "P1D", "limit": limit}
        last_err: Exception | None = None
        for attempt in range(settings.request_retries):
            try:
                resp = httpx.get(url, params=params, timeout=settings.request_timeout_s)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("status") != "OK":
                    raise DataSourceError(f"Extended returned status={payload.get('status')} for {ticker}")
                raw = payload.get("data") or []
                candles = [Candle(t_ms=int(c["T"]), close=float(c["c"])) for c in raw]
                candles.sort(key=lambda c: c.t_ms)
                if not candles:
                    raise DataSourceError(f"Extended returned zero candles for {ticker} "
                                          f"(market may not exist or be illiquid)")
                return candles
            except (httpx.HTTPError, ValueError, KeyError, DataSourceError) as exc:
                last_err = exc
                logger.warning("Extended fetch failed (attempt %d/%d) %s: %s",
                               attempt + 1, settings.request_retries, ticker, exc)
                time.sleep(1.0 * (attempt + 1))
        raise DataSourceError(f"Extended Exchange unreachable for {ticker}: {last_err}")
