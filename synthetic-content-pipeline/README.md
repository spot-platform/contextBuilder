# synthetic-content-pipeline

시뮬레이션 로그(`spot-simulator/output/event_log.jsonl`)를 5종 synthetic content(feed/detail/messages/review/plan)로 렌더링하고, 6 Layer 검증 후 실서비스 read model에 publish하는 파이프라인. LLM 호출은 `codex exec` subprocess로 수행하며, 본 패키지에는 OpenAI/Anthropic SDK 의존성이 없다.
