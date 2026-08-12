# Session Log — 2026-07-29

Ringkasan lengkap semua yang dikerjakan hari ini di project Database Tempered Glass.
Disimpan biar bisa dilanjut/dicek lagi tanpa perlu mengulang dari nol.

## 1. Setup awal
- Dibuat `CLAUDE.md` — dokumentasi project (summary, tech stack, data model, known issues, TODO).

## 2. Perbaikan data (`hp_data.db`)

Semua langkah di bawah bikin backup otomatis ke `backups/` sebelum eksekusi.

1. **311 baris `alternatif` corrupt** (double-encoded JSON, hasil CSV lama yang
   dibuka-simpan di Excel tanpa parsing yang benar) — dibersihkan, kode asli
   diekstrak dari teks rusak.
2. **Format CSV import/export diubah**: kolom `ALTERNATIF` sekarang pakai kode
   dipisah titik-koma (`;`) alih-alih JSON array (`["...","..."]`) — JSON di CSV
   gampang rusak kalau dibuka di Excel/Sheets. `_parse_alternatif()` di `app.py`
   juga dikasih sanitizer (`_sanitize_code`) biar data kotor apapun bentuknya
   otomatis dibersihkan saat import, gak nyangkut lagi.
3. **Normalisasi kode**: semua kode di sistem diseragamkan tanpa tanda strip
   (`TG-0018` → `TG0018`). Ditambah `_normalize_kode()` di `app.py` biar import
   ke depannya otomatis konsisten.
4. **Import 347 baris baru dari `TG-Accurate.xlsx`** (export item master, kemungkinan
   dari software Accurate) — 223 kode (TG0001–TG0318) yang belum ada di sistem
   ditambahkan, nama produk di-parse jadi `tipe_hp`/`merek`/`jenis_tg` yang rapi
   (boilerplate "No Brand Tempered Glass..." dibuang, `jenis_tg` disingkat jadi
   label pendek: Clear, Anti Spy 180, Ceramic Spy, dst).
   - `TG-Accurate.xlsx` **sengaja tidak di-commit ke git** (ada di `.gitignore`,
     dianggap data bisnis).
5. **Relasi `alternatif` disimetriskan**: kalau kode A punya alternatif B, B juga
   otomatis dikasih alternatif A. Awalnya ada 31 relasi satu-arah, sekarang 0.
   *(Catatan: relasi ini gak di-enforce otomatis oleh app — kalau `alternatif`
   diedit manual di luar alur import biasa, bisa drift lagi. Lihat TODO di
   `CLAUDE.md`.)*
6. **`jenis_tg` distandarisasi**: 814 baris `"Clear 0.3mm / Bening 0.3mm"` → `"Clear"`.
   (`"ESD Anti Spy 180"` vs `"Anti Spy 180"` sengaja TIDAK digabung, atas permintaan user.)
7. **Fix typo kode**: `TG048` (3 digit, kurang satu nol) → `TG0048` (11 baris).
8. **Fix bug parsing xlsx**: 332 baris `tipe_hp` Apple yang kehilangan kata "IPHONE"
   (mis. `APPLE XS` → `APPLE IPHONE XS`) — bug di parser saya sendiri, kata brand
   "IPHONE" ke-strip abis padahal itu bagian penting nama model.
9. **Fix data lama**: 1 baris `VIVO Y05` yang salah tercatat `merek='XIAOMI'` → `VIVO`.
10. **Kolom baru `merek_tg`** ditambahkan ke tabel `hp` (merek tempered glass,
    terpisah dari `merek` yang berarti merek HP). Backfill: 30 baris kode
    `TG0166`–`TG0189` = `"Spigen"`, 317 baris lain dari xlsx = `"No Brand"`,
    1602 baris data lama dibiarkan kosong (gak ada sumber infonya).

## 3. Perubahan kode (`app.py`, `templates/index.html`)

- `_normalize_kode()`, `_sanitize_code()`, `_parse_alternatif()` — hardening parsing.
- Format CSV import/export/template disesuaikan (kolom `MEREK TG` ditambahkan).
- Kolom `merek_tg` diikutkan di API (`GET /api/hp`), search, export, import.
- Migrasi kolom otomatis di `get_db()` (pola yang sudah ada, di-reuse).
- Frontend: kolom baru "Merek TG" di tabel, search hint diupdate.

## 4. Git & deploy ke server produksi

- Push ke GitHub (`https://github.com/Kevinsorensen523/tempered-glass-persamaan`).
  **Data (`hp_data.db`, `backups/`, `*.xlsx`, `reports/`) sengaja TIDAK ikut di-push**
  — semua sudah di-`.gitignore`.
- Server produksi: `192.168.68.50:8080`, SSH user `pc010`, path
  `/home/pc010/tempered-glass-persamaan`, service `tempered-glass.service` (systemd,
  gunicorn). Detail SSH ini sempat lupa, ditemukan lagi lewat `~/.ssh/config` +
  `known_hosts` di komputer ini.
- Waktu deploy pertama, server ternyata masih di commit paling lama + ada
  perubahan lokal belum ke-commit di sana (versi awal fitur password
  export/import, sudah lebih baik & dibenerin di versi terbaru) — perubahan lokal
  itu di-discard (`git checkout -- .`) atas persetujuan user, baru `git pull`.
- Restart service butuh `sudo`, password ditemukan setelah beberapa percobaan
  salah (**jangan simpan password di sini** — sudah dipakai on-the-spot, tidak
  dicatat di file manapun).

## 5. Fitur "HP Baru" (`/hp-baru`)

**Tujuan awal user**: scraping GSMArena buat nemuin HP baru yang belum ada di sistem,
dengan syarat "jangan kena rate limiter".

