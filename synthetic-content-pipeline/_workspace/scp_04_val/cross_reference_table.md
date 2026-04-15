# Layer 3 Cross-Reference Table (Phase 2)

> 출처: `synthetic_content_pipeline_plan.md` §5 Layer 3 표
> 소유: `validator-engineer`
> 모듈: `src/pipeline/validators/cross_reference.py`
> 설정: `config/rules/cross_reference.yaml`

한 스팟(spot_id)의 feed / detail / plan / messages / review 가 서로 모순되지
않는지 deterministic 하게 검증한다. LLM 호출 없음. 5 쌍으로 나누어 독립 함수
(`_pair_*`) 를 실행하고 하나의 `ValidationResult(layer="cross_ref")` 로 합친다.

`rejected_field` 는 반드시 `"<pair>:<sub_field>"` 또는 `"<content_type>:<field>"`
형태로 작성한다 — 재시도 루프가 어느 content 를 regenerate 해야 할지 식별하기
위함이다.

## 1. Pair 매핑

| # | Pair | 함수 | 체크 항목 | 파라미터 |
|---|---|---|---|---|
| 1 | feed ↔ detail | `_pair_feed_detail` | 금액 / 지역 / 카테고리 / supporter 라벨 | `price_tolerance_low/high`, `fuzzy_threshold`, `category_keywords`, `min_category_keyword_hits` |
| 2 | detail ↔ plan | `_pair_detail_plan` | 카테고리 활동 어휘 교집합 / materials 사용 | `category_keywords` (+ materials 약한 경고) |
| 3 | detail ↔ review | `_pair_detail_review` | review 에 다른 카테고리 키워드만 주로 등장하는지 | `category_keywords` |
| 4 | feed ↔ messages | `_pair_feed_messages` | 모집 상태 / 시각 정합 | 모집 어휘 내장, `time_tolerance_minutes` |
| 5 | review ↔ activity_result | `_pair_review_activity_result` | 노쇼 vs 전원 표현 / sentiment 정합 | `forbidden_unanimous_terms`, spec.activity_result |

## 2. Reject 조건 1줄 요약 (5쌍)

1. **feed ↔ detail** — feed.price_label 숫자와 detail.cost_breakdown 합계 격차가 `expected_cost × 0.5` 초과 OR detail 합계가 `[expected×0.7, expected×1.5]` 밖 OR feed.region_label 마지막 토큰이 detail 본문 fuzzy ratio < 70 OR detail 본문에 spec.category 대표 키워드 0개 OR feed.supporter_label 이 detail.host_intro 에 등장하지 않음(fuzzy < 70).
2. **detail ↔ plan** — detail 본문에 등장한 spec.category 대표 키워드 중 plan.steps 에 **반영된 단어 0개** → reject. detail.materials 전체가 plan 에 등장하지 않으면 → **warn** (약한 경고).
3. **detail ↔ review** — review_text 에 다른 카테고리 키워드가 2개 이상 검출되고 spec.category 대표 키워드가 0개일 때 reject (활동 종류 모순).
4. **feed ↔ messages** — feed.status=recruiting 인데 recruiting_intro 에 모집 어휘 0개 OR feed.time_label 시각과 messages.day_of_notice 시각 최소 차이가 `time_tolerance_minutes` (기본 30분) 초과.
5. **review ↔ activity_result** — `spec.activity_result.no_show_count > 0` 인데 review_text 에 "전원/모두/빠짐없이/모든 참가자/한 명도 빠지지" 포함 OR `activity_result.overall_sentiment` 가 positive/negative 일 때 review.sentiment 가 정반대 값.

## 3. Skip 동작

- `spot_bundle` 에 일부 content 가 없으면 해당 pair 는 `skipped_pairs` 에 기록되고 reject 를 내지 않는다.
- Pair 5 (review ↔ activity_result) 는 `spec.activity_result is None` 이면 함수 내부에서 skip (recruiting 상태이므로).
- Pair 1~4 는 키 존재 여부로 판단한다 (키가 있지만 payload 가 깨진 경우는 개별 Layer 1 에서 걸러진 후 이곳에 도달한다고 가정).

## 4. 금액 파싱 (feed ↔ detail)

- `_parse_price_numbers(label)` — rules.py 와 동일 정규식 (`만원/천원/원` 단위 인식).
- `feed.price_label="1인 1.5~2만원"` → `[15000, 20000]`. 두 값의 평균(17500)을 **feed_center** 로 사용.
- `detail.cost_breakdown[].amount` 합계와 비교해 `|feed_center − total| > expected × 0.5` 이면 reject.
- 동시에 `total` 자체가 expected 기준 tolerance 밖이면 `detail:cost_breakdown` 별도 reject (두 rejection 이 나란히 뜰 수 있음 — loop 는 detail 하나만 regen 하면 두 문제를 동시에 해결).

## 5. 시각 파싱 (feed ↔ messages)

`_parse_time_minutes` 는 두 패턴을 지원:

- `HH:MM` — `19:00` → `[1140]`
- `(오전|오후|아침|점심|저녁|밤|새벽)? N시` — `저녁 7시` → `[1140]` (저녁/밤/오후 prefix → hh+12)

feed.time_label 과 day_of_notice 양쪽에서 추출한 모든 (hh×60+mm) 의 **최소 절대값** 이
`time_tolerance_minutes` 이내여야 통과.

## 6. 카테고리 매핑 (공유)

`cross_reference.yaml::category_keywords` 는 다음 6 카테고리를 커버:

| category | 대표 키워드 샘플 |
|---|---|
| food | 식사, 밥, 먹, 맛, 저녁, 점심, 브런치, 한 끼 |
| cafe | 카페, 커피, 디저트, 빵, 차 |
| exercise | 운동, 러닝, 걷, 등산, 요가, 스트레칭, 헬스 |
| nature | 산책, 공원, 자연, 숲, 나들이, 등산 |
| bar | 술, 와인, 맥주, 칵테일, 바 |
| culture | 전시, 공연, 영화, 책, 문화, 그림, 드로잉, 스케치, 미술 |

이 매핑은 `rules.py::rule_category_consistency` (feed 개별 rule) 의 **deny** 키워드와
역할이 다르다 — cross-ref 는 **allow** 키워드 히트 수로 "활동 종류 모순" 을 잡는다.

## 7. 재시도 루프 연결 (Phase 3 예정)

Phase 3 `loop/generate_validate_retry.py` 가 이 table 의 rejection 을 parse 해 regen 대상을 결정한다. 예:

- `feed↔detail:price` → **detail** regenerate (feed 는 spec 일치 확인 후 유지)
- `feed↔detail:region` → **detail** regenerate
- `feed↔messages:time` → **messages** regenerate
- `detail↔review:activity_kind` → **review** regenerate
- `review↔activity_result:noshow` → **review** regenerate

규칙: pair 가 `A↔B` 일 때 기본 regen 대상은 **B** (뒤쪽). sentiment/cost 같이
"feed 가 진실" 인 경우가 많기 때문. 예외는 명시적으로 rejection.instruction 에
기록한다.
