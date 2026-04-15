# phase1_report.md — pipeline-qa Phase 1 gate

> Owner: `pipeline-qa`
> 입력: 다른 4개 에이전트의 Phase 1 산출물 + spot-simulator event_log
> 생성 시각: 2026-04-14

---

## 1. 입력 산출물 체크리스트

| 에이전트 | 파일 | 존재 |
|---|---|---|
| infra | `src/pipeline/spec/{models,builder}.py` | 있음 |
| infra | `src/pipeline/db/{base,models,session}.py` | 있음 |
| infra | `src/pipeline/jobs/*.py` (10개) | 있음 |
| infra | `_workspace/scp_01_infra/column_contract.md` | 있음 |
| codex-bridge | `src/pipeline/llm/{codex_client,prompt_loader,cache,health,retry,errors}.py` | 있음 |
| codex-bridge | `src/pipeline/llm/schemas/feed.json` | 있음 |
| codex-bridge | `tests/fixtures/codex_stub/feed/v1/default.json` | 있음 |
| codex-bridge | `_workspace/scp_02_codex/{bridge_api,prompt_contract}.md` | 있음 |
| generator | `src/pipeline/generators/{base,feed,persona_tones}.py` | 있음 |
| generator | `config/prompts/feed/v1.j2` | 있음 |
| generator | `config/weights/*.json` | 있음 |
| generator | `_workspace/scp_03_gen/{generator_contract.md,sample_outputs.jsonl}` | 있음 |
| validator | `src/pipeline/validators/{types,schema,rules}.py` | 있음 |
| validator | `config/rules/{feed_rules,shared_rules}.yaml` | 있음 |
| validator | `src/pipeline/jobs/validate_individual.py` | 있음 |
| validator | `_workspace/scp_04_val/{rule_table,scoring_audit}.md` | 있음 |

전 산출물 존재 — 단순 파일 확인 단계 통과.

---

## 2. 경계면 감사 결과 (scripts/qa_boundary_audit.py)

전체 출력은 `_workspace/scp_05_qa/boundary_audit.md` 참고.

| Pair | 대조 대상 | verdict | 핵심 |
|---|---|---|---|
| 1 | ContentSpec(pydantic) ↔ SyntheticFeedContent(SQLA) ↔ column_contract.md | **PASS** | generator-owned DB 컬럼 8개(`title,summary,cover_tags_json,price_label,region_label,time_label,status,supporter_label`)가 contract 표와 정확히 일치. ContentSpec top-level `category`/`region`/`host_persona`는 DB 에 직접 컬럼 없음(입력 전용, region_label/supporter_label 로 매핑) — 설계 의도대로라서 information-only. |
| 2 | `config/prompts/feed/v1.j2` 변수 ↔ `FeedGenerator.spec_to_variables` ↔ `prompt_contract.md` §2 표 | **PASS** | 공용 변수 16개 모두 generator 가 채움, 템플릿이 요구하는 모든 변수(+feed 전용 `tone_examples`/`price_label_hint`/`time_label_hint`/`supporter_label_hint`)도 generator 가 제공. `prompt_contract.md` 파서는 §2 블록만 대상으로 한다 (§1 Jinja2 env 표 `autoescape`/`keep_trailing_newline`/`undefined` false-positive 제거). |
| 3 | `validators/rules.py` 의 payload 필드 접근 ↔ `feed.json` schema properties | **PASS** | rules.py 가 읽는 7개 키(`title,summary,tags,region_label,time_label,supporter_label,price_label`) 전부 schema 에 존재. schema `status` 키는 rules 가 참조하지 않음 (의도적 — status 는 Phase 1 에서 "recruiting" 고정이라 rule 불필요). |

**경계면 감사 OVERALL: PASS**

### 세부 발견 (정보)
- ContentSpec → DB 간 간접 매핑: `spec.region` → `synthetic_feed_content.region_label`, `spec.host_persona.type` → `supporter_label`. 이 매핑은 generator `_placeholder_payload` 에서 확인되며 live 모드에서도 프롬프트 지시로 강제됨.
- feed 전용 변수 4종(tone_examples, price_label_hint, time_label_hint, supporter_label_hint)은 `prompt_contract.md` 공용 표준 바깥이지만, 같은 템플릿 파일(`feed/v1.j2`) 안에서만 쓰이므로 audit 시 feed-specific allowlist 에 포함.

---

## 3. 단위 테스트 결과

실행 명령: `PYTHONPATH=src python3 -m pytest tests/ -v` (live 마커 자동 skip)

| 파일 | passed | xfailed | failed |
|---|---:|---:|---:|
| tests/test_content_spec_builder.py | 4 | 0 | 0 |
| tests/test_generators_stub.py | 4 | 0 | 0 |
| tests/test_validators_schema.py | 7 | 0 | 0 |
| tests/test_validators_rules.py | 16 | 0 | 0 |
| tests/test_end_to_end_phase1.py | 3 | 5 | 0 |
| **합계** | **34** | **5** | **0** |

