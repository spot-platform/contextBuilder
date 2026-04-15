# scp_04_val Phase 2 Delta — Layer 3 + 4종 Layer 2 rule 확장

> 완료 마크: `scp_04_val_phase2_complete`
> 범위: Layer 3 cross-reference (5쌍, 스팟 단위) + detail/plan/messages/review 개별 Layer 2 rule + `schema.py` 4종 validator 확장 + dispatch 레이어.
> **critic(Layer 4) / diversity(Layer 5) / scoring(Layer 6) / 재시도 loop 는 Phase 3** — 건드리지 않았다.

---

## 1. 생성 / 확장 파일

### 1-1. 코드

```
src/pipeline/validators/
├── schema.py              (확장) validate_detail/plan/messages/review_schema 추가 — _validate_json_schema 헬퍼
├── cross_reference.py     (신규) Layer 3 5쌍 pair validator
├── detail_rules.py        (신규) SpotDetail 5 rule
├── plan_rules.py          (신규) SpotPlan 4 rule
├── messages_rules.py      (신규) Messages 5 rule
├── review_rules.py        (신규) Review 5 rule
├── dispatch.py            (신규) content_type → (schema_fn, rule_fn, loader) 디스패처
└── __init__.py            (업데이트) Phase 2 docstring

src/pipeline/jobs/validate_cross_reference.py   (실구현) click 커맨드 + content_validation_log insert
```

Phase 1 파일 중 `rules.py`, `types.py` 는 **수정하지 않았다.** `schema.py` 는
`validate_feed_schema` 공개 API 를 **그대로 유지**하고 4종 함수만 새로 추가.

### 1-2. 설정

```
config/rules/
├── detail_rules.yaml        (신규) description 문장 수, 비용 tolerance, policy 금기어
├── plan_rules.yaml          (신규) duration tolerance, step 범위, intro 키워드
├── messages_rules.yaml      (신규) 모집 어휘, 금기 문구, 시간 표현 패턴
├── review_rules.yaml        (신규) rating→sentiment 매핑, 노쇼 금기어, 태그/버킷 범위
└── cross_reference.yaml     (신규) fuzz 임계, 가격 tolerance, time tolerance, category_keywords
```

### 1-3. 문서

```
_workspace/scp_04_val/
├── phase2_delta.md          (이 문서)
├── cross_reference_table.md (신규) 5쌍 reject 조건, skip 동작, 재시도 매핑
└── rule_table.md            (업데이트) §6 Phase 2 — 4 type rule 표 추가
```

---

## 2. 검증 로그 (2026-04-14, S_0001 기준)

### 2-A. Layer 1 — 4종 schema validator

```
detail     ok=True n_rej=0
plan       ok=True n_rej=0
messages   ok=True n_rej=0
review     ok=True n_rej=0
```

fixture 는 codex-bridge-engineer 가 작성한 `default.json` (동일한 연무동 저녁 식사 컨텍스트) → 모두 통과.

### 2-B. Layer 2 — 4종 rule validator

```
spec: region=장안동 category=food expected_cost=27000 duration=120
detail     ok=False rej_reasons=['cost_total_out_of_range']
plan       ok=True  rej_reasons=[]
messages   ok=True  rej_reasons=[]
review     ok=True  rej_reasons=[]
```

detail 만 reject 는 정상 — stub fixture 의 cost_breakdown 합계(18000원)가
S_0001 spec (expected=27000원) 의 ±[0.7, 1.5] 범위를 벗어났기 때문이다. fixture
는 S_0001 과 다른 budget band 의 spot 컨텍스트로 작성돼 있어 **rule 이 정확히 그
불일치를 감지**한 것. plan/messages/review 는 fixture 와 spec 이 충분히 정합해
통과.

### 2-C. Layer 3 — cross-reference (기본 fixture)

```
ok=False n_rej=1
executed_pairs=['feed↔detail','detail↔plan','detail↔review','feed↔messages','review↔activity_result']
skipped_pairs=[]
- detail:cost_breakdown [cost_out_of_range_vs_spec]
  -> detail.cost_breakdown 합계를 18900~40500원 사이로 맞추어 재생성하라.
```

