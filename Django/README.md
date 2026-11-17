# Django port of the Azure Admin API

A 1:1 port of the FastAPI app in the repo root (`main.py` + `email_service.py`)
to Django 5 with native **async views**. Routes, request/response shapes, and
behavior (including mock mode when Azure credentials are absent) match the
original, so `index.html` and `groups.html` work unchanged.

## Run

```bash
pip install -r requirements.txt        # into the repo's venv
./start.sh                             # uvicorn on port 5000 (same as FastAPI)
```

or manually:

```bash
uvicorn azureadmin.asgi:application --host 0.0.0.0 --port 5000
```

`python manage.py runserver 5000` also works for quick checks, but uvicorn is
preferred: it runs one event loop, matching how the shared httpx client and
Graph semaphores are meant to be used.

Configuration comes from the **repo-root `.env`** (same file as the FastAPI
app); a `Django/.env` can override values. No database is used — don't run
`migrate`.

## Layout

- `azureadmin/` — project (settings, ASGI/WSGI, root URLs). Serves the copied
  frontends at `/` (index.html) and `/groups` (groups.html).
- `api/` — everything from `main.py`, split up:
  - `config.py` — env-derived constants (credentials flag, TTLs, bulk limits)
  - `graph.py` — token cache, shared httpx client, semaphores, retry wrapper
  - `caching.py` — TTL cache + `cache_invalidate`
  - `helpers.py` — user/group formatting, TTU role checks, upload parsing
  - `mock_data.py` — mock users/groups/licenses + `LICENSE_SKU_MAP`
  - `email_service.py` — SMTP service (template in `../templates/`)
  - `views/` — endpoints grouped by area; `views/common.py` has the
    `api_view` decorator that maps `ApiError` → FastAPI-style
    `{"detail": ...}` JSON errors
  - `urls.py` — all `/api/*` routes, same paths as FastAPI

## Deviations from the FastAPI original

- `POST /api/self-register` always returns the JSON result; the original
  returned an empty body when the email service failed to import (the final
  `return` sat inside the email `else:` branch).
- Validation errors are 422 with pydantic's error list, like FastAPI, but the
  exact error wording can differ.
- Missing multipart fields on the bulk endpoints return 400 instead of
  FastAPI's 422.
