# Phase Peer-C delta — relationship FSM + wallet/time soft tracking

sim-engine-engineer / `sim_03_engine_peer_phaseC_complete`.

플랜 §3-5 (Relationship update), §3-6 (Reputation & 소셜 자본), §5 Phase C
를 반영한 append-only 증분. 기존 Phase 1~3 pytest 53 건 그대로 통과.

## 0. 신규 / 수정 파일

| 파일 | 상태 | 역할 |
|------|------|------|
| `engine/relationships.py` | **신규** | Relationship FSM + 대칭 업데이트 + 전이 이벤트 emit + reputation EMA + referral emission |
| `engine/wallet_tracker.py` | **신규** | partner wallet 소프트 차감 / host earn_total 누적 / REPUTATION_UPDATED emit |
| `engine/runner.py` | **수정** | peer path 에만 Phase C 훅 추가 (settlement hook + JOIN 시 wallet charge). `_run_legacy` 는 **건드리지 않음** |
| `engine/settlement.py` | 유지 | **본체 수정 없음** (append-only 원칙 준수) |
| `models/*.py` | 유지 | 신규 필드 없음. `Spot._peer_c_hook_done` 은 dataclass field 가 아닌 instance attribute 로 부착 (runner 전용) |

## 1. FSM 전이 테이블 (plan §3-5 재현)

```
first_meet  ─(session_count >= 2 & avg_sat >= 0.70)──────────> regular       (BOND_UPDATED)
regular     ─(session_count >= 4 & avg_sat >= 0.80)──────────> mentor_bond   (BOND_UPDATED)
mentor_bond ─(session_count >= 6 & avg_sat >= 0.85 & rng<0.30)> friend       (FRIEND_UPGRADE)
```

구현 위치: `engine/relationships.py::_TRANSITION_RULES` (module-level
list). sim-analyst-qa 가 Phase F 분석 후 이 리스트만 고쳐도 전이가 재조정.

### 단위 테스트 trace (seed 고정, 단일 pair)

| scenario | 결과 |
|----------|------|
| sat=0.75 × 2 sessions | session 2 에서 first_meet→regular (BOND_UPDATED × 2, host+partner 대칭) |
| sat=0.85 × 4 sessions | session 2 first_meet→regular, session 4 regular→mentor_bond |
| sat=0.90 × 10 sessions (seed=3) | sessions 2/4/6 전이, friend 전이 (rng=0.248 < 0.30) |
| sat=0.50 × 10 sessions | 전이 없음 (first_meet 유지) — 임계 미달 |

모두 PASS — FSM 단위 테스트 통과.

## 2. 대칭 업데이트 규약

`update_relationship(host, partner, spot, sat, tick, rng)` 는 두 entry 를
각자 업데이트:

1. `host.relationships[partner.agent_id]`
2. `partner.relationships[host.agent_id]`

두 entry 는 독립된 `Relationship` 인스턴스이지만 같은 session_count /
total_satisfaction 을 공유 (양쪽 같은 이벤트로 동일 입력을 받음) → 전이
시점도 동일. 단, friend 전이의 `rng.random() < 0.30` roll 은 각 entry 마다
독립이므로 한 쪽은 friend, 반대 쪽은 mentor_bond 인 짧은 비대칭 상태가
존재할 수 있다 (다음 세션에서 정렬됨).

## 3. Runner 훅 포인트 3개

### (a) Settlement 후크 — `_run_peer` tick 3.5 pass

```python
if phase >= 3:
    resolve_disputes(...)
    for spot in spots:
        if spot.status == COMPLETED and spot.settled_at_tick is None:
            process_settlement(...)   # ← 수정 없음
    # 3.5 Phase Peer-C hook
    for spot in spots:
        if spot.status not in (SETTLED, FORCE_SETTLED): continue
        if spot._peer_c_hook_done: continue
        host = agents_by_id.get(spot.host_agent_id)
        peer_c_events = _run_peer_settlement_hook(spot, host, agents_by_id, tick, rng)
        event_log.extend(peer_c_events)
```

