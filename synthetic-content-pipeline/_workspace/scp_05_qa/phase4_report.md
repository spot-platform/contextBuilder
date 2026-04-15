# Phase 4 QA Report — publish + versioning gate

> pipeline-qa / scp_05_qa_phase4_complete
> 2026-04-15
> Scope: `src/pipeline/publish/publisher.py`, `src/pipeline/publish/versioning.py`,
> `src/pipeline/jobs/publish.py`, `tests/test_publisher_smoke.py`, `tests/test_versioning_smoke.py`,
> `scripts/phase4_scenario.py`

---

## 1. Phase 4 산출물 체크

| 파일 | 상태 | 공개 API / 진입점 |
|---|---|---|
| `src/pipeline/publish/publisher.py` | OK | `Publisher(session, dataset_version=None)`, `publish_spot(SpotProcessResult) → PublishResult`, `PublishResult(spot_id, dataset_version, published_rows, skipped_rows, errors, to_dict())`, `publish_dataset()` (deprecated shim) |
| `src/pipeline/publish/versioning.py` | OK | `VersionManager(session)`: `create_draft / activate / deprecate / archive / archive_expired / get_active / list_versions / compute_synthetic_ratio / apply_transition_triggers`. `VersionStatus(str, Enum)`, `TransitionStrategy(str, Enum)`. 레거시: `evaluate_synthetic_share`, `should_archive` |
| `src/pipeline/jobs/publish.py` | OK | click `publish_command` (alias `main`). 옵션: `--spot-id / --spot-result-json / --spec-json / --dataset-version / --dry-run / --db-url`. stdout JSON |
| `tests/test_publisher_smoke.py` | 확장 완료 | 13 tc (smoke 2 + 케이스 1~5,7) |
| `tests/test_versioning_smoke.py` | 확장 완료 | 12 tc (smoke 3 + 케이스 6,8) |
| `scripts/phase4_scenario.py` | 신규 | §9 FSM end-to-end 전환 시나리오 |
| `_workspace/scp_01_infra/phase4_delta.md` | 참조 | FSM 다이어그램 + 전이 표 + §9-2 임계 매핑 |

### 경계면 재확인 (QA Boundary Audit 5번)
`publisher.py` → `synthetic_*` 테이블 칼럼 매핑을 수동 대조:

| Publisher 메서드 | 대상 테이블 | 사용 컬럼 | 불일치 |
|---|---|---|---|
| `_publish_feed` | `synthetic_feed_content` | dataset_version, spot_id, title, summary, cover_tags_json, supporter_label, price_label, region_label, time_label, status, quality_score, validation_status | 없음 |
| `_publish_detail` | `synthetic_spot_detail` | dataset_version, spot_id, title, description, plan_json, materials_json, target_audience, cost_breakdown_json, host_intro, policy_notes, quality_score, validation_status | 없음 |
| `_publish_messages` | `synthetic_spot_messages` | dataset_version, spot_id, message_type, speaker_type, speaker_id, content, created_at_simulated, quality_score, validation_status | 없음 |
| `_publish_review` | `synthetic_review` | dataset_version, spot_id, reviewer_agent_id, rating, review_text, tags_json, sentiment_score, quality_score, validation_status | 없음 |
| `_resolve_active_version` | `content_version_policy` | dataset_version, status, activation_date | 없음 |

---

## 2. 전환 시나리오 8 케이스 결과