5쌍 모두 실행, 1건 reject. feed↔messages 시각 정합은 `저녁 7시 → 19:00` 정규식
확장으로 통과. detail 비용 불일치만 detach 되며, 이는 Layer 2 rule 결과와 같은
원인이므로 재생성 루프가 detail 만 regen 하면 두 Layer 의 문제가 한 번에 해결된다.

### 2-D. Layer 3 — 부정 케이스 (category sabotage)

detail.title / description / activity_purpose / progress_style 를 모두 "드로잉/그림"
culture 카테고리로 바꾸고 review_text 도 "그림 그리기" 경험으로 교체:

```
ok=False n_rej=3
- detail:cost_breakdown      [cost_out_of_range_vs_spec]   (기존)
- feed↔detail:category       [category_mismatch]           (신규, spec=food 지만 detail 본문에 food 키워드 0개)
- detail↔review:activity_kind [review_activity_kind_mismatch] (신규, review 본문에 culture 키워드만 2+건)
```

**두 개의 활동 종류 모순 rejection** 이 정확히 잡혔다. loop 는 `feed↔detail:*` →
detail regen, `detail↔review:*` → review regen 규칙으로 두 content 를 병렬 재생성할 수 있다.

---

## 3. 5쌍 cross-ref 1줄 요약

| # | pair | reject 조건 (요약) |
|---|---|---|
| 1 | feed ↔ detail | price / region / category / supporter 라벨 중 하나라도 spec/상대방과 격차 > 허용값 |
| 2 | detail ↔ plan | detail 본문의 카테고리 키워드가 plan.steps 에 전혀 반영되지 않음 (+ materials 미사용 warn) |
| 3 | detail ↔ review | review_text 에 다른 카테고리 키워드 ≥ 2건 **AND** spec.category 대표 키워드 0건 |
| 4 | feed ↔ messages | recruiting 인데 모집 어휘 0개 **OR** feed 시각과 day_of_notice 시각 차이 > 30분 |
| 5 | review ↔ activity_result | no_show_count > 0 인데 review 에 "전원/모두/빠짐없이" 포함 **OR** overall_sentiment 와 review.sentiment 가 positive↔negative 정반대 |

---

## 4. 4종 content type × rule 함수 개수

| content_type | schema fn | rule fn 개수 | warn 포함 | 비고 |
|---|---|---|---|---|
| feed     | `validate_feed_schema`     | 8 | 1 (price_unparseable) | Phase 1 (수정 없음) |
| detail   | `validate_detail_schema`   | 5 | 0 | Phase 2 추가 |
| plan     | `validate_plan_schema`     | 4 | 1 (first_step_is_intro) | Phase 2 추가 |
| messages | `validate_messages_schema` | 5 | 1 (host_tone_consistency) | Phase 2 추가 |
| review   | `validate_review_schema`   | 5 | 1 (will_rejoin_vs_rating) | Phase 2 추가 |

총 Layer 2 rule 함수 **27개** (feed 8 + detail 5 + plan 4 + messages 5 + review 5).
Layer 3 cross-ref 함수 **5쌍** (`_pair_*`) + 1 개 public entry (`validate_cross_reference`).

---

## 5. 가장 까다로운 pair — `feed ↔ messages : time` 구현 전략

### 5-1. 문제

- feed.time_label 은 "4/18(금) 19:00" 처럼 **HH:MM 포맷**이 섞인 자연어.
- messages.day_of_notice 는 "오늘 **저녁 7시** 연무동 식당에서 봬요…" 처럼 **한국어 시간 표현** (오전/오후/저녁/밤/아침 + N시).
- 두 값의 "동일 시각" 판정은 단순 문자열 매치로 불가능. "19:00" ↔ "저녁 7시" 가 같은 시각임을 deterministic 하게 증명해야 한다.
- 게다가 day_of_notice 에는 무관한 시간 (예: "비 올 확률이 높은 4시") 이 섞일 수 있어 "본문 전체에서 찾은 모든 시각 중 feed 시각과 가장 가까운 것" 을 비교 기준으로 쓰는 **min-pair** 전략이 필요.

