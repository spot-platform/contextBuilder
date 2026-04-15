---
name: build-synthetic-content-pipeline
description: synthetic_content_pipeline_plan.md를 구현할 때 반드시 이 스킬을 사용. 시뮬레이션 로그를 5종 synthetic content(feed/detail/plan/messages/review)로 렌더링하고 6 Layer 검증 후 publish하는 파이프라인을 5인 에이전트 팀(pipeline-infra-architect, codex-bridge-engineer, content-generator-engineer, validator-engineer, pipeline-qa)으로 구축한다. **LLM 호출은 OpenAI/Anthropic API가 아닌 사용자의 Codex 구독 CLI(`codex exec`)를 subprocess로 호출**한다. "synthetic content 파이프라인 구현", "콘텐츠 생성 파이프라인 만들어줘", "LLM 생성기+검증기 루프", "codex 구독으로 synthetic content", "Phase 1~4 진행" 요청 시 즉시 트리거. 기존 `local-context-builder/`, `spot-simulator/` 코드는 건드리지 않고 새 디렉토리 `synthetic-content-pipeline/`을 생성한다.
---

# build-synthetic-content-pipeline — Orchestrator

## 전제

- 플랜: `synthetic_content_pipeline_plan.md` (워크스페이스 루트)
- 작업 디렉토리: **`synthetic-content-pipeline/`** (신규). `local-context-builder/`, `spot-simulator/`와 병렬
- 시뮬레이션 입력: `../spot-simulator/output/event_log.jsonl` (읽기 전용)
- **LLM 실행 방식**: `codex exec` CLI subprocess. **API 키 기반 호출 금지**. 세부 규약은 `references/codex-subscription-usage.md`를 먼저 읽을 것
- 실행 모드: **에이전트 팀** (5명)
- 기본 모델: 모든 Agent 호출에 `model: "opus"`

## 팀

| 에이전트 | 역할 요약 |
|---------|----------|
| `pipeline-infra-architect` | 스캐폴딩, DB 6테이블, content_spec_builder, 10개 job 진입점, publisher, versioning |
| `codex-bridge-engineer` | **`codex exec` subprocess wrapper**, 프롬프트 로더, rejection-feedback 재시도, 캐시, stub 모드 |
| `content-generator-engineer` | 5종 생성기 + 프롬프트 본문, 페르소나 톤/길이/별점 분포 |
| `validator-engineer` | Layer 1~6 검증, cross-reference, critic(샘플링), diversity, scoring, 재시도 루프 |
| `pipeline-qa` | incremental 경계면 교차 검증, goldens, §14 지표 측정, Phase 게이트 |

## Phase 0 — 팀 구성 & 전제 확인

1. **Codex 로그인 확인** (사용자 측): `codex login status` 통과해야 함. 미로그인 시 사용자에게 **`! codex login`** 안내. 이 단계 생략 금지
2. `synthetic-content-pipeline/` 존재 여부 확인. 있으면 사용자에게 덮어쓸지 확인
3. `spot-simulator/output/event_log.jsonl` 존재 확인. 없으면 사용자에게 spot-simulator Phase 1 선행 여부 확인
4. 팀 구성 (`TeamCreate` 또는 동등)으로 5명 소집
5. 공유 작업 목록을 `TaskCreate`로 생성 (Phase 1만 먼저):
   - `scp_01_infra_phase1_complete`
   - `scp_02_codex_phase1_complete`
   - `scp_03_gen_phase1_complete` (feed만)
   - `scp_04_val_phase1_complete` (Layer 1+2만)
   - `scp_05_qa_phase1_complete`

## Phase 1 — 기반 + 피드 생성기 + 검증기 동시 (플랜 Week 1~2)

> **핵심**: generator와 validator를 **동시에** 만들어서 생성 결과를 즉시 확인하고 프롬프트를 조기 튜닝한다 (플랜 §12 Phase 1 핵심).

```
[1] pipeline-infra-architect 단독 실행
    - 디렉토리 + pyproject + DB 6테이블 + Alembic 초기 마이그레이션
    - content_spec_builder 구현 (event_log → ContentSpec)
    - 10개 job 진입점 껍데기
    - column_contract.md / job_contract.md 작성
        ↓
[2] codex-bridge-engineer + content-generator-engineer(feed) + validator-engineer(schema+rule) 병렬
    - bridge: codex_client.py, prompt_loader, health.check_codex_login, stub 모드
    - generator: feed.py + config/prompts/feed/v1.j2 + base.py
    - validator: validators/schema.py + validators/rules.py + rule_*.yaml
        ↓
[3] pipeline-qa: incremental 검증
    - boundary audit (column_contract ↔ DB 모델, 프롬프트 변수 ↔ generator context)
    - stub 모드 end-to-end (feed 10개 샘플)
    - goldens 5~7개 작성
        ↓
[Gate] Phase 1 종료 기준:
    - content_spec_builder가 실제 event_log에서 ContentSpec 생성 성공
    - stub 모드에서 feed 10개 전체 Layer 1+2 통과
    - live 모드(실 codex 1회)로 feed 3개 생성 smoke 성공
    - boundary audit 5쌍 중 최소 1,2,3번 PASS
    ↓
    YES → Phase 2 태스크 생성 후 진입
    NO  → 재시도 정책 적용
```

## Phase 2 — 나머지 생성기 + Cross-Reference (Week 3~4)

Phase 1 통과 후 새 태스크:
```
scp_01_infra_phase2_complete (컬럼 추가 필요 시 새 migration)
scp_03_gen_phase2_complete (detail/plan/messages/review)
scp_04_val_phase2_complete (cross_reference.py)
scp_05_qa_phase2_complete (스팟 단위 통합 테스트)
```

