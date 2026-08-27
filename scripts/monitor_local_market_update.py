from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

try:
    from send_operations_alert import (
        format_schedule_alert,
        load_env_file,
        operations_chat_ids,
        send_message,
    )
except ModuleNotFoundError:  # pytest에서 저장소 루트 패키지로 불러오는 경우
    from scripts.send_operations_alert import (
        format_schedule_alert,
        load_env_file,
        operations_chat_ids,
        send_message,
    )


ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
EXPECTED_MINUTES = {"live": 3, "fast": 5, "full": 25, "krx": 4}


@dataclass(frozen=True)
class MonitorSettings:
    weekday_times: tuple[str, ...]
    monday_times: tuple[str, ...]
    saturday_times: tuple[str, ...]
    full_times: frozenset[str]
    live_times: frozenset[str]
    krx_times: frozenset[str]
    grace_minutes: int
    expected_minutes: dict[str, int]


@dataclass(frozen=True)
class ScheduleAlert:
    scheduled_at: datetime
    mode: str
    elapsed_minutes: int
    state: str
    marker: Path


def _times(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def settings_from_env(environ: Mapping[str, str] = os.environ) -> MonitorSettings:
    """예약 갱신 설정을 환경변수에서 읽는다."""

    weekday = _times(
        environ.get(
            "LOCAL_MARKET_UPDATE_TIMES",
            "07:30,09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30",
        )
    )
    monday = _times(
        environ.get(
            "LOCAL_MARKET_UPDATE_MONDAY_TIMES",
            "09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30",
        )
    )
    saturday = _times(environ.get("LOCAL_MARKET_UPDATE_SATURDAY_TIMES", "07:30"))
    expected = {
        mode: int(environ.get(f"LOCAL_MARKET_UPDATE_EXPECTED_{mode.upper()}_MINUTES", default))
        for mode, default in EXPECTED_MINUTES.items()
    }
    return MonitorSettings(
        weekday_times=weekday,
        monday_times=monday,
        saturday_times=saturday,
        full_times=frozenset(_times(environ.get("LOCAL_MARKET_UPDATE_FULL_TIMES", "15:35"))),
        live_times=frozenset(
            _times(
                environ.get(
                    "LOCAL_MARKET_UPDATE_LIVE_TIMES",
                    "09:00,10:00,11:00,12:00,13:00,14:00,15:00",
                )
            )
        ),
        krx_times=frozenset(_times(environ.get("LOCAL_MARKET_UPDATE_KRX_TIMES", "18:30"))),
        grace_minutes=int(environ.get("LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES", "10")),
        expected_minutes=expected,
    )


def _active_times(now: datetime, settings: MonitorSettings) -> tuple[str, ...]:
    if now.weekday() == 0:
        return settings.monday_times
    if 1 <= now.weekday() <= 4:
        return settings.weekday_times
    if now.weekday() == 5:
        return settings.saturday_times
    return ()


def _mode_for(slot_time: str, now: datetime, settings: MonitorSettings) -> str:
    if now.weekday() == 5:
        return "full"
    if slot_time in settings.full_times:
        return "full"
    if slot_time in settings.krx_times:
        return "krx"
    if slot_time in settings.live_times:
        return "live"
    return "fast"


def _read_failure_state(path: Path) -> str | None:
    if not path.exists():
        return None
    values = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    stage = values.get("stage", "확인 불가")
    exit_code = values.get("exitCode", "?")
    return f"실행 실패 · 단계 {stage} · 종료코드 {exit_code}"


def find_schedule_alerts(
    *,
    now: datetime,
    settings: MonitorSettings,
    state_dir: Path,
    alert_dir: Path,
    lock_dir: Path,
) -> list[ScheduleAlert]:
    """완료 기준시간을 넘긴 오늘 예약 중 아직 복구되지 않은 항목을 찾는다."""

    current = now.astimezone(KST)
    slots: list[tuple[str, datetime, str, Path]] = []
    for slot_time in _active_times(current, settings):
        hour, minute = (int(value) for value in slot_time.split(":"))
        scheduled_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        mode = _mode_for(slot_time, current, settings)
        done = state_dir / f"{current.date().isoformat()}-{slot_time}.done"
        slots.append((slot_time, scheduled_at, mode, done))

    latest_done_at = max((slot[1] for slot in slots if slot[3].exists()), default=None)
    alerts = []
    for slot_time, scheduled_at, mode, done in slots:
        deadline = scheduled_at + timedelta(
            minutes=settings.expected_minutes[mode] + settings.grace_minutes
        )
        if current < deadline or done.exists():
            continue
        if latest_done_at is not None and latest_done_at > scheduled_at:
            continue

        marker = alert_dir / f"{current.date().isoformat()}-{slot_time}.alerted"
        if marker.exists():
            continue
        failure = _read_failure_state(state_dir / f"{current.date().isoformat()}-{slot_time}.failed")
        if failure:
            state = failure
        elif lock_dir.exists():
            state = "실행 중 · 완료 기준시간 초과"
        else:
            state = "완료 표식 없음 · 미실행 또는 조기 종료"
        alerts.append(
            ScheduleAlert(
                scheduled_at=scheduled_at,
                mode=mode,
                elapsed_minutes=int((current - scheduled_at).total_seconds() // 60),
                state=state,
                marker=marker,
            )
        )
    return alerts


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(KST)
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="시장리스크 정기 갱신 지연을 감시합니다.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--now", help="테스트용 현재시각(ISO 8601)")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    load_env_file(root / ".env")
    state_dir = root / "logs" / "local-market-update-state"
    alert_dir = root / "logs" / "local-market-update-alert-state"
    log_file = root / "logs" / "local-market-update.err.log"
    alerts = find_schedule_alerts(
        now=_parse_now(args.now),
        settings=settings_from_env(),
        state_dir=state_dir,
        alert_dir=alert_dir,
        lock_dir=root / "logs" / ".local-market-update.lock",
    )
    if not alerts:
        print("정기 갱신 지연 없음")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or ""
    chat_ids = operations_chat_ids()
    if not args.dry_run and (not token or not chat_ids):
        raise SystemExit("TELEGRAM_BOT_TOKEN과 금융공학뉴스 chat id가 필요합니다.")

    for alert in alerts:
        message = format_schedule_alert(
            scheduled_at=alert.scheduled_at,
            mode=alert.mode,
            elapsed_minutes=alert.elapsed_minutes,
            state=alert.state,
            log_file=str(log_file),
        )
        if args.dry_run:
            print(message)
            continue
        for chat_id in chat_ids:
            send_message(message, token, chat_id)
        alert.marker.parent.mkdir(parents=True, exist_ok=True)
        alert.marker.write_text(
            json.dumps(
                {
                    "sentAt": datetime.now(KST).isoformat(timespec="seconds"),
                    "scheduledAt": alert.scheduled_at.isoformat(timespec="minutes"),
                    "mode": alert.mode,
                    "state": alert.state,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"정기 갱신 지연 알림 발송: {alert.scheduled_at:%Y-%m-%d %H:%M} {alert.mode}")


if __name__ == "__main__":
    main()
