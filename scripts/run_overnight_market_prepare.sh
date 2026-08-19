#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
LOG_DIR="$ROOT/logs"
STATE_DIR="$LOG_DIR/overnight-market-prepare-state"
CANDIDATE_BASE="$ROOT/data/cache/overnight-market-prepare"
CANDIDATE_CURRENT="$CANDIDATE_BASE/current"

BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-main}"
REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-origin}"
PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-}"
SCHEDULE_GRACE_MINUTES="${OVERNIGHT_PREPARE_GRACE_MINUTES:-12}"
ONLY_AT_SCHEDULED_KST=0
CURRENT_STAGE="초기화"
SCHEDULE_STATE_FILE=""
WORKTREE=""
CANDIDATE_STAGING=""

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
  BRANCH="${LOCAL_MARKET_UPDATE_BRANCH:-$BRANCH}"
  REMOTE="${LOCAL_MARKET_UPDATE_REMOTE:-$REMOTE}"
  PYTHON_BIN="${LOCAL_MARKET_UPDATE_PYTHON:-$PYTHON_BIN}"
  SCHEDULE_GRACE_MINUTES="${OVERNIGHT_PREPARE_GRACE_MINUTES:-$SCHEDULE_GRACE_MINUTES}"
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

mkdir -p "$LOG_DIR" "$STATE_DIR" "$CANDIDATE_BASE"
RUN_LOG="$LOG_DIR/overnight-market-prepare-$(TZ=Asia/Seoul date '+%Y%m%d').log"
echo "야간 준비 로그: $RUN_LOG"
exec >> "$RUN_LOG" 2>&1

kst_now() {
  TZ=Asia/Seoul date "$1"
}

notify_failure() {
  local exit_code="$1" line_number="$2" failed_command="$3"
  trap - ERR
  "$PYTHON_BIN" "$ROOT/scripts/send_operations_alert.py" \
    --job "미국장 EOD 사전준비" \
    --stage "$CURRENT_STAGE" \
    --exit-code "$exit_code" \
    --command "line $line_number: $failed_command" \
    --log-file "$RUN_LOG" || echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 텔레그램 실패 알림도 전송하지 못했습니다." >&2
  exit "$exit_code"
}
trap 'notify_failure "$?" "$LINENO" "$BASH_COMMAND"' ERR

IFS='|' read -r SCHEDULE_ELIGIBLE EXPECTED_SLOT US_MARKET_DATE US_SEASON <<< "$("$PYTHON_BIN" "$ROOT/scripts/overnight_market_prepare.py" schedule --format shell)"

is_scheduled_now() {
  local now_time now_minutes slot_minutes elapsed_minutes state_key
  if [[ "$SCHEDULE_ELIGIBLE" != "1" ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 미국장 마감 사전준비 대상 요일이 아닙니다."
    return 1
  fi
  now_time="$(kst_now +%H:%M)"
  now_minutes="$((10#${now_time%:*} * 60 + 10#${now_time#*:}))"
  slot_minutes="$((10#${EXPECTED_SLOT%:*} * 60 + 10#${EXPECTED_SLOT#*:}))"
  elapsed_minutes="$((now_minutes - slot_minutes))"
  if ((elapsed_minutes < 0 || elapsed_minutes > SCHEDULE_GRACE_MINUTES)); then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 현재는 $US_SEASON 슬롯($EXPECTED_SLOT KST)이 아니어서 건너뜁니다."
    return 1
  fi
  state_key="$(kst_now +%Y-%m-%d)-$EXPECTED_SLOT"
  SCHEDULE_STATE_FILE="$STATE_DIR/$state_key.done"
  if [[ -f "$SCHEDULE_STATE_FILE" ]]; then
    echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 이미 완료한 야간 슬롯입니다: $EXPECTED_SLOT"
    return 1
  fi
  return 0
}

if ((ONLY_AT_SCHEDULED_KST)); then
  is_scheduled_now || exit 0
fi

RUN_STARTED_AT="$(kst_now '+%Y-%m-%d %H:%M:%S KST')"
echo "[$RUN_STARTED_AT] 미국장 $US_MARKET_DATE EOD 사전준비를 시작합니다: $US_SEASON · $EXPECTED_SLOT KST"

LOCK_DIR="$LOG_DIR/.local-market-update.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "시장리스크 갱신 또는 다른 야간 준비 작업이 실행 중입니다." >&2
  false
fi

cleanup() {
  rm -rf "$LOCK_DIR"
  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    git -C "$ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"
  fi
  if [[ -n "$CANDIDATE_STAGING" && -d "$CANDIDATE_STAGING" ]]; then
    rm -rf "$CANDIDATE_STAGING"
  fi
}
trap cleanup EXIT

CURRENT_STAGE="최신 코드 준비"
WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/market-risk-overnight.XXXXXX")"
rm -rf "$WORKTREE"
git -C "$ROOT" fetch "$REMOTE" "$BRANCH"
git -C "$ROOT" worktree add --detach "$WORKTREE" "$REMOTE/$BRANCH"

cd "$WORKTREE"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$WORKTREE/src"
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:Skipping features without any observed values:UserWarning}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-$WORKTREE/.cache/matplotlib}"
mkdir -p "$MPLCONFIGDIR" data/raw data/processed models

