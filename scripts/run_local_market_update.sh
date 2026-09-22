#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/logs"
STATE_DIR="$ROOT/logs/local-market-update-state"

BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-main}"
REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-origin}"
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-07:30,09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30}"
MONDAY_TIMES="${LOCAL_MARKET_UPDATE_MONDAY_TIMES:-09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30}"
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
LIVE_TIMES="${LOCAL_MARKET_UPDATE_LIVE_TIMES:-09:00,10:00,11:00,12:00,13:00,14:00,15:00}"
KRX_TIMES="${LOCAL_MARKET_UPDATE_KRX_TIMES:-18:30}"
SCHEDULE_GRACE_MINUTES="${LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES:-10}"
RUNTIME_SELF_UPDATE="${LOCAL_MARKET_UPDATE_RUNTIME_SELF_UPDATE:-0}"
ONLY_AT_SCHEDULED_KST=0
SCHEDULE_STATE_FILE=""
SCHEDULED_TIME=""
SCHEDULED_DAY_TYPE=""
SCHEDULE_RUNNING_FILE=""
SCHEDULE_FAILED_FILE=""
CURRENT_STAGE="예약 확인"

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
  LIVE_TIMES="${LOCAL_MARKET_UPDATE_LIVE_TIMES:-$LIVE_TIMES}"
  KRX_TIMES="${LOCAL_MARKET_UPDATE_KRX_TIMES:-$KRX_TIMES}"
  SCHEDULE_GRACE_MINUTES="${LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES:-$SCHEDULE_GRACE_MINUTES}"
  RUNTIME_SELF_UPDATE="${LOCAL_MARKET_UPDATE_RUNTIME_SELF_UPDATE:-$RUNTIME_SELF_UPDATE}"
fi

refresh_runtime_entrypoint() {
  local self_path candidate_path remote_path runtime_commit support_dir support runtime_python
  if [[ "$RUNTIME_SELF_UPDATE" != "1" ]]; then
    return 0
  fi

  self_path="$ROOT/scripts/run_local_market_update.sh"
  candidate_path="$(mktemp "$ROOT/scripts/.run_local_market_update.XXXXXX")"

  if ! git -C "$ROOT" fetch "$REMOTE" "$BRANCH"; then
    rm -f "$candidate_path"
    echo "[$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')] 런타임 스크립트 최신본 조회에 실패해 현재 버전으로 계속합니다." >&2
    return 0
  fi
  runtime_commit="$(git -C "$ROOT" rev-parse "$REMOTE/$BRANCH")"
  remote_path="$runtime_commit:scripts/run_local_market_update.sh"
  if ! git -C "$ROOT" show "$remote_path" > "$candidate_path"; then
    rm -f "$candidate_path"
    echo "[$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')] 원격 런타임 스크립트를 읽지 못해 현재 버전으로 계속합니다." >&2
    return 0
  fi
  if ! /bin/bash -n "$candidate_path"; then
    rm -f "$candidate_path"
    echo "[$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')] 원격 런타임 스크립트 구문검사에 실패해 교체하지 않습니다." >&2
    return 1
  fi
  # 감시기도 같은 커밋으로 맞춰, 실행기만 최신이고 실패 판독은 구버전인 상태를 방지합니다.
  runtime_python="${PYTHON_BIN:-python3}"
  support_dir="$(mktemp -d "$ROOT/scripts/.runtime-support.XXXXXX")"
  for support in monitor_local_market_update.py send_operations_alert.py; do
    if ! git -C "$ROOT" show "$runtime_commit:scripts/$support" > "$support_dir/$support" || \
      ! "$runtime_python" -c 'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' "$support_dir/$support"; then
      rm -rf "$support_dir"
      rm -f "$candidate_path"
      echo "운영 감시기 동기화 검증에 실패해 실행기·감시기를 교체하지 않습니다." >&2
      return 1
    fi
  done
  for support in monitor_local_market_update.py send_operations_alert.py; do
    mv -f "$support_dir/$support" "$ROOT/scripts/$support"
  done
  rmdir "$support_dir"
  if cmp -s "$candidate_path" "$self_path"; then
    rm -f "$candidate_path"
    return 0
  fi

  chmod +x "$candidate_path"
  mv -f "$candidate_path" "$self_path"
  echo "[$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')] 예약 실행기를 원격 $BRANCH 최신본으로 동기화하고 다시 시작합니다."
  exec env LOCAL_MARKET_UPDATE_RUNTIME_SELF_UPDATE=0 /bin/bash "$self_path" "$@"
}

