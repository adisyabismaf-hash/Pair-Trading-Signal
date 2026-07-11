"""L1+L2 — jalankan fetcher lalu simpan snapshot + raw response ke DB.
Setiap fungsi ingest_* dipanggil scheduler sesuai interval_minutes di yaml."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import models
from app.config import Settings, Watchlist
from app.fetchers import (alternativeme, binance, coingecko, defillama,
                          etherscan, extended)

logger = logging.getLogger(__name__)

DERIV_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def _save_raw(session: Session, source: str, endpoint: str, status: int, payload) -> None:
    # simpan ringkas: list panjang dipotong 50 item pertama agar DB tidak bengkak
    if isinstance(payload, list) and len(payload) > 50:
        payload = payload[:50]
    session.add(models.RawResponse(source=source, endpoint=endpoint,
                                   status_code=status, payload=payload))


def ingest_prices(session: Session, watchlist: Watchlist, settings: Settings) -> int:
    ids = [a.coingecko_id for a in watchlist.assets]
    if not ids:
        return 0
    data, url, status = coingecko.fetch_markets(ids, settings.coingecko_api_key)
    _save_raw(session, "coingecko", url, status, data)
    rows = coingecko.parse_markets(data)
    for r in rows:
        session.add(models.PriceSnapshot(**r, source_url=url))
    logger.info("prices: %d row", len(rows))
    return len(rows)


def ingest_tvl(session: Session, watchlist: Watchlist) -> int:
    slugs = [p.llama_slug for p in watchlist.protocols if "tvl" in p.track]
    if not slugs:
        return 0
    data, url, status = defillama.fetch_protocols()
    rows = defillama.parse_protocol_tvl(data, slugs)
    _save_raw(session, "defillama", url, status, rows)  # simpan subset watchlist saja
    for r in rows:
        session.add(models.TvlSnapshot(entity=r["entity"], entity_type="protocol",
                                       tvl_usd=r["tvl_usd"], source_url=url))
    # agregat TVL semua chain — input regime score (tvl_aggregate_delta)
    try:
        chains, url_c, _ = defillama.fetch_chains()
        total = sum(float(c.get("tvl") or 0) for c in chains)
        session.add(models.TvlSnapshot(entity="_aggregate_chains", entity_type="chain",
                                       tvl_usd=total, source_url=url_c))
    except Exception as e:
        logger.warning("chains aggregate gagal: %s", e)
    logger.info("tvl: %d row", len(rows))
    return len(rows)


def backfill_tvl_history(session: Session, watchlist: Watchlist, days: int = 9) -> int:
    """Cold start M1: isi riwayat TVL harian supaya delta 24j/7h bisa dihitung hari ini."""
    n = 0
    for p in watchlist.protocols:
        if "tvl" not in p.track:
            continue
        data, url, status = defillama.fetch_protocol_history(p.llama_slug)
        pts = defillama.parse_protocol_history(data, days=days)
        for pt in pts:
            session.add(models.TvlSnapshot(
                entity=p.llama_slug, entity_type="protocol", tvl_usd=pt["tvl_usd"],
                source_url=url,
                fetched_at=datetime.fromtimestamp(pt["ts_unix"], tz=timezone.utc)))
            n += 1
    logger.info("tvl backfill: %d row", n)
    return n


def ingest_fees(session: Session, watchlist: Watchlist) -> int:
    n = 0
    for p in watchlist.protocols:
        if "fees" not in p.track:
            continue
        try:
            data, url, status = defillama.fetch_fees_summary(p.llama_slug)
        except Exception as e:
            logger.warning("fees %s gagal: %s", p.llama_slug, e)
            continue
        parsed = defillama.parse_fees_summary(data)
        _save_raw(session, "defillama", url, status,
                  {k: parsed[k] for k in ("entity", "fees_24h_usd", "fees_7d_usd")})
        session.add(models.FeesSnapshot(entity=p.llama_slug,
                                        fees_24h_usd=parsed["fees_24h_usd"],
                                        revenue_24h_usd=None, source_url=url))
        n += 1
    logger.info("fees: %d row", n)
    return n


def ingest_stablecoins(session: Session) -> int:
    data, url, status = defillama.fetch_stablecoins()
    parsed = defillama.parse_stablecoins(data)
    _save_raw(session, "defillama", url, status, parsed["detail"])
    session.add(models.StablecoinSupply(total_supply_usd=parsed["total_supply_usd"],
                                        detail=parsed["detail"], source_url=url))
    return 1


def _deriv_binance(session: Session, symbols: list[str], settings: Settings | None) -> int:
    n = 0
    for sym in symbols:
        premium, url, status = binance.fetch_premium_index(sym)
        oi, _, _ = binance.fetch_open_interest(sym)
        parsed = binance.parse_derivatives(sym, premium, oi)
        _save_raw(session, "binance", url, status, {"premium": premium, "oi": oi})
        session.add(models.DerivativesSnapshot(**parsed, source_url=url))
        n += 1
    return n


def _deriv_extended(session: Session, symbols: list[str], settings: Settings | None) -> int:
    base = settings.extended_base_url if settings else extended.DEFAULT_BASE_URL
    n = 0
    for sym in symbols:
        stats, url, status = extended.fetch_market_stats(sym, base_url=base)
        parsed = extended.parse_derivatives(sym, stats)
        _save_raw(session, "extended", url, status, stats)
        session.add(models.DerivativesSnapshot(**parsed, source_url=url))
        n += 1
    return n


def _deriv_coingecko(session: Session, symbols: list[str], settings: Settings | None) -> int:
    if settings is None:
        raise ValueError("coingecko derivatives butuh settings (API key)")
    data, url, status = coingecko.fetch_derivatives(settings.coingecko_api_key)
    rows = coingecko.parse_derivatives(data, symbols)
    _save_raw(session, "coingecko", url, status, rows)
    for r in rows:
        session.add(models.DerivativesSnapshot(**r, source_url=url))
    return len(rows)


DERIV_PROVIDERS = {"binance": _deriv_binance, "extended": _deriv_extended,
                   "coingecko": _deriv_coingecko}
# urutan 'auto': Binance (sumber di yaml) -> Extended -> CoinGecko
DERIV_AUTO_ORDER = ["binance", "extended", "coingecko"]


def ingest_derivatives(session: Session, symbols: list[str] | None = None,
                       settings: Settings | None = None) -> int:
    """Sumber M6 sesuai DERIVATIVES_PROVIDER di .env. Provider tunggal tetap
    di-backup provider lain bila gagal (jaringan user memblokir domain exchange)."""
    symbols = symbols or DERIV_SYMBOLS
    choice = (settings.derivatives_provider if settings else "auto") or "auto"
    order = DERIV_AUTO_ORDER if choice == "auto" else \
        [choice] + [p for p in DERIV_AUTO_ORDER if p != choice]
    for provider in order:
        try:
            # savepoint: gagal di tengah -> row parsial provider ini di-rollback
            with session.begin_nested():
                n = DERIV_PROVIDERS[provider](session, symbols, settings)
            if n:
                logger.info("derivatives: %d row via %s", n, provider)
                return n
        except Exception as e:
            logger.warning("derivatives via %s gagal: %s", provider, e)
    logger.error("derivatives: semua provider gagal (%s)", " -> ".join(order))
    return 0


def ingest_fng(session: Session, limit: int = 1) -> int:
    records, url, status = alternativeme.fetch_fng(limit=limit)
    _save_raw(session, "alternativeme", url, status, records)
    rows = alternativeme.parse_fng(records)
    n = 0
    for r in rows:
        exists = session.execute(
            select(models.SentimentFng.id)
            .where(models.SentimentFng.fng_timestamp == r["fng_timestamp"])).first()
        if not exists:
            session.add(models.SentimentFng(**r))
            n += 1
    logger.info("fng: %d row baru", n)
    return n


def _latest_eth_price(session: Session) -> float | None:
    row = session.execute(
        select(models.PriceSnapshot.price_usd)
        .where(models.PriceSnapshot.coingecko_id == "ethereum")
        .order_by(desc(models.PriceSnapshot.fetched_at)).limit(1)).first()
    return row[0] if row else None


def ingest_wallet_txs(session: Session, watchlist: Watchlist, settings: Settings) -> int:
    if not watchlist.wallets:
        return 0
    eth_price = _latest_eth_price(session)
    n = 0
    for w in watchlist.wallets:
        try:
            token_txs, url_t, st = etherscan.fetch_token_txs(w.address, settings.etherscan_api_key, w.chain)
            normal_txs, url_n, _ = etherscan.fetch_normal_txs(w.address, settings.etherscan_api_key, w.chain)
        except Exception as e:
            logger.warning("wallet %s gagal: %s", w.label, e)
            continue
        _save_raw(session, "etherscan", url_t, st, token_txs[:20])
        parsed = (etherscan.parse_transfers(token_txs, w.address, w.label, eth_price, "token")
                  + etherscan.parse_transfers(normal_txs, w.address, w.label, eth_price, "normal"))
        for tx in parsed:
            exists = session.execute(
                select(models.WalletTx.id).where(
                    models.WalletTx.tx_hash == tx["tx_hash"],
                    models.WalletTx.wallet_address == tx["wallet_address"],
                    models.WalletTx.token_symbol == tx["token_symbol"],
                    models.WalletTx.direction == tx["direction"])).first()
            if not exists:
                session.add(models.WalletTx(**tx))
                n += 1
    logger.info("wallet_txs: %d row baru", n)
    return n
