# boundary_audit_phase2.md — pipeline-qa Phase 2

Phase 1 boundary audit (pair 1, 2, 3) 는 `qa_boundary_audit.py` 가 별도로 다룬다. 이 파일은 Phase 2 추가 pair 4, 5 결과만 포함한다. 5쌍 합산 PASS 여부는 `phase2_report.md` §2 의 통합 표를 참고.

### Pair 4. validators/<ct>_rules.py payload 키 ↔ <ct>.json schema ↔ stub default fixture

| content_type | rules.py 키 | schema 키 | fixture 키 | rules∖schema | fixture∖schema |
|---|---|---|---|---|---|
| `detail` | `['cost_breakdown', 'description', 'host_intro', 'materials', 'policy_notes']` | `['activity_purpose', 'cost_breakdown', 'description', 'host_intro', 'materials', 'policy_notes', 'progress_style', 'target_audience', 'title']` | `['activity_purpose', 'cost_breakdown', 'description', 'host_intro', 'materials', 'policy_notes', 'progress_style', 'target_audience', 'title']` | `[]` | `[]` |
| `plan` | `['steps', 'total_duration_minutes']` | `['steps', 'total_duration_minutes']` | `['steps', 'total_duration_minutes']` | `[]` | `[]` |
| `messages` | `['day_of_notice', 'recruiting_intro']` | `['day_of_notice', 'join_approval', 'post_thanks', 'recruiting_intro']` | `['day_of_notice', 'join_approval', 'post_thanks', 'recruiting_intro']` | `[]` | `[]` |
| `review` | `['meta', 'rating', 'review_text', 'satisfaction_tags', 'sentiment', 'will_rejoin']` | `['rating', 'recommend', 'review_text', 'satisfaction_tags', 'sentiment', 'will_rejoin']` | `['rating', 'recommend', 'review_text', 'satisfaction_tags', 'sentiment', 'will_rejoin']` | `[]` | `[]` |

**Pair 4 verdict: PASS**

- review: rules.py 가 schema 바깥 metadata key 사용 (info — allowlist): `['meta']` → 운영 LLM 응답에는 없으며, generator runner 가 주입할 때만 동작하는 optional rule. phase2_delta.md 에 명시되어야 함.

### Pair 5. cross_reference.py content type 필드 접근 ↔ 각 schema ↔ dispatch.CONTENT_TYPE_VALIDATOR

| content_type | cross_ref 접근 키 | schema 키 | accessed∖schema |
|---|---|---|---|
| `feed` | `['price_label', 'region_label', 'status', 'supporter_label', 'time_label']` | `['price_label', 'region_label', 'status', 'summary', 'supporter_label', 'tags', 'time_label', 'title']` | `[]` |
| `detail` | `['cost_breakdown', 'host_intro', 'materials']` | `['activity_purpose', 'cost_breakdown', 'description', 'host_intro', 'materials', 'policy_notes', 'progress_style', 'target_audience', 'title']` | `[]` |
| `plan` | `['steps']` | `['steps', 'total_duration_minutes']` | `[]` |
| `messages` | `['day_of_notice', 'recruiting_intro']` | `['day_of_notice', 'join_approval', 'post_thanks', 'recruiting_intro']` | `[]` |
| `review` | `['review_text', 'sentiment']` | `['rating', 'recommend', 'review_text', 'satisfaction_tags', 'sentiment', 'will_rejoin']` | `[]` |

| dispatch.CONTENT_TYPE_VALIDATOR | `['detail', 'feed', 'messages', 'plan', 'review']` |  |  |
| dispatch.CONTENT_TYPE_SCHEMA    | `['detail', 'feed', 'messages', 'plan', 'review']` |  |  |

**Pair 5 verdict: PASS**
