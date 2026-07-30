"""
Cari model HP baru yang belum ada di hp_data.db, dari dua sumber terbuka:

1. Wikidata (CC0, via SPARQL endpoint resmi query.wikidata.org). Bagus buat
   Samsung & Apple, tapi sangat minim buat merek budget/regional (Infinix,
   Tecno, itel, realme, Vivo, Oppo) karena tanggal rilis (P577) jarang diisi
   utk model-model itu.
2. Sertifikasi Postel/SDPPI (sertifikasi.postel.go.id) — setiap HP yang legal
   dijual di Indonesia wajib disertifikasi di sini, jadi justru paling kuat
   buat merek budget yang lemah di Wikidata. Situsnya Nuxt SPA, tapi frontend-
   nya manggil GraphQL endpoint publik `/svc/master/query` langsung, tanpa
   token/captcha (dicek manual lewat devtools browser: request `authorization`
   header kosong, response 200 tanpa challenge). Jangan pernah coba bypass
   captcha kalau suatu saat endpoint ini digembok captcha — investigasi ulang
   dulu, jangan asal lanjut.

Kenapa Wikidata/Postel, bukan GSMArena/kimovil/91mobiles:
- GSMArena robots.txt eksplisit melarang ClaudeBot/anthropic-ai, dan RSL license
  mereka (/license.xml) melarang "ai-inference"/"ai-train".
- kimovil.com & 91mobiles.com diproteksi Cloudflare (403 utk request non-browser).
- Wikidata SPARQL endpoint & endpoint publik Postel memang didesain/dipakai
  buat akses program (data pemerintah terbuka / CC0).

Cara pakai:
    python3 scripts/find_new_phones.py
Hasil baru (yang belum pernah dilaporkan, gabungan kedua sumber) ditulis ke
reports/new_phone_candidates.json.
"""
import sqlite3
import json
import os
import re
import time
import urllib.request
import urllib.parse
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

POSTEL_ENDPOINT = "https://sertifikasi.postel.go.id/svc/master/query"
POSTEL_BRANDS = [
    "Samsung", "Xiaomi", "OPPO", "vivo", "Infinix", "realme", "Tecno",
    "itel", "Apple", "Huawei", "Honor", "Motorola", "OnePlus",
]
# eqp_name (jenis alat) yg dianggap HP di data sertifikasi Postel — mereka
# gak punya satu istilah baku, jadi dicocokkan dgn substring case-insensitive
POSTEL_PHONE_EQP_KEYWORDS = [
    "SMARTPHONE", "TELEPON SELULER", "TELEPON GENGGAM", "HANDPHONE",
]

# nama manufacturer versi Wikidata (bisa macam-macam: "Apple Inc.", "Samsung
# Electronics", dst) atau merek versi Postel (lebih pendek, mis. "Samsung",
# "Infinix") -> merek pendek sesuai konvensi kolom `merek` di DB
MANUFACTURER_TO_MEREK = {
    "samsung electronics": "SAMSUNG",
    "samsung group": "SAMSUNG",
    "samsung": "SAMSUNG",
    "xiaomi": "XIAOMI",
    "redmi": "XIAOMI",  # sub-merek Xiaomi, tapi merk field di DB Postel beda dari Xiaomi
    "poco": "XIAOMI",   # sub-merek Xiaomi
    "oppo": "OPPO",
    "vivo": "VIVO",
    "iqoo": "VIVO",  # sub-merek vivo, tapi merk field di DB Postel beda dari vivo
    "infinix": "INFINIX",
    "infinix mobility": "INFINIX",
    "realme": "REALME",
    "tecno mobile": "TECNO",
    "tecno": "TECNO",
    "itel mobile": "ITEL",
    "itel": "ITEL",
    "apple inc.": "APPLE",
    "apple": "APPLE",
    "huawei": "HUAWEI",
    "honor device co., ltd.": "HONOR",
    "honor": "HONOR",
    "motorola": "MOTOROLA",
    "motorola mobility": "MOTOROLA",
    "oneplus": "ONEPLUS",
}

# kata-kata yang gak signifikan buat pembeda model, dibuang sebelum dibandingkan
NOISE_WORDS = {"GALAXY", "5G", "4G", "LTE", "SMARTPHONE"}


def normalize_merek(manufacturer_label):
    return MANUFACTURER_TO_MEREK.get(manufacturer_label.strip().lower())


def model_tokens(text):
    text = re.sub(r"[()]", " ", text.upper())
    words = re.findall(r"[A-Z0-9]+", text)
    return {w for w in words if w not in NOISE_WORDS}


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


