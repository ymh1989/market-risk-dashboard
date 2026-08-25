import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


WEEKDAY_TIMES = "07:30,09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30"
MONDAY_TIMES = "09:00,10:00,11:00,12:00,13:00,14:00,15:00,15:35,18:30"
LIVE_TIMES = "09:00,10:00,11:00,12:00,13:00,14:00,15:00"
KRX_TIMES = "18:30"


def load_pipeline_status_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "write_pipeline_status.py"
    spec = importlib.util.spec_from_file_location("write_pipeline_status", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pipeline_status_keeps_previous_run_history(tmp_path):
    module = load_pipeline_status_module()
    output = tmp_path / "pipeline-status.json"
    output.write_text(
        json.dumps(
            {
                "history": [
                    {
                        "runId": "2026-07-20-12:30",
                        "status": "success",
                        "scheduledTime": "12:30",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        output=str(output),
        mode="full",
        times=WEEKDAY_TIMES,
        monday_times=MONDAY_TIMES,
        saturday_times="07:30",
        full_times="15:35",
        live_times=LIVE_TIMES,
        krx_times=KRX_TIMES,
        schedule_grace_minutes=10,
        scheduled_time="15:35",
        run_id="2026-07-20-15:35",
        started_at="2026-07-20 15:35:00 KST",
        completed_at="2026-07-20 15:53:48 KST",
        total_duration=1128,
        market_duration=121,
        ml_duration=969,
        validation_duration=3,
    )

    payload = module.build_payload(args)

    assert [item["runId"] for item in payload["history"][:2]] == [
        "2026-07-20-15:35",
        "2026-07-20-12:30",
    ]
    assert [item["mode"] for item in payload["schedule"]["times"]] == [
        "fast",
        "live",
        "live",
        "live",
        "live",
        "live",
        "live",
        "live",
        "full",
        "krx",
    ]
    assert [item["mode"] for item in payload["schedule"]["mondayTimes"]] == [
        "live",
        "live",
        "live",
        "live",
        "live",
        "live",
        "live",
        "full",
        "krx",
    ]
    assert payload["schedule"]["saturdayTimes"] == [{"time": "07:30", "mode": "full"}]
    assert payload["schedule"]["weekdaysOnly"] is False
    assert payload["schedule"]["delayGraceMinutes"] == 10
    assert payload["schedule"]["expectedDurationMinutes"]["live"] == 3
    assert payload["schedule"]["expectedDurationMinutes"]["krx"] == 4
    assert all(source["lastDate"] for source in payload["sources"])


def test_research_log_copies_operational_fields_without_market_narrative():
    module = load_pipeline_status_module()
    dashboard = {
        "sections": [
            {
                "id": "market",
                "observationJournal": [
                    {
                        "id": "flow-deleveraging",
                        "title": "옵션 헤지·디레버리징",
                        "status": "주의",
                        "score": 71.2,
                        "tone": "caution",
                        "decision": "관찰지표 보강",
                        "assessment": "긴 시장 해석",
                        "operation": "가중치 0 유지",
                    }
                ],
            }
        ]
    }

    log = module.research_log(dashboard)

    assert log == [
        {
            "id": "flow-deleveraging",
            "title": "옵션 헤지·디레버리징",
            "status": "주의",
            "score": 71.2,
            "tone": "caution",
            "decision": "관찰지표 보강",
            "operation": "가중치 0 유지",
        }
    ]


def test_pipeline_status_can_refresh_research_log_without_fabricating_a_run(tmp_path):
    module = load_pipeline_status_module()
    output = tmp_path / "pipeline-status.json"
    previous = {
        "current": {"runId": "scheduled-full", "mode": "full"},
        "schedule": {"times": [{"time": "15:35", "mode": "full"}]},
        "stages": [{"id": "ml", "status": "success"}],
        "history": [{"runId": "scheduled-full"}],
    }
    output.write_text(json.dumps(previous), encoding="utf-8")
    args = SimpleNamespace(
        output=str(output),
        mode="full",
        times=WEEKDAY_TIMES,
        monday_times=MONDAY_TIMES,
        saturday_times="07:30",
        full_times="15:35",
        live_times=LIVE_TIMES,
        krx_times=KRX_TIMES,
        schedule_grace_minutes=10,
        scheduled_time="",
        run_id="",
        started_at="2026-07-29 23:00:00 KST",
        completed_at="2026-07-29 23:10:00 KST",
        total_duration=600,
        market_duration=590,
        ml_duration=0,
        validation_duration=10,
        preserve_current=True,
    )

    payload = module.build_payload(args)

    assert payload["current"] == previous["current"]
    assert payload["schedule"] == previous["schedule"]
    assert payload["stages"] == previous["stages"]
    assert payload["history"] == previous["history"]
    assert len(payload["researchLog"]) == 5
