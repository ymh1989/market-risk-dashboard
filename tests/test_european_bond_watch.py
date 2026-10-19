"""유럽 국채 관찰카드의 단위·일자 정합성과 미래정보 차단 검증."""

from datetime import date, timedelta
import math

import pytest

from scripts import update_market_risk as risk


def source_map():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(180)]
    return {
        "fr10y_naver": [{"date": day, "close": 3.4 + math.sin(i / 9) * 0.08} for i, day in enumerate(days)],
        "de10y_naver": [{"date": day, "close": 2.7} for day in days],
    }


def test_spread_units_and_common_date_only():
    sources = {
        "fr10y_naver": [{"date": "2026-10-16", "close": 4.781}, {"date": "2026-10-19", "close": 4.863}],
        "de10y_naver": [{"date": "2026-10-16", "close": 3.409}],
    }
    rows = risk.build_rate_spread_series(sources)["fr_de_10y_spread"]
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-10-16"
    assert rows[0]["close"] * 100 == pytest.approx(137.2)


def test_watch_widening_without_future_leakage_and_no_composite_impact():
    sources = source_map()
    calm = risk.france_germany_spread_indicator(sources)
    for i in range(160, 180):
        sources["fr10y_naver"][i]["close"] += (i - 159) * 0.04
    stressed = risk.france_germany_spread_indicator(sources)
    points = risk.france_germany_spread_points(sources)
    assert stressed["value"] > calm["value"]
    assert stressed["weight"] == 0 and stressed["role"] == "observation"
    # 한 나라만 새 값이 있을 때 점수나 기준일을 갱신하지 않습니다.
    sources["fr10y_naver"].append({"date": "2026-01-01", "close": 15})
    assert risk.france_germany_spread_indicator(sources) == stressed
    sources["de10y_naver"].append({"date": "2026-01-01", "close": 1})
    assert risk.france_germany_spread_points(sources)[:-1] == points
    weighted = {"id": "existing", "group": "macro", "value": 42.0, "weight": 1.0}
    _, before = risk.enrich_indicators([weighted])
    enriched, after = risk.enrich_indicators([weighted, stressed])
    assert after[0]["score"] == before[0]["score"]
    assert after[0]["contribution"] == before[0]["contribution"]
    assert enriched[-1]["contribution"] == 0


def test_missing_or_short_history_does_not_invent_score():
    assert risk.france_germany_spread_indicator({}) is None
    assert risk.france_germany_spread_points({}) == []
    assert risk.france_germany_spread_indicator({key: rows[:50] for key, rows in source_map().items()}) is None


def test_live_spread_removes_stale_derived_values_when_sources_mismatch():
    snapshot = {"date": "2026-10-19", "observedAt": "2026-10-19T09:53:00+02:00", "close": 4.863,
                "previousClose": 4.781, "isProvisional": True, "marketStatus": "OPEN"}
    german = {**snapshot, "close": 3.418, "previousClose": 3.409}
    inputs = {"fr10y_naver": snapshot, "de10y_naver": german, "fr_de_10y_spread": {"close": 999}}
    live, _ = risk.build_rate_spread_live_snapshots({}, inputs)
    assert live["fr_de_10y_spread"]["close"] * 100 == pytest.approx(144.5)
    assert live["fr_de_10y_spread"]["changeBps"] == pytest.approx(7.3)
    for bad in ({"date": "2026-10-16"}, {"close": float("nan")}, {"observedAt": "invalid"}):
        invalid = {**inputs, "de10y_naver": {**german, **bad}}
        result, statuses = risk.build_rate_spread_live_snapshots({}, invalid, {"fr_de_10y_spread": "old"})
        assert "fr_de_10y_spread" not in result and "fr_de_10y_spread" not in statuses


def test_negative_yields_remain_valid():
    sources = source_map()
    for rows in sources.values():
        for point in rows:
            point["close"] -= 4
    assert risk.france_germany_spread_indicator(sources)["value"] == risk.france_germany_spread_indicator(source_map())["value"]