refresh_runtime_entrypoint "$@"

for arg in "$@"; do
  case "$arg" in
    --only-at-scheduled-kst)
      ONLY_AT_SCHEDULED_KST=1
      ;;
    --fast|--mode=fast)
      MODE="fast"
      ;;
    --live|--mode=live)
      MODE="live"
      ;;
    --krx|--mode=krx)
      MODE="krx"
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
      SCHEDULE_RUNNING_FILE="${state_file%.done}.running"
      SCHEDULE_FAILED_FILE="${state_file%.done}.failed"
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
    rm -f "$SCHEDULE_RUNNING_FILE" "$SCHEDULE_FAILED_FILE"
  fi
}

wait_for_pages_deployment() {
  local expected_run_id="$1"
  local attempt

  for ((attempt = 1; attempt <= PAGES_VERIFY_ATTEMPTS; attempt++)); do
    if "$PYTHON_BIN" scripts/publication_delivery.py verify-remote --url "$PAGES_URL"; then
      echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] GitHub Pages 전체 게시 파일 반영을 확인했습니다: $expected_run_id"
      return 0
    fi
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] Pages 파일 검증 대기 중 ($attempt/$PAGES_VERIFY_ATTEMPTS)"
    sleep "$PAGES_VERIFY_INTERVAL_SECONDS"
  done
  return 1
}

resolve_update_mode() {
  local full_time live_time krx_time
  case "$MODE" in
    full|fast|live|krx)
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
      IFS=',' read -ra krx_times <<< "$KRX_TIMES"
      for krx_time in "${krx_times[@]}"; do
        krx_time="${krx_time//[[:space:]]/}"
        if [[ "$SCHEDULED_TIME" == "$krx_time" ]]; then
          echo "krx"
          return 0
        fi
      done
      IFS=',' read -ra live_times <<< "$LIVE_TIMES"
      for live_time in "${live_times[@]}"; do
        live_time="${live_time//[[:space:]]/}"
        if [[ "$SCHEDULED_TIME" == "$live_time" ]]; then
          echo "live"
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
if [[ "$UPDATE_MODE" != "live" ]]; then
  if [[ -z "$KOSPI_BREADTH_PYTHON" ]]; then
    KOSPI_BREADTH_PYTHON="$ROOT/.venv-breadth/bin/python"
  fi
  if [[ ! -x "$KOSPI_BREADTH_PYTHON" ]]; then
    echo "KOSPI Market Breadth용 Python을 찾지 못했습니다: $KOSPI_BREADTH_PYTHON" >&2
    echo ".venv-breadth에 pykrx를 설치하거나 KOSPI_BREADTH_PYTHON을 지정하세요." >&2
    exit 1
  fi
fi
RUN_STARTED_EPOCH="$(date +%s)"
RUN_STARTED_AT="$(kst_now '+%Y-%m-%d %H:%M:%S KST')"
RUN_TRIGGER_ID="${SCHEDULED_TIME:-manual}"
RUN_TRIGGER_ID="${RUN_TRIGGER_ID/:/}"
RUN_ID="$(kst_now '+%Y%m%dT%H%M%S')-${RUN_TRIGGER_ID}-${UPDATE_MODE}"
export MARKET_UPDATE_RUN_ID="$RUN_ID"

mkdir -p "$LOG_DIR"
LOCK_DIR="$LOG_DIR/.local-market-update.lock"
LOCK_ACQUIRED=0
WORKTREE=""
SOURCE_COMMIT=""
KEEP_FAILED_CANDIDATE=0
ELS_REUSED=0

