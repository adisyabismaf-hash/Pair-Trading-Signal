"""Etherscan API V2 multichain — wallet tracker (M3).
1 API key untuk banyak chain EVM via parameter chainid."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.fetchers.base import get_json

BASE_URL = "https://api.etherscan.io/v2/api"

CHAIN_IDS = {"ethereum": 1, "arbitrum": 42161, "base": 8453, "optimism": 10,
             "polygon": 137, "bsc": 56}

# Stablecoin utama dianggap $1 untuk estimasi USD transfer
STABLE_SYMBOLS = {"USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "USDS"}


def fetch_token_txs(address: str, api_key: str, chain: str = "ethereum",
                    offset: int = 100) -> tuple[list[dict], str, int]:
    params = {
        "chainid": CHAIN_IDS.get(chain, 1),
        "module": "account", "action": "tokentx",
        "address": address, "page": 1, "offset": offset,
        "sort": "desc", "apikey": api_key,
    }
    data, status = get_json(BASE_URL, params=params)
    if str(data.get("status")) == "0" and data.get("message") not in ("No transactions found",):
        # status 0 + result string = error API (key salah, rate limit, dll)
        if isinstance(data.get("result"), str):
            raise ValueError(f"Etherscan error: {data['result'][:200]}")
    result = data.get("result") or []
    return (result if isinstance(result, list) else []), BASE_URL + "?action=tokentx", status


def fetch_normal_txs(address: str, api_key: str, chain: str = "ethereum",
                     offset: int = 100) -> tuple[list[dict], str, int]:
    params = {
        "chainid": CHAIN_IDS.get(chain, 1),
        "module": "account", "action": "txlist",
        "address": address, "page": 1, "offset": offset,
        "sort": "desc", "apikey": api_key,
    }
    data, status = get_json(BASE_URL, params=params)
    result = data.get("result") or []
    return (result if isinstance(result, list) else []), BASE_URL + "?action=txlist", status


def parse_transfers(raw_txs: list[dict], wallet_address: str, wallet_label: str,
                    eth_price_usd: float | None = None,
                    kind: str = "token") -> list[dict[str, Any]]:
    """Normalisasi tx Etherscan -> dict siap masuk wallet_txs.
    kind: 'token' (tokentx) atau 'normal' (txlist, value dalam wei ETH)."""
    wallet = wallet_address.lower()
    out = []
    for tx in raw_txs:
        try:
            if kind == "token":
                decimals = int(tx.get("tokenDecimal") or 18)
                amount = int(tx["value"]) / (10 ** decimals)
                symbol = (tx.get("tokenSymbol") or "?").upper()
            else:
                amount = int(tx["value"]) / 1e18
                symbol = "ETH"
                if amount == 0:
                    continue
            frm, to = tx["from"].lower(), (tx.get("to") or "").lower()
            direction = "out" if frm == wallet else "in"
            counterparty = to if direction == "out" else frm
            if symbol in STABLE_SYMBOLS:
                amount_usd = amount
            elif symbol == "ETH" and eth_price_usd:
                amount_usd = amount * eth_price_usd
            else:
                amount_usd = None  # token lain: tanpa harga terpercaya, jangan mengarang
            out.append({
                "wallet_label": wallet_label,
                "wallet_address": wallet,
                "tx_hash": tx["hash"],
                "direction": direction,
                "counterparty": counterparty,
                "token_symbol": symbol,
                "amount": amount,
                "amount_usd": amount_usd,
                "block_time": datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc),
                "source_url": f"https://etherscan.io/tx/{tx['hash']}",
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out
