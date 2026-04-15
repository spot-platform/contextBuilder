# Phase 3 — §14 Success Metrics (결합)

Phase 3 게이트 판정을 위한 §14 7 지표 측정 결과.

- 측정 스크립트: `scripts/measure_success_metrics.py`
- stub 표본: `data/goldens/specs/*.json` 7 개 (in-process 실행)
- live 표본: `data/goldens/_results/phase3_e2e.jsonl` 3 개 (`pytest -m live_codex tests/test_phase3_live_smoke.py`)

## 1. Stub 모드 (7 goldens, in-process)

| # | 지표 | 목표 | 측정값 | PASS |
|---|------|------|-------|------|
| 1 | 1차 승인률 (no-retry approved) | ≥ 0.70 | 0.1714 (6/35) | ❌ |
| 2 | 최종 승인률 (approved+conditional) | ≥ 0.95 | 1.0 (35/35) | ✅ |
| 3 | 평균 quality_score | ≥ 0.80 | 0.7645 (n=35) | ❌ |
| 4 | 배치 내 diversity (1 − best_score 평균) | ≤ 0.60 | 1.0 | ❌ |
| 5 | 스팟당 LLM 호출 | ≤ 15 | 8.57 | ✅ |
| 6 | 스팟당 소요 시간 (s) | ≤ 30 | 0.054 | ✅ |
| 7 | Critic 비율 | ≤ 0.20 | 0.3167 (19/60) | ❌ |

**합계**: 3/7 통과 (stub)

### Stub 모드 per-spot 분포

| spot_id | calls | critic | retry | elapsed | cross_ref | classifications |
|---------|-------|--------|-------|---------|-----------|-----------------|
| G_CAFE_SINCHON_WEEKEND | 8 | 3 | 4 | 0.057 | FAIL | f:cond d:appr p:cond m:cond r:appr |
| G_CULTURE_DOWNTOWN_EVENING | 7 | 2 | 8 | 0.044 | FAIL | all conditional |
| G_EDGE_TIGHT_BUDGET | 10 | 3 | 17 | 0.071 | FAIL | all conditional |
| G_EDGE_TINY_GROUP | 7 | 2 | 4 | 0.033 | FAIL | f:appr others cond |
| G_EXERCISE_PARK_MORNING | 10 | 3 | 17 | 0.071 | FAIL | all conditional |
| G_FOOD_JANGAN_WEEKDAY | 10 | 3 | 17 | 0.070 | FAIL | all conditional |
| G_FOOD_YEONMU_EVENING | 8 | 3 | 0 | 0.031 | ok | f:appr d:cond p:appr m:appr r:cond |

## 2. Live 모드 (3 goldens, codex exec)

| # | 지표 | 목표 | 측정값 | PASS |
|---|------|------|-------|------|
| 1 | 1차 승인률 | ≥ 0.70 | 0.0 (0/15) | ❌ |
| 2 | 최종 승인률 | ≥ 0.95 | 1.0 (15/15) | ✅ |
| 3 | 평균 quality_score | ≥ 0.80 | 0.6836 | ❌ |
| 4 | 배치 내 diversity | ≤ 0.60 | 0.9643 | ❌ |
| 5 | 스팟당 LLM 호출 | ≤ 15 | 10.0 | ✅ |
| 6 | 스팟당 소요 시간 (s) | ≤ 30 | 39.21 | ❌ |
| 7 | Critic 비율 | ≤ 0.20 | 0.0 (0/30) | ✅ |

**합계**: 3/7 통과 (live)

### Live 모드 per-spot 분포

| spot_id | calls | critic | retry | elapsed | cross_ref | classifications |
|---------|-------|--------|-------|---------|-----------|-----------------|
| G_FOOD_YEONMU_EVENING | 10 | 0 | 20 | 42.343 | FAIL | all conditional |
| G_FOOD_JANGAN_WEEKDAY | 10 | 0 | 20 | 36.042 | FAIL | all conditional |
| G_CAFE_SINCHON_WEEKEND | 10 | 0 | 20 | 39.237 | FAIL | all conditional |

## 3. 공통 Caveats

- **표본 크기 과소**: stub 35 row / live 15 row. 통계적 유의성은 제한적이며,
  실제 매트릭스를 Phase 4 에서 200+ 스팟으로 재측정해야 한다.
- **stub fixture bias**: `tests/fixtures/codex_stub/*/v1/default.json` 1 개 를
  모든 키에 fallback 하므로 5 종 contents 가 동일 텍스트로 채워진다 →
  diversity 지표가 구조적으로 1.0 (완전 중복) 에 붙는다. 이 값은 "알고리즘
  동작 확인" 용이며 stub 기준으로 pass/fail 판정을 내릴 수 없다.
- **critic call counter 근사**: `metrics.record_call` 는 `process_single_content`
  시작 시 generation 1 건, critic 샘플링 1 건만 카운트한다. Generator 내부
  `generate_with_retry` 의 실제 codex 호출 수는 잡히지 않는다 (`phase3_delta §5`).
  따라서 실제 live 호출량은 표기된 10 보다 2~3 배 클 수 있다.
