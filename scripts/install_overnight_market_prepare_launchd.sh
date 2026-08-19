#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE_ENV="$ROOT/.env.example"
LOG_DIR="$ROOT/logs"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE_ENV" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "$ENV_FILE 파일을 만들었습니다. 텔레그램과 야간 작업 설정을 확인한 뒤 다시 실행하세요."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "야간 실패 알림에 필요한 TELEGRAM_BOT_TOKEN이 없습니다." >&2
  exit 1
fi
if [[ -z "${MARKET_OPERATIONS_TELEGRAM_CHAT_IDS:-${MARKET_OPERATIONS_TELEGRAM_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}}" ]]; then
  echo "금융공학뉴스 텔레그램 chat id가 없습니다." >&2
  exit 1
fi

LABEL="${OVERNIGHT_PREPARE_LABEL:-com.marketlab.overnight-market-prepare}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CALENDAR_INTERVALS=""

append_interval() {
  local weekday="$1" hour="$2" minute="$3"
  CALENDAR_INTERVALS="$CALENDAR_INTERVALS
      <dict>
        <key>Weekday</key>
        <integer>$weekday</integer>
        <key>Hour</key>
        <integer>$hour</integer>
        <key>Minute</key>
        <integer>$minute</integer>
      </dict>"
}

# launchd Weekday: 2=화요일, 6=토요일. 두 슬롯 중 뉴욕 DST에 맞는 하나만 실행합니다.
for weekday in 2 3 4 5 6; do
  append_interval "$weekday" 5 40
  append_interval "$weekday" 6 40
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
    <string>$ROOT/scripts/run_overnight_market_prepare.sh</string>
    <string>--only-at-scheduled-kst</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <array>$CALENDAR_INTERVALS
  </array>
  <key>ProcessType</key>
  <string>Background</string>
  <key>LowPriorityIO</key>
  <true/>
  <key>Nice</key>
  <integer>5</integer>
  <key>ThrottleInterval</key>
  <integer>60</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/overnight-market-prepare.launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/overnight-market-prepare.launchd.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "$LABEL LaunchAgent를 설치했습니다."
echo "화~토 05:40·06:40 KST에 깨우고 뉴욕 DST에 맞는 한 슬롯만 실행합니다."
echo "서머타임: 05:40 KST · 표준시간: 06:40 KST"
echo "실패 알림: MARKET_OPERATIONS_TELEGRAM_CHAT_ID(S), 미설정 시 TELEGRAM_CHAT_ID"
echo "plist: $PLIST"
echo "log: $LOG_DIR/overnight-market-prepare.launchd.log"
