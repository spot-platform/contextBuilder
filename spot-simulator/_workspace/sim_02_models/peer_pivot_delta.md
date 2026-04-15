# sim-model-designer — Phase Peer-A delta report

> Scope: `spot-simulator-peer-pivot-plan.md` §2 (신규 도메인 모델)
> + §3-4 (FeeBreakdown) + §7 (append-only 마이그레이션).
> Phase tag: `sim_02_models_peer_phaseA_complete`.

---

## 1. 변경 파일 목록 (절대 경로)

### 신규

- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/skills.py`
  (신규 모듈: `SkillTopic`, `SkillProfile`, `Assets`, `Relationship`,
  `FeeBreakdown`, 상한 상수 3 개)

### 수정 (append-only)

- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/agent.py`
  — Phase Peer-A 필드 4 개 append. 기존 필드 수정/삭제 0 건.
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/spot.py`
  — Phase Peer-A 필드 8 개 + `fee_per_partner` 파생 property append.
  `SpotStatus` enum 은 건드리지 않음.
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/event.py`
  — `PHASE_PEER_EVENT_TYPES` 상수 10 개 append. `EventLog` 구조 동일.
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/__init__.py`
  — peer 타입 re-export.
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/tests/test_models.py`
  — 기존 Phase 1~3 drift sentinel 에 `PHASE_PEER_AGENT_FIELDS` 를 OR-in.
  테스트 삭제/수정 0 건, append-only 확장.

### 워크스페이스

- `/home/seojingyu/project/spotContextBuilder/spot-simulator/_workspace/sim_02_models/column_contract.md`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/_workspace/sim_02_models/peer_pivot_delta.md` (이 파일)
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/_workspace/sim_02_models/_verify_drift.py`
  (드리프트 센티넬 수동 검증 스크립트)

---

## 2. append-only 원칙 준수 증거

### 2-1. AgentState 드리프트 결과

```
agent_total            : 28
phase1 missing         : []
phase2 missing         : []
phase3 missing         : []
peer   missing         : []
unexpected drift       : []
```

기존 phase 1 (15) + phase 2 (5) + phase 3 (4) = 24 → peer 4 추가 = 28.
`__annotations__.keys()` 집합 차분 결과 drift 0 건.

### 2-2. Spot 드리프트 결과

```
spot_total             : 31
peer_spot subset ok    : True
peer_spot missing      : []
```

phase 1~3 필드 23 개 + peer 8 개 = 31. `SpotStatus` enum 값은 7 개
(OPEN, MATCHED, CANCELED, CONFIRMED, IN_PROGRESS, COMPLETED, DISPUTED,
SETTLED, FORCE_SETTLED) — 전부 유지.

### 2-3. 수동 테스트 회귀 (pytest 샌드박스 대체)

| 테스트 파일 | PASS | FAIL |
|-------------|------|------|
| `tests/test_models.py`     | 22 | 0 |
| `tests/test_lifecycle.py`  | 11 | 0 |
| `tests/test_decision.py`   |  8 | 0 |
| `tests/test_settlement.py` | 12 | 0 |
| **합계**                    | **53** | **0** |

(드리프트 센티넬 `test_agent_state_phase_peer_field_set` 포함.)

---

## 3. `SkillTopic` 18 개 값 일람 (sim-data-integrator 공유)

**sim-data-integrator 가 persona_templates.yaml / skills_catalog.yaml 에서
사용해야 할 정확한 value 집합**. yaml 의 key 는 아래 한국어 문자열과
**byte-identical** 이어야 하며, 로드 시점에 `SkillTopic(value)` 로 변환
가능해야 한다.

