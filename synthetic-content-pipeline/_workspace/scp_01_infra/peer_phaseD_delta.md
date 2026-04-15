# Phase Peer-D Delta — pipeline-infra-architect

> **완료 마크**: `scp_01_infra_peer_phaseD_complete`
>
> ContentSpec 확장 + content_spec_builder 재작성. DB schema, LLM 프롬프트,
> validator, generator 는 일체 건드리지 않음. 기존 Phase 1~4 pytest 153 passed
> 회귀 0 유지.

---

## 1. 변경 파일 (생성 / 수정)

| 파일 | 유형 | 요약 |
|---|---|---|
| `src/pipeline/spec/models.py` | 수정 | ContentSpec 필드 11 → **32**. FeeBreakdownSpec 신규. |
| `src/pipeline/spec/builder.py` | 재작성 | dispatcher (얇음). legacy 상수 re-export 유지. |
| `src/pipeline/spec/_legacy.py` | 신규 | Phase 1 builder 본문을 **수정 없이** 이동. |
| `src/pipeline/spec/_peer.py` | 신규 | Phase Peer-D peer builder. 단일 스캔 파서 + fee 역산. |
| `src/pipeline/jobs/build_content_spec.py` | 수정 | `--mode {peer,legacy}` + `--skills-catalog` 옵션 추가. |
| `tests/conftest.py` | 수정 | `_scan_create_spot_ids` 가 CREATE_TEACH_SPOT 도 인식. |

---

## 2. ContentSpec 확장 필드 (21 신규)

기존 11 필드 (spot_id ~ activity_result) 는 전부 유지. 아래 21 필드는 모두
Optional / default 가 있어 **기존 생성자 호출이 그대로 동작**.

### 2-A. peer marketplace 핵심

| 필드 | 타입 | default | source event / payload |
|---|---|---|---|
| `skill_topic` | `Optional[str]` | None | `CREATE_TEACH_SPOT.payload.skill` |
| `host_skill_level` | `Optional[int]` (0~5) | None | `CREATE_TEACH_SPOT.payload.host_skill_level` — simulator 가 아직 기록 안 함 → 항상 None |
| `teach_mode` | `Optional[str]` | None | `CREATE_TEACH_SPOT.payload.teach_mode` (`"1:1"` \| `"small_group"` \| `"workshop"`) |
| `venue_type` | `Optional[str]` | None | `CREATE_TEACH_SPOT.payload.venue_type` (`"cafe"` \| `"home"` \| `"studio"` \| `"park"` \| `"gym"`) |
| `fee_breakdown` | `Optional[FeeBreakdownSpec]` | None | `CREATE_TEACH_SPOT.payload.fee` × partner_count 를 `skills_catalog.yaml` 로 역산 (§3 참조) |

### 2-B. origination

| 필드 | 타입 | default | source |
|---|---|---|---|
| `origination_mode` | `str` | `"offer"` | `CREATE_TEACH_SPOT.payload.origination_mode`. 없으면 `SUPPORTER_RESPONDED` 동반 여부로 추론 |
| `originating_voice` | `str` | `"host"` | 파생. `origination_mode == "request_matched"` → `"learner"`, 아니면 `"host"` |
| `originating_request_summary` | `Optional[str]` | None | `request_matched` 경로에서 `SUPPORTER_RESPONDED.payload.request_id` → `CREATE_SKILL_REQUEST` 역조회 → 한 줄 요약 |
| `responded_at_tick` | `Optional[int]` | None | `SUPPORTER_RESPONDED.tick` |
| `is_request_matched` | `bool` | False | 편의 플래그 (`origination_mode == "request_matched"`) |

### 2-C. counter-offer 재협상 이력

