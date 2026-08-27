import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import zipfile
from datetime import datetime

from flask import Flask, g, jsonify, render_template, request, send_file

app = Flask(__name__)
DB_PATH = "hp_data.db"
SMARTWATCH_DB_PATH = "smartwatch_data.db"
BACKUP_DIR = "backups"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
NEW_PHONE_REPORT = os.path.join(REPORTS_DIR, "new_phone_candidates.json")
NEW_PHONE_SEEN = os.path.join(REPORTS_DIR, "new_phone_seen.json")
NEW_PHONE_REFRESH_LOCK = os.path.join(REPORTS_DIR, "refresh.lock")
NEW_PHONE_SCRIPT = os.path.join(BASE_DIR, "scripts", "find_new_phones.py")
LAUNCH_BACKFILL_LOCK = os.path.join(REPORTS_DIR, "backfill_launch.lock")
LAUNCH_BACKFILL_SCRIPT = os.path.join(BASE_DIR, "scripts", "backfill_launch_dates.py")

# Placeholder pengganti tahun/bulan launching kalau belum ketemu datanya
# (belum berhasil dicocokkan ke Wikidata/Postel) — lihat CLAUDE.md.
LAUNCH_UNKNOWN = "Coming Soon"


# ── database ──────────────────────────────────────────────────────────────────

def _ensure_columns(conn, table: str, coldefs: dict[str, str]) -> None:
    """Tambah kolom yang belum ada ke `table` (migrasi in-place utk DB lama)."""
    existing_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in coldefs.items():
        if name not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    conn.commit()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("""
            CREATE TABLE IF NOT EXISTS hp (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kode       TEXT NOT NULL,
                tipe_hp    TEXT NOT NULL,
                merek      TEXT NOT NULL,
                jenis_tg   TEXT NOT NULL DEFAULT '',
                alternatif TEXT NOT NULL DEFAULT '[]',
                merek_tg   TEXT NOT NULL DEFAULT '',
                kode_merek_tg TEXT NOT NULL DEFAULT ''
            )
        """)
        _ensure_columns(g.db, "hp", {
            "jenis_tg":         "TEXT NOT NULL DEFAULT ''",
            "alternatif":       "TEXT NOT NULL DEFAULT '[]'",
            "merek_tg":         "TEXT NOT NULL DEFAULT ''",
            "kode_merek_tg":    "TEXT NOT NULL DEFAULT ''",
            "tahun_launching":  "TEXT NOT NULL DEFAULT ''",
            "bulan_launching":  "TEXT NOT NULL DEFAULT ''",
        })
    return g.db


def get_smartwatch_db():
    """DB terpisah dari `hp` (file sendiri, `smartwatch_data.db`) khusus data
    ukuran smartwatch -> tempered glass yang cocok. Dipisah dari hp_data.db
    biar gak campur aduk skema (smartwatch pakai `ukuran`, bukan `tipe_hp`)."""
    if "swdb" not in g:
        g.swdb = sqlite3.connect(SMARTWATCH_DB_PATH)
        g.swdb.row_factory = sqlite3.Row
        g.swdb.execute("""
            CREATE TABLE IF NOT EXISTS smartwatch (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                kode             TEXT NOT NULL,
                tipe_smartwatch  TEXT NOT NULL,
                merek            TEXT NOT NULL,
                ukuran           TEXT NOT NULL DEFAULT '',
                jenis_tg         TEXT NOT NULL DEFAULT '',
                alternatif       TEXT NOT NULL DEFAULT '[]',
                merek_tg         TEXT NOT NULL DEFAULT '',
                kode_merek_tg    TEXT NOT NULL DEFAULT ''
            )
        """)
        _ensure_columns(g.swdb, "smartwatch", {
            "ukuran":        "TEXT NOT NULL DEFAULT ''",
            "jenis_tg":      "TEXT NOT NULL DEFAULT ''",
            "alternatif":    "TEXT NOT NULL DEFAULT '[]'",
            "merek_tg":      "TEXT NOT NULL DEFAULT ''",
            "kode_merek_tg": "TEXT NOT NULL DEFAULT ''",
        })
    return g.swdb


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()
    swdb = g.pop("swdb", None)
    if swdb:
        swdb.close()


