# Simulation Log Handoff Plan: Backend + Frontend

> Scope: 2단계 spot-simulator 로그가 timing/location/hotspot signal을 더 명시적으로 배출하게 되었을 때, 백엔드와 프론트엔드가 추론을 줄이고 로그 소유권을 시뮬레이터로 옮기는 계획.

## Goal

프론트엔드가 `scheduled_tick`, `duration_ticks`, `expected_closed_at_tick`, `PERSONA_LEAVE_SPOT`, `PERSONA_RETURN_HOME`, `map_anchor`, `hotspot_signal`을 소비해서 자체 tick/lifespan/location 추론을 줄인다. 3단계 AI feed는 여전히 검증된 hotspot 주제만 LLM으로 자연어화한다.

## New simulator contract

### `CREATE_TEACH_SPOT.payload`

New/strengthened fields:

```json
{
  "scheduled_tick": 42,
  "schedule_lead_ticks": 17,
  "duration_ticks": 3,
  "expected_closed_at_tick": 45,
  "schedule_reason": "offer:small_group:cafe",
  "map_anchor": {
    "type": "region_public_jitter",
    "lat": 37.2981234,
    "lng": 127.0479123,
    "region_id": "emd_gwanggyo",
    "category": "cafe",
    "confidence": 0.55,
    "match_reason": "region_center_public_jitter"
  },
  "hotspot_signal": {
    "signal_type": "teach_spot",
    "state": "forming",
    "host_persona_type": "night_social",
    "participant_target_count": 4,
    "reason_tags": ["same_region_host", "small_group", "venue:cafe"],
    "region_character": {
      "density_cafe": 0.85,
      "density_food": 0.78,
      "density_nature": 0.72,
      "night_friendliness": 0.6,
      "group_friendliness": 0.8
    }
  }
}
```

Notes:

- `map_anchor.type=home_public_proxy_jitter` means do **not** render as exact home/private address.
- `hotspot_signal` is not feed copy. It is a map signal and should not be treated as validated AI content.

### `JOIN_TEACH_SPOT.payload`

New fields:

```json
{
  "joined_at_tick": 20,
  "join_lead_ticks": 13,
  "participant_count_after_join": 3,
  "capacity": 4,
  "join_reason_tags": ["skill_match", "same_region"]
}
```

### New mobility events

```json
{
  "event_type": "PERSONA_LEAVE_SPOT",
  "tick": 45,
  "payload": {
    "persona_id": "A_001",
    "spot_id": "S_0001",
    "from_region_id": "emd_gwanggyo",
    "to_region_id": "emd_yeongtong",
    "leave_tick": 45,
    "return_home_tick": 48,
    "reason": "activity_completed"
  }
}
```

```json
{
  "event_type": "PERSONA_RETURN_HOME",
  "tick": 48,
  "payload": {
    "persona_id": "A_001",
    "spot_id": "S_0001",
    "from_region_id": "emd_gwanggyo",
    "to_region_id": "emd_yeongtong",
    "returned_at_tick": 48,
    "reason": "activity_completed"
  }
}
```

## Backend plan

### Task B1. Extend event schema/parser

Files to inspect/update:

- Backend event-log ingestion / publisher that maps simulator JSONL to lifecycle events.
- Existing `spot.created`, `spot.participant_joined`, `spot.closed`, persona location event mappers.

Acceptance:

- Unknown fields remain backward-compatible.
- `duration_ticks`, `schedule_lead_ticks`, `map_anchor`, `hotspot_signal` are preserved in normalized event payloads.
- `PERSONA_LEAVE_SPOT` and `PERSONA_RETURN_HOME` are accepted as first-class simulator events, not dropped as unknown.

### Task B2. Publish simulator-owned lifecycle timing

Backend should prefer simulator fields:

1. `scheduled_tick`
2. `duration_ticks`
3. `expected_closed_at_tick`
4. `PERSONA_RETURN_HOME.returned_at_tick`

Acceptance:

- No backend recomputation of close time when `expected_closed_at_tick` exists.
- If legacy logs lack fields, fallback to current behavior.

### Task B3. Add hotspot signal DTO

Create/extend DTO for map layer:

```ts
type HotspotSignal = {
  spotId: string
  state: 'forming' | 'recruiting' | 'matched' | 'active' | 'completed'
  mapAnchor: MapAnchor
  skill: string
  venueType: string
  teachMode: string
  participantCount: number
  capacity: number
  reasonTags: string[]
  regionCharacter?: Record<string, number | null>
}
```

Acceptance:

- Backend exposes deterministic map signal separately from AI feed entities.
- No LLM-generated title/body is required for hotspot DTO.

### Task B4. Maintain 3단계 AI feed boundary

AI feed candidate selection may read `hotspot_signal`, but should only generate feed content after validation.

Acceptance:

- Raw hotspot does not appear as AI feed.
- AI feed records reference `source_hotspot_id` / `source_spot_id`.

## Frontend plan

### Task F1. Remove/reduce inferred lifetime logic

Find existing code that derives marker lifecycle from local timers, random lifespans, coordinate thresholds, or `Math.random()` jitter.

Replace precedence:

1. Use simulator `map_anchor.lat/lng` for marker position.
2. Use `scheduled_tick` / `expected_closed_at_tick` for lifecycle windows.
3. Use `PERSONA_LEAVE_SPOT` / `PERSONA_RETURN_HOME` for persona movement/home-return state.
4. Only fallback to old inference for legacy logs missing these fields.

Acceptance:

- No new random marker position when `map_anchor` exists.
- No immediate snap-home on `SPOT_COMPLETED` when return events exist.

### Task F2. Render hotspot as signal, not feed copy

Map marker/card should show compact signal metadata:

- skill
- venue/category
- participant count / capacity
- state badge
- reason tags chips, if useful

Avoid LLM-like feed body in the hotspot layer.

Acceptance:

- Hotspot visual is distinct from AI feed card.
- Hotspot detail can say “형성 중 / 모집 중 / 진행 중” but does not invent polished content.

### Task F3. Consume persona mobility events

Frontend location state machine should support:

```ts
'idle_home' | 'going_to_spot' | 'at_spot' | 'leaving_spot' | 'returning_home'
```

Minimum mapping:

- `CHECK_IN` -> `at_spot`
- `SPOT_COMPLETED` -> do not snap home
- `PERSONA_LEAVE_SPOT` -> `returning_home` from spot/region
- `PERSONA_RETURN_HOME` -> `idle_home`

Acceptance:

- Personas leave over staggered ticks.
- Map does not show synchronized empty gaps caused by FE snap-home.

### Task F4. Backward compatibility

Legacy logs may not include new fields.

Acceptance:

- If `map_anchor` missing: use old region center/jitter fallback.
- If `duration_ticks` missing: use old default.
- If return events missing: old completion-to-home behavior remains behind a compatibility branch.

## Rollout / QA

1. Run simulator phase 2 and archive event-log distribution:
   - create lead min/max/unique
   - duration min/max/unique
   - `PERSONA_LEAVE_SPOT == PERSONA_RETURN_HOME`
   - all `CREATE_TEACH_SPOT` have `map_anchor` and `hotspot_signal`
2. Backend parser tests with one offer and one request-matched sample.
3. Frontend replay test comparing old vs new logs:
   - marker positions should be stable from `map_anchor`
   - completed sessions should show staggered returns
   - AI feed should only appear from validated 3단계 path

## Non-goals

- No LLM copy generation in 2단계 hotspot.
- No exact private home coordinates.
- No removal of legacy fallbacks until old logs are no longer replayed.
