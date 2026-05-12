import csv
import io
import json
import os
import sqlite3
from datetime import datetime

from flask import Flask, g, jsonify, render_template, request, send_file

app = Flask(__name__)
DB_PATH = "hp_data.db"
BACKUP_DIR = "backups"


# ── database ──────────────────────────────────────────────────────────────────

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
                alternatif TEXT NOT NULL DEFAULT '[]'
            )
        """)
        # migrate: add new columns if missing (for existing DBs)
        existing_cols = {r[1] for r in g.db.execute("PRAGMA table_info(hp)").fetchall()}
        if "jenis_tg" not in existing_cols:
            g.db.execute("ALTER TABLE hp ADD COLUMN jenis_tg TEXT NOT NULL DEFAULT ''")
        if "alternatif" not in existing_cols:
            g.db.execute("ALTER TABLE hp ADD COLUMN alternatif TEXT NOT NULL DEFAULT '[]'")
        g.db.commit()
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


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
    }


def _parse_alternatif(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            return [str(a).strip() for a in parsed if str(a).strip()]
        except Exception:
            pass
    # fallback: semicolon or comma separated
    sep = ";" if ";" in raw else ","
    return [a.strip() for a in raw.split(sep) if a.strip()]


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/hp")
def list_hp():
    q = request.args.get("q", "").strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT id, kode, tipe_hp, merek, jenis_tg, alternatif FROM hp "
            "WHERE kode LIKE ? OR tipe_hp LIKE ? OR merek LIKE ? "
            "   OR jenis_tg LIKE ? OR alternatif LIKE ? "
            "ORDER BY kode",
            (like, like, like, like, like),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, kode, tipe_hp, merek, jenis_tg, alternatif FROM hp ORDER BY kode"
        ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM hp").fetchone()[0]
    return jsonify({"data": [_row_to_dict(r) for r in rows], "total": total})


@app.get("/api/hp/template")
def download_template():
    """Unduh template CSV kosong dengan contoh pengisian."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF"])
    # contoh baris
    w.writerow(["HP-001", "Galaxy S24 Ultra", "Samsung", "Privacy",
                '["HP-001A","HP-001B"]'])
    w.writerow(["HP-002", "iPhone 15 Pro Max", "Apple", "Anti Gores", '["HP-002X"]'])
    w.writerow(["HP-003", "Redmi Note 13 Pro", "Xiaomi", "Anti Blue Light", ""])
    w.writerow(["HP-004", "Pixel 8 Pro", "Google", "Full Cover", ""])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="template_import_tg.csv",
    )


@app.get("/api/hp/export")
def export_csv():
    db = get_db()
    rows = db.execute(
        "SELECT kode, tipe_hp, merek, jenis_tg, alternatif FROM hp ORDER BY kode"
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF"])
    for r in rows:
        w.writerow([r["kode"], r["tipe_hp"], r["merek"], r["jenis_tg"], r["alternatif"]])
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"data_tg_{ts}.csv",
    )


@app.post("/api/hp/import")
def import_csv():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "File tidak ditemukan"}), 400

    try:
        content = file.stream.read().decode("utf-8-sig")
        stream = io.StringIO(content)
        reader = csv.reader(stream)
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
                kode  = row[idx["KODE"]].strip()
                tipe  = row[idx["TIPE HP"]].strip()
                merek = row[idx["MEREK"]].strip()
                jenis = (row[idx["JENIS TG"]].strip()
                         if "JENIS TG" in idx and idx["JENIS TG"] < len(row) else "")
                alt_raw = (row[idx["ALTERNATIF"]].strip()
                           if "ALTERNATIF" in idx and idx["ALTERNATIF"] < len(row) else "")
                alt = _parse_alternatif(alt_raw)
                if kode and tipe and merek:
                    rows_to_insert.append(
                        (kode, tipe, merek, jenis, json.dumps(alt, ensure_ascii=False))
                    )
            except IndexError:
                continue

        if not rows_to_insert:
            return jsonify({"error": "Tidak ada baris data valid di file CSV"}), 400

        db = get_db()

        # backup data lama ke disk
        backup_created = False
        existing = db.execute(
            "SELECT kode, tipe_hp, merek, jenis_tg, alternatif FROM hp ORDER BY kode"
        ).fetchall()
        if existing:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.csv")
            with open(backup_path, "w", newline="", encoding="utf-8-sig") as bf:
                bw = csv.writer(bf)
                bw.writerow(["KODE", "TIPE HP", "MEREK", "JENIS TG", "ALTERNATIF"])
                for r in existing:
                    bw.writerow([r["kode"], r["tipe_hp"], r["merek"], r["jenis_tg"], r["alternatif"]])
            backup_created = True

        # hapus semua, lalu insert baru
        db.execute("DELETE FROM hp")
        db.executemany(
            "INSERT INTO hp (kode, tipe_hp, merek, jenis_tg, alternatif) VALUES (?, ?, ?, ?, ?)",
            rows_to_insert,
        )
        db.commit()

        return jsonify({"imported": len(rows_to_insert), "backup": backup_created})

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8080)