def _row_to_dict(r):
    try:
        alt = json.loads(r["alternatif"]) if r["alternatif"] else []
        if not isinstance(alt, list):
            alt = []
    except Exception:
        alt = []
    return {
        "id":        r["id"],
        "kode":      r["kode"],
        "tipe_hp":   r["tipe_hp"],
        "merek":     r["merek"],
        "jenis_tg":  r["jenis_tg"],
        "alternatif": alt,
        "merek_tg":  r["merek_tg"],
        "kode_merek_tg": r["kode_merek_tg"],
        "tahun_launching": r["tahun_launching"] or LAUNCH_UNKNOWN,
        "bulan_launching": r["bulan_launching"] or LAUNCH_UNKNOWN,
    }


def _smartwatch_row_to_dict(r):
    try:
        alt = json.loads(r["alternatif"]) if r["alternatif"] else []
        if not isinstance(alt, list):
            alt = []
    except Exception:
        alt = []
    return {
        "id":              r["id"],
        "kode":            r["kode"],
        "tipe_smartwatch": r["tipe_smartwatch"],
        "merek":           r["merek"],
        "ukuran":          r["ukuran"],
        "jenis_tg":        r["jenis_tg"],
        "alternatif":      alt,
        "merek_tg":        r["merek_tg"],
        "kode_merek_tg":   r["kode_merek_tg"],
    }


_CODE_TOKEN = re.compile(r"[A-Za-z0-9\-]+")


def _normalize_kode(kode: str) -> str:
    """Standar kode di sistem ini: tanpa tanda strip (mis. TG-0018 -> TG0018)."""
    return kode.replace("-", "").strip()


def _sanitize_code(item: str) -> str | None:
    """Ambil token kode yang valid dari sebuah teks, buang sisa kurung/kutip
    nyasar (mis. hasil CSV yang rusak karena dibuka-simpan di Excel)."""
    match = _CODE_TOKEN.search(item.strip())
    return _normalize_kode(match.group(0)) if match else None


