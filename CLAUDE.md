# Database Tempered Glass — Project Context

## Summary

Single-user internal web app for managing a lookup table of phone models (`hp` =
"handphone") to their tempered-glass type and equivalent/alternative glass codes.
Used by store staff to search "what glass fits phone X" and to bulk import/export
the catalog via CSV. Indonesian-language UI (`lang="id"`). Also hosts a second,
separate catalog for smartwatch tempered glass (`GET /smartwatch`, own DB file).

**Active features:**
- Search/list HP records (`GET /api/hp?q=`) — matches kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg
- CSV template download (`GET /api/hp/template`)
- Password-gated CSV export (`GET /api/hp/export?pwd=`)
- Password-gated CSV import — **full replace**: wipes table and re-inserts all rows,
  auto-backs up existing data to `backups/backup_<timestamp>.csv` first (`POST /api/hp/import`)
- Single-page frontend (`templates/index.html`, vanilla HTML/CSS/JS, no framework, no build step)
- `kode_merek_tg` column (internal TG-brand SKU code, e.g. `"KA-A02"`) — wired end-to-end:
  schema migration in `get_db()`, `_row_to_dict()`, search, template/export/import CSV (7th
  column `KODE MEREK TG`), and the table column in `templates/index.html`. Committed in
  `1ff910a` (2026-08-12). Bulk-populated with ~330 rows across KA-codes KA-A02, A06, A07,
  A08, A09, A11–A16, A19, A21, A22, plus TG Camera B05/B06 (session of 2026-08-10, see
  `session_summary.md` and `docs/progress.md`), transcribed from supplier receipt photos —
  the list in `docs/notes-ka-codes.md` has been consumed, not sitting unused.
- **`tahun_launching`/`bulan_launching` columns** (added 2026-08-27): year/month a phone
  launched, TEXT, default `''` — rendered as **"Coming Soon"** in the UI (`LAUNCH_UNKNOWN`
  in `app.py`) whenever empty, i.e. not yet matched to a source. `GET /api/hp` accepts
  `tahun_launching=` (exact) and `recent_only=1` (launched in the last 1 calendar year,
  `CAST(tahun_launching AS INTEGER) >= this_year - 1`) — used by the "🆕 1 Tahun Terakhir"
  filter button in `templates/index.html`. **Populated from Wikidata/Postel, NOT GSMArena** —
  GSMArena's robots.txt/RSL license forbid AI bots (see "Sources tried and rejected" below and
  `scripts/find_new_phones.py`'s docstring); the user explicitly chose Wikidata/Postel as the
  source when asked. `scripts/backfill_launch_dates.py` reuses `fetch_wikidata_phones()` /
  `fetch_postel_phones()` / `model_tokens()` / `normalize_merek()` from `find_new_phones.py`
  (do not duplicate that fetch/matching logic) to fill in `hp` rows still stuck at "Coming
  Soon", triggerable via the "Isi Tahun/Bulan Otomatis" button (`POST /api/hp/backfill-launch`,
  same background-thread + lock-file pattern as the HP Baru refresh). **Known limitation, not
  a bug:** only fills phones recent enough / from brands whitelisted in `BRAND_QIDS`/
  `POSTEL_BRANDS` — older or off-brand phones stay "Coming Soon" indefinitely until a broader
  source exists.
- **Smartwatch catalog** (added 2026-08-27): a **separate SQLite DB file** `smartwatch_data.db`
  (own `get_smartwatch_db()`/`g.swdb` connection, gitignored like `hp_data.db`) for tempered
  glass sized to smartwatch screens — deliberately not merged into the `hp` table, since
  smartwatches match by `ukuran` (screen size, e.g. `"44mm"`, `"1.62 inch"`), not by model-name
  token matching like phones. Table `smartwatch`: `kode, tipe_smartwatch, merek, ukuran,
  jenis_tg, alternatif, merek_tg, kode_merek_tg` — same `alternatif`/import-export/backup
  conventions as `hp` (reuses `_parse_alternatif`, `_normalize_kode`, `EXPORT_PASSWORD`/
  `IMPORT_PASSWORD`). UI at `GET /smartwatch` (`templates/smartwatch.html`, near-identical
  layout to `index.html` minus the bot-export/launch-date features — those weren't requested
  for this catalog). No launch-date columns here; add them later only if actually asked for.
