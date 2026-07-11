"""Format pesan PERSIS mengikuti struktur_output_agent_v2.md.
Urutan blok daily outlook tidak boleh berubah antar hari (A-B-C-D-E + footer)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.engine.events import SignalGroup

LEVEL_EMOJI = {"INFO": "⚪", "MONITOR": "🟡", "REVIEW": "🟠",
               "RISK_OFF": "🔴", "OPPORTUNITY": "🟢"}
LEVEL_LABEL = {"RISK_OFF": "RISK-OFF", "OPPORTUNITY": "OPPORTUNITY",
               "REVIEW": "REVIEW", "MONITOR": "MONITOR", "INFO": "INFO"}

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]

FOOTER = "Sumber: CoinGecko · DefiLlama · Etherscan · Binance | Bukan nasihat keuangan"


def fmt_num(x: float, dec: int = 1) -> str:
    """Format angka gaya Indonesia: 63.966 / +2,1 / 0,05."""
    s = f"{x:,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return s


def fmt_pct(x: float, dec: int = 1) -> str:
    return ("+" if x >= 0 else "−") + fmt_num(abs(x), dec) + "%"


def fmt_usd(x: float) -> str:
    if abs(x) >= 1e9:
        return f"${fmt_num(x / 1e9, 1)}M"   # miliar
    if abs(x) >= 1e6:
        return f"${fmt_num(x / 1e6, 1)}jt"
    if abs(x) >= 1e3:
        return f"${fmt_num(x, 0)}"
    return f"${fmt_num(x, 2)}"


def tanggal_id(dt: datetime) -> str:
    return f"{HARI[dt.weekday()]}, {dt.day} {BULAN[dt.month]} {dt.year}"


def fmt_value(metric: str, value: float) -> str:
    """Nilai metrik: metrik *_usd pakai format dolar, sisanya angka biasa."""
    if metric.endswith("_usd") or metric == "transfer_usd":
        return fmt_usd(value)
    return fmt_num(value, 3).rstrip("0").rstrip(",") or "0"


def _kartu_sinyal(idx: int, g: SignalGroup, arti: str) -> str:
    """Kartu WAJIB 4 baris: Data -> Arti -> Aksi(Bias) -> Batal."""
    emoji = LEVEL_EMOJI[g.level]
    label = LEVEL_LABEL[g.level]
    posisi = " (kamu punya posisi terkait)" if g.touches_position else ""

    data_parts = []
    for e in g.events[:3]:
        src = e.source_url.split("/")[2].removeprefix("api.").removeprefix("www.") \
            if "://" in e.source_url else e.source_url
        if e.delta_pct is not None:
            data_parts.append(f"{e.metric} {fmt_pct(e.delta_pct)} ({src or e.module})")
        elif e.new_value is not None:
            data_parts.append(f"{e.metric}={fmt_value(e.metric, e.new_value)} ({src or e.module})")
    data = " + ".join(data_parts) or "-"

    aksi = {
        "RISK_OFF": "Pertimbangkan aksi defensif pada aset terkait hari ini",
        "OPPORTUNITY": "Masukkan ke daftar kandidat riset; tunggu konfirmasi teknikal sendiri",
        "REVIEW": "Cek posisi terkait hari ini juga; pastikan stop/invalidation masih valid",
        "MONITOR": "Belum ada. Pasang mata pada trigger level di atas",
        "INFO": "Tidak ada",
    }[g.level]

    return (f"{emoji} [C-{idx}] {label} — {g.entity}{posisi}\n"
            f"   Data   : {data}\n"
            f"   Arti   : {arti}\n"
            f"   Aksi   : {aksi}\n"
            f"   Batal  : {g.invalidation}")


def format_daily_outlook(now_wib: datetime, regime: tuple[int, str],
                         prev_score: int | None, snapshot: dict[str, Any],
                         groups: list[SignalGroup], arti_map: dict[str, str],
                         radar_lines: list[str]) -> str:
    score, label = regime
    # BLOK A — header + regime
    if prev_score is None:
        tren = ""
    else:
        arrow = "↑" if score > prev_score else ("↓" if score < prev_score else "→")
        tren = f"  (kemarin: {prev_score}, {arrow})"
    blok_a = (f"📊 OUTLOOK — {tanggal_id(now_wib)} · {now_wib:%H.%M} WIB\n"
              f"Regime: ⚖️ {label.replace('_', '-')}  |  Skor: {score}/100{tren}")

    # BLOK B — snapshot angka murni, tanpa interpretasi
    b_lines = []
    pr = snapshot.get("prices", {})
    if pr:
        pairs = [f"{sym} {fmt_usd(v['price'])}  {fmt_pct(v['change_24h'])} 24j"
                 for sym, v in list(pr.items())[:2]]
        b_lines.append(" | ".join(pairs))
    extra = []
    if snapshot.get("fng") is not None:
        extra.append(f"F&G: {snapshot['fng']} ({snapshot.get('fng_label', '')})")
    if snapshot.get("stablecoin_delta_7d") is not None:
        extra.append(f"Stablecoin 7h: {fmt_pct(snapshot['stablecoin_delta_7d'])}")
    if snapshot.get("funding_btc") is not None:
        extra.append(f"Funding BTC: {fmt_pct(snapshot['funding_btc'], 3)}/8j")
    if extra:
        b_lines.append(" · ".join(extra))
    if snapshot.get("breadth_green") is not None:
        b_lines.append(f"Watchlist: {snapshot['breadth_green']} hijau / "
                       f"{snapshot['breadth_red']} merah (24j)")
    blok_b = "\n".join(b_lines) or "Snapshot: data belum lengkap (cold start)"

    # BLOK C — kartu sinyal, maks 5, urut severity
    cards = [_kartu_sinyal(i + 1, g, arti_map.get(g.entity, ""))
             for i, g in enumerate(groups[:5])]
    blok_c = "\n\n".join(cards) if cards else "Tidak ada sinyal aktif."

    # BLOK D — action summary, maks 3 baris
    actionable = [g for g in groups[:5] if g.level in ("REVIEW", "RISK_OFF", "OPPORTUNITY")]
    monitor = [g for g in groups[:5] if g.level == "MONITOR"]
    d_lines = ["✅ AKSI HARI INI:"]
    n = 0
    for g in actionable:
        n += 1
        idx = groups.index(g) + 1
        verb = "Review posisi" if g.level in ("REVIEW", "RISK_OFF") else "Riset kandidat"
        d_lines.append(f"{n}. {LEVEL_EMOJI[g.level]} {verb} {g.entity} (lihat C-{idx})")
        if n == 2:
            break
    if n < 3 and monitor and n > 0:
        n += 1
        g = monitor[0]
        d_lines.append(f"{n}. 🟡 Pasang alert {g.entity} pada level pembatal (lihat C-{groups.index(g) + 1})")
    if n == 0:
        d_lines = ["✅ AKSI HARI INI:\nTidak ada aksi hari ini — semua sinyal level INFO/MONITOR."]
        blok_d = d_lines[0]
    else:
        d_lines.append(f"{n + 1}. ⚪ Tidak ada aksi lain. Sisanya konteks.")
        blok_d = "\n".join(d_lines[:4])

    # BLOK E — radar 24 jam
    blok_e = "👀 DIPANTAU: " + (" · ".join(radar_lines) if radar_lines
                                else "tidak ada agenda khusus — modul berjalan sesuai jadwal")

    return "\n\n".join([blok_a, blok_b, blok_c, blok_d, blok_e, FOOTER])


def format_realtime_alert(g: SignalGroup, arti: str, now_wib: datetime) -> str:
    """Struktur 5 field — hanya 🟠/🔴/🟢."""
    e0 = g.events[-1]
    emoji = LEVEL_EMOJI[g.level]
    tipe = e0.type.replace("_", " ").title()
    apa_parts = []
    for e in g.events[:2]:
        if e.delta_pct is not None:
            apa_parts.append(f"{e.entity}: {e.metric} {fmt_pct(e.delta_pct)}")
        elif e.new_value is not None:
            apa_parts.append(f"{e.entity}: {e.metric} = {fmt_value(e.metric, e.new_value)}")
    posisi = " — kamu tercatat punya posisi" if g.touches_position else ""
    kenapa = arti or f"{len(g.modules)} modul independen ({', '.join(g.modules)}) searah dalam 24 jam"
    return (f"{emoji} ALERT — {tipe} · {now_wib:%H.%M} WIB\n"
            f"APA    : {'; '.join(apa_parts)}\n"
            f"KENAPA : {kenapa}\n"
            f"BIAS   : {LEVEL_LABEL[g.level]} untuk {g.entity}{posisi}\n"
            f"BATAL  : {g.invalidation}\n"
            f"LINK   : {e0.source_url or '-'}")


def format_emergency_digest(groups: list[SignalGroup], now_wib: datetime) -> str:
    """Bundel saat cap 5 alert/hari terlampaui."""
    lines = [f"📦 DIGEST DARURAT · {now_wib:%H.%M} WIB — cap alert harian tercapai, "
             f"{len(groups)} sinyal dibundel:"]
    for g in groups:
        lines.append(f"{LEVEL_EMOJI[g.level]} {g.entity} — {LEVEL_LABEL[g.level]} "
                     f"({', '.join(g.modules)}) | Batal: {g.invalidation[:80]}")
    lines.append(FOOTER)
    return "\n".join(lines)


def format_evening_digest(groups: list[SignalGroup], now_wib: datetime) -> str:
    """Digest 17.00 WIB — sinyal 🟡 ke bawah yang tidak dikirim real-time."""
    header = f"🌇 DIGEST SORE — {tanggal_id(now_wib)} · {now_wib:%H.%M} WIB"
    if not groups:
        return f"{header}\nTidak ada sinyal baru sejak outlook pagi.\n{FOOTER}"
    lines = [header]
    for g in groups:
        e0 = g.events[-1]
        detail = f"{e0.metric} {fmt_pct(e0.delta_pct)}" if e0.delta_pct is not None \
            else f"{e0.metric}={fmt_value(e0.metric, e0.new_value or 0)}"
        lines.append(f"{LEVEL_EMOJI[g.level]} {g.entity}: {detail} — batal jika {g.invalidation[:70]}")
    lines.append(FOOTER)
    return "\n".join(lines)


def format_weekly_review(stats: dict[str, Any], now_wib: datetime) -> str:
    """Akuntabilitas agent (Minggu 08.00) — bahan kalibrasi threshold."""
    lines = [
        f"📈 SKOR MINGGU INI — {tanggal_id(now_wib)}",
        (f"Sinyal terkirim : {stats['total']} "
         f"({stats['by_level'].get('RISK_OFF', 0)}🔴, "
         f"{stats['by_level'].get('OPPORTUNITY', 0)}🟢, "
         f"{stats['by_level'].get('REVIEW', 0)}🟠, "
         f"{stats['by_level'].get('MONITOR', 0)}🟡)"),
        f"Terkonfirmasi   : {stats['confirmed']}  (invalidation tidak terpicu dalam 72j)",
        f"Batal/salah     : {stats['invalidated']}",
        f"Belum jelas     : {stats['pending']}",
    ]
    if stats.get("fp_rate_4w") is not None:
        target = stats.get("fp_target", 25)
        ok = "✅" if stats["fp_rate_4w"] <= target else "⚠️"
        lines.append(f"False-positive rate 4 minggu: {fmt_num(stats['fp_rate_4w'], 0)}% "
                     f"(target <{target}%) {ok}")
    if stats.get("calibration_note"):
        lines.append(f"Usulan kalibrasi: {stats['calibration_note']}")
    lines.append(FOOTER)
    return "\n".join(lines)


def format_followup(alert_entity: str, still_valid: bool, now_wib: datetime,
                    invalidation: str) -> str:
    status = "✔ sinyal masih valid" if still_valid else "✖ sinyal batal"
    return (f"🔁 FOLLOW-UP H+1 · {now_wib:%H.%M} WIB — {alert_entity}\n"
            f"Status : {status}\n"
            f"Acuan  : {invalidation}\n"
            f"{FOOTER}")
