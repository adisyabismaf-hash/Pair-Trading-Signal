"""Trading Signals dashboard (Streamlit Community Cloud).

Reads/writes the shared Postgres directly. The 24/7 scanning + Telegram alerts
run separately on GitHub Actions (see scanner.py) so signals keep firing even
when nobody has this dashboard open.
"""
import os

import streamlit as st

# --- Copy Streamlit secrets into the environment BEFORE importing core ---
# (core.config reads os.environ at import time.)
for _key in ("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EXTENDED_BASE_URL"):
    try:
        if _key in st.secrets:
            os.environ[_key] = str(st.secrets[_key])
    except Exception:
        pass

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from core.analytics import performance  # noqa: E402
from core.database import SessionLocal, init_db  # noqa: E402
from core.engine import close_signal_manual, run_scan  # noqa: E402
from core.models import (  # noqa: E402
    Signal, SignalStatus, Strategy, WatchedPair, WatchedSymbol,
)
from core import extended_client, telegram_notifier  # noqa: E402

st.set_page_config(page_title="Trading Signals", page_icon="⚡", layout="wide")

CSS = """
<style>
.block-container { padding-top: 2rem; max-width: 1200px; }
[data-testid="stMetricValue"] { font-size: 1.6rem; }
.live-badge {
  display:inline-block; padding:2px 10px; border-radius:999px;
  background:rgba(16,185,129,.15); color:#34d399; font-size:.72rem;
  font-weight:700; border:1px solid rgba(16,185,129,.3);
}
.hot { color:#fbbf24; font-weight:700; }
.up { color:#34d399; font-weight:700; }
.down { color:#f87171; font-weight:700; }
.muted { color:#94a3b8; font-size:.8rem; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_resource
def _bootstrap():
    """Create tables + seed once per server process."""
    init_db(seed=True)
    return True


try:
    _bootstrap()
except Exception as exc:
    st.error(f"❌ Tidak bisa terhubung ke database. Cek DATABASE_URL di Secrets.\n\n{exc}")
    st.stop()


# ---------------- helpers ----------------

def fmt(v, d=2):
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.{d}f}"
    if abs(v) < 1 and v != 0:
        return f"{v:.5g}"
    return f"{v:,.{d}f}"


@st.cache_data(ttl=10, show_spinner=False)
def live_prices() -> dict:
    try:
        return {m["name"]: m for m in extended_client.fetch_markets()}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def market_names() -> list[str]:
    try:
        return [m["name"] for m in extended_client.fetch_markets()]
    except Exception:
        return []


def direction_label(d: str) -> str:
    return {
        "LONG": "🟢 LONG", "SHORT": "🔴 SHORT",
        "LONG_SPREAD": "🟢 LONG SPREAD", "SHORT_SPREAD": "🔴 SHORT SPREAD",
    }.get(d, d)


def status_label(s: str) -> str:
    return {
        "OPEN": "🔵 OPEN", "CLOSED_TP": "✅ TP", "CLOSED_SL": "❌ SL",
        "CLOSED_EXIT": "🟣 EXIT", "CLOSED_MANUAL": "⚪ MANUAL", "EXPIRED": "🟠 EXPIRED",
    }.get(s, s)


def signals_dataframe(signals: list[Signal]) -> pd.DataFrame:
    rows = []
    for s in signals:
        rows.append({
            "Market": s.market,
            "Strategi": "🔁 Pair" if s.strategy == Strategy.PAIR_TRADING else "📐 MA50",
            "Arah": direction_label(s.direction.value),
            "Status": status_label(s.status.value),
            "Entry": fmt(s.entry_price, 4 if s.strategy == Strategy.PAIR_TRADING else 2),
            "SL": "—" if s.strategy == Strategy.PAIR_TRADING else fmt(s.stop_loss),
            "TP": "—" if s.strategy == Strategy.PAIR_TRADING else fmt(s.take_profit),
            "Z-entry": f"{s.entry_zscore:.2f}" if s.entry_zscore is not None else "—",
            "P&L %": None if s.pnl_pct is None else round(s.pnl_pct, 2),
            "Dibuka": s.opened_at.strftime("%d %b %H:%M") if s.opened_at else "—",
            "Ditutup": s.closed_at.strftime("%d %b %H:%M") if s.closed_at else "—",
        })
    return pd.DataFrame(rows)


# ---------------- sidebar nav ----------------

st.sidebar.markdown("## ⚡ Trading<span style='color:#34d399'>Signals</span>", unsafe_allow_html=True)
st.sidebar.caption("Pair Trading & MA50 Daily Retest")
page = st.sidebar.radio("Menu", ["📊 Dashboard", "👁️ Watchlist", "⚡ Sinyal", "📈 Performa"],
                        label_visibility="collapsed")

with st.sidebar:
    st.divider()
    if st.button("⚡ Scan Sekarang", width="stretch", type="primary"):
        with st.spinner("Memindai watchlist… (ambil data & hitung sinyal)"):
            db = SessionLocal()
            try:
                r = run_scan(db)
                st.session_state["notice"] = (
                    f"Scan selesai: {r['signals_created']} sinyal baru, {r['signals_closed']} ditutup."
                )
            except Exception as exc:
                st.session_state["notice"] = f"❌ Scan gagal: {exc}"
            finally:
                db.close()
        st.cache_data.clear()
        st.rerun()

    if st.button("🔔 Tes Telegram", width="stretch"):
        ok = telegram_notifier.send_message(
            "🔔 <b>Tes notifikasi</b>\n\nKoneksi Telegram dari dashboard Trading Signals berhasil! ✅")
        st.session_state["notice"] = "✅ Pesan tes terkirim!" if ok else "❌ Telegram gagal — cek token/chat ID."
    st.caption("Data: Extended Exchange · Alert: Telegram")

if st.session_state.get("notice"):
    st.sidebar.success(st.session_state.pop("notice"))


# ================= DASHBOARD =================

def page_dashboard():
    db = SessionLocal()
    try:
        perf = performance(db)
        pairs = db.execute(select(WatchedPair).order_by(WatchedPair.id)).scalars().all()
        symbols = db.execute(select(WatchedSymbol).order_by(WatchedSymbol.id)).scalars().all()
        signals = db.execute(select(Signal).order_by(Signal.opened_at.desc()).limit(12)).scalars().all()
    finally:
        db.close()

    o = perf["overall"]
    prices = live_prices()

    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown("# 📊 Dashboard <span class='live-badge'>● LIVE</span>", unsafe_allow_html=True)
    with c2:
        st.caption(f"🕐 {pd.Timestamp.now(tz='Asia/Jakarta').strftime('%H:%M:%S')} WIB")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Winrate", f"{o['winrate_pct']}%" if o["winrate_pct"] is not None else "—",
              f"{o['wins']}W / {o['losses']}L")
    m2.metric("Total P&L", f"{o['total_pnl_pct']:+.2f}%")
    m3.metric("Sinyal Aktif", o["open"])
    m4.metric("Dipantau", f"{sum(p.is_active for p in pairs)} pair · {sum(s.is_active for s in symbols)} market")

    st.divider()
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("🔁 Pair Trading — spread")
        if not pairs:
            st.caption("Belum ada pair. Tambahkan di menu Watchlist.")
        for p in pairs:
            z = p.last_zscore
            hot = z is not None and abs(z) >= p.entry_zscore
            zcls = "hot" if hot else "muted"
            pa = prices.get(p.base_market, {}).get("last_price")
            pb = prices.get(p.quote_market, {}).get("last_price")
            ztxt = f"z = {z:+.2f}" if z is not None else "z = —"
            nonaktif = "" if p.is_active else " _(nonaktif)_"
            st.markdown(
                f"**{p.base_market}** vs **{p.quote_market}**{nonaktif} "
                f"<span class='{zcls}' style='float:right'>{ztxt}</span>",
                unsafe_allow_html=True,
            )
            # z-score bar: map -4..+4 to 0..1
            if z is not None:
                st.progress(min(1.0, max(0.0, (max(-4, min(4, z)) + 4) / 8)))
            corr = f"{p.last_correlation:.2f}" if p.last_correlation is not None else "—"
            st.caption(f"harga {fmt(pa)} / {fmt(pb)} · korelasi {corr}")
            if hot:
                st.warning("⚡ Spread di zona entry! Sinyal dibuat pada scan berikutnya.", icon="⚡")

    with col_right:
        st.subheader("📐 MA50 Daily Retest")
        if not symbols:
            st.caption("Belum ada market. Tambahkan di menu Watchlist.")
        for s in symbols:
            live = prices.get(s.market, {}).get("last_price") or s.last_price
            trend = s.last_trend or "FLAT"
            tcls = "up" if trend == "UP" else "down" if trend == "DOWN" else "muted"
            tsym = "▲ UPTREND" if trend == "UP" else "▼ DOWNTREND" if trend == "DOWN" else "— FLAT"
            st.markdown(
                f"**{s.market}**{'' if s.is_active else ' _(nonaktif)_'} "
                f"<span class='{tcls}' style='float:right'>{tsym}</span>",
                unsafe_allow_html=True,
            )
            st.caption(f"harga {fmt(live)} · MA50 {fmt(s.last_ma50)} · retest {s.last_retest_count or 0}/2")

    st.divider()
    st.subheader("⚡ Sinyal Terbaru")
    if signals:
        st.dataframe(signals_dataframe(signals), width="stretch", hide_index=True)
    else:
        st.caption("Belum ada sinyal. Muncul otomatis saat syarat strategi terpenuhi.")


# ================= WATCHLIST =================

def page_watchlist():
    st.markdown("# 👁️ Watchlist")
    names = market_names()
    if names:
        st.caption(f"{len(names)} market tersedia di exchange.")

    db = SessionLocal()
    try:
        # ----- Pair trading -----
        st.subheader("🔁 Pair Trading")
        with st.form("add_pair", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1, 1])
            base = c1.text_input("Base market", placeholder="BTC-USD").strip().upper()
            quote = c2.text_input("Quote market", placeholder="ETH-USD").strip().upper()
            lookback = c3.number_input("Lookback", 20, 200, 60)
            entry_z = c4.number_input("Entry z", 1.0, 5.0, 2.0, 0.5)
            exit_z = c5.number_input("Exit z", 0.0, 2.0, 0.5, 0.1)
            if st.form_submit_button("➕ Tambah Pair", type="primary"):
                err = None
                if not base or not quote:
                    err = "Isi kedua market."
                elif base == quote:
                    err = "Base dan quote tidak boleh sama."
                elif names and (base not in names or quote not in names):
                    bad = base if base not in names else quote
                    err = f"Market '{bad}' tidak ditemukan di exchange."
                elif db.execute(select(WatchedPair).where(
                        WatchedPair.base_market == base, WatchedPair.quote_market == quote)).scalar_one_or_none():
                    err = "Pair ini sudah ada."
                if err:
                    st.error(err)
                else:
                    db.add(WatchedPair(base_market=base, quote_market=quote, lookback=int(lookback),
                                       entry_zscore=float(entry_z), exit_zscore=float(exit_z)))
                    db.commit()
                    st.rerun()

        pairs = db.execute(select(WatchedPair).order_by(WatchedPair.id)).scalars().all()
        for p in pairs:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            zt = f"z={p.last_zscore:+.2f}" if p.last_zscore is not None else "z=—"
            corr = f"{p.last_correlation:.2f}" if p.last_correlation is not None else "—"
            c1.markdown(f"**{p.base_market}** vs **{p.quote_market}**  \n"
                        f"<span class='muted'>{zt} · korelasi {corr} · "
                        f"entry ±{p.entry_zscore} / exit ±{p.exit_zscore}</span>", unsafe_allow_html=True)
            c2.write("🟢 Aktif" if p.is_active else "⚪ Nonaktif")
            if c3.button("Toggle", key=f"tp{p.id}"):
                p.is_active = not p.is_active
                db.commit()
                st.rerun()
            if c4.button("🗑️", key=f"dp{p.id}"):
                db.delete(p)
                db.commit()
                st.rerun()

        st.divider()

        # ----- MA50 symbols -----
        st.subheader("📐 Trading Tradisional — MA50 Daily Retest")
        with st.form("add_symbol", clear_on_submit=True):
            c1, c2 = st.columns([4, 1])
            market = c1.text_input("Market", placeholder="SOL-USD",
                                   label_visibility="collapsed").strip().upper()
            if c2.form_submit_button("➕ Tambah", type="primary"):
                if not market:
                    st.error("Isi nama market.")
                elif names and market not in names:
                    st.error(f"Market '{market}' tidak ditemukan di exchange.")
                elif db.execute(select(WatchedSymbol).where(
                        WatchedSymbol.market == market)).scalar_one_or_none():
                    st.error("Market ini sudah ada.")
                else:
                    db.add(WatchedSymbol(market=market))
                    db.commit()
                    st.rerun()

        symbols = db.execute(select(WatchedSymbol).order_by(WatchedSymbol.id)).scalars().all()
        for s in symbols:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            c1.markdown(f"**{s.market}**  \n<span class='muted'>harga {fmt(s.last_price)} · "
                        f"MA50 {fmt(s.last_ma50)} · {s.last_trend or 'FLAT'} · "
                        f"retest {s.last_retest_count or 0}/2</span>", unsafe_allow_html=True)
            c2.write("🟢 Aktif" if s.is_active else "⚪ Nonaktif")
            if c3.button("Toggle", key=f"ts{s.id}"):
                s.is_active = not s.is_active
                db.commit()
                st.rerun()
            if c4.button("🗑️", key=f"ds{s.id}"):
                db.delete(s)
                db.commit()
                st.rerun()
    finally:
        db.close()


# ================= SINYAL =================

def page_signals():
    st.markdown("# ⚡ Sinyal")
    c1, c2 = st.columns(2)
    strat = c1.selectbox("Strategi", ["Semua", "PAIR_TRADING", "MA50_RETEST"])
    stat = c2.selectbox("Status", ["Semua", "OPEN", "CLOSED_TP", "CLOSED_SL",
                                    "CLOSED_EXIT", "CLOSED_MANUAL", "EXPIRED"])

    db = SessionLocal()
    try:
        stmt = select(Signal).order_by(Signal.opened_at.desc()).limit(300)
        if strat != "Semua":
            stmt = stmt.where(Signal.strategy == Strategy(strat))
        if stat != "Semua":
            stmt = stmt.where(Signal.status == SignalStatus(stat))
        signals = db.execute(stmt).scalars().all()

        if signals:
            st.dataframe(signals_dataframe(signals), width="stretch", hide_index=True)
        else:
            st.caption("Tidak ada sinyal untuk filter ini.")

        open_sigs = [s for s in signals if s.status == SignalStatus.OPEN]
        if open_sigs:
            st.divider()
            st.subheader("Tutup posisi manual")
            for s in open_sigs:
                c1, c2 = st.columns([4, 1])
                c1.write(f"#{s.id} · {s.market} · {direction_label(s.direction.value)}")
                if c2.button("Tutup", key=f"close{s.id}"):
                    try:
                        close_signal_manual(db, s.id)
                        st.success(f"Sinyal #{s.id} ditutup.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Gagal menutup: {exc}")
    finally:
        db.close()


# ================= PERFORMA =================

def page_performance():
    st.markdown("# 📈 Performa")
    db = SessionLocal()
    try:
        perf = performance(db)
    finally:
        db.close()

    o = perf["overall"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Winrate", f"{o['winrate_pct']}%" if o["winrate_pct"] is not None else "—",
              f"{o['wins']} menang · {o['losses']} kalah")
    m2.metric("Total P&L", f"{o['total_pnl_pct']:+.2f}%")
    m3.metric("Rata-rata/Sinyal",
              f"{o['avg_pnl_pct']:+.2f}%" if o["avg_pnl_pct"] is not None else "—")
    m4.metric("Sinyal Selesai", o["closed"], f"{o['open']} berjalan")

    st.divider()
    st.subheader("📈 Kurva Ekuitas (kumulatif P&L %)")
    curve = perf["equity_curve"]
    if len(curve) >= 2:
        df = pd.DataFrame(curve)
        df["t"] = pd.to_datetime(df["t"])
        st.line_chart(df.set_index("t")["cum_pnl_pct"], height=260)
    else:
        st.caption("Kurva muncul setelah minimal 2 sinyal selesai.")

    st.divider()
    st.subheader("Per Strategi")
    rows = []
    for s in perf["per_strategy"]:
        label = "🔁 Pair Trading" if s["strategy"] == "PAIR_TRADING" else "📐 MA50 Retest"
        rows.append({
            "Strategi": label, "Total": s["total"], "Open": s["open"], "Selesai": s["closed"],
            "Win": s["wins"], "Loss": s["losses"],
            "Winrate": f"{s['winrate_pct']}%" if s["winrate_pct"] is not None else "—",
            "Total P&L": f"{s['total_pnl_pct']:+.2f}%",
            "Avg": f"{s['avg_pnl_pct']:+.2f}%" if s["avg_pnl_pct"] is not None else "—",
            "Best": f"{s['best_pnl_pct']:+.2f}%" if s["best_pnl_pct"] is not None else "—",
            "Worst": f"{s['worst_pnl_pct']:+.2f}%" if s["worst_pnl_pct"] is not None else "—",
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ---------------- router ----------------

if page.startswith("📊"):
    page_dashboard()
elif page.startswith("👁️"):
    page_watchlist()
elif page.startswith("⚡"):
    page_signals()
else:
    page_performance()