- **HP Baru finder** (`GET /hp-baru` page, `GET /api/hp-baru`): weekly cron
  (`scripts/find_new_phones.py`, runs Mondays 08:00 on the production server via
  crontab) pulls from **two** open sources and merges results, brand-scoped token
  matching against existing `merek`+`tipe_hp` (see `already_exists()` in the
  script — do NOT go back to naive `difflib` ratio matching, it produces false
  positives/negatives, see git history commit "fix: use brand-scoped token
  matching"):
  1. Wikidata SPARQL endpoint (`fetch_wikidata_phones()`) — strong for Samsung/Apple,
     sparse for budget/regional brands (release date property rarely filled in).
  2. Sertifikasi Postel/SDPPI GraphQL endpoint (`fetch_postel_phones()`,
     `sertifikasi.postel.go.id/svc/master/query`) — added 2026-07-30 to cover the
     gap. Every phone legally sold in Indonesia must be certified here, so it's
     strong precisely for the budget/regional brands Wikidata misses (Infinix,
     Tecno, itel, realme, Vivo/iQOO, Xiaomi/Redmi/POCO). It's a public, unauthenticated
     endpoint (no login, no captcha — confirmed by inspecting real browser network
     traffic) but **does require `Origin`/`Referer` headers matching the site and a
     browser-like `User-Agent`**, or it returns `{"errors":[{"message":"forbidden"}]}`.
     Do not remove those headers. Sub-brands (Redmi/POCO under Xiaomi, iQOO under vivo)
     report under their own `merk` value in Postel's data, not the parent brand — see
     `MANUFACTURER_TO_MEREK` in the script for the mapping back to this DB's `merek`
     convention; extend that dict (not `already_exists()`) if more sub-brands show up.
  Results are merged, cross-source deduped (so the same phone found by both sources
  isn't reported twice), and land in `reports/new_phone_candidates.json` (gitignored,
  each candidate has a `"source"` field (`"wikidata"`/`"postel"`) and a stable `"key"`
  used for dedup/marking).
  **Report is a pending list that accumulates across runs, not overwritten wholesale**
  (`load_pending_candidates()` in the script) — a candidate stays listed until either
  (a) it's found to already match the DB (`already_exists()`, e.g. staff imported it
  via CSV without using the UI), or (b) staff clicks "Tandai Selesai" on `/hp-baru`,
  which calls `POST /api/hp-baru/mark` (`app.py`) to drop it from the report and add
  its key to `reports/new_phone_seen.json` permanently. Do not change this back to
  "show once then forget" — that was the original design and it silently dropped
  candidates staff hadn't gotten to yet before the next Monday's cron overwrote them.

## Tech Stack

- **Backend:** Flask (`app.py`, ~370 lines, all routes in one file), `sqlite3` stdlib driver (no ORM)
- **DB:** SQLite file `hp_data.db` (gitignored, lives next to `app.py`)
- **Frontend:** Three Jinja templates — `templates/index.html` (main HP catalog UI),
  `templates/hp_baru.html` (HP Baru report viewer), `templates/smartwatch.html` (smartwatch TG
  catalog UI, `smartwatch_data.db`) — inline `<style>` + `<script>`, zero external dependencies
  (no CDN, no npm)
- **Server:** gunicorn in production (`run.sh`), Flask dev server (`debug=True`) when run directly
- **Deploy:** `deploy.sh` provisions a Debian/Ubuntu venv; `tempered-glass.service` is the systemd unit (runs as `www-data`, working dir `/opt/tempered-glass`, port 8080)
- No test framework, no linter/formatter config, no CI present in this repo.

## Data Model

Table `hp` (auto-migrated on first request via `PRAGMA table_info` check in `get_db()`):

