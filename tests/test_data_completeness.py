from datetime import date

from scripts import audit_data_completeness
from scripts.audit_data_completeness import assess_source_group, validate_series_rows


def test_source_audit_detects_missing_and_stale_series():
    expected = {
        "fresh": {"label": "최신 지표"},
        "stale": {"label": "지연 지표"},
        "missing": {"label": "누락 지표"},
    }
    items = {
        "fresh": {"lastDate": "2026-07-23", "observations": 100},
        "stale": {"lastDate": "2026-07-10", "observations": 100},
    }

    group, checks = assess_source_group(
        "sample",
        "샘플",
        items,
        expected,
        date(2026, 7, 24),
        warning_lag=2,
        error_lag=5,
        min_observations=80,
    )

    status_by_id = {item["id"]: item["status"] for item in checks}
    assert group["status"] == "error"
    assert group["seriesPresent"] == 2
    assert status_by_id["source:sample:fresh"] == "ok"
    assert status_by_id["source:sample:stale"] == "error"
    assert status_by_id["source:sample:missing"] == "error"


def test_series_audit_rejects_duplicate_or_invalid_rows():
    rows = [
        {"date": "2026-07-22", "close": 100},
        {"date": "2026-07-22", "close": None},
    ]

    check = validate_series_rows(
        "series:test",
        "테스트 시계열",
        rows,
        min_rows=2,
        value_key="close",
    )

    assert check["status"] == "error"


def test_source_audit_marks_degraded_fallback_as_warning():
    group, checks = assess_source_group(
        "sample",
        "샘플",
        {
            "kospi": {
                "lastDate": "2026-07-24",
                "observations": 500,
                "fetchStatus": "yahoo_only: naver timeout",
            }
        },
        {"kospi": {"label": "KOSPI"}},
        date(2026, 7, 24),
        warning_lag=2,
        error_lag=5,
        min_observations=80,
    )

    assert group["status"] == "warning"
    assert group["fallbackCount"] == 1
    assert checks[0]["status"] == "warning"


def test_ml_source_audit_surfaces_supplement_failure(monkeypatch):
    monkeypatch.setattr(
        audit_data_completeness,
        "load_source_config",
        lambda _path: {
            "required": {
                "KOSPI": {
                    "provider": "yahoo",
                    "symbol": "^KS11",
                    "label": "KOSPI",
                }
            }
        },
    )
    metadata = {
        "sources": [
            {
                "column": "KOSPI",
                "provider": "yahoo",
                "status": "ok",
                "warning": "naver 조회 실패",
                "rows": 7000,
                "lastDate": "2026-07-24",
                "coverageRatio": 1.0,
            }
        ]
    }

    group, checks = audit_data_completeness.assess_ml_source_group(metadata, date(2026, 7, 24))

    assert group["status"] == "warning"
    assert checks[0]["status"] == "warning"
