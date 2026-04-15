# local-context-builder 구현 계획서

> 최종 갱신: 2026-04-13
> 상태: Draft v2 (피드백 반영)

---

## 0. 문서 개요

이 문서는 Spot 플랫폼의 지역/스팟 후보/가중치 데이터셋을 사전 생성하는 배치 서비스 `local-context-builder`의 구현 계획이다.

### 핵심 원칙

- 실시간 서비스가 아니다. 사전 수집 → 배치 처리 → 정적 데이터셋 배포 구조다.
- 초기 대상은 **수원시**(행정동 약 50개)로 한정하되, 전국 확장을 전제로 설계한다.
- 카카오 Local API 쿼터 제약 하에서 운영 가능한 수집 전략을 쓴다.
- 실서비스(Spring Boot)와는 DB 레벨 또는 파일 레벨로만 연동하며, 직접 의존하지 않는다.

---

## 1. 서비스 정의

### 서비스명

`local-context-builder`

### 역할

| 단계 | 설명 |
|------|------|
| 수집 | 행정동 단위로 카카오 장소 데이터 배치 수집 |
| 정제 | 장소 데이터 정규화, 카테고리 매핑, 중복 제거 |
| 피처 생성 | 지역별 feature 벡터 생성 (밀도, 점수, 적합도) |
| 결합 | 실유저 활동 데이터 + 페르소나 데이터 병합 |
| 산출 | 실서비스에 바로 넣을 최종 데이터셋 생성 |
| 배포 | 데이터셋 버전 관리 및 publish |

### 입력

- 카카오 Local API 원천 데이터
- 내부 실유저 활동 데이터 (Spring Boot 실서비스에서 추출)
- 내부 유저 페르소나 데이터

### 출력

- `region_feature` — 지역 요약 피처
- `persona_region_weight` — 페르소나-지역 친화도
- `spot_seed_dataset` — 스팟 형성 가중치 데이터셋

---

## 2. 기술 스택

### 런타임

- **Python 3.12+**
  - 수집/배치/피처 생성에 적합
  - pandas, httpx, sqlalchemy 생태계 활용

### 프레임워크

- **FastAPI** — 배치 트리거용 관리 API
- **Celery + Redis** — 장시간 배치 작업 큐 (수집 작업이 수 시간 소요 가능)
- **APScheduler** — 주기 실행 스케줄러 (초기엔 cron도 가능)

### 저장소

- **PostgreSQL 15+** — 배치 서비스 전용 DB (실서비스 DB와 분리)
- **Redis** — Celery 브로커, 배치 상태/락 관리

### 배포

- **Docker Compose** — 초기 단일 서버 배포
- 컨테이너 구성: app(FastAPI + Celery worker), PostgreSQL, Redis
- 추후 K8s나 클라우드 매니지드 서비스로 전환 가능

### 실서비스 연동 방식

실서비스(Spring Boot)와의 데이터 교환:

```
[Spring Boot 실서비스 DB]
        │
        ▼ (읽기 전용, 별도 커넥션)
[local-context-builder]
        │
        ▼ (쓰기)
[배치 서비스 전용 DB]
        │
        ▼ (publish)
[실서비스가 읽는 최종 테이블 or 파일]
```

**실서비스 DB 직접 조회에 대한 결정:**

실서비스에 read replica가 없으므로 두 가지 옵션이 있다.

- **옵션 A (권장, 초기):** 실서비스 DB에 read-only 계정으로 접속하되, 배치 실행을 야간 트래픽 저점 시간(새벽 2~5시)에 한정한다. 쿼리에 statement_timeout을 걸고, 한 번에 가져오는 데이터 범위를 최근 28일로 제한한다.
- **옵션 B (확장 시):** 실서비스가 활동 집계 데이터를 별도 테이블에 주기적으로 dump하거나, 이벤트 기반(CDC)으로 배치 서비스 DB에 동기화한다.

초기에는 옵션 A로 시작하고, 트래픽이 늘면 옵션 B로 전환한다.

---

## 3. 디렉토리 구조

```
local-context-builder/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
│
├── app/
│   ├── main.py                          # FastAPI 앱 진입점
│   ├── config.py                        # 환경변수, 설정 관리
│   ├── db.py                            # DB 세션, 엔진
│   ├── db_readonly.py                   # 실서비스 DB read-only 연결
│   ├── celery_app.py                    # Celery 인스턴스
│   │
│   ├── collectors/                      # 외부 데이터 수집
│   │   ├── kakao_local_client.py        # 카카오 API 클라이언트 (rate limit, retry 포함)
│   │   ├── region_master_loader.py      # 행정동 마스터 데이터 로더
│   │   ├── category_collector.py        # 카테고리 기반 장소 수집
│   │   ├── keyword_collector.py         # 키워드 기반 장소 수집
│   │   └── grid_strategy.py            # 밀집 지역 grid 분할 로직
│   │
│   ├── jobs/                            # 배치 잡 오케스트레이션
│   │   ├── bootstrap_regions.py         # 지역 마스터 초기 구축
│   │   ├── full_rebuild.py              # 전체 장소 풀수집
│   │   ├── incremental_refresh.py       # 증분 갱신
│   │   ├── merge_real_data.py           # 실유저 데이터 집계
│   │   ├── build_all_features.py        # 피처 + 가중치 전체 빌드
│   │   └── publish_dataset.py           # 최종 배포
│   │
│   ├── processors/                      # 데이터 처리 로직
│   │   ├── normalize_places.py          # 장소 정규화, 중복 제거
│   │   ├── category_mapper.py           # 카카오 카테고리 → 내부 태그 매핑
│   │   ├── build_region_features.py     # 지역 피처 벡터 생성
│   │   ├── build_persona_region_weights.py  # 페르소나-지역 친화도
│   │   └── build_spot_weights.py        # 스팟 형성 가중치
│   │
│   ├── models/                          # SQLAlchemy 모델
│   │   ├── region.py
│   │   ├── place_raw.py
│   │   ├── place_normalized.py
│   │   ├── region_feature.py
│   │   ├── real_activity_agg.py
│   │   ├── persona_region_weight.py
│   │   ├── spot_seed.py
│   │   └── dataset_version.py
│   │
│   ├── services/                        # 비즈니스 로직 서비스
│   │   ├── feature_service.py           # 피처 보정 (alpha/beta 가중치)
│   │   ├── scoring_service.py           # 점수 정규화 유틸리티
│   │   └── publisher_service.py         # 배포 로직
│   │
│   ├── monitoring/                      # 모니터링/알림
│   │   ├── health_checks.py             # 배치 상태 체크
│   │   ├── alerts.py                    # 이상 감지 및 알림
│   │   └── metrics.py                   # 수집 통계 기록
│   │
│   └── api/
│       └── admin.py                     # 관리 API 라우터
│
├── migrations/                          # Alembic 마이그레이션
├── scripts/
│   └── load_region_master.py            # 행정동 마스터 CSV → DB 적재 스크립트
├── data/
│   └── region_master_suwon.csv          # 수원시 행정동 마스터 시드 데이터
└── tests/
    ├── test_kakao_client.py
    ├── test_category_mapper.py
    ├── test_grid_strategy.py
    ├── test_normalize.py
    └── test_region_features.py
```

