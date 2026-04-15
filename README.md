# spotContextBuilder

> **또래 강사 마켓플레이스**의 cold-start 문제를 풀기 위한 **합성 콘텐츠 파이프라인**.
> 실제 지역 데이터를 수집하고 → 페르소나 기반 에이전트 시뮬로 행동 로그를 만들고 → LLM이 그 로그를 5종 콘텐츠(피드·상세·플랜·메시지·리뷰)로 옮겨 쓰고 → 다층 검증을 통과한 것만 서비스 DB에 publish합니다.

---

## 핵심 아이디어 한 줄

> **"사실(fact)은 결정론적 시뮬이 만들고, LLM은 그 사실을 문체로 옮겨 쓰는 문서 생성기로만 쓴다."**

LLM의 hallucination을 사실 수준에서 차단하고, 6-Layer 검증 루프로 품질을 강제합니다.

---

## 4-Stage 파이프라인

```
Stage 1                 Stage 2                 Stage 3                 Stage 4
실제 지역 데이터    →   또래 강사 ABM 시뮬  →   매력 큐레이션      →   LLM 콘텐츠 생성
                                                                         + 6 Layer 검증
(local-context-         (spot-simulator)        (multi-run                (synthetic-content-
 builder)                                        attractiveness)           pipeline)
```

---

## 서브시스템

| 디렉토리 | 역할 | 주요 산출물 |
|---------|-----|-----------|
| [`local-context-builder/`](./local-context-builder/) | 카카오 Local API 기반 지역 피처 수집·정규화 | `region_features`, `persona_region_affinity` |
| [`spot-simulator/`](./spot-simulator/) | 또래 강사 페르소나 에이전트 시뮬레이션 | `event_log.jsonl`, `spot_seed` |
| [`synthetic-content-pipeline/`](./synthetic-content-pipeline/) | LLM 콘텐츠 생성 + 6-Layer 검증 + publish | `synthetic_*` 테이블 row |
| [`plan/`](./plan/) | 모든 구현 플랜 문서 (ABM · peer pivot · 파이프라인) | `*-plan.md` |
| [`_workspace/`](./_workspace/) | 계층별 작업 히스토리·델타·QA 리포트 (append-only) | `*_delta.md`, `phase*_report.md` |

---

## 문서 읽는 순서

처음 들어온 사람은 이 순서로 읽으면 30분 안에 감이 잡힙니다.

1. **[팀원_피쳐_설명서.md](./팀원_피쳐_설명서.md)** — 무엇을 만드는지, 왜 이렇게 만드는지, 어디에 활용할 수 있는지. 기술 배경 없는 팀원·교수님용 설명서. 4단계 파이프라인 상세, 제품 활용 시나리오 4종(API/DTO 포함), 논문 녹이는 법.
2. **[히스토리.md](./히스토리.md)** — 어떻게 만들어졌는지, 어떻게 수정해야 하는지, 지금까지의 시행착오. 인수인계서. env 설정, Codex CLI 연결법, 협업 규칙(이슈/브랜치/PR/리뷰), 토큰 사용 현황.
3. **플랜 문서** (`plan/` 폴더):
   - [`local-context-builder-plan.md`](./plan/local-context-builder-plan.md)
   - [`spot-simulator-implementation-plan.md`](./plan/spot-simulator-implementation-plan.md)
   - [`spot-simulator-peer-pivot-plan.md`](./plan/spot-simulator-peer-pivot-plan.md) — 또래 강사 도메인 pivot 플랜. 스킬·자산·관계·fee 공식 정본.
   - [`synthetic_content_pipeline_plan.md`](./plan/synthetic_content_pipeline_plan.md)

---

## Quick Start

### 1. 시뮬레이터 한 번 돌려보기

```bash
cd spot-simulator
python main.py --config config/simulation_config.yaml
head -20 output/event_log.jsonl
```

### 2. 콘텐츠 파이프라인 stub 모드

```bash
cd synthetic-content-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest -q                              # 회귀 테스트 전부 green 확인
SCP_LLM_MODE=stub python -m pipeline.run --limit 1
```

### 3. Live 모드 (실제 Codex 호출)

Codex CLI 설치·로그인 후:

```bash
export SCP_LLM_MODE=live
python -m pipeline.run --limit 1
```

상세 env 변수와 Codex 연결법은 [`히스토리.md §2-4`](./히스토리.md#2-4-환경-설정-env-및-codex-연결) 참고.

---

## 협업 규칙 요약

- **이슈 없이 코드 없다** — 변경 전 항상 이슈 먼저.
- **브랜치 네이밍**: `<type>/<issue_number>[-<slug>]` (`feature/2`, `refactor/3`, `bug/4`, `docs/5`).
- **PR 요약 필수** — 무엇을 / 왜 / 어떤 접근을. 300~400 라인 목표.
- **최소 1명 이상 리뷰 approve** 후 merge. Squash merge 권장.

전체 규칙과 실제 적용 예시는 [`히스토리.md §2-5, §2-6`](./히스토리.md#2-5-협업-규칙--이슈--브랜치--pr--리뷰) 참고.

---

## 주의사항

- **OpenAI / Anthropic SDK를 쓰지 않습니다.** LLM 호출은 사용자의 ChatGPT 구독에 딸린 `codex` CLI를 subprocess로 호출합니다. API 키를 env에 넣지 마세요.
- **에이전트와 `_workspace/` 히스토리를 ignore하지 마세요.** 새 피쳐를 만질 때 기존 `.claude/agents/` 팀을 재활용하고, 해당 층의 `_workspace/*_delta.md`를 먼저 읽으세요.
- **append-only 원칙.** legacy 코드/필드를 삭제하지 말고 flag로 보존합니다.
