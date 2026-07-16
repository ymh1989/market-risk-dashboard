from scripts.update_market_risk import (
    _weighted_asof_score_points,
    level_and_point_change_score,
    rolling_negative_point_changes,
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
