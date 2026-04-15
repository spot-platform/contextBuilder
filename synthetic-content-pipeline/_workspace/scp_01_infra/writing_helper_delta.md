# Writing Helper Delta — Cold Start Reference Assets

작업 마크: `scp_01_infra_peer_writing_helper_complete`
작업일: 2026-04-15

이 문서는 앱의 **cold start 사용자 입력 플로우** 에 쓰이는 두 가지 reference
자료 — `fee_reference.yaml`, `plan_library.yaml` — 의 생성 규칙, 스키마, 그리고
앱이 이것들을 어떻게 consume 하는지를 기록한다.

**중요**: 이 두 yaml 은 피드 콘텐츠가 아니다. synthetic content pipeline 의
generator/validator/publisher 어느 단계도 이 파일을 읽지 않는다. 오로지 앱
프론트/서비스 레이어가 "이 스킬 얼마 받아요?", "어떻게 진행해요?" 질문에
답할 때 참조한다.

---

## 1. 산출물

| 파일 | 생성 방식 | 소스 | entry 수 |
|------|----------|------|---------|
| `config/templates/fee_reference.yaml` | `scripts/build_fee_reference.py` 자동 집계 | `../spot-simulator/output/event_log.jsonl` 의 CREATE_TEACH_SPOT | 29 |
| `config/templates/plan_library.yaml` | `scripts/build_plan_library.py` 수작업 큐레이션 (상수 ENTRIES) | 큐레이터 직접 작성 | 33 |

---

## 2. fee_reference.yaml — 생성 알고리즘

### 입력
- `../spot-simulator/output/event_log.jsonl` (15,707 events, 863 CREATE_TEACH_SPOT 중 703 개가 `fee_breakdown` 포함, 160 개는 `request_matched` 경로로 aggregate fee 만 있음)

### 집계 단계
1. event_log 스캔 → `event_type == CREATE_TEACH_SPOT` 만 수집
2. `(skill, teach_mode, venue_type)` 키로 그룹핑 → 총 29 그룹
3. 각 그룹에서 수집하는 필드:
   - `total_fees[]` — `fee_breakdown.total` 또는 `payload.fee`
   - `peer_labor_fees[]` — `fee_breakdown.peer_labor_fee` (순수 노동료)
   - `material_costs[]`, `venue_rentals[]`, `equipment_rentals[]` — 실비 3종
   - `by_level[host_skill_level]` — 레벨별 total 분포
4. percentile 계산: nearest-rank 기법 (`_percentile` 함수). 소량 샘플도 일관.
5. 레벨별 `median` + `sample_count` 제공 (레벨 데이터 있을 때만)
6. `recommended_range_label`:
   - `p25`, `p75` 를 1,000원 단위로 반올림 → `_format_won` 으로 "1.7만원" 또는 "8,000원" 형식
   - `low == high` 이면 "1인 약 9,000원", 다르면 "1인 약 1.1만원 ~ 1.3만원"
7. 정렬: `skill` 알파벳 → `teach_mode` (1:1 / small_group / workshop) → `venue_type` (home / cafe / park / studio / gym)

### low_confidence 플래그
- `sample_count < 3` 이면 entry 에 `low_confidence: true` 추가
- 현재 event_log 기준 해당 entry 0 개 (전부 >=3)

### 스킬 커버리지 (data 기반 → 14/18)

| | 커버됨 | 누락 |
|---|---|---|
| event_log 집계 | 14 스킬 (가벼운 등산, 드로잉, 러닝, 보드게임, 스마트폰 사진, 영어 프리토킹, 우쿨렐레, 원예, 캘리그라피, 코딩 입문, 타로, 핸드드립, 홈베이킹, 홈쿡) | 4 스킬 (기타, 피아노 기초, 요가 입문, 볼더링) — simulator run 에서 샘플 미생성 |

누락 4 스킬은 앱이 fee_reference.yaml 조회 시 miss 되면 plan_library.yaml 의
`pocket_money_tip` 에서 fallback 힌트를 꺼내 쓰는 것을 권장한다 (아래 consume
flow 참조).

### idempotent
동일 `event_log.jsonl` 기준 매 실행 md5 동일 (확인: `bd5a4de2a6db1608e980364174458647`).

---

## 3. plan_library.yaml — 큐레이션 전략

### 왜 수작업인가
ContentSpec `plan_outline` 은 peer builder 의 fallback 3단 구조라 복붙 가능한
풍부한 샘플이 안 됨. 옵션 2 (simulator 확장으로 plan steps 를 event_log 에
기록) 는 시간이 오래 걸려 MVP 에서 제외. 대신 큐레이션된 상수 31~33 entry 를
`scripts/build_plan_library.py` 의 `ENTRIES` 리스트에 직접 작성.

### 스키마 (entry 1 개)

```yaml
- skill: 홈베이킹
  teach_mode: small_group
  duration_minutes: 120
  style: "재료 실비 + 함께 굽기"
  steps:
    - time: "+0분"
      activity: "인사 + 오늘 만들 디저트 소개"
    - time: "+10분"
      activity: "재료 계량하고 반죽 시작"
    # ... 5~6 steps
  pocket_money_tip: "재료비 4~5천원 + 노동료 7~8천원 정도가 적정해요. ..."
```

