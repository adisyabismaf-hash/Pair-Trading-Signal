"""Trading Command Center — dashboard gabungan Pair Trading + Agent Signal.

Satu platform di Streamlit Cloud, membaca shared Postgres (Neon):
  - Pair trading & MA50: scan 24/7 via GitHub Actions (scanner.py + scan.yml)
  - Agent Signal on-chain: ingest + outlook 07:00 WIB via GitHub Actions (agent_runner.py + agent.yml)
Pengaturan watchlist keduanya dikelola dari halaman Pengaturan (tersimpan di DB).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# --- Secrets -> env SEBELUM import core/agent (keduanya baca os.environ saat import) ---
for _key in ("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EXTENDED_BASE_URL",
             "COINGECKO_API_KEY", "ETHERSCAN_API_KEY", "ANTHROPIC_API_KEY",
             "DERIVATIVES_PROVIDER"):
    try:
        if _key in st.secrets:
            os.environ[_key] = str(st.secrets[_key])
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agent"))

# Pair Trading Tools v2 (multi-venue correlation screening) lives in ./pair-trading-v2.
# Override location with the V2_DIR env var if deployed elsewhere.
V2_DIR = os.path.abspath(os.environ.get("V2_DIR", str(ROOT / "pair-trading-v2")))

from datetime import datetime, timedelta, timezone  # noqa: E402

import pandas as pd  # noqa: E402
from sqlalchemy import desc, select  # noqa: E402

from core import extended_client, telegram_notifier  # noqa: E402
from core.analytics import performance  # noqa: E402
from core.database import SessionLocal, init_db  # noqa: E402
from core.engine import close_signal_manual, run_scan  # noqa: E402
from core.models import (Signal, SignalStatus, Strategy, WatchedPair,  # noqa: E402
                         WatchedSymbol)

from app import models as am  # noqa: E402  (Agent Signal)
from app import reporting  # noqa: E402
from app.config import load_action_rules, load_positions, load_settings  # noqa: E402
from app.db import init_db as agent_init_db  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.notify.notifier import get_notifier  # noqa: E402

import agent_store  # noqa: E402
from agent_runner import PrefixNotifier  # noqa: E402

st.set_page_config(page_title="Trading Command Center", page_icon="🧭", layout="wide")

WIB = "Asia/Jakarta"

# ============================================================ CSS (gaya frontend Next.js)

CSS = """
<style>
.block-container { padding-top: 1.6rem; max-width: 1280px; }
:root {
  --card: #0d1526; --card-border: #1c2941; --row: rgba(30,41,59,.38);
  --t1: #e2e8f0; --t2: #94a3b8; --t3: #64748b;
  --emerald: #34d399; --sky: #38bdf8; --amber: #fbbf24; --red: #f87171;
}
.num { font-family: ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:2px 9px; border-radius:999px;
        font-size:10.5px; font-weight:700; letter-spacing:.04em; }
.pill.on  { background:rgba(16,185,129,.12); color:var(--emerald); border:1px solid rgba(16,185,129,.3); }
.pill.off { background:rgba(248,113,113,.12); color:var(--red); border:1px solid rgba(248,113,113,.3); }
.pill.info    { background:rgba(100,116,139,.15); color:var(--t2); border:1px solid rgba(100,116,139,.35); }
.pill.monitor { background:rgba(56,189,248,.12); color:var(--sky); border:1px solid rgba(56,189,248,.3); }
.pill.review  { background:rgba(251,191,36,.12); color:var(--amber); border:1px solid rgba(251,191,36,.35); }
.dot { width:7px; height:7px; border-radius:50%; background:var(--emerald); }
@keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:.35} }
.live-dot { animation: pulse-dot 1.6s ease-in-out infinite; }

.tcc-card { background:var(--card); border:1px solid var(--card-border); border-radius:13px;
            padding:16px 18px; margin-bottom:4px; }