def _parse_alternatif(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    items: list[str]
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            items = [str(a) for a in parsed]
        except Exception:
            items = [raw]
    else:
        # format utama: dipisah titik-koma (atau koma), tanpa kurung/kutip
        sep = ";" if ";" in raw else ","
        items = raw.split(sep)
    cleaned = [_sanitize_code(a) for a in items]
    return [c for c in cleaned if c]


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/hp-baru")
def hp_baru_page():
    return render_template("hp_baru.html")


@app.get("/api/hp-baru")
def api_hp_baru():
    if not os.path.exists(NEW_PHONE_REPORT):
        return jsonify({"generated_at": None, "new_candidates": []})
    with open(NEW_PHONE_REPORT) as f:
        return jsonify(json.load(f))


@app.post("/api/hp-baru/mark")
def mark_hp_baru_done():
    """Tandai satu kandidat HP baru sebagai selesai diproses (sudah dimasukin
    ke DB / gak relevan) — dihapus dari daftar pending & gak bakal dilaporin
    lagi sama cron find_new_phones.py ke depannya."""
    key = (request.get_json(silent=True) or {}).get("key", "").strip()
    if not key:
        return jsonify({"error": "key wajib diisi"}), 400

    seen = set()
    if os.path.exists(NEW_PHONE_SEEN):
        with open(NEW_PHONE_SEEN) as f:
            seen = set(json.load(f))
    seen.add(key)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(NEW_PHONE_SEEN, "w") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)

    if os.path.exists(NEW_PHONE_REPORT):
        with open(NEW_PHONE_REPORT) as f:
            report = json.load(f)
        report["new_candidates"] = [p for p in report.get("new_candidates", []) if p.get("key") != key]
        with open(NEW_PHONE_REPORT, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return jsonify({"marked": key})


def _run_hp_baru_refresh():
    """Jalanin scripts/find_new_phones.py di thread background (dipanggil dari
    endpoint refresh), biar request dari browser gak nunggu ~15-20 detik dan
    ketimpuk gunicorn worker timeout default (30s, gak di-override di systemd
    unit). Output digabung ke reports/cron.log yg sama kayak run cron mingguan."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    try:
        with open(os.path.join(REPORTS_DIR, "cron.log"), "a") as log:
            subprocess.run(
                [sys.executable, NEW_PHONE_SCRIPT],
                cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, timeout=120,
            )
    finally:
        try:
            os.remove(NEW_PHONE_REFRESH_LOCK)
        except FileNotFoundError:
            pass


@app.post("/api/hp-baru/refresh")
def refresh_hp_baru():
    """Trigger manual cari HP baru sekarang, gak nunggu cron Senin. Dikunci
    pakai lock file (bukan cuma flag in-memory) biar aman kalau dua request
    nyasar ke worker gunicorn yg beda."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    try:
        fd = os.open(NEW_PHONE_REFRESH_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return jsonify({"error": "Refresh masih berjalan, tunggu sebentar."}), 409
    threading.Thread(target=_run_hp_baru_refresh, daemon=True).start()
    return jsonify({"started": True})


@app.get("/api/hp-baru/refresh-status")
def refresh_hp_baru_status():
    return jsonify({"running": os.path.exists(NEW_PHONE_REFRESH_LOCK)})


@app.get("/api/hp")
def list_hp():
    q = request.args.get("q", "").strip()
    merek = request.args.get("merek", "").strip()
    jenis_tg = request.args.get("jenis_tg", "").strip()
    merek_tg = request.args.get("merek_tg", "").strip()
    tahun_launching = request.args.get("tahun_launching", "").strip()
    recent_only = request.args.get("recent_only", "").strip() == "1"

    where = []
    params: list[str] = []
    if q:
        like = f"%{q}%"
        where.append(
            "(kode LIKE ? OR tipe_hp LIKE ? OR merek LIKE ? "
            "OR jenis_tg LIKE ? OR alternatif LIKE ? OR merek_tg LIKE ? OR kode_merek_tg LIKE ?)"
        )
        params += [like, like, like, like, like, like, like]
    if merek:
        where.append("merek = ?")
        params.append(merek)
    if jenis_tg:
        where.append("jenis_tg = ?")
        params.append(jenis_tg)
    if merek_tg:
        where.append("merek_tg = ?")
        params.append(merek_tg)
    if tahun_launching:
        where.append("tahun_launching = ?")
        params.append(tahun_launching)
    if recent_only:
        # HP dgn tahun_launching terisi & >= setahun terakhir. CAST atas string
        # non-numerik (mis. kosong) balikin 0 di SQLite, jadi otomatis kefilter
        # keluar tanpa perlu cek "Coming Soon" secara eksplisit.
        where.append("CAST(tahun_launching AS INTEGER) >= ?")
        params.append(datetime.now().year - 1)

    sql = ("SELECT id, kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg, "
           "tahun_launching, bulan_launching FROM hp")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY kode"

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    total = db.execute("SELECT COUNT(*) FROM hp").fetchone()[0]
    return jsonify({"data": [_row_to_dict(r) for r in rows], "total": total})


@app.get("/api/hp/filters")
def hp_filter_options():
    """Nilai unik untuk isi dropdown filter (merek, jenis TG, merek TG, tahun launching)."""
    db = get_db()

    def distinct(col: str) -> list[str]:
        rows = db.execute(
            f"SELECT DISTINCT {col} FROM hp WHERE TRIM({col}) != '' ORDER BY {col} COLLATE NOCASE"
        ).fetchall()
        return [r[0] for r in rows]

    return jsonify({
        "merek": distinct("merek"),
        "jenis_tg": distinct("jenis_tg"),
        "merek_tg": distinct("merek_tg"),
        "kode_merek_tg": distinct("kode_merek_tg"),
        "tahun_launching": sorted(
            (t for t in distinct("tahun_launching") if t.isdigit()), reverse=True
        ),
    })


@app.post("/api/hp/backfill-launch")
def backfill_launch_dates():
    """Trigger scripts/backfill_launch_dates.py di background: cocokkan HP yg
    tahun_launching-nya masih kosong ('Coming Soon') ke data Wikidata/Postel
    (sumber yg sama & legal dgn HP Baru finder — GSMArena TIDAK dipakai, lihat
    CLAUDE.md), isi tahun/bulan-nya kalau ketemu. Pola lock file sama persis
    kayak /api/hp-baru/refresh biar gak dobel jalan dari worker gunicorn beda."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    try:
        fd = os.open(LAUNCH_BACKFILL_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return jsonify({"error": "Proses isi tahun/bulan masih berjalan, tunggu sebentar."}), 409
    threading.Thread(target=_run_launch_backfill, daemon=True).start()
    return jsonify({"started": True})


def _run_launch_backfill():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    try:
        with open(os.path.join(REPORTS_DIR, "cron.log"), "a") as log:
            subprocess.run(
                [sys.executable, LAUNCH_BACKFILL_SCRIPT],
                cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, timeout=120,
            )
    finally:
        try:
            os.remove(LAUNCH_BACKFILL_LOCK)
        except FileNotFoundError:
            pass


@app.get("/api/hp/backfill-launch-status")
def backfill_launch_status():
    return jsonify({"running": os.path.exists(LAUNCH_BACKFILL_LOCK)})


# ── export JSON untuk bot upload ────────────────────────────────────────────
# Aturan ini disepakati manual sama user (bukan tebakan):
# - 1 file JSON = 1 listing = array FLAT [{value, sku, price, stock}, ...]
#   (persis format yang bot user harapkan, tanpa wrapper product_name).
# - Nama file = judul deskriptif ("Tempered Glass {merek} {contoh model} dll
#   {merek_tg} {kode_merek_tg} {jenis_tg}"), bukan cuma nama brand.
# - 1 listing = 1 (kode_merek_tg, merek); kalau > BOT_EXPORT_VARIANT_LIMIT
#   varian, dipecah per "seri" (heuristik: token nama sebelum ketemu angka
#   model, mis. "REDMI NOTE 8" -> seri "REDMI NOTE", "SAMSUNG A50" -> seri
#   "A"); kalau 1 seri masih kelebihan, dipecah lagi per 50 (Part 1/2/dst).
# - Varian diurutkan "natural" berdasar angka model pertama (11, 15, 17 — bukan
#   urutan sembarang dari DB).
# - "value" (nama varian yang tampil ke bot) maks 20 karakter: nama merek
#   dibuang (udah kewakilan di judul file), spasi di sekitar "/" dirapatkan,
#   baru dipotong keras di karakter ke-20 kalau masih kelebihan.
# - "sku" tetap dibangun dari value ASLI (bukan yang dipotong) + kode TG-nya
#   sendiri, supaya tetap unik/tertelusur walau "value" tampilnya disingkat.

BOT_EXPORT_VARIANT_LIMIT = 50
BOT_EXPORT_VALUE_MAX_LEN = 20
_SERIES_DIGIT_RE = re.compile(r"\d")
_LEADING_ALPHA_RE = re.compile(r"^([A-Za-z]+)")
_MODEL_NUM_RE = re.compile(r"\d+")
_SLASH_SPACE_RE = re.compile(r"\s*/\s*")
_FILENAME_BAD_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _strip_merek_prefix(tipe_hp: str, merek: str) -> str:
    tokens = tipe_hp.split()
    if tokens and tokens[0].upper() == merek.upper():
        return " ".join(tokens[1:])
    return tipe_hp


def _detect_series(tipe_hp: str, merek: str) -> str:
    sisa = _strip_merek_prefix(tipe_hp, merek)
    tokens = sisa.split()
    series_tokens: list[str] = []
    for t in tokens:
        if _SERIES_DIGIT_RE.search(t):
            break
        series_tokens.append(t)
    if series_tokens:
        return " ".join(series_tokens)
    if tokens:
        m = _LEADING_ALPHA_RE.match(tokens[0])
        if m:
            return m.group(1)
    return "LAINNYA"


def _natural_sort_key(tipe_hp: str, merek: str) -> tuple[int, str]:
    """Urutan berdasar angka model pertama (S21 < S22 < S24), fallback abjad."""
    sisa = _strip_merek_prefix(tipe_hp, merek)
    m = _MODEL_NUM_RE.search(sisa)
    num = int(m.group()) if m else 0
    return (num, sisa)


def _clean_sku_value(value: str) -> str:
    return value.replace(" ", "").replace("/", "-")


def _shorten_value(value: str, merek: str, limit: int = BOT_EXPORT_VALUE_MAX_LEN) -> str:
    """Value yang ditampilkan ke bot, maks `limit` karakter: buang prefix
    merek (udah kewakilan di judul file), rapatkan spasi di sekitar '/', baru
    potong keras kalau masih kelebihan."""
    v = _strip_merek_prefix(value, merek)
    v = _SLASH_SPACE_RE.sub("/", v)
    v = " ".join(v.split())
    if len(v) <= limit:
        return v
    return v[:limit].rstrip()


def _sanitize_filename(name: str) -> str:
    name = _FILENAME_BAD_CHARS_RE.sub("", name)
    return " ".join(name.split())[:150]


def _build_title(merek: str, kode_merek_tg: str, jenis: str, merek_tg: str, crows: list) -> str:
    sample = [_strip_merek_prefix(r["tipe_hp"], merek) for r in crows[:5]]
    title = f"Tempered Glass {merek} {' '.join(sample)}"
    if len(crows) > 5:
        title += " dll"
    title += f" {merek_tg} {kode_merek_tg} {jenis}"
    return _sanitize_filename(title)


def _chunk_variant_rows(rows: list) -> list[list]:
    """rows: baris utk SATU (kode_merek_tg, merek), diurutkan natural. Return
    list of row-lists yang masing-masing <= BOT_EXPORT_VARIANT_LIMIT."""
    rows = sorted(rows, key=lambda r: _natural_sort_key(r["tipe_hp"], r["merek"]))
    if len(rows) <= BOT_EXPORT_VARIANT_LIMIT:
        return [rows]
    by_series: dict[str, list] = {}
    order: list[str] = []
    for r in rows:
        s = _detect_series(r["tipe_hp"], r["merek"])
        if s not in by_series:
            by_series[s] = []
            order.append(s)
        by_series[s].append(r)
    chunks: list[list] = []
    for s in order:
        srows = by_series[s]  # tetap urut natural (dibangun dari rows yg udah disortir)
        if len(srows) <= BOT_EXPORT_VARIANT_LIMIT:
            chunks.append(srows)
        else:
            for i in range(0, len(srows), BOT_EXPORT_VARIANT_LIMIT):
                chunks.append(srows[i:i + BOT_EXPORT_VARIANT_LIMIT])
    return chunks


@app.get("/api/hp/export-bot")
def export_bot_json():
    kode_merek_tg = request.args.get("kode_merek_tg", "").strip()
    if not kode_merek_tg:
        return jsonify({"error": "kode_merek_tg wajib diisi"}), 400
    try:
        price = int(request.args.get("price", "0"))
    except ValueError:
        return jsonify({"error": "price harus angka"}), 400
    if price <= 0:
        return jsonify({"error": "price wajib diisi dan > 0"}), 400
    try:
        stock = int(request.args.get("stock", "1000"))
    except ValueError:
        return jsonify({"error": "stock harus angka"}), 400

    db = get_db()
    rows = db.execute(
        "SELECT kode, tipe_hp, merek, jenis_tg, merek_tg, kode_merek_tg FROM hp "
        "WHERE kode_merek_tg = ? ORDER BY merek, kode",
        (kode_merek_tg,),
    ).fetchall()
    if not rows:
        return jsonify({"error": f"Tidak ada data untuk kode_merek_tg={kode_merek_tg}"}), 404

    by_merek: dict[str, list] = {}
    order: list[str] = []
    for r in rows:
        m = r["merek"]
        if m not in by_merek:
            by_merek[m] = []
            order.append(m)
        by_merek[m].append(r)

    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for merek in order:
            for crows in _chunk_variant_rows(by_merek[merek]):
                jenis = crows[0]["jenis_tg"]
                merek_tg = crows[0]["merek_tg"]
                title = _build_title(merek, kode_merek_tg, jenis, merek_tg, crows)

                fname = f"{title}.json"
                dupe_i = 2
                while fname in used_names:
                    fname = f"{title} ({dupe_i}).json"
                    dupe_i += 1
                used_names.add(fname)

                variants = []
                for r in crows:
                    full_value = " ".join(r["tipe_hp"].split())
                    value = _shorten_value(full_value, merek)
                    sku = f"{r['kode']}-{kode_merek_tg}-{merek}-{_clean_sku_value(full_value)}"
                    variants.append({"value": value, "sku": sku, "price": price, "stock": stock})

                zf.writestr(fname, json.dumps(variants, ensure_ascii=False, indent=2))
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"bot_export_{kode_merek_tg}_{ts}.zip",
    )


@app.get("/api/hp/template")
def download_template():
    """Unduh template CSV kosong dengan contoh pengisian."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF", "MEREK TG", "KODE MEREK TG",
                "TAHUN LAUNCHING", "BULAN LAUNCHING"])
    # contoh baris — KODE tanpa strip, ALTERNATIF diisi kode dipisah titik-koma (;)
    # MEREK TG diisi "No Brand" kalau tempered glass generik/gak bermerek
    # KODE MEREK TG diisi kode SKU internal merek TG-nya (mis. "KA-A02"), kosongin kalau gak ada
    # TAHUN/BULAN LAUNCHING kosongin kalau belum tau -> ditampilkan "Coming Soon" di UI
    w.writerow(["HP001", "Galaxy S24 Ultra", "Samsung", "Privacy",
                "HP001A;HP001B", "No Brand", "", "2024", "Januari"])
    w.writerow(["HP002", "iPhone 15 Pro Max", "Apple", "Anti Gores", "HP002X", "Spigen", "", "2023", "September"])
    w.writerow(["HP003", "Redmi Note 13 Pro", "Xiaomi", "Anti Blue Light", "", "No Brand", "", "", ""])
    w.writerow(["HP004", "Pixel 8 Pro", "Google", "Full Cover", "", "No Brand", "", "", ""])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="template_import_tg.csv",
    )