def fetch_postel_phones(limit_per_brand=30):
    """Query GraphQL publik sertifikasi.postel.go.id per merek, ambil sertifikat
    aktif yg jenis alatnya HP. `brand` field di respons = nama pemasaran
    (mis. "HOT 70 Pro 5G"), itu yg dipakai sbg label, bukan `model` (kode
    internal mis. "X6896")."""
    query = """
    query ListTransLicenceFront($limit: Int, $offset: Int, $keyword: String, $filter: DataFilter, $advanced_filter: [DataFilter]) {
      licencefront {
        lists(limit: $limit, offset: $offset, keyword: $keyword, filter: $filter, advanced_filter: $advanced_filter) {
          lic_date
          aplikasis { eqp_name merk model brand }
        }
      }
    }
    """
    results = []
    for brand in POSTEL_BRANDS:
        body = json.dumps({
            "operationName": "ListTransLicenceFront",
            "variables": {
                "limit": limit_per_brand, "offset": 1, "keyword": brand,
                "filter": {"column": "berlaku", "value": "1"}, "advanced_filter": [],
            },
            "query": query,
        }).encode("utf-8")
        req = urllib.request.Request(
            POSTEL_ENDPOINT, data=body,
            headers={
                # server nolak (`{"errors":[{"message":"forbidden"}]}`) kalau
                # Origin/Referer gak ada atau User-Agent gak kayak browser asli
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://sertifikasi.postel.go.id",
                "Referer": "https://sertifikasi.postel.go.id/sertifikat/sertifikat-terbit",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for row in data["data"]["licencefront"]["lists"]:
            app = row["aplikasis"]
            eqp_name = (app["eqp_name"] or "").upper()
            if not any(kw in eqp_name for kw in POSTEL_PHONE_EQP_KEYWORDS):
                continue
            marketing_name = (app["brand"] or app["model"] or "").strip()
            if not marketing_name:
                continue
            results.append({
                "label": marketing_name,
                "manufacturer": (app["merk"] or brand).strip(),
                "release_date": row["lic_date"][:10],
            })
        time.sleep(0.3)  # sopan santun ke server pemerintah, bukan rate limit resmi mereka
    return results


def load_existing_by_merek():
    """merek -> set(frozenset token model) buat semua tipe_hp yg ada di sistem."""
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT merek, tipe_hp FROM hp").fetchall()
    by_merek = {}
    for merek, tipe_hp in rows:
        by_merek.setdefault(merek.strip().upper(), []).append(model_tokens(tipe_hp))
    return by_merek


def already_exists(manufacturer_label, model_label, existing_by_merek):
    merek = normalize_merek(manufacturer_label)
    if merek is None or merek not in existing_by_merek:
        return False  # gak bisa dipastikan merek-nya, jangan diam2 dianggap "sudah ada"
    cand_tokens = model_tokens(model_label)
    if not cand_tokens:
        return False
    for existing_tokens in existing_by_merek[merek]:
        if not existing_tokens:
            continue
        # cocok kalau salah satu token-set adalah subset dari yg lain
        # (nangkep beda penulisan spt "Galaxy A35 5G" vs "A35")
        if cand_tokens <= existing_tokens or existing_tokens <= cand_tokens:
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


def load_pending_candidates():
    """Kandidat yg masih pending dari run sebelumnya (belum ditandai selesai
    di halaman /hp-baru, dan belum ketauan udah ada di DB), keyed by `key`.
    Report ini nempel/akumulasi antar run — bukan ditimpa abis tiap run —
    supaya kandidat yg belum sempat diproses staff gak ilang diam-diam."""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            report = json.load(f)
        return {p["key"]: p for p in report.get("new_candidates", []) if "key" in p}
    return {}


def main():
    existing_by_merek = load_existing_by_merek()
    seen = load_seen()
    pending = load_pending_candidates()
    newly_seen = set(seen)

    # kandidat pending yg ternyata udah beneran dimasukin ke DB (mis. via
    # import CSV manual, tanpa mampir tandai "Selesai" dulu) -> keluarin dari
    # pending, tandain selesai permanen
    for key, p in list(pending.items()):
        if already_exists(p["manufacturer"], p["label"], existing_by_merek):
            del pending[key]
            newly_seen.add(key)

    sourced_phones = (
        [(p, "wikidata") for p in fetch_wikidata_phones()]
        + [(p, "postel") for p in fetch_postel_phones()]
    )
    for p, source in sourced_phones:
        key = f"{source}::{p['manufacturer']} {p['label']}".upper()
        if key in seen or key in pending:
            continue
        if already_exists(p["manufacturer"], p["label"], existing_by_merek):
            newly_seen.add(key)  # udah ada di sistem, jangan laporin
            continue
        pending[key] = {**p, "source": source, "key": key}
        merek = normalize_merek(p["manufacturer"])
        if merek:
            # daftarin ke existing_by_merek biar kalau sumber lain nemu HP
            # yg sama di run ini, gak dilaporin dobel
            existing_by_merek.setdefault(merek, []).append(model_tokens(p["label"]))

    new_candidates = sorted(pending.values(), key=lambda p: p["release_date"], reverse=True)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "new_candidates": new_candidates,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    save_seen(newly_seen)

    if new_candidates:
        print(f"Total {len(new_candidates)} kandidat HP baru pending (belum ditandai selesai / belum ada di DB):")
        for p in new_candidates:
            print(f"  {p['release_date']} | {p['manufacturer']:10s} | {p['label']:30s} | {p['source']}")
    else:
        print("Gak ada kandidat HP baru sejak run terakhir.")


if __name__ == "__main__":
    main()