| Enum 멤버 | value (한국어) | 카테고리 |
|-----------|----------------|----------|
| `GUITAR`         | `기타`           | 음악/악기 |
| `UKULELE`        | `우쿨렐레`        | 음악/악기 |
| `PIANO_BASIC`    | `피아노 기초`     | 음악/악기 |
| `HOMECOOK`       | `홈쿡`           | 요리/베이킹 |
| `BAKING`         | `홈베이킹`        | 요리/베이킹 |
| `COFFEE`         | `핸드드립`        | 요리/베이킹 |
| `RUNNING`        | `러닝`           | 운동/신체 |
| `YOGA_BASIC`     | `요가 입문`       | 운동/신체 |
| `CLIMBING`       | `볼더링`         | 운동/신체 |
| `HIKING`         | `가벼운 등산`     | 운동/신체 |
| `DRAWING`        | `드로잉`         | 창작/예술 |
| `PHOTO`          | `스마트폰 사진`   | 창작/예술 |
| `CALLIGRAPHY`    | `캘리그라피`      | 창작/예술 |
| `ENGLISH_TALK`   | `영어 프리토킹`    | 언어/학습 |
| `CODING_BASIC`   | `코딩 입문`       | 언어/학습 |
| `GARDENING`      | `원예`           | 생활 |
| `BOARDGAME`      | `보드게임`        | 생활 |
| `TAROT`          | `타로`           | 생활 |

총 18 개. 6 카테고리 × 2~3 스킬. `from models import SkillTopic` 으로
import 해서 `SkillTopic(value_from_yaml)` 로 검증하면 된다.

---

## 4. `AgentState.assets` 기본값 ↔ persona yaml 필드 매핑

sim-data-integrator 가 `config/persona_templates.yaml` 의 각 persona entry
에 아래 키들을 채워야 `Assets()` clamp 후 default 와 다른 실제 값이
생긴다. 키 누락은 허용되며 (default 값으로 채움) clamp 는 dataclass
`__post_init__` 에서 자동 처리된다.

| yaml 키 | dataclass 필드 | default | clamp 범위 | plan 근거 | 비고 |
|---------|----------------|---------|------------|----------|------|
| `wallet_monthly`           | `Assets.wallet_monthly`           | `25_000` (원) | `>= 0` | §2-2 / §8 | persona band 6,000 ~ 60,000 |
| `pocket_money_motivation`  | `Assets.pocket_money_motivation`  | `0.5`  | `0..1` | §2-2 | host 결정 bias |
| `earn_total`                | `Assets.earn_total`               | `0`    | `>= 0` | §2-2 | Phase D 이후 증가 |
| `spent_total`               | `Assets.spent_total`              | `0`    | `>= 0` | §2-2 | Phase D 이후 증가 |
| `time_budget_weekday`       | `Assets.time_budget_weekday`      | `3` (tick)  | `0..7`  | §2-2 | 주중 참여 capacity |
| `time_budget_weekend`       | `Assets.time_budget_weekend`      | `10` (tick) | `0..14` | §2-2 | 주말 참여 capacity |
| `equipment`                 | `Assets.equipment`                | `set()` | str subset of SkillTopic value | §2-2 | list → set 자동 변환 |
| `space_level`               | `Assets.space_level`              | `1`    | `0..3` | §2-2 | 0 없음 / 1 카페 / 2 집 / 3 스튜디오 |
| `space_type`                | `Assets.space_type`               | `"cafe"` | 문자열 한정 | §2-2 | `"none"|"cafe"|"home"|"studio"|"park"|"gym"` |
| `social_capital`            | `Assets.social_capital`           | `0.5`  | `0..1` | §2-2 | 추천 영향력 |
| `reputation_score`          | `Assets.reputation_score`         | `0.5`  | `0..1` | §2-2 | 세션 종료 시 EMA 갱신 |

**AgentState 레벨 매핑 (peer 확장분)**:

| yaml 키 | dataclass 필드 | default | 비고 |
|---------|----------------|---------|------|
| `skills` | `AgentState.skills: dict[str, SkillProfile]` | `{}` | 키는 `SkillTopic.value`, 값은 `SkillProfile(level, years_exp, teach_appetite, learn_appetite)`. non-zero 엔트리만 2~6 개 |
| `assets` | `AgentState.assets: Assets` | `Assets()` | 위 표 |
| `relationships` | `AgentState.relationships: dict[str, Relationship]` | `{}` | 초기 persona 에는 empty; Phase C 엔진이 채움 |
| `role_preference` | `AgentState.role_preference: str` | `"both"` | `"prefer_teach" | "prefer_learn" | "both"` |

---

## 5. `Spot.fee_per_partner` 하위 호환

- 기존 Phase 1~3 테스트는 Spot 에 `fee` 필드가 없으며 `fee_per_partner`
  를 직접 체크하지 않는다 (검증 완료).
