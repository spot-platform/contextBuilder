# 01_infra — Infrastructure scaffolding report

**Agent:** infra-architect
**Task:** T01 — Scaffold local-context-builder per plan §2/§3/§17
**Status:** complete

## Scope

Scaffolding only. No domain logic. Every `.py` file is importable but
leaves real work to downstream agents (schema-designer,
collector-engineer, processor-engineer, integration-qa).

## Files created

Under `/home/seojingyu/project/spotContextBuilder/local-context-builder/`:

### Project root
- `pyproject.toml` — Python 3.12+, deps with `>=` floors only
- `Dockerfile` — python:3.12-slim, builds via `pip install .`
- `docker-compose.yml` — 4 services: postgres:15, redis:7-alpine, app (uvicorn), celery-worker
- `alembic.ini` — `script_location = migrations`, URL injected at runtime
- `.env.example` — all keys 1:1 with `Settings`
- `.gitignore` — excludes `.env`, caches, venvs
- `README.md` — quick start

### app/
- `app/__init__.py`
- `app/main.py` — FastAPI + `/admin/health`; TODO for admin router mount
- `app/config.py` — `Settings` (pydantic-settings) + cached `get_settings()`
- `app/db.py` — `Base(DeclarativeBase)`, batch engine, `SessionLocal`, `get_session()` dependency
- `app/db_readonly.py` — lazy real-service engine with `statement_timeout` + `default_transaction_read_only=on`; `get_readonly_engine()` / `get_readonly_session()`; returns `None` when unset
- `app/celery_app.py` — `celery` instance, `autodiscover_tasks(["app.jobs"])`, Asia/Seoul timezone
- `app/collectors/__init__.py`
- `app/jobs/__init__.py`
- `app/processors/__init__.py`
- `app/models/__init__.py` — placeholder with TODO for schema-designer to re-export mappers
- `app/services/__init__.py`
- `app/monitoring/__init__.py`
- `app/api/__init__.py`

### migrations/
- `migrations/env.py` — imports `app.db.Base` + `app.models`, sets `target_metadata`, injects DB URL
- `migrations/script.py.mako` — standard Alembic revision template
- `migrations/README`
- `migrations/versions/.gitkeep` — empty; schema-designer will fill with autogenerate

### Other
- `scripts/__init__.py`
- `tests/__init__.py`
- `data/.gitkeep`

## Versions pinned

| Component | Version |
|---|---|
| Python | `>=3.12` |
| PostgreSQL | `15` (compose image tag) |
| Redis | `7-alpine` (compose image tag) |
| FastAPI | `>=0.115` |
| SQLAlchemy | `>=2.0` |
| psycopg | `>=3.2` (binary) |
| Alembic | `>=1.13` |
| Celery | `>=5.4` |
| pydantic-settings | `>=2.4` |

## Import sanity (mental dry-run)

`python -c "from app.main import app; from app.celery_app import celery; from app.db import Base"`

- `app.main` imports `FastAPI` only -> fine
- `app.celery_app` imports `Celery` + `app.config.get_settings()` -> requires `DATABASE_URL`, `REDIS_URL`, `KAKAO_REST_API_KEY`, `ADMIN_API_KEY` in env
- `app.db` imports `sqlalchemy.orm.DeclarativeBase` + `get_settings()` -> same env requirement
- As long as `.env` is loaded (pydantic-settings picks it up automatically), module import is error-free

> Note: `get_settings()` is called at module load in both `app.db` and
> `app.celery_app` (to build the engine / celery instance). If you need
> to import these modules in a test without env vars, set them in
> `conftest.py` before import, or monkeypatch `get_settings`.

## Handoff notes for downstream agents

### schema-designer (T02)
- Put every model under `app/models/<name>.py` and inherit from `app.db.Base`
- Re-export each model from `app/models/__init__.py` so `migrations/env.py` can `from app import models` and see them
- Generate the first revision with: `alembic revision --autogenerate -m "init schema"`
- The read-only engine (`app/db_readonly.py`) must NOT be bound to `Base.metadata`; do not `Base.metadata.create_all(bind=get_readonly_engine())` ever

### collector-engineer (T03)
- Read the Kakao key from `get_settings().kakao_rest_api_key` — do not read env vars directly
- `httpx` and `rapidfuzz`/`python-Levenshtein` are in deps, ready to import
- Respect `target_city` from settings when deciding which region rows to process
- Put the client in `app/collectors/kakao_local_client.py` (plan §3)

### processor-engineer (T04)
- Lives under `app/processors/`; `pandas` and `numpy` are available
- Do not read real-service DB directly — go through `app.db_readonly.get_readonly_engine()` and check for `None`

### integration-qa (T05)
- Admin router: create `app/api/admin.py`, then `app.main.include_router(admin_router, prefix="/admin")` at the TODO marker
- Celery tasks: create them in `app/jobs/*.py`, decorate with `@celery.task`; `autodiscover_tasks` already lists `app.jobs`
- Health endpoint already exists at `/admin/health`
- `X-Admin-Key` guard should read `get_settings().admin_api_key`

## Open TODOs left as markers in code

- `app/main.py` — mount admin router (integration-qa)
- `app/models/__init__.py` — re-export ORM models (schema-designer)

## Anti-patterns explicitly avoided

- No real keys in `.env.example`
- Batch DB engine and real-service read-only engine are fully separated; the read-only one is lazy and returns `None` when unconfigured
- Hardcoding of `target_city`, DB URLs, or API keys: none — all via `Settings`
- `alembic.ini` has empty `sqlalchemy.url`; the value is injected at runtime from settings

## Artifacts

- `_workspace/01_infra/README.md` — this file
- `_workspace/01_infra/env_schema.md` — env key ↔ Settings field ↔ usage map
