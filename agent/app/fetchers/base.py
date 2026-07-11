"""HTTP client dasar semua fetcher: retry + exponential backoff, hormati 429
(Retry-After), dan tidak pernah mencetak API key ke log."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 2.0


class FetchError(Exception):
    pass


def get_json(url: str, *, params: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None,
             timeout: float = 20.0,
             max_retries: int = MAX_RETRIES) -> tuple[Any, int]:
    """GET dengan retry. Return (json, status_code). 429 -> tunggu Retry-After
    atau backoff eksponensial; 5xx/network error -> backoff; 4xx lain -> gagal."""
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After") or BACKOFF_BASE_SECONDS * (2 ** attempt))
                logger.warning("429 dari %s — tunggu %.1fs (attempt %d)", url, wait, attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                raise FetchError(f"server error {resp.status_code}")
            resp.raise_for_status()
            return resp.json(), resp.status_code
        except (httpx.RequestError, FetchError) as e:
            last_err = e
            wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
            logger.warning("Gagal GET %s (%s) — retry dalam %.1fs", url, e, wait)
            time.sleep(wait)
        except httpx.HTTPStatusError as e:
            raise FetchError(f"{url} -> {e.response.status_code}: {e.response.text[:200]}") from e
    raise FetchError(f"Gagal setelah {max_retries} percobaan: {url} ({last_err})")