PREVIOUS_MARKET_DATA="$ROOT/data/raw/market_data.csv"
if [[ -f "$PREVIOUS_MARKET_DATA" ]]; then
  cp -p "$PREVIOUS_MARKET_DATA" data/raw/market_data.csv
fi
if [[ -f "$ROOT/data/raw/market_data_sources.json" ]]; then
  cp -p "$ROOT/data/raw/market_data_sources.json" data/raw/market_data_sources.json
fi

CURRENT_STAGE="미국장 EOD 수집"
"$PYTHON_BIN" -m kospi_risk.cli fetch-market-data \
  --source-config configs/data_sources.yaml \
  --output data/raw/market_data.csv \
  --metadata data/raw/market_data_sources.json \
  --min-rows 1500

CURRENT_STAGE="시장리스크·HMM·ELS 사전계산"
make update-market-risk
make backtest-market-risk
"$PYTHON_BIN" scripts/export_els_index_risk.py
"$PYTHON_BIN" scripts/export_hmm_regime.py

CURRENT_STAGE="피처 사전계산"
"$PYTHON_BIN" -m kospi_risk.cli build-features \
  --input data/raw/market_data.csv \
  --output data/processed/features.parquet \
  --config configs/base.yaml

CURRENT_STAGE="운영 모델 사전학습"
"$PYTHON_BIN" -m kospi_risk.cli train \
  --features data/processed/features.parquet \
  --config configs/base.yaml \
  --model-output models/model_bundle.joblib

CURRENT_STAGE="ML 최신 신호 사전검증"
"$PYTHON_BIN" -m kospi_risk.cli predict-latest \
  --features data/processed/features.parquet \
  --config configs/base.yaml \
  --model models/model_bundle.joblib \
  --output reports/latest_signal.csv
"$PYTHON_BIN" scripts/export_ml_risk_signal.py

CURRENT_STAGE="대시보드 데이터 완비성"
"$PYTHON_BIN" scripts/audit_data_completeness.py --strict

CURRENT_STAGE="후보 파일 구성"
CANDIDATE_STAGING="$(mktemp -d "$CANDIDATE_BASE/.candidate.XXXXXX")"
mkdir -p "$CANDIDATE_STAGING/raw" "$CANDIDATE_STAGING/processed" "$CANDIDATE_STAGING/models" "$CANDIDATE_STAGING/dashboard" "$CANDIDATE_STAGING/quality"
cp -p data/raw/market_data.csv "$CANDIDATE_STAGING/raw/market_data.csv"
cp -p data/raw/market_data_sources.json "$CANDIDATE_STAGING/raw/market_data_sources.json"
cp -p data/processed/features.parquet "$CANDIDATE_STAGING/processed/features.parquet"
cp -p models/model_bundle.joblib "$CANDIDATE_STAGING/models/model_bundle.joblib"
cp -p data/market-history-cache.json "$CANDIDATE_STAGING/dashboard/market-history-cache.json"
cp -p data/naver-marketindex-history.json "$CANDIDATE_STAGING/dashboard/naver-marketindex-history.json"
cp -p data/els-index-risk.json "$CANDIDATE_STAGING/dashboard/els-index-risk.json"
cp -p data/hmm-regime.json "$CANDIDATE_STAGING/dashboard/hmm-regime.json"
cp -p data/ml-risk-signal.json "$CANDIDATE_STAGING/dashboard/ml-risk-signal.json"
cp -p data/data-quality.json "$CANDIDATE_STAGING/dashboard/data-quality.json"

CURRENT_STAGE="원천 품질·과거값 대조"
AUDIT_ARGS=(
  --current-data "$CANDIDATE_STAGING/raw/market_data.csv"
  --metadata "$CANDIDATE_STAGING/raw/market_data_sources.json"
  --expected-us-market-date "$US_MARKET_DATE"
  --output "$CANDIDATE_STAGING/quality/overnight-source-quality.json"
)
if [[ -f "$PREVIOUS_MARKET_DATA" ]]; then
  AUDIT_ARGS+=(--previous-data "$PREVIOUS_MARKET_DATA")
fi
"$PYTHON_BIN" scripts/overnight_market_prepare.py audit "${AUDIT_ARGS[@]}"

CURRENT_STAGE="후보 무결성 봉인"
SOURCE_COMMIT="$(git rev-parse HEAD)"
"$PYTHON_BIN" scripts/overnight_market_prepare.py seal \
  --root "$CANDIDATE_STAGING" \
  --source-commit "$SOURCE_COMMIT" \
  --slot "$EXPECTED_SLOT" \
  --us-market-date "$US_MARKET_DATE" \
  --started-at "$RUN_STARTED_AT"

CURRENT_STAGE="후보 원자 교체"
"$PYTHON_BIN" scripts/overnight_market_prepare.py publish \
  --staging "$CANDIDATE_STAGING" \
  --current "$CANDIDATE_CURRENT"
CANDIDATE_STAGING=""

if [[ -n "$SCHEDULE_STATE_FILE" ]]; then
  printf "%s\n" "$(kst_now '+%Y-%m-%d %H:%M:%S KST')" > "$SCHEDULE_STATE_FILE"
fi
CURRENT_STAGE="완료"
echo "[$(kst_now '+%Y-%m-%d %H:%M:%S KST')] 야간 후보 준비 완료: 미국장 $US_MARKET_DATE · $CANDIDATE_CURRENT"
