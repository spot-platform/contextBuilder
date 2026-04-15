---
name: verify-pipeline-integration
description: 배치 데이터 파이프라인의 경계면(모델↔마이그레이션, 잡↔API, 컬럼 계약↔실제 구현)을 교차 비교하고 incremental QA를 수행하는 스킬. FastAPI admin 라우팅과 Celery 태스크 wire-up도 함께 구현한다. local-context-builder에 admin API(§14)를 연결하거나, 각 모듈 완성 직후 경계면 정합성을 검증해야 할 때 반드시 이 스킬을 사용할 것.
---

# verify-pipeline-integration

**존재 확인이 아니라 경계면 교차 비교.** `integration-qa` 에이전트가 사용한다.

## 언제 사용하는가

- 한 팀원이 모듈 완성을 보고할 때마다(=incremental QA)
- FastAPI admin 라우터에서 배치 잡을 호출할 때 (시그니처 드리프트 방지)
- Celery 태스크 등록과 delay 호출 연결이 필요할 때
- publish 파이프라인의 quality 검증 훅을 붙일 때

## Incremental QA 원칙

**전체 완성 후 1회가 아니라, 각 모듈 완성 직후 즉시 검증.** 오케스트레이터가 팀원 완료 신호를 받으면 이 에이전트가 즉시 수신하고 해당 모듈을 검증한다.

### 검증 타임라인

1. `01_infra_complete` 수신 → 인프라 검증 (임포트 체인, .env ↔ Settings, alembic init)
2. `02_schema_complete` 수신 → DDL ↔ 모델 ↔ 마이그레이션 3중 대조
3. `03_collector_complete` 수신 → 컬렉터 쓰기부와 `place_raw_kakao` 모델 대조
4. `04_processor_complete` 수신 → 파이프라인 체인 전체 대조
5. 각 단계에서 이슈 발견 즉시 담당 에이전트에게 SendMessage로 수정 요청

## 경계면 교차 비교 매트릭스

| # | A 경계면 | B 경계면 | 검증 수단 |
|---|---|---|---|
| 1 | `models/*.py` 컬럼 | `migrations/versions/0001_initial.py` | AST 파싱 또는 `alembic upgrade head` → `inspect()` 컬럼 비교 |
| 2 | `plan.md` §4 DDL | `models/*.py` | 컬럼명/타입/nullable/unique/인덱스명 체크리스트 |
| 3 | `collectors/category_collector.py` upsert dict 키 | `PlaceRawKakao` 필드 | 정적 검사 + dict 키 집합 비교 |
| 4 | `normalize_places.py` 쓰기 필드 | `PlaceNormalized` 필드 | 필드 커버리지 |
| 5 | `build_region_features.py` 쓰기 필드 | `RegionFeature` density/score/spot 컬럼 | 컬럼 전수 체크 |
| 6 | `build_persona_region_weights.py` 쓰기 필드 | `PersonaRegionWeight` + `dataset_version` | 필수 필드 누락 체크 |
| 7 | `publish_dataset.py` 상태 전이 | `DatasetVersion.status` 값 enum | 'building'→'success'/'failed' 경로 존재 |
| 8 | `api/admin.py` 엔드포인트 목록 | 플랜 §14 12개 엔드포인트 | 경로/메서드/파라미터 체크 |
| 9 | `api/admin.py` 함수 호출 | `jobs/*.py`, `processors/*.py` 시그니처 | import + 파라미터 매칭 |
| 10 | Celery task 등록 | 엔드포인트 `.delay()` 호출 | 이름 매칭 |

## 경계면별 검증 방법

### #1: models ↔ migration

가장 자주 깨지는 곳. 두 방법 중 하나:

**A. metadata diff** (권장)
```python
from app.db import Base
from app import models  # noqa
from sqlalchemy import create_engine
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata

engine = create_engine("postgresql://...")  # 테스트 DB 또는 docker
# 1. alembic upgrade head
# 2. 현재 metadata와 비교
ctx = MigrationContext.configure(engine.connect())
diff = compare_metadata(ctx, Base.metadata)
assert diff == [], f"drift: {diff}"
```

**B. AST 파싱** (DB 없이)
마이그레이션 파일의 `op.create_table` 호출을 파싱해 컬럼 이름/타입을 추출하고 모델과 대조.

실패 시: `schema-designer`에게 SendMessage로 구체적 불일치 보고.

### #2: DDL ↔ 모델

`local-context-builder-plan.md`에서 `CREATE TABLE` 블록을 regex로 추출 → 컬럼 테이블화 → 모델과 이름/타입 대조. 체크리스트를 QA 리포트에 포함.

### #3~#7: 구현 ↔ 모델 필드 커버리지

각 잡의 upsert/insert 코드를 파싱해 쓰기 대상 필드 집합을 추출. 해당 모델의 non-autoincrement 필드와 비교하여:
- **누락된 필수 필드** (nullable=False, no default) → ❌
- **존재하지 않는 필드로 쓰기** → ❌
- **선택적 필드 미사용** → ⚠️ 경고만

