# Peer pivot — probability table (Phase B)

sim-engine-engineer 산출물. peer-pivot Phase B 에서 추가된 확률 공식과
각 가중치의 근거 위치를 한 표에 모은다. sim-analyst-qa 가 분포 검증 시
이 표와 validate 결과를 대조해 튜닝 요청을 오케스트레이터에 낸다.

모든 공식은 `spot-simulator-peer-pivot-plan.md §3` 기준. 가중치를 임의로
바꿀 때는 QA 게이트 근거를 확보한 뒤 오케스트레이터 승인 후 수정.

---

## 1. `p_teach` — `engine/peer_decision.py`

```
p_teach(agent, skill, t) =
    teach_appetite
    × (level / 5)
    × pocket_money_motivation
    × (1 - fatigue / MAX_FATIGUE)
    × time_availability(agent, t)
    × space_mod
```

| 항 | 소스 | 주석 |
|----|------|------|
| `teach_appetite` | `AgentState.skills[skill].teach_appetite` | persona_templates.yaml `skills.{topic}.teach` |
| `level / 5` | `AgentState.skills[skill].level` / 5 | L5 = 1.0, L3 = 0.6 |
| `pocket_money_motivation` | `AgentState.assets.pocket_money_motivation` | persona_templates.yaml |
| `fatigue` | `AgentState.fatigue` | decay.py 에서 관리 |
| `time_availability` | `engine.time_availability.time_availability` | weekday/weekend budget 기반 |
| `space_mod` | `1.2 if assets.space_level >= 2 else 1.0` | plan §3-2 "home 보유 +20%" |

**Gate**: `sp.level < catalog[skill].level_floor_to_teach` → 0 반환 (plan §3-4).
**Output range**: `[0, ~1.2]` (space_mod 로 1 초과 가능, decision 에서 0.6 coeff 곱함).

runner 의 decision 패스는 `rng.random() < p_teach(...) * PEER_TEACH_COEFF`
로 roll. `PEER_TEACH_COEFF = 0.6` — 실측 분포 튜닝용 knob.

---

## 2. `p_learn` — `engine/peer_decision.py`

```
p_learn(agent, skill, t, fee) =
    learn_appetite
    × (1 - level / 5)
    × budget_capability(wallet, fee)
    × (1 - fatigue / MAX_FATIGUE)
    × time_availability(agent, t)
```

| 항 | 소스 | 주석 |
|----|------|------|
| `learn_appetite` | `AgentState.skills[skill].learn_appetite` | persona yaml |
| `1 - level/5` | level headroom | 이미 잘 하면 0 |
| `budget_capability` | `engine.fee.budget_capability` | `(fee/wallet)*100` % 기반 선형 감쇠 (3%=1.0 → 30%=0.1) |
| `fatigue` | `AgentState.fatigue` | |
| `time_availability` | `engine.time_availability` | |

**Output range**: `[0, ~1]`.

---

## 3. `p_join_bonded` — `engine/peer_decision.py`

```
p_join_bonded(partner, host_id) =
    1.0                              if rel is None or rel_type == "first_meet"
    1 + min(1.0, rel.affinity)       else    # regular / mentor_bond / friend → up to 2x
```

plan §3-2 "단골 관계면 기본 확률에 2배 가산" 의 clamped 구현.

---

## 4. `find_matchable_teach_spot` — score 식 (plan §3-3)

```
score(spot, learner) =
    p_learn(learner, spot.skill_topic, tick, spot.fee_per_partner)
    × p_join_bonded(learner, spot.host_agent_id)
    × region_mod
```

| 항 | 값 |
|----|---|
| `region_mod` | `1.0` if `spot.region_id == learner.home_region_id` else `0.7` |
| tie-break | `(-score, spot_id)` |

`spot.status != OPEN`, `spot.skill_topic == ""`, `host == learner`, 이미 참가 중, capacity 꽉 참은 pre-filter.

---

## 5. Counter-offer — `engine/negotiation.py` (plan §3-counter)

### 5.1 Trigger

```
status == OPEN AND
not counter_offer_sent AND
wait_deadline_tick >= 0 AND
tick >= wait_deadline_tick AND
min_viable_count <= len(participants) < target_partner_count
```

### 5.2 재계산 (`recompute_fee_for_smaller_group`)

```
peer_labor_fee_new = int(original.peer_labor_fee * 0.85)    # 15% 할인
passthrough 3 항목        = 원본 고정 (총액)
```

partner 1 인당 분담 증가는 `spot.capacity = len(accepted)` 재할당으로 자동 반영.

### 5.3 `p_accept_counter_offer`