순서: generator 병렬 → validator cross_reference → QA 스팟 단위 end-to-end. bridge는 Phase 1 상태 유지하되 프롬프트 스키마 파일 추가.

## Phase 3 — Critic + Diversity + 재시도 루프 (Week 5)

```
scp_02_codex_phase3_complete (critic 호출 경로, rejection feedback retry 완성)
scp_04_val_phase3_complete (critic.py + diversity.py + scoring.py + loop/generate_validate_retry.py)
scp_05_qa_phase3_complete (§14 지표 측정 — 1차 승인률/평균 quality/diversity/호출수/시간/critic 비율)
```

> Phase 3은 **처음으로 live codex를 대규모로 쓰는** 단계. pipeline-qa가 goldens 500 스팟 중 50~100개만 live 실행하여 추정치를 내도 됨 (구독 비용 보호).

## Phase 4 — Publish + 전환 정책 (Week 6)

```
scp_01_infra_phase4_complete (publisher.py, versioning.py 본격 구현)
scp_05_qa_phase4_complete (전환 시나리오 테스트: draft→active→deprecated→archived)
```

## 데이터 전달 프로토콜

- **파일 기반 산출물**: `_workspace/scp_{NN}_{agent}/` 하위
- **계약 문서** (경계면 교차 검증의 기반):
  - `scp_01_infra/column_contract.md`
  - `scp_01_infra/job_contract.md`
  - `scp_02_codex/bridge_api.md`
  - `scp_02_codex/prompt_contract.md`
  - `scp_03_gen/generator_contract.md`
  - `scp_04_val/rule_table.md`, `scp_04_val/scoring_audit.md`
  - `scp_05_qa/boundary_audit.md`, `phase{1..4}_report.md`
- **실시간 조율**: `SendMessage`로 컬럼 추가 / 변수 계약 / rejection feedback 포맷 교환
- **최종 산출물**: `synthetic-content-pipeline/` 트리 + goldens 실행 보고서 + Phase 4 publish 증적

## Agent 호출 시 주의

모든 `Agent` 도구 호출에 `model: "opus"`를 명시한다. 프롬프트에 반드시 포함:
1. 현재 Phase와 이번 실행에서 완료할 태스크 ID (`scp_XX_YY_phaseN_complete`)
2. 읽어야 할 플랜 섹션 번호
3. 읽어야 할 `_workspace/` 내 이전 산출물 경로
4. **쓸 파일 경로와 쓰지 말아야 할 영역** (다른 에이전트 소관)
5. **LLM 호출은 반드시 codex-bridge를 경유한다는 재확인** (특히 validator-engineer의 critic 작업 시)

## 재시도 정책

- **Phase 1 gate 실패**:
  - feed 프롬프트 품질 낮음 → `content-generator-engineer`에 프롬프트 튜닝 요청 (§7 문체·길이)
  - validator false negative → `validator-engineer`에 rule 파라미터 재검토
  - codex 호출 실패 → `codex-bridge-engineer`에 stub/live 분기 점검
  - boundary audit 불일치 → 지목된 에이전트에게 필드명 일치 요청
- **Phase 3 §14 지표 미달**:
  - 1차 승인률 < 70% → generator의 rejection feedback 수용 품질 문제 → 프롬프트에 feedback 블록 강화
  - 평균 quality < 0.80 → scoring 가중치가 아니라 naturalness/persona_fit 절대값이 낮음 → critic 프롬프트 재검토
  - diversity > 0.60 → 템플릿 패턴 과다 → 프롬프트 variant 수 늘리기
  - 호출수 > 15회 → 재시도 한도 초과 → Layer 1+2 선행 통과율 높이기
- **1회 재시도 후에도 실패** → 오케스트레이터가 사용자에게 현황 보고 후 판단 요청

## 테스트 시나리오

### 정상 흐름
1. 사용자: "synthetic content 파이프라인 Phase 1 구현해줘"
2. Phase 0 `codex login status` OK, spot-simulator event_log 존재 확인
3. Phase 1 실행 → stub 모드 feed 10개 PASS, live 모드 feed 3개 smoke PASS
4. boundary audit 5쌍 중 1,2,3번 GREEN → Phase 2 진입 여부 사용자에게 확인

### 에러 흐름
1. Phase 1 실행 후 `pipeline-qa`가 boundary audit 2번(프롬프트 변수 ↔ generator context)에서 불일치 발견: "`feed/v1.j2`가 `participants_count` 변수를 기대하지만 generator가 `expected_count`로 넘김"
2. 오케스트레이터가 `content-generator-engineer`에게 해당 라인 지목하며 수정 요청 (SendMessage)
3. generator 수정 → QA 재검증 → PASS → Phase 1 gate 통과
4. 같은 패턴 반복 시 → `codex-bridge-engineer`에게 `prompt_contract.md`에 변수 표준 명시 요청

### Live codex smoke 실패 흐름
1. `codex exec` 호출이 `exit code 1` + stderr "not logged in"
2. pipeline-qa 또는 codex-bridge-engineer가 `health.check_codex_login()` 결과를 사용자에게 보고
3. 오케스트레이터가 **`! codex login`** 실행 안내 → 로그인 후 재시도

## 참고

- Codex CLI 사용 규약(옵션, JSON 스키마 강제, stub 모드, rate limit): `references/codex-subscription-usage.md`
- 플랜 문서: `synthetic_content_pipeline_plan.md` 전체
