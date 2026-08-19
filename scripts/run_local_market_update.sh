#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/logs"
STATE_DIR="$ROOT/logs/local-market-update-state"

BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-main}"
REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-origin}"
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-07:30,12:30,15:35}"
MONDAY_TIMES="${LOCAL_MARKET_UPDATE_MONDAY_TIMES:-12:30,15:35}"
SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"
LABEL="${LOCAL_MARKET_UPDATE_LABEL:-com.marketlab.market-risk-update}"
PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-}"
KOSPI_BREADTH_PYTHON="${KOSPI_BREADTH_PYTHON:-}"
OVERNIGHT_CANDIDATE_DIR="${OVERNIGHT_PREPARE_CANDIDATE_DIR:-$ROOT/data/cache/overnight-market-prepare/current}"
PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-https://ymh1989.github.io/market-risk-dashboard}"
PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-12}"
PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-10}"
PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-2}"
MODE="${LOCAL_MARKET_UPDATE_MODE:-auto}"
FULL_TIMES="${LOCAL_MARKET_UPDATE_FULL_TIMES:-15:35}"
SCHEDULE_GRACE_MINUTES="${LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES:-10}"
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
  MONDAY_TIMES="${LOCAL_MARKET_UPDATE_MONDAY_TIMES:-$MONDAY_TIMES}"
  SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-$SATURDAY_TIMES}"
  LABEL="${LOCAL_MARKET_UPDATE_LABEL:-$LABEL}"
  PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-$PYTHON_BIN}"
  KOSPI_BREADTH_PYTHON="${KOSPI_BREADTH_PYTHON:-$KOSPI_BREADTH_PYTHON}"
  OVERNIGHT_CANDIDATE_DIR="${OVERNIGHT_PREPARE_CANDIDATE_DIR:-$OVERNIGHT_CANDIDATE_DIR}"
  PAGES_URL="${LOCAL_MARKET_UPDATE_PAGES_URL:-$PAGES_URL}"
  PAGES_VERIFY_ATTEMPTS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_ATTEMPTS:-$PAGES_VERIFY_ATTEMPTS}"
  PAGES_VERIFY_INTERVAL_SECONDS="${LOCAL_MARKET_UPDATE_PAGES_VERIFY_INTERVAL_SECONDS:-$PAGES_VERIFY_INTERVAL_SECONDS}"
  PAGES_DEPLOY_RETRIES="${LOCAL_MARKET_UPDATE_PAGES_DEPLOY_RETRIES:-$PAGES_DEPLOY_RETRIES}"
  MODE="${LOCAL_MARKET_UPDATE_MODE:-$MODE}"
  FULL_TIMES="${LOCAL_MARKET_UPDATE_FULL_TIMES:-$FULL_TIMES}"
  SCHEDULE_GRACE_MINUTES="${LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES:-$SCHEDULE_GRACE_MINUTES}"
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

if [[ -z "$KOSPI_BREADTH_PYTHON" ]]; then
  KOSPI_BREADTH_PYTHON="$ROOT/.venv-breadth/bin/python"
fi
if [[ ! -x "$KOSPI_BREADTH_PYTHON" ]]; then
  echo "KOSPI Market Breadth용 Python을 찾지 못했습니다: $KOSPI_BREADTH_PYTHON" >&2
  echo ".venv-breadth에 pykrx를 설치하거나 KOSPI_BREADTH_PYTHON을 지정하세요." >&2
  exit 1
fi

kst_now() {
  TZ=Asia/Seoul date "$1"
}

is_scheduled_now() {
  local now_time now_weekday now_minutes scheduled_time scheduled_minutes elapsed_minutes
  local state_file state_key active_times schedule_label
  now_time="$(kst_now +%H:%M)"
  now_weekday="$(kst_now +%u)"
  now_minutes="$((10#${now_time%:*} * 60 + 10#${now_time#*:}))"

  case "$now_weekday" in
    1)
      active_times="$MONDAY_TIMES"
      schedule_label="월요일"
      SCHEDULED_DAY_TYPE="weekday"
      ;;
    2|3|4|5)
      active_times="$TIMES"
      schedule_label="화~금"
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
    scheduled_minutes="$((10#${scheduled_time%:*} * 60 + 10#${scheduled_time#*:}))"
    elapsed_minutes="$((now_minutes - scheduled_minutes))"
    if (( elapsed_minutes >= 0 && elapsed_minutes <= SCHEDULE_GRACE_MINUTES )); then
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