| 필드 | 타입 | default | source |
|---|---|---|---|
| `had_renegotiation` | `bool` | False | `SPOT_RENEGOTIATED` 이벤트 존재 여부 |
| `renegotiation_history` | `list[dict]` | `[]` | `COUNTER_OFFER_SENT` / `COUNTER_OFFER_ACCEPTED` / `COUNTER_OFFER_REJECTED` / `SPOT_RENEGOTIATED` 이벤트 순서대로 |
| `original_target_partner_count` | `Optional[int]` | None | `COUNTER_OFFER_SENT.payload.from_count` |
| `final_partner_count` | `Optional[int]` | None | `SPOT_RENEGOTIATED.payload.final_partner_count` (fallback: 실제 join-cancel 수 + host) |

> **현황**: peer event_log (15,735 events) 에는 counter-offer 이벤트가 아직 **0건**. 필드는 전부 default 값. simulator Phase C/D 완성도에 따라 실데이터가 붙으면 자동으로 채워진다.

### 2-D. 관계 & 평판

| 필드 | 타입 | default | source |
|---|---|---|---|
| `bonded_partner_count` | `int` | 0 | `BOND_UPDATED.payload.to` 가 `regular`/`mentor_bond`/`friend` 인 unique `other_agent_id` 수 |
| `bond_updates_at_settlement` | `list[dict]` | `[]` | `BOND_UPDATED` 이벤트 리스트. `{partner_id, from, to, sessions, affinity, avg_sat}` |
| `friend_upgrades` | `list[dict]` | `[]` | `FRIEND_UPGRADE` 이벤트 (peer log 에 아직 0건) |
| `referrals_triggered` | `list[dict]` | `[]` | `REFERRAL_SENT` 이벤트 (peer log 에 아직 0건) |
| `host_reputation_before` | `Optional[float]` | None | `REPUTATION_UPDATED.payload.new_score - delta` (round 4) |
| `host_reputation_after` | `Optional[float]` | None | `REPUTATION_UPDATED.payload.new_score` |
| `host_earn_from_this_spot` | `Optional[int]` | None | `POCKET_MONEY_EARNED.payload.amount` |

### 2-E. LLM 생성 가이드

| 필드 | 타입 | default | 용도 |
|---|---|---|---|
| `peer_tone_required` | `bool` | True | Phase E 프롬프트 (`feed/v2.j2` …) 가 이 플래그로 또래 강사 톤을 강제 |

---

## 3. event_log parsing 알고리즘

### Pass 1 — 단일 스캔 (O(N))

`_peer._collect_events_single_pass(event_log_path, target_spot_id)` 가 파일을
딱 한 번 연다. 두 가지를 동시에 수집:

1. `spot_events`: `spot_id == target_spot_id` AND `event_type ∈ SPOT_SCOPED_EVENT_TYPES` 필터. 등장 순 보존.
2. `request_index`: 모든 `CREATE_SKILL_REQUEST` 이벤트를 `payload.request_id` 키로 dict 인덱싱.

`SPOT_SCOPED_EVENT_TYPES` 는 peer/legacy 혼재를 모두 커버하는 26종 frozenset
(CREATE_TEACH_SPOT, JOIN_TEACH_SPOT, SPOT_MATCHED, …, BOND_UPDATED, REPUTATION_UPDATED,
COUNTER_OFFER_*, SPOT_RENEGOTIATED 등).

### Pass 2 — in-memory 분류

Pass 1 결과를 파이프로 다음 헬퍼들이 순차 소비:

```
_find_create_teach_spot(events)        → CREATE_TEACH_SPOT evt
_count_joins(events)                   → (join_count, joined, cancelled)
_resolve_scheduled_tick(events, t0)    → SPOT_MATCHED > SPOT_CONFIRMED > STARTED > t0+4
_find_settle(events)                   → SPOT_SETTLED | FORCE_SETTLED | SETTLE
_estimate_fee_breakdown(...)           → FeeBreakdownSpec (skills_catalog 역산)
_summarize_request(req_evt)            → "{skill} 배우고 싶어요 (예산 N원 venue 선호) — mode"
```

### 필드 매핑 표

