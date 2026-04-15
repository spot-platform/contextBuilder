# Runtime flow — `_run_peer` (Phase B)

sim-engine-engineer 산출물. `engine/runner.py::_run_peer` 가 매 tick
어떤 순서로 pass 를 돌리는지 한 눈에 보이게 정리한다. 기존 legacy
`_run_legacy` flow 는 건드리지 않으므로 별 문서 없이 소스만 참조.

## 0. Entry point

```
run_simulation(..., simulation_mode=...)
    simulation_mode == "legacy"   → _run_legacy  (Phase 1~3 원본)
    simulation_mode == "peer"     → _run_peer    (Phase B)
```

default 는 `"legacy"` — 53 legacy pytest 가 시그니처를 건드리지 않고 통과.

## 1. Startup (tick 루프 진입 전, 1 회)

```
reset_event_counter(1)
rng ← random.Random(seed)
spot_counter, request_counter ← itertools.count(1)
spots, open_requests, event_log ← []
_enrich_agents_with_peer_fields(agents, persona_templates)
agents_by_id ← {a.agent_id: a}
```

`_enrich_agents_with_peer_fields` 는 persona yaml 의 `skills / assets /
role_preference` 를 AgentState 에 주입한다. 기존 `agent_factory` 는
legacy 필드만 세팅하므로 이 헬퍼가 peer 경로에서만 호출된다.

## 2. Tick 내부 pass 순서

```
for tick in range(total_ticks):
  ┌──────────────────────────────────────────────────────┐
  │ 1. decay_fatigue / grow_social_need                  │  (legacy pass)
  ├──────────────────────────────────────────────────────┤
  │ 2. (phase>=2) process_lifecycle                      │  peer spots 도
  │      — OPEN/MATCHED → CONFIRMED → IN_PROGRESS         │  함께 처리됨
  │        → COMPLETED / DISPUTED / CANCELED              │
  ├──────────────────────────────────────────────────────┤
  │ 3. (phase>=3) resolve_disputes + process_settlement  │  legacy path
  │      — COMPLETED → SETTLED                            │  — settlement.py
  │      — DISPUTED → SETTLED / FORCE_SETTLED             │  본체 수정 無
  ├──────────────────────────────────────────────────────┤
  │ 3.5 (phase>=3, peer only) Phase C settlement hook    │  peer-pivot §3-5
  │      for spot in spots (SETTLED|FORCE_SETTLED)       │       §3-6
  │        if _peer_c_hook_done: skip                    │       Phase C
  │        host = agents_by_id[spot.host_agent_id]       │
  │        _run_peer_settlement_hook(...)                │
  │          ├─ update_relationship(host, partner, ...)  │
  │          │    → BOND_UPDATED / FRIEND_UPGRADE        │
  │          ├─ update_reputation(host, avg_sat)         │
  │          │    → REPUTATION_UPDATED                   │
  │          ├─ credit_host_on_settlement(host, ...)      │
  │          │    → POCKET_MONEY_EARNED                  │
  │          └─ maybe_emit_referral(partner, host, ...)  │
  │               → REFERRAL_SENT                        │
  ├──────────────────────────────────────────────────────┤
  │ 4. process_open_requests(open_requests, tick, ...)   │  peer-pivot §3-request
  │    ├─ deadline 지난 OPEN → EXPIRED + REQUEST_EXPIRED │
  │    └─ 후보 filter → rng.random() < p_respond         │
  │         → SUPPORTER_RESPONDED + CREATE_TEACH_SPOT    │
  │         → new_spot.origination_mode =                │
  │           "request_matched"                          │
  │         → learner 자동 participants 포함              │
  ├──────────────────────────────────────────────────────┤
  │ 5. decision pass (rng.shuffle(active_agents))        │
  │    for agent in active_agents:                       │
  │      (a) p_post_request — CREATE_SKILL_REQUEST       │  peer-pivot §3-request
  │      (b) p_teach       — CREATE_TEACH_SPOT           │  peer-pivot §3-2
  │          · pick_skill_to_teach (weighted sample)     │
  │          · pick_teach_mode (catalog distribution)    │
  │          · suggest_fee_breakdown (plan §3-4)         │
  │      (c) find_matchable_teach_spot — JOIN_TEACH_SPOT │  peer-pivot §3-3
  │          · try_auto_match → SPOT_MATCHED             │
  │          · Phase C: charge_partner_on_join(...)       │  §3-6 / §8-3
  │            (partner.wallet -= fee, spent_total +=)   │
  │      (d) else NO_ACTION (sampled ~1%)                │
  ├──────────────────────────────────────────────────────┤
  │ 6. Counter-offer pass                                │  peer-pivot §3-counter
  │    for spot in spots:                                │
  │      check_counter_offer_trigger(spot, tick)          │
  │        → send_counter_offer(spot, host, tick)        │
  │          — COUNTER_OFFER_SENT                        │
  │      if counter_offer_sent and                       │
  │         tick >= sent_tick + response_wait_ticks:     │
  │        → finalize_counter_offer                      │
  │          — COUNTER_OFFER_ACCEPTED/REJECTED × N       │
  │          — SPOT_RENEGOTIATED (MATCHED) or            │
  │            SPOT_TIMEOUT (CANCELED)                   │
  ├──────────────────────────────────────────────────────┤
  │ 7. (phase>=2) check-in pass                          │  legacy Phase 2
  │      for spot in spots:                              │  재사용 — peer
  │        if spot.started_at_tick == tick:              │  spot 도 동일
  │          for agent in [host, *participants]:         │  방식으로 처리
  │            rng.random() < p_checkin(agent)            │
  │              → CHECK_IN / NO_SHOW                    │
  ├──────────────────────────────────────────────────────┤
  │ 8. (phase>=2) cancel pass                            │  legacy Phase 2
  │      for spot in OPEN/MATCHED:                       │  재사용
  │        for pid in spot.participants:                 │
  │          rng.random() < P_CANCEL_JOIN                 │
  │            → CANCEL_JOIN                              │
  └──────────────────────────────────────────────────────┘
```

