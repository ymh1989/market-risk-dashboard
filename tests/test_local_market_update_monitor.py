from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.monitor_local_market_update import find_schedule_alerts, settings_from_env
from scripts.send_operations_alert import (
    format_schedule_alert,
    operations_bot_token,
    operations_chat_ids,
)


KST = ZoneInfo("Asia/Seoul")
ENV = {
    "LOCAL_MARKET_UPDATE_TIMES": "07:30,09:00,15:00,15:35,18:30",
    "LOCAL_MARKET_UPDATE_MONDAY_TIMES": "09:00,15:00,15:35,18:30",
    "LOCAL_MARKET_UPDATE_SATURDAY_TIMES": "07:30",
    "LOCAL_MARKET_UPDATE_FULL_TIMES": "15:35",
    "LOCAL_MARKET_UPDATE_LIVE_TIMES": "09:00,15:00",
    "LOCAL_MARKET_UPDATE_KRX_TIMES": "18:30",
    "LOCAL_MARKET_UPDATE_SCHEDULE_GRACE_MINUTES": "10",
}


def _alerts(tmp_path: Path, now: datetime):
    state_dir = tmp_path / "state"
    alert_dir = tmp_path / "alerts"
    state_dir.mkdir(exist_ok=True)
    for slot in ("07:30", "09:00", "15:00"):
        (state_dir / f"2026-08-27-{slot}.done").write_text("완료", encoding="utf-8")
    return find_schedule_alerts(
        now=now,
        settings=settings_from_env(ENV),
        state_dir=state_dir,
        alert_dir=alert_dir,
        lock_dir=tmp_path / "lock",
    )


def test_full_slot_waits_for_expected_duration_and_grace(tmp_path):
    assert _alerts(tmp_path, datetime(2026, 8, 27, 16, 9, tzinfo=KST)) == []

    alerts = _alerts(tmp_path, datetime(2026, 8, 27, 16, 10, tzinfo=KST))

    assert len(alerts) == 1
    assert alerts[0].mode == "full"
    assert alerts[0].elapsed_minutes == 35
    assert "완료 표식 없음" in alerts[0].state


def test_failure_marker_is_included_and_alert_is_sent_once(tmp_path):
    state_dir = tmp_path / "state"
    alert_dir = tmp_path / "alerts"
    state_dir.mkdir()
    alert_dir.mkdir()
    for slot in ("07:30", "09:00", "15:00"):
        (state_dir / f"2026-08-27-{slot}.done").write_text("완료", encoding="utf-8")
    (state_dir / "2026-08-27-15:35.failed").write_text(
        "status=failed\nexitCode=1\nstage=시장데이터·지표\n",
        encoding="utf-8",
    )
    settings = settings_from_env(ENV)
    kwargs = {
        "now": datetime(2026, 8, 27, 16, 15, tzinfo=KST),
        "settings": settings,
        "state_dir": state_dir,
        "alert_dir": alert_dir,
        "lock_dir": tmp_path / "lock",
    }

    alerts = find_schedule_alerts(**kwargs)
    assert "실행 실패" in alerts[0].state
    assert "시장데이터·지표" in alerts[0].state

    alerts[0].marker.write_text("발송", encoding="utf-8")
    assert find_schedule_alerts(**kwargs) == []


def test_later_success_supersedes_an_earlier_missing_slot(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "2026-08-27-15:00.done").write_text("완료", encoding="utf-8")

    alerts = find_schedule_alerts(
        now=datetime(2026, 8, 27, 15, 20, tzinfo=KST),
        settings=settings_from_env(ENV),
        state_dir=state_dir,
        alert_dir=tmp_path / "alerts",
        lock_dir=tmp_path / "lock",
    )

    assert alerts == []


def test_sunday_has_no_schedule_alert(tmp_path):
    alerts = find_schedule_alerts(
        now=datetime(2026, 8, 30, 20, 0, tzinfo=KST),
        settings=settings_from_env(ENV),
        state_dir=tmp_path / "state",
        alert_dir=tmp_path / "alerts",
        lock_dir=tmp_path / "lock",
    )

    assert alerts == []


def test_schedule_alert_message_is_codex_ready(tmp_path):
    log = tmp_path / "update.err.log"
    log.write_text("RuntimeError: ^KS200 관측치 1개", encoding="utf-8")

    message = format_schedule_alert(
        scheduled_at=datetime(2026, 8, 27, 15, 35, tzinfo=KST),
        mode="full",
        elapsed_minutes=95,
        state="실행 실패 · 단계 시장데이터·지표 · 종료코드 1",
        log_file=str(log),
    )

    assert "[운영 지연]" in message
    assert "Codex 붙여넣기" in message
    assert "^KS200 관측치 1개" in message
    assert len(message) < 3000


def test_operations_alert_never_falls_back_to_risk_news_credentials():
    risk_news_only = {
        "TELEGRAM_BOT_TOKEN": "risk-news-token",
        "TELEGRAM_CHAT_ID": "risk-news-chat",
        "TELEGRAM_CHAT_IDS": "risk-news-chat,team-chat",
    }
    dedicated = {
        **risk_news_only,
        "MARKET_OPERATIONS_TELEGRAM_BOT_TOKEN": "finance-engineering-token",
        "MARKET_OPERATIONS_TELEGRAM_CHAT_IDS": "private-finance-chat",
    }

    assert operations_bot_token(risk_news_only) == ""
    assert operations_chat_ids(risk_news_only) == []
    assert operations_bot_token(dedicated) == "finance-engineering-token"
    assert operations_chat_ids(dedicated) == ["private-finance-chat"]
