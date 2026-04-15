# Generator Contract — Phase 1 (feed)

> 작성: content-generator-engineer
> 파일 owner: `src/pipeline/generators/`, `config/prompts/<type>/v1.j2`, `config/weights/*.json`
> 동기 필수: codex-bridge `_workspace/scp_02_codex/prompt_contract.md`

---

## 1. ContentSpec → 공용 변수 매핑 (FeedGenerator)

`BaseGenerator.spec_to_variables(spec, variant=..., length_bucket=...)` 가
반환하는 dict 의 key 집합. **이 중 16개 키는 codex-bridge `prompt_contract.md`
의 공용 변수 표준과 100% 일치해야 한다.**

| variable | source (ContentSpec) | type | 비고 |
|---|---|---|---|
| `spot_id` | `spec.spot_id` | str | event_log 와 1:1 |
| `region_label` | `normalize_region_label(spec.region)` | str | "연무동" → "수원시 연무동" 폴백 보강 |
| `category` | `spec.category` | str | food / cafe / bar / exercise / nature / culture |
| `host_persona` | `spec.host_persona` (obj) | dict | `{type, tone, communication_style}` |
| `participants_expected_count` | `spec.participants.expected_count` | int | flatten |
| `schedule_date` | `spec.schedule.date` | str | YYYY-MM-DD |
| `schedule_time` | `spec.schedule.start_time` | str | HH:MM (24h) |
| `schedule_day_type` | `resolve_day_type(spec.schedule.date)` | str | weekday \| weekend |
| `schedule_time_slot` | `resolve_time_slot(spec.schedule.start_time)` | str | dawn / morning / late_morning / lunch / afternoon / evening / night |
| `budget_price_band` | `spec.budget.price_band` | int (1~5) | flatten |
| `budget_cost_per_person` | `spec.budget.expected_cost_per_person` | int | flatten |
| `activity_constraints` | `spec.activity_constraints` (obj) | dict | `{indoor, beginner_friendly, supporter_required}` |
| `plan_outline` | `spec.plan_outline` | list[str] | builder 가 카테고리별 폴백 생성 |
| `activity_result` | `spec.activity_result` (obj) or None | dict\|None | settle 이전이면 None |
| `desired_length_bucket` | `sample_length_bucket(spot_id, variant)` | str | short / medium / long (§7-2) |
| `sample_variant` | argument | str | primary \| alternative |

### Feed 전용 보조 변수 (공용 표준 외, prompt 본문이 참조)

| variable | source | 비고 |
|---|---|---|
| `tone_examples` | `tone_examples_for(host_persona.type)` | §7-1 페르소나별 2~3 예문 |
| `price_label_hint` | `_format_price_label(cost_per_person)` | "1인 약 1.8만원" |
| `time_label_hint` | `_format_time_label(date, start_time)` | "4/18(토) 19:00" |
| `supporter_label_hint` | `host_persona.type` | 그대로 노출용 라벨 |

---

## 2. 공용 변수 표준 체크리스트

`pipeline.generators.base.COMMON_VARIABLE_KEYS` 와 100% 일치해야 한다.
boundary audit 시 다음 16개 이름 비교:

- [x] `spot_id`
- [x] `region_label`
- [x] `category`
- [x] `host_persona` (obj)
- [x] `participants_expected_count`
- [x] `schedule_date`
- [x] `schedule_time`
- [x] `schedule_day_type` (weekday|weekend)
- [x] `schedule_time_slot` (dawn|morning|late_morning|lunch|afternoon|evening|night)
- [x] `budget_price_band`
- [x] `budget_cost_per_person`
- [x] `activity_constraints` (obj)
- [x] `plan_outline`
- [x] `activity_result` (obj)
- [x] `desired_length_bucket` (short|medium|long)
- [x] `sample_variant` (primary|alternative)

`spec_to_variables` 내부에 sanity assert 가 있어 이름이 어긋나면
`RuntimeError("spec_to_variables missing required keys: ...")` 로 빠른 실패한다.

---

## 3. Candidate 출력 shape

```python
@dataclass
class Candidate:
    content_type: str   # "feed"
    variant: str        # "primary" | "alternative"
    payload: dict       # codex 가 schema 에 맞춰 반환한 JSON
    template_id: str    # "feed:v1"
    meta: dict          # {"length_bucket","seed_hash","stub"}
```

