# 🚀 Panduan Deploy 24 Jam (Streamlit + GitHub Actions + Neon)

Panduan ini ditulis untuk pemula. Ikuti berurutan. Total ~30 menit, **semua gratis**.

Hasil akhir:
- 📊 **Dashboard online** (Streamlit) yang bisa dibuka dari HP/laptop mana saja.
- 🔔 **Scanner + alert Telegram jalan 24 jam** (GitHub Actions), walau laptop kamu mati.
- 🗄️ **Database awet** di cloud (Neon).

Arsitekturnya:

```
        ┌────────────────────┐
        │   Neon (Postgres)  │  ← database, dipakai bersama
        └─────────┬──────────┘
        ┌─────────┴──────────┐
        ▼                    ▼
┌───────────────┐   ┌──────────────────────┐
│ Streamlit App │   │  GitHub Actions cron  │
│  (dashboard)  │   │ scanner tiap 15 menit │
└───────────────┘   │  → alert Telegram     │
                    └──────────────────────┘
```

---

## Yang perlu disiapkan
- Akun **GitHub** (sudah punya ✅)
- Email untuk daftar **Neon** dan **Streamlit** (bisa "Sign in with GitHub")
- Token & Chat ID Telegram (sudah ada di project)

> ⚠️ **PENTING soal keamanan:** token Telegram kamu pernah tertulis di chat. Sebaiknya buat token baru:
> buka **@BotFather** di Telegram → `/revoke` → pilih bot → dapat token baru. Pakai token baru itu di langkah bawah.

---

## LANGKAH 1 — Buat Database gratis di Neon

1. Buka **https://neon.tech** → **Sign up** (pilih "Continue with GitHub" biar cepat).
2. Setelah masuk, klik **Create project** (nama bebas, mis. `trading-signals`). Region pilih yang dekat (mis. Singapore).
3. Setelah project jadi, cari tombol **Connect** / **Connection string**.
4. Salin **connection string**-nya. Bentuknya seperti ini:
   ```
   postgresql://neondb_owner:xxxxxxxx@ep-cool-name-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
   Simpan baik-baik — ini yang akan dipakai di 2 tempat nanti. **Jangan bagikan ke siapa pun.**

> Kalau connection string tidak ada `?sslmode=require` di ujungnya, tambahkan sendiri.

---

## LANGKAH 2 — Upload folder `cloud` ke GitHub

> ✅ **Cara paling aman & disarankan: GitHub Desktop.** Ia otomatis mengikuti `.gitignore`,
> jadi folder `venv` (besar) dan file rahasia `secrets.toml` **tidak** ikut ter-upload.
> Install dari https://desktop.github.com → login → **File → Add local repository** → arahkan ke folder `cloud`
> → **Publish repository** (centang **Keep this code private**). Selesai — lompat ke Langkah 3.

Kalau tidak mau install apa pun, pakai cara upload lewat web di bawah:

1. Buka **https://github.com/new** → buat repository baru:
   - Name: `trading-signals` (bebas)
   - Pilih **Private** (biar tidak dilihat orang)
   - **Jangan** centang "Add README"
   - Klik **Create repository**.
2. Di halaman repo kosong, klik link **"uploading an existing file"**.
3. Buka folder **`cloud`** di komputer (`D:\2. Project Builder\1. Pair Trading\1\cloud`).
   **Seret SEMUA isi di dalam folder `cloud`** (bukan folder cloud-nya, tapi isinya:
   `streamlit_app.py`, `scanner.py`, `requirements.txt`, folder `core`, folder `.github`, folder `.streamlit`, dll)
   ke area upload GitHub.

   > ⚠️ **JANGAN upload folder `venv`** (besar & tidak perlu) dan **JANGAN upload `.streamlit/secrets.toml`**
   > (rahasia — untungnya sudah saya hapus, cuma ada `secrets.toml.example` yang aman).
   >
   > 💡 Kalau folder `.github` atau `.streamlit` tidak ikut ter-drag (karena diawali titik), tidak apa —
   > lihat catatan "Kalau folder titik tidak keupload" di bawah.
4. Di bawah, klik **Commit changes**.

### Kalau folder titik (`.github`, `.streamlit`) tidak ikut ke-upload
Windows kadang menyembunyikan folder berawalan titik. Dua pilihan:
- **Pilihan A (paling aman): pakai GitHub Desktop.** Install dari https://desktop.github.com, login, "Add local repository" arahkan ke folder `cloud`, lalu Publish. Semua file (termasuk folder titik) ikut.
- **Pilihan B:** buat foldernya langsung di web GitHub → **Add file → Create new file**, ketik `.github/workflows/scan.yml` sebagai nama (GitHub otomatis bikin foldernya), lalu tempel isi file `scan.yml`. Ini yang WAJIB ada supaya scanner 24 jam jalan.

> File `.streamlit/config.toml` opsional (cuma tema). File `.streamlit/secrets.toml` **jangan** di-upload (rahasia).

---

## LANGKAH 3 — Pasang rahasia (Secrets) untuk scanner 24 jam

Ini yang membuat GitHub bisa konek ke database & Telegram.

1. Di repo GitHub kamu → **Settings** (tab paling kanan) → menu kiri **Secrets and variables** → **Actions**.
2. Klik **New repository secret**, tambahkan **satu per satu** (Name harus persis):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | connection string Neon dari Langkah 1 |
   | `TELEGRAM_BOT_TOKEN` | token bot Telegram (yang baru) |
   | `TELEGRAM_CHAT_ID` | `403542902` (chat ID kamu) |

3. Selesai. Buka tab **Actions** di repo. Kalau ada tulisan minta enable workflow, klik **enable**.
4. Klik workflow **"Trading Signal Scanner (24/7)"** → **Run workflow** (tombol kanan) untuk tes jalan pertama.
   Kalau centang hijau ✅ = scanner sukses konek DB + exchange. Nanti dia jalan otomatis tiap 15 menit.

---

## LANGKAH 4 — Deploy Dashboard ke Streamlit

1. Buka **https://share.streamlit.io** → **Continue with GitHub** → izinkan akses.
2. Klik **Create app** / **Deploy an app**.
3. Isi:
   - **Repository**: pilih `trading-signals` (repo tadi).
   - **Branch**: `main`
   - **Main file path**: `streamlit_app.py`
4. Klik **Advanced settings** → di kotak **Secrets**, tempel ini (ganti nilainya):
   ```toml
   DATABASE_URL = "postgresql://...connection string Neon kamu..."
   TELEGRAM_BOT_TOKEN = "token-bot-telegram"
   TELEGRAM_CHAT_ID = "403542902"
   EXTENDED_BASE_URL = "https://api.starknet.extended.exchange"
   ```
   (Kalau ada pilihan Python version, pilih **3.12**.)
5. Klik **Deploy**. Tunggu 2–4 menit sampai muncul dashboard-nya.
6. Kamu akan dapat link seperti `https://trading-signals-xxxx.streamlit.app` — **itu dashboard online kamu.** Bisa dibuka dari HP.