| ContentSpec 필드 | 출처 이벤트 | payload key | fallback |
|---|---|---|---|
| `spot_id` | — | 입력 인자 | — |
| `region` | `CREATE_TEACH_SPOT.region_id` | — | region_features.json lookup, 없으면 region_id 자체, 없으면 "알 수 없음" |
| `category` | `CREATE_TEACH_SPOT.payload.skill` | — | skill_topic 원값 그대로 쓴다 (Phase E 가 한국어 토픽을 해석). skill 이 없으면 `SKILL_CATEGORY_CLASS` fallback (미발동) |
| `host_persona` | — | — | `_infer_peer_host_persona(skill_topic, teach_mode)` deterministic fallback. Phase E content-generator-engineer 가 교체 예정 |
| `participants.expected_count` | `JOIN_TEACH_SPOT.agent_id` 유니크 수 | — | `max(2, join_count + 1)` |
| `participants.persona_mix` | — | — | 빈 리스트 (시뮬레이터가 persona 매핑 export 안 함) |
| `schedule.date/start_time` | SPOT_MATCHED tick | — | `_tick_to_schedule` (Phase 1 과 동일) |
| `schedule.duration_minutes` | — | — | 상수 120 |
| `budget.price_band` | `CREATE_TEACH_SPOT.payload.fee` | — | `PRICE_BAND_THRESHOLDS` (≤5k=1, ≤9k=2, ≤15k=3, ≤25k=4, else 5) |
| `budget.expected_cost_per_person` | `CREATE_TEACH_SPOT.payload.fee` | — | 원값, 없으면 9000 |
| `activity_constraints.indoor` | `venue_type` | — | `park` 이면 False, 그 외 True |
| `plan_outline` | — | — | `"가볍게 인사… {skill_topic} 함께 해보며… 마무리 정리…"` deterministic 3step |
| `activity_result` | `SPOT_SETTLED.payload` | `completed / noshow / avg_sat` | settle 이벤트 없으면 None |
| `skill_topic` | `CREATE_TEACH_SPOT.payload.skill` | — | None |
| `host_skill_level` | `CREATE_TEACH_SPOT.payload.host_skill_level` | — | None (simulator 가 기록 안 함) |
| `teach_mode` | `CREATE_TEACH_SPOT.payload.teach_mode` | — | None |
| `venue_type` | `CREATE_TEACH_SPOT.payload.venue_type` | — | None |
| `fee_breakdown` | `CREATE_TEACH_SPOT.payload.fee` × `expected_count - 1` | + skills_catalog | None (fee 0 이거나 partner 없음) |
| `origination_mode` | `CREATE_TEACH_SPOT.payload.origination_mode` | — | `SUPPORTER_RESPONDED` 이벤트 존재 시 `request_matched` 덮어쓰기 |
| `originating_voice` | — | 파생 | `is_request_matched ? "learner" : "host"` |
| `originating_request_summary` | `CREATE_SKILL_REQUEST.payload` (역조회) | `skill / max_fee / venue / mode` | None |
| `responded_at_tick` | `SUPPORTER_RESPONDED.tick` | — | None |
| `is_request_matched` | — | 파생 | False |
| `had_renegotiation` | `SPOT_RENEGOTIATED` | 존재 여부 | False |
| `renegotiation_history` | counter-offer 4종 이벤트 | — | `[]` |
| `original_target_partner_count` | `COUNTER_OFFER_SENT.payload.from_count` | — | None |
| `final_partner_count` | `SPOT_RENEGOTIATED.payload.final_partner_count` | — | None (또는 실 join 수) |
| `bonded_partner_count` | `BOND_UPDATED.payload.to ∈ {regular, mentor_bond, friend}` | — | 0 |
| `bond_updates_at_settlement` | `BOND_UPDATED` | full payload | `[]` |
| `friend_upgrades` | `FRIEND_UPGRADE` | full payload | `[]` |
| `referrals_triggered` | `REFERRAL_SENT` | full payload | `[]` |
| `host_reputation_before` | `REPUTATION_UPDATED.payload.new_score - delta` | — | None |
| `host_reputation_after` | `REPUTATION_UPDATED.payload.new_score` | — | None |
| `host_earn_from_this_spot` | `POCKET_MONEY_EARNED.payload.amount` | — | None |
| `peer_tone_required` | — | 상수 | True |

