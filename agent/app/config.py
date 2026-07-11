"""
Config loader — sumber kebenaran: action_rules.yaml + watchlist.yaml + positions.yaml.
LLM/kode TIDAK boleh meng-hardcode nilai yang sudah ada di yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ACTION_BIAS_LEVELS = ["INFO", "MONITOR", "REVIEW", "RISK_OFF", "OPPORTUNITY"]


class ConfigError(ValueError):
    """Konfigurasi tidak valid — hentikan startup, jangan jalan dengan config rusak."""


@dataclass
class Settings:
    """Nilai dari .env — API key & koneksi. Tidak pernah dicetak ke log."""
    coingecko_api_key: str = ""
    etherscan_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'agent_signal.db'}"
    timezone: str = "Asia/Jakarta"
    # sumber M6: auto (binance->extended->coingecko) | binance | extended | coingecko
    derivatives_provider: str = "auto"
    extended_base_url: str = "https://api.starknet.extended.exchange"

    def masked(self) -> dict[str, str]:
        def m(v: str) -> str:
            return (v[:4] + "…" + v[-2:]) if len(v) > 8 else ("<set>" if v else "<empty>")
        return {
            "coingecko_api_key": m(self.coingecko_api_key),
            "etherscan_api_key": m(self.etherscan_api_key),
            "anthropic_api_key": m(self.anthropic_api_key),
            "telegram_bot_token": m(self.telegram_bot_token),
            "database_url": self.database_url.split("@")[-1],
        }


def load_settings(env_path: Path | None = None) -> Settings:
    load_dotenv(env_path or PROJECT_ROOT / ".env", override=False)
    return Settings(
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),
        etherscan_api_key=os.getenv("ETHERSCAN_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        database_url=os.getenv("DATABASE_URL") or f"sqlite:///{PROJECT_ROOT / 'agent_signal.db'}",
        timezone=os.getenv("TZ") or "Asia/Jakarta",
        derivatives_provider=(os.getenv("DERIVATIVES_PROVIDER") or "auto").lower(),
        extended_base_url=os.getenv("EXTENDED_BASE_URL")
        or "https://api.starknet.extended.exchange",
    )


# ---------------------------------------------------------------- yaml loaders

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"File konfigurasi tidak ditemukan: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} harus berupa mapping YAML, dapat: {type(data).__name__}")
    return data


@dataclass
class ActionRules:
    raw: dict[str, Any]

    @property
    def meta(self) -> dict[str, Any]:
        return self.raw["meta"]

    @property
    def levels(self) -> list[str]:
        return self.raw["action_bias"]["levels"]

    @property
    def escalation_rules(self) -> list[dict[str, Any]]:
        return self.raw["action_bias"]["escalation_rules"]

    def module(self, name: str) -> dict[str, Any]:
        try:
            return self.raw["modules"][name]
        except KeyError:
            raise ConfigError(f"Modul '{name}' tidak ada di action_rules.yaml") from None

    @property
    def modules(self) -> dict[str, Any]:
        return self.raw["modules"]

    @property
    def regime(self) -> dict[str, Any]:
        return self.raw["regime_score"]

    @property
    def notifications(self) -> dict[str, Any]:
        return self.raw["notifications"]

    @property
    def llm_guardrails(self) -> dict[str, Any]:
        return self.raw["llm_guardrails"]


def load_action_rules(path: Path | None = None) -> ActionRules:
    data = _load_yaml(path or PROJECT_ROOT / "action_rules.yaml")
    for key in ("meta", "action_bias", "modules", "regime_score", "notifications", "llm_guardrails"):
        if key not in data:
            raise ConfigError(f"action_rules.yaml: bagian '{key}' hilang")
    levels = data["action_bias"].get("levels", [])
    if levels != ACTION_BIAS_LEVELS:
        raise ConfigError(f"action_bias.levels tidak sesuai kontrak: {levels}")
    weights = data["regime_score"]["weights"]
    total = sum(weights.values())
    if total != 100:
        raise ConfigError(f"regime_score.weights harus total 100, dapat {total}")
    return ActionRules(raw=data)


@dataclass
class WatchAsset:
    symbol: str
    coingecko_id: str
    tier: str = "satellite"


@dataclass
class WatchProtocol:
    name: str
    llama_slug: str
    track: list[str] = field(default_factory=lambda: ["tvl"])


@dataclass
class WatchWallet:
    label: str
    address: str
    chain: str = "ethereum"
    category: str = "whale"
    threshold_usd_override: float | None = None


@dataclass
class Watchlist:
    assets: list[WatchAsset]
    protocols: list[WatchProtocol]
    wallets: list[WatchWallet]
    exchange_labels: list[dict[str, str]]

    def exchange_addresses(self) -> set[str]:
        return {e["address"].lower() for e in self.exchange_labels if e.get("address")}


def load_watchlist(path: Path | None = None) -> Watchlist:
    data = _load_yaml(path or PROJECT_ROOT / "watchlist.yaml")

    assets = []
    for a in data.get("assets") or []:
        if not a.get("symbol") or not a.get("coingecko_id"):
            raise ConfigError(f"watchlist assets: symbol/coingecko_id tidak boleh kosong: {a}")
        assets.append(WatchAsset(symbol=a["symbol"], coingecko_id=a["coingecko_id"],
                                 tier=a.get("tier", "satellite")))
    if len(assets) > 30:
        raise ConfigError("watchlist assets: maksimal 30 (kuota CoinGecko)")

    protocols = []
    for p in data.get("protocols") or []:
        if not p.get("name") or not p.get("llama_slug"):
            raise ConfigError(f"watchlist protocols: name/llama_slug tidak boleh kosong: {p}")
        protocols.append(WatchProtocol(name=p["name"], llama_slug=p["llama_slug"],
                                       track=p.get("track", ["tvl"])))
    if len(protocols) > 20:
        raise ConfigError("watchlist protocols: maksimal 20")

    wallets = []
    for w in data.get("wallets") or []:
        if not w.get("label") or not w.get("address"):
            raise ConfigError(f"watchlist wallets: wajib berlabel + address: {w}")
        wallets.append(WatchWallet(label=w["label"], address=w["address"],
                                   chain=w.get("chain", "ethereum"),
                                   category=w.get("category", "whale"),
                                   threshold_usd_override=w.get("threshold_usd_override")))
    if len(wallets) > 20:
        raise ConfigError("watchlist wallets: maksimal 20")

    ex = data.get("exchange_labels") or {}
    exchange_labels = [e for e in (ex if isinstance(ex, list) else ex.get("labels", []) or [])
                       if isinstance(e, dict)]
    # exchange_labels di template berbentuk mapping dgn key 'source' — item alamat opsional
    if isinstance(ex, dict):
        exchange_labels = [v for v in ex.values() if isinstance(v, dict) and v.get("address")]

    return Watchlist(assets=assets, protocols=protocols, wallets=wallets,
                     exchange_labels=exchange_labels)


@dataclass
class Position:
    """Posisi aktif user — hanya 'punya/tidak', tanpa nominal absolut (privasi)."""
    entity: str            # symbol aset ATAU llama_slug protokol
    kind: str = "asset"    # asset | protocol
    note: str = ""
    invalidation_personal: str = ""


def load_positions(path: Path | None = None) -> list[Position]:
    p = path or PROJECT_ROOT / "positions.yaml"
    if not p.exists():
        return []
    data = _load_yaml(p)
    out = []
    for item in data.get("positions") or []:
        if not item.get("entity"):
            raise ConfigError(f"positions.yaml: field 'entity' wajib: {item}")
        out.append(Position(entity=item["entity"], kind=item.get("kind", "asset"),
                            note=item.get("note", ""),
                            invalidation_personal=item.get("invalidation_personal", "")))
    return out


def position_entities(positions: list[Position]) -> set[str]:
    return {p.entity.lower() for p in positions}
