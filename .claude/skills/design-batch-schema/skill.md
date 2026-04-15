---
name: design-batch-schema
description: SQL DDL 사양서를 SQLAlchemy 2.x 모델과 Alembic 마이그레이션으로 정확히 변환하고 시드 데이터를 준비하는 스킬. 타입·제약·인덱스를 누락 없이 옮기고 다른 에이전트가 참조할 컬럼 계약서까지 생성한다. local-context-builder의 9개 테이블(region_master, place_raw_kakao, place_normalized, category_mapping_rule, region_feature, real_activity_agg, persona_region_weight, spot_seed_dataset, dataset_version)을 구현할 때 반드시 이 스킬을 사용할 것.
---

# design-batch-schema

DDL 사양서를 **하나도 빠뜨리지 않고** SQLAlchemy 2.x 모델과 Alembic 마이그레이션으로 옮기는 스킬. `schema-designer` 에이전트가 사용한다.

## 언제 사용하는가

- 플랜에 SQL DDL이 명시되어 있고 그걸 ORM 레이어로 옮겨야 할 때
- 여러 테이블 간 FK/UNIQUE/INDEX 관계를 보존해야 할 때
- 다른 에이전트(수집·처리·QA)가 컬럼 계약서를 필요로 할 때

## 핵심 원칙

**DDL이 진실의 원천이다.** 모델은 DDL을 따르고, 마이그레이션은 모델을 따른다. 충돌이 발생하면 역방향 동기화 금지 — 계획자(사람)에게 문의.

## 워크플로우

### 1단계: DDL 인벤토리 추출

플랜에서 모든 `CREATE TABLE` / `CREATE INDEX` 블록을 뽑아 표로 정리.

| 테이블명 | 컬럼 수 | 제약 | 인덱스 | 연결 |
|---|---|---|---|---|
| region_master | 14 | PK, UNIQUE(region_code) | 3개 | (없음) |
| place_raw_kakao | 17 | PK, UNIQUE(source_place_id, region_id) | 3개 | FK region_id |
| ... |

이 표를 `_workspace/02_schema/model_index.md`에 저장한다. 나중에 QA가 사용한다.

### 2단계: 타입 매핑 규칙

| SQL 타입 | SQLAlchemy 2.x 타입 |
|---|---|
| `BIGSERIAL PRIMARY KEY` | `Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)` |
| `SERIAL` | `Integer, primary_key=True, autoincrement=True` |
| `VARCHAR(N)` | `String(N)` |
| `TEXT` | `Text` |
| `DOUBLE PRECISION` | `Float` (정밀도가 중요하면 `Numeric`) |
| `BOOLEAN` | `Boolean` |
| `JSONB` | `from sqlalchemy.dialects.postgresql import JSONB` — `JSONB` |
| `DATE` | `Date` |
| `TIMESTAMPTZ` | `DateTime(timezone=True)` |
| `DEFAULT NOW()` | `server_default=func.now()` |
| `DEFAULT TRUE/FALSE` | `server_default=text("true"/"false")` |
| `SMALLINT` | `SmallInteger` |
| `NOT NULL` | `nullable=False` |
| `UNIQUE(a, b)` | `__table_args__ = (UniqueConstraint("a", "b"),)` |

### 3단계: 모델 파일 작성 패턴

```python
# app/models/region.py
from __future__ import annotations
from datetime import datetime
from sqlalchemy import BigInteger, String, Float, Boolean, SmallInteger, DateTime, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class RegionMaster(Base):
    __tablename__ = "region_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    region_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    sido: Mapped[str] = mapped_column(String(20), nullable=False)
    # ... 모든 컬럼

    __table_args__ = (
        Index("idx_region_target_city", "target_city"),
        Index("idx_region_active", "is_active"),
        Index("idx_region_last_collected", "last_collected_at"),
    )
```

**인덱스명은 DDL과 정확히 일치**시킨다. 마이그레이션 diff가 0이어야 한다.

### 4단계: FK와 relationship

