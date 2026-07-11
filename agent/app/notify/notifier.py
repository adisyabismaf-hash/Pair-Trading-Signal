"""L5 — kanal notifikasi. Telegram menyusul: TelegramNotifier sudah siap penuh,
aktif otomatis begitu TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID diisi di .env.
Selama kosong, pesan ke console + file outbox/ (bisa diaudit)."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import httpx

from app.config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)


class Notifier:
    channel = "console"

    def send(self, text: str, kind: str = "message") -> bool:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """Dev/paper period tanpa Telegram: cetak + arsip ke outbox/."""
    channel = "console"

    def __init__(self, outbox_dir: Path | None = None) -> None:
        self.outbox = outbox_dir or PROJECT_ROOT / "outbox"
        self.outbox.mkdir(exist_ok=True)

    def send(self, text: str, kind: str = "message") -> bool:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.outbox / f"{stamp}_{kind}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"\n{'=' * 60}\n[OUTBOX -> {path.name}]\n{text}\n{'=' * 60}")
        return True


class TelegramNotifier(Notifier):
    channel = "telegram"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    def send(self, text: str, kind: str = "message") -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = httpx.post(url, json={"chat_id": self._chat_id, "text": text},
                              timeout=15.0)
            ok = resp.status_code == 200 and resp.json().get("ok", False)
            if not ok:
                logger.error("Telegram gagal (%s): %s", resp.status_code, resp.text[:200])
            return ok
        except httpx.RequestError as e:
            logger.error("Telegram tidak terjangkau: %s", e)
            return False


def get_notifier(settings: Settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return ConsoleNotifier()
