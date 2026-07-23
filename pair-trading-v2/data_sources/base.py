"""Shared interface every data source adapter implements."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candle:
    t_ms: int      # bar close timestamp, epoch milliseconds, UTC
    close: float


class DataSourceError(Exception):
    pass


class DataSource:
    """One adapter per venue. Subclasses implement fetch_daily_closes()."""

    name: str = "base"

    def fetch_daily_closes(self, ticker: str, limit: int) -> list[Candle]:
        """Return up to `limit` daily candles, oldest -> newest, deduplicated by day.

        Must raise DataSourceError (not a bare exception) on failure so scanner.py can
        skip a bad symbol without killing the whole run.
        """
        raise NotImplementedError