`_run_peer_settlement_hook` 순서:
1. `update_relationship(host, partner_i, spot, partner_i.satisfaction_history[-1], tick, rng)` × checked-in partners → BOND_UPDATED / FRIEND_UPGRADE
2. `update_reputation(host, avg_sat)` + `record_reputation_update(host, tick, delta, spot)` → REPUTATION_UPDATED
3. `credit_host_on_settlement(host, spot, tick)` → POCKET_MONEY_EARNED
4. `maybe_emit_referral(partner_i, host, agents_by_id, spot, tick, rng)` × checked-in partners → REFERRAL_SENT

idempotency: `spot._peer_c_hook_done = True` instance attribute (append-only 를
위해 dataclass field 로 추가하지 않고 동적 속성). 같은 spot 이 dispute
처리로 두 번 settlement 되어도 relation 업데이트는 1 회만.

### (b) JOIN 시점 wallet 차감 — `_run_peer` decision pass (c) 브랜치

```python
if execute_join_spot(agent, target, tick):
    after_join_spot(agent)
    wallet_tracker.charge_partner_on_join(agent, target)   # ← 신규
    event_log.append(make_event(..., "JOIN_TEACH_SPOT", payload={
        ..., "fee_charged": target.fee_per_partner,
        "wallet_after": agent.assets.wallet_monthly,
    }))
```

partner 가 join 확정될 때 `wallet_monthly -= fee_per_partner` (0 floor),
`spent_total += fee_per_partner`. 이 mutation 은 후속 tick 의 `p_teach` /
`p_post_request` 계산에 반영되어 partner 의 지갑 잔고에 따라 behavior drift.

### (c) Settlement 시 host 수익 누적 — (a) 의 wallet step

`credit_host_on_settlement` 에서
`host.assets.earn_total += labor × partner_count`,
`host.assets.wallet_monthly += labor × partner_count`.
**labor 만** 순마진으로 계산; passthrough (material/venue/equipment) 는
pass-through 이므로 earn_total 에 포함되지 않는다.

## 4. Legacy settlement.py 미수정 증거

```
$ wc -l engine/settlement.py
 370 engine/settlement.py
```

Phase C 작업 전후 동일. `engine/settlement.py` 는 runner 훅에서만 호출되고
본체는 read-only.

```
$ python3 -m pytest tests/ -q
.....................................................
53 passed in 0.05s
```

## 5. 50 agents × 48 tick peer smoke (seed=42)

| 항목 | Phase B baseline | Phase C 결과 | 비고 |
|------|------------------|--------------|------|
| total events | 1313 | **1721** | +408 |
| total spots | 103 | **115** | +12 |
| POCKET_MONEY_EARNED | — | **69** | spot 당 1회 (SETTLED=69) |
| REPUTATION_UPDATED | — | **69** | spot 당 1회 |
| BOND_UPDATED | — | **0** | avg_sat 분포가 0.47 (플랜 임계 0.70) |
| FRIEND_UPGRADE | — | **0** | "    |
| REFERRAL_SENT | — | **0** | "    |
| hosts with earn_total>0 | — | **32** | mean 24,960 / min 9,238 / max 51,402 |
| partners with spent_total>0 | — | **50** | mean 12,621 / min 1,868 / max 42,922 |

### event 수 차이의 원인 (Phase B → Phase C)

- 138 events = 69 POCKET_MONEY_EARNED + 69 REPUTATION_UPDATED 직접 추가
- 나머지 ~270 events = wallet_monthly mutation 이 `p_teach` (wallet 에
  의존) / `CREATE_SKILL_REQUEST` (max_fee 계산 에 의존) 의 rng 분기
  트래젝토리를 바꾼 결과. 이는 **의도된 drift** — plan §7-2 표가 요구하는
  "세션 참여 시 partner.wallet -= fee" 의 자연스러운 귀결.

### 0-count 이벤트 원인 (BOND / FRIEND / REFERRAL)

자연 run 의 avg_satisfaction 평균 0.47 (336 tick 에서도 0.47). FSM 은
session_count ≥ 2 (536 entries) 를 만족하는 pair 는 많지만 avg_sat ≥ 0.70
문턱을 넘는 pair 가 0 개여서 전이가 한 건도 발생하지 않음.

