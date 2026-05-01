# RAGAS Feed Evaluation: Direct LLM vs contextBuilder

이 문서는 논문 실험에 넣기 위한 `Direct LLM` baseline과 제안 방식(`contextBuilder + LLM`) 비교 절차를 정의한다.

## 평가 framing

본 평가는 피드의 주관적 매력도를 직접 측정하기보다, 생성된 피드가 주어진 지역/시뮬레이션/큐레이션 context에 얼마나 충실하고 관련성 있게 생성되었는지를 RAGAS로 측정한다.

- `question`: 사용자 시나리오 / 피드 생성 요청
- `contexts`: contextBuilder의 중간 산출물
  - 지역/활동 속성
  - 시뮬레이션 기반 제약
  - 호스트 persona/taste
  - POI 큐레이션 후보
  - plan/price/preparation draft
- `answer`: 최종 feed JSON을 평탄화한 텍스트

두 시스템 모두 동일한 `question`과 동일한 `contexts`로 RAGAS 평가를 받는다. 차이는 생성 단계에서 Direct LLM은 “수원시에 올라갈 로컬 취미/모임 피드 100개를 만들어줘” 수준의 도시 범위와 출력 구조만 받고 긴 피드 출력을 요구받는 반면, contextBuilder는 구조화된 context를 기반으로 긴 피드를 만든다는 점이다. 즉 baseline을 일부러 짧게 두지 않되, 세부 조건은 주지 않아 제안 구조의 grounding 효과를 비교한다.

## 비교군

1. `direct_llm`
   - `config/prompts/direct_feed/v1.j2`
   - “수원시에 올라갈 로컬 취미/모임 피드 100개”라는 도시 단위 요청과 feed JSON 구조만 제공
   - 스킬/인원/일정/가격/POI/취향/시뮬레이션 context는 제공하지 않음
   - summary 220~360자, tags 4~6개 등 긴 피드 생성을 명시적으로 요구

2. `context_builder`
   - 기존 `FeedGenerator` / `feed:v2`
   - ContentSpec, POI anchors, plan steps, price/preparation 등의 structured context 사용

## 생성 데이터셋 만들기

Stub 모드:

```bash
cd synthetic-content-pipeline
python scripts/evaluate_feed_ragas.py build \
  --out _workspace/ragas/feed_eval.jsonl
```

Live Codex 모드:

```bash
cd synthetic-content-pipeline
SCP_LLM_MODE=live python scripts/evaluate_feed_ragas.py build \
  --out _workspace/ragas/feed_eval.jsonl
```

여러 케이스를 평가하려면 ContentSpec JSON object list를 준비해서 넘긴다.

```bash
python scripts/evaluate_feed_ragas.py build \
  --spec-json data/eval/feed_specs.json \
  --out _workspace/ragas/feed_eval.jsonl
```

논문용 100개 케이스 생성/빌드 예시:

```bash
cd synthetic-content-pipeline

uv run --extra dev python scripts/build_eval_specs.py \
  --n 100 \
  --out data/eval/feed_specs_100.json

SCP_LLM_MODE=live uv run --extra dev python scripts/evaluate_feed_ragas.py build \
  --spec-json data/eval/feed_specs_100.json \
  --out _workspace/ragas/feed_eval_100.jsonl
```

## RAGAS 실행

Evaluator dependency 설치:

```bash
pip install -e '.[eval]'
```

RAGAS 실행(OpenAI evaluator 기본값):

```bash
python scripts/evaluate_feed_ragas.py ragas \
  --input _workspace/ragas/feed_eval.jsonl \
  --out _workspace/ragas/feed_eval_report.json
```

100개 케이스 RAGAS 실행:

```bash
export ANTHROPIC_API_KEY="..."

uv run --extra eval python scripts/evaluate_feed_ragas.py ragas \
  --provider anthropic \
  --model claude-3-5-haiku-latest \
  --input _workspace/ragas/feed_eval_100.jsonl \
  --out _workspace/ragas/feed_eval_100_report.json
```

100개 케이스 문체/템플릿 지표 실행:

```bash
uv run --extra dev python scripts/evaluate_feed_ragas.py style \
  --input _workspace/ragas/feed_eval_100.jsonl \
  --out _workspace/ragas/feed_style_100_report.json
```

Claude/Anthropic evaluator 실행:

```bash
export ANTHROPIC_API_KEY="..."
python scripts/evaluate_feed_ragas.py ragas \
  --provider anthropic \
  --model claude-3-5-haiku-latest \
  --input _workspace/ragas/feed_eval.jsonl \
  --out _workspace/ragas/feed_eval_report.json
```

기본 지표:

- `faithfulness`: 생성 피드가 제공 context에 근거하는지
- `context_precision_without_reference` / RAGAS `LLMContextPrecisionWithoutReference`: 제공된 context chunk가 reference answer 없이 최종 피드 생성에 유효했는지

선택 지표:

- `answer_relevancy`: 사용자 시나리오에 관련성 있게 답하는지. RAGAS 구현상 embedding evaluator가 추가로 필요할 수 있으므로, Claude-only 실행에서는 기본값에서 제외한다.

## 논문용 문장 초안

> We evaluated generated feed cards using RAGAS, a reference-free evaluation framework for retrieval-augmented generation. The Direct LLM baseline was prompted to produce long, human-like feed cards from a city-level request only (e.g., generating local community feed cards for Suwon) and the target feed JSON structure, while the proposed contextBuilder-based generator produced long feed cards from structured intermediate artifacts. These artifacts, including local attributes, simulation-derived constraints, host persona/taste profiles, curated POI candidates, and drafted plan/price/preparation contexts, were treated as retrieval contexts. We report Faithfulness and reference-free LLM Context Precision to compare city-only long-form Direct LLM generation with the proposed contextBuilder-based generation. When answer relevancy is included, we additionally report the evaluator and embedding backend used for the metric.

## 주의

RAGAS는 `재미있는 피드인가`를 직접 평가하지 않는다. 논문에서는 `context-grounded feed generation quality`를 평가했다고 표현하는 것이 안전하다.