**Kenapa GSMArena ditolak**: `robots.txt` mereka eksplisit larang `ClaudeBot` dan
`anthropic-ai` by name, dan file `/license.xml` (RSL license) eksplisit melarang
`ai-inference`/`ai-train`. Ini sinyal jelas dari pemilik situs, jadi tidak dikerjakan
sama sekali — termasuk saat user kasih link wrapper pihak ketiga (`nordmarin/gsmarena-api`,
Postman collection) yang ternyata scraper juga di baliknya.

**Sumber lain yang dicoba & hasilnya:**

| Sumber | Status | Kenapa |
|---|---|---|
| GSMArena | ❌ Ditolak | robots.txt eksplisit larang Claude/AI bot + RSL license |
| kimovil.com | ❌ Gak bisa | robots.txt permisif, tapi Cloudflare block (403) |
| 91mobiles.com | ❌ Gak bisa | sama, Cloudflare block (403) |
| smartprix.com | ❌ Gak bisa | sama, Cloudflare block (403) |
| phonedb.net | ⚠️ Bisa tapi kurang berguna | robots.txt permisif & aksesnya jalan, tapi cuma expose "device baru ditambahin ke DB mereka" (homepage feed), bukan katalog lengkap per-merek — coverage gak konsisten/tergantung timing update mereka |
| **Wikidata** (`query.wikidata.org`) | ✅ Dipakai | SPARQL endpoint resmi, data CC0, robots.txt izinkan. **Tapi coverage sangat timpang**: cuma 23 item smartphone se-dunia yang punya tanggal rilis (P577) terisi sejak 2024 — data volunteer-based, prioritas ke merek populer (Samsung, Apple), merek budget/regional (Infinix/Tecno/itel/realme/Vivo/Oppo) nyaris kosong |
| Postel/SDPPI (`sertifikasi.postel.go.id`) | 🔶 Belum selesai diriset | Database sertifikasi resmi Kominfo — **paling menjanjikan** karena mencakup SEMUA merek yang legal dijual di Indonesia (wajib sertifikasi). Gak ada `robots.txt`. Ketemu endpoint API internal (`/svc/master/query`, kelihatan di config Nuxt frontend mereka), tapi situsnya load reCAPTCHA site key juga — belum dipastikan apakah endpoint pencarian publik butuh captcha token atau tidak. **Ini prioritas TODO berikutnya.** |

**Yang dibangun:**
- `scripts/find_new_phones.py` — query Wikidata (13 brand QID target: Samsung,
  Xiaomi, OPPO, vivo, Infinix, realme, Tecno, itel, Apple, Huawei, Honor,
  Motorola, OnePlus — QID dicari manual dari data Wikidata sendiri, bukan
  ditebak, karena tebakan awal salah semua kecuali Apple), bandingin ke
  `hp_data.db`, output ke `reports/new_phone_candidates.json` (gitignored).
  Ada `reports/new_phone_seen.json` biar gak lapor kandidat yang sama berulang.
- **Bug matching yang sempat kejadian & dibenerin**: awalnya pakai
  `difflib.SequenceMatcher` ratio (threshold 0.55) buat cek "udah ada di sistem
  atau belum" — ternyata gampang salah: `iPhone 18 Pro` sempat dianggap "sudah
  ada" (ke-match sama `iPhone 11 Pro` gara-gara banyak karakter mirip), sementara
  `Samsung Galaxy A35 5G` sempat dianggap "baru" padahal udah ada (tersimpan
  sebagai `SAMSUNG A35`, beda penulisan). **Diganti** jadi pencocokan per-merek
  (pakai kolom `merek` asli, bukan tebakan) + token-set model (`already_exists()`
  di `scripts/find_new_phones.py`) — jangan balik ke pendekatan ratio, sudah
  terbukti gak akurat.
- Cron job di server: tiap Senin jam 08:00, `venv/bin/python3
  scripts/find_new_phones.py`, log ke `reports/cron.log`.
- Halaman `GET /hp-baru` + endpoint `GET /api/hp-baru` di `app.py` — biar hasil
  laporan bisa dilihat langsung di browser (`http://192.168.68.50:8080/hp-baru`),
  gak perlu SSH/command apapun. Ada tombol "📱 Cek HP Baru" di header halaman utama.

**Status akhir**: fitur jalan dan akurat, tapi **jumlah hasilnya sedikit
(saat ini 5 kandidat) bukan representasi HP baru yang sebenarnya beredar** —
itu representasi keterbatasan Wikidata. User merasa ini "kurang lengkap" (valid),
solusinya ada di TODO Postel/SDPPI di bawah.

## 6. TODO lanjutan (belum selesai)

1. **Riset Postel/SDPPI** (`sertifikasi.postel.go.id`) sebagai sumber tambahan
   yang mencakup semua merek — cek apakah endpoint `/svc/master/query` butuh
   reCAPTCHA token, cari halaman publik pencarian sertifikat yang sebenarnya
   (belum ketemu di eksplorasi awal, cuma ketemu SPA shell + config).
   **Jangan coba bypass captcha kalau ternyata ada.**
2. Password `EXPORT_PASSWORD`/`IMPORT_PASSWORD` masih hardcoded plaintext di
   `app.py` — belum dipindah ke env var (lihat `CLAUDE.md`).
3. Belum ada test suite otomatis sama sekali di project ini.
4. Sinkronisasi data server ↔ lokal: terakhir dicek, server masih pakai
   `hp_data.db` versi lama (sebelum semua perbaikan di atas) — user bilang mau
   sinkron sendiri, belum dikonfirmasi sudah selesai atau belum.
