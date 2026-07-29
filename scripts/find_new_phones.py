"""
Cari model HP baru yang belum ada di hp_data.db, dari Wikidata (data terbuka/CC0,
via SPARQL endpoint resmi query.wikidata.org).

Kenapa Wikidata, bukan GSMArena/kimovil/91mobiles:
- GSMArena robots.txt eksplisit melarang ClaudeBot/anthropic-ai, dan RSL license
  mereka (/license.xml) melarang "ai-inference"/"ai-train".
- kimovil.com & 91mobiles.com diproteksi Cloudflare (403 utk request non-browser).
- Wikidata SPARQL endpoint memang didesain buat akses program, datanya CC0.

Keterbatasan: cakupan Wikidata bagus buat Samsung & Apple, tapi sangat minim
buat merek budget/regional (Infinix, Tecno, itel, realme, Vivo, Oppo) karena
tanggal rilis (P577) jarang diisi utk model-model itu. Jangan andalkan ini
sebagai satu-satunya sumber — tetap cek manual buat merek-merek tsb.

Cara pakai:
    python3 scripts/find_new_phones.py
Hasil baru (yang belum pernah dilaporkan) ditulis ke reports/new_phone_candidates.json
"""
import sqlite3
import json
import os
import urllib.request
import urllib.parse
import difflib
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE_DIR, "hp_data.db")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
SEEN_FILE = os.path.join(REPORTS_DIR, "new_phone_seen.json")
OUTPUT_FILE = os.path.join(REPORTS_DIR, "new_phone_candidates.json")

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "tempered-glass-persamaan-newphone-finder/1.0 (contact: kevinsorensen523@gmail.com)"

BRAND_QIDS = {
    "Samsung": "Q20718",
    "Xiaomi": "Q1636958",
    "OPPO": "Q2084900",
    "vivo": "Q18301787",
    "Infinix": "Q30594096",
    "realme": "Q56275466",
    "Tecno": "Q22909292",
    "itel": "Q56375178",
    "Apple": "Q312",
    "Huawei": "Q160120",
    "Honor": "Q27070331",
    "Motorola": "Q259011",
    "OnePlus": "Q16499972",
}


def fetch_wikidata_phones(since_date="2024-01-01"):
    values_clause = " ".join(f"wd:{qid}" for qid in BRAND_QIDS.values())
    query = f"""
    SELECT ?itemLabel ?manufacturerLabel ?pubdate WHERE {{
      VALUES ?manufacturer {{ {values_clause} }}
      ?item wdt:P31 wd:Q19723451.
      ?item wdt:P176 ?manufacturer.
      ?item wdt:P577 ?pubdate.
      FILTER(?pubdate > "{since_date}"^^xsd:dateTime)
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    ORDER BY DESC(?pubdate)
    """
    url = SPARQL_ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = []
    for b in data["results"]["bindings"]:
        label = b.get("itemLabel", {}).get("value", "")
        manu = b.get("manufacturerLabel", {}).get("value", "")
        pub = b.get("pubdate", {}).get("value", "")[:10]
        if label.startswith("Q") and label[1:].isdigit():
            continue  # gak ada English label, skip
        results.append({"label": label, "manufacturer": manu, "release_date": pub})
    return results


def load_existing_tipe_hp():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT tipe_hp FROM hp").fetchall()
    return [r[0] for r in rows]


def is_similar(candidate, existing_list, threshold=0.55):
    cand_norm = candidate.upper()
    for existing in existing_list:
        ratio = difflib.SequenceMatcher(None, cand_norm, existing.upper()).ratio()
        if ratio >= threshold:
            return True
        if cand_norm in existing.upper() or existing.upper() in cand_norm:
            return True
    return False


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen_set):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen_set), f, ensure_ascii=False, indent=2)


def main():
    phones = fetch_wikidata_phones()
    existing = load_existing_tipe_hp()
    seen = load_seen()

    new_candidates = []
    newly_seen = set(seen)
    for p in phones:
        full_name = f"{p['manufacturer']} {p['label']}"
        key = full_name.upper()
        if key in seen:
            continue
        if is_similar(full_name, existing):
            newly_seen.add(key)  # udah ada di sistem, jangan laporin lagi ke depannya
            continue
        new_candidates.append(p)
        newly_seen.add(key)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_candidates": new_candidates,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    save_seen(newly_seen)

    if new_candidates:
        print(f"Ketemu {len(new_candidates)} kandidat HP baru (belum pernah dilaporkan):")
        for p in new_candidates:
            print(f"  {p['release_date']} | {p['manufacturer']:10s} | {p['label']}")
    else:
        print("Gak ada kandidat HP baru sejak run terakhir.")


if __name__ == "__main__":
    main()
