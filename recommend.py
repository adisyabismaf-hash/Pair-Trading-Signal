"""Daily "Recommended pairs" entrypoint — run by GitHub Actions on a schedule.

Runs the pair-trading-v2 multi-venue correlation screening (correlation only, no
backtest) and pushes every pair with |level correlation| >= threshold to Telegram as
a pair-trading signal candidate. Pairs already in the watchlist are flagged.

Same idea as the dashboard's ⭐ Recommended page, but headless so it fires 07:00 WIB
every day even when nobody has the dashboard open.
"""
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("recommend")

ROOT = Path(__file__).resolve().parent
V2_DIR = os.path.abspath(os.environ.get("V2_DIR", str(ROOT / "pair-trading-v2")))
THRESHOLD = float(os.environ.get("RECO_CORR_THRESHOLD", "0.90"))


def main() -> int:
    if V2_DIR not in sys.path:
        sys.path.insert(0, V2_DIR)
    import pipeline as v2_pipeline

    from core.database import SessionLocal, init_db
    from core.models import WatchedPair
    from core import telegram_notifier
    from sqlalchemy import select

    logger.info("Recommendation scan start (threshold %.2f)", THRESHOLD)
    init_db(seed=True)

    res = v2_pipeline.scan_pipeline(
        ["crypto", "tradfi", "external", "spread"],
        corr_threshold=THRESHOLD, corr_method="level", run_backtest=False)
    if not res.ok:
        logger.error("Scan failed: %s", res.reason)
        return 1

    items = v2_pipeline.build_reco_items(res)
    db = SessionLocal()
    try:
        watched = set()
        for p in db.execute(select(WatchedPair)).scalars().all():
            watched.add((p.base_market, p.quote_market))
            watched.add((p.quote_market, p.base_market))
    finally:
        db.close()
    for it in items:
        it["in_watchlist"] = it["addable"] and (it["base_market"], it["quote_market"]) in watched

    if not items:
        logger.info("No pairs qualified >= %.2f; nothing to send.", THRESHOLD)
        return 0

    ok = telegram_notifier.send_message(
        v2_pipeline.format_reco_telegram(items, THRESHOLD))
    logger.info("Recommendation push: %d pairs (top 5 sent), telegram sent=%s", len(items), ok)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Recommendation scan crashed")
        sys.exit(1)
