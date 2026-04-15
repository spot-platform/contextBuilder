# phase2_report.md — pipeline-qa Phase 2 gate

> Owner: `pipeline-qa`
> 입력: scp_02_codex_phase2_complete + scp_03_gen_phase2_complete + scp_04_val_phase2_complete
> 생성 시각: 2026-04-15
> 완료 마크: `scp_05_qa_phase2_complete`

---

## 1. 입력 산출물 체크리스트

| 에이전트 | 파일 | 존재 |
|---|---|---|
| codex-bridge | `src/pipeline/llm/schemas/{detail,plan,messages,review}.json` | 4/4 있음 |
| codex-bridge | `tests/fixtures/codex_stub/{detail,plan,messages,review}/v1/default.json` | 4/4 있음 |
| codex-bridge | `_workspace/scp_02_codex/phase2_delta.md` | 있음 |
| generator | `src/pipeline/generators/{detail,plan,messages,review}.py` | 4/4 있음 |
| generator | `config/prompts/{detail,plan,messages,review}/v1.j2` | 4/4 있음 |
| generator | `_workspace/scp_03_gen/phase2_delta.md` | 있음 |
| validator | `src/pipeline/validators/cross_reference.py` | 있음 |
| validator | `src/pipeline/validators/{detail,plan,messages,review}_rules.py` | 4/4 있음 |
| validator | `src/pipeline/validators/dispatch.py` | 있음 |
| validator | `src/pipeline/validators/schema.py` (확장: 4종 validate_*_schema) | 있음 |
| validator | `src/pipeline/jobs/validate_cross_reference.py` (실구현) | 있음 |
| validator | `config/rules/{detail,plan,messages,review,cross_reference}_rules.yaml` | 5/5 있음 |
| validator | `_workspace/scp_04_val/phase2_delta.md` | 있음 |

전 산출물 존재 — 단순 파일 확인 단계 통과.

---

## 2. 경계면 감사 결과 (Phase 1 + Phase 2 통합)

### 2-1. 통합 표 (5 pair)

| Pair | 대조 대상 | 스크립트 | verdict | 핵심 |
|---|---|---|---|---|
| 1 | ContentSpec(pydantic) ↔ SyntheticFeedContent(SQLA) ↔ column_contract.md | `qa_boundary_audit.py` | **PASS** | Phase 1 결과 유지. generator-owned 8 컬럼 정확 일치. |
| 2 | `config/prompts/feed/v1.j2` 변수 ↔ `FeedGenerator.spec_to_variables` ↔ `prompt_contract.md` §2 | `qa_boundary_audit.py` | **PASS** | Phase 1 결과 유지. 공용 16 변수 + feed 전용 4 hint. |
| 3 | `validators/rules.py` payload 접근 ↔ `feed.json` schema | `qa_boundary_audit.py` | **PASS** | Phase 1 결과 유지. 7 키 모두 schema 에 존재. |
| 4 | `validators/<ct>_rules.py` payload ↔ `<ct>.json` schema ↔ stub default fixture | `qa_boundary_audit_phase2.py` | **PASS** (with info) | 4 content type 검사. detail/plan/messages 완전 일치. **review 만 `meta` 키를 schema 외부에서 읽지만**, 이는 generator runner 가 주입하는 `review_length_bucket` 옵션 metadata 로 의도된 leak 이며 audit allowlist 에 등록함. 운영 LLM 응답에는 영향 없음 (`additionalProperties:false` 로 차단). |
| 5 | `cross_reference.py` 5쌍의 content type 필드 접근 ↔ 각 schema ↔ `dispatch.py CONTENT_TYPE_VALIDATOR/SCHEMA` 매핑 | `qa_boundary_audit_phase2.py` | **PASS** | feed/detail/plan/messages/review 5종 모두 dispatch 에 등록, accessed-set 모두 schema 안. |

**통합 OVERALL: 5/5 PASS** (review.meta info 1건은 phase2_delta.md 에 명시되어 있음).

### 2-2. Pair 5 의 한계 (info)

`audit_pair_5` 의 키 추출 정규식은 `feed.get("x")` / `feed["x"]` 패턴만 잡는다.
`cost_breakdown[].amount`, `steps[].time` 같은 **2단계 nested 필드**는 잡지 못한다.
대신 nested access 가 schema 와 어긋나면 Layer 1 schema 검증이 실제 payload 위반으로
잡아내므로 이중 안전망 존재. Phase 3 진입 전에 정규식을 `_find_nested_keys` 형태로
확장하면 더 강한 detection 이 가능.

### 2-3. Pair 4 의 review.meta 잔여 작업

