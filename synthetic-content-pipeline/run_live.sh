#!/bin/bash
# live 모드 파이프라인 실행
#
# 실행 방법 (WSL 터미널에서):
#   cd /mnt/e/SPOT_DEV/contextBuilder/synthetic-content-pipeline
#   bash run_live.sh

export PATH="$PATH:/mnt/c/Users/wjdwn/.codex/.sandbox-bin"
export SCP_LLM_MODE=live
export SCP_CODEX_CONCURRENCY=2
export SCP_CODEX_TIMEOUT=180

cd /mnt/e/SPOT_DEV/contextBuilder/synthetic-content-pipeline

echo "=== codex 확인 ==="
codex --version || { echo "[ERROR] codex not found"; exit 1; }

echo "=== 로그인 상태 ==="
codex login status 2>/dev/null || { echo "[ERROR] codex login 필요: codex login"; exit 1; }
echo "로그인 확인 완료"

echo "=== pipeline.db-journal 정리 ==="
rm -f pipeline.db-journal

echo "=== 파이프라인 실행 (live, 장안동 5 spots) ==="
.venv/bin/python3 scripts/batch_publish_mvp_500.py --limit 5 --region emd_jangan
