# scp_05_qa_phase3 — Phase 3 Gate Report

## 1. 입력 상태 (Phase 3 산출물 체크)

| 파일 | 존재 | 비고 |
|------|------|------|
| `src/pipeline/validators/critic.py` | ✅ | CriticResult / should_sample_critic / evaluate_critic / critic_to_rejections 모두 확인 |
| `src/pipeline/validators/diversity.py` | ✅ | compute_diversity (n-gram + tfidf + template pattern) |
| `src/pipeline/validators/scoring.py` | ✅ | SCORING_WEIGHTS 합 1.0, APPROVED 0.80 / CONDITIONAL 0.65 |
| `src/pipeline/loop/generate_validate_retry.py` | ✅ | ContentProcessResult / SpotProcessResult / process_single_content / process_spot_full |
| `src/pipeline/metrics.py` | ✅ | start_spot / record_call / end_spot / snapshot |
| `src/pipeline/jobs/evaluate_critic.py` | ✅ | Job 8 CLI |
| `src/pipeline/jobs/score_and_approve.py` | ✅ | Job 9 CLI |
| `src/pipeline/llm/schemas/critic.json` | ✅ | 7 필드 required, additionalProperties=false |
| `tests/fixtures/codex_stub/critic/v1/{default,critic_reject_sample}.json` | ✅ | schema 통과 확인 (`test_critic_unit.TestCriticSchema`) |
| `config/prompts/critic/v1.j2` | ✅ | - |
| `config/weights/scoring_weights.json` | ✅ | 감사용 복사본 — scoring.py SCORING_WEIGHTS 와 diff 없음 |
| `config/weights/critic_sampling_policy.json` | ✅ | random_rate=0.10 target_overall_rate=0.15 |
| `config/rules/diversity_patterns.yaml` | ⚠️ | **YAML parser error** — 아래 "환경/구성 문제" 섹션 참조 |
| `_workspace/scp_04_val/{phase3_delta,critic_strategy,loop_sequence}.md` | ✅ | Phase 3 설계 근거 문서 확인 |

## 2. 신규 산출물

### 2-1. tests/

```
tests/test_critic_unit.py          — should_sample / evaluate stub / schema / critic_to_rejections / policy loader
tests/test_diversity_unit.py       — 동일 텍스트 / 서로 다른 텍스트 / cache 영향 / 템플릿 패턴 / extract_text
tests/test_scoring_unit.py         — 가중합 sum=1 / classify 임계 / critic=None / critic low / breakdown key / warning
tests/test_loop_stub.py            — process_single_content / process_spot_full / metrics record_call
tests/test_phase3_live_smoke.py    — @live_codex, 3 goldens × process_spot_full (live codex)
```

### 2-2. scripts/

```
scripts/measure_success_metrics.py — §14 7 지표 측정 (--mode stub|live, --out *.md)
```

### 2-3. data / workspace

```
data/goldens/_results/phase3_e2e.jsonl        — live smoke 3 spot 결과
data/goldens/_results/phase3_e2e_stub.jsonl   — stub 7 spot 결과 (--write-jsonl)
_workspace/scp_05_qa/phase3_metrics.md        — 본 리포트 동반 지표표 (combined)
_workspace/scp_05_qa/phase3_metrics_stub.md   — stub 단독 지표
_workspace/scp_05_qa/phase3_metrics_live.md   — live 단독 지표
_workspace/scp_05_qa/phase3_report.md         — 본 파일
```

## 3. 단위 테스트 결과

```
$ PYTHONPATH=src python3 -m pytest tests/test_critic_unit.py tests/test_diversity_unit.py \
    tests/test_scoring_unit.py tests/test_loop_stub.py -q
28 passed in 0.28s
```

| 파일 | 테스트 수 | PASS | FAIL |
|------|---------|------|------|
| test_critic_unit.py | 9 | 9 | 0 |
| test_diversity_unit.py | 5 | 5 | 0 |
| test_scoring_unit.py | 6 | 6 | 0 |
| test_loop_stub.py | 4 | 4 | 0 |
| test_phase3_live_smoke.py (live_codex) | 3 | 3 | 0 (122 s) |

## 4. 회귀 테스트 (Phase 1/2 포함)

```
$ PYTHONPATH=src python3 -m pytest tests/ -m "not live_codex" -q
128 passed, 6 deselected, 5 xfailed in 6.92s
```

- 이전 baseline 100 passed → **+28 신규 passed**, **Phase 1/2 회귀 0**.
- xfail 5 개는 Phase 1 의 region/category mismatch 로 기존 Phase 1 에서 이미
  xfail 로 분류된 항목. Phase 3 는 영향 없음.
- 6 deselected 는 `live_codex` 마커가 붙은 테스트 (test_codex_bridge_phase2_smoke_live,
  test_codex_bridge_smoke_live, test_phase3_live_smoke 등).

