# Job Contract — 10개 job 입출력 + 의존성

> 각 job은 `pipeline <sub-command>` 로 호출 가능. 본 문서는 **데이터 흐름 계약**.

## 의존성 그래프

```
        ┌────────────────────────┐
        │ 1. build_content_spec  │  (input: event_log.jsonl)
        │  owner: infra          │
        └──────────┬─────────────┘
                   │ ContentSpec
       ┌───────────┼───────────┬───────────┐
       ▼           ▼           ▼           ▼
  ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ 2 feed │ │ 3 detail │ │ 4 msgs   │ │ 5 reviews│
  │ gen    │ │  gen     │ │  gen     │ │  gen     │
  │ owner: │ │ owner:   │ │ owner:   │ │ owner:   │
  │ generator│generator │ │ generator│ │generator │
  └───┬────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
      │           │            │            │
      └────┬──────┴────────────┴────────────┘
           ▼ (각 후보 row를 4개 synthetic_* 테이블에 insert)
   ┌──────────────────────────┐
   │ 6. validate_individual   │  per-content schema/rule
   │  owner: validator        │
   └─────────────┬────────────┘
                 ▼
   ┌──────────────────────────┐
   │ 7. validate_cross_ref    │  spot 단위 정합성
   │  owner: validator        │
   └─────────────┬────────────┘
                 ▼
   ┌──────────────────────────┐
   │ 8. evaluate_critic       │  10~20% 샘플링
   │  owner: validator        │
   └─────────────┬────────────┘
                 ▼
   ┌──────────────────────────┐
   │ 9. score_and_approve     │  Layer 6 scoring
   │  owner: validator        │
   └─────────────┬────────────┘
                 ▼
   ┌──────────────────────────┐
   │ 10. publish              │  active 플래그 + version switch
   │  owner: infra            │
   └──────────────────────────┘
```

## Job별 입출력

| # | command | owner | input | output | DB write | DB read |
|---|---|---|---|---|---|---|
| 1 | `build-content-spec` | infra | event_log path, spot_id | ContentSpec(JSON) | — | — |
| 2 | `generate-feed` | content-generator-engineer | ContentSpec | 후보 ×2 | synthetic_feed_content | — |
| 3 | `generate-detail` | content-generator-engineer | ContentSpec | 후보 ×2 | synthetic_spot_detail | — |
| 4 | `generate-messages` | content-generator-engineer | ContentSpec + lifecycle | snippet 4종 ×2 | synthetic_spot_messages | — |
| 5 | `generate-reviews` | content-generator-engineer | ContentSpec.activity_result | review ×2 | synthetic_review | — |
| 6 | `validate-individual` | validator-engineer | spot_id | pass/fail | content_validation_log | synthetic_* |
| 7 | `validate-cross-reference` | validator-engineer | spot_id | pass/fail | content_validation_log | synthetic_* |
| 8 | `evaluate-critic` | validator-engineer | spot_id (sampled) | critic score | content_validation_log | synthetic_* |
| 9 | `score-and-approve` | validator-engineer | spot_id | quality_score, validation_status | synthetic_*.quality_score/validation_status | content_validation_log |
| 10 | `publish` | infra | dataset_version | publish stats | content_version_policy, synthetic_*.validation_status | synthetic_* |

## 호출 순서 (MVP)

```
for spot_id in target_spots:
    pipeline build-content-spec --spot-id $spot_id > spec/$spot_id.json
    pipeline generate-feed       --spot-id $spot_id --dataset-version v1
    pipeline generate-detail     --spot-id $spot_id --dataset-version v1
    pipeline generate-messages   --spot-id $spot_id --dataset-version v1
    pipeline generate-reviews    --spot-id $spot_id --dataset-version v1
    pipeline validate-individual --spot-id $spot_id --dataset-version v1
    pipeline validate-cross-reference --spot-id $spot_id --dataset-version v1
    pipeline evaluate-critic     --spot-id $spot_id --dataset-version v1   # 10~20% 샘플
    pipeline score-and-approve   --spot-id $spot_id --dataset-version v1

pipeline publish --dataset-version v1
```

재시도 루프(Plan §6)는 `score_and_approve` 가 fail 처리한 spot에 대해 generate_* 부터 다시 호출.
재시도는 1회로 제한 (validator-engineer 가 quality_score 산정 시 retry_count 추적).

## 의존성 매트릭스 (단순화)

| job | depends_on |
|---|---|
| 1 build_content_spec | event_log.jsonl |
| 2~5 generators | 1 |
| 6 validate_individual | 2~5 |
| 7 validate_cross_reference | 6 |
| 8 evaluate_critic | 7 (샘플링 통과 시) |
| 9 score_and_approve | 6, 7, (8) |
| 10 publish | 9 |