### fee_breakdown 역산 공식

Phase Peer-B simulator 는 `CREATE_TEACH_SPOT.payload.fee` 에 **partner 1명당 수
료** 한 값만 넣는다. 2층 구조를 복원하려면 `skills_catalog.yaml` 을 참조한다:

```
fee_total = fee × partner_count                        (partner_count = expected_count − 1)

material_cost = catalog[skill].material_cost_per_partner × partner_count
venue_rental  =
    0                                                       if venue in (home, park)
    CAFE_VENUE_RENTAL_TOTAL (=2000)                         if venue == cafe
    catalog[skill].studio_rental_total                      if venue == studio
    catalog[skill].gym_rental_total                         if venue == gym
    0                                                       otherwise
equipment_rental = 0  (host 보유 여부 정보 없음 → 보수적)

passthrough = material + venue + equipment
peer_labor_fee =
    fee_total − passthrough                                 if passthrough ≤ fee_total
    0 (with material/venue scale-down by fee_total/passthrough)  otherwise
```

catalog 가 없거나 skill 항목이 빠져 있으면 모든 실비가 0 → `peer_labor_fee = fee_total`.

**실측**: 100 spots 샘플에서 `skill_topic non-null = 100/100`, `fee_breakdown 구성 = 100/100`,
`fee_breakdown.total ∈ (0, 90000] = 100/100`. 게이트 (≥95% non-null) 통과.

### 한계 / 알려진 fallback

| 필드 | 한계 | 현재 대응 |
|---|---|---|
| `host_skill_level` | simulator Phase B payload 에 없음 | 항상 None. Phase E 프롬프트가 "또래 강사 톤" 만 강제하면 level 불필요 |
| `fee_breakdown.equipment_rental` | host 장비 보유 여부 event_log 에 없음 | 항상 0 (보수적). `EQUIPMENT_LENT` 이벤트가 별도 집계되면 Phase F 에서 보강 가능 |
| `fee_breakdown` scale-down | catalog 가 실제 simulator fee 보다 커서 passthrough > fee_total 일 때 발생 | `peer_labor=0` + 비율 축소. 핸드드립 S_0001 같은 저가 spot 에서 트리거 (정상) |
| `participants.persona_mix` | simulator 가 agent_id→persona 매핑 export 안 함 | 빈 리스트. content-generator-engineer 가 Phase E 프롬프트 fallback 으로 처리 |
| counter-offer 4필드 | peer event_log 에 `COUNTER_OFFER_*` / `SPOT_RENEGOTIATED` 이벤트 0건 | 파서는 준비됨. simulator 가 emit 하면 자동 반영 |
| `friend_upgrades`, `referrals_triggered` | 위와 동일 | `[]` |

---

## 4. legacy vs peer 분기 구조

```
pipeline/spec/builder.py (dispatcher)
  ├─ mode="peer" (default)   → pipeline.spec._peer.build_peer_content_spec(...)
  └─ mode="legacy"           → pipeline.spec._legacy.build_legacy_content_spec(...)
```

- `builder.py` 는 **레거시 상수와 헬퍼** (`SIMULATION_START_DATE`, `TICKS_PER_DAY`,
  `_tick_to_schedule`, `_infer_category` …) 를 `_legacy` 에서 re-export 한다.
  외부 모듈이 `from pipeline.spec.builder import X` 로 가져가던 경로가 깨지지 않는다.
