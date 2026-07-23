"""Yahoo Finance chart API adapter — used for instruments Extended Exchange doesn't list
(Brent crude `BZ=F`, and any other future/FX/index you want to add: WTI `CL=F`, spot gold
`GC=F`, DXY `DX-Y.NYB`, etc).

NOT smoke-tested live: this sandbox's outbound network is restricted to a small allowlist
that did not include query1.finance.yahoo.com (requests returned empty/blocked). The
request shape below matches Yahoo's public (undocumented, no-auth) chart endpoint as of
last training knowledge:

    GET https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d
    -> {"chart": {"result": [{"timestamp": [...], "indicators": {"quote": [{"close": [...]}]}}]}}

First thing to do in Claude Code: run this against BZ=F and confirm the shape hasn't
drifted before trusting it. Yahoo has no official SLA/auth and occasionally rate-limits or
reshapes responses without notice — if this breaks, stooq.com's CSV endpoint
(`https://stooq.com/q/d/l/?s=<ticker>&i=d`) is a simpler fallback with a stable format.
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import Candle, DataSource, DataSourceError
from config import settings

logger = logging.getLogger(__name__)


class YahooFinanceSource(DataSource):
    name = "yahoo"

    def fetch_daily_closes(self, ticker: str, limit: int) -> list[Candle]:
        # ~limit trading days -> ask for enough calendar days of range to cover it,
        # Yahoo's `range` param is calendar time, futures/FX trade ~5-7 days/week.
        range_str = "2y" if limit > 250 else "1y"
        url = f"{settings.yahoo_base_url}/v8/finance/chart/{ticker}"
        params = {"range": range_str, "interval": "1d"}
        last_err: Exception | None = None
        for attempt in range(settings.request_retries):
            try:
                resp = httpx.get(url, params=params, timeout=settings.request_timeout_s,
                                 headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                payload = resp.json()
                result = (payload.get("chart") or {}).get("result") or []
                if not result:
                    raise DataSourceError(f"Yahoo returned no result for {ticker} "
                                          f"(payload: {str(payload)[:200]})")
                r0 = result[0]
                timestamps = r0.get("timestamp") or []
                closes = ((r0.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                candles = [Candle(t_ms=int(t) * 1000, close=float(c))
                          for t, c in zip(timestamps, closes) if c is not None]
                candles.sort(key=lambda c: c.t_ms)
                if not candles:
                    raise DataSourceError(f"Yahoo returned zero usable candles for {ticker}")
                return candles[-limit:]
            except (httpx.HTTPError, ValueError, KeyError, IndexError, DataSourceError) as exc:
                last_err = exc
                logger.warning("Yahoo fetch failed (attempt %d/%d) %s: %s",
                               attempt + 1, settings.request_retries, ticker, exc)
                time.sleep(1.0 * (attempt + 1))
        raise DataSourceError(f"Yahoo Finance unreachable for {ticker}: {last_err}")