EXPORT_PASSWORD = "bangkevin523"

@app.get("/api/hp/export")
def export_csv():
    pwd = request.args.get("pwd")
    if pwd != EXPORT_PASSWORD:
        return "Unauthorized: Password salah", 401
    db = get_db()
    rows = db.execute(
        "SELECT kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg, "
        "tahun_launching, bulan_launching FROM hp ORDER BY kode"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF", "MEREK TG", "KODE MEREK TG",
                "TAHUN LAUNCHING", "BULAN LAUNCHING"])
    for r in rows:
        alt_codes = _parse_alternatif(r["alternatif"])
        w.writerow([r["kode"], r["tipe_hp"], r["merek"], r["jenis_tg"], ";".join(alt_codes),
                    r["merek_tg"], r["kode_merek_tg"], r["tahun_launching"], r["bulan_launching"]])
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"data_tg_{ts}.csv",
    )


IMPORT_PASSWORD = "bangkevin523"

@app.post("/api/hp/import")
def import_csv():
    pwd = request.form.get("pwd")
    if pwd != IMPORT_PASSWORD:
        return jsonify({"error": "Password salah! Akses ditolak."}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "File tidak ditemukan"}), 400

    try:
        content = file.stream.read().decode("utf-8-sig")
        stream = io.StringIO(content)
        
        try:
            dialect = csv.Sniffer().sniff(content[:2048])
            reader = csv.reader(stream, dialect)
        except csv.Error:
            stream.seek(0)
            reader = csv.reader(stream, delimiter=';' if ';' in content else ',')

        headers = [h.strip().upper() for h in next(reader)]

        required = {"KODE", "TIPE HP", "MEREK"}
        if not required.issubset(set(headers)):
            return jsonify({
                "error": f"Kolom wajib: KODE, TIPE HP, MEREK. Ditemukan: {', '.join(headers)}"
            }), 400

        idx = {h: headers.index(h) for h in headers}

        rows_to_insert = []
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            try:
                kode  = _normalize_kode(row[idx["KODE"]])
                tipe  = row[idx["TIPE HP"]].strip()
                merek = row[idx["MEREK"]].strip()
                jenis = (row[idx["JENIS TG"]].strip()
                         if "JENIS TG" in idx and idx["JENIS TG"] < len(row) else "")
                alt_raw = (row[idx["ALTERNATIF"]].strip()
                           if "ALTERNATIF" in idx and idx["ALTERNATIF"] < len(row) else "")
                alt = _parse_alternatif(alt_raw)
                merek_tg = (row[idx["MEREK TG"]].strip()
                            if "MEREK TG" in idx and idx["MEREK TG"] < len(row) else "")
                kode_merek_tg = (row[idx["KODE MEREK TG"]].strip()
                                 if "KODE MEREK TG" in idx and idx["KODE MEREK TG"] < len(row) else "")
                tahun_launching = (row[idx["TAHUN LAUNCHING"]].strip()
                                   if "TAHUN LAUNCHING" in idx and idx["TAHUN LAUNCHING"] < len(row) else "")
                bulan_launching = (row[idx["BULAN LAUNCHING"]].strip()
                                   if "BULAN LAUNCHING" in idx and idx["BULAN LAUNCHING"] < len(row) else "")
                if kode and tipe and merek:
                    rows_to_insert.append(
                        (kode, tipe, merek, jenis, json.dumps(alt, ensure_ascii=False), merek_tg,
                         kode_merek_tg, tahun_launching, bulan_launching)
                    )
            except IndexError:
                continue

        if not rows_to_insert:
            return jsonify({"error": "Tidak ada baris data valid di file CSV"}), 400

        db = get_db()

        # backup data lama ke disk
        backup_created = False
        existing = db.execute(
            "SELECT kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg, "
            "tahun_launching, bulan_launching FROM hp ORDER BY kode"
        ).fetchall()
        if existing:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.csv")
            with open(backup_path, "w", newline="", encoding="utf-8-sig") as bf:
                bw = csv.writer(bf)
                bw.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF", "MEREK TG", "KODE MEREK TG",
                             "TAHUN LAUNCHING", "BULAN LAUNCHING"])
                for r in existing:
                    bw.writerow([r["kode"], r["tipe_hp"], r["merek"], r["jenis_tg"], r["alternatif"],
                                 r["merek_tg"], r["kode_merek_tg"], r["tahun_launching"], r["bulan_launching"]])
            backup_created = True

        # hapus semua, lalu insert baru
        db.execute("DELETE FROM hp")
        db.executemany(
            "INSERT INTO hp (kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg, "
            "tahun_launching, bulan_launching) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows_to_insert,
        )
        db.commit()

        return jsonify({"imported": len(rows_to_insert), "backup": backup_created})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── smartwatch (database terpisah, ukuran tempered glass smartwatch) ───────────