---

## 4. 데이터 모델

### 4-1. 지역 기준 테이블 — `region_master`

행정동 마스터. 전국 확장을 전제로 계층 구조를 가진다.

```sql
CREATE TABLE region_master (
    id              BIGSERIAL PRIMARY KEY,
    region_code     VARCHAR(20) NOT NULL UNIQUE,   -- 행정동 코드 (예: 4111100100)
    sido            VARCHAR(20) NOT NULL,           -- 시도 (예: 경기도)
    sigungu         VARCHAR(20) NOT NULL,           -- 시군구 (예: 수원시 장안구)
    emd             VARCHAR(30) NOT NULL,           -- 읍면동 (예: 연무동)
    center_lng      DOUBLE PRECISION NOT NULL,
    center_lat      DOUBLE PRECISION NOT NULL,
    bbox_min_lng    DOUBLE PRECISION,
    bbox_min_lat    DOUBLE PRECISION,
    bbox_max_lng    DOUBLE PRECISION,
    bbox_max_lat    DOUBLE PRECISION,
    area_km2        DOUBLE PRECISION,               -- 면적 (grid 분할 판단용)
    grid_level      SMALLINT DEFAULT 0,             -- 0=분할 불필요, 1~3=분할 수준
    target_city     VARCHAR(20),                    -- 타겟 도시 태그 (예: 'suwon')
    is_active       BOOLEAN DEFAULT TRUE,
    last_collected_at TIMESTAMPTZ,                  -- 마지막 카카오 수집 시각
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_region_target_city ON region_master(target_city);
CREATE INDEX idx_region_active ON region_master(is_active);
CREATE INDEX idx_region_last_collected ON region_master(last_collected_at);
```

**초기 데이터 소스:** 행정안전부 행정표준코드관리시스템에서 행정동 코드 CSV를 다운로드하여 적재한다. 중심좌표는 카카오 coord2regioncode API의 역방향 조회 또는 통계청 SGIS 좌표 데이터를 사용한다. 수원시만 `target_city = 'suwon'`, `is_active = TRUE`로 설정하고, 나머지 행정동은 적재만 해두고 `is_active = FALSE`로 둔다.

### 4-2. 카카오 원천 장소 — `place_raw_kakao`

카카오 API 응답을 가공 없이 저장한다.

```sql
CREATE TABLE place_raw_kakao (
    id                  BIGSERIAL PRIMARY KEY,
    region_id           BIGINT NOT NULL REFERENCES region_master(id),
    source_place_id     VARCHAR(30) NOT NULL,        -- 카카오 장소 ID
    place_name          VARCHAR(200) NOT NULL,
    category_name       VARCHAR(200),                -- "음식점 > 한식 > 갈비" 전체 경로
    category_group_code VARCHAR(10),                 -- FD6, CE7, AT4 등
    category_group_name VARCHAR(50),
    phone               VARCHAR(30),
    address_name        VARCHAR(300),
    road_address_name   VARCHAR(300),
    x                   DOUBLE PRECISION NOT NULL,   -- 경도
    y                   DOUBLE PRECISION NOT NULL,   -- 위도
    place_url           VARCHAR(500),
    distance            VARCHAR(20),
    raw_json            JSONB,                       -- 원본 응답 전체
    search_type         VARCHAR(20) NOT NULL,        -- 'category' | 'keyword'
    search_query        VARCHAR(100),                -- 검색에 사용된 키워드 (keyword 검색 시)
    collected_at        TIMESTAMPTZ DEFAULT NOW(),
    batch_id            VARCHAR(50),
    UNIQUE(source_place_id, region_id)
);

CREATE INDEX idx_place_raw_region ON place_raw_kakao(region_id);
CREATE INDEX idx_place_raw_source_id ON place_raw_kakao(source_place_id);
CREATE INDEX idx_place_raw_batch ON place_raw_kakao(batch_id);
```

### 4-3. 카테고리 매핑 룰 — `category_mapping_rule`

카카오 카테고리를 내부 태그로 변환하는 규칙 테이블. 하드코딩 대신 데이터로 관리한다.

```sql
CREATE TABLE category_mapping_rule (
    id                      SERIAL PRIMARY KEY,
    kakao_category_group_code VARCHAR(10),            -- FD6, CE7 등 (NULL이면 group 무관)
    kakao_category_pattern  VARCHAR(200),             -- category_name LIKE 패턴
    keyword_pattern         VARCHAR(200),             -- search_query 매칭 패턴 (keyword 수집분용)
    internal_tag            VARCHAR(30) NOT NULL,     -- food, cafe, activity, park, culture, nightlife, lesson
    confidence              DOUBLE PRECISION DEFAULT 1.0,  -- 매핑 신뢰도
    priority                INT DEFAULT 0,            -- 동일 장소에 복수 매칭 시 우선순위
    is_active               BOOLEAN DEFAULT TRUE,
    notes                   TEXT
);
```

**초기 매핑 규칙 (시드 데이터):**

| kakao_category_group_code | kakao_category_pattern | internal_tag | notes |
|---------------------------|----------------------|--------------|-------|
| FD6 | NULL | food | 음식점 전체 |
| CE7 | NULL | cafe | 카페 전체 |
| NULL | `%주점%` 또는 `%술집%` 또는 `%바%` | nightlife | category_name 패턴 |
| CT1 | NULL | culture | 문화시설 |
| AT4 | NULL | activity | 관광명소 → 액티비티로 매핑 |
| NULL | `%체육%` 또는 `%스포츠%` 또는 `%헬스%` | activity | 스포츠시설 |
| AC5 | NULL | lesson | 학원 |
| NULL | `%공방%` 또는 `%원데이클래스%` | lesson | 키워드 매칭 |
| NULL | `%공원%` | park | 키워드 매칭 |
| NULL | `%전시%` 또는 `%갤러리%` 또는 `%미술관%` | culture | 키워드 매칭 |

