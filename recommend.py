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


def _format_telegram(items: list[dict], threshold: float) -> str:
    lines = ["⭐ <b>REKOMENDASI PAIR TRADING</b>",
             f"Pair dengan |korelasi| ≥ {threshold:.2f} (scan multi-venue):", ""]
    for it in items[:15]:
        wl = " 👁️" if it.get("in_watchlist") else ""
        ext = "" if it["addable"] else " (di luar Extended)"
        lines.append(f"• <b>{it['symbol_a']}/{it['symbol_b']}</b> — "
                     f"level {it['corr_level']:+.3f}, returns {it['corr_returns']:+.3f}{ext}{wl}")
    if len(items) > 15:
        lines.append(f"… dan {len(items) - 15} pair lainnya")
    lines += ["", "Tambahkan ke watchlist lewat halaman ⭐ Recommended untuk mulai "
              "memantau sinyal z-score-nya."]
    return "\n".join(lines)


def main() -> int:
    if V2_DIR not in sys.path:
        sys.path.insert(0, V2_DIR)
    import pipeline as v2_pipeline
    import universe as v2_universe

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

    table = v2_universe.build_symbol_table()
    db = SessionLocal()
    try:
        watched = set()
        for p in db.execute(select(WatchedPair)).scalars().all():
            watched.add((p.base_market, p.quote_market))
            watched.add((p.quote_market, p.base_market))
    finally:
        db.close()

    items = []
    for row in res.qualified.itertuples():
        src_a, tick_a = table.get(row.symbol_a, (None, None))
        src_b, tick_b = table.get(row.symbol_b, (None, None))
        addable = src_a == "extended" and src_b == "extended"
        items.append({
            "symbol_a": row.symbol_a, "symbol_b": row.symbol_b,
            "corr_level": float(row.corr_level), "corr_returns": float(row.corr_returns),
            "addable": addable,
            "in_watchlist": addable and (tick_a, tick_b) in watched,
        })

    if not items:
        logger.info("No pairs qualified >= %.2f; nothing to send.", THRESHOLD)
        return 0

    ok = telegram_notifier.send_message(_format_telegram(items, THRESHOLD))
    logger.info("Recommendation push: %d pairs, telegram sent=%s", len(items), ok)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("Recommendation scan crashed")
        sys.exit(1)