### feed payload 키 집합 (`config/llm/schemas/feed.json` 와 일치)

```
title, summary, tags, price_label, region_label,
time_label, status, supporter_label
```

stub 모드 / bridge 미완성 시 `_placeholder_payload()` 가 위 키를 모두 채운
폴백 dict 를 반환한다. (필드 + `_stub`, `_content_type`, `_variant`, `_length_bucket`)

---

## 4. length_bucket / day_type / time_slot 계산 규칙

### 4-1. length_bucket (§7-2)

```python
rng = random.Random(hash("|".join([spot_id, variant, "len"])) & 0xFFFFFFFF)
roll = rng.random()
buckets = [("short", 0.30), ("medium", 0.50), ("long", 0.20)]
# cumulative ≤ roll 인 첫 항목
```

- deterministic: `(spot_id, variant)` 가 같으면 항상 같은 bucket.
- variant=primary / alternative 가 서로 다른 길이를 받을 수 있어, 후보 2개의
  다양성에 기여한다.

### 4-2. schedule_day_type

```python
datetime.strptime(date, "%Y-%m-%d").date().weekday() >= 5  # 토/일
```

→ `weekend` else `weekday`.

builder 의 `SIMULATION_START_DATE` 는 2026-04-18 (토). tick 0 = 토요일이므로
day 0 의 모든 spot 은 weekend.

### 4-3. schedule_time_slot

builder 가 `tick → HH:MM` 변환 시 `_tick_to_schedule()` 사용. generator 는
변환 결과의 hour 만 보고 buckets 에 매핑.

| hour 범위 | slot |
|---|---|
| 0~4 | dawn |
| 5~8 | morning |
| 9~10 | late_morning |
| 11~13 | lunch |
| 14~16 | afternoon |
| 17~20 | evening |
| 21~23 | night |

builder 상수 (`SIMULATION_START_DATE`, `MINUTES_PER_TICK`, `TICKS_PER_DAY`) 와
직접 상호작용하지 않고, 이미 변환된 `Schedule.start_time` 만 신뢰한다.
(builder 가 단일 진입점이므로 상수 변경 시에도 generator 코드는 영향 없음.)

---

## 5. codex bridge 호출 계약

generator 는 `pipeline.llm.codex_client.call_codex(template_id, variables,
schema_path, previous_rejections=...)` 만 호출한다.

- bridge 가 미배포(`ImportError`) → placeholder payload 반환 + warning
- bridge 호출 실패(예외) → placeholder payload + warning
- 정상 → 파싱된 dict 반환 (스키마 enforce 는 bridge 책임)

이는 codex-bridge 가 `prompt_contract.md` / `bridge_api.md` 를 publish 하기
전에도 generator 단독으로 동작 / 테스트되도록 하기 위함이다.

---

## 6. 페르소나 톤 자산 (§7-1)

`src/pipeline/generators/persona_tones.py::PERSONA_TONE_EXAMPLES`

| persona type | 예시 개수 |
|---|---|
| supporter_teacher | 3 |
| supporter_neutral | 3 |
| supporter_coach | 3 |
| night_social | 3 |
| weekend_explorer | 3 |
| planner | 3 |
| spontaneous | 3 |
| homebody | 3 |
| default (폴백) | 3 |

추가 페르소나가 필요하면 이 dict 만 확장한다.

---

## 7. 이번 phase 에서 생성한 파일

```
src/pipeline/generators/__init__.py
src/pipeline/generators/base.py
src/pipeline/generators/feed.py
src/pipeline/generators/persona_tones.py
config/prompts/feed/v1.j2
config/weights/length_distribution.json
config/weights/review_rating_distribution.json
_workspace/scp_03_gen/generator_contract.md   (this file)
_workspace/scp_03_gen/sample_outputs.jsonl
scripts/_scp_03_feed_check.py     (검증용 임시 스크립트)
scripts/_scp_03_sample_dump.py    (샘플 dump 용 임시 스크립트)
```

Phase 2 (detail / plan / messages / review) 는 동일 base.py 를 상속하여
`spec_to_variables` 를 super().spec_to_variables() 로 위임 + 추가 키 mix-in.
