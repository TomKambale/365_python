# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Self-service Microsoft 365 account/group portal for Taita Taveta University (TTU). A FastAPI backend wraps the Microsoft Graph API (app-only client-credentials auth) for user self-registration, license assignment, group join/leave, and staff-driven bulk group membership uploads. Welcome emails go out over SMTP.

## Running

```bash
source venv/bin/activate
python main.py            # uvicorn on 0.0.0.0:$PORT (default 5000)
```

`./start.sh` does the same. There are no tests and no linter configured.

Configuration comes from `.env` (loaded with `override=True`; `.env.example` is empty — see the `os.getenv` calls in `main.py` and `email_service.py` for the full list):

- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` — Graph credentials. **If any is missing, the app silently runs in mock mode**: every endpoint checks `HAS_AZURE_CREDENTIALS` and serves the in-memory `mock_users`/`mock_groups`/`mock_licenses` lists instead of calling Graph. Useful for frontend work without a tenant.
- `SMTP_HOST/PORT/USER/PASSWORD`, `APP_URL` — email service.
- Tunables: `PORT`, `GRAPH_CONCURRENCY`, `*_CACHE_TTL`, `BULK_MAX_BYTES`, `BULK_MAX_ROWS`, `BULK_ADD_CONCURRENCY`, `BULK_EMAIL_COLUMN`.

## Structure

- `main.py` — the entire backend (~1700 lines, all routes under `/api/*`). Organized in banner-commented sections: token management → helpers → mock data → cache/concurrency → Pydantic models → routes (health/stats, users, groups, staff bulk, licenses, self-register).
- `email_service.py` — `EmailService` singleton (`email_service`); sends via `aiosmtplib`, renders `email-template.html` with `{{placeholder}}` substitution. Imported in a try/except — the app runs fine without it.
- `index.html` (self-registration portal) and `groups.html` (group management) — standalone static frontends, **not served by FastAPI**. They hardcode `API_BASE_URL` to `localhost:5000/api` / `127.0.0.1:5000/api` and are opened via something like VS Code Live Server (ports 5500/5501 are in the CORS allowlist).

## Architecture notes

**Graph plumbing** (top of `main.py`): all Graph traffic funnels through `call_graph_api()`, which uses a module-level cached bearer token (refetched on expiry), a shared `httpx.AsyncClient`, and a global semaphore (`GRAPH_CONCURRENCY`). Network failures to Graph surface as clean 502s with a DNS/VPN hint — this host is WSL2 and has had DNS trouble reaching `graph.microsoft.com`. `graph_request_with_retry()` adds backoff on 429/5xx and is used by the bulk-write paths.

**Caching**: a tiny in-process TTL cache (`cached_call`) with per-key locks to coalesce concurrent misses. Keys are prefixed (`groups:`, `members:`, `owners:`, `studentgroups:`, user-id lookups) and mutations must call `cache_invalidate(prefix...)` — join/leave/bulk-add already do; follow that pattern for any new write endpoint.

**Roles are inferred from email domain**, not auth: `@students.ttu.ac.ke` → student, `@ttu.ac.ke` → staff (`get_role`/`require_role`). The student domain **must be checked first** since it also ends with the staff domain. There is no real authentication — clients just send an email in the request body.

**Licensing**: `LICENSE_SKU_MAP` maps "Student"/"Faculty" to lists of tenant-specific SKU GUIDs with a `required` flag per SKU; optional SKUs failing doesn't fail self-registration. These GUIDs are tied to the real TTU tenant.

**Bulk upload** (`/api/staff/groups/{id}/members/bulk`): accepts `.xlsx`/`.xlsm`/`.csv`, auto-detects the email column (header match, then heuristic), reports per-row results keyed by 1-based spreadsheet row, and can return a downloadable report. Parsing lives in `parse_emails_from_upload`.

## Django port

`Django/` contains a 1:1 port of the backend to Django with native async views (`Django/README.md` has the layout and run instructions — `./start.sh` there runs uvicorn on port 5000). Routes, response shapes, mock mode, and the Graph/cache architecture match `main.py`; the FastAPI app in the repo root remains the original. Changes to backend behavior need to be mirrored in both until one is retired.

## Gotchas

- `venv/` is committed/tracked in parts and `__pycache__` shows up in `git status` — don't take the status output as a signal of real changes.
- Commented-out code blocks in `main.py` are kept deliberately as history of prior approaches (e.g. the old single-SKU license flow); don't resurrect them without checking the active replacement below.
