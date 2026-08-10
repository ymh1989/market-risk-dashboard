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
TIMES="${LOCAL_MARKET_UPDATE_TIMES:-07:30,12:30,15:35}"
MONDAY_TIMES="${LOCAL_MARKET_UPDATE_MONDAY_TIMES:-12:30,15:35}"
SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CALENDAR_INTERVALS=""

append_calendar_intervals() {
  local times="$1"
  shift
  local weekdays=("$@") schedule_time calendar_hour calendar_minute weekday
  IFS=',' read -ra schedule_times <<< "$times"
  for schedule_time in "${schedule_times[@]}"; do
    schedule_time="${schedule_time//[[:space:]]/}"
    [[ -z "$schedule_time" ]] && continue
    calendar_hour="${schedule_time%:*}"
    calendar_minute="${schedule_time#*:}"
    if [[ "$calendar_hour" == "$schedule_time" || ! "$calendar_hour" =~ ^[0-9]{1,2}$ || ! "$calendar_minute" =~ ^[0-9]{1,2}$ ]]; then
      echo "시장리스크 예약 시각 값이 올바르지 않습니다: $schedule_time" >&2
      exit 1
    fi
    if (( 10#$calendar_hour > 23 || 10#$calendar_minute > 59 )); then
      echo "시장리스크 예약 시각 값이 올바르지 않습니다: $schedule_time" >&2
      exit 1
    fi
    for weekday in "${weekdays[@]}"; do
      CALENDAR_INTERVALS="$CALENDAR_INTERVALS
      <dict>
        <key>Weekday</key>
        <integer>$weekday</integer>
        <key>Hour</key>
        <integer>$((10#$calendar_hour))</integer>
        <key>Minute</key>
        <integer>$((10#$calendar_minute))</integer>
      </dict>"
    done
  done
}

# launchd Weekday: 1=월요일, 6=토요일. 일요일 일정은 만들지 않습니다.
append_calendar_intervals "$MONDAY_TIMES" 1
append_calendar_intervals "$TIMES" 2 3 4 5
append_calendar_intervals "$SATURDAY_TIMES" 6

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
echo "월요일 목표 시각: $MONDAY_TIMES KST"
echo "화~금 목표 시각: $TIMES KST"
echo "토요일 목표 시각: $SATURDAY_TIMES KST"
echo "일요일은 실행하지 않습니다."
echo "plist: $PLIST"
echo "log: $LOG_DIR/local-market-update.log"