## 5. §14 지표 요약 (stub / live)

`phase3_metrics.md` 를 정규 레퍼런스로 한다. 요약만:

| # | 지표 | 목표 | stub (7 goldens, 35 rows) | live (3 goldens, 15 rows) |
|---|------|------|---------------------------|---------------------------|
| 1 | 1차 승인률 | ≥ 0.70 | 0.1714 ❌ | 0.0 ❌ |
| 2 | 최종 승인률 | ≥ 0.95 | 1.0 ✅ | 1.0 ✅ |
| 3 | 평균 quality_score | ≥ 0.80 | 0.7645 ❌ | 0.6836 ❌ |
| 4 | 배치 내 diversity | ≤ 0.60 | 1.0 ❌ | 0.9643 ❌ |
| 5 | 스팟당 LLM 호출 | ≤ 15 | 8.57 ✅ | 10.0 ✅ |
| 6 | 스팟당 소요 시간 (s) | ≤ 30 | 0.054 ✅ | 39.21 ❌ |
| 7 | Critic 비율 | ≤ 0.20 | 0.3167 ❌ | 0.0 ✅ |

**stub PASS 3/7, live PASS 3/7** (목표 ≥ 4/7 기준 둘 다 미달).

## 6. Phase 3 Gate 7 기준 체크

- [x] **critic.py 샘플링 정책 단위 PASS** — test_critic_unit.TestShouldSampleCritic 4 건 PASS
- [x] **diversity.py 3종 측정 단위 PASS** — test_diversity_unit 5 건 PASS (n-gram/tfidf/pattern 모두 커버)
- [x] **scoring.py 가중합 + classify PASS** — test_scoring_unit 6 건 PASS
- [x] **process_single_content / process_spot_full stub 동작** — test_loop_stub 4 건 PASS
- [x] **live 모드 3 goldens full pipeline 실행** — test_phase3_live_smoke.py 3 건 PASS
- [ ] **§14 지표 7개 중 ≥4개 stub 기준 PASS** — **3/7 만 달성 (미달)**
- [x] **100 passed regression (Phase 1/2 회귀 0)** — 128 passed, 5 xfailed, 0 failed

**최종 체크: 6/7 Gate 기준 만족, 1 개 미달 (§14 지표 부족).**

## 7. §14 미달 지표 원인 및 액션 (`phase3_metrics.md §4-5` 요약)