- Phase 1 생성기 / validator / qa 가 `from pipeline.spec.builder import build_content_spec`
  형태로 쓰는 경우, 새 default `mode="peer"` 로 동작한다. conftest 가 이미
  CREATE_TEACH_SPOT 을 sample_spot_ids fixture 로 뽑도록 수정되어 있어 회귀 없음.
- legacy event_log 로 회귀 테스트를 돌리고 싶다면 `mode="legacy"` 를 명시.

### 호출 시그니처

```python
build_content_spec(
    event_log_path: str | Path,
    spot_id: str,
    *,
    mode: str = "peer",                              # "peer" | "legacy"
    region_features_path: str | Path | None = None,  # 둘 다 optional
    skills_catalog_path: str | Path | None = None,   # peer 전용
) -> ContentSpec
```

Jobs CLI:

```
python3 -m pipeline.jobs.build_content_spec \
    --event-log ../spot-simulator/output/event_log.jsonl \
    --spot-id S_0001 \
    --mode peer \
    [--region-features PATH] [--skills-catalog PATH]
```

---

## 5. Phase E 변수 매핑 표 (content-generator-engineer 에게 전달)

`base.py` 의 `COMMON_VARIABLE_KEYS` (현재 16개) 에 아래를 **append-only** 로
추가할 것. 필드명은 ContentSpec 원명을 snake_case 그대로 쓴다:

| 신규 variable key | 값 소스 | Optional? | Phase E 용도 |
|---|---|---|---|
| `skill_topic` | `spec.skill_topic` | 필수 (peer 에서 non-null) | 또래 강사 토픽 — 프롬프트 최상단 블록 |
| `host_skill_level` | `spec.host_skill_level` | 나중 | 현재 None. Phase 후반 simulator 가 기록하면 on |
| `teach_mode` | `spec.teach_mode` | 필수 | "같이 해봐요" vs "한 명한테 천천히" 톤 분기 |
| `venue_type` | `spec.venue_type` | 필수 | 실내/실외/카페 등 배경 묘사 |
| `fee_breakdown` | `spec.fee_breakdown.model_dump()` (dict) or None | 선택 | "실비 N원 포함" 강제 멘트 |
| `fee_per_partner` | `spec.budget.expected_cost_per_person` | 필수 | 편의 계산 |
| `origination_mode` | `spec.origination_mode` | 필수 | feed/detail/messages/review v2 공통 상단 블록 분기 (plan §3-request 참고) |
| `originating_voice` | `spec.originating_voice` | 필수 | `"host"`/`"learner"` voice 가이드 |
| `originating_request_summary` | `spec.originating_request_summary` | request 경로 전용 | "제가 요청 올렸는데…" review tone |
| `is_request_matched` | `spec.is_request_matched` | 필수 | 편의 플래그 (voice 블록 on/off) |
| `responded_at_tick` | `spec.responded_at_tick` | request 경로 전용 | 응답 속도 언급 |
| `had_renegotiation` | `spec.had_renegotiation` | 필수 | "원래 5명이었는데…" review hook |
| `renegotiation_history` | `spec.renegotiation_history` | 선택 | 상세 재협상 묘사 |
| `original_target_partner_count` | `spec.original_target_partner_count` | 선택 | 재협상 묘사용 |
| `final_partner_count` | `spec.final_partner_count` | 선택 | 재협상 묘사용 |
| `bonded_partner_count` | `spec.bonded_partner_count` | 필수 (0 OK) | "N명이 단골로 돌아왔어요" review hook |
| `bond_updates_at_settlement` | `spec.bond_updates_at_settlement` | 선택 | per-partner 관계 변화 |
| `friend_upgrades` | `spec.friend_upgrades` | 선택 | "친구가 되었어요" drama |
| `referrals_triggered` | `spec.referrals_triggered` | 선택 | "추천으로 왔어요" |
| `host_reputation_before` | `spec.host_reputation_before` | 선택 | — |
| `host_reputation_after` | `spec.host_reputation_after` | 선택 | 평판 변화 언급 |
| `host_earn_from_this_spot` | `spec.host_earn_from_this_spot` | 선택 | "용돈 벌이" 컨텍스트 |
| `peer_tone_required` | `spec.peer_tone_required` | 필수 (True 고정) | 프롬프트 peer-tone block 강제 |