record_schedule_running() {
  if [[ -n "$SCHEDULE_RUNNING_FILE" ]]; then
    printf "status=running\nrunId=%s\nmode=%s\nstartedAt=%s\n" \
      "$RUN_ID" "$UPDATE_MODE" "$RUN_STARTED_AT" > "$SCHEDULE_RUNNING_FILE"
    rm -f "$SCHEDULE_FAILED_FILE"
  fi
}

record_schedule_failure() {
  local exit_code="$1"
  if [[ -n "$SCHEDULE_FAILED_FILE" && ! -f "$SCHEDULE_STATE_FILE" ]]; then
    printf "status=failed\nexitCode=%s\nstage=%s\nfailedAt=%s\nrunId=%s\n" \
      "$exit_code" "$CURRENT_STAGE" "$(kst_now '+%Y-%m-%d %H:%M:%S KST')" "$RUN_ID" > "$SCHEDULE_FAILED_FILE"
    rm -f "$SCHEDULE_RUNNING_FILE"
  fi
}

cleanup() {
  local exit_code="$?"
  if (( exit_code != 0 )); then
    record_schedule_failure "$exit_code" || true
  fi
  if (( LOCK_ACQUIRED )); then
    rm -rf "$LOCK_DIR"
  fi
  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    if (( exit_code != 0 && KEEP_FAILED_CANDIDATE )); then
      echo "게시 복구용 작업폴더를 보관합니다: $WORKTREE" >&2
      "$PYTHON_BIN" "$WORKTREE/scripts/retry_market_publication.py" save \
        --root "$WORKTREE" --runtime-root "$ROOT" --run-id "$RUN_ID" \
        --source-commit "$SOURCE_COMMIT" --stage "$CURRENT_STAGE" \
        --remote "$REMOTE" --branch "$BRANCH" --scheduled-time "$SCHEDULED_TIME" \
        --started-at "$RUN_STARTED_AT" || true
    else
      git -C "$ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"
    fi
  fi
  return "$exit_code"
}
trap cleanup EXIT

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  CURRENT_STAGE="중복 실행 잠금"
  echo "다른 로컬 시장리스크 갱신 작업이 실행 중입니다." >&2
  exit 1
fi
LOCK_ACQUIRED=1
record_schedule_running

WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/market-risk-update.XXXXXX")"
rm -rf "$WORKTREE"

CURRENT_STAGE="임시 워크트리 준비"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] $REMOTE/$BRANCH 기준 임시 worktree를 준비합니다."
git -C "$ROOT" fetch "$REMOTE" "$BRANCH"
git -C "$ROOT" worktree add --detach "$WORKTREE" "$REMOTE/$BRANCH"

cd "$WORKTREE"
SOURCE_COMMIT="$(git rev-parse HEAD)"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$WORKTREE/src"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Skipping features without any observed values:UserWarning}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$WORKTREE/.cache/matplotlib}"
export M7_CREDIT_RAW_DIR="${M7_CREDIT_RAW_DIR:-$ROOT/data/raw/m7_credit_proxy}"
export INDEX_HISTORY_CACHE_DIR="${INDEX_HISTORY_CACHE_DIR:-$ROOT/data/raw/index_history}"
VKOSPI_CACHE_DIR="${VKOSPI_CACHE_DIR:-$ROOT/data/cache/vkospi}"
mkdir -p "$MPLCONFIGDIR" "$INDEX_HISTORY_CACHE_DIR" "$VKOSPI_CACHE_DIR"

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
  mkdir -p "$WORKTREE/data/raw/vkospi"
  for filename in stockplus_vkospi.csv stockplus_vkospi.metadata.json; do
    source_file="$VKOSPI_CACHE_DIR/$filename"
    if [[ -f "$source_file" ]]; then
      cp -p "$source_file" "$WORKTREE/data/raw/vkospi/$filename"
    fi
  done
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
  mkdir -p "$VKOSPI_CACHE_DIR"
  for filename in stockplus_vkospi.csv stockplus_vkospi.metadata.json; do
    source_file="$WORKTREE/data/raw/vkospi/$filename"
    if [[ -f "$source_file" ]]; then
      cp -p "$source_file" "$VKOSPI_CACHE_DIR/$filename"
    fi
  done
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

