#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/logs"
STATE_DIR="$ROOT/logs/local-market-update-state"

BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-main}"
REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-origin}"
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-07:30,12:30,15:35}"
SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"
LABEL="${LOCAL_MARKET_UPDATE_LABEL:-com.marketlab.market-risk-update}"
PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-}"
PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-https://ymh1989.github.io/market-risk-dashboard}"
PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-12}"
PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-10}"
PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-2}"
MODE="${LOCAL_MARKET_UPDATE_MODE:-auto}"
FULL_TIMES="${LOCAL_MARKET_UPDATE_FULL_TIMES:-07:30,15:35}"
ONLY_AT_SCHEDULED_KST=0
SCHEDULE_STATE_FILE=""
SCHEDULED_TIME=""
SCHEDULED_DAY_TYPE=""

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-$BRANCH}"
  REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-$REMOTE}"
  TIMES="${LOCAL_MARKET_UPDATE_TIMES:-$TIMES}"
  SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-$SATURDAY_TIMES}"
  LABEL="${LOCAL_MARKET_UPDATE_LABEL:-$LABEL}"
  PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-$PYTHON_BIN}"
  PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-$PAGES_URL}"
  PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-$PAGES_VERIFY_ATTEMPTS}"
  PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-$PAGES_VERIFY_INTERVAL_SECONDS}"
  PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-$PAGES_DEPLOY_RETRIES}"
  MODE="${LOCAL_MARKET_UPDATE_MODE:-$MODE}"
  FULL_TIMES="${LOCAL_MARKET_UPDATE_FULL_TIMES:-$FULL_TIMES}"
fi

for arg in "$@"; do
  case "$arg" in
    --only-at-scheduled-kst)
      ONLY_AT_SCHEDULED_KST=1
      ;;
    --fast|--mode=fast)
      MODE="fast"
      ;;
    --full|--mode=full)
      MODE="full"
      ;;
    --mode=auto)
      MODE="auto"
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
  local now_time now_weekday scheduled_time state_file state_key active_times schedule_label
  now_time="$(kst_now +%H:%M)"
  now_weekday="$(kst_now +%u)"

  case "$now_weekday" in
    1|2|3|4|5)
      active_times="$TIMES"
      schedule_label="평일"
      SCHEDULED_DAY_TYPE="weekday"
      ;;
    6)
      active_times="$SATURDAY_TIMES"
      schedule_label="토요일"
      SCHEDULED_DAY_TYPE="saturday"
      ;;
    *)
      echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 일요일은 예약 갱신을 건너뜁니다."
      return 1
      ;;
  esac

  IFS=',' read -ra schedule_times <<< "$active_times"
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
      SCHEDULED_TIME="$scheduled_time"
      return 0
    fi
  done

  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] $schedule_label 예약 시각($active_times KST)이 아니어서 건너뜁니다."
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

resolve_update_mode() {
  local full_time
  case "$MODE" in
    full|fast)
      echo "$MODE"
      ;;
    auto)
      if [[ -z "$SCHEDULED_TIME" ]]; then
        echo "full"
        return 0
      fi
      if [[ "$SCHEDULED_DAY_TYPE" == "saturday" ]]; then
        echo "full"
        return 0
      fi
      IFS=',' read -ra full_times <<< "$FULL_TIMES"
      for full_time in "${full_times[@]}"; do
        full_time="${full_time//[[:space:]]/}"
        if [[ "$SCHEDULED_TIME" == "$full_time" ]]; then
          echo "full"
          return 0
        fi
      done
      echo "fast"
      ;;
    *)
      echo "알 수 없는 갱신 모드입니다: $MODE" >&2
      exit 2
      ;;
  esac
}

if (( ONLY_AT_SCHEDULED_KST )); then
  is_scheduled_now || exit 0
fi

UPDATE_MODE="$(resolve_update_mode)"
RUN_STARTED_EPOCH="$(date +%s)"
RUN_STARTED_AT="$(kst_now '+%Y-%m-%d %H:%M:%S KST')"

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
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Skipping features without any observed values:UserWarning}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$WORKTREE/.cache/matplotlib}"
mkdir -p "$MPLCONFIGDIR"

seed_local_data_cache() {
  local filename source_file
  mkdir -p "$WORKTREE/data/raw"
  for filename in market_data.csv market_data_sources.json; do
    source_file="$ROOT/data/raw/$filename"
    if [[ -f "$source_file" ]]; then
      cp -p "$source_file" "$WORKTREE/data/raw/$filename"
      echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 로컬 원시 데이터 캐시를 사용합니다: $filename"
    fi
  done
}

