# Database Tempered Glass — Project Context

## Summary

Single-user internal web app for managing a lookup table of phone models (`hp` =
"handphone") to their tempered-glass type and equivalent/alternative glass codes.
Used by store staff to search "what glass fits phone X" and to bulk import/export
the catalog via CSV. Indonesian-language UI (`lang="id"`).

**Active features:**
- Search/list HP records (`GET /api/hp?q=`) — matches kode, tipe_hp, merek, jenis_tg, alternatif
- CSV template download (`GET /api/hp/template`)
- Password-gated CSV export (`GET /api/hp/export?pwd=`)
- Password-gated CSV import — **full replace**: wipes table and re-inserts all rows,
  auto-backs up existing data to `backups/backup_<timestamp>.csv` first (`POST /api/hp/import`)
- Single-page frontend (`templates/index.html`, vanilla HTML/CSS/JS, no framework, no build step)

## Tech Stack

- **Backend:** Flask (`app.py`, ~244 lines, all routes in one file), `sqlite3` stdlib driver (no ORM)
- **DB:** SQLite file `hp_data.db` (gitignored, lives next to `app.py`)
- **Frontend:** Single Jinja template `templates/index.html`, inline `<style>` + `<script>`, zero external dependencies (no CDN, no npm)
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

- **CRITICAL:** `EXPORT_PASSWORD` and `IMPORT_PASSWORD` are hardcoded plaintext in `app.py:128,154`
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
- Search (`/api/hp?q=`) across all five LIKE-matched columns, including partial JSON matches inside `alternatif`
- DB migration path: opening an old `hp_data.db` created before `jenis_tg`/`alternatif` columns existed
- Wrong password on export/import (expect 401, no data leak in error body)
- Concurrent import while another request is reading (SQLite locking behavior under gunicorn's 2 workers)

## Next TODOs

1. Move `EXPORT_PASSWORD`/`IMPORT_PASSWORD` to env vars (`.env`, gitignored — already in `.gitignore`)
2. Add a pytest suite covering the scenarios above (no test infra currently set up: would need `pytest`,
   probably a temp-DB fixture overriding `DB_PATH`)
3. Consider splitting `app.py` if more routes/entities are added (currently under the 800-line guideline, no action needed yet)

## Future/Speculative Work

None currently planned — no MK2 or major version in progress. If a v2/redesign effort starts, split
its notes into `docs/` rather than growing this file.
