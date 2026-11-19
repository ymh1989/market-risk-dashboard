from datetime import date, datetime, timezone
import os
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

from scripts.market_session_schedule import (
    KST, breadth_reference_date, extra_launchd_intervals, original_time,
    schedule_overrides, shifted_times,
)
from scripts.monitor_local_market_update import find_schedule_alerts, settings_from_env
from scripts.run_session_news import news_environment
from scripts.install_session_news_launchd import adjusted_plist, special_preparation_plist

ROOT = Path(__file__).resolve().parents[1]
CSAT = date(2026, 11, 19)
TIMES = "07:30,09:00,15:00,15:35,18:30"


def test_only_confirmed_session_changes_and_tomorrow_restores():
    assert shifted_times(TIMES, CSAT) == "07:30,09:00,15:00,16:35,19:30"
    for day in (date(2026, 11, 18), date(2026, 11, 20), date(2027, 11, 19)):
        assert shifted_times(TIMES, day) == TIMES
    assert shifted_times("16:10", CSAT) == "16:10"
    assert original_time("16:35", CSAT) == "15:35"
    assert original_time("19:30", CSAT) == "18:30"


@pytest.mark.parametrize("time,expected", [("15:35", "2026-11-18"), ("16:34", "2026-11-18"), ("16:35", "2026-11-19")])
def test_breadth_waits_for_delayed_close(time, expected):
    assert breadth_reference_date(datetime.fromisoformat(f"2026-11-19T{time}+09:00")).isoformat() == expected
    assert breadth_reference_date(datetime(2026, 11, 20, 15, 35, tzinfo=KST)) == date(2026, 11, 20)


def test_launchd_adds_special_wakeups_without_next_year_rule():
    xml = extra_launchd_intervals(TIMES, TIMES, "07:30", CSAT)
    entries = plistlib.loads(f'<plist version="1.0"><array>{xml}</array></plist>'.encode())
    assert entries == [
        {"Month": 11, "Day": 19, "Hour": 16, "Minute": 35},
        {"Month": 11, "Day": 19, "Hour": 19, "Minute": 30},
    ]
    assert extra_launchd_intervals(TIMES, TIMES, "07:30", date(2026, 11, 20)) == ""


def test_watchdog_uses_shifted_deadlines_and_correct_modes(tmp_path):
    settings = settings_from_env({"LOCAL_MARKET_UPDATE_TIMES": "15:35,18:30"})
    def alerts(time):
        return find_schedule_alerts(now=datetime.fromisoformat(f"2026-11-19T{time}+09:00"), settings=settings,
                                    state_dir=tmp_path, alert_dir=tmp_path / "alerts", lock_dir=tmp_path / "lock")
    assert alerts("16:40") == []
    assert [(a.scheduled_at.strftime("%H:%M"), a.mode) for a in alerts("17:10")] == [("16:35", "full")]
    (tmp_path / "2026-11-19-16:35.done").touch()
    assert alerts("19:00") == []
    assert [(a.scheduled_at.strftime("%H:%M"), a.mode) for a in alerts("19:45")] == [("19:30", "krx")]


def test_schedule_payload_keeps_normal_times():
    schedule = {"times": [{"time": "15:35", "mode": "full"}, {"time": "18:30", "mode": "krx"}]}
    result = schedule_overrides(schedule)
    assert result["2026-11-19"]["times"] == [{"time": "16:35", "mode": "full"}, {"time": "19:30", "mode": "krx"}]
    assert schedule["times"][0]["time"] == "15:35"
    assert "2026-11-20" not in result


