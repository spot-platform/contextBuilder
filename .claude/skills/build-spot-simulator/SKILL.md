---
name: build-spot-simulator
description: spot-simulator-implementation-plan.md를 구현할 때 반드시 이 스킬을 사용. Phase 1(MVP loop, 50 agents × 48 tick) → Phase 2(lifecycle) → Phase 3(settlement)를 5인 에이전트 팀(sim-infra-architect, sim-model-designer, sim-engine-engineer, sim-data-integrator, sim-analyst-qa)으로 구축한다. "spot-simulator 구현해줘", "시뮬레이터 만들어줘", "Phase 1/2/3 진행", "에이전트 기반 시뮬레이션" 요청 시 즉시 이 스킬을 트리거. spotContextBuilder 워크스페이스 내 독립 디렉토리 spot-simulator/를 생성하며, 기존 local-context-builder 코드는 건드리지 않는다.
---

# build-spot-simulator — Orchestrator

## 전제

- 플랜: `spot-simulator-implementation-plan.md` (워크스페이스 루트)
- 작업 디렉토리: `spot-simulator/` (신규, 기존 `local-context-builder/`와 분리)
- 실행 모드: **에이전트 팀** (5명)
- 기본 모델: 모든 Agent 호출에 `model: "opus"`

## 팀

| 에이전트 | 역할 요약 |
|---------|----------|
| `sim-infra-architect` | 디렉토리·pyproject·main·config yaml 스캐폴딩 |
| `sim-model-designer` | AgentState/Spot/EventLog, 시간·감쇠 유틸 |
| `sim-engine-engineer` | runner/decision/lifecycle/settlement |
| `sim-data-integrator` | config·페르소나·region 로딩 + 엔진 어댑터 |
| `sim-analyst-qa` | Phase별 validate + visualize + pytest, 게이트키퍼 |

## Phase 0 — 팀 구성

1. `spot-simulator/` 존재 여부 확인. 있으면 사용자에게 덮어쓸지 확인
2. `TeamCreate` (또는 동등한 팀 초기화)로 위 5명 소집
3. 공유 작업 목록을 `TaskCreate`로 생성:
   - `sim_01_infra_complete`
   - `sim_02_models_phase1_complete`
   - `sim_04_data_complete`
   - `sim_03_engine_phase1_complete`
   - `sim_05_qa_phase1_complete`
   - (Phase 2/3 태스크는 Phase 1 통과 후 생성)

## Phase 1 — MVP Loop (병렬 가능한 것은 병렬로)

```
[1] sim-infra-architect 단독 실행 (스캐폴딩)
        ↓
[2] sim-model-designer + sim-data-integrator 병렬
    - model-designer: dataclass + 시간·감쇠 유틸
    - data-integrator: loader, 더미 JSON, 페르소나 템플릿 값
        ↓
[3] sim-engine-engineer 실행 (runner/decision Phase 1)
        ↓
[4] sim-analyst-qa 실행 (validate_phase1 + visualize + pytest)
        ↓
[Gate] Phase 1 §2.8 기준 7개 모두 통과?
    - YES → Phase 2 진행
    - NO  → 실패 원인별 재시도 (아래 재시도 정책)
```

### 재시도 정책

- 행동 분포 이상(예: CREATE 너무 적음) → `sim-engine-engineer`에 가중치 재검토 요청
- fatigue/social_need 수렴·발산 → `sim-model-designer`에 감쇠 파라미터 조정 요청
- 더미 데이터 편향 → `sim-data-integrator`에 분포 재생성 요청
- 테스트 인프라 고장 → `sim-infra-architect`에 conftest/pyproject 확인 요청
- 1회 재시도 후에도 실패하면 오케스트레이터가 사용자에게 현황 보고하고 판단 요청

## Phase 2 — Lifecycle + 상호작용

Phase 1 통과 후에만 진입. 새 태스크 생성:

```
sim_02_models_phase2_complete (trust_score, duration, checked_in 등 필드 추가)
sim_03_engine_phase2_complete (lifecycle.py 신규, decision.py에 social modifier)
sim_05_qa_phase2_complete (validate_phase2 §3.7)
```

순서: model → engine → qa. data-integrator는 Phase 1 상태 유지하되 리드타임 분포만 필요 시 보강.

## Phase 3 — Settlement + Review + Trust

Phase 2 통과 후에만 진입:

```
sim_02_models_phase3_complete
sim_03_engine_phase3_complete (settlement.py 신규)
sim_04_data_phase3_complete (실데이터 연결은 선택)
sim_05_qa_phase3_complete (validate_phase3 §4.6)
```

## 데이터 전달 프로토콜

- **파일 기반 산출물**: `_workspace/sim_{NN}_{agent}/` 하위에 모든 중간 산출물 저장
- **계약 문서**: `column_contract.md` / `probability_table.md` / `adapter_contract.md` / `boundary_audit.md` — 경계면 교차 검증의 기반
- **실시간 조율**: `SendMessage`로 필드 추가·어댑터 시그니처 변경·튜닝 요청 교환
- **최종 산출물**: `spot-simulator/` 트리 + `output/event_log.jsonl` + `_workspace/sim_05_qa/phase{1,2,3}_report.md`

## Agent 호출 시 주의

모든 `Agent` 도구 호출에 `model: "opus"` 파라미터를 명시한다. 프롬프트에는:
1. 현재 Phase와 이번 실행에서 완료해야 할 태스크 ID
2. 읽어야 할 플랜 섹션 번호
3. 읽어야 할 `_workspace/` 내 이전 산출물 경로
4. 쓸 파일 경로와 쓰지 말아야 할 영역(다른 에이전트 소관)

## 테스트 시나리오

### 정상 흐름
1. 사용자: "spot-simulator Phase 1 구현해줘"
2. Phase 0 팀 구성 → Phase 1 실행 → QA 통과
3. `output/event_log.jsonl`에 30+ 이벤트, `_workspace/sim_05_qa/phase1_report.md`에 7개 기준 모두 PASS
4. 오케스트레이터가 Phase 2 진입 여부를 사용자에게 확인

### 에러 흐름
1. Phase 1 실행 후 `validate_phase1`에서 "CREATE_SPOT 3개 (<5)" 실패
2. `sim-analyst-qa`가 `phase1_report.md`에 실패 기록, 원인 가설: "host_score 분포 편향 또는 `p_create` 공식의 time_weight가 지배적"
3. 오케스트레이터가 가설을 판정하여 `sim-data-integrator`(더미 재생성) 또는 `sim-engine-engineer`(time_weight 재조정) 중 하나 선택
4. 1회 재시도 → 여전히 실패 시 사용자에게 현황 보고