@app.get("/smartwatch")
def smartwatch_page():
    return render_template("smartwatch.html")


@app.get("/api/smartwatch")
def list_smartwatch():
    q = request.args.get("q", "").strip()
    merek = request.args.get("merek", "").strip()
    jenis_tg = request.args.get("jenis_tg", "").strip()
    merek_tg = request.args.get("merek_tg", "").strip()
    ukuran = request.args.get("ukuran", "").strip()

    where = []
    params: list[str] = []
    if q:
        like = f"%{q}%"
        where.append(
            "(kode LIKE ? OR tipe_smartwatch LIKE ? OR merek LIKE ? OR ukuran LIKE ? "
            "OR jenis_tg LIKE ? OR alternatif LIKE ? OR merek_tg LIKE ? OR kode_merek_tg LIKE ?)"
        )
        params += [like] * 8
    if merek:
        where.append("merek = ?")
        params.append(merek)
    if jenis_tg:
        where.append("jenis_tg = ?")
        params.append(jenis_tg)
    if merek_tg:
        where.append("merek_tg = ?")
        params.append(merek_tg)
    if ukuran:
        where.append("ukuran = ?")
        params.append(ukuran)

    sql = ("SELECT id, kode, tipe_smartwatch, merek, ukuran, jenis_tg, alternatif, merek_tg, "
           "kode_merek_tg FROM smartwatch")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY kode"

    db = get_smartwatch_db()
    rows = db.execute(sql, params).fetchall()
    total = db.execute("SELECT COUNT(*) FROM smartwatch").fetchone()[0]
    return jsonify({"data": [_smartwatch_row_to_dict(r) for r in rows], "total": total})


