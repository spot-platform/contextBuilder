# Version Policy — Plan §9 전환 트리거 코드 매핑

> §9-1, §9-2, §9-3 의 정책을 `pipeline.publish.versioning` 모듈의 함수 시그니처로 변환.

## 함수 시그니처 (구현 위치: `src/pipeline/publish/versioning.py`)

```python
PolicyStatus = Literal["draft", "active", "deprecated", "archived"]
TransitionStrategy = Literal["immediate", "gradual", "ab_test"]

def create_version(
    dataset_version: str,
    *,
    status: PolicyStatus = "draft",
) -> str:
    """ContentVersionPolicy 새 row 생성 → policy id."""

def activate_version(
    dataset_version: str,
    *,
    replacement_for: Optional[str] = None,
) -> None:
    """draft → active. replacement_for가 있으면 그 버전을 deprecated로 동시에 전환 (atomic)."""

def evaluate_synthetic_share(real_spot_count: int) -> float:
    """§9-2 임계값 → synthetic 비중 (0.0 ~ 1.0)."""

def should_archive(real_spot_count: int) -> bool:
    """real_spot_count >= 50 일 때 True."""
```

## 임계값 매핑 (§9-2)

| real_spot_count | synthetic_share | trigger | policy 변경 |
|---|---|---|---|
| 0~9 | 1.00 | Phase 1 (Pure Synthetic) | active 유지 |
| 10~29 | 0.50 | Phase 2 진입 | 로깅만, status 유지 |
| 30~49 | 0.20 | Phase 2 후반 | 로깅만, status 유지 |
| ≥ 50 | 0.00 | Phase 3 → Sunset | `archived` 로 status 변경 |

## §9-3 버전 전환 흐름

```
v1 active 상태에서 v2 배포 시:
    1. create_version("v2", status="draft")            # ContentVersionPolicy insert
    2. (validator-engineer) v2 콘텐츠 전체 검증 완료
    3. activate_version("v2", replacement_for="v1")    # atomic switch
       - v1.status = deprecated
       - v1.deprecation_date = now()
       - v2.status = active
       - v2.activation_date = now()
       - v2.replacement_version = None
    4. (스케줄러) 30일 후:
       - v1.status = archived
```

## 트리거 호출 시점

- `pipeline publish --dataset-version vN` 실행 시:
  1. publisher.publish_dataset(vN) 호출
  2. activate_version(vN, replacement_for=현재_active_버전)
  3. evaluate_synthetic_share(real_spot_count) 계산 후 metric 로깅
  4. should_archive(real_spot_count) → True 면 별도 archived 알림

- 모니터링 잡 (별도, MVP 범위 외)에서 매일:
  - real_spot_count 카운트
  - should_archive() True 인 region/category 조합 archived 처리

## 에러 처리

- v2가 draft 인데 active로 가려는 시도 → ValueError
- replacement_for 가 active 상태가 아님 → ValueError
- 동시에 두 버전이 active 가 되는 것은 SQL unique 제약 없이 application-level 보장 (validator-engineer 는 이 invariant를 신뢰함)

## TODO

- ContentVersionPolicy CRUD 실제 SessionLocal 호출 구현 (현재는 스텁)
- atomic switch 시 동일 트랜잭션 사용 (`with SessionLocal.begin():`)
- transition_strategy=gradual 인 경우 비중 전환 스케줄러 별도 설계
