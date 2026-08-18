from __future__ import annotations

import importlib.util
import json
import urllib.parse
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


def test_fred_official_api_parser(monkeypatch):
    module = load_update_module()
    api_key = "d" * 32
    observations = [
        {"date": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}", "value": str(4 + index / 1000)}
        for index in range(84)
    ]
    observations[3]["value"] = "."

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"observations": observations}).encode("utf-8")

    def fake_urlopen(request, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        assert query["series_id"] == ["DGS2"]
        assert query["api_key"] == [api_key]
        assert query["file_type"] == ["json"]
        assert timeout == 8
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = module.fetch_fred_api_series("DGS2", api_key, lookback_days=180)

    assert len(result) == 83
    assert result[0]["date"] == observations[0]["date"]
    assert all(point["volume"] is None for point in result)


def test_fred_live_query_runs_before_recent_cache(monkeypatch):
    module = load_update_module()
    cached = [
        {"date": "2026-08-18", "close": 4.0, "volume": None}
        for _ in range(80)
    ]
    live = [
        {"date": "2026-08-19", "close": 4.1, "volume": None}
        for _ in range(80)
    ]
    calls = []

    monkeypatch.setattr(module, "load_cached_fred_series", lambda _config: (cached, "cache"))
    monkeypatch.setattr(
        module,
        "fetch_fred_series",
        lambda series_id, **kwargs: calls.append(series_id) or live,
    )

    result = module.fetch_fred_series_with_fallback(module.FRED_SERIES["us2y"])

    assert calls == ["DGS2"]
    assert result is live


def test_fred_api_error_masks_key_before_csv_fallback(monkeypatch, capsys):
    module = load_update_module()
    api_key = "c" * 32
    csv_series = [
        {"date": f"2026-01-{(index % 28) + 1:02d}", "close": 4.0, "volume": None}
        for index in range(80)
    ]

    monkeypatch.setattr(module, "load_local_env_value", lambda _name: api_key)
    monkeypatch.setattr(
        module,
        "fetch_fred_api_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(f"request failed api_key={api_key}")
        ),
    )
    monkeypatch.setattr(module, "fetch_fred_graph_series", lambda *args, **kwargs: csv_series)

    result = module.fetch_fred_series("DGS2")
    output = capsys.readouterr().out

    assert result is csv_series
    assert api_key not in output
    assert "***" in output


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
