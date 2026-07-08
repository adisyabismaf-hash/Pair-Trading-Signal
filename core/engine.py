"""Signal engine: scans the watchlist, opens/closes signals, sends Telegram alerts."""
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.models import (
    Direction, ScanLog, Signal, SignalStatus, Strategy, WatchedPair, WatchedSymbol, utcnow,
)
from core import extended_client, ma50_retest, pair_trading, telegram_notifier

logger = logging.getLogger(__name__)

_scan_lock = threading.Lock()


def _open_signal_exists(db: Session, strategy: Strategy, market: str) -> bool:
    stmt = select(Signal.id).where(
        Signal.strategy == strategy,
        Signal.market == market,
        Signal.status == SignalStatus.OPEN,
    ).limit(1)
    return db.execute(stmt).first() is not None


# ---------------- MA50 retest ----------------

def _scan_symbol(db: Session, ws: WatchedSymbol) -> tuple[int, int]:
    created = closed = 0
    candles = extended_client.fetch_candles(ws.market, interval="P1D", limit=250)
    if len(candles) < settings.ma_period + 10:
        logger.info("Not enough candles for %s (%d)", ws.market, len(candles))
        return 0, 0

    result = ma50_retest.analyze(candles)
    state = result["state"]
    ws.last_price = state["price"]
    ws.last_ma50 = state["ma50"]
    ws.last_trend = state["trend"]
    ws.last_retest_count = state["retest_count"]
    ws.last_checked_at = utcnow()

    # 1) Monitor open signals for this market against candles since entry.
    open_signals = db.execute(select(Signal).where(
        Signal.strategy == Strategy.MA50_RETEST,
        Signal.market == ws.market,
        Signal.status == SignalStatus.OPEN,
    )).scalars().all()
    for sig in open_signals:
        opened_ms = int(sig.opened_at.replace(tzinfo=sig.opened_at.tzinfo or timezone.utc).timestamp() * 1000)
        since = [c for c in candles if c["t"] >= opened_ms - 86_400_000]
        exit_info = ma50_retest.check_exit(
            sig.direction.value, sig.entry_price, sig.stop_loss, sig.take_profit, since)
        if exit_info:
            sig.status = SignalStatus(exit_info["status"])
            sig.exit_price = exit_info["exit_price"]
            sig.pnl_pct = round(exit_info["pnl_pct"], 4)
            sig.closed_at = utcnow()
            db.flush()
            telegram_notifier.notify_closed_signal(sig)
            closed += 1

    # 2) New entry?
    signal = result["signal"]
    if signal and not _open_signal_exists(db, Strategy.MA50_RETEST, ws.market):
        sig = Signal(
            strategy=Strategy.MA50_RETEST,
            direction=Direction(signal["direction"]),
            market=ws.market,
            entry_price=signal["entry"],
            stop_loss=signal["stop_loss"],
            take_profit=signal["take_profit"],
            reason=signal["reason"],
            details={"ma50": signal["ma50"], "retest_count": signal["retest_count"]},
        )
        db.add(sig)
        db.flush()
        sig.telegram_sent = telegram_notifier.notify_new_signal(sig)
        created += 1
        logger.info("New MA50 signal: %s %s @ %s", sig.direction.value, ws.market, sig.entry_price)
    return created, closed


# ---------------- Pair trading ----------------

def _scan_pair(db: Session, wp: WatchedPair) -> tuple[int, int]:
    created = closed = 0
    candles_a = extended_client.fetch_candles(wp.base_market, interval="P1D", limit=250)
    candles_b = extended_client.fetch_candles(wp.quote_market, interval="P1D", limit=250)
    stats = pair_trading.analyze(candles_a, candles_b, wp.lookback)
    if stats is None:
        logger.info("Not enough aligned candles for %s/%s", wp.base_market, wp.quote_market)
        return 0, 0

    wp.last_zscore = stats["zscore"]
    wp.last_hedge_ratio = stats["hedge_ratio"]
    wp.last_correlation = stats["correlation"]
    wp.last_checked_at = utcnow()

    label = f"{wp.base_market} vs {wp.quote_market}"

    # 1) Monitor open pair signals.
    open_signals = db.execute(select(Signal).where(
        Signal.strategy == Strategy.PAIR_TRADING,
        Signal.market == label,
        Signal.status == SignalStatus.OPEN,
    )).scalars().all()
    for sig in open_signals:
        d = sig.details or {}
        entry_a, entry_b = d.get("entry_price_a"), d.get("entry_price_b")
        opened = sig.opened_at if sig.opened_at.tzinfo else sig.opened_at.replace(tzinfo=timezone.utc)
        holding_days = (datetime.now(timezone.utc) - opened).total_seconds() / 86400
        verdict = pair_trading.check_exit(sig.direction.value, stats["zscore"], wp.exit_zscore, holding_days)
        if verdict:
            sig.status = SignalStatus(verdict)
            sig.exit_zscore = stats["zscore"]
            sig.exit_price = stats["spread"]
            if entry_a and entry_b:
                sig.pnl_pct = round(pair_trading.open_pnl_pct(
                    sig.direction.value, sig.hedge_ratio or stats["hedge_ratio"],
                    entry_a, entry_b, stats["price_a"], stats["price_b"]), 4)
            sig.closed_at = utcnow()
            db.flush()
            telegram_notifier.notify_closed_signal(sig)
            closed += 1

    # 2) New entry?
    entry = pair_trading.entry_signal(stats, wp.entry_zscore, wp.exit_zscore)
    if entry and not _open_signal_exists(db, Strategy.PAIR_TRADING, label):
        sig = Signal(
            strategy=Strategy.PAIR_TRADING,
            direction=Direction(entry["direction"]),
            market=label,
            base_market=wp.base_market,
            quote_market=wp.quote_market,
            entry_price=entry["spread"],
            entry_zscore=entry["entry_zscore"],
            hedge_ratio=entry["hedge_ratio"],
            reason=entry["reason"],
            details={
                "entry_price_a": entry["price_a"],
                "entry_price_b": entry["price_b"],
                "lookback": wp.lookback,
                "exit_zscore": wp.exit_zscore,
                "stop_zscore": settings.pair_stop_zscore,
            },
        )
        db.add(sig)
        db.flush()
        sig.telegram_sent = telegram_notifier.notify_new_signal(sig)
        created += 1
        logger.info("New pair signal: %s %s (z=%.2f)", sig.direction.value, label, stats["zscore"])
    return created, closed