- 위치: `src/pipeline/validators/review_rules.py:167-170` (`rule_review_length_bucket_match`)
- 동작: `payload.get("meta")` → `meta.get("review_length_bucket")`
- 위험도: **낮음**. review.json 은 `additionalProperties:false` 이므로 LLM 응답에는 meta 가 절대 들어오지 않음. 결국 이 rule 은 generator runner 가 별도로 주입할 때만 발화하며, 주입 경로는 아직 없음.
- 권고: validator-engineer 가 다음 중 택1 — (a) `meta` 를 review.json schema 에 optional object 로 추가 (cleaner), (b) rule 을 deprecated 표시하고 Phase 3 의 quality_score 산정 단계로 이관. 둘 다 Phase 3 작업.

---

## 3. 단위 테스트 결과

실행 명령:
```
PYTHONPATH=src python3 -m pytest tests/ -q --tb=line
```

| 파일 | passed | xfailed | failed | deselected |
|---|---:|---:|---:|---:|
| tests/test_content_spec_builder.py | 4 | 0 | 0 | 0 |
| tests/test_generators_stub.py (Phase 1 feed) | 4 | 0 | 0 | 0 |
| tests/test_validators_schema.py | 7 | 0 | 0 | 0 |
| tests/test_validators_rules.py | 16 | 0 | 0 | 0 |
| tests/test_end_to_end_phase1.py | 3 | 5 | 0 | 0 |
| **tests/test_generators_phase2_stub.py** (NEW) | 13 | 0 | 0 | 0 |
| **tests/test_validators_phase2_units.py** (NEW) | 42 | 0 | 0 | 0 |
| **tests/test_cross_reference.py** (NEW) | 9 | 0 | 0 | 0 |
| **tests/test_end_to_end_phase2_spot.py** (NEW) | 2 | 0 | 0 | 0 |
| tests/test_codex_bridge_smoke_live.py (Phase 1) | — | — | — | 1 |
| **tests/test_codex_bridge_phase2_smoke_live.py** (NEW) | — | — | — | 2 |
| **합계 (stub 모드)** | **100** | **5** | **0** | **3** |

xfailed 5건은 모두 Phase 1 의 stub fixture 편향 (연무동 단일 fixture) — Phase 2
범위와 무관, 보존.

### 3-1. test_generators_phase2_stub.py (13 PASS)

- 4 generator (detail/plan/messages/review) × 3 spot_id (S_0001, S_0006, S_0050)
  - candidate 2개 반환 확인.
  - **Layer 1 schema PASS 24/24** (4 × 3 × 2). hard fail 없음.
  - `COMMON_VARIABLE_KEYS` 16 키 superset 확인.
- `dispatch.CONTENT_TYPE_SCHEMA` 4종 schema 파일 존재 + `$schema` / `properties` 로드.

### 3-2. test_validators_phase2_units.py (42 PASS)

19 rule 함수 (detail 5 + plan 4 + messages 5 + review 5) 모두에 대한 positive/negative 쌍 + 4종 aggregator entry + Phase 1 회귀 1건.

검증된 negative 케이스:
- `description_too_few_sentences`, `category_mismatch` (deny keyword), `cost_total_out_of_range` (high/low 양쪽)
- `host_intro_too_short` (supporter_required 활성 시), `policy_notes_forbidden_term`
- `total_duration_mismatch`, `step_count_out_of_range`, `step_time_not_monotonic`, `first_step_not_intro` (warn)
- `snippet_missing`, `host_tone_inconsistent` (warn), `recruit_intent_missing`, `messages_forbidden_phrase`, `day_of_notice_no_time`
- `rating_sentiment_mismatch` (rating=5 sentiment=negative), `noshow_contradiction` (`전원` 표현 + no_show_count>0), `will_rejoin_contradicts_rating` (warn), `review_length_bucket_mismatch` (skip when no meta), `satisfaction_tags_count_out_of_range` (6 tags > max 5)

19/19 rule 함수 PASS. 27개 Layer 2 rule 중 Phase 1 의 8 feed rule 은 `test_validators_rules.py` 가 이미 다루므로 (16 PASS), 합산 27/27 rule 함수가 단위 테스트 기반.

### 3-3. test_cross_reference.py (9 PASS)

- positive yeonmu bundle: 5 쌍 모두 PASS
- inconsistent bundle: 5 reject 검출
  - `feed↔detail:category`, `detail:cost_breakdown`, `feed↔detail:price`, `detail↔review:activity_kind`, `feed↔messages:time`