| # | 케이스 | 파일 / 테스트 | PASS/FAIL | 핵심 assertion |
|---|---|---|---|---|
| 1 | rejected skip 매트릭스 | `test_publisher_rejected_skip_matrix` (parametrize 6개) | **PASS 6/6** | rejected type: `published_rows[t]==0`, `skipped_rows[t]>=1`, 다른 type 정상 |
| 2 | plan-only rejected | `test_publisher_plan_rejected_detail_approved` | **PASS** | `SyntheticSpotDetail.plan_json` 은 falsy, `skipped_rows["plan"]==1`, `published_rows["detail"]==1` |
| 3 | messages snippet 누락 (3/4) | `test_publisher_messages_missing_snippet` | **PASS** | `published_rows["messages"]==3`, 실제 DB row 수 3, caplog 에 "missing snippet" 경고 ≥1 |
| 4 | dry-run rollback 격리 | `test_publisher_dry_run_rollback_leaves_db_empty` | **PASS** | 2번째 session 으로 count(*) 확인 → feed/detail/messages/review 전부 0 |
| 5 | active 부재 → v_init 폴백 | `test_publisher_fallback_when_no_active_version` | **PASS** | `publisher.dataset_version=="v_init"`, `"no active content_version_policy"` WARNING 1건 |
| 6 | atomic switch 트랜잭션 격리 | `test_versioning_atomic_switch_normal` + `test_versioning_atomic_switch_rollback_on_exception` | **PASS 2/2** | 정상: v1 deprecated+v2 active+replacement_version="v2". 예외 주입 후 rollback: v1 active / v2 draft 그대로 |
| 7 | process_spot_full → publisher (stub) | `test_process_spot_full_to_publisher_stub` | **PASS** | SCP_LLM_MODE=stub, 5 type contents 전부 존재, `errors==[]`, feed/detail/review 중 최소 1건 publish. 실측: v1 첫 publish에서 feed=1/detail=1/messages=4/review=1 달성 (scenario.py 에서 재확인) |
| 8 | `compute_synthetic_ratio` 경계 (9/10/29/30/49/50) | `test_compute_synthetic_ratio_boundary` (parametrize 6개) | **PASS 6/6** | 9→1.0, 10→0.5, 29→0.5, 30→0.2, 49→0.2, 50→0.0 |

### pytest 실측 출력 (확장 25 tc)

```
tests/test_publisher_smoke.py::test_publisher_importable_and_instantiable PASSED
tests/test_publisher_smoke.py::test_publisher_publish_single_spot PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types0-expected_published0] PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types1-expected_published1] PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types2-expected_published2] PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types3-expected_published3] PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types4-expected_published4] PASSED
tests/test_publisher_smoke.py::test_publisher_rejected_skip_matrix[rejected_types5-expected_published5] PASSED
tests/test_publisher_smoke.py::test_publisher_plan_rejected_detail_approved PASSED
tests/test_publisher_smoke.py::test_publisher_messages_missing_snippet PASSED
tests/test_publisher_smoke.py::test_publisher_dry_run_rollback_leaves_db_empty PASSED
tests/test_publisher_smoke.py::test_publisher_fallback_when_no_active_version PASSED
tests/test_publisher_smoke.py::test_process_spot_full_to_publisher_stub PASSED
tests/test_versioning_smoke.py::test_versioning_full_lifecycle PASSED
tests/test_versioning_smoke.py::test_versioning_archive_expired_grace PASSED
tests/test_versioning_smoke.py::test_versioning_enums_importable PASSED
tests/test_versioning_smoke.py::test_versioning_atomic_switch_normal PASSED
tests/test_versioning_smoke.py::test_versioning_atomic_switch_rollback_on_exception PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[9-1.0] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[10-0.5] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[29-0.5] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[30-0.2] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[49-0.2] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_boundary[50-0.0] PASSED
tests/test_versioning_smoke.py::test_compute_synthetic_ratio_mid_range PASSED
======================= 25 passed, 67 warnings in 0.42s ========================
```

### scenario 스크립트 실측 요약 (`scripts/phase4_scenario.py`)

```
row_counts_by_version:
  v1: {feed:1, detail:1, messages:4, review:1}
  v2: {feed:1, detail:1, messages:4, review:1}
final_history:
  v1: archived   (activation 04:04:18.369, deprecation 04:04:18.374, replacement_version=v2)
  v2: active     (activation 04:04:18.374)
publish_results:
  spot_v1_first  @ v1: feed=1/detail=1/plan=0/messages=4/review=1  errors=[]
  spot_v2_second @ v2: feed=1/detail=1/plan=0/messages=4/review=1  errors=[]
```

stub 모드 `process_spot_full("spot_phase4", golden_cafe_sinchon_weekend)` 1회 실행 결과를 `spot_id` 만 바꿔 두 번 publish. 5 type 전부 approved 분류로 통과, messages 4 snippet 4 row 정상.

---

## 3. Phase 4 gate 판정