pages_publication_run_id() {
  local response
  response="$(curl -fsSL --max-time 20 -H "Cache-Control: no-cache" "${PAGES_URL%/}/data/publication-manifest.json?check=$(date +%s)" 2>/dev/null || true)"
  if [[ -z "$response" ]]; then
    return 0
  fi
  "$PYTHON_BIN" -c 'import json, sys; payload=json.load(sys.stdin); print(payload.get("runId", "") if payload.get("status") == "ready" else "")' <<< "$response" 2>/dev/null || true
}

wait_for_pages_deployment() {
  local expected_run_id="$1"
  local attempt deployed_run_id

  for ((attempt = 1; attempt <= PAGES_VERIFY_ATTEMPTS; attempt++)); do
    deployed_run_id="$(pages_publication_run_id)"
    if [[ "$deployed_run_id" == "$expected_run_id" ]]; then
      echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] GitHub Pages 원자적 스냅샷 반영을 확인했습니다: $deployed_run_id"
      return 0
    fi
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] Pages 반영 대기 중 ($attempt/$PAGES_VERIFY_ATTEMPTS): ${deployed_run_id:-manifest 응답 없음}"
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
RUN_TRIGGER_ID="${SCHEDULED_TIME:-manual}"
RUN_TRIGGER_ID="${RUN_TRIGGER_ID/:/}"
RUN_ID="$(kst_now '+%Y%m%dT%H%M%S')-${RUN_TRIGGER_ID}-${UPDATE_MODE}"
export MARKET_UPDATE_RUN_ID="$RUN_ID"

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
export M7_CREDIT_RAW_DIR="${M7_CREDIT_RAW_DIR:-$ROOT/data/raw/m7_credit_proxy}"
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
  mkdir -p "$WORKTREE/data/processed" "$WORKTREE/data/quality"
  if [[ -f "$ROOT/data/processed/kospi_breadth.parquet" ]]; then
    cp -p "$ROOT/data/processed/kospi_breadth.parquet" "$WORKTREE/data/processed/kospi_breadth.parquet"
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 로컬 Market Breadth 캐시를 사용합니다."
  fi
  if [[ -f "$ROOT/data/quality/kospi_breadth_update.json" ]]; then
    cp -p "$ROOT/data/quality/kospi_breadth_update.json" "$WORKTREE/data/quality/kospi_breadth_update.json"
  fi
  mkdir -p "$WORKTREE/data/cache"
  if [[ -f "$ROOT/data/cache/walk_forward_backtest.joblib" ]]; then
    cp -p "$ROOT/data/cache/walk_forward_backtest.joblib" "$WORKTREE/data/cache/walk_forward_backtest.joblib"
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 로컬 Walk-forward fold 캐시를 사용합니다."
  fi
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
  mkdir -p "$ROOT/data/processed" "$ROOT/data/quality"
  if [[ -f "$WORKTREE/data/processed/kospi_breadth.parquet" ]]; then
    cp -p "$WORKTREE/data/processed/kospi_breadth.parquet" "$ROOT/data/processed/kospi_breadth.parquet"
  fi
  if [[ -f "$WORKTREE/data/quality/kospi_breadth_update.json" ]]; then
    cp -p "$WORKTREE/data/quality/kospi_breadth_update.json" "$ROOT/data/quality/kospi_breadth_update.json"
  fi
  if [[ -f "$WORKTREE/data/cache/walk_forward_backtest.joblib" ]]; then
    mkdir -p "$ROOT/data/cache"
    cp -p "$WORKTREE/data/cache/walk_forward_backtest.joblib" "$ROOT/data/cache/walk_forward_backtest.joblib"
  fi
}

seed_local_data_cache

