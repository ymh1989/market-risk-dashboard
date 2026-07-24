import json
import math
from datetime import date, timedelta

from scripts.update_market_risk import (
    _weighted_asof_score_points,
    change_pressure_component_points,
    compare_series_quality,
    equity_stress_component_points,
    equity_stress_score_at,
    fetch_naver_market_index_latest_snapshot,
    fetch_naver_market_index_latest_snapshots,
    fetch_naver_market_index_series,
    level_and_change_component_points,
    level_and_change_score_at,
    level_and_point_change_score,
    japan_us_rate_spread_component_points,
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


def test_naver_live_bond_parser_separates_current_yield_from_previous_close(monkeypatch):
    payload = {
        "localTradedAt": "2026-07-24T16:01:40+09:00",
        "closePriceYield": 4.435,
        "yieldToLastClosePrice": 4.383,
        "yieldChange": 0.052,
        "delayTime": 0,
        "delayTimeName": "실시간",
        "marketStatus": "OPEN",
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert "/economic/bond/KR10YT%3DRR" in request.full_url
        assert timeout == 20
        return FakeResponse()

    monkeypatch.setattr("scripts.update_market_risk.urllib.request.urlopen", fake_urlopen)

    snapshot = fetch_naver_market_index_latest_snapshot(
        {"category": "bond", "symbol": "KR10YT=RR"}
    )

    assert snapshot["date"] == "2026-07-24"
    assert snapshot["observedAt"] == "2026-07-24T16:01:40+09:00"
    assert snapshot["close"] == 4.435
    assert snapshot["previousClose"] == 4.383
    assert snapshot["changeBps"] == 5.2
    assert snapshot["delayTimeName"] == "실시간"
    assert snapshot["displayStatus"] == "실시간"


def test_naver_delayed_market_index_parser_preserves_delay_status(monkeypatch):
    payload = {
        "localTradedAt": "2026-07-24T08:32:37+01:00",
        "closePrice": "98.57",
        "fluctuations": "-2.12",
        "delayTime": 10,
        "delayTimeName": "10분 지연",
        "marketStatus": "OPEN",
        "priceDataType": "DELAYED_PRICE",
        "marketIndexTotalInfos": [
            {"code": "lastClosePrice", "key": "전일", "value": "100.69"}
        ],
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(
        "scripts.update_market_risk.urllib.request.urlopen",
        lambda request, timeout: FakeResponse(),
    )

    snapshot = fetch_naver_market_index_latest_snapshot(
        {"category": "energy", "symbol": "LCOcv1"}
    )

    assert snapshot["close"] == 98.57
    assert snapshot["previousClose"] == 100.69
    assert snapshot["change"] == -2.12
    assert snapshot["changeBps"] is None
    assert snapshot["displayStatus"] == "10분 지연"


def test_latest_snapshot_is_appended_as_provisional_without_changing_eod(monkeypatch):
    series_map = {
        "kr3y": [{"date": "2026-07-23", "close": 3.907}],
        "kr10y": [{"date": "2026-07-23", "close": 4.383}],
    }
    original = json.loads(json.dumps(series_map))

    def fake_latest(config):
        previous_close = 3.907 if config["symbol"].startswith("KR3Y") else 4.383
        return {
            "date": "2026-07-24",
            "observedAt": "2026-07-24T16:00:00+09:00",
            "close": previous_close + 0.05,
            "previousClose": previous_close,
        }

    monkeypatch.setattr(
        "scripts.update_market_risk.NAVER_LATEST_INDEX_IDS",
        ("kr3y", "kr10y"),
    )
    monkeypatch.setattr(
        "scripts.update_market_risk.fetch_naver_market_index_latest_snapshot",
        fake_latest,
    )

    snapshots, statuses = fetch_naver_market_index_latest_snapshots(series_map)

    assert series_map == original
    assert statuses == {"kr3y": "live", "kr10y": "live"}
    assert all(snapshot["isProvisional"] for snapshot in snapshots.values())
    assert all(snapshot["confirmedDate"] == "2026-07-23" for snapshot in snapshots.values())


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


def test_japan_us_rate_spread_watch_tracks_narrowing_without_future_data():
    start = date(2025, 1, 1)
    dates = [(start + timedelta(days=index)).isoformat() for index in range(100)]
    narrowing_map = {
        "us10y_naver": [{"date": day, "close": 4.5} for day in dates],
        "jp10y_naver": [
            {"date": day, "close": 0.8 + index * 0.02} for index, day in enumerate(dates)
        ],
    }
    widening_map = {
        "us10y_naver": [{"date": day, "close": 4.5} for day in dates],
        "jp10y_naver": [
            {"date": day, "close": 2.8 - index * 0.02} for index, day in enumerate(dates)
        ],
    }

    narrowing = japan_us_rate_spread_component_points(narrowing_map)
    widening = japan_us_rate_spread_component_points(widening_map)
    historical_score = narrowing[-1]["value"]
    narrowing_map["jp10y_naver"].append(
        {"date": (start + timedelta(days=100)).isoformat(), "close": 4.4}
    )
    extended = japan_us_rate_spread_component_points(narrowing_map)

    assert historical_score > 0
    assert widening[-1]["value"] == 0.0
    assert extended[-2]["value"] == historical_score


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