- Phase Peer-A 이후, `Spot.fee_per_partner` 는 **dataclass 필드가 아닌
  property**. `fee_breakdown.total // max(1, capacity)` 로 파생.
  - `capacity <= 0` 또는 `fee_breakdown` 이 all-zero default 이면 `0` 반환
    → legacy spot (skill_topic="") 은 자연스럽게 0 원 스팟으로 보인다.
- engine/fee.py (Phase C) 의 `suggest_fee_breakdown` 은 `FeeBreakdown` 을
  생성해서 `Spot.fee_breakdown` 에 주입하며, 이후 `spot.fee_per_partner`
  는 자동으로 반영된다.
- legacy `budget_penalty(agent, spot)` 가 `spot.fee_per_partner` 를 읽어도
  — peer spot 이면 FeeBreakdown 기반 실제 값, legacy spot 이면 0 — 둘 다
  의미 있는 수치를 돌려준다.

---

## 6. `FeeBreakdown` / 상한 상수 — 사용처 조합표

| 소비자 (phase) | import | 용도 |
|----------------|--------|------|
| `engine/fee.py` (Phase C) | `FeeBreakdown`, `LABOR_CAP_PER_PARTNER`, `SOFT_CAP_PER_PARTNER`, `HARD_CAP_PER_PARTNER` | `suggest_fee_breakdown()` 에서 labor clip, total clip |
| `engine/decision.py` (Phase B/C) | `FeeBreakdown.total` | `budget_capability(agent, fee)` 에서 `wallet_monthly` 대비 비율 |
| `synthetic-content-pipeline/src/pipeline/validators/rules.py` (Phase E) | 세 상한 상수 | feed rules reject 임계값 (프로 강사 가격 배제) |
| `spot-simulator/analysis/validate_peer.py` (Phase F) | 세 상한 상수 | 분포 게이트 3a/3b/3c |

**truth source 원칙**: 상한 상수는 오직 `models/skills.py` 한 곳에만 정의.
engine / validator / analysis 모두 여기에서 import. 숫자를 다른 곳에
복제하지 말 것.

---

## 7. 필드 개수 증감 요약

| 대상 | Phase 3 말 기준 | Phase Peer-A 후 | 증감 |
|------|----------------|-----------------|------|
| `AgentState` 필드 | 24 | 28 | **+4** (`skills`, `assets`, `relationships`, `role_preference`) |
| `Spot` 필드       | 23 | 31 | **+8** (`skill_topic`, `host_skill_level`, `fee_breakdown`, `required_equipment`, `venue_type`, `is_followup_session`, `bonded_partner_ids`, `teach_mode`) |
| `Spot` property  | 0 | 1 | **+1** (`fee_per_partner`) |
| `SpotStatus` values | 9 | 9 | 0 (건드리지 않음) |
| `PHASE_*_EVENT_TYPES` 상수 | 2 (P2, P3) | 3 | **+1** (`PHASE_PEER_EVENT_TYPES`, 10 값) |
| 신규 dataclass | 2 (`SettlementResult`, `Review`) | 6 | **+4** (`SkillProfile`, `Assets`, `Relationship`, `FeeBreakdown`) |
| 신규 enum | 1 (`SpotStatus`) | 2 | **+1** (`SkillTopic` 18 값) |

---

## 8. Phase B 에 넘길 open question

다음 항목들은 **데이터 모델 범위 밖** — 엔진 / 데이터 통합자 / 분석가가
Phase B 에서 합의해야 한다. sim-model-designer 는 default 만 잡아 두었다.

1. **wallet_monthly 리셋 주기**
   - plan §8 표: `월 리셋 (선택)`.
   - 한 달 = 몇 tick 으로 간주할 것인가? Phase 1 기준 2,400 tick / 50
     agents / 48 tick/day 로는 `tick % (30*48) == 0` 같은 cron 성 로직이
     필요. → `engine/decay.py` 혹은 `engine/reset.py` 에서 처리.
   - 현재 `Assets.wallet_monthly` 는 "월 잔고" 가 아니라 "월 총 예산"
     으로 해석되어야 하는데, `engine/fee.py` 가 소진을 추적할 필드가
     별도로 필요하다 (예: `wallet_current`). Phase B 에서 추가 필드
     append 예정.