update_kospi_breadth_data() {
  local end_date="$1"
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 증권플러스 VKOSPI 일봉을 증분 갱신합니다: ${end_date}까지"
  "$PYTHON_BIN" scripts/update_vkospi.py \
    --end "$end_date" \
    --output data/raw/vkospi/stockplus_vkospi.csv \
    --metadata data/raw/vkospi/stockplus_vkospi.metadata.json
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] KOSPI Market Breadth를 갱신합니다: EOD ${end_date}까지"
  "$KOSPI_BREADTH_PYTHON" -m kospi_risk.cli update-kospi-breadth \
    --start 2024-01-01 \
    --end "$end_date" \
    --output data/processed/kospi_breadth.parquet \
    --metadata data/quality/kospi_breadth_update.json \
    --vkospi data/raw/vkospi/stockplus_vkospi.csv \
    --vkospi-metadata data/raw/vkospi/stockplus_vkospi.metadata.json \
    --raw-dir "$ROOT/data/raw/kospi_breadth" \
    --skip-plots
  "$KOSPI_BREADTH_PYTHON" scripts/export_kospi_breadth.py \
    --input data/processed/kospi_breadth.parquet \
    --metadata data/quality/kospi_breadth_update.json \
    --output data/kospi-breadth.json
  persist_local_data_cache
}

refresh_els_index_risk() {
  if "$PYTHON_BIN" scripts/export_els_index_risk.py; then
    return 0
  fi
  if [[ -s data/els-index-risk.json ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] ELS 원천 조회 실패로 직전 검증본을 유지합니다." >&2
    ELS_REUSED=1
    return 0
  fi
  echo "ELS 지수 리스크 신규 산출과 직전 검증본 사용이 모두 불가능합니다." >&2
  return 1
}

CURRENT_STAGE="시장데이터·지표"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 갱신 모드: $UPDATE_MODE"
if [[ "$UPDATE_MODE" == "live" ]]; then
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 장중 경량 갱신: 현재 시장값과 위험점수만 갱신합니다."
  MARKET_STAGE_STARTED_EPOCH="$(date +%s)"
  make update-market-risk
  MARKET_STAGE_COMPLETED_EPOCH="$(date +%s)"
  ML_STAGE_STARTED_EPOCH="$MARKET_STAGE_COMPLETED_EPOCH"
  ML_STAGE_COMPLETED_EPOCH="$MARKET_STAGE_COMPLETED_EPOCH"
elif [[ "$UPDATE_MODE" == "krx" ]]; then
  if [[ -n "$SCHEDULED_TIME" ]]; then
    BREADTH_END_DATE="$(kst_now '+%Y-%m-%d')"
  else
    BREADTH_END_DATE="$("$KOSPI_BREADTH_PYTHON" -c 'import pandas as pd; dates=pd.to_datetime(pd.read_parquet("data/processed/kospi_breadth.parquet")["date"], errors="coerce").dropna(); print(dates.max().date().isoformat() if len(dates) else "")')"
    if [[ -z "$BREADTH_END_DATE" ]]; then
      echo "수동 KRX 보강 기준일을 확인할 수 없습니다." >&2
      exit 1
    fi
  fi
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] KRX 확정치 갱신: 외국인·기관·프로그램 순매매만 보강합니다."
  MARKET_STAGE_STARTED_EPOCH="$(date +%s)"
  update_kospi_breadth_data "$BREADTH_END_DATE"
  "$KOSPI_BREADTH_PYTHON" scripts/verify_kospi_flow_final.py \
    --input data/processed/kospi_breadth.parquet \
    --date "$BREADTH_END_DATE"
  MARKET_STAGE_COMPLETED_EPOCH="$(date +%s)"
  ML_STAGE_STARTED_EPOCH="$MARKET_STAGE_COMPLETED_EPOCH"
  ML_STAGE_COMPLETED_EPOCH="$MARKET_STAGE_COMPLETED_EPOCH"