OVERNIGHT_MARKET_DATA_SHA=""
seed_overnight_candidate() {
  local worktree_commit candidate_sha
  if [[ "$SCHEDULED_TIME" != "07:30" || ! -d "$OVERNIGHT_CANDIDATE_DIR" ]]; then
    return 0
  fi
  worktree_commit="$(git rev-parse HEAD)"
  candidate_sha="$("$PYTHON_BIN" scripts/overnight_market_prepare.py verify \
    --root "$OVERNIGHT_CANDIDATE_DIR" \
    --expected-commit "$worktree_commit" \
    --max-age-hours 4 \
    --format sha 2>/dev/null || true)"
  if [[ -z "$candidate_sha" ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 야간 후보가 없거나 현재 코드와 달라 기존 계산 경로를 사용합니다."
    return 0
  fi
  cp -p "$OVERNIGHT_CANDIDATE_DIR/raw/market_data.csv" data/raw/market_data.csv
  cp -p "$OVERNIGHT_CANDIDATE_DIR/raw/market_data_sources.json" data/raw/market_data_sources.json
  cp -p "$OVERNIGHT_CANDIDATE_DIR/dashboard/market-history-cache.json" data/market-history-cache.json
  cp -p "$OVERNIGHT_CANDIDATE_DIR/dashboard/naver-marketindex-history.json" data/naver-marketindex-history.json
  OVERNIGHT_MARKET_DATA_SHA="$candidate_sha"
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 검증된 야간 미국장 후보와 시장 시계열 캐시를 불러왔습니다."
}

seed_overnight_candidate

BREADTH_END_DATE="$("$PYTHON_BIN" -c 'from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; now=datetime.now(ZoneInfo("Asia/Seoul")); target=now if now.strftime("%H:%M") >= "15:35" else now-timedelta(days=1); print(target.date().isoformat())')"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] KOSPI Market Breadth를 갱신합니다: EOD ${BREADTH_END_DATE}까지"
"$KOSPI_BREADTH_PYTHON" -m kospi_risk.cli update-kospi-breadth \
  --start 2024-01-01 \
  --end "$BREADTH_END_DATE" \
  --output data/processed/kospi_breadth.parquet \
  --metadata data/quality/kospi_breadth_update.json \
  --raw-dir "$ROOT/data/raw/kospi_breadth" \
  --skip-plots
"$KOSPI_BREADTH_PYTHON" scripts/export_kospi_breadth.py \
  --input data/processed/kospi_breadth.parquet \
  --metadata data/quality/kospi_breadth_update.json \
  --output data/kospi-breadth.json
persist_local_data_cache

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 갱신 모드: $UPDATE_MODE"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] M7 공개시장 신용스트레스 프록시를 갱신합니다."
"$PYTHON_BIN" -m m7_credit_proxy.pipeline --update-latest
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
CURRENT_MARKET_DATA_SHA="$("$PYTHON_BIN" -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("data/raw/market_data.csv").read_bytes()).hexdigest())')"
if [[ -n "$OVERNIGHT_MARKET_DATA_SHA" && "$CURRENT_MARKET_DATA_SHA" == "$OVERNIGHT_MARKET_DATA_SHA" ]]; then
  cp -p "$OVERNIGHT_CANDIDATE_DIR/processed/features.parquet" data/processed/features.parquet
  cp -p "$OVERNIGHT_CANDIDATE_DIR/models/model_bundle.joblib" models/model_bundle.joblib
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 미국장 원자료가 야간 후보와 같아 피처·운영 모델을 재사용합니다."
else
  if [[ -n "$OVERNIGHT_MARKET_DATA_SHA" ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 07:30 재조회에서 원자료 변경을 감지해 피처·모델을 다시 계산합니다."
  fi
  "$PYTHON_BIN" -m kospi_risk.cli build-features --input data/raw/market_data.csv --output data/processed/features.parquet --config configs/base.yaml
  "$PYTHON_BIN" -m kospi_risk.cli train --features data/processed/features.parquet --config configs/base.yaml
fi
if [[ "$UPDATE_MODE" == "full" ]]; then
  BACKTEST_CACHE_ARGS=()
  if [[ "$SCHEDULED_DAY_TYPE" == "saturday" ]]; then
    BACKTEST_CACHE_ARGS+=(--refresh-cache)
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 토요일 정기 전체 ML 검증: fold 캐시를 새로 구축합니다."
  fi
  "$PYTHON_BIN" -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md "${BACKTEST_CACHE_ARGS[@]}"
  persist_local_data_cache
else
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] fast 모드: ML walk-forward 백테스트를 생략하고 직전 OOS 메트릭을 재사용합니다."
fi
"$PYTHON_BIN" -m kospi_risk.cli predict-latest --features data/processed/features.parquet --config configs/base.yaml --output reports/latest_signal.csv
"$PYTHON_BIN" scripts/export_ml_risk_signal.py
ML_STAGE_COMPLETED_EPOCH="$(date +%s)"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 게시 직전 시장지표 최신값을 갱신합니다."
"$PYTHON_BIN" scripts/update_market_risk.py --refresh-live-only

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
  --monday-times "$MONDAY_TIMES" \
  --saturday-times "$SATURDAY_TIMES" \
  --full-times "$FULL_TIMES" \
  --schedule-grace-minutes "$SCHEDULE_GRACE_MINUTES" \
  --scheduled-time "$SCHEDULED_TIME" \
  --run-id "$RUN_ID" \
  --started-at "$RUN_STARTED_AT" \
  --completed-at "$RUN_COMPLETED_AT" \
  --total-duration "$((RUN_COMPLETED_EPOCH - RUN_STARTED_EPOCH))" \
  --market-duration "$((MARKET_STAGE_COMPLETED_EPOCH - MARKET_STAGE_STARTED_EPOCH))" \
  --ml-duration "$((ML_STAGE_COMPLETED_EPOCH - ML_STAGE_STARTED_EPOCH))" \
  --validation-duration "$((VALIDATION_STAGE_COMPLETED_EPOCH - VALIDATION_STAGE_STARTED_EPOCH))"

ATOMIC_REUSE_ARGS=()
if [[ "$UPDATE_MODE" == "fast" ]]; then
  ATOMIC_REUSE_ARGS+=(
    --reused-file data/market-stress-episodes.json
    --reused-file data/market-history-cache.json
  )
fi

prepare_atomic_publication() {
  "$PYTHON_BIN" scripts/prepare_atomic_publication.py \
    --run-id "$RUN_ID" \
    --mode "$UPDATE_MODE" \
    --started-at "$RUN_STARTED_AT" \
    "${ATOMIC_REUSE_ARGS[@]}"
}

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 오프라인 HTML 스냅샷을 생성합니다."
"$PYTHON_BIN" scripts/export_offline_dashboard.py --stable-only
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 게시 파일을 동일 runId로 검증하고 봉인합니다: $RUN_ID"
prepare_atomic_publication
python3 tests/smoke_test.py

git config user.name "${LOCAL_MARKET_UPDATE_GIT_NAME:-local-market-risk-bot}"
git config user.email "${LOCAL_MARKET_UPDATE_GIT_EMAIL:-local-market-risk-bot@users.noreply.github.com}"

push_update_commit() {
  if git push "$REMOTE" "HEAD:$BRANCH"; then
    return 0
  fi

  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 원격 변경을 감지했습니다. 최신 $REMOTE/$BRANCH 위로 데이터 커밋을 재배치합니다."
  git fetch "$REMOTE" "$BRANCH"
  git rebase -X theirs "$REMOTE/$BRANCH"

  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 재배치된 코드 기준으로 오프라인 HTML과 스모크 테스트를 다시 검증합니다."
  "$PYTHON_BIN" scripts/export_offline_dashboard.py --stable-only
  prepare_atomic_publication
  python3 tests/smoke_test.py
  git add -- "${PUBLISH_FILES[@]}"
  if ! git diff --cached --quiet; then
    git commit --amend --no-edit
  fi
  git push "$REMOTE" "HEAD:$BRANCH"
}

PUBLISH_FILES=(
  data/risk-dashboard.json
  data/market-risk-snapshot.json
  data/market-risk-timeseries.json
  data/naver-marketindex-history.json
  data/market-risk-backtest.json
  data/market-stress-episodes.json
  data/market-history-cache.json
  data/els-index-risk.json
  data/hmm-regime.json
  data/ml-risk-signal.json
  data/data-quality.json
  data/pipeline-status.json
  data/publication-manifest.json
  data/m7-credit-proxy.json
  data/kospi-breadth.json
  reports/market-risk-dashboard-offline.html
)
git add -- "${PUBLISH_FILES[@]}"

if git diff --cached --quiet; then
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 변경된 데이터가 없어 커밋하지 않습니다."
else
  git commit -m "Update market risk data"
  push_update_commit
fi

for ((retry = 0; retry <= PAGES_DEPLOY_RETRIES; retry++)); do
  if wait_for_pages_deployment "$RUN_ID"; then
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
