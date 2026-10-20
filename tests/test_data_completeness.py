from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from kospi_risk.dram_spot import PRODUCTS, build_payload

from scripts import audit_data_completeness
from scripts.audit_data_completeness import (
    assess_source_group,
    issue_log_lines,
    validate_series_rows,
    validate_dram_score_history,
)


def dram_audit_fixture(count):
    """공식 가격 수와 실제 생성 가능한 점수 수가 다른 경계를 재현합니다."""
    history = [
        {
            "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
            "pricesUsd": {key: 10 + index * 0.1 for key in PRODUCTS},
            "source": "trendforce-official",
        }
        for index in range(count)
    ]
    payload = build_payload(history, generated_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
    indicator = None
    if payload["series"]:
        indicator = {
            "id": "dram_spot_cycle_watch", "role": "observation", "weight": 0,
            "asOf": payload["latest"]["date"], "value": payload["latest"]["score"],
        }
    trend = [{"date": row["date"], "value": row["score"]} for row in payload["series"]]
    return payload, indicator, trend


@pytest.mark.parametrize("count,status", [(20, "ok"), (21, "warning"), (22, "warning"), (79, "warning"), (80, "ok"), (90, "ok")])
def test_dram_audit_accepts_complete_initial_score_history(count, status):
    payload, indicator, trend = dram_audit_fixture(count)
    result = validate_dram_score_history(payload, indicator, trend)
    assert result["status"] == status
    assert f"기대 점수 {max(0, count - 20)}개" in result["detail"]


@pytest.mark.parametrize("damage", [
    "missing", "truncated", "duplicate", "invalid", "wrong_value", "wrong_date",
    "wrong_latest", "wrong_card", "missing_card", "wrong_source", "wrong_count",
    "wrong_readiness", "invalid_price", "bad_date", "duplicate_history", "wrong_series",
])
def test_dram_audit_still_rejects_real_history_corruption(damage):
    payload, indicator, trend = dram_audit_fixture(90)
    if damage == "missing":
        trend = []
    elif damage == "truncated":
        trend = trend[-1:]
    elif damage == "duplicate":
        trend[1] = deepcopy(trend[0])
    elif damage == "invalid":
        trend[-1]["value"] = float("nan")
    elif damage == "wrong_value":
        trend[-1]["value"] -= 1
    elif damage == "wrong_date":
        trend[-1]["date"] = "2026-12-31"
    elif damage == "wrong_latest":
        payload["latest"]["score"] -= 1
    elif damage == "wrong_card":
        indicator["weight"] = 0.1
    elif damage == "missing_card":
        indicator = None
    elif damage == "wrong_source":
        payload["history"][0]["source"] = "stored-history"
    elif damage == "wrong_count":
        payload["qualityChecks"]["officialObservationCount"] = 21
    elif damage == "wrong_readiness":
        payload["qualityChecks"]["scoreStatus"] = "building-official-history"
    elif damage == "invalid_price":
        payload["history"][0]["pricesUsd"][next(iter(PRODUCTS))] = 0
    elif damage == "bad_date":
        payload["history"][0]["date"] = "not-a-date"
    elif damage == "duplicate_history":
        payload["history"][1]["date"] = payload["history"][0]["date"]
    elif damage == "wrong_series":
        payload["series"] = payload["series"][-1:]
    assert validate_dram_score_history(payload, indicator, trend)["status"] == "error"


def test_dram_audit_ignores_chart_only_history_for_score_readiness():
    payload, indicator, trend = dram_audit_fixture(20)
    payload["priceHistory"] = dram_audit_fixture(90)[0]["history"]
    assert validate_dram_score_history(payload, indicator, trend)["status"] == "ok"
    assert validate_dram_score_history(payload, indicator, [{"date": "2026-01-20", "value": 50}])["status"] == "error"


def test_dram_audit_failure_is_in_error_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit_data_completeness, "parse_args", lambda: type(
        "Args", (), {"output": str(tmp_path / "quality.json"), "strict": True}
    )())
    monkeypatch.setattr(audit_data_completeness, "build_report", lambda: {
        "status": "error", "score": 99, "summary": {"ok": 1, "warning": 0, "error": 1},
        "issues": [{"id": "series:market:dram_spot_cycle_watch", "label": "DRAM 추이",
                    "status": "error", "detail": "점수 누락"}],
    })
    assert audit_data_completeness.main() == 1
    assert "dram_spot_cycle_watch" in capsys.readouterr().err