else
BREADTH_END_DATE="$("$PYTHON_BIN" -c 'from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; now=datetime.now(ZoneInfo("Asia/Seoul")); target=now if now.strftime("%H:%M") >= "15:35" else now-timedelta(days=1); print(target.date().isoformat())')"
update_kospi_breadth_data "$BREADTH_END_DATE"

echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] M7 공개시장 신용스트레스 프록시를 갱신합니다."
"$PYTHON_BIN" -m m7_credit_proxy.pipeline --update-latest
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] FreeSIS 과거 원장과 KB 증시주변자금 최종일을 갱신합니다."
"$PYTHON_BIN" scripts/update_kb_market_funds.py
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] DRAM 현물가격 관찰지표를 갱신합니다."
"$PYTHON_BIN" scripts/update_dram_spot_prices.py
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 시장리스크 데이터를 갱신합니다."
MARKET_STAGE_STARTED_EPOCH="$(date +%s)"
make update-market-risk
make backtest-market-risk
if [[ "$UPDATE_MODE" == "full" ]]; then
  REFRESH_STRESS_CACHE=1 make analyze-stress-episodes
else
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] fast 모드: 스트레스 에피소드 히스토리 재계산을 생략합니다."
fi
refresh_els_index_risk
python3 scripts/export_hmm_regime.py
MARKET_STAGE_COMPLETED_EPOCH="$(date +%s)"

