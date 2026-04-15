---
name: build-local-context-builder
description: local-context-builder-plan.md의 계획(Kakao 장소 수집 → 정규화 → 피처 → 페르소나 가중치 → 스팟 시드 → publish 배치 서비스)을 에이전트 팀으로 end-to-end 구현하는 오케스트레이터 스킬. infra-architect, schema-designer, collector-engineer, processor-engineer, integration-qa 5명을 에이전트 팀으로 편성하여 단계별로 실행한다. "local-context-builder 구현해줘", "계획서대로 만들어줘", "plan 파일을 구현해줘" 등 이 프로젝트의 전체/다수 모듈을 한 번에 구현해야 할 때 반드시 이 스킬을 사용할 것.
---

# build-local-context-builder (오케스트레이터)

`local-context-builder-plan.md`의 전체 구현을 5명 에이전트 팀으로 수행하는 오케스트레이터 스킬.

## 언제 사용하는가

- "계획서대로 local-context-builder를 만들어줘"
- "plan.md의 MVP 범위를 구현해줘"
- 프로젝트를 처음부터 스캐폴딩하여 publish까지 돌아가는 상태로 만들고 싶을 때

단일 모듈만 손보는 경우(예: "카카오 클라이언트만 수정")는 이 오케스트레이터를 쓰지 말고 해당 에이전트의 스킬을 직접 호출한다.

## 실행 모드

**에이전트 팀 모드.** 5명 팀을 `TeamCreate`로 구성하고, `TaskCreate`로 단계별 작업을 할당한다. 팀원은 `SendMessage`로 직접 통신하며 `_workspace/` 파일 기반으로 산출물을 공유한다.

## 팀 구성

| 에이전트 | 타입 | 스킬 | 담당 |
|---|---|---|---|
| `infra-architect` | general-purpose | `scaffold-python-batch` | 프로젝트 스캐폴딩, Docker, Alembic, 진입점 |
| `schema-designer` | general-purpose | `design-batch-schema` | 9개 모델 + 마이그레이션 + 시드 |
| `collector-engineer` | general-purpose | `build-kakao-collector` | Kakao 클라이언트 + grid + 수집 잡 |
| `processor-engineer` | general-purpose | `build-feature-pipeline` | 정규화·피처·페르소나·스팟 시드·publish |
| `integration-qa` | general-purpose | `verify-pipeline-integration` | admin API·Celery·incremental QA |

모든 에이전트는 `model: "opus"`.

## 아키텍처 패턴

**파이프라인 + 생성-검증 복합**:
- `infra-architect` → `schema-designer`는 직렬 (다음 단계가 이전 결과에 의존)
- `collector-engineer`와 `processor-engineer`는 스키마 확정 이후 병렬 가능
- `integration-qa`는 각 단계 완료 직후 incremental하게 검증

## 워크플로우

### Phase 0: 선행 체크

1. `local-context-builder-plan.md` 존재 여부와 최신 섹션(§3, §4, §14) 검증
2. `.claude/agents/`와 `.claude/skills/` 디렉토리에 에이전트/스킬 파일이 모두 준비되어 있는지 확인
3. 프로젝트 루트(`local-context-builder/`) 디렉토리 신규/기존 여부 확인 — 기존이면 덮어쓰기 방지
4. `_workspace/` 생성

### Phase 1: 팀 편성과 작업 보드 초기화

```
TeamCreate(team_name="lcb-build", members=[infra-architect, schema-designer, collector-engineer, processor-engineer, integration-qa])
```

공유 작업 보드에 다음 태스크를 생성하고 의존 관계를 건다:

| ID | 소유 | 제목 | 의존 |
|---|---|---|---|
| T01 | infra-architect | 프로젝트 스캐폴딩 + config + Alembic | - |
| T02 | schema-designer | 9개 모델 + 초기 마이그레이션 + 시드 | T01 |
| T03 | collector-engineer | Kakao 클라이언트 + grid + full_rebuild | T02 |
| T04 | processor-engineer | 정규화 + 피처 + 페르소나 + 스팟 시드 + publish | T02 |
| T05 | integration-qa | admin API + Celery wire + incremental QA | T01 (계속 진행) |
| T06 | integration-qa | 엔드투엔드 통합 테스트 | T03, T04 |
| T07 | (팀 전체) | QA 리포트 이슈 전량 해소 | T05, T06 |

T03과 T04는 T02 완료 후 병렬로 진행. T05는 T01 완료 직후 시작하되 T02~T04와 중첩 진행(incremental QA).

### Phase 2: 순차 실행

1. **T01** infra-architect 실행. 완료 보고 + `_workspace/01_infra/` 아티팩트 생성 대기
2. **integration-qa가 01 검증**: 인프라 임포트 체인과 .env 매핑 확인
3. **T02** schema-designer 실행. `_workspace/02_schema/column_contract.md` 생성 확인
4. **integration-qa가 02 검증**: DDL ↔ 모델 ↔ 마이그레이션 3중 대조
5. **T03, T04** collector-engineer와 processor-engineer 병렬 실행
   - 두 에이전트는 `column_contract.md`를 읽고 자기 범위 작업
   - 서로 필요한 인터페이스는 SendMessage로 확인
