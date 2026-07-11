"""Agent Signal runner untuk GitHub Actions — pengganti APScheduler di cloud.

Setiap invocation = satu tugas lalu exit (model cron Actions):
  python agent_runner.py fast          # M3 wallet + M4 harga + M6 derivatif + realtime scan + follow-up
  python agent_runner.py slow          # M1 TVL + M2 fees (tiap 4 jam)
  python agent_runner.py daily-ingest  # M5 stablecoin + M7 Fear&Greed (harian, sebelum outlook)
  python agent_runner.py outlook       # daily outlook 07:00 WIB
  python agent_runner.py digest        # evening digest 17:00 WIB
  python agent_runner.py weekly        # weekly review Minggu
  python agent_runner.py all-ingest    # semua ingest sekaligus (cold start / manual)

Konfigurasi via env (GitHub Secrets): DATABASE_URL, COINGECKO_API_KEY,
ETHERSCAN_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY (ops).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agent"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent_runner")

TELEGRAM_PREFIX = "🤖 [Agent Signal]\n"  # pembeda dari pesan pair trading di chat yang sama


class PrefixNotifier:
    """Bungkus notifier apa pun dengan prefix sumber pesan."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.channel = inner.channel

    def send(self, text: str, kind: str = "message") -> bool:
        return self._inner.send(TELEGRAM_PREFIX + text, kind=kind)


def main() -> int:
    task = sys.argv[1] if len(sys.argv) > 1 else "fast"

    from app import ingest, reporting
    from app.config import load_action_rules, load_positions, load_settings
    from app.db import init_db, session_scope
    from app.notify.notifier import get_notifier

    import agent_store

    init_db()
    agent_store.init_store()

    rules = load_action_rules()
    wl = agent_store.effective_watchlist()
    pos = load_positions()
    settings = load_settings()
    notifier = PrefixNotifier(get_notifier(settings))

    logger.info("task=%s | %d aset, %d protokol | notifier=%s",
                task, len(wl.assets), len(wl.protocols), notifier.channel)

    def _try(name, fn):
        """Satu sumber gagal tidak boleh mematikan sumber lain."""
        try:
            with session_scope() as s:
                fn(s)
            logger.info("%s OK", name)
        except Exception:
            logger.exception("%s GAGAL (lanjut)", name)

    if task in ("fast", "all-ingest"):
        _try("ingest_prices", lambda s: ingest.ingest_prices(s, wl, settings))
        _try("ingest_wallets", lambda s: ingest.ingest_wallet_txs(s, wl, settings))
        _try("ingest_derivatives", lambda s: ingest.ingest_derivatives(s, settings=settings))
    if task in ("slow", "all-ingest"):
        _try("ingest_tvl", lambda s: ingest.ingest_tvl(s, wl))
        _try("ingest_fees", lambda s: ingest.ingest_fees(s, wl))
    if task in ("daily-ingest", "all-ingest"):
        _try("ingest_stablecoins", lambda s: ingest.ingest_stablecoins(s))
        _try("ingest_fng", lambda s: ingest.ingest_fng(s))

    if task == "fast":
        with session_scope() as s:
            sent = reporting.process_realtime_alerts(s, rules, wl, pos, settings, notifier)
            logger.info("realtime: %d alert", len(sent))
        with session_scope() as s:
            reporting.check_followups(s, rules, notifier)
    elif task == "outlook":
        with session_scope() as s:
            reporting.send_daily_outlook(s, rules, wl, pos, settings, notifier)
    elif task == "digest":
        with session_scope() as s:
            reporting.send_evening_digest(s, rules, wl, pos, notifier)
    elif task == "weekly":
        with session_scope() as s:
            reporting.send_weekly_review(s, rules, notifier)
    elif task not in ("fast", "slow", "daily-ingest", "all-ingest"):
        logger.error("task tidak dikenal: %s", task)
        return 2

    logger.info("selesai: %s", task)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logger.exception("agent_runner crash")
        sys.exit(1)
