# Scoring Audit — Phase 3 placeholder

> 출처: `synthetic_content_pipeline_plan.md` §5 Layer 6
> 소유: `validator-engineer`
> 본 문서는 Phase 1 시점의 placeholder. Phase 3 (`scp_04_val_phase3_complete`)에서 본격 채워진다.

## 1. 가중치 (Plan §5 Layer 6, 변경 금지)

```
quality_score =
    0.25 * naturalness
  + 0.20 * consistency
  + 0.20 * persona_fit
  + 0.15 * region_fit
  + 0.10 * business_rule_fit
  + 0.10 * diversity_score
```

가중치는 yaml 로 빼지 않음. `src/pipeline/validators/scoring.py` (Phase 3) 에서 상수 하드코딩 + docstring 에 본 행 출처 명시.

## 2. 점수 source 매핑 (예정)

| 항목 | 0~1 source | 산정 시점 |
|---|---|---|
| naturalness | critic 응답 `naturalness_score` | Layer 4 (10~20% sampled) — 미샘플 시 기본 0.85 |
| consistency | Layer 1+2 통과율 (1.0 = 모두 pass, reject당 -0.15) | Layer 1~2 종료 직후 |
| persona_fit | critic 응답 `persona_fit_score` | Layer 4 — 미샘플 시 spec.host_persona 매핑 표 0.80 |
| region_fit | rule_region_consistency similarity / 100 | Layer 2 |
| business_rule_fit | rule 1~8 통과율 (rejections=0 → 1.0, 1건 → 0.85, 2건 → 0.70, 3+ → 0.50) | Layer 2 |
| diversity_score | Layer 5 n-gram + TF-IDF 통과 시 1.0, n-gram 위반 -0.2, TF-IDF 위반 -0.3 | Layer 5 |

## 3. 판정 임계 (Plan §5 Layer 6 표)

| 점수 | 판정 | 후속 |
|---|---|---|
| ≥ 0.80 | 승인 | publish 큐로 |
| 0.65 ~ 0.79 | 조건부 | critic 강제 호출 → 재판정 |
| < 0.65 | reject | rejection feedback과 함께 generator 루프로 (최대 2회) |

## 4. Phase 1 placeholder 정책

Phase 1 에서는 score 산정 자체를 하지 않는다. `content_validation_log.score` 컬럼은 NULL 로 두고, `status` 만 `passed/failed/warning` 으로 채운다 (`validate_individual` job 동작 그대로).

Phase 3 진입 시 위 매핑을 코드화하고, 본 문서를 "확정" 으로 promote 한다.

## 5. 경계값 시뮬레이션 (Phase 3 작성 예정)

- 모든 rule pass + critic naturalness 0.80 → quality_score ?
- region_fit 0.74 (경계) + 나머지 1.0 → 합계?
- business_rule_fit 0.70 (rule 2건 reject) + 나머지 1.0 → 0.65 cutoff 아래/위 어디?

(Phase 3에서 채움.)
