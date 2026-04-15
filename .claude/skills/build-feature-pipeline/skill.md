---
name: build-feature-pipeline
description: 원본 장소 데이터를 정규화하고 지역 피처 벡터·페르소나-지역 가중치·스팟 시드 데이터셋으로 변환하고 dataset_version 기반으로 publish하는 파이프라인을 구현하는 스킬. percentile rank 정규화, alpha/beta 실데이터 보정, 미매핑 모니터링, publish 검증까지 포함. local-context-builder의 normalize_places / build_region_features / build_persona_region_weights / build_spot_weights / publish_dataset을 구현할 때 반드시 이 스킬을 사용할 것.
---

# build-feature-pipeline

플랜 §6 STEP 3 ~ §11 STEP 10을 구현하는 스킬. `processor-engineer` 에이전트가 사용한다.

## 언제 사용하는가

- `place_raw_kakao` → `place_normalized` 변환 로직을 구현할 때
- 행정동 단위 피처 벡터(`region_feature`)를 생성할 때
- 페르소나별 지역 친화도(`persona_region_weight`)를 계산할 때
- 최종 `spot_seed_dataset`을 만들고 `dataset_version`으로 publish할 때
- 실서비스 DB에서 활동 집계를 읽어 alpha/beta 보정을 적용할 때

## 파이프라인 체인

```
place_raw_kakao
   ↓ normalize_places + category_mapper
place_normalized
   ↓ build_region_features (+ scoring_service.percentile_rank)
region_feature (per target_city normalized)
   ↓ merge_real_data (v1.1, optional)
real_activity_agg + feature_service.get_weights(region)
   ↓ build_persona_region_weights
persona_region_weight
   ↓ build_spot_weights
spot_seed_dataset
   ↓ publish_dataset (+ publisher_service verify)
dataset_version(status=success)
```

## STEP 3: 정규화

### 중복 제거 2단계

1. **1차**: `source_place_id` exact match. 같은 장소가 다른 검색(카테고리/키워드)으로 수집된 경우. 첫 레코드 유지, 나머지 무시
2. **2차**: 좌표 10m 이내 + 이름 Levenshtein 0.8 이상 → `duplicate_review_queue` 또는 로그에만 기록 (MVP는 자동 병합 금지)

Levenshtein 거리는 `python-Levenshtein` 또는 `difflib.SequenceMatcher`로.

### 카테고리 매핑

**`category_mapping_rule` 테이블 조회로만 결정한다 — 코드에 하드코딩 금지.**

```python
# app/processors/category_mapper.py
def map_place(place_raw: PlaceRawKakao, rules: list[CategoryMappingRule]) -> tuple[str, dict[str, bool]]:
    tags: dict[str, bool] = {}
    matched = []

    for rule in rules:
        if rule.kakao_category_group_code and rule.kakao_category_group_code == place_raw.category_group_code:
            matched.append(rule)
            continue
        if rule.kakao_category_pattern and _like(place_raw.category_name, rule.kakao_category_pattern):
            matched.append(rule)
            continue
        if rule.keyword_pattern and _like(place_raw.search_query, rule.keyword_pattern):
            matched.append(rule)
            continue

    for rule in sorted(matched, key=lambda r: -r.priority):
        tags[f"is_{rule.internal_tag}"] = True

    primary = _primary_from_tags(tags) or "other"
    return primary, tags
```

`_like`는 `%foo%` 스타일 패턴을 Python 정규식 또는 `in` 연산으로 변환.

### 파생 태그

```python
is_night_friendly = tags.get("is_nightlife") or _contains_any(category_name, ["주점", "바", "포차"])
is_group_friendly = tags.get("is_activity") or tags.get("is_lesson") or _contains_any(category_name, ["파티", "단체", "모임"])
```

### 미매핑 모니터링

배치 종료 시:
```python
other_rate = other_count / total_count
if other_rate > 0.15:
    logger.warning("unmapped rate exceeds threshold: %.1f%%", other_rate * 100)
```

integration-qa가 alerts.py에서 이 로그를 픽업할 수 있도록 이벤트 구조로 리턴도 함께.

## STEP 4: 지역 피처

### 정규화: percentile rank

```python
# app/services/scoring_service.py
import numpy as np

def percentile_rank(values: list[float]) -> list[float]:
    """타겟 도시 전체 지역의 값 배열을 받아 각 원소의 percentile rank(0~1)를 리턴."""
    if not values:
        return []
    arr = np.asarray(values, dtype=float)
    order = arr.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(arr)) / max(len(arr) - 1, 1)
    return ranks.tolist()

def sigmoid_normalize(x: float, midpoint: float = 1.0, steepness: float = 4.0) -> float:
    import math
    return 1 / (1 + math.exp(-steepness * (x - midpoint)))
```

**주의**: percentile rank는 타겟 도시 내 상대 값이다. `target_city` 필터로 region을 선별한 뒤 한 번에 계산해야 한다. region 단위로 따로 계산하면 0이 나온다.

### 피처 계산 순서