| # | 기준 | 통과? | 근거 |
|---|---|---|---|
| 1 | Publisher 가 5 content type 을 `synthetic_*` 테이블에 정상 insert | PASS | 케이스 7 (process_spot_full → publisher) + scenario.py 결과에서 feed/detail/messages(4)/review 전부 insert 확인. messages `message_type` 컬럼에 4 key 기록 |
| 2 | Publisher 가 rejected / 누락 content 를 graceful 하게 skip | PASS | 케이스 1 (parametrize 6개) + 케이스 2 (plan rejected) + 케이스 3 (snippet 누락) 모두 통과. skipped_rows 카운팅 + errors 없음 |
| 3 | VersionManager atomic switch (v1→deprecated & v2→active) 단일 transaction | PASS | 케이스 6 정상 flow + 예외 주입 후 rollback 확인. flush 예외 시 `v1 active / v2 draft` 그대로 유지 |
| 4 | `compute_synthetic_ratio` 임계 경계 6개 정확 | PASS | 9/10/29/30/49/50 parametrize 6/6 PASS |
| 5 | `archive_expired(grace_days=0)` 로 deprecated → archived 전환 | PASS | scenario.py 에서 v1 deprecated → archived 전환 확인 (final_history v1.status=archived). 단위는 `test_versioning_archive_expired_grace` 가 grace_days=30 으로 별도 커버 |
| 6 | 전체 회귀 134+ passed 유지 | PASS | **153 passed / 5 xfailed / 6 deselected** (live_codex). Phase 3 134 → Phase 4 QA 153 (+19). failures 0 |

→ **Phase 4 gate: 6/6 PASS**

---

## 4. §9 전환 FSM 다이어그램 + 트리거

```
         create_draft(v)               activate(v)                 deprecate(v) / activate(new)
   (none) ─────────────►  ┌───────┐  ──────────►  ┌────────┐  ──────────────────────────►  ┌────────────┐
                          │ DRAFT │                │ ACTIVE │                              │ DEPRECATED │
                          └───────┘                └────────┘                              └─────┬──────┘
                                                                                                  │
                                                         archive(v) [강제]                        │
                                                         archive_expired(grace_days) [cutoff]     │
                                                                                                  ▼
                                                                                            ┌──────────┐
                                                                                            │ ARCHIVED │  terminal
                                                                                            └──────────┘
```

### 전이 트리거 (versioning.py 소스 기준)

| 전이 | 메서드 | 부작용 |
|---|---|---|
| `(none) → draft` | `create_draft(v, transition_strategy, real_content_threshold)` | row insert. 중복 시 ValueError |
| `draft → active` | `activate(v)` | **atomic**: 기존 active 전부 deprecated 처리 (`deprecation_date=now`, `replacement_version=v`) + 자기 `status=active, activation_date=now` → 단일 flush |
| `active → deprecated` | `deprecate(v)` **또는** 다른 `activate(other)` | `deprecation_date=now` |
| `deprecated → archived` (수동) | `archive(v)` | `status=archived` |
| `deprecated → archived` (배치) | `archive_expired(grace_days)` | `deprecation_date ≤ now - grace_days` 인 것만 일괄 전환, archived list 반환 |

### §9-2 real_spot_count → synthetic_ratio 매핑

| real_spot_count | ratio | action | 의미 |
|---|---|---|---|
| 0–9 | 1.00 | keep | Phase 1 Pure Synthetic |
| 10–29 | 0.50 | scale_down | Phase 2 Mixed entry |
| 30–49 | 0.20 | scale_down | Phase 2 Mixed late |
| ≥ 50 | 0.00 | archive_pending | Phase 3 Sunset |

`apply_transition_triggers({key: count})` 는 active 1개 기준 카테고리/지역 key 별 action 추천 리스트를 반환만 함 (실제 meta 테이블 업데이트 없음 — §7 TODO).

---

## 5. 남은 TODO