.tcc-card h3 { margin:0 0 12px; font-size:14.5px; color:#cbd5e1; font-weight:600; }

.stat .label { font-size:11px; letter-spacing:.09em; color:var(--t3); text-transform:uppercase; }
.stat .value { font-size:24px; font-weight:700; margin-top:6px; color:var(--t1); }
.stat .value.good { color:var(--emerald); } .stat .value.accent { color:var(--sky); }
.stat .value.warn { color:var(--amber); } .stat .value.bad { color:var(--red); }
.stat .sub { font-size:11.5px; color:var(--t3); margin-top:3px; }

.prow { background:var(--row); border-radius:10px; padding:12px 14px; margin-bottom:11px; }
.prow-top { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:9px; }
.prow .names { font-weight:600; font-size:13.5px; color:var(--t1); }
.prow .names .vs { color:var(--t3); font-weight:400; }
.prow .meta { font-size:11.5px; color:var(--t3); margin-top:2px; }
.zval { font-size:20px; font-weight:700; text-align:right; }
.zval.hot { color:var(--amber); } .zval.cool { color:var(--sky); }
.zcap { font-size:9.5px; letter-spacing:.12em; color:var(--t3); text-transform:uppercase; text-align:right; }
.gauge { position:relative; height:8px; border-radius:999px; background:#1e293b; }
.gauge .zone { position:absolute; top:0; bottom:0; background:rgba(245,158,11,.22); }
.gauge .zone.l { left:0; border-radius:999px 0 0 999px; } .gauge .zone.r { right:0; border-radius:0 999px 999px 0; }
.gauge .mid { position:absolute; left:50%; top:0; bottom:0; width:1px; background:#475569; }
.gauge .mark { position:absolute; top:50%; width:13px; height:13px; border-radius:50%;
               transform:translate(-50%,-50%); background:var(--sky); border:2px solid #cbd5e1; }
.gauge .mark.hot { background:var(--amber); border-color:#fde68a; }
.gauge-scale { display:flex; justify-content:space-between; font-size:9.5px; color:var(--t3); margin-top:4px; }
.gauge-scale .entry { color:rgba(245,158,11,.75); }
.alert-inline { margin-top:9px; border:1px solid rgba(251,191,36,.3); background:rgba(251,191,36,.08);
                color:#fcd34d; border-radius:8px; padding:6px 11px; font-size:12px; }

.srow { display:flex; justify-content:space-between; align-items:center; gap:12px;
        background:var(--row); border-radius:10px; padding:10px 14px; margin-bottom:9px; }
.srow .sym { font-weight:600; font-size:13.5px; color:var(--t1); }
.srow .meta { font-size:11.5px; color:var(--t3); }
.trend { font-size:11.5px; font-weight:700; margin-top:2px; }
.trend.up { color:var(--emerald); } .trend.down { color:var(--red); } .trend.flat { color:var(--t3); }
.trend .retest { font-weight:400; color:var(--t3); margin-left:8px; }

.regime-wrap { background:var(--row); border-radius:10px; padding:13px 14px 10px; margin-bottom:12px; }
.regime-top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; }
.regime-label { font-size:16px; font-weight:700; color:var(--emerald); }
.regime-label.riskoff { color:var(--red); } .regime-label.netral { color:var(--t2); }
.regime-score { font-size:12px; color:var(--t3); }
.regime-score b { color:var(--t1); font-size:15px; }
.rgauge { position:relative; height:8px; border-radius:999px;
  background:linear-gradient(90deg, rgba(248,113,113,.5) 0 34%, rgba(100,116,139,.45) 34% 55%,
             rgba(52,211,153,.4) 55% 70%, rgba(16,185,129,.75) 70% 100%); }
.rgauge .mark { position:absolute; top:50%; width:13px; height:13px; border-radius:50%;
                transform:translate(-50%,-50%); background:var(--emerald); border:2px solid #a7f3d0; }
.rscale { display:flex; font-size:9.5px; color:var(--t3); margin-top:5px; }
.rscale .z1{flex:0 0 34%;} .rscale .z2{flex:0 0 21%;} .rscale .z3{flex:0 0 15%;} .rscale .z4{flex:1; text-align:right;}

.fng { display:flex; gap:14px; align-items:center; margin-bottom:12px; }
.fng .v { font-size:22px; font-weight:700; color:var(--amber); }
.fng .bar { flex:1; position:relative; height:6px; border-radius:999px;
  background:linear-gradient(90deg, rgba(248,113,113,.6), rgba(251,191,36,.55), rgba(52,211,153,.6)); }
.fng .bar .mark { position:absolute; top:50%; width:11px; height:11px; border-radius:50%;
  transform:translate(-50%,-50%); background:var(--amber); border:2px solid #fde68a; }
.fng .cap { font-size:11px; color:var(--t3); }

.erow { display:grid; grid-template-columns:46px 1fr auto; gap:10px; align-items:start;
        background:var(--row); border-radius:10px; padding:10px 13px; margin-bottom:9px; }
.mod { font-family:ui-monospace, Consolas, monospace; font-size:11px; font-weight:700; color:var(--sky);
       background:rgba(56,189,248,.1); border:1px solid rgba(56,189,248,.25); border-radius:6px;
       text-align:center; padding:3px 0; }
.erow .what { font-size:12.5px; color:var(--t2); } .erow .what b { color:var(--t1); }
.erow .inv { font-size:11px; color:var(--t3); margin-top:2px; }
.erow .when { font-size:10.5px; color:var(--t3); margin-top:4px; text-align:right; }
.d-up { color:var(--emerald); font-weight:600; } .d-dn { color:var(--red); font-weight:600; }
.svc-line { font-size:12.5px; color:var(--t2); display:flex; justify-content:space-between;
            align-items:center; margin:4px 0; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ============================================================ bootstrap

@st.cache_resource
def _bootstrap():
    init_db(seed=True)          # tabel pair trading
    agent_init_db()             # tabel agent signal
    agent_store.init_store()    # watchlist agent di DB (seed dari yaml sekali)
    return True


try:
    _bootstrap()
except Exception as exc:
    st.error(f"❌ Tidak bisa terhubung ke database. Cek DATABASE_URL di Secrets.\n\n{exc}")
    st.stop()


# ============================================================ helpers

def fmt(v, d=2):
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.{d}f}"
    if abs(v) < 1 and v != 0:
        return f"{v:.5g}"
    return f"{v:,.{d}f}"


def to_wib(value) -> str:
    if not value:
        return "—"
    try:
        return pd.to_datetime(value, utc=True).tz_convert(WIB).strftime("%d %b %H:%M")
    except Exception:
        return str(value)


@st.cache_data(ttl=12, show_spinner=False)
def live_prices() -> dict:
    try:
        return {m["name"]: m for m in extended_client.fetch_markets()}
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def market_names() -> list[str]:
    return sorted(live_prices().keys())


@st.cache_data(ttl=300, show_spinner=False)
def candle_closes(market: str, limit: int = 60) -> list[float]:
    try:
        return [c["c"] for c in extended_client.fetch_candles(market, limit=limit)]
    except Exception:
        return []


def direction_label(d: str) -> str:
    return {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT",
            "LONG_SPREAD": "🟢 LONG SPREAD", "SHORT_SPREAD": "🔴 SHORT SPREAD"}.get(d, d)


def status_label(s: str) -> str:
    return {"OPEN": "🔵 OPEN", "CLOSED_TP": "✅ TP", "CLOSED_SL": "❌ SL",
            "CLOSED_EXIT": "🟣 EXIT", "CLOSED_MANUAL": "⚪ MANUAL", "EXPIRED": "🟠 EXPIRED"}.get(s, s)


def signals_dataframe(signals: list[Signal]) -> pd.DataFrame:
    rows = []
    for s in signals:
        pair = s.strategy == Strategy.PAIR_TRADING
        rows.append({
            "Market": s.market,
            "Strategi": "🔁 Pair" if pair else "📐 MA50",
            "Arah": direction_label(s.direction.value),
            "Status": status_label(s.status.value),
            "Entry": fmt(s.entry_price, 4 if pair else 2),
            "SL": "—" if pair else fmt(s.stop_loss),
            "TP": "—" if pair else fmt(s.take_profit),
            "Z-entry": f"{s.entry_zscore:.2f}" if s.entry_zscore is not None else "—",
            "P&L %": None if s.pnl_pct is None else round(s.pnl_pct, 2),
            "Dibuka": to_wib(s.opened_at), "Ditutup": to_wib(s.closed_at),
        })
    return pd.DataFrame(rows)


# ---------- pembangun HTML (gaya mockup) ----------

def stat_html(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (f"<div class='tcc-card stat'><div class='label'>{label}</div>"
            f"<div class='value {tone}'>{value}</div><div class='sub'>{sub}</div></div>")


def zgauge_html(p: WatchedPair, prices: dict) -> str:
    z, entry = p.last_zscore, p.entry_zscore
    pct = lambda v: ((max(-4.0, min(4.0, v)) + 4.0) / 8.0) * 100  # noqa: E731
    hot = z is not None and abs(z) >= entry
    pa = prices.get(p.base_market, {}).get("last_price")
    pb = prices.get(p.quote_market, {}).get("last_price")
    corr = f"{p.last_correlation:.2f}" if p.last_correlation is not None else "—"
    ztxt = f"{z:+.2f}" if z is not None else "—"
    mark = (f"<div class='mark {'hot live-dot' if hot else ''}' style='left:{pct(z)}%'></div>"
            if z is not None else "")
    warn = ("<div class='alert-inline'>⚡ Spread di zona entry! Sinyal dibuat pada scan berikutnya.</div>"
            if hot else "")
    off = "" if p.is_active else " <span style='color:var(--t3)'>(nonaktif)</span>"
    return f"""<div class='prow'>
      <div class='prow-top'>
        <div><div class='names'>{p.base_market} <span class='vs'>vs</span> {p.quote_market}{off}</div>
          <div class='meta num'>{fmt(pa)} / {fmt(pb)} · korelasi {corr}</div></div>
        <div><div class='zval num {'hot' if hot else 'cool'}'>{ztxt}</div>
          <div class='zcap'>z-score</div></div>
      </div>
      <div class='gauge'><div class='zone l' style='width:{pct(-entry)}%'></div>
        <div class='zone r' style='width:{100 - pct(entry)}%'></div>
        <div class='mid'></div>{mark}</div>
      <div class='gauge-scale'><span>-4</span><span class='entry'>-{entry:g}</span><span>0</span>
        <span class='entry'>+{entry:g}</span><span>+4</span></div>
      {warn}</div>"""


def spark_svg(values: list[float], w: int = 120, h: int = 36) -> str:
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = (mx - mn) or 1.0
    pad = 2
    x = lambda i: pad + (i / (len(values) - 1)) * (w - 2 * pad)          # noqa: E731
    y = lambda v: h - pad - ((v - mn) / rng) * (h - 2 * pad)             # noqa: E731
    pts = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    up = values[-1] >= values[0]
    c = "#34d399" if up else "#f87171"
    gid = f"g{abs(hash(tuple(values))) % 99999}"
    return (f"<svg width='{w}' height='{h}'><defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'>"
            f"<stop offset='0%' stop-color='{c}' stop-opacity='.25'/>"
            f"<stop offset='100%' stop-color='{c}' stop-opacity='0'/></linearGradient></defs>"
            f"<path d='{pts} L{x(len(values) - 1):.1f},{h - pad} L{pad},{h - pad} Z' fill='url(#{gid})'/>"
            f"<path d='{pts}' fill='none' stroke='{c}' stroke-width='1.5'/>"
            f"<circle cx='{x(len(values) - 1):.1f}' cy='{y(values[-1]):.1f}' r='2.5' fill='{c}' class='live-dot'/></svg>")


def ma_row_html(s: WatchedSymbol, prices: dict) -> str:
    live = prices.get(s.market, {}).get("last_price") or s.last_price
    trend = s.last_trend or "FLAT"
    cls, txt = {"UP": ("up", "▲ UPTREND"), "DOWN": ("down", "▼ DOWNTREND")}.get(trend, ("flat", "— FLAT"))
    spark = spark_svg(candle_closes(s.market, 45))
    off = "" if s.is_active else " <span style='color:var(--t3)'>(nonaktif)</span>"
    return f"""<div class='srow'><div>
      <div class='sym'>{s.market}{off}</div>
      <div class='meta num'>{fmt(live)} · MA50 {fmt(s.last_ma50)}</div>
      <div class='trend {cls}'>{txt}<span class='retest'>retest {s.last_retest_count or 0}/2</span></div>
      </div>{spark}</div>"""


def regime_html(outlook) -> str:
    if not outlook:
        return "<div class='regime-wrap'><span style='color:var(--t3)'>Belum ada outlook.</span></div>"
    score, label = outlook.regime_score, outlook.regime_label
    cls = "riskoff" if label == "RISK_OFF" else ("netral" if label == "NETRAL" else "")
    return f"""<div class='regime-wrap'>
      <div class='regime-top'><span class='regime-label {cls}'>⚖️ {label.replace('_', '-')}</span>
        <span class='regime-score num'><b>{score}</b>/100 · {outlook.outlook_date}</span></div>
      <div class='rgauge'><div class='mark' style='left:{score}%'></div></div>
      <div class='rscale'><span class='z1'>RISK-OFF 0–34</span><span class='z2'>NETRAL 35–55</span>
        <span class='z3'>N-BULLISH</span><span class='z4'>RISK-ON ≥71</span></div></div>"""


def fng_html(row) -> str:
    if not row:
        return ""
    return f"""<div class='fng'><span class='v num'>{row.value}</span>
      <div class='bar'><div class='mark' style='left:{row.value}%'></div></div>
      <span class='cap'>Fear &amp; Greed — {row.value_classification}<br>{to_wib(row.fng_timestamp)}</span></div>"""


def event_html(e) -> str:
    sev = {"INFO": "info", "MONITOR": "monitor", "REVIEW_CANDIDATE": "review"}.get(e.severity, "info")
    sev_txt = {"REVIEW_CANDIDATE": "REVIEW"}.get(e.severity, e.severity)
    delta = ""
    if e.delta_pct is not None:
        cls = "d-up" if e.delta_pct >= 0 else "d-dn"
        delta = f" <span class='{cls} num'>{e.delta_pct:+.1f}%</span>"
    inv = f"<div class='inv'>Invalidasi: {e.invalidation}</div>" if e.invalidation else ""
    return f"""<div class='erow'><span class='mod'>{e.module.split('_')[0]}</span>
      <div><div class='what'><b>{e.entity}</b> {e.type} · {e.metric}{delta}</div>{inv}</div>
      <div><span class='pill {sev}'>{sev_txt}</span><div class='when num'>{to_wib(e.ts)}</div></div></div>"""


# ---------- query data ----------

def pair_data():
    db = SessionLocal()
    try:
        return {
            "perf": performance(db),
            "pairs": db.execute(select(WatchedPair).order_by(WatchedPair.id)).scalars().all(),
            "symbols": db.execute(select(WatchedSymbol).order_by(WatchedSymbol.id)).scalars().all(),
            "signals": db.execute(select(Signal).order_by(desc(Signal.opened_at)).limit(300)).scalars().all(),
        }
    finally:
        db.close()


def agent_data(hours: int = 24, days: int = 7):
    with session_scope() as s:
        outlook = s.execute(select(am.DailyOutlook)
                            .order_by(desc(am.DailyOutlook.outlook_date)).limit(1)).scalars().first()
        cutoff_e = datetime.now(timezone.utc) - timedelta(hours=hours)
        events = s.execute(select(am.Event).where(am.Event.ts >= cutoff_e)
                           .order_by(desc(am.Event.ts)).limit(100)).scalars().all()
        cutoff_a = datetime.now(timezone.utc) - timedelta(days=days)
        alerts = s.execute(select(am.AlertLog).where(am.AlertLog.sent_at >= cutoff_a)
                           .order_by(desc(am.AlertLog.sent_at)).limit(100)).scalars().all()
        fng = s.execute(select(am.SentimentFng)
                        .order_by(desc(am.SentimentFng.fng_timestamp)).limit(1)).scalars().first()
        s.expunge_all()
    return {"outlook": outlook, "events": events, "alerts": alerts, "fng": fng}


def _agent_notifier():
    return PrefixNotifier(get_notifier(load_settings()))


# ============================================================ sidebar

st.sidebar.markdown("## 🧭 Trading<span style='color:#34d399'>Command</span>Center",
                    unsafe_allow_html=True)
st.sidebar.caption("Pair Trading · MA50 · Agent Signal On-Chain")
page = st.sidebar.radio("Menu", ["🏠 Ringkasan", "🔁 Pair Trading", "⭐ Recommended",
                                 "🤖 Agent Signal", "📈 Performa", "⚙️ Pengaturan"],
                        label_visibility="collapsed")

st.sidebar.divider()
st.sidebar.markdown("<div class='svc-line'><span>Database (Neon)</span>"
                    "<span class='pill on'><span class='dot live-dot'></span>TERHUBUNG</span></div>"
                    "<div class='svc-line'><span>Scanner &amp; Agent</span>"
                    "<span class='pill on'>GitHub Actions</span></div>", unsafe_allow_html=True)

if st.sidebar.button("⚡ Scan Sekarang", width="stretch", type="primary"):
    with st.spinner("Memindai watchlist…"):
        db = SessionLocal()
        try:
            r = run_scan(db)
            st.session_state["notice"] = (f"Scan selesai: {r['signals_created']} sinyal baru, "
                                          f"{r['signals_closed']} ditutup.")
        except Exception as exc:
            st.session_state["notice_err"] = f"Scan gagal: {exc}"
        finally:
            db.close()
    st.cache_data.clear()
    st.rerun()

if st.sidebar.button("🔄 Refresh data", width="stretch"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("Scan pair: tiap 15 mnt · Agent: tiap 20 mnt\n\n"
                   "Outlook 07:00 · Digest 17:00 WIB")
st.sidebar.caption(f"🕐 {pd.Timestamp.now(tz=WIB).strftime('%d %b %Y · %H:%M:%S')} WIB")

if st.session_state.get("notice"):
    st.sidebar.success(st.session_state.pop("notice"))
if st.session_state.get("notice_err"):
    st.sidebar.error(st.session_state.pop("notice_err"))


# ============================================================ PAGE: ringkasan

def page_overview():
    d = pair_data()
    a = agent_data()
    prices = live_prices()
    o = d["perf"]["overall"]

    c1, c2 = st.columns([3, 1])
    c1.markdown("# 🏠 Ringkasan <span class='pill on'><span class='dot live-dot'></span>LIVE</span>",
                unsafe_allow_html=True)
    c2.caption(f"🕐 {pd.Timestamp.now(tz=WIB).strftime('%H:%M:%S')} WIB")

    ol = a["outlook"]
    cols = st.columns(5)
    wr = f"{o['winrate_pct']}%" if o["winrate_pct"] is not None else "—"
    cols[0].markdown(stat_html("Winrate", wr, f"{o['wins']}W / {o['losses']}L",
                               "good" if (o["winrate_pct"] or 0) >= 50 else ""), unsafe_allow_html=True)
    cols[1].markdown(stat_html("Total P&L", f"{o['total_pnl_pct']:+.2f}%", "kumulatif selesai",
                               "good" if o["total_pnl_pct"] >= 0 else "bad"), unsafe_allow_html=True)
    cols[2].markdown(stat_html("Sinyal Aktif", str(o["open"]), "posisi berjalan", "accent"),
                     unsafe_allow_html=True)
    cols[3].markdown(stat_html("Regime", ol.regime_label.replace("_", "-") if ol else "—",
                               f"skor {ol.regime_score}/100" if ol else "belum ada outlook",
                               "good" if ol and ol.regime_score >= 56 else ""), unsafe_allow_html=True)
    cols[4].markdown(stat_html("Fear & Greed", str(a["fng"].value) if a["fng"] else "—",
                               a["fng"].value_classification if a["fng"] else "", "warn"),
                     unsafe_allow_html=True)

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("<div class='tcc-card'><h3>🔁 Pair Trading — spread live</h3>" +
                    "".join(zgauge_html(p, prices) for p in d["pairs"]) +
                    ("<span style='color:var(--t3);font-size:12.5px'>Belum ada pair — tambah di Pengaturan.</span>"
                     if not d["pairs"] else "") + "</div>", unsafe_allow_html=True)
    with col_r:
        st.markdown("<div class='tcc-card'><h3>📐 MA50 Daily Retest — harga live</h3>" +
                    "".join(ma_row_html(s, prices) for s in d["symbols"]) +
                    ("<span style='color:var(--t3);font-size:12.5px'>Belum ada market — tambah di Pengaturan.</span>"
                     if not d["symbols"] else "") + "</div>", unsafe_allow_html=True)

        ev = a["events"]
        n_rev = sum(1 for e in ev if e.severity == "REVIEW_CANDIDATE")
        st.markdown(f"<div class='tcc-card'><h3>🤖 Agent Signal — event 24 jam "
                    f"<span style='color:var(--t3);font-weight:400'>({len(ev)} event · {n_rev} review)</span></h3>" +
                    ("".join(event_html(e) for e in ev[:4]) or
                     "<span style='color:var(--t3);font-size:12.5px'>Tidak ada event 24 jam terakhir.</span>") +
                    "</div>", unsafe_allow_html=True)

    if ol:
        with st.expander(f"📰 Daily Outlook {ol.outlook_date} — {ol.regime_label.replace('_', '-')} "
                         f"(skor {ol.regime_score})"):
            st.code(ol.message_text, language=None, wrap_lines=True)

    st.markdown("<div class='tcc-card'><h3>⚡ Sinyal Terbaru</h3></div>", unsafe_allow_html=True)
    latest = d["signals"][:12]
    if latest:
        st.dataframe(signals_dataframe(latest), width="stretch", hide_index=True)
    else:
        st.caption("Belum ada sinyal — muncul otomatis saat syarat strategi terpenuhi.")


# ============================================================ PAGE: pair trading

def page_pair():
    st.markdown("# 🔁 Pair Trading & MA50")
    d = pair_data()
    prices = live_prices()

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("<div class='tcc-card'><h3>🔁 Spread live</h3>" +
                    "".join(zgauge_html(p, prices) for p in d["pairs"]) + "</div>",
                    unsafe_allow_html=True)
    with col_r:
        st.markdown("<div class='tcc-card'><h3>📐 MA50 Daily Retest</h3>" +
                    "".join(ma_row_html(s, prices) for s in d["symbols"]) + "</div>",
                    unsafe_allow_html=True)

    st.markdown("<div class='tcc-card'><h3>⚡ Sinyal</h3></div>", unsafe_allow_html=True)
    f1, f2 = st.columns(2)
    strat = f1.selectbox("Strategi", ["Semua", "PAIR_TRADING", "MA50_RETEST"])
    stat = f2.selectbox("Status", ["Semua", "OPEN", "CLOSED_TP", "CLOSED_SL",
                                   "CLOSED_EXIT", "CLOSED_MANUAL", "EXPIRED"])
    signals = d["signals"]
    if strat != "Semua":
        signals = [s for s in signals if s.strategy == Strategy(strat)]
    if stat != "Semua":
        signals = [s for s in signals if s.status == SignalStatus(stat)]
    if signals:
        st.dataframe(signals_dataframe(signals), width="stretch", hide_index=True)
    else:
        st.caption("Tidak ada sinyal untuk filter ini.")

    open_sigs = [s for s in signals if s.status == SignalStatus.OPEN]
    if open_sigs:
        st.subheader("Tutup posisi manual")
        db = SessionLocal()
        try:
            for s in open_sigs:
                c1, c2 = st.columns([4, 1])
                c1.write(f"#{s.id} · {s.market} · {direction_label(s.direction.value)}")
                if c2.button("Tutup", key=f"close{s.id}"):
                    try:
                        close_signal_manual(db, s.id)
                        st.session_state["notice"] = f"Sinyal #{s.id} ditutup."
                    except Exception as exc:
                        st.session_state["notice_err"] = f"Gagal menutup: {exc}"
                    st.cache_data.clear()
                    st.rerun()
        finally:
            db.close()


# ============================================================ PAGE: agent signal

def page_agent():
    st.markdown("# 🤖 Agent Signal — Crypto On-Chain Advisor")
    st.caption("Read-only advisor · outlook 07:00 WIB via GitHub Actions · bukan nasihat keuangan.")

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("📰 Buat Daily Outlook", width="stretch"):
        with st.spinner("Membuat outlook… (±1 menit)"):
            try:
                rules, pos, settings = load_action_rules(), load_positions(), load_settings()
                wl = agent_store.effective_watchlist()
                with session_scope() as s:
                    reporting.send_daily_outlook(s, rules, wl, pos, settings, _agent_notifier())
                st.session_state["notice"] = "Daily outlook selesai & terkirim."
            except Exception as exc:
                st.session_state["notice_err"] = f"Outlook gagal: {exc}"
        st.cache_data.clear()
        st.rerun()
    if b2.button("🔍 Realtime Scan", width="stretch"):
        with st.spinner("Scan realtime…"):
            try:
                rules, pos, settings = load_action_rules(), load_positions(), load_settings()
                wl = agent_store.effective_watchlist()
                with session_scope() as s:
                    sent = reporting.process_realtime_alerts(s, rules, wl, pos, settings,
                                                             _agent_notifier())
                st.session_state["notice"] = f"Realtime scan: {len(sent)} alert."
            except Exception as exc:
                st.session_state["notice_err"] = f"Scan gagal: {exc}"
        st.cache_data.clear()
        st.rerun()

    hours = st.select_slider("Rentang event", options=[6, 12, 24, 48, 72, 168], value=24,
                             format_func=lambda h: f"{h} jam")
    a = agent_data(hours=hours)
    ol = a["outlook"]

    col_l, col_r = st.columns([5, 4])
    with col_l:
        st.markdown("<div class='tcc-card'><h3>Regime &amp; Sentimen</h3>" +
                    regime_html(ol) + fng_html(a["fng"]) + "</div>", unsafe_allow_html=True)
        st.markdown("<div class='tcc-card'><h3>📰 Daily Outlook</h3></div>", unsafe_allow_html=True)
        if ol:
            st.code(ol.message_text, language=None, wrap_lines=True)
            st.caption(f"narasi: {'LLM' if ol.llm_used else 'template'} · dibuat {to_wib(ol.created_at)}")
        else:
            st.caption("Belum ada outlook — klik **Buat Daily Outlook** atau tunggu jadwal 07:00 WIB.")
    with col_r:
        ev = a["events"]
        n_rev = sum(1 for e in ev if e.severity == "REVIEW_CANDIDATE")
        st.markdown(f"<div class='tcc-card'><h3>🧩 Event M1–M7 — {hours} jam "
                    f"<span style='color:var(--t3);font-weight:400'>({len(ev)} event · {n_rev} review)</span></h3>" +
                    ("".join(event_html(e) for e in ev[:12]) or
                     "<span style='color:var(--t3);font-size:12.5px'>Tidak ada event pada rentang ini.</span>") +
                    "</div>", unsafe_allow_html=True)

    st.markdown("<div class='tcc-card'><h3>🔔 Riwayat Alert (7 hari)</h3></div>", unsafe_allow_html=True)
    if a["alerts"]:
        rows = [{"Terkirim": to_wib(x.sent_at), "Jenis": x.kind, "Level": x.level or "—",
                 "Entitas": x.entity or "—", "Channel": x.channel,
                 "Status": "✅ terkirim" if x.delivered else "📤 outbox",
                 "Follow-up": x.followup_status or "—"} for x in a["alerts"]]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.caption("Belum ada alert dalam 7 hari terakhir.")


# ============================================================ PAGE: performa

def page_performance():
    st.markdown("# 📈 Performa")
    d = pair_data()
    perf = d["perf"]
    o = perf["overall"]

    cols = st.columns(4)
    cols[0].markdown(stat_html("Winrate", f"{o['winrate_pct']}%" if o["winrate_pct"] is not None else "—",
                               f"{o['wins']} menang · {o['losses']} kalah",
                               "good" if (o["winrate_pct"] or 0) >= 50 else ""), unsafe_allow_html=True)
    cols[1].markdown(stat_html("Total P&L", f"{o['total_pnl_pct']:+.2f}%", "",
                               "good" if o["total_pnl_pct"] >= 0 else "bad"), unsafe_allow_html=True)
    cols[2].markdown(stat_html("Rata-rata/Sinyal",
                               f"{o['avg_pnl_pct']:+.2f}%" if o["avg_pnl_pct"] is not None else "—", ""),
                     unsafe_allow_html=True)
    cols[3].markdown(stat_html("Sinyal Selesai", str(o["closed"]), f"{o['open']} berjalan", "accent"),
                     unsafe_allow_html=True)

    st.markdown("<div class='tcc-card'><h3>📈 Kurva Ekuitas (kumulatif P&L %)</h3></div>",
                unsafe_allow_html=True)
    curve = perf["equity_curve"]
    if len(curve) >= 2:
        df = pd.DataFrame(curve)
        df["t"] = pd.to_datetime(df["t"])
        st.line_chart(df.set_index("t")["cum_pnl_pct"], height=260)
    else:
        st.caption("Kurva muncul setelah minimal 2 sinyal selesai.")

    st.markdown("<div class='tcc-card'><h3>Per Strategi</h3></div>", unsafe_allow_html=True)
    rows = []
    for s in perf["per_strategy"]:
        rows.append({"Strategi": "🔁 Pair Trading" if s["strategy"] == "PAIR_TRADING" else "📐 MA50 Retest",
                     "Total": s["total"], "Open": s["open"], "Selesai": s["closed"],
                     "Win": s["wins"], "Loss": s["losses"],
                     "Winrate": f"{s['winrate_pct']}%" if s["winrate_pct"] is not None else "—",
                     "Total P&L": f"{s['total_pnl_pct']:+.2f}%"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ============================================================ PAGE: pengaturan

def page_settings():
    st.markdown("# ⚙️ Pengaturan")
    st.caption("Semua perubahan tersimpan di database — langsung dipakai scanner & agent "
               "di GitHub Actions pada run berikutnya.")
    tab_pair, tab_ma, tab_agent, tab_notif = st.tabs(
        ["🔁 Pair Trading", "📐 MA50 Market", "🤖 Agent Watchlist", "🔔 Notifikasi & Jadwal"])

    names = market_names()

    # ---------------- pair ----------------
    with tab_pair:
        with st.form("add_pair", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1, 1])
            base = c1.text_input("Base market", placeholder="BTC-USD").strip().upper()
            quote = c2.text_input("Quote market", placeholder="ETH-USD").strip().upper()
            lookback = c3.number_input("Lookback", 20, 200, 60)
            entry_z = c4.number_input("Entry z", 1.0, 5.0, 2.0, 0.5)
            exit_z = c5.number_input("Exit z", 0.0, 2.0, 0.5, 0.1)
            if st.form_submit_button("➕ Tambah Pair", type="primary"):
                db = SessionLocal()
                try:
                    err = None
                    if not base or not quote:
                        err = "Isi kedua market."
                    elif base == quote:
                        err = "Base dan quote tidak boleh sama."
                    elif names and (base not in names or quote not in names):
                        err = f"Market '{base if base not in names else quote}' tidak ada di exchange."
                    elif db.execute(select(WatchedPair).where(
                            WatchedPair.base_market == base,
                            WatchedPair.quote_market == quote)).scalar_one_or_none():
                        err = "Pair ini sudah ada."
                    if err:
                        st.error(err)
                    else:
                        db.add(WatchedPair(base_market=base, quote_market=quote, lookback=int(lookback),
                                           entry_zscore=float(entry_z), exit_zscore=float(exit_z)))
                        db.commit()
                        st.rerun()
                finally:
                    db.close()

        db = SessionLocal()
        try:
            for p in db.execute(select(WatchedPair).order_by(WatchedPair.id)).scalars().all():
                c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
                zt = f"z={p.last_zscore:+.2f}" if p.last_zscore is not None else "z=—"
                c1.markdown(f"**{p.base_market}** vs **{p.quote_market}**  \n"
                            f"<span style='color:var(--t3);font-size:12px'>{zt} · "
                            f"entry ±{p.entry_zscore:g} / exit ±{p.exit_zscore:g} · lookback {p.lookback}</span>",
                            unsafe_allow_html=True)
                c2.write("🟢 Aktif" if p.is_active else "⚪ Nonaktif")
                if c3.button("Toggle", key=f"tp{p.id}"):
                    p.is_active = not p.is_active
                    db.commit()
                    st.rerun()
                if c4.button("🗑️", key=f"dp{p.id}"):
                    db.delete(p)
                    db.commit()
                    st.rerun()
        finally:
            db.close()

    # ---------------- ma50 ----------------
    with tab_ma:
        with st.form("add_symbol", clear_on_submit=True):
            c1, c2 = st.columns([4, 1])
            market = c1.text_input("Market", placeholder="SOL-USD",
                                   label_visibility="collapsed").strip().upper()
            if c2.form_submit_button("➕ Tambah", type="primary"):
                db = SessionLocal()
                try:
                    if not market:
                        st.error("Isi nama market.")
                    elif names and market not in names:
                        st.error(f"Market '{market}' tidak ada di exchange.")
                    elif db.execute(select(WatchedSymbol).where(
                            WatchedSymbol.market == market)).scalar_one_or_none():
                        st.error("Market ini sudah ada.")
                    else:
                        db.add(WatchedSymbol(market=market))
                        db.commit()
                        st.rerun()
                finally:
                    db.close()

        db = SessionLocal()
        try:
            for s in db.execute(select(WatchedSymbol).order_by(WatchedSymbol.id)).scalars().all():
                c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
                c1.markdown(f"**{s.market}**  \n<span style='color:var(--t3);font-size:12px'>"
                            f"harga {fmt(s.last_price)} · MA50 {fmt(s.last_ma50)} · "
                            f"{s.last_trend or 'FLAT'}</span>", unsafe_allow_html=True)
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

    # ---------------- agent watchlist ----------------
    with tab_agent:
        st.markdown("#### Aset (CoinGecko) — maks 30")
        st.caption("`coingecko_id` harus persis (huruf kecil, cek coingecko.com/coins/list) — "
                   "typo = data kosong diam-diam. M4 memantau harga & volume aset ini tiap 20 menit.")
        with st.form("add_asset", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([1.4, 2, 1.2, 1])
            sym = c1.text_input("Symbol", placeholder="SOL").strip().upper()
            cgid = c2.text_input("coingecko_id", placeholder="solana").strip().lower()
            tier = c3.selectbox("Tier", ["satellite", "core"])
            if c4.form_submit_button("➕ Tambah", type="primary"):
                with session_scope() as s:
                    total = len(s.execute(select(agent_store.AgentWatchAsset)).scalars().all())
                    dup = s.execute(select(agent_store.AgentWatchAsset)
                                    .where(agent_store.AgentWatchAsset.symbol == sym)).scalar_one_or_none()
                    if not sym or not cgid:
                        st.error("Symbol dan coingecko_id wajib diisi.")
                    elif dup:
                        st.error(f"{sym} sudah ada di watchlist.")
                    elif total >= agent_store.MAX_ASSETS:
                        st.error(f"Maksimal {agent_store.MAX_ASSETS} aset (kuota CoinGecko).")
                    else:
                        s.add(agent_store.AgentWatchAsset(symbol=sym, coingecko_id=cgid, tier=tier))
                        st.rerun()

        with session_scope() as s:
            assets = s.execute(select(agent_store.AgentWatchAsset)
                               .order_by(agent_store.AgentWatchAsset.id)).scalars().all()
            s.expunge_all()
        for a_ in assets:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            c1.markdown(f"**{a_.symbol}**  \n<span style='color:var(--t3);font-size:12px'>"
                        f"{a_.coingecko_id} · {a_.tier}</span>", unsafe_allow_html=True)
            c2.write("🟢 Aktif" if a_.is_active else "⚪ Nonaktif")
            if c3.button("Toggle", key=f"ta{a_.id}"):
                with session_scope() as s:
                    row = s.get(agent_store.AgentWatchAsset, a_.id)
                    row.is_active = not row.is_active
                st.rerun()
            if c4.button("🗑️", key=f"da{a_.id}"):
                with session_scope() as s:
                    s.delete(s.get(agent_store.AgentWatchAsset, a_.id))
                st.rerun()

        st.divider()
        st.markdown("#### Protokol DeFi (DefiLlama) — maks 20")
        st.caption("Slug persis dari defillama.com/protocol/{slug}. M1 memantau TVL, M2 memantau fees.")
        with st.form("add_proto", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns([1.8, 1.8, 0.8, 0.8, 1])
            pname = c1.text_input("Nama", placeholder="Hyperliquid").strip()
            slug = c2.text_input("llama_slug", placeholder="hyperliquid").strip().lower()
            t_tvl = c3.checkbox("TVL", value=True)
            t_fee = c4.checkbox("Fees", value=True)
            if c5.form_submit_button("➕ Tambah", type="primary"):
                with session_scope() as s:
                    total = len(s.execute(select(agent_store.AgentWatchProtocol)).scalars().all())
                    dup = s.execute(select(agent_store.AgentWatchProtocol)
                                    .where(agent_store.AgentWatchProtocol.name == pname)).scalar_one_or_none()
                    if not pname or not slug:
                        st.error("Nama dan slug wajib diisi.")
                    elif dup:
                        st.error(f"{pname} sudah ada.")
                    elif total >= agent_store.MAX_PROTOCOLS:
                        st.error(f"Maksimal {agent_store.MAX_PROTOCOLS} protokol.")
                    else:
                        s.add(agent_store.AgentWatchProtocol(name=pname, llama_slug=slug,
                                                             track_tvl=t_tvl, track_fees=t_fee))
                        st.rerun()

        with session_scope() as s:
            protos = s.execute(select(agent_store.AgentWatchProtocol)
                               .order_by(agent_store.AgentWatchProtocol.id)).scalars().all()
            s.expunge_all()
        for p_ in protos:
            c1, c2, c3, c4 = st.columns([4, 2, 1, 1])
            track = " + ".join(t for t, on in (("TVL", p_.track_tvl), ("fees", p_.track_fees)) if on)
            c1.markdown(f"**{p_.name}**  \n<span style='color:var(--t3);font-size:12px'>"
                        f"{p_.llama_slug} · {track}</span>", unsafe_allow_html=True)
            c2.write("🟢 Aktif" if p_.is_active else "⚪ Nonaktif")
            if c3.button("Toggle", key=f"tpr{p_.id}"):
                with session_scope() as s:
                    row = s.get(agent_store.AgentWatchProtocol, p_.id)
                    row.is_active = not row.is_active
                st.rerun()
            if c4.button("🗑️", key=f"dpr{p_.id}"):
                with session_scope() as s:
                    s.delete(s.get(agent_store.AgentWatchProtocol, p_.id))
                st.rerun()

    # ---------------- notifikasi & jadwal ----------------
    with tab_notif:
        if st.button("🔔 Tes Telegram"):
            ok = telegram_notifier.send_message(
                "🔔 <b>Tes notifikasi</b>\n\nKoneksi Telegram dari Trading Command Center berhasil! ✅")
            if ok:
                st.success("✅ Pesan tes terkirim!")
            else:
                st.error("❌ Telegram gagal — cek TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID di Secrets.")

        st.markdown("#### Jadwal otomatis (GitHub Actions)")
        st.dataframe(pd.DataFrame([
            {"Tugas": "Scan pair & MA50", "Jadwal": "tiap 15 menit", "Workflow": "scan.yml"},
            {"Tugas": "Agent: harga + wallet + derivatif + realtime", "Jadwal": "tiap 20 menit", "Workflow": "agent.yml"},
            {"Tugas": "Agent: TVL + fees", "Jadwal": "tiap 4 jam", "Workflow": "agent.yml"},
            {"Tugas": "Agent: stablecoin + Fear & Greed", "Jadwal": "06:40 WIB", "Workflow": "agent.yml"},
            {"Tugas": "Daily Outlook", "Jadwal": "07:00 WIB", "Workflow": "agent.yml"},
            {"Tugas": "Evening Digest", "Jadwal": "17:00 WIB", "Workflow": "agent.yml"},
            {"Tugas": "Weekly Review", "Jadwal": "Minggu 08:30 WIB", "Workflow": "agent.yml"},
        ]), width="stretch", hide_index=True)
        st.caption("Ubah jadwal: edit cron di `.github/workflows/` lalu push. "
                   "Threshold M1–M7 & eskalasi: `agent/action_rules.yaml` (sumber kebenaran — "
                   "sengaja tidak diedit dari UI supaya terversi di Git).")


# ============================================================ PAGE: recommended

def _load_v2():
    """Import the v2 scan pipeline from ./pair-trading-v2. Returns (pipeline, universe)
    or None if the folder is missing."""
    if not os.path.isdir(V2_DIR):
        return None
    if V2_DIR not in sys.path:
        sys.path.insert(0, V2_DIR)
    import pipeline as v2_pipeline
    import universe as v2_universe
    return v2_pipeline, v2_universe


def _build_recommendations(res, v2_universe, watched_pairs) -> list[dict]:
    table = v2_universe.build_symbol_table()
    watched = set()
    for p in watched_pairs:
        watched.add((p.base_market, p.quote_market))
        watched.add((p.quote_market, p.base_market))
    items = []
    for row in res.qualified.itertuples():
        src_a, tick_a = table.get(row.symbol_a, (None, None))
        src_b, tick_b = table.get(row.symbol_b, (None, None))
        addable = src_a == "extended" and src_b == "extended"
        items.append({
            "symbol_a": row.symbol_a, "symbol_b": row.symbol_b,
            "corr_level": float(row.corr_level), "corr_returns": float(row.corr_returns),
            "base_market": tick_a if addable else None,
            "quote_market": tick_b if addable else None,
            "addable": addable, "source_a": src_a, "source_b": src_b,
            "in_watchlist": addable and (tick_a, tick_b) in watched,
        })
    return items


def _format_reco_telegram(items: list[dict], threshold: float) -> str:
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


def page_recommended():
    st.markdown("# ⭐ Recommended <span class='pill on'>KORELASI ≥ 0.90</span>",
                unsafe_allow_html=True)
    st.caption("Pair dengan korelasi tinggi hasil screening multi-venue (Extended · Yahoo · "
               "Binance) — pilih untuk masuk watchlist, atau kirim daftarnya ke Telegram.")

    try:
        loaded = _load_v2()
    except Exception as exc:
        st.error(f"❌ Gagal memuat modul v2 dari `{V2_DIR}`:\n\n{exc}")
        return
    if loaded is None:
        st.warning(f"Folder `pair-trading-v2` tidak ditemukan di `{V2_DIR}`. "
                   "Pastikan foldernya ikut ter-deploy, atau set env var `V2_DIR`.")
        return
    v2_pipeline, v2_universe = loaded

    c1, c2 = st.columns([1, 3])
    threshold = c1.number_input("Threshold |korelasi|", 0.50, 1.00, 0.90, 0.01)
    rescan = c2.button("🔄 Scan Ulang", type="primary")

    cache = st.session_state.get("reco_cache")
    if rescan or cache is None or cache["threshold"] != float(threshold):
        with st.spinner("Memindai korelasi di 3 venue… (±20 detik)"):
            try:
                res = v2_pipeline.scan_pipeline(
                    ["crypto", "tradfi", "external", "spread"],
                    corr_threshold=float(threshold), corr_method="level",
                    run_backtest=False)
            except Exception as exc:
                st.error(f"❌ Scan gagal: {exc}")
                return
        if not res.ok:
            st.error(f"❌ Scan gagal: {res.reason}")
            return
        cache = {"result": res, "threshold": float(threshold),
                 "at": pd.Timestamp.now(tz=WIB).strftime("%d %b %H:%M WIB")}
        st.session_state["reco_cache"] = cache
    res = cache["result"]

    db = SessionLocal()
    try:
        watched_pairs = db.execute(select(WatchedPair)).scalars().all()
    finally:
        db.close()
    items = _build_recommendations(res, v2_universe, watched_pairs)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Pair Lolos", len(items))
    m2.metric("Bisa Dipantau", sum(i["addable"] for i in items), "kedua kaki di Extended")
    m3.metric("Sudah di Watchlist", sum(i["in_watchlist"] for i in items))
    m4.metric("Scan Terakhir", cache["at"])

    if not items:
        st.info("Tidak ada pair yang lolos threshold ini.")
        return

    def status_of(i):
        if i["in_watchlist"]:
            return "👁️ Dipantau"
        return "🟦 Kandidat" if i["addable"] else "⬜ Di luar Extended"

    st.dataframe(pd.DataFrame([{
        "Pair": f"{i['symbol_a']} vs {i['symbol_b']}",
        "Korelasi Level": round(i["corr_level"], 3),
        "Korelasi Returns": round(i["corr_returns"], 3),
        "Sumber": f"{i['source_a']} / {i['source_b']}",
        "Status": status_of(i),
    } for i in items]), width="stretch", hide_index=True)
    st.caption("💡 Korelasi **level** tinggi tapi **returns** rendah = dua aset kebetulan "
               "sama-sama naik, bukan edge trading. Pair di luar Extended (Brent, PAXG, XAUT) "
               "tidak bisa dipantau live oleh engine ini.")

    st.divider()
    col_add, col_tg = st.columns(2)

    with col_add:
        st.subheader("➕ Tambah ke Watchlist")
        candidates = [i for i in items if i["addable"] and not i["in_watchlist"]]
        if not candidates:
            st.caption("Semua kandidat Extended sudah ada di watchlist.")
        else:
            labels = [f"{i['symbol_a']}/{i['symbol_b']}" for i in candidates]
            chosen = st.multiselect("Pilih pair", labels, key="reco_pick")
            if st.button("➕ Tambahkan", disabled=not chosen, key="reco_add"):
                db = SessionLocal()
                added = 0
                try:
                    for i in candidates:
                        if f"{i['symbol_a']}/{i['symbol_b']}" not in chosen:
                            continue
                        dupe = db.execute(select(WatchedPair).where(
                            WatchedPair.base_market == i["base_market"],
                            WatchedPair.quote_market == i["quote_market"])).scalar_one_or_none()
                        if not dupe:
                            db.add(WatchedPair(base_market=i["base_market"],
                                               quote_market=i["quote_market"]))
                            added += 1
                    db.commit()
                finally:
                    db.close()
                st.session_state["notice"] = f"✅ {added} pair masuk watchlist."
                st.cache_data.clear()
                st.rerun()

    with col_tg:
        st.subheader("📨 Kirim ke Telegram")
        st.caption("Kirim daftar rekomendasi ini ke Telegram sebagai sinyal pair apa "
                   "saja yang bisa masuk watchlist.")
        if st.button("📨 Kirim Sekarang", key="reco_tg"):
            ok = telegram_notifier.send_message(
                _format_reco_telegram(items, cache["threshold"]))
            st.session_state["notice"] = (
                f"📨 Rekomendasi {len(items)} pair terkirim ke Telegram." if ok
                else "")
            if not ok:
                st.session_state["notice_err"] = "❌ Telegram gagal — cek token/chat ID."
            st.rerun()


# ============================================================ router

if page.startswith("🏠"):
    page_overview()
elif page.startswith("🔁"):
    page_pair()
elif page.startswith("⭐"):
    page_recommended()
elif page.startswith("🤖"):
    page_agent()
elif page.startswith("📈"):
    page_performance()
else:
    page_settings()
