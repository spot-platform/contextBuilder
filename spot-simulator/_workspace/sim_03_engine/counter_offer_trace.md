# Counter-offer trace — 1 sample (Phase B)

sim-engine-engineer 산출물. `engine/negotiation.py` 가 plan §3-counter
공식을 어떻게 tick-by-tick 으로 굴리는지 실제 파라미터를 대입해 한 사이클
완주한 trace.

**Seed**: `rng = random.Random(7)` — determinism 확인용.

---

## 0. 초기 상태 (tick 2, spot 생성 직후)

| 필드 | 값 |
|------|---|
| spot_id | `S_0042` |
| skill_topic | 볼더링 |
| teach_mode | small_group |
| capacity / target | 5 / 5 |
| min_viable_count | 2 |
| wait_deadline_tick | 10 |
| scheduled_tick | 20 |
| fee_breakdown | `labor=6000 + material=0 + venue=14000 + equip=2500 = 22500` |
| fee_per_partner (target 5 기준) | `22500 // 5 = 4500` |

**참가자 이력**:
- tick 2~9: partner `A_P1` (wallet=60k, rel=regular affinity=0.7), `A_P2` (wallet=40k, no rel) join.
- tick 9 끝 기준 참가 2 명, 목표 5 미달.

---

## 1. tick 10 — `check_counter_offer_trigger`

- `status == OPEN` ✓
- `counter_offer_sent == False` ✓
- `tick(10) >= wait_deadline_tick(10)` ✓
- `min_viable_count(2) <= len(participants)(2) < target_partner_count(5)` ✓

→ trigger **True**

---

## 2. tick 10 — `send_counter_offer`

### 2.1 원본 보존
```
original_fee_breakdown = FeeBreakdown(6000, 0, 14000, 2500)  # total=22500
```

### 2.2 `recompute_fee_for_smaller_group`
```
peer_labor_new = int(6000 * 0.85) = 5100
passthrough    = (0, 14000, 2500)   # 총액 고정
new_total      = 21600
```

### 2.3 spot 갱신
```
spot.fee_breakdown         = FeeBreakdown(5100, 0, 14000, 2500)
spot.counter_offer_sent    = True
spot.counter_offer_sent_tick = 10
```

### 2.4 이벤트 emit
```json
{
  "event_type": "COUNTER_OFFER_SENT",
  "payload": {
    "from_count": 5,
    "to_count": 2,
    "original_total": 22500,
    "new_total": 21600
  }
}
```

현 시점 partner 1 인당 분담은 아직 `capacity=5` 기준이지만, finalize 단계
에서 `spot.capacity = len(accepted)` 로 재할당되면 `fee_per_partner` 가
자동으로 `21600 / accepted_count` 로 바뀐다.

---

## 3. 파트너 응답 확률 (`p_accept_counter_offer`)

이 시점 `len(participants) = 2` 이므로:
- `new_fee = 21600 // 2 = 10800`
- `original_fee = 22500 // 5 = 4500`
- `fee_delta_ratio = 10800 / 4500 = 2.40`

### A_P1 (wallet=60k, rel.affinity=0.7)
```
affordability   = min(1, 60000 / (10800*3)) = 0.617
relationship    = 0.7 * 0.3                = 0.210
price_penalty   = max(0, 2.40-1.0) * 0.4   = 0.560
p               = 0.617*0.6 + 0.210 - 0.560 = 0.020
p (clamped)     = 0.100
```
→ p_accept = **0.100** (affinity 가 있어도 price_penalty 가 압도)

### A_P2 (wallet=40k, 관계 없음)
```
affordability   = min(1, 40000 / 32400)    = 1.000
relationship    = 0
price_penalty   = 0.560
p               = 0.600 - 0.560             = 0.040
p (clamped)     = 0.100
```
→ p_accept = **0.100**

두 partner 모두 ratio 2.40 (분담 2.4 배 증가) 때문에 price_penalty 에
눌려 최소 clamp 0.1 에 걸렸다.

---

## 4. tick 13 — `finalize_counter_offer`

`tick(13) >= counter_offer_sent_tick(10) + response_wait_ticks(3)` ✓

`rng = Random(7)` 상태에서:
- `rng.random()` = 0.324 → A_P1: 0.324 > 0.100 → REJECT
- `rng.random()` = 0.837 → A_P2: 0.837 > 0.100 → REJECT

이벤트 emit:
```
COUNTER_OFFER_REJECTED {"partner_id":"A_P1", "reason":"budget"}
COUNTER_OFFER_REJECTED {"partner_id":"A_P2", "reason":"budget"}
```

`accepted = []`, `len(accepted)(0) < min_viable_count(2)` → CANCELED.
```
SPOT_TIMEOUT {"reason":"counter_offer_rejected"}
```

### 최종 상태
```
spot.status                 = CANCELED
spot.canceled_at_tick       = 13
spot.renegotiation_history  = [{
  tick: 13, from_count: 5, to_count: 0,
  from_total: 22500, to_total: 21600,
  accepted_by: [], rejected_by: ["A_P1","A_P2"]
}]
A_P1.relationships["A_H"].affinity = 0.70   # unchanged (reject path)
```

---

## 5. 왜 이 spot 은 실패했는가 — 플랜 정합성 체크

plan §3-counter 예시는 "labor 25k + passthrough 25k → 50k / 5 명" 시나리오
이지만, 이 trace 에서는 `peer_labor(6000)` 이 passthrough (16.5k) 에 비해
작아 재조정 할인 효과가 미미 (`22500 → 21600`, 4% 감소). 반면 partner 수
감소로 분담은 `4500 → 10800` (2.4 배) → price_penalty 가 즉시 발동.

**교훈 (Phase C 튜닝 후보)**:
1. 목표 인원 대비 실제 인원 차이가 큰 경우 (여기서는 5→2), 역제안이 사실상
   실패 루트. counter-offer 의 labor discount 0.85 만으론 passthrough 비중이
   큰 스킬 (볼더링/스튜디오/베이킹) 을 구제하기 어렵다.
2. 트리거에 `target - current <= 2` 같은 제약을 추가해 "거의 찬 spot" 만
   역제안하도록 바꾸면 C2 수락률 ≥ 50% 목표가 쉬워질 것.
3. sim-analyst-qa 에게 peer-mode 8h 시뮬 후 counter_offer 발동률 (C1) 과
   수락률 (C2) 을 측정해 달라고 요청 필요.

---

## 6. 성공 경로 예시 (참고)

동일 spot 파라미터에 partner 2 명 모두 단골 (`affinity=0.8`) + wallet 60k
였다면:
```
p_accept(A_P1) = 1.0 * 0.6 + 0.8*0.3 - 0.56 = 0.28 → clamp 0.28
p_accept(A_P2) = ... = 0.28
```
여전히 낮다. `fee_delta_ratio` 가 2.0 이상이면 clamp 범위를 벗어나지 않는
한 수락이 어렵다는 걸 확인. plan §3-counter 예시의 1.3~2.0 범위를 맞추
려면 target_partner_count 차이가 적은 케이스를 쓸 것.