def test_market_calendar_excludes_known_closed_session(monkeypatch):
    class Calendar:
        def sessions_in_range(self, start, end):
            assert start == date(2026, 10, 10)
            assert end == date(2026, 10, 13)
            return [date(2026, 10, 13)]
    monkeypatch.setattr(audit_data_completeness, "session_calendar", lambda *args: Calendar())
    assert audit_data_completeness.business_day_lag(date(2026, 10, 9), date(2026, 10, 13), "XTKS") == 1
    assert audit_data_completeness.business_day_lag(date(2026, 10, 9), date(2026, 10, 13)) == 2
    assert audit_data_completeness.market_calendar_name("us10y_naver") is None


def test_only_validated_fresh_m7_fallback_is_information():
    source = {"source": "price:AAPL", "provider": "dashboard_yahoo", "status": "warning",
              "last_value_date": "2026-10-12", "row_count": 1254, "is_stale": False,
              "duplicate_count": 0, "null_count": 0}
    def assess(patch):
        _, checks = audit_data_completeness.assess_m7_source_group({"sources": [{**source, **patch}]}, date(2026, 10, 13))
        return next(check for check in checks if check["id"] == "source:m7:price:AAPL")
    check = assess({})
    assert check["status"] == "ok"
    assert check["attention"] == "information"
    for patch in [{"null_count": 1}, {"duplicate_count": 1}, {"is_stale": True}, {"row_count": 2}, {"provider": "local_cache"}, {"last_value_date": "2026-09-01"}]:
        assert assess(patch)["attention"] == "action"


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


def test_source_audit_does_not_treat_preserved_history_as_failed_latest_fetch():
    group, checks = assess_source_group(
        "naver",
        "네이버",
        {"scfi": {
            "lastDate": "2026-09-24",
            "observations": 144,
            "fetchStatus": "live+history_preserved",
        }},
        {"scfi": {"label": "SCFI"}},
        date(2026, 9, 29),
        warning_lag=2,
        error_lag=5,
        min_observations=60,
        weekly_ids={"scfi"},
    )

    assert group["status"] == "ok"
    assert group["fallbackCount"] == 0
    assert checks[0]["status"] == "ok"


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


def test_m7_source_audit_marks_dashboard_price_fallback_as_warning():
    required_prices = (
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "META",
        "NVDA",
        "TSLA",
        "QQQ",
        "IGIB",
        "LQD",
        "HYG",
        "IEF",
    )
    payload = {
        "sources": [
            *[
                {
                    "source": f"price:{ticker}",
                    "provider": "dashboard_yahoo",
                    "status": "warning",
                    "last_value_date": "2026-07-30",
                }
                for ticker in required_prices
            ],
            {
                "source": "ofr_fsi",
                "provider": "ofr",
                "status": "ok",
                "last_value_date": "2026-07-28",
            },
            {
                "source": "treasury_yield_curve",
                "provider": "treasury",
                "status": "ok",
                "last_value_date": "2026-07-30",
            },
        ]
    }

    group, checks = audit_data_completeness.assess_m7_source_group(
        payload, date(2026, 7, 31)
    )

    assert group["status"] == "warning"
    assert group["seriesPresent"] == 14
    assert group["fallbackCount"] == 12
    assert not any(check["status"] == "error" for check in checks)


def test_issue_log_lines_include_failed_check_identity_and_detail():
    lines = issue_log_lines(
        {
            "issues": [
                {
                    "id": "cross:kospi200-hmm-els",
                    "label": "KOSPI200 HMM·ELS 기준일",
                    "status": "error",
                    "detail": "HMM 2026-07-28 · ELS 2026-07-29",
                }
            ]
        }
    )

    assert lines == [
        "[데이터 오류] KOSPI200 HMM·ELS 기준일 "
        "(cross:kospi200-hmm-els) · HMM 2026-07-28 · ELS 2026-07-29"
    ]
