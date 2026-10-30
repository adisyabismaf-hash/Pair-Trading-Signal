#!/usr/bin/env python3
"""EMA-185 (4H) break-signal scanner.

Fetches 4H candles from Extended for each symbol, computes the EMA185 break signal
(see analytics/ema_signal.py), and reports the ones currently signalling LONG / SHORT.

    python ema_scanner.py --symbols BTC,ETH,SOL --period 185

Signal rule: LONG = 3 consecutive 4H closes above EMA185 after a break up; SHORT = 1
close below EMA185 after a break down. A 15-min poller should dedup by `candle_t_ms`
so each 4H candle only alerts once.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import httpx

from config import settings
from universe import CRYPTO_UNIVERSE
from analytics.ema_signal import ema_break_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ema_scanner")

DEFAULT_SYMBOLS = [sym for sym, _src, _tk in CRYPTO_UNIVERSE]  # BTC, ETH, SOL, ... (Extended)
ARROW = {"LONG": "🟢", "SHORT": "🔴"}


def fetch_4h(ticker: str, limit: int = 1000) -> list[dict]:
    """4H candles for an Extended market, oldest -> newest. Each: {t_ms, close}."""
    url = f"{settings.extended_base_url}/api/v1/info/candles/{ticker}/trades"
    params = {"interval": "PT4H", "limit": min(limit, 1000)}
    last_err = None
    for attempt in range(settings.request_retries):
        try:
            resp = httpx.get(url, params=params, timeout=settings.request_timeout_s)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "OK":
                raise ValueError(f"status={payload.get('status')}")
            rows = payload.get("data") or []
            out = [{"t_ms": int(c["T"]), "close": float(c["c"])} for c in rows]
            out.sort(key=lambda c: c["t_ms"])
            return out
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            last_err = exc
            logger.warning("4H fetch failed (%d/%d) %s: %s",
                           attempt + 1, settings.request_retries, ticker, exc)
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Extended 4H unreachable for {ticker}: {last_err}")


def analyze_symbol(symbol: str, ticker: str | None = None, *, period: int = 185,
                   confirm_long: int = 3, confirm_short: int = 1) -> dict:
    """Fetch 4H candles and compute the EMA185 break state for one symbol."""
    ticker = ticker or f"{symbol}-USD"
    candles = fetch_4h(ticker)
    closes = [c["close"] for c in candles]
    state = ema_break_signal(closes, period=period,
                             confirm_long=confirm_long, confirm_short=confirm_short)
    state["symbol"] = symbol
    state["candle_t_ms"] = candles[-1]["t_ms"] if candles else None
    state["candles"] = len(candles)
    return state


def scan_ema(symbols: list[str] | None = None, *, period: int = 185,
             confirm_long: int = 3, confirm_short: int = 1) -> list[dict]:
    rows = []
    for symbol in (symbols or DEFAULT_SYMBOLS):
        try:
            rows.append(analyze_symbol(symbol, period=period,
                                       confirm_long=confirm_long, confirm_short=confirm_short))
        except Exception as exc:
            logger.warning("Skipping %s: %s", symbol, exc)
    return rows


def format_ema_telegram(rows: list[dict], *, period: int = 185) -> str | None:
    """Telegram message of symbols currently signalling. None if no active signal."""
    sig = [r for r in rows if r.get("signal")]
    if not sig:
        return None
    lines = [f"📐 <b>SINYAL EMA{period} (4H)</b>", ""]
    for r in sig:
        emoji = ARROW.get(r["signal"], "")
        action = "LONG (Buy)" if r["signal"] == "LONG" else "SHORT (Sell)"
        dist = f" ({r['dist_pct']:+.1f}% dari EMA)" if r.get("dist_pct") is not None else ""
        lines.append(f"{emoji} <b>{r['symbol']}</b> — {action}\n"
                     f"   harga {r['price']:,.4g} · EMA{period} {r['ema']:,.4g}{dist}")
    lines += ["", "Aturan: LONG = 3 close 4H di atas EMA185 setelah break; "
              "SHORT = 1 close di bawah."]
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="", help="Comma-separated (default: crypto universe)")
    p.add_argument("--period", type=int, default=185)
    p.add_argument("--confirm-long", type=int, default=3)
    p.add_argument("--confirm-short", type=int, default=1)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    rows = scan_ema(symbols, period=args.period,
                    confirm_long=args.confirm_long, confirm_short=args.confirm_short)
    print(f"\n=== EMA{args.period} (4H) — {len(rows)} simbol ===")
    for r in sorted(rows, key=lambda x: (x.get("signal") is None, x["symbol"])):
        tag = f"  <<< {r['signal']}" if r.get("signal") else ""
        side = r.get("side") or "—"
        print(f"  {r['symbol']:5} {side:5} above={r['consec_above']} below={r['consec_below']} "
              f"price={r['price']} ema={r['ema']}{tag}")
    msg = format_ema_telegram(rows, period=args.period)
    print("\n--- Telegram ---")
    print(msg or "(tidak ada sinyal aktif)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
