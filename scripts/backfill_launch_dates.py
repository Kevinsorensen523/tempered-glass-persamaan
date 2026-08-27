"""
Isi kolom `tahun_launching`/`bulan_launching` di `hp` untuk baris yang masih
kosong (ditampilkan "Coming Soon" di UI), dengan mencocokkan merek+tipe_hp ke
data rilis dari Wikidata & Sertifikasi Postel/SDPPI — sumber yang SAMA dan
LEGAL dengan `find_new_phones.py` (fungsi fetch & matching di-reuse langsung
dari sana, jangan duplikasi). GSMArena TIDAK dipakai di sini juga, alasannya
sama seperti di find_new_phones.py: robots.txt-nya melarang bot AI.

Keterbatasan yang disengaja (bukan bug): Wikidata di-query cuma sejak
`since_date` (rilis lawas jarang ada P577-nya di Wikidata pula) dan Postel
cuma ambil `limit_per_brand` sertifikat teraktif per merek — jadi backfill ini
hanya akan berhasil ngisi HP yang rilisnya relatif baru & mereknya ada di
BRAND_QIDS/POSTEL_BRANDS (lihat find_new_phones.py). HP lama/merek di luar
whitelist itu akan tetap "Coming Soon" sampai ada sumber lain — itu wajar,
bukan berarti scriptnya salah jalan.

Cara pakai:
    python3 scripts/backfill_launch_dates.py
Bisa dipanggil manual, dijadwalkan cron bareng find_new_phones.py, atau lewat
tombol "Isi Tahun/Bulan Otomatis" di UI (POST /api/hp/backfill-launch di app.py).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_new_phones import (  # noqa: E402
    fetch_postel_phones,
    fetch_wikidata_phones,
    model_tokens,
    normalize_merek,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE_DIR, "hp_data.db")

BULAN_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


def build_release_index(since_date="2018-01-01"):
    """merek -> list of (token-set, release_date 'YYYY-MM-DD') dari kedua sumber."""
    sourced = (
        [(p, "wikidata") for p in fetch_wikidata_phones(since_date=since_date)]
        + [(p, "postel") for p in fetch_postel_phones()]
    )
    index: dict[str, list[tuple[set, str]]] = {}
    for p, _source in sourced:
        merek = normalize_merek(p["manufacturer"])
        if not merek or not p.get("release_date"):
            continue
        tokens = model_tokens(p["label"])
        if not tokens:
            continue
        index.setdefault(merek, []).append((tokens, p["release_date"]))
    return index


def find_release_date(merek: str, tipe_hp: str, index: dict) -> str | None:
    merek_key = merek.strip().upper()
    if merek_key not in index:
        return None
    cand_tokens = model_tokens(tipe_hp)
    if not cand_tokens:
        return None
    for tokens, release_date in index[merek_key]:
        if cand_tokens <= tokens or tokens <= cand_tokens:
            return release_date
    return None


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, merek, tipe_hp FROM hp WHERE TRIM(tahun_launching) = ''"
    ).fetchall()
    if not rows:
        print("Semua baris sudah punya tahun_launching (atau tabel kosong). Tidak ada yang perlu di-backfill.")
        return

    index = build_release_index()
    updated = 0
    for r in rows:
        release_date = find_release_date(r["merek"], r["tipe_hp"], index)
        if not release_date:
            continue
        try:
            tahun, bulan_num = release_date[:4], int(release_date[5:7])
        except (ValueError, IndexError):
            continue
        bulan = BULAN_ID.get(bulan_num)
        if not bulan:
            continue
        db.execute(
            "UPDATE hp SET tahun_launching = ?, bulan_launching = ? WHERE id = ?",
            (tahun, bulan, r["id"]),
        )
        updated += 1
    db.commit()
    print(f"Backfill selesai: {updated}/{len(rows)} baris kosong berhasil diisi tahun/bulan launching.")


if __name__ == "__main__":
    main()