| # | 항목 | 소유 | 비고 |
|---|---|---|---|
| T1 | `real_spot_count` 실소스 연결 | data-integrator + infra | `local-context-builder` 의 spot 메타 테이블과 SELECT-only 어댑터 필요. 현재는 `apply_transition_triggers({})` 입력이 외부 주입식 |
| T2 | `archive_expired` 스케줄러 잡 | infra | cron/Airflow 트리거. 함수는 준비, 호출 시점 미정 |
| T3 | `transition_strategy = gradual` 구현 | infra | 비중 점진 전환 — 현재는 immediate 만 의미 있음 |
| T4 | `transition_strategy = ab_test` 구현 | infra | 트래픽 라우터 필요 — MVP 범위 외 |
| T5 | Publisher 인스턴스의 `dataset_version` 캐시 무효화 | infra (운영 가이드) | 짧은 publish 잡 단위로 인스턴스 재생성 가정, 현재 코드로 충분 |
| T6 | diversity 튜닝 (§14 ≤ 0.60) | validator | Phase 3 metric 보고서에 기록된 이슈 추적 |
| T7 | 스팟당 소요 시간 튜닝 (§14 ≤ 30초) | generator | stub 에서는 충분히 빠름. live codex 에서 1~2분 소요 — 병렬화/캐시 연구 |
| T8 | `datetime.utcnow()` → `datetime.now(UTC)` 교체 | infra (마이너) | 67 DeprecationWarning, 기능 영향 없음 |
| T9 | xfail 5건 (golden E2E region_mismatch) | validator / generator | Phase 2 known issue, Phase 4 미영향 |

---

## 6. 파이프라인 전체 완성도 요약 (Phase 1 ~ Phase 4)

### 코드 완성도

| Phase | 범위 | 상태 |
|---|---|---|
| 1 | infra (DB/설정/테이블), content_spec_builder, 5종 생성기 스텁, publisher/versioning 스텁 | 완료 (Phase 3 까지 유지) |
| 2 | Layer 1~5 validators, generator stub → real prompt, critic 샘플러 | 완료 |
| 3 | loop `process_spot_full`, scoring, cross-reference, diversity, critic 통합, metrics, live smoke | 완료 (§14 5/7 PASS) |
| 4 | publisher 본 구현, versioning FSM, publish job CLI, QA 확장 | **이번 이터레이션 완료** |

### 회귀 테스트 누적 (stub 모드)

| 단계 | passed | xfailed | deselected (live) | 증감 |
|---|---|---|---|---|
| Phase 3 closing | 134 | 5 | 6 | — |
| Phase 4 infra smoke 스켈레톤 | 134 | 5 | 6 | +0 (이미 포함) |
| **Phase 4 QA 확장** (**이번**) | **153** | 5 | 6 | **+19** |

(신규 19 = publisher 11 + versioning 8. 기존 smoke 6 tc 가 확장 파일에 흡수되어 순증가는 19.)

| 파일 | tc 수 |
|---|---|
| `tests/test_publisher_smoke.py` | 13 |
| `tests/test_versioning_smoke.py` | 12 |
| 그 외 phase 1~3 | 128 |
| **총** | **153 passed** |

### §14 지표 누적 (Phase 3 측정 기준, Phase 4 에서 재측정 없음)

| 지표 | 목표 | Phase 3 결과 | 상태 |
|---|---|---|---|
| 1차 승인률 | ≥ 70% | 72% (stub) | PASS |
| 최종 승인률 | ≥ 95% | 96% | PASS |
| 평균 quality_score | ≥ 0.80 | 0.83 | PASS |
| 배치 내 diversity | ≤ 0.60 | 0.64 | MISS (T6) |
| 스팟당 LLM 호출 | ≤ 15 | 12 | PASS |
| 스팟당 소요 시간 | ≤ 30초 | 22초 (stub), 90+ 초 (live) | PASS (stub) / MISS (live — T7) |
| Critic 비용 비율 | ≤ 20% | 18% | PASS |

Phase 4 는 publish 단계이므로 §14 지표에 신규 회귀 없음 — publisher 가 classification 을 그대로 존중하며 DB 에 보존만 함.

### 결론

- **Phase 4 gate 6/6 PASS**, 전체 회귀 153 passed 유지.
- 남은 리스크는 §14 diversity 0.64 (T6), live codex 스팟 소요 90초+ (T7), real_spot_count 소스 연결 (T1) 세 가지로, 모두 Phase 4 게이트 밖이고 optimization/integration 과제.
- Publisher / VersionManager 는 FSM 불변식 (동시 active 1개, archived terminal, atomic switch)이 단위 테스트로 커버되었다.
- 다음 단계 (post-phase4 optimization) 에서 T1/T6/T7 을 우선 순위로 처리 가능한 상태.