### 커버리지
- **18 스킬 전부** (기타, 우쿨렐레, 피아노 기초, 홈쿡, 홈베이킹, 핸드드립, 러닝, 요가 입문, 볼더링, 가벼운 등산, 드로잉, 스마트폰 사진, 캘리그라피, 영어 프리토킹, 코딩 입문, 원예, 보드게임, 타로)
- 총 33 entry (대부분 skill 당 1~2 teach_mode 조합, 인기 스킬은 3 조합)
- 평균 5.09 steps/entry (min 4, max 6)

### 톤 가이드 & 자동 검증
`_validate_entries` 가 모든 entry 에 대해 검사:
- `steps` ≥ 3
- `pocket_money_tip` 존재
- **프로 강사 어휘 금지** — `PRO_WORDS = ("강좌", "강사", "수강생", "수강료", "강의료", "자격증", "원데이 클래스", "강의")`

현재 0 건 hit. `pocket_money_tip` 에서 "강사료" 라고 쓸 뻔한 2개 entry (피아노
기초 1:1, 요가 입문 small_group) 는 "노동료" 로 교체.

---

## 4. 앱 Consume Flow (pseudo-code)

앱 프론트 사용자 플로우: "cold start → 스팟 만들기 → 폼" 에서 사용자가 스킬
이름을 입력/선택하면 앱이 두 yaml 을 번갈아 조회해 힌트 블록을 그려준다.

```python
# 의사코드 — 앱 서비스 레이어
def cold_start_hint(user_input):
    # 1. skill 추론 (enum 매칭 or fuzzy 검색)
    skill = infer_skill_from_text(user_input.raw)            # 예: "기타"
    mode  = infer_teach_mode(user_input)                      # "1:1" / "small_group" / "workshop"
    venue = infer_venue(user_input)                           # "cafe" / "home" / ...

    # 2. fee_reference.yaml 조회 (정확 매치 → 점차 fallback)
    fee_entry = (
        lookup_fee(skill, mode, venue)                        # (skill, mode, venue) 3-key
        or lookup_fee(skill, mode, None)                      # venue 무시
        or lookup_fee(skill, None, None)                      # skill 만 일치하는 엔트리 중 median 사용
    )

    # 3. plan_library.yaml 조회 (1~2 샘플 추천)
    plan_samples = lookup_plans(skill, mode, top_k=2)         # 매칭 없으면 같은 skill 의 다른 mode 반환

    # 4. hint 블록 조립
    return {
        "fee_label":
            fee_entry.recommended_range_label
            if fee_entry else plan_samples[0].pocket_money_tip,
        "fee_detail": {
            "labor":     fee_entry.peer_labor_fee.median     if fee_entry else None,
            "passthru":  fee_entry.passthrough_note           if fee_entry else None,
            "sample_n":  fee_entry.sample_count               if fee_entry else 0,
        },
        "plan_suggestions": [
            {
                "style":     s.style,
                "duration":  s.duration_minutes,
                "steps":     s.steps,          # 그대로 앱 UI 에 렌더
                "money_tip": s.pocket_money_tip,
            }
            for s in plan_samples
        ],
    }
```

### UX 패턴

```
[사용자 입력] "기타 1:1 카페에서 해보려는데요"

[앱 응답 — cold start hint]
  보통 1인 약 1.2만원 정도 받으세요
  - 순수 노동료: 약 9,000원 (p50)
  - 실비(재료/대관/장비): 약 3,000원 별도 안내 권장
  - 샘플 N=12

[플랜 추천]
  > 초보 친화 · 60분
  +0분: 가볍게 인사하고 오늘 배우고 싶은 곡/목표 물어보기
  +10분: 기본 자세와 코드 잡는 법 손봐드리기
  +25분: 간단한 코드 진행 (Am, C, G) 같이 짚어보기
  +45분: 한 소절 정도 같이 쳐보기
  +55분: 오늘 해본 것 정리하고 다음에 뭐 해볼지 얘기
  팁: 1:1 은 집중도가 높아서 1.7~1.9만원 정도가 적당해요. 기타 빌려드리면 실비 2~3천원 더.

  [다른 스타일 보기]  [이대로 플랜 담기]
```

---

## 5. 파이프라인과의 격리

- `scripts/build_fee_reference.py` 와 `scripts/build_plan_library.py` 는 **pipeline 코드를 import 하지 않는다**. `src/pipeline/*` 는 read-only.
- `config/templates/*.yaml` 파일은 `src/pipeline/spec/_peer.py`, `generator`, `validator`, `publisher` 어느 것에서도 참조되지 않는다. 순수 앱 consumer 용.
- pipeline-qa 의 500-spot publish 와 독립적으로 실행 가능 (event_log 만 공유 소스).

---

## 6. 재실행 & 업데이트 규칙

| 트리거 | 재빌드 명령 |
|--------|-----------|
| simulator event_log 갱신 | `python3 scripts/build_fee_reference.py` |
| skill 가격대 수동 조정 필요 | (현재 구조는 event_log 주도 — 필요 시 event_log 생성 단계에서 tuning) |
| plan 템플릿 추가/수정 | `scripts/build_plan_library.py` 의 `ENTRIES` 편집 후 재실행 |
| 금지어 추가 | 같은 파일의 `PRO_WORDS` tuple 에 추가 후 재실행 (validator 가 자동 거름) |

두 스크립트 모두 **idempotent**. 출력은 sort_keys 비활성화 + 명시적 entry
정렬로 결정론적.
