# critic strategy — §10 4 전략 → 코드 매핑

플랜 §10 "Critic 비용 관리" 의 네 가지 전략 중 MVP 에서 구현되는 것은
**전략 1 (샘플링)** 만이다. 나머지 3 종은 TODO 로 남기고, loop 가 나중에
확장할 수 있도록 훅 지점만 명시해 둔다.

## 전략 1. Critic 샘플링 (구현됨)

- 모듈: `src/pipeline/validators/critic.py`
- 함수: `should_sample_critic(spot_id, content_type, layer123_result, batch_stats, rng, policy)`
- 우선순위:
  1. `new_category_region` — 탐색 목적. `batch_stats.seen_category_region` 이 None 이거나
     현재 조합이 집합에 없으면 무조건 sampled=True.
  2. `boundary_score` — `layer123.warnings` 또는 `batch_stats.retry_count > 0`.
  3. `random_10pct` — `policy.random_rate` (기본 0.10) 에 해당하는 난수 샘플.
- 호출 위치: `process_single_content` — 콘텐츠 타입마다 1 회 호출.
  샘플링된 경우 첫 후보(`primary`)를 critic 에 태운다.

## 전략 2. 배치 생성 (TODO)

- 현 루프는 스팟 단위 직렬 처리. 배치(동일 카테고리/지역 복수 스팟) 파이프라인은
  아직 미구현. `process_spot_full` 가 `approved_cache` 인자를 받는 이유가
  이 자리에 배치 누적 캐시를 꽂기 위한 것이다.
- TODO: `JobRunner` 층에서 batch 단위로 system prompt 재사용. codex-bridge
  이 prompt_loader 재사용 API 를 추가해야 함.

## 전략 3. Critic 캐싱 (TODO)

- 현재는 critic 호출 결과를 캐시에 저장하지 않음. stub 모드는 `codex_client._load_stub_response`
  가 고정 파일을 반환하므로 사실상 자동 캐시다.
- TODO: live 모드에서 (`category`, `region`, `spot_type`) 3-tuple 단위로 최근
  critic 결과를 유지. 같은 조합이 3 회 연속 `sampled=False` 로 흘러가면
  샘플 비율을 50% 감소.
- 훅: `critic.load_critic_sampling_policy` 의 정책 파일에 `cache_decay_after`
  같은 필드를 추가하면 확장 가능.

## 전략 4. 경량 critic 모델 (TODO)

- `critic.evaluate_critic` 는 `SCP_CODEX_MODEL_CRITIC` env 로 모델을 스위치 가능.
  현재는 단일 모델 (기본 `gpt-5-codex`) 만 사용.
- TODO: `eval_focus` 에 따라 heavy(=신규 카테고리) vs light(=자연스러움 only)
  두 경로 구분. light 는 safety 체크 생략, heavy 는 현 5 항목 유지.

## 샘플링 호출 수 상한 (§10 추정표 대비)

- MVP 목표: 500 스팟 × 5 type × 0.15 = 375 critic 호출.
- 현 `process_spot_full` 는 critic 을 content type 마다 최대 1 회 호출하므로
  스팟당 상한 5 회. 500 스팟 × 15% sampled 비율 = 목표와 일치.
- 배치 전체에서 `new_category_region` 이 몰리면 상한 초과 가능. loop 가
  `batch_stats.seen_category_region` 을 누적하므로 동일 스팟 안에서 중복 호출은
  없다. 배치 간에는 호출자가 seen 집합을 누적 전달해야 한다.