**검증**: `calculate_satisfaction` 을 monkey-patch 해 0.92 고정으로 336 tick
run:
```
BOND_UPDATED:       1036
FRIEND_UPGRADE:     175
REFERRAL_SENT:      869
POCKET_MONEY_EARNED: 717
REPUTATION_UPDATED: 717
final rel types: mentor_bond=271, friend=175, regular=144, first_meet=48
```
→ runner 훅이 전체 경로(FSM + reputation + wallet + referral)를 정확히
emit. Phase C 엔진 로직은 **문제 없음**. 0 count 는 Phase F 에 넘겨야 할
**satisfaction tuning 문제**.

## 6. Phase D / Phase F 에 넘길 것

### Phase D (event_log + content_spec_builder) 로 넘길 open question

1. `JOIN_TEACH_SPOT` payload 에 `fee_charged`, `wallet_after` 필드가 추가됨 —
   content_spec_builder 가 파싱하는가?
2. `POCKET_MONEY_EARNED` payload 의 `new_wallet`, `earn_total` 누적값이
   content spec 의 "호스트가 이번 수업으로 얼마 벌었어요" 서사에 쓸 수 있는가?
3. `BOND_UPDATED` payload 의 `affinity` (신규 필드) 를 review 톤 선택에 활용?
4. `FRIEND_UPGRADE` 이벤트 발생 시 해당 세션의 review 가 "이제 친구가
   되었다" 류 voice 로 렌더될 수 있는가? (review prompt v2 에 반영)
5. `REFERRAL_SENT` 는 feed/messages 의 어느 컨텍스트에 노출?

### Phase F 튜닝 후보 파라미터

| 파라미터 | 위치 | 현재값 | 튜닝 근거 |
|---------|------|-------|---------|
| `SATISFACTION_BASE` | `settlement.py` | 0.5 | natural avg 0.47 → 0.70 이상 끌어올리려면 base 0.6~0.65 시도 |
| `CATEGORY_MATCH_BONUS` | `settlement.py` | 0.15 | 0.20 으로 늘려서 매칭 품질 반영 |
| `TRUST_GAP_PENALTY` | `settlement.py` | 0.10 | 0.05 로 줄여 avg 끌어올림 |
| `_TRANSITION_RULES` first_meet→regular 임계 | `relationships.py` | `(2, 0.70)` | `(2, 0.60)` 으로 완화 시도 |
| `_AFFINITY_STABILITY_CAP_SESSIONS` | `relationships.py` | 6 | 4 로 줄이면 affinity 성장이 빨라짐 |
| `_REFERRAL_MIN_SOURCE_AVG_SAT` | `relationships.py` | 0.75 | 0.65 |
| `_REFERRAL_TARGET_SC_BUMP` | `relationships.py` | 0.02 | 0.03 |
| `_REPUTATION_EMA_ALPHA` | `relationships.py` | 0.1 | 0.2 로 늘리면 평판이 최근 세션에 민감 |
| wallet_monthly 월 리셋 주기 | `wallet_tracker.py` | **없음** | 336 tick (1 week) 또는 1344 tick (1 month) 로 결정 |
| wallet 0-floor 정책 | `wallet_tracker.py` | hard floor | 적자 허용 → 부정 값으로 "체납" 시뮬 가능 |
| peer_labor vs passthrough 수익 | `wallet_tracker.py` | labor 만 | passthrough 일부를 host 마진으로 인정할지 |

## 7. 결정성 (determinism)

모든 rng 호출은 주입된 `random.Random(seed)` 하나를 경유:

- `update_relationship` — friend 전이 30% roll (host/partner 각 1회)
- `maybe_emit_referral` — 확률 gate 1회 + (내부) 타겟 pick 은 결정론 sort
- `record_reputation_update` — rng 미사용
- `credit_host_on_settlement` — rng 미사용
- `charge_partner_on_join` — rng 미사용

runner 의 tick 내 호출 순서는 `1.decay → 2.lifecycle → 3.settlement →
3.5 peer_c_hook → 4.process_open_requests → 5.decide → 6.counter_offer →
7.checkin → 8.cancel` 로 고정. 같은 seed 에서 동일 event_log.jsonl 생성
(53 legacy + peer smoke 모두 재현성 확인).