### #8~#10: API 라우팅

플랜 §14의 엔드포인트 인벤토리:
```
POST /admin/bootstrap
POST /admin/full-rebuild
POST /admin/incremental-refresh
POST /admin/build-features
POST /admin/publish
GET  /admin/status
GET  /admin/dataset/latest
GET  /admin/dataset/versions
GET  /admin/region/{region_id}
GET  /admin/region/{region_id}/places
GET  /admin/persona-region/{persona_type}/{region_id}
GET  /admin/health
GET  /admin/metrics
```

각각에 대해:
- `api/admin.py`에 존재?
- HTTP 메서드 일치?
- pydantic request/response 스키마 정의?
- `X-Admin-Key` 헤더 검증 존재?
- 내부 호출 대상 잡/서비스 함수 존재?

## admin API 구현

라우팅 파일 스켈레톤:

```python
# app/api/admin.py
from fastapi import APIRouter, Depends, Header, HTTPException
from app.config import Settings
from app.celery_app import celery
from app import jobs  # full_rebuild, incremental_refresh, etc.

router = APIRouter(prefix="/admin")

def require_admin_key(x_admin_key: str | None = Header(None)):
    if not x_admin_key or x_admin_key != Settings().admin_api_key:
        raise HTTPException(401, "invalid admin key")

@router.post("/full-rebuild", dependencies=[Depends(require_admin_key)])
def full_rebuild(target_city: str = "suwon"):
    task = celery.send_task("jobs.full_rebuild", args=[target_city])
    return {"task_id": task.id}
```

장시간 배치는 반드시 Celery task로 등록하고 `.send_task(...)` 또는 `.delay(...)` 호출. 동기 실행은 `build-features`처럼 짧은 작업에만.

## Celery 태스크 등록

```python
# app/celery_app.py (integration-qa가 확장)
from celery import Celery
from app.config import Settings
from app.jobs import full_rebuild as _full_rebuild
from app.jobs import incremental_refresh as _incr
from app.jobs import build_all_features as _build
from app.jobs import publish_dataset as _publish

_s = Settings()
celery = Celery("lcb", broker=_s.redis_url, backend=_s.redis_url)

@celery.task(name="jobs.full_rebuild")
def task_full_rebuild(target_city: str):
    return _full_rebuild.run_full_rebuild(target_city)

# 동일 패턴으로 나머지 태스크 등록
```

**주의**: 태스크 이름이 엔드포인트의 `send_task` 이름과 정확히 일치해야 함.

## QA 리포트 작성

`_workspace/05_qa_report.md`에 경계면별로 결과를 기록. 각 이슈는:

```markdown
### [#1 models ↔ migration] `region_feature.feature_json` 타입 불일치
- 파일: `app/models/region_feature.py:18`, `migrations/versions/0001_initial.py:124`
- 모델: `JSONB`, 마이그레이션: `Text`
- 원인: 마이그레이션 수동 수정 시 누락
- 요청: `schema-designer` @ 2026-04-13 13:42
- 상태: resolved (2026-04-13 13:58)
```

## 통합 테스트 최소 1개

```python
# tests/test_integration_pipeline.py
def test_pipeline_end_to_end_with_mocked_kakao(test_db, mock_kakao):
    # 1. seed region_master with 2 regions
    # 2. run full_rebuild with mocked API returning 3 places
    # 3. run normalize → place_normalized has 3 rows, category mapped
    # 4. run build_region_features → region_feature rows exist
    # 5. run build_persona_region_weights → 5 × 2 = 10 rows
    # 6. run build_spot_weights → spot_seed_dataset rows
    # 7. run publish_dataset → dataset_version.status == 'success'
```

Kakao API는 fixture로 완전히 mock.

## 체크리스트

- [ ] 경계면 매트릭스 10개 전부 리뷰
- [ ] admin 엔드포인트 12개 전부 라우터에 존재
- [ ] Celery 태스크 등록 이름과 send_task 이름 일치
- [ ] `_workspace/05_qa_report.md` 작성
- [ ] incremental QA 기록 (각 팀원 완료 시점별)
- [ ] 최소 1개 엔드투엔드 통합 테스트 통과
- [ ] 발견 이슈는 SendMessage로 담당자에게 전달

## 안티패턴

- "파일이 있음"으로 패스 처리
- `api/admin.py` 작성 후 `jobs/*.py`의 실제 함수명 검증 없이 끝냄
- 모델만 읽고 마이그레이션은 읽지 않음
- QA 리포트를 "전부 OK" 한 줄로 마감
- 이슈를 리포트에만 적고 수정 요청을 보내지 않음
- Celery 태스크를 등록만 하고 엔드포인트에서 호출하지 않음 (또는 반대)
