import json
import math
from datetime import date, timedelta

from scripts.update_market_risk import (
    _weighted_asof_score_points,
    change_pressure_component_points,
    compare_series_quality,
    equity_stress_component_points,
    equity_stress_score_at,
    fetch_naver_market_index_series,
    level_and_change_component_points,
    level_and_change_score_at,
    level_and_point_change_score,
    make_difference_series,
    make_product_series,
    rolling_negative_point_changes,
    shipping_cost_pressure_score,
    yen_carry_unwind_component_points,
)


def test_foreign_ownership_drop_uses_percentage_points():
    values = [50.0] * 20 + [49.0, 50.5]

    changes = rolling_negative_point_changes(values, periods=20)

    assert changes[19] is None
    assert changes[20] == 1.0
    assert changes[21] == 0.0


def test_point_change_score_handles_up_and_down_risk_directions():
    widening_series = [
        {"date": f"2026-01-{day:02d}", "close": 3.0 + day * 0.01, "volume": None}
        for day in range(1, 81)
    ]
    falling_curve_series = [
        {"date": f"2026-01-{day:02d}", "close": 1.0 - day * 0.01, "volume": None}
        for day in range(1, 81)
    ]

    spread_score = level_and_point_change_score(widening_series, change_periods=20, direction="up")
    curve_score = level_and_point_change_score(falling_curve_series, change_periods=20, direction="down")

    assert spread_score["metrics"]["changePoints"] > 0
    assert curve_score["metrics"]["changePoints"] < 0
    assert spread_score["score"] > 50
    assert curve_score["score"] > 50


def test_weighted_asof_score_points_uses_component_points():
    points = _weighted_asof_score_points(
        {
            "daily": {"weight": 0.7, "points": [{"date": "2026-01-01", "value": 40}, {"date": "2026-01-02", "value": 60}]},
            "weekly": {"weight": 0.3, "points": [{"date": "2026-01-01", "value": 80}]},
        },
        limit=10,
    )

    assert points[-1] == {"date": "2026-01-02", "value": 66.0}


def test_naver_market_index_parser_sorts_dates_and_removes_commas(monkeypatch):
    payload = [
        {
            "localTradedAt": "2026-07-17T15:00:00+08:00",
            "closePrice": "3,080.31",
            "openPrice": "3,000.00",
            "highPrice": "3,100.00",
            "lowPrice": "2,990.00",
        },
        {
            "localTradedAt": "2026-07-10T15:00:00+08:00",
            "closePrice": "2,980.00",
            "openPrice": "-",
            "highPrice": None,
            "lowPrice": "2,900.00",
        },
    ]

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert "transport" in request.full_url
        assert timeout == 20
        return FakeResponse()

    monkeypatch.setattr("scripts.update_market_risk.urllib.request.urlopen", fake_urlopen)

    series = fetch_naver_market_index_series(
        {
            "category": "transport",
            "symbol": ".SCFIDXSSE",
            "target_observations": 2,
            "min_observations": 2,
        }
    )

    assert [point["date"] for point in series] == ["2026-07-10", "2026-07-17"]
    assert series[-1]["close"] == 3080.31
    assert series[0]["open"] is None


def test_shipping_cost_pressure_combines_weekly_and_daily_series_without_future_values():
    start = date(2024, 1, 1)
    scfi = [
        {"date": (start + timedelta(days=7 * index)).isoformat(), "close": 1000 * (1.01**index), "volume": None}
        for index in range(90)
    ]
    bdti = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 900 * (1.002**index), "volume": None}
        for index in range(500)
    ]
    bdi = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 1500 * (0.999**index), "volume": None}
        for index in range(500)
    ]

    result = shipping_cost_pressure_score({"scfi": scfi, "bdti": bdti, "bdi": bdi})

    assert result["score"] > 50
    assert result["metrics"]["scfiChange4ObsPct"] > 0
    assert result["metrics"]["bdtiChange20ObsPct"] > 0
    assert result["metrics"]["bdiChange20ObsPct"] < 0


