# Trading Command Center

Satu platform: **Pair Trading + MA50 Retest** dan **Agent Signal on-chain** dalam satu
dashboard Streamlit + satu database Postgres (Neon), berjalan 24 jam via GitHub Actions.

| Komponen | Peran | Jadwal |
|---|---|---|
| `streamlit_app.py` | Dashboard gabungan + Pengaturan + ⭐ Recommended (Streamlit Cloud) | selalu on |
| `scanner.py` + `.github/workflows/scan.yml` | Scan pair & MA50, alert Telegram | tiap 15 menit |
| `recommend.py` + `.github/workflows/recommend.yml` | Rekomendasi pair (korelasi ≥ 0.90, multi-venue) → Telegram | 07:00 WIB |
| `agent_runner.py` + `.github/workflows/agent.yml` | Ingest on-chain (M1–M7), realtime alert, outlook 07:00 / digest 17:00 WIB / weekly review | lihat agent.yml |
| `core/` | Engine pair trading & MA50 | — |
| `pair-trading-v2/` | Screening korelasi multi-venue (Extended + Yahoo Brent + Binance PAXG/XAUT) + backtest z-score | — |
| `agent/` | Agent Signal 5-layer (fetchers → storage → engine → LLM → notify) | — |
| `agent_store.py` | Watchlist agent di DB — diedit dari halaman Pengaturan | — |

## ⭐ Recommended (baru)

Halaman **⭐ Recommended** menjalankan screening korelasi multi-venue dari `pair-trading-v2/`
dan menampilkan semua pair dengan |korelasi| ≥ 0.90 sebagai kandidat watchlist. Dari sana
kamu bisa mencentang pair untuk langsung masuk watchlist, atau kirim daftarnya ke Telegram.
`recommend.py` (via `recommend.yml`) mengirim daftar ini otomatis tiap pagi 07:00 WIB.

## Secrets yang dibutuhkan

**GitHub → Settings → Secrets and variables → Actions** dan **Streamlit Cloud → App secrets**:

| Secret | Wajib | Keterangan |
|---|---|---|
| `DATABASE_URL` | ✅ | Postgres Neon (sudah ada) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | ✅ | Satu bot untuk keduanya — pesan agent berprefix 🤖 [Agent Signal] |
| `COINGECKO_API_KEY` | ✅ (agent) | Demo key CoinGecko |
| `ETHERSCAN_API_KEY` | ✅ (agent M3) | Etherscan V2 |
| `ANTHROPIC_API_KEY` | opsional | Narasi outlook via LLM; kosong = template |

## Pengaturan

Semua dari halaman **⚙️ Pengaturan** di dashboard (tersimpan di DB, langsung dipakai
run Actions berikutnya): pair watchlist, market MA50, aset & protokol agent.
Threshold M1–M7/eskalasi tetap di `agent/action_rules.yaml` (terversi di Git, sengaja).

## Menjalankan lokal

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py          # dashboard (tanpa DATABASE_URL → Postgres localhost)
python agent_runner.py all-ingest       # isi data agent manual
python agent_runner.py outlook          # tes outlook
```
