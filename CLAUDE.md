# Database Tempered Glass — Project Context

## Summary

Single-user internal web app for managing a lookup table of phone models (`hp` =
"handphone") to their tempered-glass type and equivalent/alternative glass codes.
Used by store staff to search "what glass fits phone X" and to bulk import/export
the catalog via CSV. Indonesian-language UI (`lang="id"`).

**Active features:**
- Search/list HP records (`GET /api/hp?q=`) — matches kode, tipe_hp, merek, jenis_tg, alternatif, merek_tg, kode_merek_tg
- CSV template download (`GET /api/hp/template`)
- Password-gated CSV export (`GET /api/hp/export?pwd=`)
- Password-gated CSV import — **full replace**: wipes table and re-inserts all rows,
  auto-backs up existing data to `backups/backup_<timestamp>.csv` first (`POST /api/hp/import`)
- Single-page frontend (`templates/index.html`, vanilla HTML/CSS/JS, no framework, no build step)
- **⚠️ Uncommitted (in progress):** new column `kode_merek_tg` (internal TG-brand SKU code,
  e.g. `"KA-A02"`) being added end-to-end — schema migration in `get_db()`, `_row_to_dict()`,
  search, template/export/import CSV (7th column `KODE MEREK TG`), and a new table column in
  `templates/index.html`. Not yet committed as of 2026-08-10 — finish/verify before starting
  unrelated work, or stash if picking up something else. See `docs/notes-ka-codes.md` for the
  raw KA-A code list this column is meant to hold (user hadn't given import instructions as of
  2026-07-30 — check if that's been resolved before assuming the list is unused).
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
- **Frontend:** Two Jinja templates — `templates/index.html` (main catalog UI) and
  `templates/hp_baru.html` (HP Baru report viewer) — inline `<style>` + `<script>`, zero
  external dependencies (no CDN, no npm)
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
| kode_merek_tg | TEXT   | internal TG-brand SKU code (e.g. "KA-A02"), default `''` — **uncommitted, in progress**, see Active Features |

`alternatif` relationships are meant to be **symmetric**: if kode A lists kode B as
an alternative, B should list A back. Not enforced by the schema/app — was manually
fixed once (see git history), watch for drift if `alternatif` is edited outside the
normal import flow.

`alternatif` is stored as a JSON string and parsed with `_parse_alternatif()` on import
(accepts JSON array, or `;`/`,`-separated fallback) and `_row_to_dict()` on read.

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
- DB migration path: opening an old `hp_data.db` created before `jenis_tg`/`alternatif`/`merek_tg`/`kode_merek_tg` columns existed
- Wrong password on export/import (expect 401, no data leak in error body)
- Concurrent import while another request is reading (SQLite locking behavior under gunicorn's 2 workers)
- `kode_merek_tg` CSV round-trip (template → edit → import → export) now that it's a 7th column — untested since the column is brand new/uncommitted

## Next TODOs

1. Finish and commit the in-progress `kode_merek_tg` column work (see Active Features) — currently
   uncommitted local changes to `app.py` and `templates/index.html`.
2. Move `EXPORT_PASSWORD`/`IMPORT_PASSWORD` to env vars (`.env`, gitignored — already in `.gitignore`)
3. Add a pytest suite covering the scenarios above (no test infra currently set up: would need `pytest`,
   probably a temp-DB fixture overriding `DB_PATH`)
4. Consider splitting `app.py` if more routes/entities are added (currently under the 800-line guideline, no action needed yet)

Sources tried and rejected for the HP Baru finder (see `scripts/find_new_phones.py` for what's
actually used): GSMArena (robots.txt explicitly disallows ClaudeBot/anthropic-ai + RSL license
forbids ai-inference/ai-train), kimovil.com / 91mobiles.com / smartprix.com (permissive robots.txt
but Cloudflare-blocked, 403 on any non-browser request), phonedb.net (accessible and legal, but only
exposes a "recently added to their DB" homepage feed, not a real per-brand catalog — coverage for
budget brands is inconsistent/timing-dependent).

## Future/Speculative Work

None currently planned — no MK2 or major version in progress. If a v2/redesign effort starts, split
its notes into `docs/` rather than growing this file.