persist_local_data_cache() {
  local filename source_file
  mkdir -p "$ROOT/data/raw"
  for filename in market_data.csv market_data_sources.json; do
    source_file="$WORKTREE/data/raw/$filename"
    if [[ -f "$source_file" ]]; then
      cp -p "$source_file" "$ROOT/data/raw/$filename"
    fi
  done
}

seed_local_data_cache

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 갱신 모드: $UPDATE_MODE"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 시장리스크 데이터를 갱신합니다."
MARKET_STAGE_STARTED_EPOCH="$(date +%s)"
make update-market-risk
make backtest-market-risk
if [[ "$UPDATE_MODE" == "full" ]]; then
  REFRESH_STRESS_CACHE=1 make analyze-stress-episodes
else
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] fast 모드: 스트레스 에피소드 히스토리 재계산을 생략합니다."
fi
python3 scripts/export_els_index_risk.py
python3 scripts/export_hmm_regime.py
MARKET_STAGE_COMPLETED_EPOCH="$(date +%s)"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] ML risk-off 산출물을 갱신합니다."
ML_STAGE_STARTED_EPOCH="$(date +%s)"
"$PYTHON_BIN" -m kospi_risk.cli fetch-market-data --source-config configs/data_sources.yaml --output data/raw/market_data.csv --metadata data/raw/market_data_sources.json --min-rows 1500
persist_local_data_cache
"$PYTHON_BIN" -m kospi_risk.cli build-features --input data/raw/market_data.csv --output data/processed/features.parquet --config configs/base.yaml
"$PYTHON_BIN" -m kospi_risk.cli train --features data/processed/features.parquet --config configs/base.yaml
if [[ "$UPDATE_MODE" == "full" ]]; then
  "$PYTHON_BIN" -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md
else
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] fast 모드: ML walk-forward 백테스트를 생략하고 직전 OOS 메트릭을 재사용합니다."
fi
"$PYTHON_BIN" -m kospi_risk.cli predict-latest --features data/processed/features.parquet --config configs/base.yaml --output reports/latest_signal.csv
"$PYTHON_BIN" scripts/export_ml_risk_signal.py
ML_STAGE_COMPLETED_EPOCH="$(date +%s)"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 대시보드 데이터를 검증합니다."
VALIDATION_STAGE_STARTED_EPOCH="$(date +%s)"
"$PYTHON_BIN" scripts/audit_data_completeness.py --strict
make test
VALIDATION_STAGE_COMPLETED_EPOCH="$(date +%s)"
RUN_COMPLETED_AT="$(kst_now '+%Y-%m-%d %H:%M:%S KST')"
RUN_COMPLETED_EPOCH="$(date +%s)"

"$PYTHON_BIN" scripts/write_pipeline_status.py \
  --mode "$UPDATE_MODE" \
  --times "$TIMES" \
  --saturday-times "$SATURDAY_TIMES" \
  --full-times "$FULL_TIMES" \
  --scheduled-time "$SCHEDULED_TIME" \
  --started-at "$RUN_STARTED_AT" \
  --completed-at "$RUN_COMPLETED_AT" \
  --total-duration "$((RUN_COMPLETED_EPOCH - RUN_STARTED_EPOCH))" \
  --market-duration "$((MARKET_STAGE_COMPLETED_EPOCH - MARKET_STAGE_STARTED_EPOCH))" \
  --ml-duration "$((ML_STAGE_COMPLETED_EPOCH - ML_STAGE_STARTED_EPOCH))" \
  --validation-duration "$((VALIDATION_STAGE_COMPLETED_EPOCH - VALIDATION_STAGE_STARTED_EPOCH))"

git config user.name "${LOCAL_MARKET_UPDATE_GIT_NAME:-local-market-risk-bot}"
git config user.email "${LOCAL_MARKET_UPDATE_GIT_EMAIL:-local-market-risk-bot@users.noreply.github.com}"

git add \
  data/risk-dashboard.json \
  data/market-risk-snapshot.json \
  data/market-risk-timeseries.json \
  data/naver-marketindex-history.json \
  data/market-risk-backtest.json \
  data/market-stress-episodes.json \
  data/market-history-cache.json \
  data/els-index-risk.json \
  data/hmm-regime.json \
  data/ml-risk-signal.json \
  data/data-quality.json \
  data/pipeline-status.json

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