### 5-2. 구현

```python
_TIME_HHMM_RE = re.compile(r"(\d{1,2})\s*:\s*(\d{2})")
_TIME_HOUR_RE = re.compile(r"(오전|오후|아침|점심|저녁|밤|새벽)?\s*(\d{1,2})\s*시")

_TIME_PREFIX_PM = {"오후", "저녁", "밤"}
_TIME_PREFIX_AM = {"오전", "아침", "새벽"}


def _parse_time_minutes(text: str) -> list[int]:
    minutes = []
    for m in _TIME_HHMM_RE.finditer(text):
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            minutes.append(hh * 60 + mm)
    for m in _TIME_HOUR_RE.finditer(text):
        prefix, hh = m.group(1), int(m.group(2))
        if prefix in _TIME_PREFIX_PM and hh < 12:
            hh += 12
        if prefix in _TIME_PREFIX_AM and hh == 12:
            hh = 0
        if 0 <= hh <= 23:
            minutes.append(hh * 60)
    return minutes
```

비교 로직:

```python
feed_times = _parse_time_minutes(feed.time_label)      # [1140]  (19:00)
notice_times = _parse_time_minutes(day_of_notice)       # [1140]  (저녁 7시 → 19:00)
if feed_times and notice_times:
    min_diff = min(abs(f - n) for f in feed_times for n in notice_times)
    if min_diff > rules["time_tolerance_minutes"]:
        reject(...)
```

### 5-3. 설계 결정

1. **둘 다 있을 때만 비교** — 한쪽이 비어있으면 skip. false-positive 를 줄이기 위함. "시각이 누락된" 케이스는 `rule_day_of_notice_has_time` (messages_rules.py) 가 별도로 잡는다.
2. **min-pair 절대차** — day_of_notice 에 여러 시각이 나올 수 있으므로 "최소 차이가 임계 이내" 로 판정. 이 방식은 "19:00 모임 공지에 비 소식 4시가 섞여 있어도" 통과.
3. **prefix 확장성** — `_TIME_PREFIX_PM/AM` set 으로 분리해 이후 "한낮", "이른 저녁" 같은 표현이 필요하면 set 에 추가만 하면 된다.
4. **정규식 한계 인정** — `내일 3시` / `모레 15시` 같은 **날짜 상대 표현**은 파싱하지 않는다 (현재 범위는 day_of_notice = 당일 안내 목적). 만약 generator 가 이런 표현을 쓰면 `rule_day_of_notice_has_time` 에서 걸리지 않아 warn 이 사라지므로 별도 이슈가 되며, Phase 3 critic 이 잡을 영역으로 남겼다.
5. **초기 false-positive 케이스 수정** — 처음 구현에서는 "저녁 7시" 를 7\*60=420 으로 오해석해 fixture 가 reject 되었다. prefix set 에 `저녁/밤/아침/새벽/점심` 을 추가해 해결. phase2_delta 를 쓰는 현 시점 기본 fixture 는 모두 통과.

이 pair 가 까다로운 이유는 **생성기가 동일한 의미를 다른 포맷으로 표현** 하기 때문이다.
cross-ref validator 는 이 의미 동등성을 LLM 없이 deterministic regex 로 증명해야 한다.
반면 다른 pair (price/category) 는 숫자 또는 substring 매치로 충분히 결정성을 확보할
수 있어 상대적으로 단순했다.

---

## 6. 안 한 것

- Layer 4 critic (codex-bridge critic 호출, 샘플링 10~20%) — Phase 3
- Layer 5 diversity (n-gram, TF-IDF, 템플릿 패턴) — Phase 3
- Layer 6 scoring 가중합 (0.25/0.20/0.20/0.15/0.10/0.10) — Phase 3
- `loop/generate_validate_retry.py` 재시도 오케스트레이션 (최대 2회) — Phase 3
- Phase 1 `rules.py` / `types.py` 수정 — 변경 없음 (확장만)

Phase 3 의 재시도 루프는 이 문서의 §7 (`cross_reference_table.md`) 규칙에 따라
rejected_field prefix 로 어느 content 를 regen 할지 결정한다.