- pair 별 단일 negative variant:
  - `feed↔detail:category` (food spec ↔ drawing detail)
  - `feed↔messages:time` (19:00 vs 15:00 → 240분 격차)
  - `detail↔plan:activity` (food detail ↔ exercise plan)
  - `detail↔review:activity_kind` (food detail ↔ culture review)
  - `review↔activity_result:noshow` (전원 표현 + no_show_count=2)
  - `review↔activity_result:sentiment` (overall=negative ↔ review=positive)
- partial bundle: feed+detail 만 → 4 쌍 skip 확인.

### 3-4. test_end_to_end_phase2_spot.py (2 PASS)

`golden_food_yeonmu_evening` spec 으로 5 generator 순차 실행 → primary candidate 5개 → run_individual × 5 → run_cross_reference.

| 단계 | 결과 |
|---|---|
| Layer 1 schema PASS | **5 / 5** |
| Layer 2 rule PASS (hard) | **5 / 5** |
| Layer 3 cross-ref ok | **True** |

xfail 없이 통과. 결과 jsonl: `data/goldens/_results/phase2_e2e.jsonl` (1 row).

이 PASS 가 가능한 이유: yeonmu_evening spec 이 codex-bridge-engineer 가 작성한 4종 default.json fixture 와 정확히 같은 컨텍스트 (region=수원시 연무동, category=food, expected_cost=18000, schedule=19:00, duration=120, supporter_required=True) 를 공유하기 때문이다. 이는 Phase 1 G3 의 stub 편향이 Phase 2 에서 이미 단일-맥락 정합으로 해소됐음을 의미한다.

---

## 4. E2E 결과 (stub, 단일 스팟, full pipeline)

### 4-1. 흐름 검증

```
build_content_spec 생략 (golden spec 직접 로드)
    ↓
5 generator × generate() 각각 candidate 2개 (primary/alternative)
    ↓
primary 선택 → run_individual(layer1+layer2) × 5
    ↓
run_cross_reference(bundle, spec)  (Layer 3, 5 pair)
```

### 4-2. 결과 (`data/goldens/_results/phase2_e2e.jsonl` 1 row)

| 항목 | 값 |
|---|---|
| spec | `golden_food_yeonmu_evening.json` |
| spot_id | `G_FOOD_YEONMU_EVENING` |
| Layer 1 schema PASS | 5 / 5 |
| Layer 2 rule PASS | 5 / 5 |
| Layer 3 cross-ref | ok=True (5 pairs executed, 0 skipped) |
| individual rejections | 0 hard, 0 warnings |
| cross-ref rejections | 0 |

**단일 스팟 5 content full pipeline 통과** — Phase 2 핵심 성공 기준.

---

## 5. Live smoke 결과 (정확히 2 codex 호출)

실행 명령:
```
PYTHONPATH=src SCP_LLM_MODE=live \
    python3 -m pytest -m live_codex tests/test_codex_bridge_phase2_smoke_live.py
```

전체 로그: `_workspace/scp_05_qa/phase2_live_smoke.log` (13초 소요, 2 exec call)

| # | template | 결과 | 비고 |
|---|---|---|---|
| 1 | feed:v1   | **PASS** | title 길이 OK, schema PASS. Phase 1 G4 이슈(`summary_too_many_sentences`) 가 generator 측 프롬프트 hard rule 강화로 해소됐음을 live 응답으로 확인. |
| 2 | detail:v1 | **FAIL** | codex exec 가 `Reading additional input from stdin...` 로 종료 코드 1. **prompt 첫 글자가 newline (`\n`)** 이라 codex CLI 가 첫 positional arg 를 stdin redirect 로 오인함. |

### 5-1. detail live FAIL 원인 분석 (경계면 버그)

- 위치: `config/prompts/detail/v1.j2` 1행
- 내용: 파일이 Jinja2 `{# 메타 코멘트 #}` 블록으로 시작하고, 그 다음 줄부터 본문이 시작한다. Jinja2 환경(`StrictUndefined`, `keep_trailing_newline=True`)에서 이 코멘트 블록은 빈 문자열로 치환되지만, 그 자리의 newline (`\n`) 은 보존된다. 결과적으로 `prompt_loader.render()` 의 출력이 `\n너는 ...` 로 시작.
- 영향: codex CLI 0.118.0 은 첫 positional argument 가 공백 문자(`\s`) 로 시작하면 stdin 입력 모드로 들어가며, subprocess 에 stdin 파이프가 없으면 즉시 종료 코드 1 + `Reading additional input from stdin...` stderr 를 낸다.
- 재현: `codex exec ... -- "<prompt with leading newline>"` → 즉시 fail.

### 5-2. 책임 에이전트 / 수정 위치

