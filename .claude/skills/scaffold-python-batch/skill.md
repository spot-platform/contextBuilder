---
name: scaffold-python-batch
description: Python 배치 서비스의 기초 골격을 생성하는 스킬. pyproject.toml, Docker Compose(app/postgres/redis), FastAPI+Celery 진입점, Alembic, pydantic-settings 기반 config, .env.example, app/db.py와 app/db_readonly.py를 일관되게 구성한다. local-context-builder 같은 FastAPI+Celery+PostgreSQL+Redis 기반 배치 서비스를 처음 스캐폴딩할 때 반드시 이 스킬을 사용할 것.
---

# scaffold-python-batch

Python 배치 서비스(FastAPI + Celery + PostgreSQL + Redis)의 기초를 일관되게 세우는 스킬. `infra-architect` 에이전트가 사용한다.

## 언제 사용하는가

- 새 배치 서비스 프로젝트 디렉토리를 스캐폴딩할 때
- 기존 프로젝트에 Celery/Alembic/Docker 기반을 처음 도입할 때
- 스캐폴딩과 로직 구현을 분리해야 할 때 (이 스킬은 **스켈레톤만** 만든다)

이 스킬이 하는 일이 아닌 것:
- 모델 정의 (schema-designer가 담당)
- 외부 API 클라이언트 (collector-engineer)
- 데이터 처리 로직 (processor-engineer)
- admin API 라우팅 (integration-qa)

## 워크플로우

### 1단계: 플랜의 디렉토리 구조 수용

`local-context-builder-plan.md` §3 같은 디렉토리 사양서가 있다면, 그것을 **진실의 원천**으로 삼는다. 추가·누락 없이 그대로 만든다.

없다면 기본 템플릿:

```
project/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── db_readonly.py
│   ├── celery_app.py
│   ├── collectors/
│   ├── jobs/
│   ├── processors/
│   ├── models/
│   ├── services/
│   ├── monitoring/
│   └── api/
├── migrations/
├── scripts/
├── data/
└── tests/
```

모든 Python 하위 디렉토리에 빈 `__init__.py`를 둔다.

### 2단계: pyproject.toml

Python 3.12+, 의존성은 다음 카테고리별로:
- **웹/비동기**: fastapi, uvicorn[standard], httpx
- **작업 큐**: celery, redis
- **DB/ORM**: sqlalchemy>=2, psycopg[binary] 또는 psycopg2-binary, alembic
- **설정**: pydantic-settings, python-dotenv
- **데이터**: pandas
- **테스트**: pytest, pytest-asyncio, respx (HTTP mock)

버전은 업스트림 최신 안정 버전을 고정하지 말고 `>=` 하한만 지정하여 유연성 확보.

### 3단계: Docker Compose

3개 서비스:
1. **app** — Dockerfile 빌드. `command`는 `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Celery worker는 별도 서비스로 분리하거나 같은 컨테이너에서 프로세스 매니저로 실행
2. **postgres** — `postgres:15` 공식 이미지, volume 마운트, 환경변수로 DB/유저/비번
3. **redis** — `redis:7-alpine`

포트: app 8000, postgres 5432(내부), redis 6379(내부). app만 호스트에 노출.

환경변수는 `env_file: .env`로 주입.

### 4단계: config.py (pydantic-settings)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Batch DB
    database_url: str
    # Real-service read-only DB
    realservice_database_url: str | None = None
    realservice_statement_timeout_ms: int = 30000
    # Celery / Redis
    redis_url: str
    # Kakao
    kakao_rest_api_key: str
    # Admin API
    admin_api_key: str
    # Target
    target_city: str = "suwon"
```

`.env.example`에는 모든 필드의 **예시값**(절대 실제 키 넣지 말 것)을 넣고, 주석으로 용도를 표기.

### 5단계: db.py / db_readonly.py

두 엔진을 **명확히 분리**:

```python
# app/db.py — 배치 전용 DB (쓰기 가능)
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import Settings

class Base(DeclarativeBase):
    pass

_settings = Settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
```

```python
# app/db_readonly.py — 실서비스 DB (read-only)
# connect_args로 default_transaction_read_only=on, statement_timeout 설정
```

read-only는 **절대 Base에 바인딩하지 말 것**. 쓰기 실수 방지.

### 6단계: Alembic 세팅

`alembic init migrations` 후 `migrations/env.py`에서:
- `from app.db import Base`
- `from app import models  # noqa — 모델 import 트리거`
- `target_metadata = Base.metadata`
- `sqlalchemy.url`은 `Settings().database_url`에서 동적으로

`alembic.ini`의 `script_location = migrations`.

### 7단계: main.py와 celery_app.py 최소 골격

`main.py`:
```python
from fastapi import FastAPI
app = FastAPI(title="local-context-builder")

@app.get("/admin/health")
def health():
    return {"status": "ok"}
```

admin 라우터 import는 TODO로 남겨두고 integration-qa가 채운다.

`celery_app.py`:
```python
from celery import Celery
from app.config import Settings
_s = Settings()
celery = Celery("lcb", broker=_s.redis_url, backend=_s.redis_url)
celery.conf.task_routes = {}  # 태스크 등록은 integration-qa가 채움
```

### 8단계: 완료 보고

`_workspace/01_infra/README.md`와 `env_schema.md`를 작성하여 팀원이 참조할 수 있게 한다. `env_schema.md`는 `.env` 키 ↔ Settings 필드 ↔ 사용처를 1:1로 표기.

## 체크리스트

- [ ] 플랜의 디렉토리 구조와 1:1 일치
- [ ] 모든 하위 디렉토리에 `__init__.py`
- [ ] `.env.example`의 모든 키가 `Settings`의 필드에 존재 (역도 성립)
- [ ] `db.py`와 `db_readonly.py` 엔진 분리 확인
- [ ] `alembic upgrade head`가 빈 DB에서 에러 없이 실행 (모델 없어도 no-op로 성공)
- [ ] `uvicorn app.main:app` 기동 + `/admin/health` 200 OK
- [ ] Celery import 에러 없음 (`python -c "from app.celery_app import celery"`)
- [ ] `_workspace/01_infra/README.md`와 `env_schema.md` 작성 완료

## 안티패턴

- 로직을 채워 넣음 (모델, 수집, 처리 — 모두 다른 에이전트 몫)
- `.env` 자체를 커밋하거나 실제 키를 `.env.example`에 노출
- 하나의 엔진으로 배치 DB와 실서비스 DB를 모두 처리
- 기존 파일을 조회 없이 덮어씀