FK는 반드시 선언하되 `relationship()`은 **실제 사용처가 있을 때만** 추가한다. 과잉 join의 원인이 된다.

```python
region_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("region_master.id"), nullable=False)
```

### 5단계: 단일 Alembic 마이그레이션

`migrations/versions/0001_initial.py`에 9개 테이블 전부를 `op.create_table(...)`로 정의. `autogenerate`를 쓰되 **결과 diff를 꼼꼼히 리뷰**하고 인덱스명/제약명이 DDL과 일치하는지 확인.

autogenerate가 부족하면 수동 작성. 순서:
1. `region_master` (다른 테이블의 FK 대상)
2. `category_mapping_rule` (독립)
3. `place_raw_kakao`, `place_normalized` (region 참조)
4. `region_feature`, `real_activity_agg`, `persona_region_weight`, `spot_seed_dataset` (region 참조)
5. `dataset_version` (독립)

downgrade는 역순.

### 6단계: 시드 데이터

- `scripts/load_region_master.py` — CSV 읽어서 `region_master`에 upsert. 수원시만 `is_active=True`, `target_city='suwon'`
- `scripts/load_category_mapping.py` — JSON 읽어서 `category_mapping_rule`에 upsert
- `data/region_master_suwon.csv` — 헤더: `region_code,sido,sigungu,emd,center_lng,center_lat,bbox_min_lng,bbox_min_lat,bbox_max_lng,bbox_max_lat,area_km2`. 수원시 약 50개 행정동 코드는 확보 가능한 만큼 채우고, 좌표는 비워두어도 된다 (TBD 명시)
- `data/category_mapping_seed.json` — 플랜의 초기 매핑 표 10~15개를 JSON 배열로

JSON 배열 예시 구조:
```json
[
  {"kakao_category_group_code": "FD6", "internal_tag": "food", "priority": 10, "notes": "음식점 전체"},
  {"kakao_category_pattern": "%주점%", "internal_tag": "nightlife", "priority": 8}
]
```

### 7단계: 컬럼 계약서 생성

`_workspace/02_schema/column_contract.md` — 다른 에이전트가 신뢰할 수 있는 문서. 테이블별로:

```markdown
## place_raw_kakao

| 컬럼 | 타입 | NULL | 쓰기 책임 | 읽기 책임 |
|---|---|---|---|---|
| source_place_id | VARCHAR(30) | NO | collector | processor |
| search_type | VARCHAR(20) | NO | collector ('category'|'keyword') | processor |
| raw_json | JSONB | YES | collector | processor(옵션) |
| batch_id | VARCHAR(50) | YES | collector | qa |
```

"쓰기 책임"과 "읽기 책임"을 명시하면 collector/processor가 자기 범위를 명확히 알 수 있다.

## 체크리스트

- [ ] 9개 테이블 전부 모델 파일 존재
- [ ] 모든 DDL 컬럼이 모델에 존재 (역도)
- [ ] 모든 DDL 인덱스가 마이그레이션에 존재 (역도)
- [ ] 타입 매핑 테이블대로 변환
- [ ] 초기 마이그레이션이 `alembic upgrade head`로 적용됨 (SQLite이 아닌 PostgreSQL 대상)
- [ ] `downgrade`로 되돌아감
- [ ] `data/` 시드 파일 존재
- [ ] `load_region_master.py`가 수원시 행정동만 active로 처리
- [ ] `_workspace/02_schema/model_index.md`와 `column_contract.md` 작성

## 안티패턴

- `Text`로 `VARCHAR(N)`을 뭉개기 — 제약이 사라진다
- `DateTime`에 `timezone=False` — TIMESTAMPTZ는 반드시 tz-aware
- relationship을 자동 생성하면서 `lazy="joined"` 기본 사용 — 배치에서 N+1 성능 폭발
- DDL에 없는 컬럼 추가 (예: "편의상 created_by 넣어둘게요")
- JSONB 컬럼에 `Text`를 씀 — jsonb 연산자 사용 불가
