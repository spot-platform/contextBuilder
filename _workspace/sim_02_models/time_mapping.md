# sim_02_models — Tick -> Time Mapping (Phase 1 verification table)

Task: `sim_02_models_phase1_complete`
Agent: `sim-model-designer`
Date: 2026-04-14

Phase 1 simulates **2 days (48 ticks)** per plan §1. The table below is the
expected output of `engine.time_utils.{get_time_slot, get_day_type,
schedule_key}` for every tick in the Phase 1 window. This is the
reference sim-analyst-qa should use to verify `weekday_dawn` events are
suppressed and `weekday_evening` is the peak slot.

Day 0 is interpreted as Monday (weekday). In a 48-tick window, no weekend
ticks appear — weekend coverage is exercised in Phase 2 (168 ticks).

## Mapping table

| tick | hour | day_type | time_slot     | schedule_key           |
|-----:|-----:|----------|---------------|------------------------|
|    0 |    0 | weekday  | dawn          | weekday_dawn           |
|    1 |    1 | weekday  | dawn          | weekday_dawn           |
|    2 |    2 | weekday  | dawn          | weekday_dawn           |
|    3 |    3 | weekday  | dawn          | weekday_dawn           |
|    4 |    4 | weekday  | dawn          | weekday_dawn           |
|    5 |    5 | weekday  | dawn          | weekday_dawn           |
|    6 |    6 | weekday  | dawn          | weekday_dawn           |
|    7 |    7 | weekday  | morning       | weekday_morning        |
|    8 |    8 | weekday  | morning       | weekday_morning        |
|    9 |    9 | weekday  | morning       | weekday_morning        |
|   10 |   10 | weekday  | late_morning  | weekday_late_morning   |
|   11 |   11 | weekday  | late_morning  | weekday_late_morning   |
|   12 |   12 | weekday  | lunch         | weekday_lunch          |
|   13 |   13 | weekday  | lunch         | weekday_lunch          |
|   14 |   14 | weekday  | afternoon     | weekday_afternoon      |
|   15 |   15 | weekday  | afternoon     | weekday_afternoon      |
|   16 |   16 | weekday  | afternoon     | weekday_afternoon      |
|   17 |   17 | weekday  | afternoon     | weekday_afternoon      |
|   18 |   18 | weekday  | evening       | weekday_evening        |
|   19 |   19 | weekday  | evening       | weekday_evening        |
|   20 |   20 | weekday  | evening       | weekday_evening        |
|   21 |   21 | weekday  | night         | weekday_night          |
|   22 |   22 | weekday  | night         | weekday_night          |
|   23 |   23 | weekday  | night         | weekday_night          |
|   24 |    0 | weekday  | dawn          | weekday_dawn           |
|   25 |    1 | weekday  | dawn          | weekday_dawn           |
|   26 |    2 | weekday  | dawn          | weekday_dawn           |
|   27 |    3 | weekday  | dawn          | weekday_dawn           |
|   28 |    4 | weekday  | dawn          | weekday_dawn           |
|   29 |    5 | weekday  | dawn          | weekday_dawn           |
|   30 |    6 | weekday  | dawn          | weekday_dawn           |
|   31 |    7 | weekday  | morning       | weekday_morning        |
|   32 |    8 | weekday  | morning       | weekday_morning        |
|   33 |    9 | weekday  | morning       | weekday_morning        |
|   34 |   10 | weekday  | late_morning  | weekday_late_morning   |
|   35 |   11 | weekday  | late_morning  | weekday_late_morning   |
|   36 |   12 | weekday  | lunch         | weekday_lunch          |
|   37 |   13 | weekday  | lunch         | weekday_lunch          |
|   38 |   14 | weekday  | afternoon     | weekday_afternoon      |
|   39 |   15 | weekday  | afternoon     | weekday_afternoon      |
|   40 |   16 | weekday  | afternoon     | weekday_afternoon      |
|   41 |   17 | weekday  | afternoon     | weekday_afternoon      |
|   42 |   18 | weekday  | evening       | weekday_evening        |
|   43 |   19 | weekday  | evening       | weekday_evening        |
|   44 |   20 | weekday  | evening       | weekday_evening        |
|   45 |   21 | weekday  | night         | weekday_night          |
|   46 |   22 | weekday  | night         | weekday_night          |
|   47 |   23 | weekday  | night         | weekday_night          |

Verified against the Phase 1 probe (`tests/_sim02_probe.py`, removed after
verification). Regenerate with:

```python
from engine import get_day_type, get_time_slot, schedule_key
for t in range(48):
    print(t, t % 24, get_day_type(t), get_time_slot(t), schedule_key(t))
```

## Slot coverage sanity

Every hour 0..23 maps to exactly one slot (plan §2.5 boundaries are
inclusive on both ends, `start <= hour <= end`):

- `dawn`         : hours 0,1,2,3,4,5,6         (7 hours)
- `morning`      : hours 7,8,9                 (3 hours)
- `late_morning` : hours 10,11                 (2 hours)
- `lunch`        : hours 12,13                 (2 hours)
- `afternoon`    : hours 14,15,16,17           (4 hours)
- `evening`      : hours 18,19,20              (3 hours)
- `night`        : hours 21,22,23              (3 hours)

Total: 24 hours, no gaps, no overlaps — the `dawn` fallback branch in
`get_time_slot` is therefore unreachable for integer ticks, but is kept
as defensive behaviour for future non-integer tick experiments.

## Phase 1 validation anchors

`sim-analyst-qa` uses this table to assert:

1. Event histogram for `weekday_dawn` keys has near-zero CREATE_SPOT /
   JOIN_SPOT counts (plan §2.8 bullet 5).
2. `weekday_evening` is the modal slot for successful CREATE_SPOT events.
3. Since no weekend ticks occur in Phase 1, any event with
   `region_id`-derived `schedule_key` starting with `weekend_` indicates
   a bug in the tick loop.