| column       | type    | notes                                   |
|--------------|---------|------------------------------------------|
| id           | INTEGER | PK autoincrement                        |
| kode         | TEXT    | unique-ish product code (not enforced)  |
| tipe_hp      | TEXT    | phone model name                        |
| merek        | TEXT    | brand                                   |
| jenis_tg     | TEXT    | tempered glass type, default `''`       |
| alternatif   | TEXT    | JSON array string of alternative kodes, default `'[]'` |
| merek_tg     | TEXT    | tempered glass brand (e.g. "Spigen", "No Brand"), default `''` — separate from `merek` (phone brand) |
| kode_merek_tg | TEXT   | internal TG-brand SKU code (e.g. "KA-A02"), default `''` |
| tahun_launching | TEXT | phone launch year, default `''` (shown as "Coming Soon"), see Active Features |
| bulan_launching | TEXT | phone launch month (Indonesian name, e.g. "Januari"), default `''` (shown as "Coming Soon") |

`alternatif` relationships are meant to be **symmetric**: if kode A lists kode B as
an alternative, B should list A back. Not enforced by the schema/app — was manually
fixed once (see git history), watch for drift if `alternatif` is edited outside the
normal import flow.

`alternatif` is stored as a JSON string and parsed with `_parse_alternatif()` on import
(accepts JSON array, or `;`/`,`-separated fallback) and `_row_to_dict()` on read.

`kode` has **no UNIQUE constraint** — a standing gotcha (see "kode-uniqueness" note below).

### Table `smartwatch` (separate DB file `smartwatch_data.db`, own connection `get_smartwatch_db()`)

| column          | type | notes |
|-----------------|------|-------|
| id              | INTEGER | PK autoincrement |
| kode            | TEXT | not enforced unique, same caveat as `hp.kode` |
| tipe_smartwatch | TEXT | smartwatch model name |
| merek           | TEXT | brand |
| ukuran          | TEXT | screen size (e.g. "44mm", "1.62 inch") — the primary match key for this catalog, unlike `hp` which matches by model name |
| jenis_tg        | TEXT | default `''` |
| alternatif      | TEXT | JSON array string, default `'[]'`, same helpers as `hp.alternatif` |
| merek_tg        | TEXT | default `''` |
| kode_merek_tg   | TEXT | default `''` |

### kode-uniqueness gotcha (standing rule, from the 2026-08-10 KA-code bulk-entry session)

`hp.kode` has no UNIQUE constraint, which caused two real data-integrity incidents during
bulk entry (see `session_summary.md` for full detail): (1) an accidental overwrite of 54
unrelated legacy rows (TG0339–TG0392, accepted as permanent loss) from a stale range query,
and (2) a cross-KA-code kode collision affecting 40 rows because "reuse freed kode block"
logic only checked one KA-code's own old range instead of the whole table. **Any future kode
reassignment must**: (a) compute new ranges from `max(kode)` over the **entire** `hp` table,
never a per-batch subrange, and (b) be followed by a full-table collision check
(`(kode) -> {kode_merek_tg}` should be 1:1 per intended batch) plus a gap check on the sorted
distinct kode sequence.

## Code Style & Conventions