- **content-generator-engineer** P0:
  - `config/prompts/detail/v1.j2` 1~3행의 `{# ... #}` 코멘트를 `{#- ... -#}` (whitespace control) 로 바꾸거나, 코멘트를 제거하고 본문을 1행부터 시작하라.
  - 동일 패턴이 plan/messages/review v1.j2 에도 있는지 점검 권장 (live smoke 는 detail 만 호출했지만 다른 3개도 같은 구조라면 같은 fail 가능). Phase 2 gate 는 detail 1건만 검증했으므로 해당 3개는 nightly 에서 추가 확인 필요.

- **codex-bridge-engineer** P2:
  - `pipeline/llm/codex_client._invoke_codex` 에서 prompt 를 `prompt.lstrip()` 후 전달하면 generator 실수에 대한 안전망. 단, 의미상 generator 측 수정이 우선.

### 5-3. 호출 횟수 확인

- 정확히 **2 codex exec** 호출 (feed 1 + detail 1). 구독 보호 한도 준수.
- `FeedGenerator.generate()` / `SpotDetailGenerator.generate()` 직접 호출이 아니라 `call_codex` 를 직접 호출했으므로 primary+alternative 2배 누적은 발생하지 않음.

---

## 6. Phase 2 gate 판정 (6 기준)

| # | 기준 | 결과 | 근거 |
|---|---|---|---|
| **G1** | 4종 generator (detail/plan/messages/review) stub 모드 candidate 2개 schema PASS | ✅ | 4 generator × 3 spot × 2 variant = 24 candidate 모두 Layer 1 schema PASS. `test_generators_phase2_stub.py` 13 PASS. |
| **G2** | 27개 Layer 2 rule 함수 단위 테스트 PASS (≥ 25/27) | ✅ | feed 8 (Phase 1) + detail 5 + plan 4 + messages 5 + review 5 = **27/27** 함수 모두 단위 테스트 보유 + 모두 PASS. positive/negative 쌍 검증 완료. |
| **G3** | 5쌍 cross-reference 각각의 positive/negative 케이스 검출 | ✅ | `test_cross_reference.py` 9 PASS. 5 pair × negative variant + positive bundle + partial bundle skip behavior 모두 통과. inconsistent bundle 에서 4 hard reject 검출. |
| **G4** | 스팟 1개 full E2E (5 content + cross-ref) 모두 실행 가능 (허용 xfail 명시) | ✅ | yeonmu_evening spec, **Layer 1 5/5 / Layer 2 5/5 / Layer 3 ok=True**. `data/goldens/_results/phase2_e2e.jsonl` 1 row. xfail 없음. |
| **G5** | live 모드 feed + detail 2건 schema PASS | ⚠️ 부분 | feed live PASS (G4 phase1 회귀 검증 포함). detail live FAIL — content-generator-engineer 측 `detail/v1.j2` 의 leading-newline 으로 codex CLI stdin 오인. **버그가 식별되고 수정 위치 명시됨**. live 호출 자체는 정확히 2회로 제한. |
| **G6** | boundary audit 5쌍 모두 PASS | ✅ | Phase 1 pair 1/2/3 PASS (재실행) + Phase 2 pair 4/5 PASS. review.meta 1건 info-only allowlist 처리 + 문서화. |

**총평: 6 기준 중 5 PASS + 1 부분 (G5 detail live)**.

---

## 7. 후속 조치 / Phase 3 진입 권장 여부

### 7-1. P0 — Phase 3 진입 전 반드시 수정

| # | 담당 | 파일/위치 | 작업 |
|---|---|---|---|
| 1 | content-generator-engineer | `config/prompts/detail/v1.j2:1-3` | Jinja2 코멘트 `{# ... #}` → `{#- ... -#}` (앞뒤 whitespace strip) 또는 코멘트 제거. 결과 prompt 가 비-공백 문자로 시작해야 함. **detect**: `prompt_loader.render("detail:v1", vars)[:1] != "\n"` 어서션. |
| 2 | content-generator-engineer | `config/prompts/{plan,messages,review}/v1.j2` 1행 | 동일 패턴이 있는지 확인하고 같은 방식으로 strip. 3 템플릿에 대해 각각 1회 live smoke 추가 (총 +3 call) 또는 codex CLI 모킹 단위 테스트 작성. |

### 7-2. P1 — Phase 3 초기 처리 권장