@pytest.mark.parametrize("day,time,eligible,mode", [
    ("2026-11-19", "15:35", False, ""), ("2026-11-19", "16:35", True, "full"),
    ("2026-11-19", "18:30", False, ""), ("2026-11-19", "19:30", True, "krx"),
    ("2026-11-20", "15:35", True, "full"), ("2026-11-20", "16:35", False, ""),
    ("2026-11-19", "09:00", True, "live"),
])
def test_actual_shell_dispatch_uses_exception(tmp_path, day, time, eligible, mode):
    source = (ROOT / "scripts/run_local_market_update.sh").read_text()
    functions = []
    for name in ("is_scheduled_now", "resolve_update_mode"):
        body = source.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]
        functions.append(f"{name}() {{{body}\n}}")
    clock = f'''kst_now() {{
case "$1" in
+%H:%M) echo {time};;
+%u) echo {date.fromisoformat(day).isoweekday()};;
+%Y-%m-%d) echo {day};;
*) echo {day};;
esac
}}'''
    command = "set -eu\n" + clock + "\n" + "\n".join(functions) + '\nif is_scheduled_now; then echo "SELECTED:$SCHEDULED_TIME:$(resolve_update_mode)"; fi'
    env = {**os.environ, "ROOT": str(ROOT), "PYTHON_BIN": sys.executable,
           "TIMES": TIMES, "MONDAY_TIMES": TIMES, "SATURDAY_TIMES": "07:30", "MODE": "auto",
           "STATE_DIR": str(tmp_path), "FULL_TIMES": "15:35", "KRX_TIMES": "18:30",
           "LIVE_TIMES": "09:00,15:00", "SCHEDULE_GRACE_MINUTES": "10", "RETRY_SCHEDULED_KST": ""}
    result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    if eligible:
        assert f"SELECTED:{time}:{mode}" in result.stdout, (result.stdout, result.stderr)
    else:
        assert "SELECTED:" not in result.stdout, (result.stdout, result.stderr)


def test_news_keeps_credentials_and_freshness_guard(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEWS_DIGEST_TIMES=08:30,12:30,16:10\nTELEGRAM_BOT_TOKEN=not-a-real-token\n")
    current = datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc)
    changed = news_environment(env, current, {})
    assert changed["NEWS_DIGEST_TIMES"] == "08:30,12:30,17:10"
    assert changed["MARKET_COMMENTARY_TIMES"] == "08:30,12:30,17:10"
    assert changed["NEWS_DIGEST_DASHBOARD_CARD_TIMES"] == "08:30,17:10"
    assert changed["LOCAL_MARKET_UPDATE_FULL_TIMES"] == "16:35"
    assert "TELEGRAM_BOT_TOKEN" not in changed
    normal = news_environment(env, datetime(2026, 11, 20, tzinfo=KST), {})
    assert normal["NEWS_DIGEST_TIMES"] == "08:30,12:30,16:10"


def test_news_agent_install_is_idempotent_and_retains_arguments(tmp_path):
    original = {"Label": "com.marketlab.news-digest.prepare", "ProgramArguments": ["python3", "/news/scripts/prepare.py", "--only-at-preparation-kst"],
                "StartCalendarInterval": [{"Hour": 15, "Minute": 40}]}
    wrapper = tmp_path / "run_session_news.py"
    result = adjusted_plist(original, wrapper, CSAT)
    assert result["ProgramArguments"] == ["python3", str(wrapper), "/news/scripts/prepare.py", "--only-at-preparation-kst"]
    assert {"Month": 11, "Day": 19, "Hour": 16, "Minute": 40} in result["StartCalendarInterval"]
    assert adjusted_plist(result, wrapper, CSAT) == result
    assert original["ProgramArguments"][1] == "/news/scripts/prepare.py"


def test_running_preparation_gets_separate_date_limited_job(tmp_path):
    original = {"Label": "com.marketlab.news-digest.prepare", "ProgramArguments": ["python3", "/news/scripts/prepare.py", "--only-at-preparation-kst"],
                "StartCalendarInterval": [{"Hour": 15, "Minute": 40}], "RunAtLoad": True}
    result = special_preparation_plist(original, tmp_path / "run_session_news.py", CSAT, 30)
    assert result["Label"] == "com.marketlab.news-digest.session-20261119.prepare"
    assert result["StartCalendarInterval"] == [{"Month": 11, "Day": 19, "Hour": 16, "Minute": 40}]
    assert "RunAtLoad" not in result
    assert original["Label"] == "com.marketlab.news-digest.prepare"
