import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


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
        times="07:30,12:30,15:35",
        full_times="07:30,15:35",
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
    assert [item["mode"] for item in payload["schedule"]["times"]] == ["full", "fast", "full"]
    assert all(source["lastDate"] for source in payload["sources"])