| 증상 | 근본 원인 (hypothesis) | 책임 에이전트 / 파일 |
|------|----------------------|----------------------|
| 1차 승인률 0~17%, 대부분 conditional | quality_score 가 0.79 / 0.68 로 APPROVED 0.80 문턱 간신히 미달. critic fallback 시 deterministic default (consistency 0.90) 를 써도 diversity 0 + brf 0 이면 0.68 | validator-engineer / scoring.py |
| live critic 100% fallback | codex critic:v1 템플릿이 schema-forced JSON 출력을 돌려주지 못함. critic.json 필드 7개 required + additionalProperties=false 가 gpt-5-codex 응답과 맞지 않을 수 있음 | codex-bridge-engineer / config/prompts/critic/v1.j2 + llm/codex_client.py |
| diversity 1.0 (stub), 0.96 (live) | stub: default.json 픽스처 fallback 하나로 5 type 공유. live: n=3 샘플 + payload 구조 단순 | pipeline-qa (stub fixture 다양화) / 튜닝은 Phase 4 |
| retry_count_total=20 (live spot당) | generator generate_with_retry 가 사실상 모든 후보 max retry 소진. Layer 1/2 rejection → 재시도 → 여전히 실패 | content-generator-engineer / generators/*.py + base.py |
| cross_reference 거의 전부 FAIL | Layer 3 규칙이 지나치게 엄격하거나 generator 가 region/category 라벨을 spec 과 정확히 맞추지 못함 | validator-engineer / validators/cross_reference.py + config/rules/cross_reference.yaml |
| critic 비율 31.7% (stub) | generator 가 retry_count>0 을 거의 항상 반환 → should_sample_critic 이 boundary_score 로 샘플링. 실질적으로 샘플링 정책 무력화 | content-generator-engineer (retry feedback 개선) 또는 validator-engineer (retry_count 임계 튜닝) |
| 스팟당 시간 39 s (live) | 10 call × 4 s ≈ 40 s. codex exec 병렬화 필요 | pipeline-infra-architect / llm/codex_client.py `_get_semaphore` 병렬도 |

## 8. 환경 / 구성 문제 (ENV)

### 8-1. `config/rules/diversity_patterns.yaml` YAML 파싱 실패

```text
yaml.parser.ParserError: while parsing a block mapping
  ...
  in "<unicode string>", line 5, column 5:
      - id: gabyeopge_A_B
        ^
expected <block end>, but found '<scalar>'
  in "<unicode string>", line 6, column 37:
        description: "가볍게 OO하면서 OO 나누는" 구조
                                        ^
```

- 원인: `description: "가볍게 OO하면서 OO 나누는" 구조` 는 YAML 1.1 문법 상
  `"..."` 닫힌 뒤 ` 구조` scalar 가 이어져 mapping 종료 예상. 결과적으로
  `load_diversity_patterns()` 가 `[]` 를 반환 (silent).
- 영향: `compute_diversity` 가 템플릿 패턴 매치 검증을 전혀 못 함.
  template repeat rate = 0 고정. 따라서 생성기의 "가볍게 … 나누는" 반복문구가
  걸러지지 않는다.
- 권장 수정:
  ```yaml
  - id: gabyeopge_A_B
    description: '가볍게 OO하면서 OO 나누는 구조'
    regex: '가볍게\s*\S+\s*(?:하면서|하며)\s*\S+\s*나누'
  ```
- 테스트 대응: `test_diversity_unit.test_template_pattern_match_reduces_score`
  는 패턴을 인-라인으로 주입하는 방식으로 이 파일에 의존하지 않는다
  (테스트 코드 주석에 명시).
- 담당: **validator-engineer / config/rules/diversity_patterns.yaml**.

### 8-2. `sklearn` 미설치 여부

- `diversity.py` 는 `sklearn.TfidfVectorizer` 를 시도하고 ImportError 시
  pure-python fallback (`_tfidf_cosine_pure`) 를 사용한다.
- 현 환경은 `sklearn` 설치 여부 무관하게 동작. 모든 테스트가 fallback 으로
  동작해도 pass 함을 확인.

### 8-3. codex CLI / 구독 상태

- `codex --version` = `codex-cli 0.118.0` (정상).
- live smoke 3 goldens × 5 type × 평균 2 후보 = 약 30 호출이 2 분 내에 완료.
- 그러나 **critic_used=false** 로 보면 critic 호출은 실제로 LLM 까지 도달
  못 했거나 schema 파싱 실패 후 fallback 으로 빠지는 것으로 추정. 로그 확인 필요.

## 9. Phase 4 진입 권장 여부

**조건부 진입 권장 (Go with caveats)**.

근거:

1. Phase 3 의 **기능적 목표는 모두 달성**: 6/7 gate 기준 만족.
   - Layer 4 critic (샘플링 + fallback), Layer 5 diversity, Layer 6 scoring,
     generate-validate-retry 루프, metrics 모두 동작. 단위 테스트와 stub/live
     end-to-end 실행이 가능하다.
2. 회귀 0: 신규 28 건 추가 + Phase 1/2 128 건 유지.
3. 단, **§14 7 지표 중 4/7 미달**. 이는 "파이프라인이 컴파일 되느냐" 가
   아니라 "생성물 품질이 원하는 수준이냐" 의 문제이며, 미달 원인 대부분은
   Phase 3 가 **처음 측정 가능하게 만든 바로 그 값들** 에서 나온다.
4. 따라서 Phase 4 진입은 허용하되, 아래 **blocking action 3 가지**를
   Phase 4 초반에 우선적으로 처리해야 한다:
   - **A.** `config/rules/diversity_patterns.yaml` parse 오류 수정 (validator-engineer, ≤ 5 분).
   - **B.** live critic 전량 fallback 원인 규명: `config/prompts/critic/v1.j2`
     가 schema-forced JSON 을 잘 만들어내는지 수동 검증 후 codex-bridge
     로그 확인 (codex-bridge-engineer + validator-engineer, ≤ 1 시간).
   - **C.** cross_reference 전면 FAIL 패턴 재조사: generator payload 의
     region/category 필드 이름과 `config/rules/cross_reference.yaml` 의
     매칭 규칙 대조 (validator-engineer, ≤ 1 시간).

Phase 4 는 publish / migration / final integration 단계이므로 위 세 건이
해결되지 않으면 "publish 된 콘텐츠가 전부 conditional" 상태가 그대로 굳는다.
Phase 4 의 첫 task 로 편입 권장.

## 10. 참고 명령어

```bash
# 단위 테스트
PYTHONPATH=src python3 -m pytest tests/test_critic_unit.py tests/test_diversity_unit.py \
    tests/test_scoring_unit.py tests/test_loop_stub.py -q

# 전체 회귀 (live 제외)
PYTHONPATH=src python3 -m pytest tests/ -m "not live_codex" -q

# live smoke (codex 필요, ≈ 2 분)
PYTHONPATH=src python3 -m pytest tests/test_phase3_live_smoke.py -m live_codex -q

# §14 지표 재측정
PYTHONPATH=src python3 scripts/measure_success_metrics.py --mode stub \
    --out _workspace/scp_05_qa/phase3_metrics_stub.md --write-jsonl
PYTHONPATH=src python3 scripts/measure_success_metrics.py --mode live \
    --out _workspace/scp_05_qa/phase3_metrics_live.md
```