2. **`skills` dict key 타입**
   - 현재 `dict[str, SkillProfile]` — key 를 `SkillTopic` 대신 str
     로 둔 이유는 yaml 로드가 str 을 내놓기 때문. 엔진 코드는
     `SkillTopic.value` 로 key 를 lookup 하고, 필요 시 `SkillTopic(key)`
     로 enum 변환 가능. Phase B 에서 engine 이 `dict[SkillTopic, ...]` 을
     선호하면 전환 여부를 재검토.

3. **`required_equipment` 검증 책임**
   - `required_equipment: list[str]` 는 SkillTopic value subset 이어야 하는가,
     아니면 자유 텍스트인가? 현재 free-form list. Phase B 에서
     `engine/decision.py` 의 `find_matchable_spot` 이 partner
     `assets.equipment` 와 교집합을 구할 때 어떤 normalization 이
     필요한지 확정 필요.

4. **`Relationship.rel_type` 전이 idempotency**
   - `evolved_to_friend` 필드만으로 FRIEND_UPGRADE 이벤트 중복 방지. 그
     이전 전이 (`first_meet → regular`, `regular → mentor_bond`) 는
     `rel_type` 값 자체로 판단한다. Phase B 에서 전이 로직은
     engine/relationship.py (신규) 에 둔다고 가정.

5. **`FeeBreakdown` 의 `fee_per_partner` vs `fee_total` 네이밍**
   - plan §2-4 는 `fee_per_partner` 를 필드로 적었으나 plan §3-4 는
     `Spot.fee_breakdown + property` 로 대체한다. 우리는 후자를 채택.
   - 혹시 engine/decision.py 레거시 코드가 `spot.fee` 나 `spot.fee_per_partner`
     를 **대입 (assign)** 한다면, property 는 setter 가 없어 error 가 난다.
     Phase B 에서 engine 레거시 코드가 이 필드를 쓰는지 grep 필요. 현재
     테스트에서는 아무도 대입하지 않아 통과.

6. **`Spot.capacity` 와 `fee_breakdown.total` 의 "partner 수" 불일치**
   - `fee_per_partner` property 는 `capacity` 로 나누지만, `capacity` 는
     "수용 한도 상한", 실제 참여자는 `len(participants)` 일 수 있다.
     plan §3-4 suggest_fee_breakdown 에서는 `expected_partners` 를
     쓴다. 어느 쪽이 분모인지 Phase C 에서 합의 필요. 현재는 legacy 와
     정합성을 위해 `capacity` 기준.

---

## 9. 통신 summary

### → sim-data-integrator

- `SkillTopic` 18 value 리스트 (위 §3) → yaml key 로 그대로 사용.
- `Assets` 7 차원 default 값 + clamp 범위 (위 §4) → persona band 생성 시
  참조.
- `AgentState.skills` 는 dict key=str(SkillTopic.value), value=SkillProfile.
  2~6 non-zero entry 권장 (plan §2-1 주석).
- **계약**: yaml 의 `skill` key 가 `SkillTopic(value)` 로 round-trip
  되지 않으면 로드 시점에 ValueError 발생. sim-data-integrator 의 loader
  가 이를 catch 해 report.

### → sim-engine-engineer (다음 Phase B)

- `AgentState.assets.wallet_monthly` 추적을 위한 `wallet_current` 필드는
  아직 없다 (Phase B 에서 append 예정).
- `Spot.fee_per_partner` 는 **property** — setter 없음. 값 변경은
  `fee_breakdown` 을 재구성해서 대입.
- `PHASE_PEER_EVENT_TYPES` 10 개 event_type 를 engine 이 emit 해야
  게이트 통과 (plan §2-6).

### → sim-analyst-qa (Phase F 게이트 설계)

- `Assets.wallet_monthly` / `pocket_money_motivation` / `space_level`
  분포 히스토그램을 persona 로드 직후 기록할 것.
- `FeeBreakdown.total` 분포가 `LABOR_CAP_PER_PARTNER` / `SOFT_CAP` /
  `HARD_CAP` 에 대해 plan §10 의 3a / 3b / 3c 기준을 만족하는지 검증.
- `Relationship.rel_type` 분포 (first_meet / regular / mentor_bond /
  friend 비중) 를 tick 구간별로 추적.
