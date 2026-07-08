"""Telegram notifications for trading signals."""
import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured; skipping notification")
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram API error: %s", data)
            return False
        return True
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def _fmt(v: float | None, digits: int = 4) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    return f"{v:.{digits}g}" if abs(v) < 1 else f"{v:,.{digits}f}"


def notify_new_signal(sig) -> bool:
    """sig: models.Signal"""
    if sig.strategy.value == "MA50_RETEST":
        emoji = "ðŸŸ¢" if sig.direction.value == "LONG" else "ðŸ”´"
        text = (
            f"{emoji} <b>SINYAL BARU â€” MA50 Daily Retest</b>\n\n"
            f"ðŸ“Š Market: <b>{sig.market}</b>\n"
            f"ðŸ“ˆ Arah: <b>{sig.direction.value}</b>\n"
            f"ðŸ’µ Entry: <b>{_fmt(sig.entry_price)}</b>\n"
            f"ðŸ›‘ Stop Loss: <b>{_fmt(sig.stop_loss)}</b>\n"
            f"ðŸŽ¯ Take Profit: <b>{_fmt(sig.take_profit)}</b>\n\n"
            f"ðŸ“ {sig.reason}"
        )
    else:
        emoji = "ðŸ”"
        legs = ("LONG " + (sig.base_market or "?") + " / SHORT " + (sig.quote_market or "?")
                if sig.direction.value == "LONG_SPREAD"
                else "SHORT " + (sig.base_market or "?") + " / LONG " + (sig.quote_market or "?"))
        text = (
            f"{emoji} <b>SINYAL BARU â€” Pair Trading</b>\n\n"
            f"ðŸ“Š Pair: <b>{sig.market}</b>\n"
            f"âš–ï¸ Posisi: <b>{legs}</b>\n"
            f"ðŸ“ Z-score entry: <b>{sig.entry_zscore:+.2f}</b>\n"
            f"ðŸ“ Hedge ratio: <b>{_fmt(sig.hedge_ratio)}</b>\n\n"
            f"ðŸ“ {sig.reason}"
        )
    return send_message(text)


def notify_closed_signal(sig) -> bool:
    pnl = sig.pnl_pct or 0.0
    emoji = "âœ…" if pnl >= 0 else "âŒ"
    status_label = {
        "CLOSED_TP": "Take Profit tercapai ðŸŽ¯",
        "CLOSED_SL": "Stop Loss kena ðŸ›‘",
        "CLOSED_EXIT": "Mean reversion selesai (z-score kembali normal)",
        "CLOSED_MANUAL": "Ditutup manual",
        "EXPIRED": "Kedaluwarsa (max holding period)",
    }.get(sig.status.value, sig.status.value)
    text = (
        f"{emoji} <b>SINYAL DITUTUP</b>\n\n"
        f"ðŸ“Š {sig.market} ({sig.strategy.value})\n"
        f"â„¹ï¸ {status_label}\n"
        f"ðŸ’° P&amp;L: <b>{pnl:+.2f}%</b>"
    )
    return send_message(text)