> **주의 (Phase E 담당자)**: `COMMON_VARIABLE_KEYS` 를 확장하는 순간 **기존 stub
> fixture (`tests/fixtures/default.json`)** 에도 해당 key 가 들어있어야 sanity
> assert 가 통과한다. 현재 ContentSpec 생성은 건드리지 말고, `base.spec_to_variables`
> 에서 optional mix-in 으로 peer 필드를 추가하거나 신규 common set 를 정의할 것.
> 이 부분은 Phase D 범위 밖이므로 content-generator-engineer 가 설계 선택.

---

## 6. 검증 실행 결과

### 6-1. legacy 회귀 (pytest 153 passed 유지)

```
$ python3 -m pytest tests/ -m "not live_codex" -q
................................xxxx.x..................................  [ 45%]
........................................................................  [ 91%]
..............                                                            [100%]
153 passed, 6 deselected, 5 xfailed, 67 warnings in 1.54s
```

- Phase 4 기준 그대로. xfailed 5 건은 Phase 1 골든 region_mismatch 로 기존부터 expected-fail.
- `test_content_spec_builder.py` 4 건이 **peer event_log 에서 그대로 통과**
  (conftest `_scan_create_spot_ids` 가 CREATE_TEACH_SPOT 을 인식).

### 6-2. Peer mode — 첫 번째 teach spot (S_0001)

```
target spot: S_0001
spec.skill_topic = "핸드드립"
spec.teach_mode  = "small_group"
spec.venue_type  = "home"
spec.fee_breakdown = {peer_labor:0, material:5835, venue:0, equipment:0}  # catalog scale-down 트리거
spec.origination_mode = "offer"
spec.originating_voice = "host"
spec.host_reputation_before = 0.5
spec.host_reputation_after  = 0.4941
spec.host_earn_from_this_spot = 10560
spec.plan_outline = ["가볍게 인사…", "핸드드립 함께 해보며…", "마무리 정리…"]
spec.activity_result = {actual:2, noshow:1, duration:105, sentiment:"neutral"}
```

### 6-3. Request-matched 경로 (S_0002)

```
request_matched spot: S_0002
origination_mode             = "request_matched"
originating_voice            = "learner"
is_request_matched           = True
originating_request_summary  = "영어 프리토킹 배우고 싶어요 (예산 13,333원 cafe 선호) — small_group"
responded_at_tick            = 2
skill_topic / teach_mode / venue_type = "영어 프리토킹" / small_group / cafe
fee_breakdown = {peer_labor:0, material:0, venue:1890, equipment:0}
```

### 6-4. 관계 파싱 확인 (S_0054, 캘리그라피 장안동)

```
bonded spot: S_0054
bonded_partner_count = 4   # 4명 전원 first_meet → regular 전환
bond_updates[:3] = [
  {partner_id:A_97841, from:first_meet, to:regular, sessions:2, affinity:0.503, avg_sat:0.629},
  {partner_id:A_59735, from:first_meet, to:regular, sessions:2, affinity:0.503, avg_sat:0.629},
  {partner_id:A_94939, from:first_meet, to:regular, sessions:2, affinity:0.481, avg_sat:0.601},
]
host_reputation_before = 0.5058
host_reputation_after  = 0.5098
host_earn_from_this_spot = 14400
```

### 6-5. Legacy mode 호환 (legacy event_log S_0001)

```
legacy region: 장안동   category: food
peer fields (defaults):
  skill_topic           = None
  origination_mode      = "offer"
  originating_voice     = "host"
  bonded_partner_count  = 0
  fee_breakdown         = None
  is_request_matched    = False
```

### 6-6. Phase D 게이트 (100 spot 샘플)

