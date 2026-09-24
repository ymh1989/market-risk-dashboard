"""예약 실패 재시도가 다른 시각이나 미래 작업을 실행하지 않는지 검증합니다."""

import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_local_market_update.sh"


def run_schedule_check(tmp_path, *, retry="18:30", now="19:00", marker="failed"):
    if marker:
        (tmp_path / f"2026-09-24-18:30.{marker}").write_text("원본 실패 기록\n", encoding="utf-8")
    source = SCRIPT.read_text(encoding="utf-8")
    function = "is_scheduled_now() {" + source.split("is_scheduled_now() {", 1)[1].split("\nmark_scheduled_done()", 1)[0]
    environment = {
        **os.environ,
        "STATE_DIR": str(tmp_path),
        "TIMES": "07:30,15:35,18:30",
        "MONDAY_TIMES": "15:35,18:30",
        "SATURDAY_TIMES": "07:30",
        "RETRY_SCHEDULED_KST": retry,
        "SCHEDULE_GRACE_MINUTES": "10",
        "TEST_NOW": now,
    }
    clock = '''
kst_now() {
  case "$1" in
    +%H:%M) printf '%s\n' "$TEST_NOW" ;;
    +%u) printf '4\n' ;;
    +%Y-%m-%d) printf '2026-09-24\n' ;;
    *) printf '2026-09-24 %s:00 KST\n' "$TEST_NOW" ;;
  esac
}
'''
    return subprocess.run(
        ["/bin/bash", "-c", "set -Eeuo pipefail\n" + clock + function +
         '\nis_scheduled_now\nprintf "slot=%s\\nstate=%s\\n" "$SCHEDULED_TIME" "$SCHEDULE_STATE_FILE"'],
        env=environment, text=True, capture_output=True, timeout=5,
    )


def test_retry_keeps_original_slot_outside_grace_window(tmp_path):
    result = run_schedule_check(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "slot=18:30" in result.stdout
    assert "2026-09-24-18:30.done" in result.stdout
    assert (tmp_path / "2026-09-24-18:30.failed").read_text() == "원본 실패 기록\n"


@pytest.mark.parametrize("kwargs", [
    {"marker": None},
    {"marker": "done"},
    {"now": "18:00"},
    {"retry": "17:00"},
    {"retry": ""},
])
def test_retry_rejects_unfailed_completed_future_or_unscheduled_slots(tmp_path, kwargs):
    result = run_schedule_check(tmp_path, **kwargs)
    assert result.returncode != 0


def test_regular_scheduled_run_is_unchanged(tmp_path):
    result = run_schedule_check(tmp_path, retry="", now="18:31", marker=None)
    assert result.returncode == 0, result.stderr
    assert "slot=18:30" in result.stdout


def test_runtime_uses_calendar_before_query_and_keeps_failure_audit():
    source = SCRIPT.read_text(encoding="utf-8")
    krx = source.split('elif [[ "$UPDATE_MODE" == "krx" ]]; then', 1)[1]
    assert krx.index("--resolve-date") < krx.index('update_kospi_breadth_data "$BREADTH_END_DATE"')
    assert '"${SCHEDULE_FAILED_FILE}.before-retry-$RUN_ID"' in source
    assert '--krx-session-date "${BREADTH_END_DATE:-}"' in source
