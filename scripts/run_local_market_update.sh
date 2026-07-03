#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/logs"
STATE_DIR="$ROOT/logs/local-market-update-state"

BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-main}"
REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-origin}"
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-08:30,12:30,16:10}"
LABEL="${LOCAL_MARKET_UPDATE_LABEL:-com.marketlab.market-risk-update}"
PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-}"
PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-https://ymh1989.github.io/market-risk-dashboard}"
PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-12}"
PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-10}"
PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-2}"
ONLY_AT_SCHEDULED_KST=0
SCHEDULE_STATE_FILE=""

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-$BRANCH}"
  REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-$REMOTE}"
  TIMES="${LOCAL_MARKET_UPDATE_TIMES:-$TIMES}"
  LABEL="${LOCAL_MARKET_UPDATE_LABEL:-$LABEL}"
  PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-$PYTHON_BIN}"
  PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-$PAGES_URL}"
  PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-$PAGES_VERIFY_ATTEMPTS}"
  PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-$PAGES_VERIFY_INTERVAL_SECONDS}"
  PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-$PAGES_DEPLOY_RETRIES}"
fi

for arg in "$@"; do
  case "$arg" in
    --only-at-scheduled-kst)
      ONLY_AT_SCHEDULED_KST=1
      ;;
    *)
      echo "알 수 없는 옵션입니다: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

kst_now() {
  TZ=Asia/Seoul date "$1"
}

is_scheduled_now() {
  local now_time now_weekday scheduled_time state_file state_key
  now_time="$(kst_now +%H:%M)"
  now_weekday="$(kst_now +%u)"

  if (( now_weekday > 5 )); then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 평일이 아니어서 건너뜁니다."
    return 1
  fi

  IFS=',' read -ra schedule_times <<< "$TIMES"
  for scheduled_time in "${schedule_times[@]}"; do
    scheduled_time="${scheduled_time//[[:space:]]/}"
    if [[ "$now_time" == "$scheduled_time" ]]; then
      mkdir -p "$STATE_DIR"
      state_key="$(kst_now +%Y-%m-%d)-$scheduled_time"
      state_file="$STATE_DIR/$state_key.done"
      if [[ -f "$state_file" ]]; then
        echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 이미 실행한 예약 시각입니다: $scheduled_time"
        return 1
      fi
      SCHEDULE_STATE_FILE="$state_file"
      return 0
    fi
  done

  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 예약 시각($TIMES KST)이 아니어서 건너뜁니다."
  return 1
}

mark_scheduled_done() {
  if [[ -n "$SCHEDULE_STATE_FILE" ]]; then
    printf "%s\n" "$(kst_now '+%Y-%m-%d %H:%M:%S KST')" > "$SCHEDULE_STATE_FILE"
  fi
}

pages_generated_at() {
  local response
  response="$(curl -fsSL --max-time 20 -H "Cache-Control: no-cache" "${PAGES_URL%/}/data/ml-risk-signal.json?check=$(date +%s)" 2>/dev/null || true)"
  if [[ -z "$response" ]]; then
    return 0
  fi
  "$PYTHON_BIN" -c 'import json, sys; print(json.load(sys.stdin).get("generatedAt", ""))' <<< "$response" 2>/dev/null || true
}

wait_for_pages_deployment() {
  local expected_generated_at="$1"
  local attempt deployed_generated_at

  for ((attempt = 1; attempt <= PAGES_VERIFY_ATTEMPTS; attempt++)); do
    deployed_generated_at="$(pages_generated_at)"
    if [[ "$deployed_generated_at" == "$expected_generated_at" ]]; then
      echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] GitHub Pages 반영을 확인했습니다: $deployed_generated_at"
      return 0
    fi
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] Pages 반영 대기 중 ($attempt/$PAGES_VERIFY_ATTEMPTS): ${deployed_generated_at:-응답 없음}"
    sleep "$PAGES_VERIFY_INTERVAL_SECONDS"
  done
  return 1
}

if (( ONLY_AT_SCHEDULED_KST )); then
  is_scheduled_now || exit 0
fi

mkdir -p "$LOG_DIR"
LOCK_DIR="$LOG_DIR/.local-market-update.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "다른 로컬 시장리스크 갱신 작업이 실행 중입니다." >&2
  exit 1
fi

WORKTREE=""
cleanup() {
  rm -rf "$LOCK_DIR"
  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    git -C "$ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"
  fi
}
trap cleanup EXIT

WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/market-risk-update.XXXXXX")"
rm -rf "$WORKTREE"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] $REMOTE/$BRANCH 기준 임시 worktree를 준비합니다."
git -C "$ROOT" fetch "$REMOTE" "$BRANCH"
git -C "$ROOT" worktree add --detach "$WORKTREE" "$REMOTE/$BRANCH"

cd "$WORKTREE"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$WORKTREE/src"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 시장리스크 데이터를 갱신합니다."
make update-market-risk
make backtest-market-risk
REFRESH_STRESS_CACHE=1 make analyze-stress-episodes
python3 scripts/export_els_index_risk.py

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] ML risk-off 산출물을 갱신합니다."
"$PYTHON_BIN" -m kospi_risk.cli fetch-market-data --source-config configs/data_sources.yaml --output data/raw/market_data.csv --metadata data/raw/market_data_sources.json --range 10y --min-rows 1500
"$PYTHON_BIN" -m kospi_risk.cli build-features --input data/raw/market_data.csv --output data/processed/features.parquet --config configs/base.yaml
"$PYTHON_BIN" -m kospi_risk.cli train --features data/processed/features.parquet --config configs/base.yaml
"$PYTHON_BIN" -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md
"$PYTHON_BIN" -m kospi_risk.cli predict-latest --features data/processed/features.parquet --config configs/base.yaml --output reports/latest_signal.csv
"$PYTHON_BIN" scripts/export_ml_risk_signal.py

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 대시보드 데이터를 검증합니다."
make test

git config user.name "${LOCAL_MARKET_UPDATE_GIT_NAME:-local-market-risk-bot}"
git config user.email "${LOCAL_MARKET_UPDATE_GIT_EMAIL:-local-market-risk-bot@users.noreply.github.com}"

git add \
  data/risk-dashboard.json \
  data/market-risk-snapshot.json \
  data/market-risk-timeseries.json \
  data/market-risk-backtest.json \
  data/market-stress-episodes.json \
  data/market-history-cache.json \
  data/els-index-risk.json \
  data/ml-risk-signal.json

if git diff --cached --quiet; then
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 변경된 데이터가 없어 커밋하지 않습니다."
else
  git commit -m "Update market risk data"
  git push "$REMOTE" "HEAD:$BRANCH"
fi

EXPECTED_GENERATED_AT="$("$PYTHON_BIN" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["generatedAt"])' data/ml-risk-signal.json)"
for ((retry = 0; retry <= PAGES_DEPLOY_RETRIES; retry++)); do
  if wait_for_pages_deployment "$EXPECTED_GENERATED_AT"; then
    mark_scheduled_done
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 로컬 예약 갱신을 완료했습니다."
    exit 0
  fi

  if ((retry < PAGES_DEPLOY_RETRIES)); then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] Pages 미반영으로 재배포를 요청합니다 ($((retry + 1))/$PAGES_DEPLOY_RETRIES)."
    git commit --allow-empty -m "Retry GitHub Pages deployment"
    git push "$REMOTE" "HEAD:$BRANCH"
  fi
done

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] GitHub Pages가 최신 데이터로 반영되지 않았습니다." >&2
exit 1