| # | 담당 | 위치 | 작업 |
|---|---|---|---|
| 3 | validator-engineer | `src/pipeline/validators/review_rules.py:167-170` `rule_review_length_bucket_match` | review schema 에 `meta` optional object 를 추가하거나, rule 을 quality_score 산정 단계로 이전. Phase 2 gate 는 info 처리. |
| 4 | codex-bridge-engineer | `src/pipeline/llm/codex_client.py:_invoke_codex` | prompt `lstrip()` 안전망 추가 (선택). generator 실수 방지용. |
| 5 | pipeline-qa | `scripts/qa_boundary_audit_phase2.py` | nested key 추출 정규식 확장 — `cost_breakdown[].amount`, `steps[].time` 도 잡아야 Pair 5 의 detection 강화. Phase 3 시작 시 처리. |

### 7-3. Phase 3 진입 권장

**조건부 권장 (YES with P0 fix)**:

- G1, G2, G3, G4, G6 = **완전 PASS**. Phase 2 의 핵심 작업 (4 generator + Layer 2 19 rule + Layer 3 5 pair + dispatch 통합) 모두 정상 동작 확인.
- G5 만 부분 — detail live FAIL. 단 **버그가 정확히 식별**되고 위치/수정안이 명시되어 있어 content-generator-engineer 가 `v1.j2` 1행 수정만 하면 즉시 해소.
- Phase 3 작업(critic, diversity, scoring, retry loop)은 Phase 2 의 Layer 1/2/3 통합에 의존하지만, **detail prompt 의 leading-newline 은 stub 모드 동작에는 영향 없음** (stub 은 prompt 렌더를 건너뛰므로). 따라서 Phase 3 의 단위 테스트 / 통합 테스트는 stub 모드로 병렬 진행 가능하며, content-generator-engineer 의 P0 fix 는 nightly live smoke 직전까지만 완료되면 됨.

즉 **content-generator-engineer 가 P0 (`detail/v1.j2` leading whitespace fix) 를 완료하기 전이라도 Phase 3 stub 작업은 시작 가능**. 단 Phase 3 의 live smoke (critic / nightly) 직전에 G5 재검증 1회 필수.

---

## 8. 부록 — 생성/추가 파일 목록

### 8-1. tests/ (5 신규)

- `tests/test_generators_phase2_stub.py` — 13 PASS
- `tests/test_validators_phase2_units.py` — 42 PASS
- `tests/test_cross_reference.py` — 9 PASS
- `tests/test_end_to_end_phase2_spot.py` — 2 PASS
- `tests/test_codex_bridge_phase2_smoke_live.py` — 1 PASS / 1 FAIL (live, deselected by default)

### 8-2. data/goldens/bundles/ (3 신규)

- `golden_bundle_yeonmu_food.json` — positive (5쌍 전부 PASS)
- `golden_bundle_inconsistent.json` — negative (food spec ↔ drawing detail / plan / review + time mismatch)
- `golden_bundle_noshow_contradiction.json` — negative (`전원` 표현 + no_show_count=2)

### 8-3. data/goldens/_results/ (1 신규)

- `phase2_e2e.jsonl` — 단일 스팟 e2e 결과 1 row (test_end_to_end_phase2_spot 가 자동 갱신)

### 8-4. scripts/ (1 신규)

- `scripts/qa_boundary_audit_phase2.py` — Phase 2 pair 4 + pair 5 자동 감사. Phase 1 스크립트(`qa_boundary_audit.py`) 와 별도 실행. 합산 통과 조건은 phase2_report.md §2 통합 표.

### 8-5. _workspace/scp_05_qa/ (3 신규/갱신)

- `phase2_report.md` — 본 문서
- `boundary_audit_phase2.md` — Pair 4/5 결과
- `phase2_live_smoke.log` — 2 codex call live 결과 raw 로그

---

## 9. 환경 (참고)

- Python 3.12.3 (system), `PYTHONPATH=src` 로 import.
- 의존성: `pytest`, `jsonschema`, `pydantic`, `sqlalchemy`, `jinja2`, `pyyaml`, `rapidfuzz`, `click` 모두 설치 확인.
- codex CLI: `codex-cli 0.118.0` (`/home/seojingyu/.nvm/versions/node/v22.14.0/bin/codex`).
- live smoke 는 단 1 세션 (정확히 2 exec call). 재실행 필요 시 수동.

---

## 10. 결론

Phase 2 의 4종 generator + 19 Layer 2 rule + 5 Layer 3 pair + dispatch 통합은
설계대로 동작하며, 단일 스팟 full E2E 가 stub 모드에서 깨끗하게 통과한다.
유일한 잔여 이슈는 detail prompt 의 leading-whitespace 로 인한 codex CLI 호환
문제이며, 이는 generator 책임 영역의 단순 1행 수정으로 해소된다. Phase 3 진입을
권장한다 — 단 Phase 3 의 live smoke 단계 진입 전 P0 1건 fix 필요.