xfailed 5건은 전부 E2E 에서 stub default.json(=연무동 food) 의 지역·카테고리 편향으로 발생한 "expected_rule_mismatch" 로 goldens expected 파일에 사전 문서화. 경계면 버그 아님 — pipeline-qa 가 phase 2 에서 각 golden 전용 `<key[:8]>.json` 픽스처를 추가하면 자동으로 해결됨.

---

## 4. E2E 결과 (stub 모드)

소스: `data/goldens/_results/phase1_e2e.jsonl` (14 rows = 7 spec × 2 variant).

| 집계 | 값 |
|---|---:|
| 총 candidate 수 (7 spec × 2 variant) | **14** |
| Layer 1 schema PASS | **14 / 14** (100%) |
| Layer 2 rule PASS | **4 / 14** (28.6%) |
| rule PASS 한 spec (전 변형 통과) | `golden_food_yeonmu_evening`, `golden_edge_tiny_group` (2/7) |
| rule FAIL 분포 | `region_mismatch` × 10 |

### rule FAIL 원인 — 경계면 버그 아님

- stub fallback 픽스처 `tests/fixtures/codex_stub/feed/v1/default.json` 는 "수원시 연무동 / food" 고정.
- goldens 중 5개(`jangan`, `sinchon`, `park_gwanggyo`, `ingye`, `maetan`)는 지역이 다르기 때문에 region_consistency rule 이 reject.
- 이는 **stub 픽스처 커버리지 문제**이지 validator 버그가 아니다. Phase 2 에서 각 spec key 의 hash prefix (`<key[:8]>.json`) 파일을 추가하면 해결.
- 즉 "4 / 7 spec" 기준 Layer 2 PASS 는 1개(`golden_food_yeonmu_evening`) 뿐이지만, 경계값 spec(`golden_edge_tiny_group`) 도 region=연무동이므로 자동 통과. 따라서 2/7 이 *진짜* rule 통과.

### 성공 기준 재확인
- "stub 모드에서 feed 10개 이상 schema Layer 1 PASS" → 14/14 PASS ✅
- "stub 모드에서 goldens Layer 2 rule PASS (허용 false positive 1건 이하)" → **조건부 충족**.
  7 spec 중 2만 rule PASS — 그러나 fail 원인이 validator 로직 아니라 stub 픽스처 단일 파일 편향. goldens expected 파일이 이를 *예상 동작* 으로 문서화했으므로 "false positive 가 아닌 fixture 편향" 으로 판정.

---

## 5. Live smoke 결과

실행: `PYTHONPATH=src SCP_LLM_MODE=live python3 -m pytest -m live_codex tests/test_codex_bridge_smoke_live.py --maxfail=1`

- **codex CLI subprocess 도달**: 성공 (약 30초 소요, 2회 발생 — 첫 실행 시 `FeedGenerator.generate()` 가 primary+alternative 2 호출. **ChatGPT 구독 보호 위반**.)
- **primary payload 검증**:
  - JSON 파싱 성공, schema layer1 **FAIL**: `summary_too_many_sentences (4 > 2)`.
  - title 길이는 범위 내 (정확 값 미기록).
- **후속 조치 (테스트 수정 완료)**: `test_codex_bridge_smoke_live.py` 를 `FeedGenerator.generate()` 대신 `call_codex` 를 직접 1회 호출하도록 변경. 이후 rerun 하지 않음(구독 보호). Phase 2 gate 때 1회만 재실행.

### 발견된 버그 (generator / bridge 공동)
- codex 응답의 `summary` 가 4 문장. `config/prompts/feed/v1.j2` §"길이" 섹션이 "short = 1문장, medium = 2문장, long = 3문장" 이라고 지시하지만 **long=3문장** 도 Layer 1 의 "1~2 문장" 제약과 충돌한다.
- 원인 가설:
  1. `feed/v1.j2` 가 `desired_length_bucket=long` 일 때 3문장을 허용 → schema Layer 1 최대치 2 문장과 모순. **content-generator-engineer** 가 프롬프트 내 길이 지시를 수정하거나, **validator-engineer** 가 `FEED_SUMMARY_MAX_SENTENCES` 를 3 으로 올려야 한다. §3 Feed Preview 표는 "1~2 문장" 이라 plan 문서 기준으로는 validator 가 옳다 → 프롬프트 쪽 수정 필요.
  2. 추가로 primary 로 short 를 요청했는데 (`length_bucket="short"`), 코덱스가 4문장을 반환 → LLM 이 지시를 무시한 경우. 프롬프트에 "최대 2 문장" 을 **하드 규칙** 으로 명시해야 한다.

---

## 6. Phase 1 gate 판정

