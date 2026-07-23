"""Central configuration for Pair Trading Tools v2.

Everything here can be overridden with CLI flags in scanner.py; these are just defaults.
"""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    # -- correlation screening --
    corr_threshold: float = 0.90          # minimum |correlation| to qualify as a watchlist candidate
    corr_method: str = "level"            # "level" (log-price, matches v1) or "returns" (log-return, stricter)

    # -- backtest (z-score / OLS hedge ratio pair trading, same model as v1) --
    lookback_days: int = 60               # rolling window used to fit hedge ratio + spread mean/std
    backtest_days: int = 90               # ~3 months; how far back the backtest simulates entries
    min_backtest_days: int = 30           # floor for the shortened window used on thin-history pairs
    entry_zscore: float = 2.0
    exit_zscore: float = 0.5
    stop_zscore: float = 4.0
    max_holding_days: int = 30

    # -- data sources --
    extended_base_url: str = field(default_factory=lambda: _env(
        "EXTENDED_BASE_URL", "https://api.starknet.extended.exchange"))
    yahoo_base_url: str = field(default_factory=lambda: _env(
        "YAHOO_BASE_URL", "https://query1.finance.yahoo.com"))
    # data-api.binance.vision is Binance's official public market-data mirror; the main
    # api.binance.com host is unreachable from some networks (times out from here).
    binance_base_url: str = field(default_factory=lambda: _env(
        "BINANCE_BASE_URL", "https://data-api.binance.vision"))

    candle_limit: int = 600               # daily candles to request (covers lookback + backtest with margin)
    request_timeout_s: float = 20.0
    request_retries: int = 3

    # -- output --
    reports_dir: str = "reports"


settings = Settings()