@app.get("/api/smartwatch/filters")
def smartwatch_filter_options():
    db = get_smartwatch_db()

    def distinct(col: str) -> list[str]:
        rows = db.execute(
            f"SELECT DISTINCT {col} FROM smartwatch WHERE TRIM({col}) != '' ORDER BY {col} COLLATE NOCASE"
        ).fetchall()
        return [r[0] for r in rows]

    return jsonify({
        "merek": distinct("merek"),
        "ukuran": distinct("ukuran"),
        "jenis_tg": distinct("jenis_tg"),
        "merek_tg": distinct("merek_tg"),
        "kode_merek_tg": distinct("kode_merek_tg"),
    })


@app.get("/api/smartwatch/template")
def download_smartwatch_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE SMARTWATCH", "MEREK", "UKURAN", "JENIS TG", "ALTERNATIF",
                "MEREK TG", "KODE MEREK TG"])
    # UKURAN diisi ukuran layar smartwatch (mis. "44mm", "1.43 inch") — dipakai
    # buat nyocokin ukuran TG, bukan match nama model kayak tipe_hp
    w.writerow(["SW001", "Galaxy Watch 6", "Samsung", "44mm", "Clear", "SW001A", "No Brand", ""])
    w.writerow(["SW002", "Apple Watch Series 9", "Apple", "45mm", "Privacy", "", "No Brand", ""])
    w.writerow(["SW003", "Mi Band 8", "Xiaomi", "1.62 inch", "Anti Gores", "", "No Brand", ""])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="template_import_tg_smartwatch.csv",
    )