6. **integration-qa가 03/04 검증**: 쓰기 필드 커버리지 + 함수 시그니처
7. **T05/T06** integration-qa가 admin API/Celery 연결 + 통합 테스트
8. **T07** 이슈 해소 루프: QA 리포트의 open 이슈가 0이 될 때까지 반복

### Phase 3: 마무리

1. `_workspace/05_qa_report.md`의 open 이슈가 0인지 확인
2. 최종 체크리스트 실행:
   - `alembic upgrade head` no-op 성공
   - `pytest` 그린
   - `uvicorn app.main:app` 기동 + `/admin/health` 200
   - Kakao 클라이언트 테스트 (mocked) 통과
   - 파이프라인 통합 테스트(mocked) 통과
3. 최종 보고서: `_workspace/final_report.md` — 구현된 모듈, 미구현 항목(v1.1으로 연기된 것), 실행 방법

## 데이터 전달 프로토콜

### 파일 기반 (주력)

`_workspace/` 경로 컨벤션:
```
_workspace/
├── 01_infra/README.md, env_schema.md
├── 02_schema/model_index.md, column_contract.md
├── 03_collector/README.md, api_surface.md
├── 04_processor/README.md, dataflow.md
├── 05_qa_report.md
└── final_report.md
```

파일명 컨벤션: `{phase}_{agent}_{artifact}.md`. 중간 산출물은 보존(감사 추적).

### 메시지 기반 (실시간 조율)

- 수정 요청: QA → 담당자
- 인터페이스 질문: collector ↔ processor, processor ↔ schema
- 완료 신호: 각 에이전트 → integration-qa (incremental QA 트리거)

### 태스크 기반 (진행 관리)

위 Phase 1 표의 T01~T07을 TaskCreate/TaskUpdate로 관리. 팀원은 본인 태스크를 `in_progress` → `completed`로 전이.

## 에러 핸들링

| 에러 유형 | 대응 |
|---|---|
| 에이전트 1회 실행 실패 | 1회 재시도. 재실패 시 해당 태스크 스킵 + 리포트에 누락 명시 |
| 경계면 불일치 발견 | QA가 담당자에게 SendMessage → 수정 → 재검증. 3회 실패 시 오케스트레이터에게 에스컬레이션 |
| Kakao API 키 없음 | MVP 구현은 mock 기반이므로 실제 키 없이도 진행. 운영 실행은 사용자에게 위임 |
| alembic upgrade 실패 | schema-designer에게 마이그레이션 수정 요청. 해결 전까지 T03/T04 차단 |
| 플랜과 다른 의견 충돌 | 플랜이 진실의 원천. 플랜 수정이 필요하면 사용자 확인 후에만 |

**원칙**: 상충 데이터는 삭제하지 않고 `_workspace/05_qa_report.md`에 출처와 함께 기록한다.

## 테스트 시나리오

### 정상 흐름

입력: 사용자가 "plan.md의 MVP 범위로 구현해줘" 요청

1. Phase 0: 플랜 파일 존재 확인 → OK
2. Phase 1: 팀 편성, 태스크 7개 생성
3. T01 완료 → 01_infra 검증 OK
4. T02 완료 → 02_schema 검증에서 `region_feature.culture_score` 컬럼 모델 누락 발견
5. QA가 schema-designer에게 수정 요청, 재검증 OK
6. T03, T04 병렬 실행 → 완료
7. T05 admin API 12개 엔드포인트 wire
8. T06 통합 테스트(Kakao mocked) 그린
9. T07 QA 리포트 open 이슈 0
10. final_report 생성, 사용자에게 실행 방법 안내

### 에러 흐름

- T03에서 collector-engineer가 `place_raw_kakao.search_type` 컬럼을 빠뜨림
- QA가 경계면 #3 검사에서 감지 → SendMessage
- 1차 재시도: collector가 수정. 검증 통과
- 리포트에 이슈 기록: open → resolved

## 체크리스트

- [ ] 플랜 파일 존재 확인
- [ ] 5명 팀 편성 완료
- [ ] 7개 태스크 생성 + 의존 관계 설정
- [ ] 모든 Agent 호출에 `model: "opus"` 명시
- [ ] 각 Phase 완료 후 incremental QA 실행
- [ ] `_workspace/` 아티팩트 6종 전부 생성
- [ ] 최종 QA 리포트 open 이슈 0
- [ ] 통합 테스트 1개 이상 통과
- [ ] `final_report.md` 작성
- [ ] 사용자에게 실행 방법(`docker compose up`, `alembic upgrade head`, `pytest`) 안내

## 범위 제한

**이 오케스트레이터가 하지 않는 것**:
- 실제 Kakao API 호출 (키는 사용자 관리)
- 실서비스 Spring Boot DB에 실제 연결 (개발 환경에서는 mock/skip)
- 수원시 행정동 좌표의 실제 수집 (시드 CSV 헤더와 코드만 확보, 좌표는 사용자가 채움)
- 운영 배포 (Docker build + 프로덕션 환경 수동 작업)
- v1.1 범위(실데이터 결합, 증분 갱신 스케줄러, 모니터링 자동화) — 인터페이스만 준비
