# env_schema.md

1:1 mapping between `.env` keys, `app.config.Settings` fields, and the
modules that consume them. If you add a new config knob, update **all
three** columns in the same PR.

## Core mapping

| `.env` key | `Settings` field | Type | Default | Consumers |
|---|---|---|---|---|
| `DATABASE_URL` | `database_url` | `str` (required) | — | `app/db.py` (batch engine), `migrations/env.py` (Alembic URL) |
| `REALSERVICE_DATABASE_URL` | `realservice_database_url` | `str \| None` | `None` | `app/db_readonly.py` (`get_readonly_engine`) |
| `REALSERVICE_STATEMENT_TIMEOUT_MS` | `realservice_statement_timeout_ms` | `int` | `30000` | `app/db_readonly.py` (libpq `options`) |
| `REDIS_URL` | `redis_url` | `str` (required) | — | `app/celery_app.py` (broker + backend) |
| `KAKAO_REST_API_KEY` | `kakao_rest_api_key` | `str` (required) | — | `app/collectors/kakao_local_client.py` (set by collector-engineer) |
| `ADMIN_API_KEY` | `admin_api_key` | `str` (required) | — | `app/api/admin.py` (set by integration-qa) — `X-Admin-Key` header check |
| `TARGET_CITY` | `target_city` | `str` | `"suwon"` | `app/jobs/*.py`, `app/collectors/*.py`, `app/processors/*.py` — scopes each batch run |

## Compose-only keys (NOT on `Settings`)

These are consumed by `docker-compose.yml` when bootstrapping the
Postgres container. They are read from `.env` via variable
interpolation, not via pydantic-settings.

| `.env` key | Used by | Purpose |
|---|---|---|
| `POSTGRES_USER` | `docker-compose.yml` / postgres service | Initial superuser |
| `POSTGRES_PASSWORD` | `docker-compose.yml` / postgres service | Initial password |
| `POSTGRES_DB` | `docker-compose.yml` / postgres service | Initial database name |

> The Python app never reads `POSTGRES_USER`/`POSTGRES_PASSWORD` directly;
> it only reads the assembled `DATABASE_URL`. Keep those two in sync in
> your local `.env` file.

## Invariants

1. Every field in `Settings` has a matching key in `.env.example`, and
   vice versa (except compose-only keys, which are listed above).
2. Secrets (`KAKAO_REST_API_KEY`, `ADMIN_API_KEY`,
   `REALSERVICE_DATABASE_URL`) must never be hardcoded. Only
   placeholder values (`replace-me-*`, empty string) appear in
   `.env.example`.
3. `REALSERVICE_DATABASE_URL` is the only batch-used field that is
   optional. All code paths that touch the real-service DB must call
   `get_readonly_engine()` and gracefully handle `None` (e.g. skip
   the merge_real_data job when the engine is absent).
4. `ADMIN_API_KEY` must only be compared via constant-time comparison
   once integration-qa wires up the auth guard.

## How `Settings` is loaded

- `pydantic_settings.BaseSettings` reads from process env first, then
  from the `.env` file (via `model_config.env_file`).
- Field names are case-insensitive, so `TARGET_CITY` matches
  `target_city`.
- `get_settings()` in `app/config.py` memoises the instance with
  `functools.lru_cache`. Tests that want fresh settings should call
  `get_settings.cache_clear()` before re-importing modules that
  capture a `Settings` at import time (`app/db.py`, `app/celery_app.py`).