CURRENT_STAGE="ML 신호"
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
  if [[ "$SCHEDULED_DAY_TYPE" == "saturday" ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 토요일 정기 전체 ML 검증: fold 캐시를 새로 구축합니다."
    "$PYTHON_BIN" -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md --refresh-cache
  else
    "$PYTHON_BIN" -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md
  fi
  persist_local_data_cache
else
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] fast 모드: ML walk-forward 백테스트를 생략하고 직전 OOS 메트릭을 재사용합니다."
fi
"$PYTHON_BIN" -m kospi_risk.cli predict-latest --features data/processed/features.parquet --config configs/base.yaml --output reports/latest_signal.csv
"$PYTHON_BIN" scripts/export_ml_risk_signal.py
ML_STAGE_COMPLETED_EPOCH="$(date +%s)"
fi

if [[ "$UPDATE_MODE" != "krx" ]]; then
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 게시 직전 시장지표 최신값을 갱신합니다."
  "$PYTHON_BIN" scripts/update_market_risk.py --refresh-live-only
fi

CURRENT_STAGE="품질 검증"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 대시보드 데이터를 검증합니다."
VALIDATION_STAGE_STARTED_EPOCH="$(date +%s)"
"$PYTHON_BIN" scripts/audit_data_completeness.py --strict
make test
VALIDATION_STAGE_COMPLETED_EPOCH="$(date +%s)"
RUN_COMPLETED_AT="$(kst_now '+%Y-%m-%d %H:%M:%S KST')"
RUN_COMPLETED_EPOCH="$(date +%s)"
KEEP_FAILED_CANDIDATE=1

"$PYTHON_BIN" scripts/write_pipeline_status.py \
  --mode "$UPDATE_MODE" \
  --times "$TIMES" \
  --monday-times "$MONDAY_TIMES" \
  --saturday-times "$SATURDAY_TIMES" \
  --full-times "$FULL_TIMES" \
  --live-times "$LIVE_TIMES" \
  --krx-times "$KRX_TIMES" \
  --schedule-grace-minutes "$SCHEDULE_GRACE_MINUTES" \
  --scheduled-time "$SCHEDULED_TIME" \
  --run-id "$RUN_ID" \
  --started-at "$RUN_STARTED_AT" \
  --completed-at "$RUN_COMPLETED_AT" \
  --total-duration "$((RUN_COMPLETED_EPOCH - RUN_STARTED_EPOCH))" \
  --market-duration "$((MARKET_STAGE_COMPLETED_EPOCH - MARKET_STAGE_STARTED_EPOCH))" \
  --ml-duration "$((ML_STAGE_COMPLETED_EPOCH - ML_STAGE_STARTED_EPOCH))" \
  --validation-duration "$((VALIDATION_STAGE_COMPLETED_EPOCH - VALIDATION_STAGE_STARTED_EPOCH))"

ATOMIC_PUBLICATION_ARGS=(
  --run-id "$RUN_ID"
  --mode "$UPDATE_MODE"
  --started-at "$RUN_STARTED_AT"
)
if (( ELS_REUSED )); then
  ATOMIC_PUBLICATION_ARGS+=(--reused-file data/els-index-risk.json)
fi
if [[ "$UPDATE_MODE" == "fast" ]]; then
  ATOMIC_PUBLICATION_ARGS+=(
    --reused-file data/market-stress-episodes.json
  )
elif [[ "$UPDATE_MODE" == "live" ]]; then
  ATOMIC_PUBLICATION_ARGS+=(
    --reused-file data/market-risk-backtest.json
    --reused-file data/market-stress-episodes.json
    --reused-file data/els-index-risk.json
    --reused-file data/hmm-regime.json
    --reused-file data/ml-risk-signal.json
    --reused-file data/m7-credit-proxy.json
    --reused-file data/kb-market-funds.json
    --reused-file data/kospi-breadth.json
  )
elif [[ "$UPDATE_MODE" == "krx" ]]; then
  ATOMIC_PUBLICATION_ARGS+=(
    --reused-file data/risk-dashboard.json
    --reused-file data/market-risk-snapshot.json
    --reused-file data/market-risk-timeseries.json
    --reused-file data/naver-marketindex-history.json
    --reused-file data/market-risk-backtest.json
    --reused-file data/market-stress-episodes.json
    --reused-file data/market-history-cache.json
    --reused-file data/els-index-risk.json
    --reused-file data/hmm-regime.json
    --reused-file data/ml-risk-signal.json
    --reused-file data/m7-credit-proxy.json
    --reused-file data/kb-market-funds.json
  )
fi

prepare_atomic_publication() {
  "$PYTHON_BIN" scripts/prepare_atomic_publication.py "${ATOMIC_PUBLICATION_ARGS[@]}"
}

CURRENT_STAGE="게시 파일 준비"
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
  "$PYTHON_BIN" scripts/publication_delivery.py check-rebase --base "$SOURCE_COMMIT" --target "$REMOTE/$BRANCH"
  git rebase "$REMOTE/$BRANCH"
  SOURCE_COMMIT="$(git rev-parse "$REMOTE/$BRANCH")"

  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 재배치된 코드 기준으로 오프라인 HTML과 스모크 테스트를 다시 검증합니다."
  "$PYTHON_BIN" scripts/export_offline_dashboard.py --stable-only
  prepare_atomic_publication
  python3 tests/smoke_test.py
  load_publish_files
  git add -- "${PUBLISH_FILES[@]}"
  "$PYTHON_BIN" scripts/publication_delivery.py verify-staged
  if ! git diff --cached --quiet; then
    git commit --amend --no-edit
  fi
  git push "$REMOTE" "HEAD:$BRANCH"
}

load_publish_files() {
  local paths path
  paths="$("$PYTHON_BIN" scripts/publication_delivery.py files)"
  PUBLISH_FILES=()
  while IFS= read -r path; do
    PUBLISH_FILES+=("$path")
  done <<< "$paths"
}
load_publish_files
git add -- "${PUBLISH_FILES[@]}"
"$PYTHON_BIN" scripts/publication_delivery.py verify-staged

if git diff --cached --quiet; then
  echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 변경된 데이터가 없어 커밋하지 않습니다."
else
  git commit -m "Update market risk data"
  push_update_commit
fi

CURRENT_STAGE="GitHub Pages 확인"
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