# ---------------- Orchestration ----------------

def run_scan(db: Session) -> dict:
    """Scan the entire active watchlist. Returns a summary dict."""
    if not _scan_lock.acquire(blocking=False):
        return {"ok": False, "pairs_scanned": 0, "symbols_scanned": 0,
                "signals_created": 0, "signals_closed": 0,
                "message": "Scan lain sedang berjalan"}
    try:
        created = closed = pairs = symbols = 0
        errors: list[str] = []

        for ws in db.execute(select(WatchedSymbol).where(WatchedSymbol.is_active)).scalars().all():
            try:
                c, x = _scan_symbol(db, ws)
                created += c
                closed += x
                symbols += 1
                db.commit()
            except Exception as exc:  # one bad market must not kill the scan
                db.rollback()
                logger.exception("Symbol scan failed for %s", ws.market)
                errors.append(f"{ws.market}: {exc}")

        for wp in db.execute(select(WatchedPair).where(WatchedPair.is_active)).scalars().all():
            try:
                c, x = _scan_pair(db, wp)
                created += c
                closed += x
                pairs += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.exception("Pair scan failed for %s/%s", wp.base_market, wp.quote_market)
                errors.append(f"{wp.base_market}/{wp.quote_market}: {exc}")

        ok = len(errors) == 0
        message = "Scan selesai tanpa error" if ok else "; ".join(errors)[:1000]
        log = ScanLog(ok=ok, pairs_scanned=pairs, symbols_scanned=symbols,
                      signals_created=created, signals_closed=closed, message=message)
        db.add(log)
        db.commit()
        return {"ok": ok, "pairs_scanned": pairs, "symbols_scanned": symbols,
                "signals_created": created, "signals_closed": closed, "message": message}
    finally:
        _scan_lock.release()


def close_signal_manual(db: Session, signal_id: int) -> Signal | None:
    """Close an open signal at the current market price, realize P&L, notify Telegram.
    Returns the updated Signal, or None if it doesn't exist / is already closed."""
    sig = db.get(Signal, signal_id)
    if not sig or sig.status != SignalStatus.OPEN:
        return None

    if sig.strategy == Strategy.MA50_RETEST:
        candles = extended_client.fetch_candles(sig.market, limit=2)
        if not candles:
            raise extended_client.ExtendedApiError("harga terkini tidak tersedia")
        price = candles[-1]["c"]
        sig.exit_price = price
        if sig.direction.value == "LONG":
            sig.pnl_pct = round((price - sig.entry_price) / sig.entry_price * 100, 4)
        else:
            sig.pnl_pct = round((sig.entry_price - price) / sig.entry_price * 100, 4)
    else:
        d = sig.details or {}
        ca = extended_client.fetch_candles(sig.base_market, limit=2)
        cb = extended_client.fetch_candles(sig.quote_market, limit=2)
        if not ca or not cb or not d.get("entry_price_a") or not d.get("entry_price_b"):
            raise extended_client.ExtendedApiError("data harga pair tidak lengkap")
        sig.pnl_pct = round(pair_trading.open_pnl_pct(
            sig.direction.value, sig.hedge_ratio or 1.0,
            d["entry_price_a"], d["entry_price_b"], ca[-1]["c"], cb[-1]["c"]), 4)

    sig.status = SignalStatus.CLOSED_MANUAL
    sig.closed_at = utcnow()
    db.commit()
    db.refresh(sig)
    telegram_notifier.notify_closed_signal(sig)
    return sig

