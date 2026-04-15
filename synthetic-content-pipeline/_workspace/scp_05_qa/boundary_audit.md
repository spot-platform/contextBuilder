# boundary_audit.md — pipeline-qa Phase 1

### Pair 1. ContentSpec ↔ DB(synthetic_feed_content) ↔ column_contract.md

| 항목 | 값 |
|---|---|
| ContentSpec top-level fields | `['activity_constraints', 'activity_result', 'budget', 'category', 'host_persona', 'participants', 'plan_outline', 'region', 'schedule', 'spot_id', 'spot_type']` |
| DB columns (all) | `['cover_tags_json', 'created_at', 'dataset_version', 'id', 'price_label', 'quality_score', 'region_label', 'spot_id', 'status', 'summary', 'supporter_label', 'time_label', 'title', 'validation_status']` |
| DB columns (generator-owned set) | `['cover_tags_json', 'price_label', 'region_label', 'status', 'summary', 'supporter_label', 'time_label', 'title']` |
| contract.md generator columns | `['cover_tags_json', 'price_label', 'region_label', 'status', 'summary', 'supporter_label', 'time_label', 'title']` |
| generator_db_cols - contract_cols (contract 누락 후보) | `[]` |
| contract_cols - generator_db_cols (DB 누락 후보) | `[]` |
| (info) ContentSpec 필드 중 DB 에 직접 컬럼 없음 | `['category', 'host_persona', 'region']` |

**Pair 1 verdict: PASS**

### Pair 2. feed/v1.j2 ↔ spec_to_variables ↔ prompt_contract.md

| 항목 | 값 |
|---|---|
| Jinja2 템플릿 변수 | `['activity_constraints', 'activity_result', 'budget_cost_per_person', 'budget_price_band', 'category', 'desired_length_bucket', 'host_persona', 'participants_expected_count', 'plan_outline', 'previous_rejections', 'price_label_hint', 'region_label', 'sample_variant', 'schedule_date', 'schedule_day_type', 'schedule_time', 'schedule_time_slot', 'spot_id', 'supporter_label_hint', 'time_label_hint', 'tone_examples']` |
| spec_to_variables 반환 키 | `['activity_constraints', 'activity_result', 'budget_cost_per_person', 'budget_price_band', 'category', 'desired_length_bucket', 'host_persona', 'participants_expected_count', 'plan_outline', 'price_label_hint', 'region_label', 'sample_variant', 'schedule_date', 'schedule_day_type', 'schedule_time', 'schedule_time_slot', 'spot_id', 'supporter_label_hint', 'time_label_hint', 'tone_examples']` |
| prompt_contract.md 공용 변수 | `['activity_constraints', 'activity_result', 'budget_cost_per_person', 'budget_price_band', 'category', 'desired_length_bucket', 'host_persona', 'participants_expected_count', 'plan_outline', 'region_label', 'sample_variant', 'schedule_date', 'schedule_day_type', 'schedule_time', 'schedule_time_slot', 'spot_id']` |
| 템플릿 요구 - generator 제공 (누락 후보) | `[]` |
| generator 반환 - contract 표준 (확장 후보, feed-specific 제외) | `[]` |
| contract - generator (generator 누락 후보) | `[]` |

**Pair 2 verdict: PASS**

### Pair 3. validators/rules.py payload 접근 ↔ feed.json schema properties

| 항목 | 값 |
|---|---|
| rules.py 가 읽는 payload 키 | `['price_label', 'region_label', 'summary', 'supporter_label', 'tags', 'time_label', 'title']` |
| feed.json schema properties | `['price_label', 'region_label', 'status', 'summary', 'supporter_label', 'tags', 'time_label', 'title']` |
| rules.py → schema 에 없는 키 (경고) | `[]` |

**Pair 3 verdict: PASS**