**매핑 로직:**
1. `category_group_code` 정확히 매칭 시도
2. `category_name` LIKE 패턴 매칭 시도
3. `search_query` 키워드 패턴 매칭 시도 (keyword 수집분에서 category_group_code가 비어있을 때)
4. 복수 매칭 시 `priority` 높은 것 우선, `confidence` 가중치 반영
5. 어디에도 안 걸리면 `internal_tag = 'other'` 처리하고 로그 남김

### 4-4. 정제 장소 — `place_normalized`

```sql
CREATE TABLE place_normalized (
    id                  BIGSERIAL PRIMARY KEY,
    region_id           BIGINT NOT NULL REFERENCES region_master(id),
    source              VARCHAR(20) DEFAULT 'kakao',
    source_place_id     VARCHAR(30) NOT NULL,
    name                VARCHAR(200) NOT NULL,
    primary_category    VARCHAR(30) NOT NULL,         -- food, cafe, activity, park, culture, nightlife, lesson, other
    sub_category        VARCHAR(100),                 -- 카카오 원본 카테고리 경로 보존
    lng                 DOUBLE PRECISION NOT NULL,
    lat                 DOUBLE PRECISION NOT NULL,
    address_name        VARCHAR(300),
    road_address_name   VARCHAR(300),
    -- boolean 태그 (복수 태그 가능)
    is_food             BOOLEAN DEFAULT FALSE,
    is_cafe             BOOLEAN DEFAULT FALSE,
    is_activity         BOOLEAN DEFAULT FALSE,
    is_park             BOOLEAN DEFAULT FALSE,
    is_culture          BOOLEAN DEFAULT FALSE,
    is_nightlife        BOOLEAN DEFAULT FALSE,
    is_lesson           BOOLEAN DEFAULT FALSE,
    -- 파생 태그
    is_night_friendly   BOOLEAN DEFAULT FALSE,        -- 영업시간 또는 카테고리 기반 추정
    is_group_friendly   BOOLEAN DEFAULT FALSE,        -- 카테고리 + 키워드 기반 추정
    mapping_confidence  DOUBLE PRECISION DEFAULT 1.0,
    collected_at        TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source, source_place_id)
);

CREATE INDEX idx_place_norm_region ON place_normalized(region_id);
CREATE INDEX idx_place_norm_category ON place_normalized(primary_category);
```

### 4-5. 지역 피처 — `region_feature`

```sql
CREATE TABLE region_feature (
    id                      BIGSERIAL PRIMARY KEY,
    region_id               BIGINT NOT NULL REFERENCES region_master(id),
    dataset_version         VARCHAR(50) NOT NULL,
    -- 장소 밀도 (해당 카테고리 장소 수 / 면적)
    food_density            DOUBLE PRECISION DEFAULT 0,
    cafe_density            DOUBLE PRECISION DEFAULT 0,
    activity_density        DOUBLE PRECISION DEFAULT 0,
    nightlife_density       DOUBLE PRECISION DEFAULT 0,
    lesson_density          DOUBLE PRECISION DEFAULT 0,
    -- 접근성/적합도 점수 (0~1 정규화)
    park_access_score       DOUBLE PRECISION DEFAULT 0,
    culture_score           DOUBLE PRECISION DEFAULT 0,
    night_liveliness_score  DOUBLE PRECISION DEFAULT 0,
    -- Spot 유형별 적합도 (0~1 정규화)
    casual_meetup_score     DOUBLE PRECISION DEFAULT 0,    -- 밥/카페/가볍게 만남
    lesson_spot_score       DOUBLE PRECISION DEFAULT 0,    -- 원데이클래스/공방/레슨
    solo_activity_score     DOUBLE PRECISION DEFAULT 0,    -- 혼자 할 수 있는 활동
    group_activity_score    DOUBLE PRECISION DEFAULT 0,    -- 단체 활동
    -- 보정 관련
    kakao_raw_score         DOUBLE PRECISION,              -- 카카오 데이터만으로 계산한 종합 점수
    real_data_score         DOUBLE PRECISION,              -- 실데이터만으로 계산한 종합 점수
    blended_score           DOUBLE PRECISION,              -- alpha * kakao + beta * real
    alpha_used              DOUBLE PRECISION,              -- 이 버전에서 사용한 alpha 값
    beta_used               DOUBLE PRECISION,              -- 이 버전에서 사용한 beta 값
    -- 메타
    raw_place_count         INT DEFAULT 0,
    normalized_place_count  INT DEFAULT 0,
    feature_json            JSONB,                         -- 확장용 자유 필드
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(region_id, dataset_version)
);
```

### 4-6. 실데이터 집계 — `real_activity_agg`

```sql
CREATE TABLE real_activity_agg (
    id                      BIGSERIAL PRIMARY KEY,
    region_id               BIGINT NOT NULL REFERENCES region_master(id),
    window_start            DATE NOT NULL,
    window_end              DATE NOT NULL,
    -- 기본 집계
    real_spot_count          INT DEFAULT 0,
    real_join_count          INT DEFAULT 0,
    real_completion_count    INT DEFAULT 0,
    real_cancel_count        INT DEFAULT 0,
    real_noshow_count        INT DEFAULT 0,
    -- 비율
    completion_rate          DOUBLE PRECISION,
    cancel_rate              DOUBLE PRECISION,
    noshow_rate              DOUBLE PRECISION,
    -- 카테고리별 비중
    real_food_spot_ratio     DOUBLE PRECISION DEFAULT 0,
    real_activity_spot_ratio DOUBLE PRECISION DEFAULT 0,
    real_lesson_spot_ratio   DOUBLE PRECISION DEFAULT 0,
    real_night_spot_ratio    DOUBLE PRECISION DEFAULT 0,
    -- 시간대 분포 (JSON으로 유연하게)
    time_slot_distribution   JSONB,                        -- {"FRI_19": 0.3, "SAT_14": 0.2, ...}
    -- 기타
    real_avg_group_size      DOUBLE PRECISION,
    real_hot_score           DOUBLE PRECISION,             -- 실데이터 기반 지역 인기도
    created_at               TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(region_id, window_start, window_end)
);
```