- Everything lives in `app.py` — no blueprints/packages yet (fine at current size; see TODO below if it grows)
- Route handlers use `@app.get` / `@app.post` shorthand decorators
- DB access goes through `get_db()` (per-request connection via Flask `g`, closed in `teardown_appcontext`)
- Comments and some identifiers are in Indonesian (matches the UI language) — keep consistent when extending
- No type hints in most of `app.py` except `_parse_alternatif(raw: str) -> list[str]` — follow existing style (add type hints for new functions per global Python rule, but don't mass-retrofit old code)
- Section dividers use `# ── name ──…` comment banners (database / routes)

## Known Issues / Security TODOs

- **CRITICAL:** `EXPORT_PASSWORD` and `IMPORT_PASSWORD` are hardcoded plaintext in `app.py:247,275`
  (`"bangkevin523"`), sent as query param (`?pwd=`) / form field. This is a real secret checked into
  git history. Should move to an environment variable at minimum; a real auth mechanism would be better.
  Sending the password as a GET query string (`/api/hp/export?pwd=`) also risks it leaking into server
  access logs and browser history.
- No CSRF protection on the import endpoint.
- No input length/size limits on CSV import (unbounded file read into memory).
- `except Exception` used broadly in `_row_to_dict` and `import_csv` — swallows unexpected errors;
  acceptable for now given single-user internal tool, but revisit if this becomes multi-tenant.
- No rate limiting on any endpoint.
- `app.run(debug=True, ...)` in `if __name__ == "__main__"` — fine for local dev, but make sure debug
  mode is never what actually gets deployed (production path uses gunicorn via `run.sh`, so this is currently OK).
- No automated tests exist for any of the API routes or CSV parsing logic (`_parse_alternatif`,
  import/export round-trip, migration path for existing DBs).

## Untested Scenarios (no test suite exists yet — these are manual-test/first-test-suite candidates)

- CSV import with malformed/missing `ALTERNATIF` column
- CSV import with mixed delimiters (`csv.Sniffer` fallback logic in `import_csv`)
- CSV import replacing data when `hp` table is empty (no backup should be created — verify `backup_created` stays `False`)
- Search (`/api/hp?q=`) across all seven LIKE-matched columns, including partial JSON matches inside `alternatif`
- DB migration path: opening an old `hp_data.db` created before `jenis_tg`/`alternatif`/`merek_tg`/`kode_merek_tg`/`tahun_launching`/`bulan_launching` columns existed
- Wrong password on export/import (expect 401, no data leak in error body) — manually smoke-tested for both `hp` and `smartwatch` endpoints 2026-08-27, both correctly 401
- Concurrent import while another request is reading (SQLite locking behavior under gunicorn's 2 workers)
- `kode_merek_tg`/`tahun_launching`/`bulan_launching` CSV round-trip (template → edit → import → export) — manually smoke-tested via Flask test client 2026-08-27 (import/export/filters/recent_only all verified), still no automated pytest suite
- `recent_only=1` filter behavior across a year boundary (right now Jan 1 flips the cutoff instantly — untested whether that's the desired semantics or should be a rolling 365 days)
- `scripts/backfill_launch_dates.py` against the real ~3200-row `hp_data.db` — only smoke-tested against synthetic rows so far, not run against production data

## Next TODOs

1. Run `scripts/backfill_launch_dates.py` (or the "Isi Tahun/Bulan Otomatis" button) against the
   real `hp_data.db` and spot-check the match quality before relying on it.
2. Populate `smartwatch_data.db` with real data — currently just the schema/UI/API, table is empty.
3. Move `EXPORT_PASSWORD`/`IMPORT_PASSWORD` to env vars (`.env`, gitignored — already in `.gitignore`)
4. Add a pytest suite covering the scenarios above (no test infra currently set up: would need `pytest`,
   probably a temp-DB fixture overriding `DB_PATH`/`SMARTWATCH_DB_PATH`)
5. Consider splitting `app.py` if more routes/entities are added — now ~370 → ~600+ lines after the
   launch-date + smartwatch additions, still under the 800-line guideline but worth watching.

Sources tried and rejected for the HP Baru finder (see `scripts/find_new_phones.py` for what's
actually used): GSMArena (robots.txt explicitly disallows ClaudeBot/anthropic-ai + RSL license
forbids ai-inference/ai-train), kimovil.com / 91mobiles.com / smartprix.com (permissive robots.txt
but Cloudflare-blocked, 403 on any non-browser request), phonedb.net (accessible and legal, but only
exposes a "recently added to their DB" homepage feed, not a real per-brand catalog — coverage for
budget brands is inconsistent/timing-dependent).

## Future/Speculative Work

None currently planned — no MK2 or major version in progress. If a v2/redesign effort starts, split
its notes into `docs/` rather than growing this file.
