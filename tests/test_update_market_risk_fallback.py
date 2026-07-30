from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_market_risk.py"


def load_update_module():
    spec = importlib.util.spec_from_file_location("update_market_risk_fallback_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_cache_supplies_dgs2_when_raw_csv_has_no_fred_columns(tmp_path, monkeypatch):
    module = load_update_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "HISTORY_CACHE_FILE", tmp_path / "data" / "market-history-cache.json")

    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "market_data.csv").write_text("date,KOSPI\n2026-01-01,3000\n", encoding="utf-8")

    today = date.today()
    points = [
        {"date": (today - timedelta(days=119 - index)).isoformat(), "close": 4.0 + index / 1000}
        for index in range(120)
    ]
    module.HISTORY_CACHE_FILE.write_text(
        json.dumps({"schemaVersion": 2, "fred": {"us2y": points}}),
        encoding="utf-8",
    )

    series, source = module.load_cached_fred_series(module.FRED_SERIES["us2y"])

    assert source == "data/market-history-cache.json"
    assert len(series) == 120
    assert series[-1]["date"] == today.isoformat()


def test_yahoo_history_cache_prevents_latest_date_regression(monkeypatch):
    module = load_update_module()
    live_series = [
        {"date": f"2026-07-{day:02d}", "close": float(day), "volume": None}
        for day in range(1, 18)
    ]
    live_series = [
        {"date": f"2026-{month:02d}-{day:02d}", "close": float(month * 100 + day), "volume": None}
        for month in range(1, 7)
        for day in range(1, 15)
    ] + live_series
    cached_series = live_series + [
        {"date": "2026-07-29", "close": 21.5, "volume": None}
    ]
    monkeypatch.setattr(module, "fetch_yahoo_chart", lambda _symbol, range_value="2y": live_series)

    series, status = module.fetch_yahoo_chart_with_fallback("^VIX3M", cached_series)

    assert status == "yahoo+history-cache"
    assert series[-1] == {"date": "2026-07-29", "close": 21.5, "volume": None}
    assert len({point["date"] for point in series}) == len(series)


def test_yahoo_live_series_wins_when_it_is_newer(monkeypatch):
    module = load_update_module()
    cached_series = [
        {"date": f"2026-06-{day:02d}", "close": float(day), "volume": None}
        for day in range(1, 31)
    ] * 3
    live_series = [
        {"date": f"2026-{month:02d}-{day:02d}", "close": float(month * 100 + day), "volume": None}
        for month in range(1, 7)
        for day in range(1, 15)
    ] + [{"date": "2026-07-30", "close": 20.0, "volume": None}]
    monkeypatch.setattr(module, "fetch_yahoo_chart", lambda _symbol, range_value="2y": live_series)

    series, status = module.fetch_yahoo_chart_with_fallback("^VIX3M", cached_series)

    assert status == "yahoo"
    assert series is live_series
    assert series[-1]["date"] == "2026-07-30"