### 4-7. 페르소나-지역 가중치 — `persona_region_weight`

```sql
CREATE TABLE persona_region_weight (
    id                      BIGSERIAL PRIMARY KEY,
    dataset_version         VARCHAR(50) NOT NULL,
    persona_type            VARCHAR(50) NOT NULL,
    region_id               BIGINT NOT NULL REFERENCES region_master(id),
    affinity_score          DOUBLE PRECISION NOT NULL,     -- 종합 친화도
    create_offer_score      DOUBLE PRECISION,              -- Host로서 Offer 생성 적합도
    create_request_score    DOUBLE PRECISION,              -- Partner로서 Request 생성 적합도
    join_score              DOUBLE PRECISION,              -- 참여 적합도
    explanation_json        JSONB,                         -- 점수 근거 (디버깅용)
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(dataset_version, persona_type, region_id)
);
```

### 4-8. 최종 산출물 — `spot_seed_dataset`

```sql
CREATE TABLE spot_seed_dataset (
    id                      BIGSERIAL PRIMARY KEY,
    dataset_version         VARCHAR(50) NOT NULL,
    region_id               BIGINT NOT NULL REFERENCES region_master(id),
    spot_type               VARCHAR(50) NOT NULL,          -- casual_meetup, lesson, activity, night_social 등
    category                VARCHAR(30) NOT NULL,          -- food, cafe, activity, lesson 등
    expected_demand_score   DOUBLE PRECISION,
    expected_supply_score   DOUBLE PRECISION,
    recommended_capacity    INT,
    recommended_time_slots  JSONB,                         -- ["FRI_19", "SAT_18"]
    price_band              VARCHAR(20),                   -- low, mid, high
    final_weight            DOUBLE PRECISION NOT NULL,
    payload_json            JSONB,                         -- 실서비스에서 쓸 추가 데이터
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(dataset_version, region_id, spot_type, category)
);
```

### 4-9. 데이터셋 버전 — `dataset_version`

```sql
CREATE TABLE dataset_version (
    id                  BIGSERIAL PRIMARY KEY,
    version_name        VARCHAR(50) NOT NULL UNIQUE,       -- 예: v_20260413_001
    build_type          VARCHAR(20) NOT NULL,               -- 'full' | 'incremental'
    target_city         VARCHAR(20),                        -- 'suwon' | NULL(전국)
    built_at            TIMESTAMPTZ,
    source_window_start DATE,
    source_window_end   DATE,
    region_count        INT,
    place_count         INT,
    status              VARCHAR(20) DEFAULT 'building',     -- building → success | failed
    error_message       TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_dataset_version_status ON dataset_version(status);
```

---

## 5. 카카오 API 사용 전략

### 5-1. 사용 API