## 3. 결정성 노트

- `rng` 는 runner 가 주입된 seed 로 단일 인스턴스를 만들어 모든
  pass 에 그대로 넘긴다. `rng.shuffle` / `rng.random` / `rng.randint` 호출
  순서는 위 pass 번호 순서 그대로.
- `process_open_requests` 는 request 를 순회하며 각 request 당 1 host
  만 매칭 → `rng.random()` 호출 횟수가 후보 수에 비례.
- decision pass 순서 안에서 `(a) → (b) → (c) → (d)` 는 "앞이 성공하면
  break" 구조. 한 agent 는 한 tick 에 최대 1 action.
- counter-offer 는 같은 tick 에 `send → finalize` 2 단계가 모두 가능
  (response_wait_ticks == 0 인 edge case). 기본 3 tick 지연이므로 통상
  경우엔 tick T 에서 send, tick T+3 에서 finalize 가 분리된다.
- lifecycle 처리는 여전히 **단일 패스** — 같은 tick 에 OPEN→MATCHED→
  CONFIRMED 다단 전이 금지. 이월은 process_lifecycle 내부 계약.

## 4. Legacy 회귀 안전성

- `_run_legacy` 본체는 이름만 rename — 바디 바이트 동일.
- `run_simulation(..., phase=1)` 호출은 기본 simulation_mode="legacy"
  로 떨어지므로 Phase 1~3 pytest 는 변화 없음.
- 신규 peer 모듈(`fee.py`, `peer_decision.py`, `negotiation.py`,
  `request_lifecycle.py`, `time_availability.py`, `_peer_math.py`) 는
  legacy path 에서 **import 만** 되고 호출되지 않는다.

## 5. Phase C 완료 상태

- `process_settlement` 본체는 그대로 (append-only 보호). Phase C 는
  `_run_peer_settlement_hook` 이라는 **runner 레이어의 후크 함수** 를 통해
  relationship FSM / reputation / wallet / referral 을 추가한다.
- `_enrich_agents_with_peer_fields` 가 초기화한 `relationships: dict` 빈
  엔트리에 첫 session 완료 시 `engine/relationships.py::update_relationship`
  이 `first_meet` entry 를 생성한다.
- `p_join_bonded` (engine/peer_decision.py:136) 는 이제 relationship 이 전이된
  이후 bonded learner 의 join 확률 가산으로 연결된다. 자연 run 에서는
  satisfaction 분포 tuning (Phase F) 전까지 전이 event 가 0 건이므로
  effective multiplier 는 여전히 1.0.
- `counter-offer` 수락 시 `negotiation.py::finalize_counter_offer` 가 이미
  `rel.affinity += 0.05` 를 수행 — 이는 Phase B 에서 구현되어 Phase C 의
  relationship 리스트와 공존한다.
- 상세 훅 포인트 / 단위 테스트 결과 / 튜닝 후보는
  `_workspace/sim_03_engine/peer_phaseC_delta.md` 참조.
