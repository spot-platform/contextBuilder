# Phase 4 Delta — publish + versioning 본 구현

> scp_01_infra_phase4_complete (pipeline-infra-architect)
> 2026-04-15

Phase 1 의 publisher / versioning 스텁을 본 구현으로 교체. 생성/검증/스코어링 로직은 건드리지 않음.

---

## 1. publisher.py 구조

`src/pipeline/publish/publisher.py`

```
Publisher(session, dataset_version=None)
    │
    ├─ _resolve_active_version()   # content_version_policy.status='active' 자동 선택
    │                              # 없으면 'v_init' 폴백
    │
    └─ publish_spot(SpotProcessResult) → PublishResult
            │
            ├─ feed       → _publish_feed       → SyntheticFeedContent (1 row)
            ├─ detail     → _publish_detail     → SyntheticSpotDetail  (1 row)
            │                                     plan_cpr 가 publishable 이면 plan_json 컬럼에 embed
            ├─ plan       → (별도 row 없음, detail 에 묻힘)
            ├─ messages   → _publish_messages   → SyntheticSpotMessages (4 row, 1 per snippet)
            └─ review     → _publish_review     → SyntheticReview (1 row)
```

### 책임 분리

| 책임 | 담당 |
|---|---|
| classification 계산 | loop/scoring (Phase 3, 변경 없음) |
| content_validation_log insert | validators (Phase 2/3, 변경 없음) |
| synthetic_* row insert | **Publisher (Phase 4)** |
| dataset_version 결정 | **Publisher → VersionManager 조회** |
| commit/rollback | 호출자 (publish.py CLI 또는 테스트) — Publisher 는 flush 만 |

### rejected 처리

- `cpr.classification == "rejected"` → publish 안 함, `skipped_rows[content_type] += 1`
- `selected_candidate is None` → 동일하게 skip
- exception → `errors` 리스트에 누적, 다른 type 은 계속 진행

### plan embed 규칙

`plan` 은 Phase 1 스키마 §8 에 별도 테이블이 없다 (`synthetic_spot_detail.plan_json` JSONB 만 존재).
따라서 `_publish_detail` 이 `plan_cpr` 를 받아서 publishable 이면 그 payload 를 `plan_json` 컬럼에 그대로 저장.
plan 이 rejected 라도 detail 은 publish 가능 (plan_json 만 None).

### messages 4 → 4 row

| key | message_type | speaker_type | speaker_id | created_at_simulated |
|---|---|---|---|---|
| recruiting_intro | "recruiting_intro" | host | NULL | NULL |
| join_approval    | "join_approval"    | host | NULL | NULL |
| day_of_notice    | "day_of_notice"    | host | NULL | NULL |
| post_thanks      | "post_thanks"      | host | NULL | NULL |

전부 quality_score / validation_status 는 동일 (cpr 단위).

---

## 2. versioning.py FSM

`src/pipeline/publish/versioning.py`

```
                create_draft()
                   │
                   ▼
                ┌───────┐
                │ DRAFT │
                └───┬───┘
                    │ activate()
                    │   ├─ 기존 ACTIVE → DEPRECATED (atomic, 같은 트랜잭션)
                    │   └─ 자기 → ACTIVE, activation_date=now()
                    ▼
                ┌────────┐
                │ ACTIVE │
                └───┬────┘
                    │ deprecate()  또는  다른 버전의 activate() 호출
                    │   deprecation_date=now()
                    │   replacement_version=새 버전
                    ▼
                ┌────────────┐
                │ DEPRECATED │
                └─────┬──────┘
                      │ archive()                  → 강제 전환
                      │ archive_expired(grace_days)→ deprecation_date+grace_days 경과한 것만
                      ▼
                ┌──────────┐
                │ ARCHIVED │  (terminal)
                └──────────┘
```

### 전이 표

| from | to | 메서드 | 조건 |
|---|---|---|---|
| (none) | DRAFT | `create_draft(v)` | 동일 dataset_version 이 없어야 함 |
| DRAFT | ACTIVE | `activate(v)` | 대상이 DRAFT 여야 함; 기존 ACTIVE 는 자동 DEPRECATED |
| ACTIVE | DEPRECATED | `deprecate(v)` 또는 다른 `activate(other)` | deprecation_date=now |
| DEPRECATED | ARCHIVED | `archive(v)` | 강제 |
| DEPRECATED | ARCHIVED | `archive_expired(grace_days)` | `deprecation_date + grace_days <= now` |

### 불변식

- 동시에 ACTIVE 가 두 개일 수 없다 — `activate()` 가 기존 ACTIVE 들을 모두 DEPRECATED 처리.
- ARCHIVED 는 terminal — 다시 ACTIVE 로 못 돌린다.
- DRAFT 는 직접 DEPRECATED/ARCHIVED 로 못 간다 (먼저 ACTIVE 거쳐야 함).