| API | 용도 | 문서 |
|-----|------|------|
| `GET /v2/local/geo/coord2regioncode` | 좌표 → 행정동 코드 변환 | [Kakao Developers](https://developers.kakao.com/docs/latest/ko/local/dev-guide#coord-to-district) |
| `GET /v2/local/search/category` | 카테고리별 장소 수집 | [Kakao Developers](https://developers.kakao.com/docs/latest/ko/local/dev-guide#search-by-category) |
| `GET /v2/local/search/keyword` | 키워드 장소 수집 | [Kakao Developers](https://developers.kakao.com/docs/latest/ko/local/dev-guide#search-by-keyword) |

### 5-2. API 제약 사항과 대응

| 제약 | 내용 | 대응 |
|------|------|------|
| 일간 쿼터 | 앱 종류에 따라 일일 호출 제한 | 수집량 계산 후 분산 실행 |
| 페이지 한계 | category search 최대 45건 (15건 × 3페이지) | 밀집 지역은 grid 분할 필수 |
| 반경 한계 | category search 반경 최대 20km | 행정동 크기 내에서 충분 |
| 인증 | REST API 키 헤더 방식 | `Authorization: KakaoAK {REST_API_KEY}` |

### 5-3. Grid 분할 전략 (핵심)

카테고리 검색은 한 번에 최대 45건만 반환한다. 장소가 밀집된 행정동에서는 결과가 잘리므로, **grid 분할은 선택이 아니라 필수**다.

**분할 판단 로직:**

```
1. 행정동 중심좌표 기준 반경 1km로 카테고리 검색 실행
2. 반환 건수가 40건 이상이면 → "밀집 지역"으로 판정
3. 밀집 지역은 bbox를 2×2 또는 3×3 grid로 분할
4. 각 grid 셀 중심좌표 기준으로 반경 축소하여 재수집
5. 재수집 후에도 40건 이상이면 한 단계 더 분할 (최대 3단계)
```

**구현: `grid_strategy.py`**

```
입력: region의 bbox
출력: 수집용 (center_lng, center_lat, radius) 리스트

기본: [(center_lng, center_lat, 1000)]   # 반경 1km
분할 시: bbox를 N×N으로 쪼갠 각 셀의 중심 + 적정 반경
```

**수원시 예상:** 수원시 행정동 대부분은 반경 1km로 충분하나, 인계동/영통동 같은 상업 밀집 지역은 2×2 이상 분할이 필요할 수 있다.

### 5-4. 카테고리 수집 세트

스팟 형성에 영향을 주는 카테고리만 수집한다.

| 카카오 category_group_code | 의미 | 수집 여부 |
|---------------------------|------|----------|
| FD6 | 음식점 | ✅ |
| CE7 | 카페 | ✅ |
| CT1 | 문화시설 | ✅ |
| AT4 | 관광명소 | ✅ |
| AC5 | 학원 | ✅ (레슨/클래스 추출용) |
| AD5 | 숙박 | ❌ |
| MT1 | 대형마트 | ❌ |
| CS2 | 편의점 | ❌ |
| PS3 | 어린이집 | ❌ |
| SC4 | 학교 | ❌ |
| BK9 | 은행 | ❌ |
| OL7 | 주유소 | ❌ |
| SW8 | 지하철역 | ❌ |
| HP8 | 병원 | ❌ |
| PK6 | 주차장 | ❌ |
| PO3 | 공공기관 | ❌ |

### 5-5. 키워드 수집 세트

카테고리 검색으로 잡히지 않는 장소를 보강한다. `{행정동명} + 키워드` 조합.

| 키워드 | 목적 |
|--------|------|
| `{emd} 맛집` | 음식점 보강 |
| `{emd} 카페` | 카페 보강 |
| `{emd} 술집` / `{emd} 바` | 야간 장소 |
| `{emd} 원데이클래스` | 레슨/체험 |
| `{emd} 공방` | 공방/체험 |
| `{emd} 운동` / `{emd} 헬스` | 스포츠/활동 |
| `{emd} 공원` / `{emd} 산책` | 공원/야외 |
| `{emd} 스터디카페` | 스터디/모임 |
| `{emd} 전시` / `{emd} 갤러리` | 문화 |

### 5-6. 수원시 수집량 추정

```
행정동 수: 약 50개
카테고리 검색: 5 카테고리 × 50 행정동 = 250 호출 (grid 분할 시 최대 ×4 = 1,000)
키워드 검색: 9 키워드 × 50 행정동 = 450 호출
합계: 약 700 ~ 1,500 호출 (풀빌드 1회)
```

일간 쿼터 내에서 1일 이내 풀빌드 가능. 호출 간 200ms sleep으로 rate limit 방어.

---

## 6. 수집 파이프라인 (STEP 1~3)

### STEP 1. 지역 마스터 생성

**구현:** `bootstrap_regions.py` + `region_master_loader.py`

**데이터 소스:**
- 행정안전부 행정표준코드관리시스템 CSV (행정동 코드, 이름, 계층)
  - URL: https://www.code.go.kr
  - 전국 행정동 전체를 적재하되, 수원시만 `is_active = TRUE`
- 중심좌표/bbox:
  - 1차: 통계청 SGIS 행정경계 좌표 데이터 활용
  - 2차: 각 행정동 코드로 카카오 coord2regioncode API 검증
  - 수동 보정이 필요한 경우 `data/region_master_suwon.csv`에 직접 입력

**실행:** 최초 1회. 이후 행정동 개편 시 수동 갱신.

**전국 확장 시:** `target_city` 컬럼 값을 추가하고 해당 도시의 `is_active`를 TRUE로 전환하면 된다.

### STEP 2. 카카오 장소 풀수집

**구현:** `full_rebuild.py`, `category_collector.py`, `keyword_collector.py`, `grid_strategy.py`

**흐름:**

```
for region in regions.filter(is_active=True, target_city='suwon'):
    # 1. grid 분할 판단
    grid_cells = grid_strategy.plan(region)

    # 2. 카테고리 수집
    for cell in grid_cells:
        for category_code in [FD6, CE7, CT1, AT4, AC5]:
            results = kakao_client.search_category(
                category_group_code=category_code,
                x=cell.lng, y=cell.lat, radius=cell.radius,
                sort='distance'
            )
            upsert place_raw_kakao (search_type='category')

    # 3. 키워드 수집
    for keyword_template in KEYWORD_SET:
        keyword = f"{region.emd} {keyword_template}"
        results = kakao_client.search_keyword(query=keyword)
        upsert place_raw_kakao (search_type='keyword', search_query=keyword)

    # 4. 수집 시각 기록
    region.last_collected_at = now()
```

**에러 처리:**
- API 429 (rate limit): exponential backoff + 재시도
- API 5xx: 3회 재시도 후 해당 region 스킵, 로그 남김
- 한 region 실패해도 다음 region 계속 진행
- 배치 완료 시 실패 region 목록 기록

**실행:** 최초 1회 전체. 이후 `incremental_refresh`에서 부분 실행.

### STEP 3. 장소 정규화

**구현:** `normalize_places.py`, `category_mapper.py`

**처리 순서:**

```
1. place_raw_kakao에서 batch_id 기준 미처리 레코드 조회

2. 중복 제거
   - 1차: source_place_id 기준 exact match (같은 장소 다른 검색으로 수집된 경우)
   - 2차: 좌표 근접(10m 이내) + 이름 유사도(Levenshtein 0.8 이상) → 수동 리뷰 큐

3. 카테고리 매핑
   - category_mapping_rule 테이블 조회
   - 매핑 우선순위: category_group_code 정확 매칭 > category_name 패턴 > keyword 패턴
   - 복수 태그 가능 (예: "카페 겸 공방" → is_cafe=True, is_lesson=True)
   - 미매핑 장소는 primary_category='other', 로그 기록

4. 파생 태그 계산
   - is_night_friendly: nightlife이거나, category_name에 '주점/바/포차' 포함
   - is_group_friendly: activity이거나, lesson이거나, category_name에 '파티/단체/모임' 포함

5. place_normalized에 upsert
```

**미매핑 모니터링:** 배치 실행 후 `primary_category = 'other'` 비율이 15% 넘으면 알림 발생. 매핑 룰 보강 필요 신호.

---

## 7. 피처 생성 파이프라인 (STEP 4)

### STEP 4. 지역 feature 생성

**구현:** `build_region_features.py`, `scoring_service.py`

**처리:**

행정동별로 아래 피처를 계산한다.

#### 밀도 계산

```
food_density     = count(is_food) / area_km2
cafe_density     = count(is_cafe) / area_km2
activity_density = count(is_activity) / area_km2
nightlife_density = count(is_nightlife) / area_km2
lesson_density   = count(is_lesson) / area_km2
```

#### 점수 계산

```
park_access_score    = min(1.0, count(is_park) / 3)       -- 공원 3개 이상이면 만점
culture_score        = min(1.0, count(is_culture) / 5)    -- 문화시설 5개 이상이면 만점
night_liveliness_score = sigmoid_normalize(nightlife_density)
```

#### Spot 유형별 적합도

```
casual_meetup_score  = weighted_avg(food_density_norm, cafe_density_norm, park_access_score)
                       weights: [0.4, 0.35, 0.25]

lesson_spot_score    = weighted_avg(lesson_density_norm, culture_score, activity_density_norm)
                       weights: [0.5, 0.3, 0.2]

solo_activity_score  = weighted_avg(cafe_density_norm, park_access_score, culture_score)
                       weights: [0.4, 0.3, 0.3]

group_activity_score = weighted_avg(activity_density_norm, food_density_norm, lesson_density_norm)
                       weights: [0.4, 0.35, 0.25]
```

#### 정규화 방법

**타겟 도시 내 상대 순위 기반 정규화 (percentile rank)를 사용한다.**

```
normalized_value = percentile_rank(raw_value, all_values_in_target_city)
```

이유:
- min-max는 극단값에 취약
- z-score는 분포 가정이 필요
- percentile rank는 해석이 직관적 ("수원시 내 상위 30% 지역")
- 도시 추가 시 도시별 독립 정규화 가능

**주의:** 수원시 50개 행정동만으로는 분포가 얇을 수 있다. 초기에는 percentile rank가 다소 거칠 수 있으나, 도시 확장 시 자연스럽게 개선된다.

---

## 8. 실데이터 결합 파이프라인 (STEP 5~6)

### STEP 5. 실유저 활동 데이터 집계

**구현:** `merge_real_data.py`

**입력:** 실서비스(Spring Boot) DB에서 읽기 전용으로 조회

**실서비스 DB 접근 규칙:**
- read-only 계정 사용
- 실행 시간: 새벽 2~5시 (야간 저점)
- statement_timeout: 30초
- 조회 범위: 최근 28일 rolling window
- 한 번에 1개 지역씩 조회 (bulk 조회 금지)

**필요한 실서비스 테이블 (Spring Boot 쪽에 존재해야 할 것):**

```
- spots (또는 동등한 모임 테이블): region 정보, category, 생성일시
- participations (참여 로그): spot_id, user_id, status, 일시
- spot_completions (완료/취소/노쇼 로그): spot_id, result, 일시
```

**집계 로직:**

```
for region in active_regions:
    지역에 속한 스팟 목록 조회 (최근 28일)
    real_spot_count = count(spots)
    real_join_count = sum(participations)
    real_completion_count = count(result='completed')
    real_cancel_count = count(result='cancelled')
    real_noshow_count = count(result='noshow')

    completion_rate = real_completion_count / real_spot_count
    cancel_rate = real_cancel_count / real_spot_count

    카테고리별 비중 계산
    시간대별 분포 계산
    평균 모집 인원 계산

    upsert real_activity_agg
```

**실데이터가 없는 초기 상태:** real_activity_agg가 비어있으면 alpha=1.0, beta=0.0으로 카카오 데이터만 사용. 자연스럽게 fallback.

### STEP 6. 지역 feature 보정

**구현:** `feature_service.py`

**보정 공식:**

```
blended_score = alpha * kakao_raw_score + beta * real_data_score
```

**alpha/beta 결정 로직:**

```python
def get_weights(region_id: int) -> tuple[float, float]:
    agg = get_real_activity_agg(region_id)

    if agg is None or agg.real_spot_count < 5:
        return (1.0, 0.0)    # 실데이터 부족 → 카카오만
    elif agg.real_spot_count < 20:
        return (0.8, 0.2)    # 초기 단계
    elif agg.real_spot_count < 50:
        return (0.6, 0.4)    # 성장 단계
    else:
        return (0.4, 0.6)    # 안정 단계 → 실데이터 우선
```

region별로 독립적으로 alpha/beta가 결정된다. 같은 도시 내에서도 활성 지역과 비활성 지역의 보정 수준이 다를 수 있다.

---

## 9. 페르소나 결합 파이프라인 (STEP 7~8)

### STEP 7. 페르소나 타입 정의

**초기 5개 페르소나 (MVP):**

| persona_type | 설명 | 핵심 선호 |
|-------------|------|----------|
| `casual_foodie` | 가볍게 밥/카페 모임 | food, cafe, 소그룹(2~4), 주말 낮 |
| `night_social` | 저녁/야간 사교 모임 | nightlife, food, 중그룹(4~8), 금/토 저녁 |
| `lesson_seeker` | 원데이클래스/배움 | lesson, culture, 소그룹(2~6), 주말 오후 |
| `solo_healing` | 혼자 또는 소수 힐링 | park, cafe, culture, 소그룹(1~3), 평일 가능 |
| `supporter_teacher` | 호스트/강사형 | lesson, activity, 다양한 규모, 주중+주말 |

**저장:** 초기에는 JSON 설정 파일(`data/persona_types.json`). 추후 실서비스 페르소나 시스템과 연동 시 DB 테이블로 전환.

### STEP 8. 페르소나-지역 친화도 계산

**구현:** `build_persona_region_weights.py`

**MVP 공식 (단순화):**

초기엔 `category_match`와 `time_match` 두 항목만으로 시작한다. 복잡한 공식은 데이터가 쌓인 후 이터레이션.

```
affinity_score = w1 * category_match + w2 * time_match
```

#### category_match 계산

```python
def category_match(persona, region_feature) -> float:
    """페르소나가 선호하는 카테고리의 지역 밀도 가중 평균"""
    score = 0
    for category, weight in persona.category_preferences.items():
        # 예: casual_foodie의 category_preferences = {"food": 0.5, "cafe": 0.4, "park": 0.1}
        region_density = getattr(region_feature, f"{category}_density_norm", 0)
        score += weight * region_density
    return score
```

#### time_match 계산

```python
def time_match(persona, real_agg) -> float:
    """페르소나 선호 시간대와 지역 실활동 시간대의 겹침 정도"""
    if real_agg is None or real_agg.time_slot_distribution is None:
        return 0.5  # 데이터 없으면 중립
    overlap = cosine_similarity(
        persona.preferred_time_slots,
        real_agg.time_slot_distribution
    )
    return overlap
```

#### 파생 점수

```
create_offer_score   = affinity_score * supply_factor(region)
create_request_score = affinity_score * demand_factor(region)
join_score           = affinity_score
```

`supply_factor`: 해당 지역에 호스트가 부족할수록 높음 (공급 필요)
`demand_factor`: 해당 지역에 참여 수요가 높을수록 높음

**향후 추가 항목 (v2 이후):**
- `budget_match_proxy`: 지역 평균 소비 수준 대비 페르소나 예산 선호 매칭 (실데이터 기반)
- `real_user_similarity`: 해당 지역 기존 활성 유저의 페르소나 분포와의 유사도 (코사인 유사도)
- `group_preference_match`: 지역 평균 모집 규모 대비 페르소나 선호 규모 매칭

---

## 10. 최종 산출물 생성 (STEP 9)

### STEP 9. 스팟 시드 데이터셋 생성

**구현:** `build_spot_weights.py`

**생성 로직:**

```
for region in active_regions:
    feature = get_region_feature(region)
    real_agg = get_real_activity_agg(region)

    for spot_type in [casual_meetup, lesson, activity, night_social, solo_healing]:
        for category in relevant_categories(spot_type):
            expected_demand = calculate_demand(feature, real_agg, spot_type, category)
            expected_supply = calculate_supply(feature, real_agg, spot_type, category)

            recommended_capacity = infer_capacity(spot_type, real_agg)
            recommended_time_slots = infer_time_slots(spot_type, real_agg)
            price_band = infer_price_band(category, region)

            final_weight = weighted_combine(expected_demand, expected_supply)

            upsert spot_seed_dataset
```

**출력 예시:**

```json
{
  "region_id": 42,
  "region_name": "영통동",
  "spot_type": "casual_meetup",
  "category": "food",
  "expected_supply_score": 0.82,
  "expected_demand_score": 0.77,
  "recommended_capacity": 4,
  "recommended_time_slots": ["FRI_19", "SAT_18", "SAT_12"],
  "price_band": "mid",
  "final_weight": 0.81
}
```

---

## 11. 배포 파이프라인 (STEP 10)

### STEP 10. Publish

**구현:** `publish_dataset.py`, `publisher_service.py`

**배포 방식: DB upsert + 버전 관리 (권장)**

```
1. dataset_version 레코드 생성 (status = 'building')
2. 최종 테이블들에 dataset_version 포함하여 insert
3. 무결성 검증:
   - 모든 active region에 대해 region_feature가 존재하는지
   - persona_region_weight가 모든 persona × region 조합에 존재하는지
   - spot_seed_dataset에 비정상 값(NaN, 음수) 없는지
4. 검증 통과 → status = 'success'
5. 검증 실패 → status = 'failed', error_message 기록, 이전 버전 유지

실서비스 조회 쿼리:
SELECT * FROM spot_seed_dataset
WHERE dataset_version = (
    SELECT version_name FROM dataset_version
    WHERE status = 'success'
    ORDER BY built_at DESC LIMIT 1
)
```

**보조 배포 방식 (선택):**
- JSON 파일 export: 디버깅/분석용
- API 제공: 실서비스가 pull하는 read-only 엔드포인트 (추후)

---

## 12. 스케줄 및 증분 업데이트

### 초기 구축 (1회성)

```
bootstrap_regions → full_rebuild → build_all_features → publish_dataset
```

### 주기 업데이트

**실행 주기:** 주 1회 야간 (일요일 새벽 2시)

```
1. incremental_refresh   — 카카오 데이터 증분 수집
2. merge_real_data        — 실유저 활동 집계 갱신
3. build_region_features  — 지역 피처 재계산
4. build_persona_region_weights — 페르소나 친화도 재계산
5. build_spot_weights     — 스팟 시드 재계산
6. publish_dataset        — 배포
```

### 증분 갱신 전략

#### A. 카카오 데이터

| 지역 유형 | 갱신 주기 | 판단 기준 |
|----------|----------|----------|
| 활성 지역 (실데이터 있음) | 7일 | `last_collected_at` + 7일 초과 |
| 비활성 지역 | 30일 | `last_collected_at` + 30일 초과 |
| 신규 활성화 지역 | 즉시 | `is_active = TRUE` AND `last_collected_at IS NULL` |

#### B. 실데이터

- 최근 28일 rolling window 전체 재집계
- 집계 대상: `is_active = TRUE`인 지역 전체

#### C. 피처 및 가중치

**전체 재계산을 기본으로 한다.**

수원시 50개 행정동 × 5개 페르소나 = 250건의 가중치 계산이라 부분 재계산의 복잡도 대비 이점이 없다. 전국 확장 시(3,500+ 행정동) 부분 재계산 전환을 검토한다.

---

## 13. 모니터링 및 알림

### 배치 실행 모니터링

| 항목 | 임계치 | 알림 |
|------|--------|------|
| 배치 실행 시간 초과 | 풀빌드 > 2시간, 증분 > 30분 | Slack 알림 |
| 카카오 API 에러율 | > 10% | Slack 알림 |
| 수집 장소 수 급감 | 이전 대비 30% 이상 감소 | Slack 알림 |
| 미매핑 카테고리 비율 | > 15% | Slack 알림 |
| dataset publish 실패 | status = 'failed' | Slack 알림 즉시 |
| 카카오 API 쿼터 잔여 | < 20% | 사전 경고 |

### 데이터 품질 체크 (publish 전)

```
1. 모든 active region에 region_feature 존재?
2. 모든 region_feature의 raw_place_count > 0?
3. persona_region_weight에 NaN 없음?
4. spot_seed_dataset의 final_weight가 0~1 범위?
5. 이전 버전 대비 region_feature 값 급변(>50%) 지역 목록 로깅
```

### 구현

- 초기: 로그 파일 + 간단한 Slack webhook
- 확장 시: Prometheus + Grafana

---

## 14. 관리 API

### 엔드포인트

```
POST /admin/bootstrap
  - 지역 마스터 초기 구축
  - params: target_city (default: 'suwon')

POST /admin/full-rebuild
  - 전체 지역 풀빌드
  - params: target_city (default: 'suwon')

POST /admin/incremental-refresh
  - 증분 갱신 실행
  - params: target_city, force (default: false)

POST /admin/build-features
  - 피처 + 가중치 재계산만 실행 (수집 없이)

POST /admin/publish
  - 최신 결과 publish

GET /admin/status
  - 현재 실행 중인 배치 상태

GET /admin/dataset/latest
  - 최신 성공 버전 조회

GET /admin/dataset/versions
  - 버전 히스토리

GET /admin/region/{region_id}
  - 특정 지역 feature 상세

GET /admin/region/{region_id}/places
  - 특정 지역 정규화 장소 목록

GET /admin/persona-region/{persona_type}/{region_id}
  - 특정 페르소나-지역 가중치 상세

GET /admin/health
  - 서비스 헬스체크

GET /admin/metrics
  - 수집 통계 (지역별 장소 수, 마지막 수집 시각 등)
```

### 인증

- 초기: 단순 API key 헤더 (`X-Admin-Key`)
- 확장 시: 실서비스 인증 시스템 연동

---

## 15. 최소 구현 범위 (MVP)

첫 이터레이션에서 구현할 범위.

### 수집

- 수원시 행정동 (약 50개)
- 카테고리 검색: FD6, CE7, CT1, AT4, AC5
- 키워드 검색: 맛집, 카페, 원데이클래스, 공방, 운동 (5개)
- grid 분할: 2×2 고정 (밀집 판정 시)

### 저장

- `region_master`, `place_raw_kakao`, `place_normalized`, `region_feature`
- `category_mapping_rule` (시드 데이터 10~15개 규칙)

### 피처

- 밀도 5종 + 점수 3종 + Spot 적합도 4종
- 정규화: 수원시 내 percentile rank

### 페르소나

- 5개 타입 JSON 설정
- 친화도 계산: category_match만 (time_match는 실데이터 필요)

### 출력

- `persona_region_weight` (5 × 50 = 250건)
- `spot_seed_dataset` (50 × 4~5 spot_type × 2~3 category ≈ 500~750건)

### 미포함 (v2 이후)

- 실유저 활동 데이터 결합 (merge_real_data)
- alpha/beta 보정
- time_match, budget_match_proxy
- 증분 갱신 스케줄러
- 모니터링/알림 자동화

---

## 16. 구현 일정

### 1주차: 프로젝트 셋업 + 지역 마스터

- [ ] FastAPI + PostgreSQL + Docker Compose 구성
- [ ] Alembic 마이그레이션 세팅
- [ ] 환경변수/시크릿 구성 (.env)
- [ ] `region_master` DDL + 모델
- [ ] 수원시 행정동 마스터 데이터 수집 및 적재
- [ ] 중심좌표/bbox 확보 (통계청 데이터 또는 수동)
- [ ] `GET /admin/health`, `GET /admin/region/{id}` 구현

### 2주차: 카카오 수집기

- [ ] `kakao_local_client.py` 구현 (인증, rate limit, retry, 에러 핸들링)
- [ ] `category_collector.py` 구현
- [ ] `keyword_collector.py` 구현
- [ ] `grid_strategy.py` 구현 (밀집 판정 + 분할)
- [ ] `place_raw_kakao` DDL + 모델
- [ ] `full_rebuild.py` 잡 구현
- [ ] `POST /admin/full-rebuild` 연결
- [ ] 수원시 풀수집 테스트 실행

### 3주차: 정규화 + 피처 생성

- [ ] `category_mapping_rule` DDL + 시드 데이터 적재
- [ ] `category_mapper.py` 구현
- [ ] `normalize_places.py` 구현 (중복 제거 + 카테고리 매핑)
- [ ] `place_normalized` DDL + 모델
- [ ] 미매핑 비율 확인 → 매핑 룰 보강
- [ ] `scoring_service.py` 구현 (percentile rank 정규화)
- [ ] `build_region_features.py` 구현
- [ ] `region_feature` DDL + 모델
- [ ] 수원시 전체 피처 생성 및 검증

### 4주차: 페르소나 + 최종 산출물

- [ ] 페르소나 타입 5개 JSON 정의
- [ ] `build_persona_region_weights.py` 구현 (category_match만)
- [ ] `persona_region_weight` DDL + 모델
- [ ] `build_spot_weights.py` 구현
- [ ] `spot_seed_dataset` DDL + 모델
- [ ] `dataset_version` DDL + 모델
- [ ] `publish_dataset.py` 구현 (검증 + status 관리)
- [ ] 수원시 전체 파이프라인 end-to-end 실행

### 5주차: 실데이터 연동 + 보정 (v1.1)

- [ ] 실서비스 DB read-only 연결 구성
- [ ] `merge_real_data.py` 구현
- [ ] `real_activity_agg` DDL + 모델
- [ ] `feature_service.py` alpha/beta 보정 구현
- [ ] time_match 추가
- [ ] 실데이터 반영된 재계산 테스트

### 6주차: 자동화 + 운영

- [ ] Celery 워커 구성
- [ ] APScheduler 주기 실행 설정
- [ ] `incremental_refresh.py` 구현
- [ ] 모니터링: 수집 통계 로깅
- [ ] 알림: Slack webhook 연동
- [ ] publish 전 데이터 품질 체크 자동화
- [ ] 관리 API 전체 연결 확인
- [ ] 운영 문서 작성

---

## 17. 운영 규칙

### API 격리

- 카카오 API는 `local-context-builder`만 호출한다
- 실서비스(Spring Boot)는 카카오 API를 직접 호출하지 않는다
- 실서비스는 최종 산출물 테이블만 조회한다

### 데이터셋 안전성

- 새 dataset은 `status = 'success'`일 때만 active 전환
- publish 실패 시 이전 성공 버전 유지
- 이전 성공 버전 최소 3개 보관 (롤백 대비)

### 시크릿 관리

- 카카오 REST API 키: 배치 서비스만 보유
- 실서비스 DB 접속 정보: 배치 서비스 환경변수
- 관리 API 키: 별도 환경변수
- `.env` 파일은 git에 커밋하지 않음

### 전국 확장 체크리스트

1. `region_master`에 대상 도시 행정동 적재
2. `target_city` 값 설정, `is_active = TRUE` 전환
3. 카카오 API 쿼터 확인 (도시당 예상 호출량 계산)
4. `full_rebuild` 실행 (target_city 파라미터)
5. 정규화는 도시별 독립 정규화 (percentile rank 기준 풀이 도시 단위)
6. 카카오 API 쿼터 부족 시 앱 추가 또는 수집 기간 분산

---

## 18. 최종 플로우 요약

```
[행정안전부 행정동 코드 CSV]
    ↓
[region_master 적재] ← 수원시만 active
    ↓
[카카오 category/keyword 수집] ← grid 분할 포함
    ↓
[place_raw_kakao 저장]
    ↓
[정규화 + 중복 제거 + 카테고리 매핑] ← category_mapping_rule 참조
    ↓
[place_normalized]
    ↓
[region_feature 생성] ← percentile rank 정규화
    ↓
[실유저 활동 집계 병합] ← alpha/beta 보정 (실데이터 있을 때)
    ↓
[페르소나-지역 가중치 계산] ← MVP: category_match만
    ↓
[spot_seed_dataset 생성]
    ↓
[데이터 품질 검증]
    ↓
[dataset_version publish (status='success')]
    ↓
[실서비스는 latest success version만 조회]
```