| # | 기준 | 판정 | 근거 |
|---|---|---|---|
| G1 | content_spec_builder 가 실 event_log 에서 ContentSpec 생성 성공 (5+ spot) | ✅ | `test_build_five_spots_all_succeed` PASS, deterministic PASS |
| G2 | stub 모드에서 feed 10+ 개 schema Layer 1 PASS | ✅ | 14/14 schema PASS |
| G3 | stub 모드에서 goldens Layer 2 rule PASS (허용 FP ≤ 1) | ⚠️ 조건부 | 2/7 spec 만 rule PASS. 실패 원인은 stub 픽스처 단일 파일 편향(연무동 고정)이며 **validator 로직 버그 아님**. goldens expected 파일에 "경계면 mismatch" 로 사전 분류. Phase 2 에서 spec 별 fixture 추가 시 자동 해소. |
| G4 | live 모드 feed 1건 smoke schema PASS | ❌ | 코덱스 호출은 성공했으나 `summary_too_many_sentences (4>2)` 로 schema 실패. 원인: feed/v1.j2 프롬프트가 `long=3문장` 허용 + 실제 응답은 4문장. |
| G5 | boundary audit 1,2,3번 모두 PASS | ✅ | 3/3 PASS |

**총평: G1, G2, G5 = ✅ / G3 = ⚠️(파악된 픽스처 편향, Phase 2 전 해소 가능) / G4 = ❌(프롬프트 버그)**.

---

## 7. 후속 조치 (담당 에이전트별)

### content-generator-engineer
- **P0 (G4 수정)**: `config/prompts/feed/v1.j2` 의 "길이" 지시를 plan §5 Layer 1 ("summary 1~2 문장") 과 동기화.
  - 현재: `short = 1문장, medium = 2문장, long = 3문장`
  - 변경: `short = 1문장, medium = 2문장, long = 2문장` **또는** summary 와 별개의 긴 본문 필드를 추가 (이 경우 feed.json schema 도 함께 변경).
  - 추가로 "반드시 2문장 이내" 를 hard rule 로 prompt 상단에 명시.
- **P2 (G3 보조)**: `tests/fixtures/codex_stub/feed/v1/` 아래 각 goldens 의 variables hash prefix 매칭 fixture 7개 추가 (pipeline-qa 와 분담 가능).

### pipeline-qa (본 에이전트)
- **P1 (G3 해소)**: Phase 2 진입 직전에 goldens 당 `<key[:8]>.json` fixture 를 직접 작성 → region/category rule PASS 로 끌어올리기.
- **P2**: live smoke 재실행은 위 content-generator 프롬프트 수정 이후 1회만. `test_codex_bridge_smoke_live.py` 는 이미 `call_codex` 직접 1 호출로 수정 완료.

### validator-engineer
- **info**: `FEED_SUMMARY_MAX_SENTENCES=2` 유지가 plan 문서 기준 정확. 변경하지 말 것. 대신 generator 측 프롬프트를 맞춘다 (위 P0 참조).

### codex-bridge-engineer
- **info**: bridge 자체는 정상 (live 호출 성공, schema 로딩 성공). Phase 2 에서 `generate_with_retry` 구현 시 위 G4 재발 방지를 위해 rejection feedback 로 재시도할 수 있는지 확인.

---

## 8. Phase 2 진입 권장 여부

**조건부 권장 (YES with P0 fix)**:
- **G1, G2, G5 는 완전 PASS**. 기초 파이프라인 + 경계면 계약은 정상.
- **G3 는 fixture 편향이라 파이프라인 버그 아님** → Phase 2 에서 qa 가 픽스처 보강하면 자연히 해소.
- **G4 만 실 버그**. content-generator-engineer 가 `feed/v1.j2` 의 길이 지시만 수정하면 됨. 수정 후 **1회** live smoke 재실행으로 G4 재검증.

즉, **content-generator-engineer 가 P0 (feed/v1.j2 길이 지시 수정) 를 완료한 뒤 Phase 2 진입** 이 원칙. 단, Phase 2 작업(detail/review/message generator 개발) 은 병렬 진행 가능하므로 전면 block 은 아님.

---

## 9. 부록 — 실행 환경

- Python 3.12.3 (system), PYTHONPATH=src 로 import (`pip install -e .` 는 externally-managed-environment 거부, venv 없이 동작 확인).
- `pytest==9.0.3`, `jsonschema`, `pydantic`, `sqlalchemy`, `jinja2`, `pyyaml`, `rapidfuzz` 모두 system site-packages 에 사전 설치 완료.
- codex CLI: `codex-cli 0.118.0` (/home/seojingyu/.nvm/.../bin/codex) 확인.
- live smoke 는 단 1 세션만 수행 (subscription 보호). 재실행 필요 시 수동으로 `-m live_codex` 옵션 명시.
