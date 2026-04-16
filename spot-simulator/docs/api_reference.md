# spot-simulator — 산출물 & 인터페이스 레퍼런스

> 작성일: 2026-04-16 · 대상 코드: `spot-simulator/`
> 이 문서는 simulator가 **무엇을 내보내는가**를 한곳에 정리한다. Web API가 없는 프로젝트라 "Swagger"에 대응하는 건 CLI + 산출물 파일 + 공용 dataclass다. 세 축으로 나눠 본다.
> 1. **CLI 진입점** (`python main.py ...`)
> 2. **파일 산출물** (`output/event_log.jsonl`, validation/visualize 리포트)
> 3. **공용 dataclass / enum** (AgentState, Spot, EventLog 등 — 다른 프로젝트가 import해서 재사용)

---

## 목차

1. [개요](#1-개요)
2. [CLI 진입점](#2-cli-진입점)
3. [입력 계약](#3-입력-계약)
4. [출력 산출물](#4-출력-산출물)
5. [EventLog 스키마 (JSONL 한 줄 = 한 이벤트)](#5-eventlog-스키마-jsonl-한-줄--한-이벤트)
6. [공용 dataclass 카탈로그](#6-공용-dataclass-카탈로그)
7. [validation 결과 구조](#7-validation-결과-구조)
8. [부록: event_log를 직접 질의할 때의 쿼리 템플릿](#8-부록)

---

## 1. 개요

`spot-simulator`는 에이전트 기반 시뮬레이터다. **DB도 HTTP 서버도 없다** — 대신 설정 파일(YAML/JSON) + `local-context-builder`가 만든 `region_features.json` / `persona_region_affinity.json`을 읽어 tick 루프를 돌리고, 전 과정의 이벤트를 `output/event_log.jsonl` 한 파일로 남긴다. 그 파일이 다운스트림(`synthetic-content-pipeline`)의 유일한 입력 계약이다.

주요 특징:
- **결정론적**: `reset_event_counter()` + `random.seed(...)`로 같은 시드에서 바이트 동일 출력.
- **append-only 스키마**: Phase 1 → 2 → 3 → Peer-A/B/C로 필드만 붙었고 기존 필드는 절대 리네임/삭제 안 된다. 다운스트림이 phase 값에 관계없이 그대로 파싱한다.
- **두 런타임 모드**: `simulation_mode: "legacy"` (Phase 1~3 순수 경로) / `"peer"` (또래 강사 marketplace). 둘 다 EventLog 컨테이너는 동일, event_type 카탈로그만 확장.
- **공용 dataclass 재사용**: `synthetic-content-pipeline`이 `models.skills.FeeBreakdown`, `SkillRequest` 같은 타입을 직접 import한다. import 경로 안정성이 이 문서의 관심사 중 하나.

---

## 2. CLI 진입점

### `python main.py`

모든 시뮬레이션은 이 한 줄로 돌린다. 내부적으로는 `engine.runner.run_phase(phase, config_path)`를 호출한다.

| 플래그 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `--phase` | int {1, 2, 3} | `1` | 1=MVP loop, 2=lifecycle, 3=settlement. `simulation_mode="peer"`일 땐 phase ≥ 3이면 settlement pass까지 돈다. |
| `--config` | Path | `config/simulation_config.yaml` | 위상 공통 설정. phase별 `agents`/`total_ticks`/`seed`/`action_count`/`target_runtime_seconds`와 `simulation_mode`, `peer:*` 하위 knobs를 담는다. |

예:
```bash
# 가장 일반적인 사용 — peer 모드 + Phase 2 lifecycle
python main.py --phase 2

# Phase 1 MVP 루프로 빠르게 sanity check
python main.py --phase 1 --config config/simulation_config.yaml
```

종료 시 `output/event_log.jsonl`을 덮어쓴다. 이전 로그를 보존하려면 호출 측에서 rename 해둬야 한다(관례: `event_log_<tag>.jsonl`).

### `python -m analysis.run_validate`

이벤트 로그를 읽어 plan §2.8 / §3.7 / §4.6 검증 기준을 통과했는지 채점한다.

```bash
python -m analysis.run_validate --phase 2 --event-log output/event_log.jsonl
```

실패 기준 검출 시 non-zero exit — CI 게이트로 쓸 수 있다.

### `python -m analysis.visualize`

tick-time heatmap, event_type 분포, fill_rate 히스토그램 등의 PNG/CSV 리포트를 `_workspace/`에 떨어뜨린다(분석용). 필수는 아니다.

---

## 3. 입력 계약

simulator는 네 종류의 입력을 가진다. 위치/포맷이 안 맞으면 `data/loader.py`가 startup에 터진다.

### 3.1 `config/simulation_config.yaml`

`data.loader.load_simulation_config()`의 입력. 현재 파일:

```yaml
simulation_mode: "peer"         # "legacy" | "peer"
peer:
  request_wait_deadline_ticks: 12
  counter_offer_response_ticks: 3
  max_open_requests_per_learner: 2
  region_match_bonus: 1.0
phase_1:
  agents: 50
  total_ticks: 48
  seed: 42
  time_resolution_hours: 1
  action_count: 3
  target_runtime_seconds: 5
phase_2:
  agents: 500
  total_ticks: 168
  seed: 42
  ...
phase_3:
  agents: 2000
  total_ticks: 336
  seed: 42
  ...
```

| 키 | 용도 |
|----|------|
| `simulation_mode` | runner 진입점 선택. `"peer"`면 `_run_peer`, 아니면 `_run_legacy` (바이트 동일 Phase 1~3 경로). |
| `peer.request_wait_deadline_ticks` | SkillRequest `wait_deadline_tick = created + N` |
| `peer.counter_offer_response_ticks` | counter-offer 응답 수집 기간 |
| `peer.max_open_requests_per_learner` | 학생당 동시 open 요청 상한 |
| `peer.region_match_bonus` | 같은 home_region일 때 `p_learn` 가산치 |
| `phase_<n>.agents` | 해당 phase의 agent 수 |
| `phase_<n>.total_ticks` | tick 길이 (1 tick = 1 h 기본) |
| `phase_<n>.seed` | `random.seed()`와 `reset_event_counter()`에 동시 투입 |
| `phase_<n>.action_count` | action catalog 크기 (Phase 1=3, 2=7, 3=11) |
| `phase_<n>.target_runtime_seconds` | 성능 가드레일 — 초과 시 QA가 경고 |

### 3.2 `config/persona_templates.yaml` + `config/skills_catalog.yaml`

- `persona_templates.yaml`: 페르소나 id → (host_score, join_score, budget_level, assets 기본값, skill 분포, schedule_weights, role_preference) 템플릿. `data/agent_factory.build_agent_population`이 이걸 읽고 agent를 찍어낸다.
- `skills_catalog.yaml`: `SkillTopic` 18종 × 난이도·venue·재료비·장비 요구량. `engine.fee.suggest_fee_breakdown`이 `FeeBreakdown` 초기값을 산출할 때 참조.

두 파일 다 dataclass로 파싱되면서 `__post_init__`에서 clamp되므로, 범위 밖 값은 자동으로 수정된다.

### 3.3 `data/region_features.json` / `data/persona_region_affinity.json`

`local-context-builder`가 publish한 **snapshot 사본**. 현재는 수동 복사 파일이지만 스키마가 match되어야 한다:

- `region_features.json` — region_id → `region_feature` 브리프 (`kakao_raw_score`, `blended_score`, `food_density`, ...). `data.adapters.region_create_affinity`가 조회.
- `persona_region_affinity.json` — `(persona_type, region_id)` → `affinity_score`. `data.adapters.category_match` 류가 조회.

필드 호환성은 `local-context-builder/docs/api_reference.md` §4의 `region_feature` / `persona_region_weight` 스키마와 맞춰야 한다.

### 3.4 (peer 모드 전용) `config/personas/*.yaml`

1인당 개별 override가 필요한 경우에만. 기본은 `persona_templates.yaml` 하나로 충분.

---

## 4. 출력 산출물

### 4.1 `output/event_log.jsonl` ← **primary artifact**

- **포맷**: JSONL, 한 줄 = 하나의 `EventLog` 레코드 (`serialize_event`는 `sort_keys=True`로 저장해 diff 친화적).
- **라이프사이클**: 매 실행마다 전체 덮어쓰기. 다운스트림(`synthetic-content-pipeline`의 `build-content-spec`)이 이 파일을 기본 입력으로 읽는다(`default="../spot-simulator/output/event_log.jsonl"`).
- **크기 가이드**: Phase 1은 수천 행, Phase 2는 수만, Phase 3는 수십만 행까지 나온다. 파싱은 라인 스트리밍 권장.
- **결정론**: 같은 시드 + 같은 config면 바이트 동일 — byte diff로 회귀 탐지 가능.

스키마는 §5 참조.

### 4.2 `output/event_log_*.jsonl` (보존본)

- `event_log_legacy_v1.jsonl`: peer 도입 전 Phase 1~3 출력 동결본. 다운스트림 backward compat 테스트용.
- `event_log_pre_mvp_expansion.jsonl`: MVP 확장 직전 스냅샷.
실행 루프는 이들을 건드리지 않는다. 순전히 참조용.

### 4.3 `_workspace/*` (분석 산출물, 선택)

- `analysis.visualize`가 PNG/CSV로 떨어뜨리는 분석 리포트.
- `analysis.run_validate`가 JSON 리포트(`validate_phase<n>.json`)와 콘솔 요약을 남김.
- 다운스트림 계약에 포함 안 됨 — 사람이 읽는 용도.

---

## 5. EventLog 스키마 (JSONL 한 줄 = 한 이벤트)

**소스**: `models/event.py`의 `EventLog` dataclass + `serialize_event`.

### 5.1 공통 컨테이너 (모든 이벤트가 동일)

| 필드 | 타입 | 필수 | 설명 |
|------|------|:---:|------|
| `event_id` | int | ✓ | 모듈 전역 monotonic 카운터. `reset_event_counter(start=1)`로 재설정. UUID 대신 int를 쓰는 이유는 2.4K~1.68M tick 루프 성능 때문. |
| `tick` | int | ✓ | `0`부터 시작하는 시뮬레이션 tick (기본 1 tick = 1 h). |
| `event_type` | string | ✓ | 아래 카탈로그 중 하나. `str`이라 enum 강제 아님. |
| `agent_id` | string \| null | ✓ | 행위자. lifecycle emit(SPOT_MATCHED 등)은 null. |
| `spot_id` | string \| null | ✓ | 대상 스팟. agent 단독 action(VIEW_FEED 등)은 null. |
| `region_id` | string \| null | ✓ | 해결 우선순위: 명시 인자 → `spot.region_id` → `agent.home_region_id`. |
| `payload` | object | ✓ | event_type별 자유 필드. 빈 `{}` 가능. §5.3 참조. |

JSON 예:

```json
{"event_id": 2, "tick": 0, "event_type": "CREATE_TEACH_SPOT",
 "agent_id": "A_91033", "spot_id": "S_0001", "region_id": "emd_sinchon",
 "payload": {"capacity": 1, "fee": 17867,
             "fee_breakdown": {"peer_labor_fee": 15867, "material_cost": 0,
                               "venue_rental": 2000, "equipment_rental": 0,
                               "passthrough_total": 2000, "total": 17867},
             "host_skill_level": 4, "origination_mode": "offer",
             "skill": "영어 프리토킹", "teach_mode": "1:1", "venue_type": "cafe"}}
```

### 5.2 event_type 카탈로그

카탈로그는 `models.event`에 **세 개의 set**으로 선언되어 있다. EventLog는 `str`을 저장할 뿐 강제하지 않으므로, 다운스트림이 unknown type을 만나도 crash 안 하게 설계됐다.

#### Phase 1 (`_run_legacy` 시작점)

| event_type | emit 주체 | 용도 |
|------------|-----------|------|
| `CREATE_SPOT` | agent | MVP 모드에서 호스트가 스팟 생성 |
| `JOIN_SPOT` | agent | 참여 |
| `NO_ACTION` | agent | 아무것도 안 함 (~2% 샘플만 기록 — `NO_ACTION_LOG_PROB`) |
| `SPOT_MATCHED` | lifecycle | capacity 또는 min_participants 도달 |

#### Phase 2 (`PHASE2_EVENT_TYPES`)

lifecycle 처리 + 참여 취소 / 체크인 / 노쇼 / 완료.

| event_type | emit 주체 | 용도 |
|------------|-----------|------|
| `CANCEL_JOIN` | agent | 참여 취소 (`P_CANCEL_JOIN` 확률로 발화) |
| `CHECK_IN` | agent | 스팟 시작 시점 체크인 (`_p_checkin` 공식) |
| `NO_SHOW` | agent | 체크인 실패 |
| `COMPLETE_SPOT` | agent | 완주 표시 |
| `SPOT_TIMEOUT` | lifecycle | 모집 기한 초과 → CANCELED |
| `SPOT_CONFIRMED` | lifecycle | MATCHED → CONFIRMED |
| `SPOT_STARTED` | lifecycle | CONFIRMED → IN_PROGRESS |
| `SPOT_COMPLETED` | lifecycle | IN_PROGRESS → COMPLETED |
| `SPOT_DISPUTED` | lifecycle | 체크인율 너무 낮음 → DISPUTED |

#### Phase 3 (`PHASE3_EVENT_TYPES`)

settlement / review / feed / save.

| event_type | emit 주체 | 용도 |
|------------|-----------|------|
| `WRITE_REVIEW` | agent | `process_settlement`가 `random() < p_review`일 때 발화 |
| `SETTLE` | agent | 호스트가 정산을 트리거했다는 bookkeeping 이벤트 (최대 1/스팟) |
| `SPOT_SETTLED` | lifecycle | COMPLETED → SETTLED 또는 DISPUTED → SETTLED(6h 규칙) |
| `FORCE_SETTLED` | lifecycle | DISPUTED → FORCE_SETTLED (24h timeout, `payload={"reason": "dispute_timeout"}`) |
| `DISPUTE_RESOLVED` | lifecycle | DISPUTED → SETTLED 전이 명시 |
| `VIEW_FEED` | agent | 피드 열람 (spot_id 없음, agent 단독) |
| `SAVE_SPOT` | agent | 북마크 (`AgentState.saved_spots`에 append) |

#### Peer 모드 (`PHASE_PEER_EVENT_TYPES`)

`simulation_mode: "peer"`일 때만 나타난다. 카운터-오퍼와 request dual path 포함.

| event_type | payload 주요 필드 |
|------------|--------------------|
| `SKILL_SIGNAL` | `{skill, role:"offer"\|"request", motivation:0~1}` |
| `CREATE_TEACH_SPOT` | `{skill, capacity, fee, fee_breakdown, host_skill_level, teach_mode, venue_type, origination_mode}` |
| `JOIN_TEACH_SPOT` | `{skill, fee_charged, wallet_after, is_follower}` |
| `SKILL_TRANSFER` | `{skill, level_gain:0.0~0.3}` |
| `BOND_UPDATED` | `{from, to, sessions}` (first_meet → regular → mentor_bond) |
| `FRIEND_UPGRADE` | `{skill, sessions, avg_sat}` — idempotent |
| `REFERRAL_SENT` | `{host, skill, reason}` |
| `EQUIPMENT_LENT` | `{equipment, duration_ticks}` |
| `POCKET_MONEY_EARNED` | `{amount, spot_id, partner_count}` |
| `REPUTATION_UPDATED` | `{delta, new_score}` |
| `COUNTER_OFFER_SENT` | `{from_count, to_count, original_total, new_total}` |
| `COUNTER_OFFER_ACCEPTED` | `{partner_id, new_fee}` |
| `COUNTER_OFFER_REJECTED` | `{partner_id, reason:"budget"\|"timing"\|"other"}` |
| `SPOT_RENEGOTIATED` | `{renegotiation_count, final_total, final_partner_count}` |
| `CREATE_SKILL_REQUEST` | `{request_id, skill, max_fee, mode, venue, deadline_tick}` |
| `SUPPORTER_RESPONDED` | `{request_id, host_agent_id, proposed_fee, spot_id}` |
| `REQUEST_EXPIRED` | `{request_id, reason:"no_response"\|"learner_canceled"}` |

### 5.3 payload 계약 요약

- 매 event_type의 payload는 **dict**이고 schema-free하다. 현재 구현에서 실제로 채우는 key는 위 §5.2 표가 전부다.
- 다운스트림(`synthetic-content-pipeline`)은 payload에서 `skill`, `fee_breakdown`, `origination_mode`, `teach_mode`, `venue_type`, `is_follower`를 읽어 ContentSpec에 실어 나른다. 이 key들은 **계약**이다 — 없애려면 다운스트림 호환성 확인 필요.
- 새 필드를 넣을 때는 항상 append-only. 절대 리네임/삭제하지 말 것.

---

## 6. 공용 dataclass 카탈로그

다른 프로젝트(특히 `synthetic-content-pipeline`)가 import하는 공용 타입. 파일 경로는 현재 기준.

### 6.1 `models.agent.AgentState`

tick 루프를 통과하며 mutate되는 per-agent 상태. **필드 추가만 허용**.

| 그룹 | 필드 | 타입 | 설명 |
|------|------|------|------|
| identity | `agent_id` | str | |
|         | `persona_type` | str | persona_templates의 키 |
|         | `home_region_id` | str | |
|         | `active_regions` | list[str] | |
|         | `interest_categories` | list[str] | |
| dispositions | `host_score` | float 0~1 | 스팟 생성 성향 |
|             | `join_score` | float 0~1 | 참여 성향 |
| dynamic | `fatigue` | float 0~1 | 매 tick 갱신 |
|         | `social_need` | float 0~1 | 매 tick 갱신 |
|         | `current_state` | str | `idle`/`hosting`/`joined`/`checked_in` |
| schedule | `schedule_weights` | dict[str, float] | 키: `"{day_type}_{time_slot}"` |
| budget | `budget_level` | int 1~3 | |
| tracking | `last_action_tick` | int | `-1` = 없음 |
|          | `hosted_spots` | list[str] | |
|          | `joined_spots` | list[str] | |
| Phase 2 | `trust_score` | float 0~1 | 호스트 신뢰도 |
|         | `prev_trust` | float | settlement 직전 스냅샷 |
|         | `confirmed_spots` | list[str] | |
|         | `checked_in_spots` | set[str] | O(1) 조회 |
|         | `noshow_spots` | set[str] | |
| Phase 3 | `trust_threshold` | float 0~1 | 최소 허용 호스트 신뢰도 |
|         | `review_spots` | list[str] | WRITE_REVIEW 대상 |
|         | `saved_spots` | list[str] | 북마크 |
|         | `satisfaction_history` | list[float] | 정산별 만족도 |
| Peer-A | `skills` | dict[str, SkillProfile] | `SkillTopic.value → SkillProfile` |
|        | `assets` | `Assets` | 7차원 자산 |
|        | `relationships` | dict[str, Relationship] | `other_agent_id → Relationship` |
|        | `role_preference` | str | `prefer_teach`/`prefer_learn`/`both` |

헬퍼: `AgentState.checked_in_for(spot_id) -> bool`.

### 6.2 `models.spot.Spot` + `SpotStatus`

```python
class SpotStatus(StrEnum):
    OPEN = "OPEN"
    MATCHED = "MATCHED"
    CANCELED = "CANCELED"
    CONFIRMED = "CONFIRMED"           # Phase 2
    IN_PROGRESS = "IN_PROGRESS"       # Phase 2
    COMPLETED = "COMPLETED"           # Phase 2
    DISPUTED = "DISPUTED"             # Phase 2
    SETTLED = "SETTLED"               # Phase 3
    FORCE_SETTLED = "FORCE_SETTLED"   # Phase 3
```

| 그룹 | 필드 | 타입 |
|------|------|------|
| core | `spot_id`, `host_agent_id`, `region_id`, `category` | str |
|      | `capacity`, `min_participants`, `scheduled_tick`, `created_at_tick` | int |
|      | `status` | SpotStatus (기본 OPEN) |
|      | `participants` | list[str] |
| Phase 2 lifecycle | `duration` | int (1~3, 기본 2) |
|                   | `confirmed_at_tick`/`started_at_tick`/`completed_at_tick`/`disputed_at_tick`/`canceled_at_tick` | int \| None |
|                   | `checked_in`/`noshow` | set[str] |
| Phase 3 settlement | `avg_satisfaction` | float \| None |
|                    | `noshow_count` | int |
|                    | `settled_at_tick` | int \| None |
|                    | `force_settled` | bool |
|                    | `review_count` | int |
| Peer-A teach | `skill_topic` | str ("" = legacy spot) |
|              | `host_skill_level` | int 0~5 |
|              | `fee_breakdown` | `FeeBreakdown` |
|              | `required_equipment` | list[str] |
|              | `venue_type` | str `none`/`cafe`/`home`/`studio`/`park`/`gym`/`online` |
|              | `is_followup_session` | bool |
|              | `bonded_partner_ids` | list[str] |
|              | `teach_mode` | str `1:1`/`small_group`/`workshop` |
| Peer-A+ counter-offer | `target_partner_count` | int |
|                       | `min_viable_count` | int (기본 2) |
|                       | `wait_deadline_tick` | int |
|                       | `counter_offer_sent` | bool |
|                       | `counter_offer_sent_tick` | int |
|                       | `original_fee_breakdown` | FeeBreakdown \| None |
|                       | `renegotiation_history` | list[dict] |
| Peer-A+ origination | `origination_mode` | str `offer`/`request_matched` |
|                     | `origination_agent_id` | str |
|                     | `originating_request_id` | str \| None |
|                     | `responded_at_tick` | int |

파생 프로퍼티: `spot.fee_per_partner → int` — `fee_breakdown.total` (파트너당 단위로 이미 계산됨).

### 6.3 `models.skills.*`

#### `SkillTopic` (StrEnum, 18종)

값은 한국어 문자열 그대로. YAML/event payload/LLM prompt가 전부 이 값을 쓴다.

`기타`, `우쿨렐레`, `피아노 기초`, `홈쿡`, `홈베이킹`, `핸드드립`, `러닝`, `요가 입문`, `볼더링`, `가벼운 등산`, `드로잉`, `스마트폰 사진`, `캘리그라피`, `영어 프리토킹`, `코딩 입문`, `원예`, `보드게임`, `타로`.

#### `SkillProfile`

| 필드 | 타입 | 범위 |
|------|------|------|
| `level` | int | 0~5 (0 없음, 5 전수 가능) |
| `years_exp` | float | ≥0 |
| `teach_appetite` | float | 0~1 |
| `learn_appetite` | float | 0~1 |

`__post_init__`에서 clamp.

#### `Assets`

| 차원 | 필드 | 타입/기본 |
|------|------|----------|
| 금전 | `wallet_monthly` | int, 기본 25,000 (원) |
|      | `pocket_money_motivation` | 0~1, 기본 0.5 |
|      | `earn_total`, `spent_total` | int |
| 시간 | `time_budget_weekday` | int 0~7 tick, 기본 3 |
|      | `time_budget_weekend` | int 0~14 tick, 기본 10 |
| 장비/공간 | `equipment` | set[str] (SkillTopic value subset) |
|          | `space_level` | int 0~3 |
|          | `space_type` | str `none`/`cafe`/`home`/`studio`/`park`/`gym` |
| 소셜 | `social_capital` | 0~1 |
|      | `reputation_score` | 0~1, EMA |

#### `Relationship`

| 필드 | 타입 | 설명 |
|------|------|------|
| `other_agent_id` | str | |
| `rel_type` | str | `first_meet`/`regular`/`mentor_bond`/`friend` |
| `skill_topic` | str \| None | 관계 형성 주 스킬 |
| `session_count` | int | |
| `total_satisfaction` | float | |
| `last_interaction_tick` | int | |
| `affinity` | 0~1 | 다음 세션 호의도 |
| `evolved_to_friend` | bool | FRIEND_UPGRADE idempotency |

프로퍼티: `avg_satisfaction = total_satisfaction / session_count` (session_count=0이면 0.0).

**관계 전이 임계**:
- first_meet → regular: `session_count ≥ 2 AND avg_sat ≥ 0.70`
- regular → mentor_bond: `≥ 4 AND ≥ 0.80`
- mentor_bond → friend: `≥ 6 AND ≥ 0.85`

#### `FeeBreakdown` + 상한 상수

| 필드 | 타입 | 단위 | 설명 |
|------|------|------|------|
| `peer_labor_fee` | int | 원/인 | 또래 강사 순마진 |
| `material_cost` | int | 원/인 | 재료비 실비 |
| `venue_rental` | int | 원/인 | 장소 대관료 실비 |
| `equipment_rental` | int | 원/인 | 장비 대여료 실비 |

프로퍼티: `total`, `passthrough_total = material + venue + equipment`.

**상한 상수 (원/인)** — engine/fee.py와 synthetic-content-pipeline의 feed validator 양쪽에서 single source of truth로 import:

| 상수 | 값 | 의미 |
|------|---:|------|
| `LABOR_CAP_PER_PARTNER` | 18,000 | peer_labor_fee 단독 상한 |
| `SOFT_CAP_PER_PARTNER` | 25,000 | total 일반 상한 (passthrough 없으면 초과 시 reject) |
| `HARD_CAP_PER_PARTNER` | 45,000 | total 절대 상한 (실비 포함해도 이 이상은 또래 강사 아님) |

#### `SkillRequest` (peer mode 전용)

학생이 먼저 게시하는 "배우고 싶어요" 요청.

| 필드 | 타입 | 설명 |
|------|------|------|
| `request_id` | str | `R_NNNN` 형식 |
| `learner_agent_id` | str | |
| `skill_topic` | str | SkillTopic value |
| `region_id` | str | |
| `created_at_tick` | int | |
| `max_fee_per_partner` | int | 학생 지갑 상한 |
| `preferred_teach_mode` | str | `1:1`/`small_group`/`workshop` |
| `preferred_venue` | str | `cafe`/`home`/`park`/`studio`/`gym`/`online` |
| `wait_deadline_tick` | int | `-1` = 미설정 |
| `status` | str | `OPEN`/`MATCHED`/`EXPIRED`/`CANCELED` |
| `matched_spot_id` | str \| None | |
| `matched_at_tick` | int \| None | |
| `respondent_agent_id` | str \| None | |
| `rejected_respondent_ids` | list[str] | |

상태 머신: `OPEN → MATCHED` (호스트 응답) / `OPEN → EXPIRED` (wait_deadline 초과) / `OPEN → CANCELED` (학생 취소, Phase C 확장).

### 6.4 `models.settlement.*` (Phase 3)

#### `SettlementResult`

`engine.settlement.process_settlement`의 반환값. `dataclasses.asdict`로 JSON 직렬화 가능.

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot_id` | str | |
| `completed_count` | int | CHECKED_IN participant 수 |
| `noshow_count` | int | `len(participants) - completed_count` |
| `avg_satisfaction` | float | checked-in 평균 (0명이면 0.0) |
| `host_trust_delta` | float | `host.trust_score - host.prev_trust` |
| `status` | str | `"SETTLED"` / `"FORCE_SETTLED"` |
| `settled_at_tick` | int | |

#### `Review`

| 필드 | 타입 | 설명 |
|------|------|------|
| `reviewer_agent_id` | str | |
| `spot_id` | str | |
| `satisfaction` | float 0~1 | `calculate_satisfaction`의 출력 |
| `tick` | int | 정산 tick과 동일 |

---

## 7. validation 결과 구조

`analysis/validate.py`는 EventLog를 읽어 기준을 채점한다. 내부 dict 형태는 고정 포맷이 아니지만, 중요한 임계값들은 파일 상단 상수로 선언되어 있으니 다운스트림이 직접 import 가능:

| 상수 | 값 | Phase | 의미 |
|------|---:|:-----:|------|
| `MIN_TOTAL_EVENTS` | 30 | 1 | 최소 이벤트 수 |
| `MIN_CREATE_SPOT` | 5 | 1 | 최소 CREATE_SPOT 수 |
| `MIN_JOIN_SPOT` | 10 | 1 | |
| `MIN_SPOT_MATCHED` | 2 | 1 | |
| `MAX_DAWN_RATIO` | 0.10 | 1 | 새벽 시간 action 비중 상한 |
| `MIN_HOST_SCORE_RATIO` | 1.3 | 1 | top50% / bottom50% CREATE 수 비율 |
| `MIN_FATIGUE_VARIANCE` | 0.005 | 1 | fatigue^2 population variance 바닥 |
| `MIN_FATIGUE_RANGE` | 0.05 | 1 | fatigue max-min 스프레드 바닥 |
| `PHASE2_CANCELED_RATIO_MIN/MAX` | 0.15/0.30 | 2 | CANCELED 비율 밴드 |
| `PHASE2_FOMO_MEAN_FILL_MIN` | 0.70 | 2 | MATCHED 시점 fill_rate 평균 |
| `PHASE2_HOST_TRUST_RATIO_MIN` | 1.25 | 2 | trust 상/하 쿼타일 match 비율 |
| `PHASE2_HOST_TRUST_VARIANCE_FLOOR` | 1e-6 | 2 | 이 미만은 NEUTRAL 판정 |
| `PHASE2_LEAD_TIME_MIN` | 12 | 2 | MATCHED 스팟 평균 lead_time tick |
| `PHASE2_NO_SHOW_RATIO_MIN/MAX` | 0.05/0.15 | 2 | NO_SHOW/CHECK_IN 비율 밴드 |
| `PHASE2_DISPUTED_RATIO_MAX` | 0.30 | 2 | DISPUTED/COMPLETED 비율 상한 |

튜닝 PR 시 이 값들만 grep해 바꾸면 된다.

---

## 8. 부록 — event_log.jsonl을 직접 질의할 때의 쿼리 템플릿

`event_log.jsonl`은 DB가 아니지만, `jq`나 pandas로 충분히 질의 가능하다. Simulation이 끝난 뒤 로그를 살펴볼 때 시작점으로.

### 8.1 event_type 분포

```bash
jq -r .event_type output/event_log.jsonl | sort | uniq -c | sort -rn
```

### 8.2 tick 시간대 분포 (시간대별 event 수)

```bash
jq -r '.tick % 24' output/event_log.jsonl | sort -n | uniq -c
```

### 8.3 region별 생성된 teach-spot 수

```bash
jq -r 'select(.event_type=="CREATE_TEACH_SPOT") | .region_id' output/event_log.jsonl \
  | sort | uniq -c | sort -rn
```

### 8.4 특정 spot의 lifecycle 트레이스

```bash
SPOT=S_0001
jq "select(.spot_id==\"$SPOT\")" output/event_log.jsonl
```

### 8.5 fee 분포 (peer 모드)

```bash
jq -r 'select(.event_type=="CREATE_TEACH_SPOT") | .payload.fee' output/event_log.jsonl \
  | awk '{ total += $1; n++ } END { print "mean=" total/n, "n=" n }'
```

### 8.6 pandas로 로드

```python
import pandas as pd
df = pd.read_json("output/event_log.jsonl", lines=True)
df.groupby("event_type").size().sort_values(ascending=False)
df[df.event_type == "CREATE_TEACH_SPOT"].payload.apply(lambda p: p["fee"]).describe()
```

### 8.7 CSV export (외부 도구용)

```bash
jq -r '[.event_id, .tick, .event_type, .agent_id // "", .spot_id // "", .region_id // ""] | @csv' \
  output/event_log.jsonl > output/event_log.csv
```

---

## 부가 정보

- Event log에 새 필드가 필요하면 **payload**에만 추가하고 카탈로그 주석을 업데이트한다. `EventLog` 컨테이너 자체는 append-only도 하지 말 것 — 다운스트림이 dict 구조 가정해버림.
- 다운스트림 `synthetic-content-pipeline`의 `spec/builder.py`는 이 문서가 명시한 payload key들을 **정확히** 읽는다 (`skill`, `fee_breakdown`, `origination_mode`, ...). 키를 바꾸려면 그쪽의 `_peer.py` / `_legacy.py`도 같이 업데이트.
- `FeeBreakdown` / `SkillTopic` / cap 상수는 synthetic-content-pipeline이 `from models.skills import ...` 형태로 **직접 import**한다. 즉 이 모듈은 공개 API. 리네임 시 그쪽 코드를 같이 건드려야 한다.
