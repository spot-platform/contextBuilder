# local-context-builder

Batch service that collects Kakao Local data, blends it with real-service
activity logs, and publishes spot context datasets for the Spring Boot
main service. See `../local-context-builder-plan.md` for the full spec.

## Stack

- Python 3.12+
- FastAPI (admin API) + Celery 5 + Redis 7 (task queue)
- PostgreSQL 15 (batch-owned DB) + read-only pool to the live service DB
- SQLAlchemy 2.x + Alembic
- Docker Compose for local dev

## Quick start

```bash
cp .env.example .env          # fill in Kakao + admin keys
docker compose up --build     # postgres, redis, app (uvicorn), celery-worker
curl http://localhost:8000/admin/health
```

Run migrations inside the app container:

```bash
docker compose exec app alembic upgrade head
```

## Layout

See `local-context-builder-plan.md` §3. Top-level dirs:

- `app/` — FastAPI + Celery + collectors, processors, services, models
- `migrations/` — Alembic revisions
- `scripts/` — one-off CLI utilities
- `data/` — seed CSVs (e.g. Suwon region master)
- `tests/` — pytest suite
