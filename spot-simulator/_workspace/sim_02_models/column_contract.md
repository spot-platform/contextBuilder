# sim-model-designer — column contract (Phase Peer-A append)

> Phase 1/2/3 column contract 는 기존 sim-infra 레거시 문서에 있음.
> 이 파일은 **Phase Peer-A (peer-pivot §2) 추가분만** 다룬다.
> 각 행은 `field | type | default | clamp | owner phase | plan §`.

---

## AgentState (peer append)

| field | type | default | clamp | phase | plan § |
|-------|------|---------|-------|-------|-------|
| `skills`          | `dict[str, SkillProfile]`     | `{}`             | key ∈ SkillTopic.value | Peer-A | §2-1 / §2-5 |
| `assets`          | `Assets`                       | `Assets()`        | dataclass clamps  | Peer-A | §2-2 / §2-5 |
| `relationships`   | `dict[str, Relationship]`      | `{}`             | key = other_agent_id | Peer-A | §2-3 / §2-5 |
| `role_preference` | `str`                          | `"both"`          | `"prefer_teach"` \| `"prefer_learn"` \| `"both"` | Peer-A | §2-5 |

## Spot (peer append)

| field | type | default | clamp | phase | plan § |
|-------|------|---------|-------|-------|-------|
| `skill_topic`        | `str`                 | `""`             | `SkillTopic.value` 또는 빈 문자열(legacy) | Peer-A | §2-4 |
| `host_skill_level`   | `int`                 | `0`              | `0..5` (SkillProfile 과 일치) | Peer-A | §2-4 |
| `fee_breakdown`      | `FeeBreakdown`         | `FeeBreakdown()`  | dataclass 내부        | Peer-A | §2-4 / §3-4 |
| `required_equipment` | `list[str]`            | `[]`              | SkillTopic value subset 권장 | Peer-A | §2-4 |
| `venue_type`         | `str`                 | `"cafe"`          | `"none"|"cafe"|"home"|"studio"|"park"|"gym"|"online"` | Peer-A | §2-4 |
| `is_followup_session`| `bool`                 | `False`           | —                  | Peer-A | §2-4 |
| `bonded_partner_ids` | `list[str]`            | `[]`              | agent_id 참조       | Peer-A | §2-4 |
| `teach_mode`         | `str`                 | `"small_group"`   | `"1:1"|"small_group"|"workshop"` | Peer-A | §2-4 / §3-4 |

**property**: `fee_per_partner = fee_breakdown.total // max(1, capacity)`,
`capacity <= 0` 이면 `0`. setter 없음.

---

## SkillProfile (신규, plan §2-1)

| field | type | default | clamp |
|-------|------|---------|-------|
| `level`           | `int`    | `0`   | `0..5`  |
| `years_exp`       | `float`  | `0.0` | `>= 0`  |
| `teach_appetite`  | `float`  | `0.0` | `0..1`  |
| `learn_appetite`  | `float`  | `0.0` | `0..1`  |

## Assets (신규, plan §2-2)

| field | type | default | clamp |
|-------|------|---------|-------|
| `wallet_monthly`          | `int`    | `25_000` | `>= 0` |
| `pocket_money_motivation` | `float`  | `0.5`    | `0..1` |
| `earn_total`              | `int`    | `0`      | `>= 0` |
| `spent_total`             | `int`    | `0`      | `>= 0` |
| `time_budget_weekday`     | `int`    | `3`      | `0..7` |
| `time_budget_weekend`     | `int`    | `10`     | `0..14`|
| `equipment`               | `set[str]` | `set()` | list → set 자동 변환 |
| `space_level`             | `int`    | `1`      | `0..3` |
| `space_type`              | `str`    | `"cafe"` | str    |
| `social_capital`          | `float`  | `0.5`    | `0..1` |
| `reputation_score`        | `float`  | `0.5`    | `0..1` |

## Relationship (신규, plan §2-3)

| field | type | default |
|-------|------|---------|
| `other_agent_id`      | `str`               | (required) |
| `rel_type`            | `str`               | `"first_meet"` |
| `skill_topic`         | `Optional[str]`      | `None` |
| `session_count`       | `int`               | `0` |
| `total_satisfaction`  | `float`             | `0.0` |
| `last_interaction_tick` | `int`              | `-1` |
| `affinity`            | `float`             | `0.5` |
| `evolved_to_friend`   | `bool`              | `False` |

**property**: `avg_satisfaction = total_satisfaction / session_count`
(zero-safe → `0.0`).

## FeeBreakdown (신규, plan §3-4)

| field | type | default |
|-------|------|---------|
| `peer_labor_fee`    | `int` | `0` |
| `material_cost`     | `int` | `0` |
| `venue_rental`      | `int` | `0` |
| `equipment_rental`  | `int` | `0` |

**property**: `total`, `passthrough_total`.

## 상한 상수 (plan §3-4)

| 상수 | 값 | 대상 |
|------|-----|------|
| `LABOR_CAP_PER_PARTNER`  | `10_000` (원) | `peer_labor_fee` 단독 |
| `SOFT_CAP_PER_PARTNER`   | `15_000` (원) | `total` (passthrough 없이) |
| `HARD_CAP_PER_PARTNER`   | `30_000` (원) | `total` (passthrough 포함) |

---

## SkillTopic (신규 enum, 18 values)

GUITAR / UKULELE / PIANO_BASIC / HOMECOOK / BAKING / COFFEE /
RUNNING / YOGA_BASIC / CLIMBING / HIKING / DRAWING / PHOTO /
CALLIGRAPHY / ENGLISH_TALK / CODING_BASIC / GARDENING / BOARDGAME / TAROT.

value 는 한국어 (기타, 우쿨렐레, ..., 타로). 자세한 매핑은
`peer_pivot_delta.md` §3 참조.

---

## PHASE_PEER_EVENT_TYPES (신규, plan §2-6)

```
SKILL_SIGNAL
CREATE_TEACH_SPOT
JOIN_TEACH_SPOT
SKILL_TRANSFER
BOND_UPDATED
FRIEND_UPGRADE
REFERRAL_SENT
EQUIPMENT_LENT
POCKET_MONEY_EARNED
REPUTATION_UPDATED
```

기존 `PHASE2_EVENT_TYPES` / `PHASE3_EVENT_TYPES` 는 건드리지 않음.
`EventLog` 구조도 동일 (event_id, tick, event_type, agent_id, spot_id,
region_id, payload).
