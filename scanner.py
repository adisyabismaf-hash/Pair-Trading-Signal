"""24/7 scanner entrypoint — run by GitHub Actions on a schedule.

Each run: ensure tables exist, scan the active watchlist once, open/close
signals, and push Telegram alerts. Exits non-zero only on a fatal error so the
GitHub Actions run is marked failed and you get notified.
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scanner")


def main() -> int:
    from core.database import SessionLocal, init_db
    from core.engine import run_scan

    logger.info("Scanner start")
    init_db(seed=True)

    db = SessionLocal()
    try:
        result = run_scan(db)
        logger.info(
            "Scan done: ok=%s pairs=%d symbols=%d created=%d closed=%d | %s",
            result["ok"], result["pairs_scanned"], result["symbols_scanned"],
            result["signals_created"], result["signals_closed"], result["message"],
        )
        # A scan that finished but had per-market errors is still a successful run;
        # individual errors are recorded in scan_logs and surfaced on the dashboard.
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Scanner crashed")
        sys.exit(1)
