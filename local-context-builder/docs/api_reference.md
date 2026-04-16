# local-context-builder — API & 산출물 레퍼런스

> 작성일: 2026-04-16 · 대상 코드: `local-context-builder/app/`
> 이 문서는 **admin API 스펙**(쿼리 가능한 쪽)과 **각 스텝이 DB에 남기는 원시 산출물**(아직 쿼리가 없지만 나중에 만들 수 있는 쪽)을 한곳에 정리한다.
> Swagger 대체용이며, FastAPI OpenAPI 스키마(`GET /openapi.json`)와 병행해 읽으면 된다.

---

## 목차

1. [개요](#1-개요)
2. [공통 규약](#2-공통-규약)
3. [Admin API](#3-admin-api)
   - 3.1 [POST 잡 트리거](#31-post--잡-트리거)
   - 3.2 [GET 상태/조회](#32-get--상태조회)
   - 3.3 [DTO 정의](#33-dto-정의)
4. [파이프라인 스텝별 원시 산출물](#4-파이프라인-스텝별-원시-산출물)
   - STEP 0~10 각 테이블 스키마와 "어떤 쿼리를 만들 수 있는지" 힌트
5. [부록: 샘플 쿼리 템플릿](#5-부록-샘플-쿼리-템플릿)

---

## 1. 개요

`local-context-builder`는 카카오 Local API를 긁어 수원시 행정동(EMD) 단위의 **지역 피처 + 페르소나 가중치 + 스팟 시드**를 배치로 뽑아 내는 서비스다. 외부 트리거 표면은 단 하나 — `app/api/admin.py`의 FastAPI 라우터. 모든 긴 잡은 Celery로 위임되고, 짧은 읽기성 질의만 동기로 응답한다.

데이터 흐름(STEP 번호는 plan §6~§11 기준):

```
STEP 0   bootstrap_regions        → region_master, category_mapping_rule
STEP 1~3 full_rebuild/incremental → place_raw_kakao
STEP 4   normalize_places         → place_normalized
STEP 5   (v1.1) merge_real_data   → real_activity_agg
STEP 6   build_region_features    → region_feature
STEP 7   (STEP 6 내부 blending)    → region_feature.blended_score
STEP 8   build_persona_region_weights → persona_region_weight
STEP 9   build_spot_weights       → spot_seed_dataset
STEP 10  publish_dataset          → dataset_version (building→success/failed)
```

DB는 총 **9 테이블**(`region_master`, `category_mapping_rule`, `place_raw_kakao`, `place_normalized`, `real_activity_agg`, `region_feature`, `persona_region_weight`, `spot_seed_dataset`, `dataset_version`)이 전부. 이 중 **admin API가 현재 노출하는 것**은 `region_master / place_normalized / region_feature / persona_region_weight / dataset_version` 5개뿐이며, 나머지 4개는 스캐폴딩만 되어 있고 조회 쿼리가 없다 — §4에서 별도로 표시한다.

---

## 2. 공통 규약

- **Base URL**: `http://<host>:8000`
- **Router prefix**: `/admin`
- **인증**: `GET /admin/health`를 제외한 전 엔드포인트가 `X-Admin-Key` 헤더 필수. 값은 서버 `app.config.get_settings().admin_api_key`와 일치해야 함. 불일치 시 `401 {"detail": "invalid admin key"}`.
- **Content-Type**: 요청/응답 모두 `application/json`.
- **시간 포맷**: 모든 timestamp는 ISO 8601 문자열(`datetime.isoformat()`). UTC 가정.
- **잡 트리거 공통 응답**: 모든 POST는 `TaskQueuedResponse` 한 가지 모양으로 떨어진다 — 실제 작업 결과는 Celery worker 로그와 `dataset_version` 테이블에서 사후 확인.
- **에러 포맷**: FastAPI 기본 — `{"detail": "<message>"}`, 상태 코드로 원인 판별.

| 상태 코드 | 의미 |
|----------|------|
| 200      | 정상 |
| 400      | 필수 필드 누락 (예: `/publish`의 `dataset_version` 빈값) |
| 401      | `X-Admin-Key` 누락 또는 불일치 |
| 404      | 리소스 없음 (region_id 없는 행정동, persona×region 행 없음, 성공한 dataset_version 없음) |
| 422      | Pydantic 검증 실패 (타입 불일치, 범위 초과 등) |
| 500      | 내부 예외 |

---

## 3. Admin API

총 **13개 엔드포인트**. POST 5개는 Celery 잡을 큐잉만 하고 즉시 리턴(긴 잡), GET 8개는 DB를 직접 읽어 동기 응답한다.

### 3.1 POST — 잡 트리거

각 POST는 `X-Admin-Key` 필수. 응답은 전부 [`TaskQueuedResponse`](#taskqueuedresponse).

#### POST `/admin/bootstrap`

`region_master`와 `category_mapping_rule` 시드 로드. 초기 구성 시 한 번, 혹은 행정동 추가/카테고리 룰 재정의 시 수동으로 호출.

- **Body**: [`BootstrapRequest`](#bootstraprequest) *(optional; 기본값은 `target_city="suwon"`)*
- **Celery task**: `jobs.bootstrap_regions`
- **부수효과**: `data/region_master_suwon.csv`와 `data/category_mapping_seed.json`를 읽어 upsert.

```json
// Request
POST /admin/bootstrap
X-Admin-Key: <key>
{ "target_city": "suwon" }

// Response 200
{ "task_id": "e3f1...", "task_name": "jobs.bootstrap_regions", "status": "queued", "args": {"target_city": "suwon"} }
```

#### POST `/admin/full-rebuild`

카카오 Local API 전수 수집 스윕. **몇 십 분~수 시간** 단위로 도는 가장 무거운 잡. 모든 활성 region × 카테고리 그리드에 대해 페이지네이션을 돌면서 `place_raw_kakao`에 upsert.

- **Body**: [`FullRebuildRequest`](#fullrebuildrequest)
- **Celery task**: `jobs.full_rebuild`
- **사후 확인**: `GET /admin/status`의 `total_places_raw`가 증가하는지, 또는 worker 로그 `batch_id` 단위 진행률.

#### POST `/admin/incremental-refresh`

신선도가 떨어진 region만 선별 재수집. `region_master.last_collected_at` 기준으로 cadence(plan §12)를 만족하지 않는 행만 다시 찍는다. `force=true`면 cadence 무시하고 전부 찍음.

- **Body**: [`IncrementalRefreshRequest`](#incrementalrefreshrequest)
- **Celery task**: `jobs.incremental_refresh`

#### POST `/admin/build-features`

정규화 → 피처 → 페르소나 가중치 → 스팟 시드 → publish를 한 번에 돌리는 파이프라인. 원시 수집이 끝난 뒤 사용한다. `dataset_version`을 비우면 서버가 `v_YYYYMMDD_<hex6>` 형식으로 자동 생성.

- **Body**: [`BuildFeaturesRequest`](#buildfeaturesrequest)
- **Celery task**: `jobs.build_all_features`
- **사후 확인**: `GET /admin/dataset/latest`가 새 버전으로 바뀌고 `status="success"`가 되는지.

#### POST `/admin/publish`

기존 `dataset_version`에 대해 **quality gate만 다시** 돌리는 재검증 잡. `build_features`가 이미 내부에서 publish를 호출하므로 일반 경로에서는 거의 쓰지 않고, 수작업으로 특정 버전의 상태를 되돌리거나 재확인할 때만 사용.

- **Body**: [`PublishRequest`](#publishrequest) — `dataset_version`은 **필수**(빈값이면 400)
- **Celery task**: `jobs.publish_dataset`

---

### 3.2 GET — 상태/조회

#### GET `/admin/health` *(인증 없음)*

라이브니스 프로브. DB + Redis 두 체크를 합성해서 하나라도 실패면 `status="error"`. 오케스트레이터가 컨테이너 재시작 여부 결정용으로 호출.

- **Response**: [`HealthResponse`](#healthresponse)
- 호출 빈도가 높으므로 의도적으로 인증 제외.

#### GET `/admin/status`

운영 대시보드용 파이프라인 한눈 보기.

- **Response**: [`StatusResponse`](#statusresponse)
- **반환 필드**: 최신 dataset_version, 그 상태, 총/활성 region 수, 원시/정규화 place 수.

#### GET `/admin/dataset/latest`

가장 최근 `status="success"` 빌드 1건. 아직 성공한 빌드가 없으면 404.

- **Response**: [`DatasetVersionSummary`](#datasetversionsummary)

#### GET `/admin/dataset/versions?limit=20`

최근 N개의 `dataset_version` 행(성공/실패/빌딩 상관 없이 `created_at` 내림차순).

- **Query**: `limit` int (기본 20, 1~200)
- **Response**: [`DatasetVersionsResponse`](#datasetversionsresponse)

#### GET `/admin/region/{region_id}`

행정동 메타데이터 + 가장 최근 `region_feature` 한 행. 피처 없으면 `latest_feature=null`.

- **Path**: `region_id` int (≥1)
- **Response**: [`RegionDetail`](#regiondetail)
- **404**: region 없음

#### GET `/admin/region/{region_id}/places?limit=100&offset=0`

해당 region의 **정규화된** place 리스트. 페이지네이션 지원. 총 count는 `places` 배열과 별개로 `count`에 들어간다.

- **Path**: `region_id` int (≥1)
- **Query**: `limit` (1~1000, 기본 100), `offset` (≥0, 기본 0)
- **Response**: [`RegionPlacesResponse`](#regionplacesresponse)

#### GET `/admin/persona-region/{persona_type}/{region_id}?dataset_version=...`

페르소나 × region 어피니티 행 한 건. `dataset_version`을 지정하지 않으면 가장 최근(=`created_at` desc) 행을 반환. `explanation`은 피처별 기여도가 JSON으로 들어가 있어 디버깅용으로 쓸 수 있음.

- **Path**: `persona_type` str, `region_id` int
- **Query**: `dataset_version` str (optional)
- **Response**: [`PersonaRegionDetail`](#personaregiondetail)

#### GET `/admin/metrics`

Prometheus 없을 때 쓰는 단순 카운터. 총/활성 region, 원시/정규화 place, 빌드 버전 총/성공/실패 카운트.

- **Response**: [`MetricsResponse`](#metricsresponse)

---

### 3.3 DTO 정의

모든 모델은 Pydantic `BaseModel`. `Optional[...]` 필드는 JSON에서 `null` 또는 누락 가능.

#### `TaskQueuedResponse`

모든 POST 잡의 공통 응답.

| 필드        | 타입              | 필수 | 설명 |
|-------------|-------------------|:----:|------|
| `task_id`   | string            | ✓    | Celery가 발급한 task id (worker 로그 추적 키) |
| `task_name` | string            | ✓    | `jobs.<module>` 형식 |
| `status`    | string            | ✓    | 항상 `"queued"` |
| `args`      | object            | ✓    | 요청 본문 그대로 에코 (auditing용) |

#### `BootstrapRequest`

| 필드          | 타입   | 기본값  | 설명 |
|---------------|--------|---------|------|
| `target_city` | string | `suwon` | `region_master.target_city` 필터 |

#### `FullRebuildRequest`

| 필드          | 타입   | 기본값  | 설명 |
|---------------|--------|---------|------|
| `target_city` | string | `suwon` | 수집 대상 도시 |

#### `IncrementalRefreshRequest`

| 필드          | 타입    | 기본값   | 설명 |
|---------------|---------|----------|------|
| `target_city` | string  | `suwon`  | 수집 대상 도시 |
| `force`       | boolean | `false`  | `last_collected_at` cadence 무시 여부 |

#### `BuildFeaturesRequest`

| 필드              | 타입             | 기본값   | 설명 |
|-------------------|------------------|----------|------|
| `target_city`     | string           | `suwon`  | 대상 도시 |
| `dataset_version` | string \| null   | `null`   | 비우면 서버가 `v_YYYYMMDD_<hex6>` 자동 생성 |

#### `PublishRequest`

| 필드              | 타입   | 기본값  | 설명 |
|-------------------|--------|---------|------|
| `target_city`     | string | `suwon` | 대상 도시 |
| `dataset_version` | string | **필수** | 재검증할 버전. 빈값이면 400 |
| `build_type`      | string | `full`  | `full` / `incremental` 라벨 |

#### `StatusResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `latest_dataset_version` | string \| null | 가장 최근 빌드 이름 |
| `latest_status`          | string \| null | `building` / `success` / `failed` |
| `latest_built_at`        | string \| null | ISO8601 |
| `total_regions`          | int  | `region_master` 행 수 |
| `active_regions`         | int  | `is_active=true` 필터 |
| `total_places_raw`       | int  | `place_raw_kakao` 행 수 |
| `total_places_normalized`| int  | `place_normalized` 행 수 |

#### `DatasetVersionSummary`

`GET /admin/dataset/latest`와 `DatasetVersionsResponse.versions[]`에 공통 사용.

| 필드 | 타입 | 설명 |
|------|------|------|
| `version_name`  | string         | 고유 버전 이름 (UNIQUE) |
| `build_type`    | string         | `full` / `incremental` 등 |
| `target_city`   | string \| null | 대상 도시 |
| `status`        | string \| null | `building` / `success` / `failed` |
| `built_at`      | string \| null | ISO8601 |
| `region_count`  | int \| null    | 빌드된 region 수 |
| `place_count`   | int \| null    | 빌드된 정규화 place 수 |
| `error_message` | string \| null | 실패 시 사유 |

#### `DatasetVersionsResponse`

| 필드       | 타입                          | 설명 |
|-----------|-------------------------------|------|
| `versions`| array\<DatasetVersionSummary\> | `created_at desc`, 최대 `limit`개 |

#### `RegionFeatureBrief`

`region_feature` 한 행에서 **blending 메타(alpha/beta/real_data_score 제외)**만 요약.

| 필드 | 타입 | 단위/범위 | 설명 |
|------|------|-----------|------|
| `dataset_version`        | string         | —     | 어느 버전의 피처인지 |
| `food_density`           | float \| null  | 개/km² | `is_food` 밀도 |
| `cafe_density`           | float \| null  | 개/km² | `is_cafe` 밀도 |
| `activity_density`       | float \| null  | 개/km² | `is_activity` 밀도 |
| `nightlife_density`      | float \| null  | 개/km² | `is_nightlife` 밀도 |
| `lesson_density`         | float \| null  | 개/km² | `is_lesson` 밀도 |
| `park_access_score`      | float \| null  | 0~1    | 공원 접근성 |
| `culture_score`          | float \| null  | 0~1    | 문화시설 접근성 |
| `night_liveliness_score` | float \| null  | 0~1    | 야간 활기도 |
| `casual_meetup_score`    | float \| null  | 0~1    | 가벼운 만남 적합도 |
| `lesson_spot_score`      | float \| null  | 0~1    | 레슨 스팟 적합도 |
| `solo_activity_score`    | float \| null  | 0~1    | 솔로 활동 적합도 |
| `group_activity_score`   | float \| null  | 0~1    | 그룹 활동 적합도 |
| `kakao_raw_score`        | float \| null  | 0~1    | Kakao only 스코어 |
| `blended_score`          | float \| null  | 0~1    | Kakao + real data 블렌딩 |
| `raw_place_count`        | int   \| null  | —      | 원시 place 수 |
| `normalized_place_count` | int   \| null  | —      | 정규화 place 수 |

> 현재 API는 **real_data_score / alpha_used / beta_used**와 **feature_json**을 노출하지 않는다. 필요하면 §5 쿼리 템플릿 참고.

#### `RegionDetail`

| 필드 | 타입 | 설명 |
|------|------|------|
| `id`                | int    | `region_master.id` |
| `region_code`       | string | 행정동 코드 |
| `sido`              | string | 시도 |
| `sigungu`           | string | 시군구 |
| `emd`               | string | 행정동 |
| `center_lng`        | float  | 중심 경도 |
| `center_lat`        | float  | 중심 위도 |
| `area_km2`          | float \| null | 면적 |
| `target_city`       | string \| null | `suwon` 등 |
| `is_active`         | bool \| null | 활성 여부 |
| `last_collected_at` | string \| null | ISO8601 |
| `latest_feature`    | [RegionFeatureBrief](#regionfeaturebrief) \| null | 가장 최근 피처 1건 |

#### `PlaceBrief`

`place_normalized`의 6개 컬럼만 노출. 태그(`is_*`), 신뢰도, 주소는 내리지 않음 → 필요 시 §5 쿼리 추가.

| 필드 | 타입 | 설명 |
|------|------|------|
| `source_place_id`  | string | 카카오 place id |
| `name`             | string | 상호명 |
| `primary_category` | string | 내부 태그 |
| `sub_category`     | string \| null | 보조 카테고리 |
| `lng`              | float  | 경도 |
| `lat`              | float  | 위도 |

#### `RegionPlacesResponse`

| 필드       | 타입              | 설명 |
|-----------|-------------------|------|
| `region_id`| int              | |
| `count`    | int              | 총 행 수(페이지 아님) |
| `places`   | array\<PlaceBrief\>| 현재 페이지만 |

#### `PersonaRegionDetail`

| 필드 | 타입 | 설명 |
|------|------|------|
| `dataset_version`      | string         | |
| `persona_type`         | string         | `data/persona_types.json` 중 하나 |
| `region_id`            | int            | |
| `affinity_score`       | float          | 총합 친화도 |
| `create_offer_score`   | float \| null  | 오퍼 스팟 생성 적합도 |
| `create_request_score` | float \| null  | 리퀘스트 스팟 생성 적합도 |
| `join_score`           | float \| null  | 참여 적합도 |
| `explanation`          | object \| null | 피처별 기여 기록(JSONB) |

#### `HealthResponse`

| 필드     | 타입   | 설명 |
|---------|--------|------|
| `status`| string | `"ok"` 또는 `"error"` (하위 체크 중 하나라도 실패면 error) |
| `checks`| object | `{ "db": {...}, "redis": {...} }` — 각 체크는 `status` 키 포함 |

#### `MetricsResponse`

| 필드 | 타입 | 설명 |
|------|------|------|
| `total_regions`          | int | `region_master` 행 수 |
| `active_regions`         | int | `is_active=true` |
| `places_raw`             | int | `place_raw_kakao` 행 수 |
| `places_normalized`      | int | `place_normalized` 행 수 |
| `dataset_versions`       | int | `dataset_version` 총 수 |
| `successful_versions`    | int | `status="success"` |
| `failed_versions`        | int | `status="failed"` |
| `latest_dataset_version` | string \| null | 가장 최근 성공 버전 이름 |

---

## 4. 파이프라인 스텝별 원시 산출물

각 스텝이 **어느 테이블에** **어떤 컬럼으로** 무엇을 남기는지를 정리한다. `API?` 열은 현재 admin API가 그 데이터를 노출하는지 여부다.

> **어떻게 읽으면 되나**: "API?" 가 ❌인 테이블은 지금은 조회 쿼리가 없으므로 필요해지면 새 엔드포인트를 추가하면 된다. 각 섹션의 "추천 쿼리" 블록이 시작점이다.

### STEP 0 · bootstrap_regions

**잡**: `app/jobs/bootstrap_regions.py` · **출력 테이블**: `region_master`, `category_mapping_rule`

#### `region_master` *(API? ✅ `/admin/region/{id}`)*

행정동(EMD) 마스터. 이후 모든 테이블이 `region_id`로 참조한다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id`                | BIGINT PK | 자동 증분 |
| `region_code`       | VARCHAR(20) UNIQUE | 행정동 코드 |
| `sido` / `sigungu` / `emd` | VARCHAR | 시도/시군구/행정동명 |
| `center_lng` / `center_lat` | FLOAT | 중심좌표 |
| `bbox_min_lng` / `bbox_min_lat` / `bbox_max_lng` / `bbox_max_lat` | FLOAT | 경계 박스 (nullable) |
| `area_km2`          | FLOAT | 면적 — `region_feature`의 density 분모 |
| `grid_level`        | SMALLINT | 카카오 그리드 레벨 (기본 0) |
| `target_city`       | VARCHAR(20) | `suwon` 등 |
| `is_active`         | BOOLEAN | 배치 포함 여부 |
| `last_collected_at` | TIMESTAMPTZ | incremental refresh 기준점 |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

**인덱스**: `(target_city)`, `(is_active)`, `(last_collected_at)`

#### `category_mapping_rule` *(API? ❌)*

카카오 카테고리 → 내부 태그 매핑 룰. normalize 단계에서 참조.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | INT PK | |
| `kakao_category_group_code` | VARCHAR(10) | `FD6`, `CE7` 등 |
| `kakao_category_pattern`    | VARCHAR(200) | regex/like 패턴 |
| `keyword_pattern`           | VARCHAR(200) | 상호명 키워드 매칭 |
| `internal_tag`              | VARCHAR(30) | `food`, `cafe`, `activity`, `park`, `culture`, `nightlife`, `lesson` 등 |
| `confidence`                | FLOAT | `place_normalized.mapping_confidence`로 전파 |
| `priority`                  | INT | 우선순위 높음 → `primary_category` |
| `is_active`                 | BOOLEAN | 비활성 룰 |
| `notes`                     | TEXT | 설명 |

**쿼리 만들 만한 것**: "어떤 룰이 몇 건의 place에 매칭되었는가(ruleset coverage)", "신규 룰 A/B 시뮬레이션".

---

### STEP 1~3 · full_rebuild / incremental_refresh

**잡**: `app/jobs/full_rebuild.py`, `incremental_refresh.py` · **출력 테이블**: `place_raw_kakao`

#### `place_raw_kakao` *(API? ❌)*

카카오 Local API의 응답을 **손대지 않고** 저장. normalize가 바뀌어도 재수집 없이 다시 파생 가능하게 `raw_json`이 JSONB로 보존된다.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | BIGINT PK | |
| `region_id` | BIGINT FK→region_master | |
| `source_place_id` | VARCHAR(30) | 카카오 place id |
| `place_name` | VARCHAR(200) | 상호명 |
| `category_name` | VARCHAR(200) | 카카오 원본 카테고리 체인 |
| `category_group_code` | VARCHAR(10) | `FD6`, `CE7` 등 |
| `category_group_name` | VARCHAR(50) | |
| `phone`, `address_name`, `road_address_name` | VARCHAR | |
| `x`, `y` | FLOAT | 경도, 위도 |
| `place_url` | VARCHAR(500) | |
| `distance` | VARCHAR(20) | 카카오 응답 그대로 |
| `raw_json` | JSONB | 원본 응답 한 건 통째 |
| `search_type` | VARCHAR(20) | `category` / `keyword` |
| `search_query` | VARCHAR(100) | 사용된 쿼리 (category_group_code 또는 키워드) |
| `collected_at` | TIMESTAMPTZ | |
| `batch_id` | VARCHAR(50) | 한 번의 잡 실행을 묶는 키 (worker 로그와 매칭 가능) |

**제약**: `UNIQUE(source_place_id, region_id)` — 중복 upsert 방지.
**인덱스**: `(region_id)`, `(source_place_id)`, `(batch_id)`.

**쿼리 만들 만한 것**:
- batch_id별 수집 진척/에러율
- region당 place 수 히스토그램 (grid coverage)
- source_place_id 중복률 (normalize 병합 비율 추정)
- `raw_json->>'place_url'` 없는 비율 같은 데이터 품질 지표

---

### STEP 4 · normalize_places

**잡/프로세서**: `app/processors/normalize_places.py`, `category_mapper.py` · **출력 테이블**: `place_normalized`

#### `place_normalized` *(API? 부분 ✅)*

`/admin/region/{id}/places`가 6개 컬럼만 노출. 태그 booleans와 `mapping_confidence`는 현재 API에 없음.

| 컬럼 | 타입 | 노출 | 설명 |
|------|------|:---:|------|
| `id` | BIGINT PK | ❌ | |
| `region_id` | BIGINT FK | ❌ | (경로 파라미터로 필터) |
| `source` | VARCHAR(20) | ❌ | 기본 `'kakao'` |
| `source_place_id` | VARCHAR(30) | ✅ | |
| `name` | VARCHAR(200) | ✅ | |
| `primary_category` | VARCHAR(30) | ✅ | 우선순위 태그 |
| `sub_category` | VARCHAR(100) | ✅ | 보조 |
| `lng` / `lat` | FLOAT | ✅ | |
| `address_name` / `road_address_name` | VARCHAR | ❌ | |
| `is_food` | BOOL | ❌ | 다중 태그 |
| `is_cafe` | BOOL | ❌ | |
| `is_activity` | BOOL | ❌ | |
| `is_park` | BOOL | ❌ | |
| `is_culture` | BOOL | ❌ | |
| `is_nightlife` | BOOL | ❌ | |
| `is_lesson` | BOOL | ❌ | |
| `is_night_friendly` | BOOL | ❌ | 파생 태그 |
| `is_group_friendly` | BOOL | ❌ | 파생 태그 |
| `mapping_confidence` | FLOAT | ❌ | category_mapping_rule의 confidence |
| `collected_at` / `updated_at` | TIMESTAMPTZ | ❌ | |

**제약**: `UNIQUE(source, source_place_id)` — 동일 place가 여러 region에 걸쳐도 정규화 행은 1개.
**인덱스**: `(region_id)`, `(primary_category)`.

**쿼리 만들 만한 것**:
- region × `primary_category` 매트릭스
- `mapping_confidence < 0.8` 구간 — 룰셋 취약 구간 탐지
- `is_night_friendly AND is_group_friendly` 겹치는 place 리스트
- `place_raw_kakao`와 left join해서 "정규화되지 않은 원시 place" 찾기

---

### STEP 5 · merge_real_data *(v1.1 스텁)*

**잡**: `app/jobs/merge_real_data.py` · **출력 테이블**: `real_activity_agg`
`REALSERVICE_DATABASE_URL` 미설정 시 **no-op**. 현재(2026-04) 기본값은 실행되지 않음.

#### `real_activity_agg` *(API? ❌)*

실서비스 read replica에서 집계한 region 단위 스냅샷. region_feature의 `blended_score`를 만들기 위한 원료.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | BIGINT PK | |
| `region_id` | BIGINT FK | |
| `window_start` / `window_end` | DATE | 집계 윈도우 |
| `real_spot_count` | INT | 실제 생성된 스팟 수 |
| `real_join_count` | INT | 참여 수 |
| `real_completion_count` | INT | 완주 수 |
| `real_cancel_count` | INT | 취소 수 |
| `real_noshow_count` | INT | 노쇼 수 |
| `completion_rate` / `cancel_rate` / `noshow_rate` | FLOAT | 비율 |
| `real_food_spot_ratio` | FLOAT | 카테고리 점유율 |
| `real_activity_spot_ratio` | FLOAT | |
| `real_lesson_spot_ratio` | FLOAT | |
| `real_night_spot_ratio` | FLOAT | |
| `time_slot_distribution` | JSONB | `{ "09_12": 0.12, ... }` 자유 포맷 |
| `real_avg_group_size` | FLOAT | 평균 그룹 크기 |
| `real_hot_score` | FLOAT | 인기도 스칼라 |
| `created_at` | TIMESTAMPTZ | |

**제약**: `UNIQUE(region_id, window_start, window_end)`.

**쿼리 만들 만한 것**: 블렌딩 alpha/beta 튜닝용 분포, kakao_raw_score vs real_data_score 스캐터, 윈도우별 cohort 비교.

---

### STEP 6 + 7 · build_region_features

**프로세서**: `app/processors/build_region_features.py`, `app/services/scoring_service.py` · **출력 테이블**: `region_feature`
(STEP 6: Kakao only 계산, STEP 7: real data와 블렌딩 — 한 테이블에서 같이 업데이트됨)

#### `region_feature` *(API? 부분 ✅)*

`(region_id, dataset_version)`당 한 행. `/admin/region/{id}` 응답의 `latest_feature`로 일부 필드만 노출.

| 컬럼 | 타입 | 노출 | 설명 |
|------|------|:---:|------|
| `id` | BIGINT PK | ❌ | |
| `region_id` | BIGINT FK | ❌ | |
| `dataset_version` | VARCHAR(50) | ✅ | |
| `food_density` | FLOAT | ✅ | count/area_km2 |
| `cafe_density` | FLOAT | ✅ | |
| `activity_density` | FLOAT | ✅ | |
| `nightlife_density` | FLOAT | ✅ | |
| `lesson_density` | FLOAT | ✅ | |
| `park_access_score` | FLOAT | ✅ | 0~1 |
| `culture_score` | FLOAT | ✅ | 0~1 |
| `night_liveliness_score` | FLOAT | ✅ | 0~1 |
| `casual_meetup_score` | FLOAT | ✅ | 0~1 |
| `lesson_spot_score` | FLOAT | ✅ | 0~1 |
| `solo_activity_score` | FLOAT | ✅ | 0~1 |
| `group_activity_score` | FLOAT | ✅ | 0~1 |
| `kakao_raw_score` | FLOAT | ✅ | |
| `real_data_score` | FLOAT | ❌ | STEP 5가 돌아야 채워짐 |
| `blended_score` | FLOAT | ✅ | `α·kakao + β·real` |
| `alpha_used` / `beta_used` | FLOAT | ❌ | 블렌딩 가중치 — 재현성 기록 |
| `raw_place_count` | INT | ✅ | |
| `normalized_place_count` | INT | ✅ | |
| `feature_json` | JSONB | ❌ | 새 피처 실험용 escape hatch |
| `created_at` | TIMESTAMPTZ | ❌ | |

**제약**: `UNIQUE(region_id, dataset_version)`.

**쿼리 만들 만한 것**:
- 버전 간 `blended_score` diff (회귀 탐지)
- `feature_json` 안의 실험 필드 burst 여부
- `alpha_used` / `beta_used` 분포 — 블렌딩 파라미터가 일관적인가

---

### STEP 8 · build_persona_region_weights

**프로세서**: `app/processors/build_persona_region_weights.py` · **출력 테이블**: `persona_region_weight`

#### `persona_region_weight` *(API? ✅ `/admin/persona-region/{persona_type}/{region_id}`)*

`(dataset_version, persona_type, region_id)`당 한 행.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | BIGINT PK | |
| `dataset_version` | VARCHAR(50) | |
| `persona_type` | VARCHAR(50) | `data/persona_types.json`의 키 |
| `region_id` | BIGINT FK | |
| `affinity_score` | FLOAT | 총합 점수 |
| `create_offer_score` | FLOAT \| null | 오퍼 스팟 생성 적합도 |
| `create_request_score` | FLOAT \| null | 리퀘스트 스팟 생성 적합도 |
| `join_score` | FLOAT \| null | 참여 적합도 |
| `explanation_json` | JSONB | `{ "food_density": 0.12, "culture_score": 0.08, ... }` — 피처별 기여 |
| `created_at` | TIMESTAMPTZ | |

**제약**: `UNIQUE(dataset_version, persona_type, region_id)`.

**쿼리 만들 만한 것**:
- 페르소나별 top-N region 랭킹 (`ORDER BY affinity_score DESC`)
- `explanation_json`을 jsonb_each로 풀어 피처 기여 레이더 차트
- 버전 간 랭킹 변동 비교 (Spearman 등)

---

### STEP 9 · build_spot_weights

**프로세서**: `app/processors/build_spot_weights.py` · **출력 테이블**: `spot_seed_dataset`

#### `spot_seed_dataset` *(API? ❌)*

publish 최종 산출물. 다운스트림(매칭/추천)이 직접 읽는 테이블.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | BIGINT PK | |
| `dataset_version` | VARCHAR(50) | |
| `region_id` | BIGINT FK | |
| `spot_type` | VARCHAR(50) | `casual_meetup`, `lesson`, `solo`, `group`, ... |
| `category` | VARCHAR(30) | `food`, `cafe`, `activity`, ... |
| `expected_demand_score` | FLOAT | |
| `expected_supply_score` | FLOAT | |
| `recommended_capacity` | INT | 스팟 권장 인원 |
| `recommended_time_slots` | JSONB | 예: `["morning", "evening"]` |
| `price_band` | VARCHAR(20) | `low` / `mid` / `high` |
| `final_weight` | FLOAT | 공급/수요 결합 최종 가중치 |
| `payload_json` | JSONB | 다운스트림 확장 필드 |
| `created_at` | TIMESTAMPTZ | |

**제약**: `UNIQUE(dataset_version, region_id, spot_type, category)`.

**쿼리 만들 만한 것** (→ **여기가 제일 먼저 엔드포인트 추가해야 할 곳**):
- `GET /admin/dataset/{version}/spots?region_id=&spot_type=` 형태로 조회
- region별 top spot_type 랭킹
- `final_weight` vs `expected_demand/supply` 회귀
- payload_json 스키마 드리프트 탐지

---

### STEP 10 · publish_dataset

**서비스**: `app/services/publisher_service.py` · **출력 테이블**: `dataset_version`

`publish()`는 먼저 `status="building"`으로 row를 만들고, `verify_quality()`의 체크 리스트가 비면 `success`, 아니면 `failed`로 플립. 기존 성공 버전은 **절대** 수정하지 않는다(다운스트림 stable fallback 보장).

#### `dataset_version` *(API? ✅ `/admin/dataset/latest`, `/admin/dataset/versions`, `/admin/status`)*

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | BIGINT PK | |
| `version_name` | VARCHAR(50) UNIQUE | `v_YYYYMMDD_<hex6>` 규약 |
| `build_type` | VARCHAR(20) | `full` / `incremental` |
| `target_city` | VARCHAR(20) | |
| `built_at` | TIMESTAMPTZ | 실제 빌드 끝난 시각 |
| `source_window_start` / `source_window_end` | DATE | 원천 데이터 윈도우 |
| `region_count` | INT | 빌드된 region 수 |
| `place_count` | INT | 빌드된 정규화 place 수 |
| `status` | VARCHAR(20) | `building` / `success` / `failed` |
| `error_message` | TEXT | quality gate 실패 사유 |
| `notes` | TEXT | 자유 메모 |
| `created_at` | TIMESTAMPTZ | row insert 시점 |

**인덱스**: `(status)`.

---

## 5. 부록 — 샘플 쿼리 템플릿

API가 아직 감싸지 않은 질문을 DB에서 바로 확인할 때 쓸 수 있는 시작점. `psql` 기준.

### 5.1 원시 수집 상태 (STEP 1~3)

```sql
-- batch_id당 수집 건수와 최근 수집 시각
SELECT batch_id,
       COUNT(*)                    AS raw_rows,
       MIN(collected_at)           AS started,
       MAX(collected_at)           AS finished
  FROM place_raw_kakao
 GROUP BY batch_id
 ORDER BY finished DESC
 LIMIT 20;

-- region별 place 수 (그리드 coverage)
SELECT r.id, r.emd, COUNT(p.id) AS raw_count
  FROM region_master r
  LEFT JOIN place_raw_kakao p ON p.region_id = r.id
 WHERE r.is_active AND r.target_city = 'suwon'
 GROUP BY r.id, r.emd
 ORDER BY raw_count;
```

### 5.2 normalize 품질 (STEP 4)

```sql
-- 신뢰도 낮은 정규화 행 상위
SELECT region_id, primary_category, mapping_confidence, name
  FROM place_normalized
 WHERE mapping_confidence < 0.8
 ORDER BY mapping_confidence ASC
 LIMIT 200;

-- 원시는 있는데 정규화되지 않은 place
SELECT r.source_place_id, r.region_id, r.category_name
  FROM place_raw_kakao r
  LEFT JOIN place_normalized n
         ON n.source = 'kakao' AND n.source_place_id = r.source_place_id
 WHERE n.id IS NULL
 LIMIT 100;
```

### 5.3 region_feature 전체 노출 (STEP 6 + 7)

```sql
-- RegionFeatureBrief에서 빠진 real_data_score/alpha/beta 포함
SELECT region_id,
       kakao_raw_score, real_data_score, blended_score,
       alpha_used, beta_used,
       feature_json
  FROM region_feature
 WHERE dataset_version = :version
 ORDER BY region_id;
```

### 5.4 persona × region 랭킹 (STEP 8)

```sql
SELECT persona_type, region_id, affinity_score,
       create_offer_score, create_request_score, join_score
  FROM persona_region_weight
 WHERE dataset_version = :version
   AND persona_type = :persona
 ORDER BY affinity_score DESC
 LIMIT 20;
```

### 5.5 spot seed 조회 (STEP 9 — API 없음)

```sql
-- region별 top spot_type
SELECT dataset_version, region_id, spot_type, category,
       final_weight, expected_demand_score, expected_supply_score,
       recommended_capacity, recommended_time_slots, price_band
  FROM spot_seed_dataset
 WHERE dataset_version = :version
   AND region_id       = :region_id
 ORDER BY final_weight DESC;

-- 버전 간 final_weight diff (동일 region × spot_type × category)
SELECT a.region_id, a.spot_type, a.category,
       a.final_weight AS new_w, b.final_weight AS old_w,
       a.final_weight - b.final_weight AS delta
  FROM spot_seed_dataset a
  JOIN spot_seed_dataset b
    ON a.region_id = b.region_id
   AND a.spot_type = b.spot_type
   AND a.category  = b.category
 WHERE a.dataset_version = :new_version
   AND b.dataset_version = :old_version
 ORDER BY ABS(a.final_weight - b.final_weight) DESC
 LIMIT 50;
```

### 5.6 real_activity_agg 요약 (STEP 5 — v1.1)

```sql
SELECT region_id, window_start, window_end,
       real_spot_count, completion_rate, cancel_rate, noshow_rate,
       real_hot_score, time_slot_distribution
  FROM real_activity_agg
 WHERE window_end >= CURRENT_DATE - INTERVAL '30 days'
 ORDER BY real_hot_score DESC NULLS LAST
 LIMIT 50;
```

### 5.7 dataset_version 감사 로그 (STEP 10)

```sql
-- 최근 실패 원인
SELECT version_name, built_at, status, error_message
  FROM dataset_version
 WHERE status = 'failed'
 ORDER BY created_at DESC
 LIMIT 20;
```

---

## 부가 정보

- FastAPI는 `/openapi.json`, `/docs`, `/redoc`을 기본 노출하므로 **자동 생성된 Swagger UI**도 함께 쓸 수 있다. 이 문서는 그 위에 **DB 스키마·파이프라인 스텝 컨텍스트**를 덧붙인 것.
- 새 엔드포인트를 추가할 때 DTO는 기존 `admin.py` 상단의 "Pydantic schemas" 블록 패턴을 그대로 따르면 OpenAPI에 자동 반영된다.
- 우선적으로 감쌀 만한 후보(지금 ❌인 것들): `spot_seed_dataset` 조회, `place_normalized` 태그 필터, `region_feature` 풀 뷰, `category_mapping_rule` 룰셋 커버리지.