---

## 3. §9-2 전환 트리거 → compute_synthetic_ratio 매핑

| real_spot_count | synthetic_ratio | 의미 (§9-1 phase) | 권장 action |
|---|---|---|---|
| 0 ~ 9   | 1.00 | Phase 1 Pure Synthetic | `keep` (active 유지) |
| 10 ~ 29 | 0.50 | Phase 2 Mixed (entry)  | `scale_down` |
| 30 ~ 49 | 0.20 | Phase 2 Mixed (late)   | `scale_down` |
| ≥ 50    | 0.00 | Phase 3 → Sunset       | `archive_pending` (별도 archive 잡 필요) |

`policy.real_content_threshold` 가 명시되면 0.5 진입점이 거기로 이동하고, 0.2 / 0.0 임계는 비례적으로 (×3 / ×5) 이동한다.

`apply_transition_triggers({region|category: count, ...})` 는 active 버전 1 개를 기준으로 카테고리/지역 조합별 권장 action 리스트를 반환만 한다. 실제 비중 적용은 별도 meta 테이블이 없으므로 보류.

---

## 4. publish job CLI

`src/pipeline/jobs/publish.py`

옵션:

| flag | 설명 |
|---|---|
| `--spot-id` | 필수. publish 대상 spot id |
| `--spot-result-json` | (Path) `SpotProcessResult.to_dict()` JSON 경로. 우선순위 1. |
| `--spec-json`        | (Path) `ContentSpec` JSON. spot-result-json 없을 때 `process_spot_full` 호출. |
| `--dataset-version`  | 명시 시 그 버전, 생략 시 active 자동 |
| `--dry-run`          | flush 후 rollback. DB 변경 없이 PublishResult JSON 출력 |
| `--db-url`           | SQLAlchemy URL. 생략 시 `SCP_DB_URL` 또는 `sqlite:///./pipeline.db` |

stdout 출력: `PublishResult.to_dict()` 한 줄 JSON + `dry_run` 키.

---

## 5. DB 마이그레이션

Phase 1 `0001_initial_schema.py` 에 다음이 모두 정의되어 있어 추가 마이그레이션 불필요:

- `synthetic_spot_messages.message_type` (VARCHAR(30) NOT NULL) ✓
- `content_version_policy` 의 모든 컬럼 (dataset_version / status / activation_date / deprecation_date / replacement_version / transition_strategy / real_content_threshold / created_at) ✓

따라서 phase 4 마이그레이션 신규 리비전 없음.

---

## 6. 단위 테스트 스켈레톤

| 파일 | 책임 |
|---|---|
| `tests/test_publisher_smoke.py` | Publisher import + 인스턴스화 + 1 spot publish (4 row in messages 확인) |
| `tests/test_versioning_smoke.py` | 라이프사이클 + atomic switch + archive_expired + compute_synthetic_ratio 임계 |

세부 케이스 (예: rejected skip, plan embed 검증, exception 시 errors 누적) 는 **pipeline-qa 가 Phase 4 gate 에서 채운다**.

---

## 7. 남은 TODO

1. **`real_spot_count` 소스 부재**
   `apply_transition_triggers` 에 넘길 카테고리/지역별 real_spot_count 가 어디서 오는지 미정.
   현재 synthetic-content-pipeline 안에는 real spot 카운트 가능한 테이블이 없음 (`synthetic_*` 만 존재).
   → `local-context-builder` 의 spot 메타 테이블과 연동 필요. 별도 ETL 잡 또는 SELECT-only 어댑터.

2. **archive 스케줄러 별도 잡 부재**
   `archive_expired()` 가 함수로 존재하지만 호출 시점 (cron / Airflow 등) 은 미정. MVP 범위 외.

3. **transition_strategy=gradual / ab_test 미구현**
   현재는 immediate 만 의미가 있음. gradual 은 비중 점진 전환 스케줄러 필요, ab_test 는 트래픽 라우터 필요 — 둘 다 인프라 책임 외.

4. **`publish.py` 에서 process_spot_full 경로의 회귀 비용**
   ChatGPT 구독 기반이라 live 호출 spot 1 개 publish 만 해도 1~2 분 소요. CI 에서는 stub 모드로 제한.

5. **Publisher의 dataset_version 캐시 무효화**
   Publisher 인스턴스가 살아있는 동안 active 버전이 바뀌면 stale 가능. 짧은 publish 잡 단위로 인스턴스 재생성하는 운영 가정.

---

## 8. 회귀 영향

- Phase 3 의 loop / generators / validators / scoring / metrics — 변경 없음.
- 기존 `tests/` 128 passed (Phase 3 결과) 유지 + `tests/test_publisher_smoke.py` (2 tc) + `tests/test_versioning_smoke.py` (4 tc) = **134 passed 목표**.