def test_shipping_demand_strength_reduces_cost_divergence_risk():
    start = date(2024, 1, 1)
    scfi = [
        {"date": (start + timedelta(days=7 * index)).isoformat(), "close": 1000 * (1.01**index)}
        for index in range(90)
    ]
    bdti = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 900 * (1.002**index)}
        for index in range(500)
    ]
    weak_bdi = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 1500 * (0.999**index)}
        for index in range(500)
    ]
    strong_bdi = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 1500 * (1.003**index)}
        for index in range(500)
    ]

    weak_demand = shipping_cost_pressure_score({"scfi": scfi, "bdti": bdti, "bdi": weak_bdi})
    strong_demand = shipping_cost_pressure_score({"scfi": scfi, "bdti": bdti, "bdi": strong_bdi})

    assert weak_demand["score"] > strong_demand["score"]
    assert weak_demand["metrics"]["costDemandDivergence"] > strong_demand["metrics"]["costDemandDivergence"]


def test_product_series_uses_last_available_value_without_looking_ahead():
    first = [
        {"date": "2026-01-01", "close": 10.0},
        {"date": "2026-01-03", "close": 20.0},
    ]
    second = [
        {"date": "2026-01-02", "close": 2.0},
        {"date": "2026-01-04", "close": 3.0},
    ]

    products = make_product_series(first, second)

    assert products == [
        {"date": "2026-01-02", "close": 20.0, "volume": None},
        {"date": "2026-01-03", "close": 40.0, "volume": None},
        {"date": "2026-01-04", "close": 60.0, "volume": None},
    ]


def test_difference_series_uses_last_available_value_without_looking_ahead():
    first = [
        {"date": "2026-01-01", "close": 3.0},
        {"date": "2026-01-03", "close": 4.0},
    ]
    second = [
        {"date": "2026-01-02", "close": 5.0},
        {"date": "2026-01-04", "close": 6.0},
    ]

    differences = make_difference_series(first, second)

    assert differences == [
        {"date": "2026-01-02", "close": -2.0, "volume": None},
        {"date": "2026-01-03", "close": -1.0, "volume": None},
        {"date": "2026-01-04", "close": -2.0, "volume": None},
    ]


def test_change_only_pressure_is_zero_when_risk_direction_is_absent():
    start = date(2025, 1, 1)
    rising = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 100 + index}
        for index in range(100)
    ]

    downside_pressure = change_pressure_component_points(
        rising, change_periods=5, direction="down"
    )

    assert downside_pressure[-1]["value"] == 0.0


def test_yen_carry_watch_requires_actual_yen_strength():
    start = date(2025, 1, 1)
    dates = [(start + timedelta(days=index)).isoformat() for index in range(100)]
    series_map = {
        "vix": [{"date": day, "close": 10 + index} for index, day in enumerate(dates)],
        "spx": [{"date": day, "close": 300 - index} for index, day in enumerate(dates)],
    }
    market_index_map = {
        "usdjpy": [{"date": day, "close": 100 + index} for index, day in enumerate(dates)]
    }

    points = yen_carry_unwind_component_points(series_map, market_index_map)

    assert points[-1]["value"] == 0.0


def test_precomputed_rolling_scores_match_point_in_time_calculation():
    start = date(2025, 1, 1)
    series = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "close": 100 + index * 0.15 + math.sin(index / 5) * 4,
            "volume": None,
        }
        for index in range(180)
    ]

    equity_points = {point["date"]: point["value"] for point in equity_stress_component_points(series)}
    level_points = {
        point["date"]: point["value"] for point in level_and_change_component_points(series)
    }

    for index in (60, 100, 179):
        current_date = series[index]["date"]
        assert equity_points[current_date] == equity_stress_score_at(series, index)
        assert level_points[current_date] == level_and_change_score_at(series, index)


def test_series_quality_reports_basis_point_difference():
    reference = [
        {"date": "2026-07-15", "close": 4.13},
        {"date": "2026-07-16", "close": 4.16},
    ]
    candidate = [
        {"date": "2026-07-15", "close": 4.128},
        {"date": "2026-07-16", "close": 4.156},
    ]

    quality = compare_series_quality(reference, candidate)

    assert quality["overlapCount"] == 2
    assert quality["meanAbsoluteDifferenceBp"] == 0.3
    assert quality["maxAbsoluteDifferenceBp"] == 0.4