```
skill_topic non-null      : 100/100 (100.0%)   [gate ≥95% PASS]
fee_breakdown constructed : 100/100
fee_breakdown total 0~90k : 100/100
origination_mode dist     : {offer: 77, request_matched: 23}
                            # §3-request R1 목표 15~40% → PASS (23%)
top skills                : 영어 프리토킹 23, 핸드드립 17, 보드게임 9,
                            홈쿡 8, 원예 8, 드로잉 8, …
```

---

## 7. Phase E (content-generator-engineer) 로 넘기는 인계 사항

1. `base.COMMON_VARIABLE_KEYS` 에 peer 변수 (5장 표) 를 append 할 때,
   **기존 generator stub fixture 가 key 를 보유하도록** 같이 업데이트해야 한다.
   이 작업은 Phase D 범위 밖이므로 여기서 하지 않았다.
2. `category` 가 peer mode 에서는 **한국어 skill_topic** ("핸드드립", "영어 프리토킹" …)
   으로 들어온다. 기존 feed/rules/category 매핑이 있다면 peer-aware 로 업데이트 필요.
3. `plan_outline` 은 deterministic 3-step fallback ("가볍게 인사… {skill} 함께
   해보며… 마무리 정리…"). 프롬프트가 더 풍부한 plan 을 원하면 `_peer._infer_peer_host_persona`
   / plan 생성 로직을 참고해 자체 생성 권장.
4. `fee_breakdown.peer_labor_fee == 0` 케이스가 흔하다 (catalog 추정이 실제
   simulator fee 를 초과). LLM 프롬프트에서 "실비만으로 운영" 톤을 쓸 수 있도록
   분기 처리하라.
5. `host_persona.tone / communication_style` 은 Phase D 기본값 ("친근하고 가벼운
   또래 톤" / "같이 해보자는 제안형 친구 말투") 수준. 제품 DNA 에 맞춰 Phase E 에서
   persona_tones 재작성하면 된다.

---

## 8. 통신 프로토콜 — 수신자별 요약

- **content-generator-engineer** → 5장 변수 매핑 표 + 7장 인계. `ContentSpec`
  필드 21개 추가 반영 + `spec_to_variables` append-only 확장.
- **validator-engineer** → ContentSpec 에 peer 필드가 추가되었으나 기존
  `content_validation_log` 스키마는 그대로. Layer 3 cross-ref 에 peer 필드
  활용 여부는 Phase E 판단.
- **codex-bridge-engineer** → 프롬프트 경로 규칙 (`config/prompts/{content_type}/v{n}.j2`)
  그대로. peer 변수는 Jinja2 StrictUndefined 에서 누락 시 컴파일 에러 나므로
  `base.spec_to_variables` 확장과 fixture 업데이트를 Phase E 와 함께 끝내야 함.
- **pipeline-qa** → `data/goldens/specs/*.json` 은 기존 7개 그대로 유지. Phase F
  에서 peer goldens 를 추가할 때 본 문서 2~3 장의 필드 매핑 표를 참고.

---

## 9. 완료 마크

- [x] ContentSpec 확장 (+21 필드, 전부 Optional/default, 기존 생성자 호환)
- [x] `build_content_spec(mode="peer" | "legacy")` dispatcher 완성
- [x] `_legacy.py` 에 Phase 1 본문 이동 (본문 수정 0)
- [x] `_peer.py` 신규 + 단일 스캔 파서 O(N)
- [x] `jobs/build_content_spec.py` CLI `--mode`, `--skills-catalog` 추가
- [x] conftest sample_spot_ids fixture CREATE_TEACH_SPOT 인식
- [x] pytest 153 passed 회귀 0
- [x] 5개 검증 시나리오 (legacy 회귀 / peer S_0001 / request_matched / bond / legacy spec) 전부 통과
- [x] Phase D 게이트 (skill non-null ≥95%) PASS (100/100)

**scp_01_infra_peer_phaseD_complete**
