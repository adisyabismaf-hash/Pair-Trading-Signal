"""Scan pipeline for Pair Trading Tools v2, decoupled from the CLI.

`scan_pipeline()` runs fetch -> align -> correlation screening -> backtest and returns
plain dataframes/lists, so it can be driven by scanner.py (CLI, prints + writes reports)
or by the v1 Streamlit dashboard (cloud/streamlit_app.py) without shelling out.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from config import settings
from data_sources import SOURCES, DataSourceError
from universe import DEFAULT_UNIVERSE, SPREAD_PAIRS
from analytics import correlation_report, qualifying_pairs, backtest_pair
from analytics.backtest import summarize_trades

logger = logging.getLogger("pipeline")


@dataclass
class ScanResult:
    ok: bool
    reason: str = ""
    aligned: pd.DataFrame = field(default_factory=pd.DataFrame)
    corr_level: pd.DataFrame = field(default_factory=pd.DataFrame)
    corr_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    qualified: pd.DataFrame = field(default_factory=pd.DataFrame)
    trade_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: list = field(default_factory=list)
    symbols_fetched: list = field(default_factory=list)
    symbols_failed: list = field(default_factory=list)
    shortened_windows: list = field(default_factory=list)
    skipped_short_history: list = field(default_factory=list)
    spread_pair_labels: dict = field(default_factory=dict)  # "A/B" -> spread label


def fetch_universe(symbol_specs: list[tuple[str, str, str]], limit: int) -> dict[str, pd.Series]:
    """symbol_specs: list of (symbol, source_name, source_ticker). Returns symbol -> close series
    (indexed by ms-epoch timestamp), skipping any symbol whose fetch fails."""
    out = {}
    for symbol, source_name, ticker in symbol_specs:
        source = SOURCES.get(source_name)
        if source is None:
            logger.warning("Skipping %s: unknown source %r", symbol, source_name)
            continue
        try:
            candles = source.fetch_daily_closes(ticker, limit)
            s = pd.Series({c.t_ms: c.close for c in candles}).sort_index()
            s = s[~s.index.duplicated(keep="last")]
            out[symbol] = s
            logger.info("Fetched %s (%s/%s): %d candles, %s -> %s", symbol, source_name, ticker,
                       len(s), pd.to_datetime(s.index.min(), unit="ms").date(),
                       pd.to_datetime(s.index.max(), unit="ms").date())
        except DataSourceError as exc:
            logger.warning("Skipping %s (%s/%s): %s", symbol, source_name, ticker, exc)
    return out


def align_closes(series_by_symbol: dict[str, pd.Series]) -> pd.DataFrame:
    """Outer-join all series onto a shared daily grid. Gaps inside a symbol's history
    (weekends/holidays for tradfi) are forward-filled; days before a symbol listed stay
    NaN, so a short-history symbol (e.g. XAUT, listed 2026-03) no longer truncates the
    whole universe — each pair is later evaluated on its own overlap."""
    if not series_by_symbol:
        return pd.DataFrame()
    day_ms = 86_400_000
    # Venues stamp their daily candle at different times within the day (Extended 00:00
    # UTC, Binance close-time 23:59:59.999, Yahoo the exchange's open) — floor every
    # timestamp to its UTC day so all sources land on one shared grid.
    normalized = {}
    for symbol, s in series_by_symbol.items():
        ns = pd.Series(s.values, index=(s.index // day_ms) * day_ms)
        normalized[symbol] = ns[~ns.index.duplicated(keep="last")]
    start = min(s.index.min() for s in normalized.values())
    end = min(s.index.max() for s in normalized.values())  # don't ffill past a stale source's last candle
    if start >= end:
        return pd.DataFrame()
    idx = list(range(start, end + 1, day_ms))
    df = pd.DataFrame(index=idx)
    for symbol, s in normalized.items():
        col = s.reindex(idx)
        first_valid = col.first_valid_index()
        if first_valid is not None:
            col.loc[first_valid:] = col.loc[first_valid:].ffill()
        df[symbol] = col
    return df


def resolve_symbol_specs(universe_names: list[str]) -> list[tuple[str, str, str]]:
    specs = []
    for name in universe_names:
        if name == "spread":
            continue  # spread pairs pull their symbols from whichever universes are selected
        group = DEFAULT_UNIVERSE.get(name)
        if group is None:
            logger.warning("Unknown universe %r, skipping", name)
            continue
        specs.extend(group)
    return specs


RISK_EMOJI = {"Low": "🟢", "Moderate": "🟡", "High": "🔴"}


def risk_rating(corr_level: float | None, corr_returns: float | None) -> str:
    """Low / Moderate / High trading risk from the two correlations.

    Returns correlation (day-to-day co-movement) is the honest measure, so it sets the
    base tier. A large gap where the level correlation is much higher than the returns
    correlation means the headline number overstates the relationship (two things merely
    trending together) — that bumps the risk up one tier."""
    # Round to the 2 decimals the UI shows so the rating never contradicts the number
    # a user sees (e.g. displayed "returns 0.80" always rates as the >=0.80 tier).
    lv = round(abs(corr_level), 2) if corr_level is not None else 0.0
    rt = round(abs(corr_returns), 2) if corr_returns is not None else 0.0
    tier = 0 if rt >= 0.80 else (1 if rt >= 0.60 else 2)   # Low / Moderate / High
    if lv - rt >= 0.25:                                     # level overstates the link
        tier = min(2, tier + 1)
    return ("Low", "Moderate", "High")[tier]


def build_reco_items(result: "ScanResult", lookback: int = 60) -> list[dict]:
    """Turn a correlation scan into recommendation dicts, each enriched with the pair's
    current spread z-score and a same-underlying flag. Callers add DB-specific fields
    (e.g. in_watchlist) afterwards."""
    from analytics import current_zscore
    from universe import build_symbol_table, same_underlying

    table = build_symbol_table()
    items = []
    for row in result.qualified.itertuples():
        src_a, tick_a = table.get(row.symbol_a, (None, None))
        src_b, tick_b = table.get(row.symbol_b, (None, None))
        addable = src_a == "extended" and src_b == "extended"
        try:
            z = current_zscore(result.aligned, row.symbol_a, row.symbol_b, lookback)
        except Exception:
            z = None
        items.append({
            "symbol_a": row.symbol_a, "symbol_b": row.symbol_b,
            "corr_level": float(row.corr_level), "corr_returns": float(row.corr_returns),
            "risk": risk_rating(row.corr_level, row.corr_returns),
            "zscore": z, "same_underlying": same_underlying(row.symbol_a, row.symbol_b),
            "base_market": tick_a if addable else None,
            "quote_market": tick_b if addable else None,
            "addable": addable, "source_a": src_a, "source_b": src_b,
        })
    return items


def top_recommendations(items: list[dict], limit: int = 5) -> list[dict]:
    """Best `limit` pairs by |correlation|, excluding same-underlying wrapper pairs
    (e.g. PAXG/XAUT are both gold)."""
    diversified = [i for i in items if not i.get("same_underlying")]
    diversified.sort(key=lambda i: abs(i["corr_level"]), reverse=True)
    return diversified[:limit]


def signal_legs(symbol_a: str, symbol_b: str, z: float | None,
                signal_z: float = 2.0) -> str:
    """Explicit long/short legs when the spread z-score is at an entry extreme, else ''.

    spread = log(A) - h·log(B):
      z >= +signal_z -> spread mahal -> SHORT A / LONG B
      z <= -signal_z -> spread murah -> LONG A / SHORT B
    """
    if z is None:
        return ""
    if z >= signal_z:
        return f"🔴 SHORT {symbol_a} / LONG {symbol_b}"
    if z <= -signal_z:
        return f"🟢 LONG {symbol_a} / SHORT {symbol_b}"
    return ""


def pairs_with_symbol(items: list[dict], symbol: str) -> list[dict]:
    """Every qualifying pair that has `symbol` as one leg, sorted by |correlation| desc."""
    s = symbol.upper()
    out = [i for i in items if s in (i["symbol_a"].upper(), i["symbol_b"].upper())]
    out.sort(key=lambda i: abs(i["corr_level"]), reverse=True)
    return out


def build_focus_items(result: "ScanResult", symbol: str, lookback: int = 60) -> list[dict]:
    """Reco items for EVERY pair involving `symbol`, taken from the full correlation
    matrix (not just pairs above the screening threshold) so a named reference pair like
    BTC/ETH is available even when its correlation dips below the cutoff. Sorted by
    |correlation| desc."""
    from analytics import current_zscore
    from universe import build_symbol_table, same_underlying

    if symbol not in result.corr_level.columns:
        return []
    table = build_symbol_table()
    out = []
    for other in result.corr_level.columns:
        if other == symbol:
            continue
        cl = result.corr_level.loc[symbol, other]
        if pd.isna(cl):
            continue
        cr = result.corr_returns.loc[symbol, other]
        src_a, tick_a = table.get(symbol, (None, None))
        src_b, tick_b = table.get(other, (None, None))
        addable = src_a == "extended" and src_b == "extended"
        try:
            z = current_zscore(result.aligned, symbol, other, lookback)
        except Exception:
            z = None
        cr_val = float(cr) if not pd.isna(cr) else None
        out.append({
            "symbol_a": symbol, "symbol_b": other,
            "corr_level": float(cl), "corr_returns": cr_val,
            "risk": risk_rating(float(cl), cr_val),
            "zscore": z, "same_underlying": same_underlying(symbol, other),
            "base_market": tick_a if addable else None,
            "quote_market": tick_b if addable else None,
            "addable": addable, "source_a": src_a, "source_b": src_b,
        })
    out.sort(key=lambda i: abs(i["corr_level"]), reverse=True)
    return out


def _reco_line(it: dict, signal_z: float, signals: list) -> str:
    """One bullet: pair + z-score, then both correlations and a risk rating, plus an
    explicit long/short leg if |z| >= signal_z."""
    z = it.get("zscore")
    ztxt = f"z {z:+.2f}" if z is not None else "z —"
    wl = " 👁️" if it.get("in_watchlist") else ""
    cr = it.get("corr_returns")
    ret = f"{cr:.2f}" if cr is not None else "—"
    risk = it.get("risk", "—")
    remoji = RISK_EMOJI.get(risk, "")
    line = (f"• <b>{it['symbol_a']}/{it['symbol_b']}</b> · {ztxt}{wl}\n"
            f"   korelasi {it['corr_level']:.2f} · returns {ret} · risiko {remoji} {risk}")
    legs = signal_legs(it["symbol_a"], it["symbol_b"], z, signal_z)
    if legs:
        line += f"\n   → SINYAL: {legs}"
        signals.append(legs)
    return line


# Reference pairs always shown in the focus section even if correlation dips below the
# screening threshold (user asked for BTC/ETH explicitly).
PINNED_PAIRS = {frozenset(("BTC", "ETH"))}


def format_reco_telegram(items: list[dict], threshold: float, *,
                         limit: int = 5, signal_z: float = 2.0,
                         focus_symbol: str = "BTC",
                         focus_items: list[dict] | None = None) -> str:
    """Telegram message: top `limit` diversified pairs, then a dedicated section for every
    pair involving `focus_symbol` (default BTC), each with correlation, z-score, and an
    explicit long/short signal when |z| >= signal_z.

    `focus_items` (from build_focus_items) lets the focus section include pairs below the
    threshold — used to always show BTC/ETH. Without it, the section falls back to the
    focus-symbol pairs already in `items` (i.e. only those >= threshold)."""
    top = top_recommendations(items, limit)
    signals: list[str] = []

    lines = ["⭐ <b>REKOMENDASI PAIR TRADING</b>",
             f"Top {limit} korelasi {threshold:.2f}–1.00 (aset berbeda):", ""]
    for n, it in enumerate(top, 1):
        lines.append(f"{n}. " + _reco_line(it, signal_z, signals)[2:])  # drop leading "• "

    # Focus-symbol section (BTC pairs): threshold-passing pairs + any pinned reference
    # pair (BTC/ETH), skipping ones already listed in the top block.
    pool = focus_items if focus_items is not None else pairs_with_symbol(items, focus_symbol)
    top_keys = {frozenset((it["symbol_a"], it["symbol_b"])) for it in top}
    focus = []
    for it in pool:
        key = frozenset((it["symbol_a"], it["symbol_b"]))
        if key in top_keys:
            continue
        if abs(it["corr_level"]) >= threshold or key in PINNED_PAIRS:
            focus.append(it)
    if focus:
        lines += ["", f"₿ Pair {focus_symbol}:"]
        lines += [_reco_line(it, signal_z, signals) for it in focus]

    if signals:
        lines += ["", f"⚡ {len(signals)} pair di zona entry (|z| ≥ {signal_z:.0f}):"]
        lines += [f"• {legs}" for legs in signals]
    else:
        lines += ["", f"Belum ada yang tembus |z| ≥ {signal_z:.0f} — pantau saja dulu."]
    return "\n".join(lines)


def scan_pipeline(universe_names: list[str], *,
                  corr_threshold: float = settings.corr_threshold,
                  corr_method: str = settings.corr_method,
                  lookback: int = settings.lookback_days,
                  backtest_days: int = settings.backtest_days,
                  min_backtest_days: int = settings.min_backtest_days,
                  entry_zscore: float = settings.entry_zscore,
                  exit_zscore: float = settings.exit_zscore,
                  stop_zscore: float = settings.stop_zscore,
                  max_holding_days: int = settings.max_holding_days,
                  candle_limit: int = settings.candle_limit,
                  run_backtest: bool = True) -> ScanResult:
    include_spread = "spread" in universe_names
    symbol_specs = resolve_symbol_specs(universe_names)

    # Spread pairs may reference symbols outside the selected universes (e.g. BRENT/PAXG/
    # XAUT even if you only asked for crypto,tradfi) -- pull those in too.
    if include_spread:
        wanted = {s for pair in SPREAD_PAIRS for s in (pair[1], pair[2])}
        have = {s for s, _, _ in symbol_specs}
        missing = wanted - have
        if missing:
            all_specs = {s: (src, tk) for group in DEFAULT_UNIVERSE.values() for s, src, tk in group}
            for s in missing:
                if s in all_specs:
                    src, tk = all_specs[s]
                    symbol_specs.append((s, src, tk))

    logger.info("Universe: %d symbols requested", len(symbol_specs))
    series_by_symbol = fetch_universe(symbol_specs, limit=candle_limit)
    symbols_failed = sorted({s for s, _, _ in symbol_specs} - set(series_by_symbol.keys()))
    if len(series_by_symbol) < 2:
        return ScanResult(ok=False, reason="insufficient data",
                          symbols_fetched=sorted(series_by_symbol), symbols_failed=symbols_failed)

    aligned = align_closes(series_by_symbol)
    if aligned.empty:
        return ScanResult(ok=False, reason="no overlap",
                          symbols_fetched=sorted(series_by_symbol), symbols_failed=symbols_failed)
    logger.info("Aligned window: %s -> %s (%d days), symbols: %s",
               pd.to_datetime(aligned.index.min(), unit="ms").date(),
               pd.to_datetime(aligned.index.max(), unit="ms").date(),
               len(aligned), list(aligned.columns))

    logp = np.log(aligned)
    corr = correlation_report(logp)
    qualified = qualifying_pairs(logp, threshold=corr_threshold, method=corr_method)

    needed_days = lookback + backtest_days
    pairs_to_backtest = [(row.symbol_a, row.symbol_b, "correlation") for row in qualified.itertuples()]
    spread_pair_labels = {}
    if include_spread:
        queued = {frozenset((a, b)) for a, b, _ in pairs_to_backtest}
        for label, a, b, note in SPREAD_PAIRS:
            if a in aligned.columns and b in aligned.columns:
                spread_pair_labels[f"{a}/{b}"] = label
            if frozenset((a, b)) in queued:
                continue  # already qualified via correlation screening — don't backtest twice
            if a in aligned.columns and b in aligned.columns:
                pairs_to_backtest.append((a, b, f"spread:{label}"))
            else:
                logger.info("Spread pair %r skipped: %s or %s not in aligned data", label, a, b)

    all_trades = []
    skipped_short_history = []
    shortened_windows = []
    if not run_backtest:
        # Correlation screening only (e.g. the "Recommended" pages) — much faster,
        # skips the per-pair backtest entirely.
        pairs_to_backtest = []
    for a, b, tag in pairs_to_backtest:
        # Each pair is backtested on its own overlapping history, so one late-listed
        # symbol doesn't shrink the window for everyone else.
        pair_df = aligned[[a, b]].dropna()
        available = len(pair_df)
        effective_backtest = backtest_days
        if available < needed_days:
            # Keep the lookback intact (it drives the hedge-ratio/z-score fit) and
            # shorten the simulated period instead, down to a floor of min_backtest_days.
            effective_backtest = available - lookback
            if effective_backtest < min_backtest_days:
                skipped_short_history.append(
                    f"{a}/{b} ({tag}): {available} overlapping days < lookback "
                    f"{lookback} + min backtest {min_backtest_days}")
                continue
            shortened_windows.append(f"{a}/{b} ({tag}): backtest window {effective_backtest}d "
                                     f"instead of {backtest_days}d ({available} days overlap)")
        try:
            trades = backtest_pair(
                pair_df, a, b,
                lookback_days=lookback, backtest_days=effective_backtest,
                entry_zscore=entry_zscore, exit_zscore=exit_zscore,
                stop_zscore=stop_zscore, max_holding_days=max_holding_days,
            )
            all_trades.extend(trades)
        except ValueError as exc:
            skipped_short_history.append(f"{a}/{b} ({tag}): {exc}")

    if shortened_windows:
        logger.warning("Backtest window shortened for %d pair(s) with thin history:\n  %s",
                       len(shortened_windows), "\n  ".join(shortened_windows))
    if skipped_short_history:
        logger.warning("Backtest skipped for %d pair(s) with insufficient history "
                       "(need %d aligned days):\n  %s",
                       len(skipped_short_history), needed_days,
                       "\n  ".join(skipped_short_history))

    return ScanResult(
        ok=True,
        aligned=aligned,
        corr_level=corr["level"],
        corr_returns=corr["returns"],
        qualified=qualified,
        trade_summary=summarize_trades(all_trades),
        trades=all_trades,
        symbols_fetched=sorted(series_by_symbol),
        symbols_failed=symbols_failed,
        shortened_windows=shortened_windows,
        skipped_short_history=skipped_short_history,
        spread_pair_labels=spread_pair_labels,
    )
