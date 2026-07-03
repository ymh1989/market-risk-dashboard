from scripts.update_market_risk import rolling_negative_point_changes


def test_foreign_ownership_drop_uses_percentage_points():
    values = [50.0] * 20 + [49.0, 50.5]

    changes = rolling_negative_point_changes(values, periods=20)

    assert changes[19] is None
    assert changes[20] == 1.0
    assert changes[21] == 0.0
