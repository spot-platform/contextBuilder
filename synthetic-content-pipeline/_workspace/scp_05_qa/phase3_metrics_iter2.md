# Phase 3 — §14 Success Metrics

- mode: **live**
- source: `data/goldens/_results/phase3_e2e.jsonl`
- spots: 3
- contents (rows): 15

## 지표 표

| # | 지표 | 목표 | 측정값 | PASS |
|---|------|------|-------|------|
| 1 | 1차 승인률 (no-retry approved) | ≥ 0.70 | 0.8667 (13/15) | ✅ |
| 2 | 최종 승인률 (approved+conditional) | ≥ 0.95 | 1.0 (15/15) | ✅ |
| 3 | 평균 quality_score | ≥ 0.80 | 0.8217 (n=15) | ✅ |
| 4 | 배치 내 diversity (1 - score 평균) | ≤ 0.60 | 0.6288 (작을수록 좋음) | ❌ |
| 5 | 스팟당 LLM 호출 | ≤ 15 | 7.667 (total=23) | ✅ |
| 6 | 스팟당 소요 시간 (s) | ≤ 30 | 132.8967 (n=3) | ❌ |
| 7 | Critic 비율 | ≤ 0.20 | 0.0 (0/23) | ✅ |

**합계**: 5/7 통과

## Raw Per-Spot Breakdown

| spot_id | calls | critic | retry | elapsed | cross_ref | per-content classification |
|---------|-------|--------|-------|---------|-----------|----------------------------|
| G_FOOD_YEONMU_EVENING | 7 | 0 | 0 | 111.11 | ok | feed:approved, detail:approved, plan:approved, messages:approved, review:approved |
| G_CAFE_SINCHON_WEEKEND | 8 | 0 | 0 | 137.49 | FAIL | feed:approved, detail:approved, plan:conditional, messages:approved, review:approved |
| G_EXERCISE_PARK_MORNING | 8 | 0 | 0 | 150.09 | ok | feed:approved, detail:approved, plan:conditional, messages:approved, review:approved |

## Caveats

- 표본 크기 n=3 스팟, contents=15 행. 통계적 의미가 작으므로 §14 합격선은 *경향성* 으로 해석한다.
- stub 모드는 critic/generator 모두 픽스처 default.json 으로 동작하므로 diversity 가 매우 낮게 (≈ 동일 텍스트) 측정될 수 있다 — 이 지표는 live 결과로 재측정 권장.
- LLM 호출 카운트는 process_spot_full 내부 metrics.record_call 으로 잡힌다. generator 의 내부 retry 호출은 record_call 에 직접 잡히지 않으므로 live 모드에서는 실제 codex exec 호출 수보다 작게 측정될 수 있다.

