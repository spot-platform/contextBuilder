# scp_04_val — Phase 3 delta

Phase 1 (Layer 1 schema), Phase 2 (Layer 2 rule + Layer 3 cross-reference) 이후
Phase 3 에서는 **Layer 4 critic (sampling) + Layer 5 diversity + Layer 6 scoring
+ generate→validate→retry 전체 루프** 를 조립했다.

## 1. 신규 생성 파일

| 경로 | 책임 | layer |
|------|------|-------|
| `src/pipeline/validators/critic.py` | LLM critic 샘플링 + 호출 + fallback | 4 |
| `src/pipeline/validators/diversity.py` | n-gram / TF-IDF / template pattern | 5 |
| `src/pipeline/validators/scoring.py` | quality_score 가중합 + classify | 6 |
| `src/pipeline/loop/__init__.py` | loop 서브패키지 export | — |
| `src/pipeline/loop/generate_validate_retry.py` | §6 전체 루프 | 1~6 |
| `src/pipeline/metrics.py` | §14 thread-local 측정기 | — |
| `src/pipeline/jobs/evaluate_critic.py` | Job 8 CLI (스텁 → 실구현) | 4 |
| `src/pipeline/jobs/score_and_approve.py` | Job 9 CLI (스텁 → 실구현) | 6 |
| `config/prompts/critic/v1.j2` | critic 프롬프트 v1 | 4 |
| `config/weights/scoring_weights.json` | §5 Layer 6 가중치 (감사용 복사본) | 6 |
| `config/weights/critic_sampling_policy.json` | §10 샘플링 정책 | 4 |
| `config/rules/diversity_patterns.yaml` | 템플릿 패턴 시드 | 5 |

## 2. 수정 파일

없음. `generators/`, `llm/retry.py`, `llm/codex_client.py`, 기존 validators
(Phase 1/2) 는 일체 건드리지 않았다.

## 3. 변수 / 가중치 상수 (single source of truth)

```python
SCORING_WEIGHTS = {
    "naturalness": 0.25,
    "consistency": 0.20,
    "persona_fit": 0.20,
    "region_fit": 0.15,
    "business_rule_fit": 0.10,
    "diversity": 0.10,
}
APPROVED_THRESHOLD = 0.80
CONDITIONAL_THRESHOLD = 0.65
```

`config/weights/scoring_weights.json` 은 **감사용 복사본**이다. 코드가 읽는
진짜 값은 `scoring.py` 의 SCORING_WEIGHTS 상수이며, 두 값이 diff 가 나면
플랜 §5 Layer 6 과 함께 동시에 수정해야 한다.

## 4. Critic 샘플링 정책 (§10)

`should_sample_critic` 는 3 종 기준 중 우선순위대로 sampled 판정:

1. `new_category_region` — `batch_stats.seen_category_region` 집합에 없는 조합
2. `boundary_score` — layer123.warnings 가 있거나 retry_count > 0
3. `random_10pct` — `policy.random_rate` (기본 0.10)

`target_overall_rate` 0.15 는 §10 호출 추정표를 맞추는 기준.

## 5. Generation 호출 카운트 caveat

`metrics.record_call("generation", ...)` 은 `process_single_content` 시작 시
콘텐츠 타입당 1 회만 호출된다. **generator 내부(`generate_with_retry`) 가
실제로 codex 를 몇 번 호출했는지는 loop 에서 볼 수 없다** — generator 파일을
수정 금지라서 내부 hook 을 꽂을 수 없기 때문이다.

따라서 `llm_calls_total = type 당 1 + critic 샘플 1` 의 근사값이다.
정확한 호출 수를 측정하려면 후속 phase 에서 `codex_client._invoke_codex` 에
thread-local increment 를 달아야 한다 (pipeline-infra-architect 협의 필요).

재시도 횟수 (`retry_count_total`) 는 `candidate.meta["retry_count"]` 로부터
간접 추정한다. `0/1/2` 삼진 근사값 (plan §13 MVP 한도가 2회이므로 실사용 충분).

## 6. 검증 (스모크)

```
$ SCP_LLM_MODE=stub PYTHONPATH=src python3 -c "...critic..."
naturalness= 0.88 reject= False fallback= False

$ PYTHONPATH=src python3 -c "...compute_diversity..."
[0.9366451877780346, 0.9366451877780346]

$ PYTHONPATH=src python3 -c "...compute_quality_score..."
weights_sum= 1.0 score= 0.875 class= approved

$ SCP_LLM_MODE=stub PYTHONPATH=src python3 -c "...process_single_content..."
class= conditional q= 0.712 critic_used= True reason= new_category_region

$ SCP_LLM_MODE=stub PYTHONPATH=src python3 -c "...process_spot_full..."
approved= False llm_calls= 10 elapsed= 0.071
contents= ['feed', 'detail', 'plan', 'messages', 'review']

$ PYTHONPATH=src python3 -m pytest tests/ -m "not live_codex" -q
100 passed, 3 deselected, 5 xfailed  (회귀 0)
```

stub 모드에서 approved=False 가 나오는 이유: 모든 5 type 이 `default.json`
fixture 를 공유하기 때문에 cross-reference 가 실패한다 (한 스팟에 대한 spec 과
다른 스팟의 stub payload 를 매칭시킴). 실제 live 모드에서는 spec 기반으로
각 fixture 가 생성되므로 문제되지 않는다.
