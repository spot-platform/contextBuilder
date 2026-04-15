# local-context-builder 구현 최종 리포트

> 하네스 실행 완료: 2026-04-13

## 실행 요약

5명 에이전트 팀이 `local-context-builder-plan.md`의 MVP 범위를 end-to-end로 구현했다.

| 태스크 | 소유               | 상태 | 결과                                                           |
| ------ | ------------------ | ---- | -------------------------------------------------------------- |
| T01    | infra-architect    | ✅   | 25개 파일, 플랜 §3 디렉토리 구조                               |
| T02    | schema-designer    | ✅   | 9개 모델, 0001 마이그레이션, 시드 38 region + 17 mapping rules |
| T03    | collector-engineer | ✅   | Kakao 클라이언트 + grid + full_rebuild, 14 respx 테스트        |
| T04    | processor-engineer | ✅   | 정규화·피처·페르소나·스팟시드·publish, pure-python 테스트      |
| T05    | integration-qa     | ✅   | 14 admin 엔드포인트, Celery wire, 경계면 10/10 PASS            |

## 경계면 검증 (최종)

모든 10개 경계면 PASS. 3개 이슈 발견 → 전부 해결 → open 0.

상세: `_workspace/05_qa_report.md`

## MVP 범위 밖 (v1.1으로 연기됨)

- 실유저 활동 데이터 결합 (merge_real_data는 스켈레톤)
- alpha/beta 보정 (인터페이스만 존재, 호출부 없음)
- time_match 페르소나 계산 (explanation_json에 null)
- 증분 갱신 스케줄러 (APScheduler)
- Slack 알림 자동화 (webhook 스텁만)

## 사용자가 채워야 할 것

1. **Kakao REST API 키**: `.env`의 `KAKAO_REST_API_KEY`
2. **수원시 행정동 실제 좌표**: `data/region_master_suwon.csv`의 `center_lng/lat`, `bbox_*` 컬럼은 placeholder. 실제 데이터는 통계청 SGIS 또는 수동 수집
3. **DB/Redis 접속 정보**: `.env`의 `DATABASE_URL`, `REDIS_URL`
4. **Admin API 키**: `.env`의 `ADMIN_API_KEY`

## 실행 방법

```bash
cd local-context-builder/

# 1. 환경변수 설정
cp .env.example .env
# .env 수정: KAKAO_REST_API_KEY, DATABASE_URL, REDIS_URL, ADMIN_API_KEY

# 2. 인프라 기동
docker compose up -d postgres redis
alembic upgrade head

# 3. 시드 적재
python -m scripts.load_region_master
python -m scripts.load_category_mapping

# 4. 서비스 기동
docker compose up -d app celery-worker
# 또는 로컬:
# uvicorn app.main:app --host 0.0.0.0 --port 8000
# celery -A app.celery_app worker -l info

# 5. 파이프라인 트리거 (X-Admin-Key 헤더 필수)
curl -X POST http://localhost:8000/admin/bootstrap \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" -d '{"target_city":"suwon"}'

curl -X POST http://localhost:8000/admin/full-rebuild \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" -d '{"target_city":"suwon"}'

curl -X POST http://localhost:8000/admin/build-features \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" -d '{"target_city":"suwon"}'

# 6. 최신 데이터셋 확인
curl http://localhost:8000/admin/dataset/latest -H "X-Admin-Key: $ADMIN_API_KEY"
```

## 테스트

```bash
# 단위 테스트 (DB 불필요)
pytest tests/ -v

# 통합 테스트 (Postgres 필요, opt-in)
INTEGRATION_DATABASE_URL=postgresql+psycopg://lcb:lcb@localhost:5432/lcb_test \
  pytest tests/test_integration_pipeline.py -v
```

## 아티팩트 경로

- `_workspace/01_infra/{README.md, env_schema.md}`
- `_workspace/02_schema/{model_index.md, column_contract.md}`
- `_workspace/03_collector/{README.md, api_surface.md}`
- `_workspace/04_processor/{README.md, dataflow.md}`
- `_workspace/05_qa_report.md`
- `_workspace/final_report.md` (이 문서)
