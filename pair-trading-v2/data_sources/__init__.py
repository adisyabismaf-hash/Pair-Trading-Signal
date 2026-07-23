from .base import DataSource, Candle, DataSourceError
from .extended_exchange import ExtendedExchangeSource
from .yahoo_finance import YahooFinanceSource
from .binance import BinanceSource

SOURCES = {
    "extended": ExtendedExchangeSource(),
    "yahoo": YahooFinanceSource(),
    "binance": BinanceSource(),
}

__all__ = ["DataSource", "Candle", "DataSourceError", "SOURCES",
           "ExtendedExchangeSource", "YahooFinanceSource", "BinanceSource"]