```
new_fee              = spot.fee_breakdown.total // len(participants)
original_fee         = spot.original_fee_breakdown.total // spot.target_partner_count
fee_delta_ratio      = new_fee / original_fee
affordability        = min(1, wallet_monthly / (new_fee * 3))
relationship_boost   = rel.affinity * 0.3      (단골일 때만)
price_penalty        = max(0, ratio - 1) * 0.4
p                    = affordability * 0.6 + relationship_boost - price_penalty
p                    = clamp(p, 0.1, 0.9)
```

| 가중치 | 값 | 근거 |
|--------|---|------|
| affordability coeff | 0.6 | plan §3-counter |
| relationship boost coeff | 0.3 | plan §3-counter |
| price penalty coeff | 0.4 | plan §3-counter |
| min clamp | 0.1 | plan §3-counter (floor) |
| max clamp | 0.9 | plan §3-counter (ceiling) |

### 5.4 Finalize

`tick >= counter_offer_sent_tick + counter_offer_response_ticks` (default 3)
→ rng 수락 roll → `accepted >= min_viable_count` 이면 MATCHED + SPOT_RENEGOTIATED,
아니면 CANCELED + SPOT_TIMEOUT.

수락 시 partner.relationships[host].affinity += 0.05 (plan §3-counter 마지막 문단).

---

## 6. Request lifecycle — `engine/request_lifecycle.py` (plan §3-request)

### 6.1 `p_post_request`

```
p_post_request(learner, skill, tick) =
    learn_appetite × role_mod × (1 - fatigue/max) × time_availability
```

| role_preference | role_mod |
|-----------------|----------|
| `prefer_learn`  | 1.2      |
| `both`          | 1.0      |
| `prefer_teach`  | 0.2      |

**Gate**: `learn_appetite < 0.3` → 0.

runner 에선 `PEER_POST_REQUEST_COEFF = 0.6` 를 곱해 roll.

### 6.2 `p_respond_to_request`

```
p_respond_to_request(host, request, tick) =
    teach_appetite × pocket_money_motivation × relationship_boost × fatigue_mod
```

- `relationship_boost = 1.0 + rel.affinity * 0.5` (없으면 1.0)
- Gate 1: `sp.level < level_floor_to_teach(skill)` → 0
- Gate 2: `suggest_fee_breakdown(...).total // 3 > request.max_fee_per_partner` → 0

host 후보 필터: `reputation_score > 0.3`.
결정성 정렬: `(home_region!=request.region, agent_id)`.

### 6.3 Spot 생성 파라미터

```
capacity              = 3
min_participants      = 2
scheduled_tick        = tick + 8
wait_deadline_tick    = tick + 12
origination_mode      = "request_matched"
origination_agent_id  = request.learner_agent_id
participants[0]       = learner (자동 join)
```

---

## 7. runner `_run_peer` — tick 내부 coeff 요약

| 상수 | 값 | 위치 | 쓰임 |
|------|---|------|------|
| `PEER_NO_ACTION_LOG_PROB`   | 0.01 | runner.py | NO_ACTION 샘플링 |
| `PEER_POST_REQUEST_COEFF`   | 0.6  | runner.py | `rng < p_post_request * coeff` |
| `PEER_TEACH_COEFF`          | 0.6  | runner.py | `rng < p_teach * coeff` |
| `PEER_LEARN_COEFF`          | 1.0  | runner.py | (현재 미사용 — `find_matchable_teach_spot` 내부가 score 로 대체) |
| `DEFAULT_MAX_OPEN_REQUESTS_PER_LEARNER` | 2 | runner.py | 학생 1 명당 동시 open 요청 상한 |
| `DEFAULT_COUNTER_OFFER_RESPONSE_TICKS`  | 3 | negotiation.py | finalize 대기 |
| `_DEFAULT_SCHEDULED_LEAD`   | 8 | request_lifecycle.py | request matched spot lead |
| `_DEFAULT_WAIT_DEADLINE_LEAD` | 12 | request_lifecycle.py | |
| `_LABOR_DISCOUNT`           | 0.85 | negotiation.py | plan §3-counter |
| `_ACCEPT_AFFINITY_BUMP`     | 0.05 | negotiation.py | plan §3-counter |

---

## 8. 튜닝 frozen-list (변경 금지)

공식 상수 중 **plan 에 명시된 것** 은 QA 게이트 근거 없이 변경하지 말 것:

- `recompute_fee_for_smaller_group` 0.85 labor discount
- `p_accept_counter_offer` 0.6 / 0.3 / 0.4 계수, 0.1 / 0.9 clamp
- `role_mod` 테이블 (1.2 / 1.0 / 0.2)
- `level_mod` 공식 `0.6 + level * 0.15`
- `motivation_mod` 공식 `0.8 + motivation * 0.4`
- `level_floor_to_teach = 3` catalog default
- `LABOR_CAP = 10_000`, `SOFT_CAP = 15_000`, `HARD_CAP = 30_000`

튜닝 허용 상수 (runner.py coeff) 는 sim-analyst-qa 피드백 기반으로 조정 가능.