- **cross_reference 광범위 FAIL**: stub 7 / live 3 스팟 모두 cross_ref 가
  FAIL 로 잡힌다 (§3. 원인 분석 참고).

## 4. 미달 지표 공통 원인 분석

### 4-1. 1차 승인률 0% ~ 17% (목표 70%)

- stub: 일부 type 이 approved 로 잡히지만 다수는 conditional.
  conditional 분류는 `compute_quality_score = 0.79` 같이 approved 0.80 문턱을
  *간신히* 못 넘는 데서 발생.
- live: **전 항목 conditional**. 이유는 quality_score 가 일정하게 0.68 로 수렴.
  → deterministic default (critic=None, layer123=ok, diversity=0) 케이스이거나,
  critic fallback + diversity 0 의 조합.
- 근본 원인: live 경로에서 **critic 호출이 전량 fallback** (critic_used=false,
  sample_reason 은 설정) — 즉 codex 가 critic:v1 템플릿으로 스키마-강제 JSON
  응답을 돌려주지 않고 있다. 이 때 `scoring._deterministic_defaults` 가 쓰이고,
  `business_rule_fit` 와 `diversity` 가 반영되어 최종 0.68 수렴.

### 4-2. 평균 quality_score 0.68 ~ 0.76 (목표 0.80)

- stub: 0.7645. critic default 점수(0.85~0.92) 는 받지만 diversity 가 1.0
  → 0.0 (동일 텍스트) 감점으로 0.01~0.04 하락.
- live: 0.6836. 위 4-1 원인으로 critic default 로 고정.

### 4-3. 배치 내 diversity 0.96 ~ 1.0 (목표 ≤ 0.60)

- stub: 픽스처 fallback 으로 동일 텍스트 → 계산상 1.0. 알고리즘 결함이
  아니라 fixture 풀의 편향. Phase 4 에서 키 기반 fixture n 개 준비 필요.
- live: live 응답 자체가 conservative 톤으로 수렴한다는 뜻일 수 있으나
  n=3 표본으로 단정 불가.

### 4-4. Critic 비율 31.7% (stub), 0% (live) — 둘 다 목표 20%

- stub: 모든 spot 이 boundary_score 로 잡혀 critic 이 거의 매번 호출됨.
  사유: generator 가 retry_count > 0 로 반환하여 `boundary_score` 가 거의
  항상 trigger → 샘플링 정책이 사실상 무력화.
- live: critic 호출 자체가 fallback → critic_used=false → 비율 0.
  즉 "예산 절감" 이라기보다는 "critic 경로가 동작하지 않는다" 는 알람.

### 4-5. cross_reference FAIL (거의 전체)

- Layer 3 cross_reference 가 FAIL 이지만, Layer 6 classification 은 conditional
  까지 통과. 이는 `process_spot_full` 재생성 루프에서 `cross_ref_result.ok` 를
  반영한 뒤에도 반박되지 않았다는 뜻 — Layer 3 이 너무 엄격하거나,
  generator 출력이 region/category 라벨을 spec 과 정확히 못 맞추고 있다는 신호.
- 연관 에이전트: `validator-engineer` 의 `cross_reference.py` 규칙 재조정
  또는 `content-generator-engineer` 의 region 라벨 정합성 개선.

### 4-6. 스팟당 시간 39 s (live) — 목표 30 s

- 스팟당 평균 10 call × ≈ 4 s ≈ 40 s. codex exec 콜 타임 단축은
  infra 쪽 (timeout / 병렬도) 작업으로만 해결 가능. 병렬화 권장.

## 5. 원인-별 액션 제안

| 증상 | 에이전트 | 파일 | 제안 |
|------|---------|------|------|
| critic 호출이 live 에서 전량 fallback | codex-bridge-engineer | `src/pipeline/llm/codex_client.py`, `config/prompts/critic/v1.j2` | critic 템플릿 rendering / schema 출력 검증, `_resolve_critic_model` 로 해결되는 모델 ID 확인 |
| diversity_patterns.yaml parse 실패 (silent) | validator-engineer | `config/rules/diversity_patterns.yaml` | description 값의 `"..." 구조` 문자열이 YAML scalar 오류. 따옴표 이스케이프 또는 block style 사용 |
| quality_score=0.68 고정 | validator-engineer | `src/pipeline/validators/scoring.py` `_deterministic_defaults` | critic fallback 시 consistency 0.90 대신 layer_rule 신호 기반으로 재계산 (또는 conditional 임계값 하향 검토는 플랜 수정 필요) |
| retry_count_total=20 (spot 당) | content-generator-engineer | `src/pipeline/generators/base.py` `generate_with_retry` | retry 횟수 2 회 상한이 5 type × 2 후보 × 2 retry = 20 로 "거의 매 후보 재시도 exhausted" 의미. rejection feedback 경로 점검 |
| cross_reference FAIL 대량 | validator-engineer | `src/pipeline/validators/cross_reference.py`, `config/rules/cross_reference.yaml` | 규칙 완화 또는 generator 실제 payload shape 와 field 이름 맞추기 |
| stub diversity 0 | pipeline-qa | `tests/fixtures/codex_stub/*/v1/` | 후속 Phase: 각 content type 당 2~3 개 variant fixture 추가 |
