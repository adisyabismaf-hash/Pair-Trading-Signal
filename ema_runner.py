"""EMA185 (4H) break-signal runner — run by GitHub Actions on a schedule.

Each run: scan the crypto universe for EMA185 (4H) break signals, dedup against the
ema_alerts table (so every 4H candle alerts at most once across the many scheduled runs),
and push new LONG/SHORT signals to Telegram.

Rule: LONG = 3 consecutive 4H closes above EMA185 after a break up; SHORT = 1 close below.
"""
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ema_runner")

ROOT = Path(__file__).resolve().parent
V2_DIR = os.path.abspath(os.environ.get("V2_DIR", str(ROOT / "pair-trading-v2")))

EMA_PERIOD = int(os.environ.get("EMA_PERIOD", "185"))
EMA_CONFIRM_LONG = int(os.environ.get("EMA_CONFIRM_LONG", "3"))
EMA_CONFIRM_SHORT = int(os.environ.get("EMA_CONFIRM_SHORT", "1"))
EMA_SYMBOLS = [s.strip().upper() for s in os.environ.get("EMA_SYMBOLS", "").split(",") if s.strip()]


def main() -> int:
    if V2_DIR not in sys.path:
        sys.path.insert(0, V2_DIR)
    import ema_scanner

    from core.database import SessionLocal, init_db
    from core.models import EmaAlert
    from core import telegram_notifier
    from sqlalchemy import select

    logger.info("EMA%d (4H) scan start", EMA_PERIOD)
    init_db(seed=False)

    rows = ema_scanner.scan_ema(EMA_SYMBOLS or None, period=EMA_PERIOD,
                                confirm_long=EMA_CONFIRM_LONG, confirm_short=EMA_CONFIRM_SHORT)
    active = [r for r in rows if r.get("signal")]
    if not active:
        logger.info("No active EMA signals.")
        return 0

    db = SessionLocal()
    fresh = []
    try:
        for r in active:
            exists = db.execute(select(EmaAlert.id).where(
                EmaAlert.symbol == r["symbol"],
                EmaAlert.candle_t_ms == r["candle_t_ms"],
                EmaAlert.signal == r["signal"])).first()
            if exists:
                continue
            db.add(EmaAlert(symbol=r["symbol"], signal=r["signal"],
                            candle_t_ms=r["candle_t_ms"], price=r.get("price"),
                            ema=r.get("ema")))
            fresh.append(r)
        db.commit()
    finally:
        db.close()

    if fresh:
        msg = ema_scanner.format_ema_telegram(fresh, period=EMA_PERIOD)
        ok = telegram_notifier.send_message(msg) if msg else False
        logger.info("EMA scan: %d new signal(s), telegram sent=%s", len(fresh), ok)
    else:
        logger.info("EMA scan: %d active but all already alerted.", len(active))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("EMA runner crashed")
        sys.exit(1)
