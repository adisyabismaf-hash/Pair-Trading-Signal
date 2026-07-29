"""Default asset universe for Pair Trading Tools v2.

Each entry is (symbol, source, source_ticker):
  symbol         -- the name used everywhere in this tool's output (short, human-readable)
  source         -- one of "extended", "yahoo", "binance" (see data_sources/)
  source_ticker  -- the ticker string that specific source's API expects

Confirmed live against Extended Exchange's public API during development (2026-07-23):
  EXISTS:      BTC-USD, ETH-USD, SOL-USD, XRP-USD, LTC-USD, AVAX-USD, SUI-USD, DOGE-USD,
               ADA-USD, LINK-USD, XAU-USD (gold), XAG-USD (silver), WTI-USD (oil),
               XPT-USD (platinum), EUR-USD, USDJPY-USD, SPX-USD
  DOES NOT EXIST on Extended: BRENT-USD, OIL-USD, XAUT-USD, NATGAS-USD
  EXISTS BUT ILLIQUID (unusable, ~1 candle of history at probe time): PAXG-USD

Everything under CRYPTO_UNIVERSE and TRADFI_UNIVERSE below was pulled from Extended and is
known-good. YAHOO_UNIVERSE and BINANCE_UNIVERSE fill the gaps (Brent, PAXG, XAUT) — these
adapters are implemented but were not smoke-tested live (see README "Known limitations").
"""

CRYPTO_UNIVERSE = [
    ("BTC", "extended", "BTC-USD"),
    ("ETH", "extended", "ETH-USD"),
    ("SOL", "extended", "SOL-USD"),
    ("XRP", "extended", "XRP-USD"),
    ("LTC", "extended", "LTC-USD"),
    ("AVAX", "extended", "AVAX-USD"),
    ("SUI", "extended", "SUI-USD"),
    ("DOGE", "extended", "DOGE-USD"),
    ("ADA", "extended", "ADA-USD"),
    ("LINK", "extended", "LINK-USD"),
]

# Tradfi synthetics that Extended Exchange itself lists as perp markets.
TRADFI_UNIVERSE = [
    ("XAU", "extended", "XAU-USD"),      # spot gold
    ("XAG", "extended", "XAG-USD"),      # spot silver
    ("WTI", "extended", "WTI-USD"),      # WTI crude oil
    ("XPT", "extended", "XPT-USD"),      # platinum
    ("EURUSD", "extended", "EUR-USD"),
    ("USDJPY", "extended", "USDJPY-USD"),
    ("SPX", "extended", "SPX-USD"),      # S&P 500 index perp
]

# Assets Extended does NOT list, needed specifically for the "same asset, different
# venue/wrapper" spread pairs the user asked about (Brent vs WTI, PAXG vs XAUT, XAU vs
# XAUT). Pulled from Yahoo Finance (futures/FX) or Binance (crypto-wrapped commodities).
EXTERNAL_UNIVERSE = [
    ("BRENT", "yahoo", "BZ=F"),          # ICE Brent crude front-month future
    ("PAXG", "binance", "PAXGUSDT"),     # Paxos Gold, 1 token = 1 troy oz, on-chain + CEX
    ("XAUT", "binance", "XAUTUSDT"),     # Tether Gold, 1 token = 1 troy oz
]

DEFAULT_UNIVERSE = {
    "crypto": CRYPTO_UNIVERSE,
    "tradfi": TRADFI_UNIVERSE,
    "external": EXTERNAL_UNIVERSE,
}

# ---------------------------------------------------------------------------------------
# Named spread pairs: economically the *same* underlying, traded on different venues or
# in different wrappers. These matter for a pairs desk regardless of the trailing
# correlation reading (they SHOULD be ~1.0 correlated and near-zero-spread; a divergence
# is the signal, not a coincidence to filter for) so scanner.py always backtests them,
# tagging them separately from the auto-screened correlation candidates.
# ---------------------------------------------------------------------------------------
SPREAD_PAIRS = [
    # (label, symbol_a, symbol_b, note)
    ("Brent vs WTI crude", "BRENT", "WTI", "Two crude oil benchmarks; spread reflects "
     "regional supply/refining/transport differentials, normally mean-reverting."),
    ("PAXG vs XAUT", "PAXG", "XAUT", "Two competing tokenized-gold stablecoins, both "
     "claim 1:1 backing by physical gold; spread should hover near zero basis points."),
    ("XAU vs XAUT", "XAU", "XAUT", "Spot gold (Extended synthetic) vs tokenized gold "
     "(Tether Gold); divergence = crypto-wrapper premium/discount to physical spot."),
    ("XAU vs PAXG", "XAU", "PAXG", "Spot gold vs tokenized gold (Paxos); same idea as "
     "XAU vs XAUT with the other major gold token."),
]


# Symbols that are economically the SAME underlying asset (just different wrapper/venue).
# A pair between two members is a wrapper/venue spread, not a diversified pair trade, so
# the "Recommended" Telegram list skips them (e.g. PAXG vs XAUT are both tokenized gold).
SAME_ASSET_GROUPS = {
    "gold": {"XAU", "PAXG", "XAUT"},
    "oil": {"WTI", "BRENT"},
}


def same_underlying(symbol_a: str, symbol_b: str) -> bool:
    """True if both symbols are the same underlying asset in different wrappers/venues."""
    return any(symbol_a in members and symbol_b in members
               for members in SAME_ASSET_GROUPS.values())


def build_symbol_table():
    """Flat dict symbol -> (source, source_ticker) across every configured universe."""
    table = {}
    for group in DEFAULT_UNIVERSE.values():
        for symbol, source, ticker in group:
            table[symbol] = (source, ticker)
    return table