@app.get("/api/smartwatch/export")
def export_smartwatch_csv():
    pwd = request.args.get("pwd")
    if pwd != EXPORT_PASSWORD:
        return "Unauthorized: Password salah", 401
    db = get_smartwatch_db()
    rows = db.execute(
        "SELECT kode, tipe_smartwatch, merek, ukuran, jenis_tg, alternatif, merek_tg, kode_merek_tg "
        "FROM smartwatch ORDER BY kode"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE SMARTWATCH", "MEREK", "UKURAN", "JENIS TG", "ALTERNATIF",
                "MEREK TG", "KODE MEREK TG"])
    for r in rows:
        alt_codes = _parse_alternatif(r["alternatif"])
        w.writerow([r["kode"], r["tipe_smartwatch"], r["merek"], r["ukuran"], r["jenis_tg"],
                    ";".join(alt_codes), r["merek_tg"], r["kode_merek_tg"]])
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"data_tg_smartwatch_{ts}.csv",
    )


@app.post("/api/smartwatch/import")
def import_smartwatch_csv():
    pwd = request.form.get("pwd")
    if pwd != IMPORT_PASSWORD:
        return jsonify({"error": "Password salah! Akses ditolak."}), 401

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "File tidak ditemukan"}), 400

    try:
        content = file.stream.read().decode("utf-8-sig")
        stream = io.StringIO(content)

        try:
            dialect = csv.Sniffer().sniff(content[:2048])
            reader = csv.reader(stream, dialect)
        except csv.Error:
            stream.seek(0)
            reader = csv.reader(stream, delimiter=';' if ';' in content else ',')

        headers = [h.strip().upper() for h in next(reader)]

        required = {"KODE", "TIPE SMARTWATCH", "MEREK"}
        if not required.issubset(set(headers)):
            return jsonify({
                "error": f"Kolom wajib: KODE, TIPE SMARTWATCH, MEREK. Ditemukan: {', '.join(headers)}"
            }), 400

        idx = {h: headers.index(h) for h in headers}

        rows_to_insert = []
        for row in reader:
            if not any(c.strip() for c in row):
                continue
            try:
                kode  = _normalize_kode(row[idx["KODE"]])
                tipe  = row[idx["TIPE SMARTWATCH"]].strip()
                merek = row[idx["MEREK"]].strip()
                ukuran = (row[idx["UKURAN"]].strip()
                          if "UKURAN" in idx and idx["UKURAN"] < len(row) else "")
                jenis = (row[idx["JENIS TG"]].strip()
                         if "JENIS TG" in idx and idx["JENIS TG"] < len(row) else "")
                alt_raw = (row[idx["ALTERNATIF"]].strip()
                           if "ALTERNATIF" in idx and idx["ALTERNATIF"] < len(row) else "")
                alt = _parse_alternatif(alt_raw)
                merek_tg = (row[idx["MEREK TG"]].strip()
                            if "MEREK TG" in idx and idx["MEREK TG"] < len(row) else "")
                kode_merek_tg = (row[idx["KODE MEREK TG"]].strip()
                                 if "KODE MEREK TG" in idx and idx["KODE MEREK TG"] < len(row) else "")
                if kode and tipe and merek:
                    rows_to_insert.append(
                        (kode, tipe, merek, ukuran, jenis, json.dumps(alt, ensure_ascii=False),
                         merek_tg, kode_merek_tg)
                    )
            except IndexError:
                continue

        if not rows_to_insert:
            return jsonify({"error": "Tidak ada baris data valid di file CSV"}), 400

        db = get_smartwatch_db()

        # backup data lama ke disk (pola sama kayak import HP)
        backup_created = False
        existing = db.execute(
            "SELECT kode, tipe_smartwatch, merek, ukuran, jenis_tg, alternatif, merek_tg, kode_merek_tg "
            "FROM smartwatch ORDER BY kode"
        ).fetchall()
        if existing:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_smartwatch_{ts}.csv")
            with open(backup_path, "w", newline="", encoding="utf-8-sig") as bf:
                bw = csv.writer(bf)
                bw.writerow(["KODE", "TIPE SMARTWATCH", "MEREK", "UKURAN", "JENIS TG", "ALTERNATIF",
                             "MEREK TG", "KODE MEREK TG"])
                for r in existing:
                    bw.writerow([r["kode"], r["tipe_smartwatch"], r["merek"], r["ukuran"], r["jenis_tg"],
                                 r["alternatif"], r["merek_tg"], r["kode_merek_tg"]])
            backup_created = True

        db.execute("DELETE FROM smartwatch")
        db.executemany(
            "INSERT INTO smartwatch (kode, tipe_smartwatch, merek, ukuran, jenis_tg, alternatif, "
            "merek_tg, kode_merek_tg) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows_to_insert,
        )
        db.commit()

        return jsonify({"imported": len(rows_to_insert), "backup": backup_created})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