1. 각 region의 raw density (count/area_km2)를 계산
2. 타겟 도시 내 모든 region의 density를 모아 `percentile_rank` 적용 → `*_density_norm`
3. park_access_score, culture_score는 count 기반 clip
4. night_liveliness_score는 sigmoid_normalize(nightlife_density)
5. Spot 적합도 점수는 plan §7의 weighted_avg 공식
6. `raw_place_count`, `normalized_place_count`도 반드시 채움

### 컬럼 체크

`region_feature`의 모든 density/score/spot 적합도 컬럼을 누락 없이 채운다. `dataset_version`도 반드시.

## STEP 5~6: 실데이터 결합 (v1.1)

MVP는 건너뛰어도 되지만 인터페이스는 만들어 둔다.

```python
# app/services/feature_service.py
def get_weights(real_agg: RealActivityAgg | None) -> tuple[float, float]:
    if real_agg is None or real_agg.real_spot_count < 5:
        return (1.0, 0.0)
    if real_agg.real_spot_count < 20:
        return (0.8, 0.2)
    if real_agg.real_spot_count < 50:
        return (0.6, 0.4)
    return (0.4, 0.6)
```

실데이터 조회는 `app/db_readonly.py` 엔진을 사용. **절대 쓰기 금지**, `SET statement_timeout = 30000`를 커넥션 옵션으로.

## STEP 7~8: 페르소나 가중치

`data/persona_types.json`:
```json
[
  {
    "persona_type": "casual_foodie",
    "category_preferences": {"food": 0.5, "cafe": 0.4, "park": 0.1},
    "preferred_time_slots": {"SAT_12": 0.3, "SAT_14": 0.3, "SUN_12": 0.2, "FRI_19": 0.2}
  },
  ...
]
```

MVP 공식:
```python
affinity = category_match(persona, feature)  # w1=1.0, time_match 제외
create_offer_score   = affinity * supply_factor
create_request_score = affinity * demand_factor
join_score           = affinity
```

`supply_factor`, `demand_factor`는 MVP에서 1.0 고정. v1.1부터 실데이터 기반.

## STEP 9: 스팟 시드

```python
SPOT_TYPES = {
    "casual_meetup": ["food", "cafe"],
    "lesson":        ["lesson", "culture"],
    "activity":      ["activity", "park"],
    "night_social":  ["nightlife", "food"],
    "solo_healing":  ["cafe", "park", "culture"],
}
```

`final_weight`는 `weighted_combine(expected_demand, expected_supply)`. 0~1 클램프.

## STEP 10: Publish

```python
# app/services/publisher_service.py
def publish_dataset(db, version_name: str, target_city: str):
    version = DatasetVersion(version_name=version_name, build_type="full", target_city=target_city, status="building")
    db.add(version); db.flush()

    try:
        _verify_quality(db, version, target_city)
        version.status = "success"
        version.built_at = datetime.utcnow()
    except QualityError as e:
        version.status = "failed"
        version.error_message = str(e)
        db.commit()
        raise

    db.commit()
```

`_verify_quality` 검증 항목:
1. 모든 `is_active=True` region에 대해 `region_feature` 존재
2. 모든 `region_feature`의 `raw_place_count > 0`
3. `persona_region_weight`에 NaN/∞ 없음
4. `spot_seed_dataset.final_weight ∈ [0, 1]`
5. 이전 성공 버전 대비 값 급변(>50%) region 로깅 (경고만)

### 이전 버전 보관

`dataset_version`에서 `status='success'`인 최신 3개만 유지할 필요는 없다 — 보관이 저렴. 삭제 로직은 만들지 말고, 실서비스 조회는 **가장 최신 success만** 바라보게 한다.

## 체크리스트

- [ ] `category_mapper`가 DB 테이블(category_mapping_rule)만 참조, 하드코딩 없음
- [ ] `normalize_places`가 복수 태그 설정 가능 (is_cafe + is_lesson)
- [ ] 파생 태그 2개(`is_night_friendly`, `is_group_friendly`) 구현
- [ ] `percentile_rank`가 타겟 도시 전체 값 배열을 받음
- [ ] `region_feature`의 density 5 + score 3 + spot 적합도 4 전부 채움
- [ ] `feature_service.get_weights()`가 real_spot_count 임계값 4단계 처리
- [ ] `persona_types.json`에 5개 페르소나 정의
- [ ] `persona_region_weight`에 `dataset_version` 채움
- [ ] `publish_dataset`이 status 전이(building→success/failed)를 구현
- [ ] quality 검증 5개 항목 구현
- [ ] `_workspace/04_processor/dataflow.md` 작성

## 안티패턴

- `category_mapping_rule`을 무시하고 코드에 `if category_group_code == "FD6"` 류 하드코딩
- region 단위로 percentile_rank를 계산 (배열이 길이 1이라 항상 0)
- `place_normalized`에 `primary_category`만 설정하고 bool 태그를 비워둠 → 이후 피처 계산 시 `is_food=False`로 전부 집계
- publish 실패 시 이전 성공 버전의 `status`를 건드림
- NaN/∞를 그대로 저장 → quality 검증 fail
- 실서비스 DB에 쓰기 쿼리 실행
