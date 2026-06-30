#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE_ENV="$ROOT/.env.example"
LOG_DIR="$ROOT/logs"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE_ENV" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "$ENV_FILE 파일을 만들었습니다. LOCAL_MARKET_UPDATE_TIMES를 확인한 뒤 다시 실행하세요."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

LABEL="${LOCAL_MARKET_UPDATE_LABEL:-com.marketlab.market-risk-update}"
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-08:30,16:10}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CALENDAR_INTERVALS=""
UNIQUE_MINUTES=""

IFS=',' read -ra schedule_times <<< "$TIMES"
for schedule_time in "${schedule_times[@]}"; do
  schedule_time="${schedule_time//[[:space:]]/}"
  [[ -z "$schedule_time" ]] && continue
  schedule_minute="${schedule_time#*:}"
  if [[ "$schedule_minute" == "$schedule_time" || ! "$schedule_minute" =~ ^[0-9]{1,2}$ ]]; then
    echo "LOCAL_MARKET_UPDATE_TIMES 값이 올바르지 않습니다: $schedule_time" >&2
    exit 1
  fi
  if (( schedule_minute < 0 || schedule_minute > 59 )); then
    echo "LOCAL_MARKET_UPDATE_TIMES 분 값이 올바르지 않습니다: $schedule_time" >&2
    exit 1
  fi
  if [[ " $UNIQUE_MINUTES " != *" $schedule_minute "* ]]; then
    UNIQUE_MINUTES="$UNIQUE_MINUTES $schedule_minute"
  fi
done

for calendar_minute in $UNIQUE_MINUTES; do
  for calendar_hour in $(seq 0 23); do
    CALENDAR_INTERVALS="$CALENDAR_INTERVALS
    <dict>
      <key>Hour</key>
      <integer>$calendar_hour</integer>
      <key>Minute</key>
      <integer>$calendar_minute</integer>
    </dict>"
  done
done

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$ROOT/scripts/run_local_market_update.sh</string>
    <string>--only-at-scheduled-kst</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <array>$CALENDAR_INTERVALS
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/local-market-update.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/local-market-update.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "$LABEL LaunchAgent를 설치했습니다."
echo "목표 시각: $TIMES KST, 평일만 실행"
echo "plist: $PLIST"
echo "log: $LOG_DIR/local-market-update.log"