---

## Selesai! Cara kerjanya sekarang

| Kapan | Apa yang terjadi |
|---|---|
| Setiap 15 menit, 24 jam | GitHub Actions menjalankan `scanner.py` → cek watchlist → kalau ada sinyal, **kirim Telegram** + simpan ke Neon |
| Kapan pun kamu buka link Streamlit | Dashboard menampilkan winrate, P&L, watchlist, sinyal — data dari Neon yang sama |
| Kamu klik "Scan Sekarang" di dashboard | Scan manual langsung saat itu juga |

Kamu **tidak perlu menyalakan laptop**. Alert tetap masuk Telegram.

---

## ⚠️ Catatan penting (wajib baca)

1. **GitHub Actions berhenti kalau repo menganggur 60 hari.** Kalau kamu tidak pernah commit apa pun selama 60 hari, GitHub otomatis menonaktifkan jadwal cron. Cukup buka tab **Actions** dan klik **Enable** lagi (atau commit apa saja) untuk menyalakannya kembali. Selama kamu sesekali buka repo, aman.

2. **Jadwal cron GitHub kadang telat 5–15 menit** saat server GitHub ramai. Jadi "tiap 15 menit" bisa jadi 20–30 menit. Ini normal untuk layanan gratis — tidak masalah untuk sinyal daily.

3. **Neon free tier** cukup besar untuk ini, tapi database "tidur" saat tak dipakai lalu bangun otomatis (~1 detik) saat scanner konek. Tidak masalah.

4. **Streamlit free** akan menidurkan dashboard kalau tidak dibuka beberapa hari — tapi begitu kamu buka linknya, dia bangun sendiri. Yang penting scanner (GitHub Actions) tetap jalan terpisah, jadi **alert 24 jam tidak terpengaruh** oleh dashboard yang tidur.

5. **Ganti watchlist** cukup lewat dashboard (menu Watchlist). Perubahan langsung dipakai scanner berikutnya karena database-nya sama.

---

## Kalau ada yang error

| Masalah | Solusi |
|---|---|
| GitHub Actions merah ❌ | Klik run-nya, lihat log. Biasanya `DATABASE_URL` salah/kurang `?sslmode=require`. Perbaiki di Settings → Secrets. |
| Dashboard: "Tidak bisa terhubung ke database" | Secret `DATABASE_URL` di Streamlit salah. Betulkan di app → **⋮ → Settings → Secrets**. |
| Telegram tidak masuk | Klik "🔔 Tes Telegram" di dashboard. Kalau gagal, cek token & chat ID. Pastikan kamu sudah tekan **Start** di bot. |
| Mau ganti kode | Edit file di GitHub (atau GitHub Desktop → push). Streamlit auto-redeploy; scanner pakai versi terbaru otomatis. |

---

## Tes di komputer sendiri dulu (opsional)
Kalau mau mencoba dashboard lokal sebelum deploy:
```powershell
cd "D:\2. Project Builder\1. Pair Trading\1\cloud"
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
# buat file .streamlit\secrets.toml (contoh ada di secrets.toml.example)
venv\Scripts\python.exe -m streamlit run streamlit_app.py
```
Buka http://localhost:8501. (Untuk lokal, `DATABASE_URL` bisa pakai Postgres lokal maupun Neon.)

> ⚠️ **Disclaimer**: sinyal berbasis aturan statistik/teknikal, **bukan jaminan profit** dan bukan saran finansial. Kelola risiko sendiri.
