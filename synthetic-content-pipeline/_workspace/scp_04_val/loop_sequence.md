# loop sequence — §6 흐름 → 함수 호출 시퀀스

`src/pipeline/loop/generate_validate_retry.py` 가 플랜 §6 플로우를 어떻게
함수 호출로 풀었는지 정리.

## 입력 / 출력

- 입력: `(spot_id, ContentSpec, approved_cache, rng?)`
- 출력: `SpotProcessResult`
  - `contents: dict[content_type, ContentProcessResult]`
  - `cross_ref_result: ValidationResult`
  - `llm_calls_total, elapsed_seconds, retry_count_total, approved`

## 호출 시퀀스 (1 스팟)

```
process_spot_full(spot_id, spec, approved_cache, rng)
│
├─ metrics.start_spot(spot_id)
│
├─ for ct in (feed, detail, plan, messages, review):
│   │
│   ├─ process_single_content(spot_id, ct, spec, factory, batch_stats, cache, rng)
│   │   │
│   │   ├─ metrics.record_call("generation", ct)
│   │   ├─ generator = factory()
│   │   ├─ candidates = generator.generate(spec)        ← Layer 1+2 내장 retry 후 2 개
│   │   │     (generators/base.py 가 generate_with_retry 를 이미 수행)
│   │   ├─ for each candidate:
│   │   │     layer123 = run_individual(ct, payload, spec)   ← dispatch (Layer 1+2)
│   │   ├─ diversity_scores = compute_diversity(candidates, ct, approved_cache)
│   │   ├─ (sampled, reason) = should_sample_critic(..., batch_stats, rng, policy)
│   │   ├─ if sampled:
│   │   │     critic_result = evaluate_critic(..., sample_reason=reason)   ← Layer 4
│   │   │     (실패 시 CriticResult.deterministic_default, fallback=True)
│   │   ├─ for each candidate:
│   │   │     (score, breakdown) = compute_quality_score(critic, layer123, div)
│   │   ├─ select best by quality_score
│   │   └─ return ContentProcessResult
│   │
│   └─ batch_stats.seen_category_region.add(category|region)
│
├─ spot_bundle = {ct: selected.payload for ct, cpr in contents.items()}
├─ cross_ref_result = run_cross_reference(spot_bundle, spec)        ← Layer 3
│
├─ if not cross_ref_result.ok:
│       failing_types = rejected_field.split(":")[0]
│       for ct in failing_types:
│           process_single_content(...)       ← 해당 type 만 1 회 재생성
│       run_cross_reference(...)              ← 한 번 더 검증
│
├─ snap = metrics.end_spot()
├─ elapsed_seconds / llm_calls_total / retry_count_total 채우기
├─ approved = all ok 이면서 cross_ref ok
└─ return SpotProcessResult
```

## 재시도 정책

- **Generator 내부 (Layer 1+2)**: `generate_with_retry` 가 최대 2 회 재시도.
  rejection feedback 을 다음 호출의 `previous_rejections` 로 주입.
- **Loop 층 (Layer 3)**: cross-reference 실패 시 **해당 type 만** 1 회 재생성.
  두 번째 실패는 그대로 기록, approved=False.
- **Layer 4~6**: critic 실패는 deterministic fallback 이고 재시도하지 않음.
  scoring 도 재시도 없음 (순수 함수).

## 모든 반환값이 선택적일 때의 폴백 규칙

| 상황 | 동작 |
|------|------|
| generator 예외 | ContentProcessResult(selected=None, score=0.0, classification="rejected") |
| run_individual 예외 | layer123 = ok=True 로 간주 (Layer 3 가 커버) |
| critic 호출 실패 | CriticResult.deterministic_default, fallback=True |
| diversity 계산 예외 | compute_diversity 가 내부에서 sklearn → pure python 폴백 |
| cross_reference 예외 | ValidationResult(ok=True, meta={"error":...}) |

## pipeline-qa 가 §14 지표 측정 시 읽을 메타 필드

`SpotProcessResult.to_dict()` 출력 기준:

- `llm_calls_total` (int) — 스팟당 총 LLM 호출 수 (generation + critic)
- `elapsed_seconds` (float) — 스팟당 생성 소요 시간
- `retry_count_total` (int) — metrics 가 관찰한 재시도 수 근사치
- `approved` (bool) — 최종 승인 여부 (1차 승인률 측정)
- `contents[ct].quality_score` (float) — 스팟당 평균 quality_score 용
- `contents[ct].classification` — approved / conditional / rejected
- `contents[ct].critic_used` (bool) — Critic 비용 비율 측정용
- `contents[ct].candidates_meta[].retry_count` / `retry_exhausted` — 재시도 분포
- `contents[ct].layer_results.diversity` — 배치 내 평균 유사도 측정용
- `cross_ref_result.ok` — cross-reference 성공률 측정용
