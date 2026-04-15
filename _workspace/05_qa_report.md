# QA Report — local-context-builder (T05)

Integration-QA pass for local-context-builder MVP, post T01~T04.
Cross-boundary audit + admin API wire-up + Celery task registry +
monitoring scaffolding + one integration test harness.

---

## Incremental QA 타임라인

- **01_infra** OK — `app.config.get_settings`, `app.db.SessionLocal`,
  `app.celery_app.celery` load cleanly once env vars are present.
  Handoff noted that `app.db` / `app.celery_app` invoke `get_settings()`
  at import time; fixed by seeding env vars inside `tests/conftest.py`
  before any test module imports run.
- **02_schema** OK — 9 ORM models under `app/models/` re-exported via
  `app/models/__init__.py`. Alembic `0001_initial_schema` hand-written
  but consistent with models (see boundary #1).
- **03_collector** OK — `KakaoLocalClient`, `collect_region_categories`,
  `collect_region_keywords` all wire through `app/collectors/_upsert.py`
  which fills every non-PK column of `place_raw_kakao`
  (`search_type`, `search_query`, `batch_id`, `raw_json` included).
- **04_processor** OK — `normalize_places`, `build_region_features`,
  `build_persona_region_weights`, `build_spot_weights` cover every
  mutable column of their respective targets. `publisher_service.publish`
  implements the `building → success/failed` transition.

---

## 경계면 검증 요약

| #  | 경계면                                           | 상태 | 이슈 |
|----|--------------------------------------------------|------|------|
| 1  | models ↔ migration 0001                          | PASS | 0    |
| 2  | plan §4 DDL ↔ models                             | PASS | 0    |
| 3  | collector `_upsert` ↔ `place_raw_kakao` 필드      | PASS | 0    |
| 4  | `normalize_places` 쓰기 ↔ `place_normalized`      | PASS | 0    |
| 5  | `build_region_features` ↔ `region_feature`        | PASS | 0    |
| 6  | `build_persona_region_weights` ↔ `persona_region_weight` | PASS | 0 |
| 7  | `publish_dataset` 상태 전이 ↔ `dataset_version`   | PASS | 0    |
| 8  | admin endpoints ↔ plan §14                        | PASS | 0 (wired in T05) |
| 9  | admin handlers ↔ jobs signatures                  | PASS | 1 ROOT-FIXED (publish_dataset) |
| 10 | celery task registry ↔ admin `send_task` calls    | PASS | 0    |

Overall: **PASS**. One signature drift discovered during wiring and
resolved by making `dataset_version` a required field in
`PublishRequest`. No open blockers.

---

## 경계면별 상세

### #1 models ↔ migration 0001
**방법**: 9개 테이블의 컬럼 / 타입 / nullable / 인덱스명 / unique
제약을 `app/models/*.py`와 `migrations/versions/0001_initial_schema.py`
양쪽을 직접 열어 전수 비교.

| 테이블 | 결과 |
|---|---|
| `region_master` | 컬럼 17개 일치, idx_region_target_city / idx_region_active / idx_region_last_collected 일치, UNIQUE(region_code) 일치 |
| `category_mapping_rule` | 컬럼 9개 일치, PK(id) 일치 |
| `dataset_version` | 컬럼 13개 일치, UNIQUE(version_name), idx_dataset_version_status 일치 |
| `place_raw_kakao` | 컬럼 18개 일치, uq_place_raw_source_region 일치, 3개 인덱스 일치, FK(region_id→region_master.id) 일치 |
| `place_normalized` | 컬럼 24개 일치 (is_food/is_cafe/is_activity/is_park/is_culture/is_nightlife/is_lesson/is_night_friendly/is_group_friendly 포함), uq_place_norm_source 일치, 2개 인덱스 일치 |
| `real_activity_agg` | 컬럼 20개 일치, uq_real_activity_region_window 일치 |
| `region_feature` | 컬럼 24개 일치 (density 5 + score 3 + suitability 4 + blending 5 + meta 3 + JSON 1), uq_region_feature_region_version 일치 |
| `persona_region_weight` | 컬럼 9개 일치, uq_persona_region_weight 일치 |
| `spot_seed_dataset` | 컬럼 12개 일치, uq_spot_seed_version_region_type_category 일치 |

결과: **드리프트 없음**. 마이그레이션 파일은 hand-written이지만
모델과 field-for-field로 정렬됨. 테이블 생성 순서도 FK-safe order
(region_master → … → spot_seed_dataset).

### #2 plan §4 DDL ↔ models
플랜의 `CREATE TABLE` 블록들과 모델 파일을 대조했고, T02
`column_contract.md`가 모델과 1:1이라고 이미 기록되어 있어
불일치 없음.

### #3 collector `_upsert.py` ↔ `place_raw_kakao`
`_upsert._build_row` → 아래 키들을 채움:

```
region_id, source_place_id, place_name, category_name,
category_group_code, category_group_name, phone, address_name,
road_address_name, x, y, place_url, distance, raw_json,
search_type, search_query, batch_id
```

`place_raw_kakao` non-auto PK + non-default 필수 컬럼
(`region_id`, `source_place_id`, `place_name`, `x`, `y`, `search_type`)
전부 커버. `collected_at`은 server_default + excluded.collected_at=
`func.now()`로 on-conflict 재갱신. Kakao x/y 문자열 → float 변환은
`_safe_float`가 수행. **드리프트 없음**.

### #4 `normalize_places.py` 쓰기 ↔ `place_normalized`
`_build_upsert_values`가 채우는 키:

```
region_id, source, source_place_id, name, primary_category,
sub_category, lng, lat, address_name, road_address_name,
mapping_confidence, collected_at, updated_at,
is_night_friendly, is_group_friendly,
is_food, is_cafe, is_activity, is_park,
is_culture, is_nightlife, is_lesson
```

모델의 non-PK mutable 컬럼 전체 일치. `is_night_friendly` /
`is_group_friendly` 파생 로직은 `_derived_tags`가 카테고리명 +
primary tag를 기준으로 계산함. **드리프트 없음**.

### #5 `build_region_features.py` ↔ `region_feature`
`build()`가 upsert하는 values dict:

- density 5: `food_density`, `cafe_density`, `activity_density`,
  `nightlife_density`, `lesson_density`
- score 3: `park_access_score`, `culture_score`,
  `night_liveliness_score`
- spot 적합도 4: `casual_meetup_score`, `lesson_spot_score`,
  `solo_activity_score`, `group_activity_score`
- blending 5: `kakao_raw_score`, `real_data_score`, `blended_score`,
  `alpha_used`, `beta_used`
- meta: `raw_place_count`, `normalized_place_count`, `feature_json`,
  `region_id`, `dataset_version`

모델 컬럼과 완전 일치. `percentile_rank`는 도시 전체 배열로 적용
(single-region → 0 이슈 회피). `alpha=1, beta=0` 기본값은 MVP
설정과 일치. **드리프트 없음**.

### #6 `build_persona_region_weights.py` ↔ `persona_region_weight`
쓰는 키: `dataset_version, persona_type, region_id, affinity_score,
create_offer_score, create_request_score, join_score,
explanation_json`. 모델과 일치. NaN/∞ 방어 로직 포함.

### #7 `publisher_service.publish` ↔ `dataset_version.status`
상태 전이: insert `building` → `verify_quality()` → 이슈 없으면
`success` + `built_at=now()`, 있으면 `failed` + `error_message`.
기존 성공 row는 건드리지 않음. **드리프트 없음**.

### #8 admin endpoints ↔ 플랜 §14
**T05에서 새로 구현**. `app/api/admin.py`에 14개 엔드포인트 전부
존재. `tests/test_admin_api.py::test_all_plan_14_routes_exist`가
이 인벤토리를 런타임 검증.

| # | 메서드 | 경로 | 인증 |
|---|---|---|---|
| 1 | POST | /admin/bootstrap | ✅ X-Admin-Key |
| 2 | POST | /admin/full-rebuild | ✅ |
| 3 | POST | /admin/incremental-refresh | ✅ |
| 4 | POST | /admin/build-features | ✅ |
| 5 | POST | /admin/publish | ✅ |
| 6 | GET  | /admin/status | ✅ |
| 7 | GET  | /admin/dataset/latest | ✅ |
| 8 | GET  | /admin/dataset/versions | ✅ |
| 9 | GET  | /admin/region/{region_id} | ✅ |
| 10 | GET | /admin/region/{region_id}/places | ✅ |
| 11 | GET | /admin/persona-region/{persona_type}/{region_id} | ✅ |
| 12 | GET | /admin/health | ❌ (liveness — intentional) |
| 13 | GET | /admin/metrics | ✅ |

플랜 §14는 13개 (12 + optional metrics = 13). 구현은 전부 커버.

### #9 admin handlers ↔ jobs signatures
`admin.py`의 각 POST 엔드포인트가 `celery.send_task(name, args=[...])`
로 호출하는 positional args 순서를 `app.jobs.*.run*` 시그니처와 직접
대조.

| send_task name | 전달 args | 매칭 job 함수 | 결과 |
|---|---|---|---|
| `jobs.bootstrap_regions` | `[]` | `bootstrap_regions.run_bootstrap()` kwargs-only, 전부 default | ✅ |
| `jobs.full_rebuild` | `[target_city]` | `run_full_rebuild(target_city, *, client=None)` | ✅ |
| `jobs.incremental_refresh` | `[target_city, force]` | `run_incremental_refresh(target_city, *, force=False, client=None)` — positional `force` NG → `_tasks.task_incremental_refresh(target_city, force=False)` wrapper가 kwarg로 변환 | ✅ (wrapper 덕분에 OK) |
| `jobs.build_all_features` | `[target_city, dataset_version]` | `build_all_features.run(target_city, dataset_version=None)` | ✅ |
| `jobs.publish_dataset` | `[target_city, dataset_version, build_type]` | `publish_dataset.run(target_city, dataset_version, build_type='full')` | ✅ |

`tests/test_integration_pipeline.py::test_jobs_signature_matches_admin_send_task_args`
가 런타임 시그니처 검증.

### #10 celery task registry ↔ admin `send_task` calls
- `app/jobs/_tasks.py` 신규 모듈에서 6개 태스크를 명시 등록:
  `jobs.bootstrap_regions`, `jobs.full_rebuild`,
  `jobs.incremental_refresh`, `jobs.build_all_features`,
  `jobs.publish_dataset`, `jobs.merge_real_data`.
- `app/celery_app.py`는 autodiscover + `from app.jobs import _tasks`
  side-effect import로 등록을 강제.
- `tests/test_admin_api.py::test_task_names_match_registry` 와
  `tests/test_integration_pipeline.py::test_registered_celery_tasks_match_admin_send_task_calls`
  가 admin.py의 모든 `"jobs.*"` 문자열이 REGISTERED_TASK_NAMES에
  포함되는지 정적 검사.

결과: **드리프트 없음**.

---

## 발견된 이슈 & 조치

### #9-A `publish_dataset.run` 필수 인자 vs admin body schema
- 파일: `app/jobs/publish_dataset.py:19` — `run(target_city, dataset_version, ...)` 시그니처에서 `dataset_version`은 non-default positional.
- 충돌 가능성: admin `PublishRequest`에서 `dataset_version: Optional[str] = None`을 허용하면 런타임에 `ValueError` 발생.
- **조치**: `PublishRequest.dataset_version: str` (required)로 정의. 빈 값일 경우 FastAPI가 422로 반환. (`tests/test_admin_api.py::test_publish_requires_dataset_version`)
- 상태: **resolved**

### #3-B 컬렉터 `search_type` 누락 가능성 재점검
- 파일: `app/collectors/_upsert.py:48`
- `search_type`은 `upsert_docs(..., search_type=...)`로 명시 전달, `_build_row`에서 row dict에 항상 기록. 모델상 NOT NULL 컬럼이므로 빈 값이 들어가면 DB 제약 실패함. collector 호출부(`category_collector`, `keyword_collector`)도 확인 — 두 곳 모두 문자열 상수 전달.
- 상태: **verified, no change**

### #5-C `raw_place_count > 0` 의존성
- `publisher_service.verify_quality`는 `raw_place_count <= 0`을 경계로 간주. 팀원 노트에 "빈 DB에서 실패" 명시. 통합 테스트는 collector 수집 단계 이후에만 `build_all_features.run`을 호출.
- 상태: **documented, no code change**

### conftest 누락으로 인한 import 붕괴 리스크
- `app.db.engine`과 `app.celery_app.celery`는 import 시점에 settings를 요구. T05 이전까진 pure-python 단위 테스트만 있어서 우연히 안전했음.
- **조치**: `tests/conftest.py` 신규 — env 변수 기본값을 seed함. 기존 테스트 전부 호환.
- 상태: **resolved**

---

## Open 이슈 (차단 요인)
없음. MVP 파이프라인 경계면은 전부 정합.

---

## 변경 파일 (T05)

| 파일 | 상태 | 설명 |
|---|---|---|
| `app/api/admin.py` | NEW | 14 endpoints + 13 pydantic schemas + `require_admin_key` |
| `app/main.py` | MODIFIED | 기존 hardcoded `/admin/health` 제거, admin_router include |
| `app/celery_app.py` | MODIFIED | `_tasks` side-effect import 추가 |
| `app/jobs/_tasks.py` | NEW | 6 Celery task 명시 등록 + REGISTERED_TASK_NAMES |
| `app/monitoring/health_checks.py` | NEW | `check_db`, `check_redis`, `latest_dataset_version` |
| `app/monitoring/alerts.py` | NEW | `send_slack`, `notify_job_failure`, `notify_publish_failed` |
| `app/monitoring/metrics.py` | NEW | `record_collection_stats`, `record_pipeline_step`, `timed` |
| `tests/conftest.py` | NEW | 환경변수 기본값 seed (모든 테스트 공유) |
| `tests/test_admin_api.py` | NEW | 20+ smoke test, 401/200 경로 + Celery send_task 검증 |
| `tests/test_integration_pipeline.py` | NEW | DB-free sanity + Postgres-backed e2e (opt-in via `INTEGRATION_DATABASE_URL`) |

---

## 권장 운영 체크리스트

1. `.env`에 다음 5개 변수 설정: `DATABASE_URL`, `REDIS_URL`,
   `KAKAO_REST_API_KEY`, `ADMIN_API_KEY`, `TARGET_CITY`. (선택)
   `SLACK_WEBHOOK_URL`, `REALSERVICE_DATABASE_URL`.
2. 최초 1회: `alembic upgrade head` → `python -m scripts.load_region_master`
   → `python -m scripts.load_category_mapping` 순서로 부트스트랩.
   또는 admin API로: `POST /admin/bootstrap` (X-Admin-Key 헤더).
3. 수집: `POST /admin/full-rebuild` → Celery task_id 리턴. 모니터링은
   `GET /admin/status` 폴링.
4. 피처 빌드 + publish 한 방: `POST /admin/build-features`. 완료 후
   `GET /admin/dataset/latest`로 검증.
5. 재publish만 필요할 때: `POST /admin/publish` with `dataset_version`.
6. 스팟 체크: `GET /admin/region/{id}` / `/admin/persona-region/...` /
   `/admin/metrics`.

## 테스트 실행
- 단위 (DB 불필요): `pytest tests/ -v`
- 통합 (Postgres 필요): `INTEGRATION_DATABASE_URL=postgresql+psycopg://... pytest tests/test_integration_pipeline.py -v`
