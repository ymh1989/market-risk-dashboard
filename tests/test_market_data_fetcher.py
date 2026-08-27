from __future__ import annotations

import json

import pandas as pd
import pytest

from kospi_risk import market_data_fetcher
from kospi_risk.market_data_fetcher import (
    SourceFetchResult,
    fetch_fred_series,
    fetch_market_data,
    fetch_naver_series,
)


def fake_source_config():
    return {
        "fetch": {"provider": "yahoo", "range": "1y", "alignment": "outer"},
        "required": {
            "KOSPI": {"provider": "yahoo", "symbol": "^KS11", "label": "KOSPI"},
            "SPX": {"provider": "yahoo", "symbol": "^GSPC", "label": "S&P 500"},
            "SOX": {"provider": "yahoo", "symbol": "^SOX", "label": "SOX"},
            "USDKRW": {"provider": "yahoo", "symbol": "KRW=X", "label": "USD/KRW"},
        },
        "optional": {
            "VIX": {"provider": "yahoo", "symbol": "^VIX", "label": "VIX"},
        },
    }


def fake_fetcher(column, spec, fetch_config, range_value, start, end):
    dates = pd.bdate_range("2024-01-01", periods=5)
    frame = pd.DataFrame({"date": dates, column: range(100, 105)})
    return SourceFetchResult(
        column=column,
        provider="yahoo",
        symbol=spec["symbol"],
        label=spec["label"],
        frame=frame,
        status="ok",
    )


def test_fetch_market_data_merges_sources_and_writes_metadata():
    df, metadata = fetch_market_data(fake_source_config(), fetcher=fake_fetcher)
    assert list(df.columns) == ["date", "KOSPI", "SPX", "SOX", "USDKRW", "VIX"]
    assert len(df) == 5
    assert metadata["rows"] == 5
    assert metadata["missingByColumn"]["KOSPI"] == 0
    assert all(source["status"] == "ok" for source in metadata["sources"])


def test_required_source_failure_is_fail_fast():
    def failing_fetcher(column, spec, fetch_config, range_value, start, end):
        if column == "KOSPI":
            raise RuntimeError("boom")
        return fake_fetcher(column, spec, fetch_config, range_value, start, end)

    with pytest.raises(RuntimeError, match="필수 데이터 수집 실패"):
        fetch_market_data(fake_source_config(), fetcher=failing_fetcher)


def test_optional_source_failure_is_recorded_not_fatal():
    def partly_failing_fetcher(column, spec, fetch_config, range_value, start, end):
        if column == "VIX":
            raise RuntimeError("optional failed")
        return fake_fetcher(column, spec, fetch_config, range_value, start, end)

    df, metadata = fetch_market_data(fake_source_config(), fetcher=partly_failing_fetcher)
    assert "VIX" not in df.columns
    failed = [source for source in metadata["sources"] if source["status"] == "failed"]
    assert failed[0]["column"] == "VIX"


def test_left_alignment_uses_kospi_trading_calendar():
    config = fake_source_config()
    config["fetch"]["alignment"] = "left"

    def mismatched_fetcher(column, spec, fetch_config, range_value, start, end):
        dates = pd.bdate_range("2024-01-01", periods=5)
        if column != "KOSPI":
            dates = dates.append(pd.DatetimeIndex([pd.Timestamp("2024-01-08")]))
        return SourceFetchResult(
            column=column,
            provider="yahoo",
            symbol=spec["symbol"],
            label=spec["label"],
            frame=pd.DataFrame({"date": dates, column: range(100, 100 + len(dates))}),
            status="ok",
        )

    df, metadata = fetch_market_data(config, fetcher=mismatched_fetcher)
    assert len(df) == 5
    assert df["date"].max() == pd.Timestamp("2024-01-05")
    assert metadata["alignment"] == "left"


def test_configured_start_date_is_forwarded_to_sources():
    config = fake_source_config()
    config["fetch"].pop("range")
    config["fetch"]["start"] = "1996-01-01"
    observed = []

    def recording_fetcher(column, spec, fetch_config, range_value, start, end):
        observed.append((range_value, start, end))
        return fake_fetcher(column, spec, fetch_config, range_value, start, end)

    fetch_market_data(config, fetcher=recording_fetcher)
    assert observed
    assert all(item[0] is None and item[1] == "1996-01-01" and item[2] for item in observed)


def test_fred_series_parser_uses_observation_date_csv(monkeypatch):
    monkeypatch.setattr(market_data_fetcher, "_local_env_value", lambda _name: "")

    class FakeCompletedProcess:
        stdout = "observation_date,DGS2\n2024-01-01,4.25\n2024-01-02,.\n2024-01-03,4.30\n"

    def fake_run(args, check, capture_output, text):
        assert "fredgraph.csv" in args[-1]
        assert check and capture_output and text
        return FakeCompletedProcess()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = fetch_fred_series(
        "US2Y",
        {"provider": "fred", "symbol": "DGS2", "label": "US 2Y"},
        {"timeout_seconds": 5},
        start="2024-01-02",
        end="2024-01-04",
    )

    assert result.provider == "fred_csv"
    assert result.symbol == "DGS2"
    assert list(result.frame["US2Y"]) == [4.30]


def test_fred_series_prefers_official_api(monkeypatch):
    api_key = "a" * 32
    payload = {
        "observations": [
            {"date": "2024-01-01", "value": "4.25"},
            {"date": "2024-01-02", "value": "."},
            {"date": "2024-01-03", "value": "4.30"},
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert "api.stlouisfed.org/fred/series/observations" in request.full_url
        assert "series_id=DGS2" in request.full_url
        assert f"api_key={api_key}" in request.full_url
        assert timeout == 5
        return FakeResponse()

    monkeypatch.setattr(market_data_fetcher, "_local_env_value", lambda _name: api_key)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: pytest.fail("CSV fallback should not run"))

    result = fetch_fred_series(
        "US2Y",
        {"provider": "fred", "symbol": "DGS2", "label": "US 2Y"},
        {"timeout_seconds": 5},
        start="2024-01-01",
        end="2024-01-04",
    )

    assert result.provider == "fred_api"
    assert result.status == "ok"
    assert list(result.frame["US2Y"]) == [4.25, 4.30]


def test_fred_series_falls_back_to_csv_without_leaking_key(monkeypatch):
    api_key = "b" * 32

    class FakeCompletedProcess:
        stdout = "observation_date,DGS2\n2024-01-01,4.25\n2024-01-03,4.30\n"

    def fail_api(request, timeout):
        raise TimeoutError(f"failed request {request.full_url}")

    monkeypatch.setattr(market_data_fetcher, "_local_env_value", lambda _name: api_key)
    monkeypatch.setattr("urllib.request.urlopen", fail_api)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: FakeCompletedProcess())

    result = fetch_fred_series(
        "US2Y",
        {"provider": "fred", "symbol": "DGS2", "label": "US 2Y"},
        {"timeout_seconds": 5},
        start="2024-01-01",
        end="2024-01-04",
    )

    assert result.provider == "fred_csv"
    assert result.status == "fallback"
    assert api_key not in (result.error or "")
    assert "***" in (result.error or "")


def test_fred_series_uses_history_cache_when_network_fails(tmp_path, monkeypatch):
    cache_file = tmp_path / "market-history-cache.json"
    cache_file.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "fred": {
                    "us2y": [
                        {"date": "2024-01-02", "close": 4.25},
                        {"date": "2024-01-03", "close": 4.30},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    def fail(*args, **kwargs):
        raise TimeoutError("network timeout")

    monkeypatch.setattr(market_data_fetcher, "FRED_HISTORY_CACHE", cache_file)
    monkeypatch.setattr(market_data_fetcher, "_local_env_value", lambda _name: "")
    monkeypatch.setattr("subprocess.run", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)

    result = fetch_fred_series(
        "US2Y",
        {"provider": "fred", "symbol": "DGS2", "cache_key": "us2y", "label": "US 2Y"},
        {"timeout_seconds": 1},
        start="2024-01-01",
        end="2024-01-04",
    )

    assert result.provider == "fred_cache"
    assert result.status == "cached"
    assert list(result.frame["US2Y"]) == [4.25, 4.30]


def test_naver_series_parser_reads_index_close(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return (
                b"[['date','open','high','low','close','volume','foreign'],"
                b"['20260722',7000,7100,6900,7010,100,0],"
                b"['20260723',7010,7200,7000,7150,110,0]]"
            )

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse())
    result = fetch_naver_series(
        "KOSPI",
        {"provider": "naver", "symbol": "KOSPI", "label": "KOSPI"},
        {"timeout_seconds": 1},
        start="2026-07-01",
        end="2026-07-24",
    )

    assert result.provider == "naver"
    assert result.frame["date"].max() == pd.Timestamp("2026-07-23")
    assert result.frame.iloc[-1]["KOSPI"] == 7150


def test_supplement_source_extends_and_overrides_primary_dates():
    config = fake_source_config()
    config["required"]["KOSPI"]["supplements"] = [
        {"provider": "naver", "symbol": "KOSPI", "label": "KOSPI"}
    ]

    def supplementing_fetcher(column, spec, fetch_config, range_value, start, end):
        if column == "KOSPI" and spec.get("provider") == "naver":
            dates = pd.to_datetime(["2024-01-05", "2024-01-08"])
            values = [999, 110]
            provider = "naver"
        else:
            dates = pd.bdate_range("2024-01-01", periods=5)
            values = list(range(100, 105))
            provider = str(spec.get("provider", "yahoo"))
        return SourceFetchResult(
            column=column,
            provider=provider,
            symbol=spec["symbol"],
            label=spec["label"],
            frame=pd.DataFrame({"date": dates, column: values}),
            status="ok",
        )

    df, metadata = fetch_market_data(config, fetcher=supplementing_fetcher)

    assert df["date"].max() == pd.Timestamp("2024-01-08")
    assert df.loc[df["date"] == pd.Timestamp("2024-01-05"), "KOSPI"].item() == 999
    kospi_source = next(item for item in metadata["sources"] if item["column"] == "KOSPI")
    assert kospi_source["provider"] == "yahoo+naver"
    assert kospi_source["status"] == "supplemented"


def test_rebased_supplement_only_appends_after_primary_history():
    config = fake_source_config()
    config["optional"]["VIX"]["supplements"] = [
        {
            "provider": "yahoo",
            "symbol": "VIX-PROXY",
            "label": "VIX proxy",
            "rebase_to_primary": True,
            "append_after_primary": True,
        }
    ]

    def supplementing_fetcher(column, spec, fetch_config, range_value, start, end):
        if column == "VIX" and spec["symbol"] == "^VIX":
            dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
            values = [100.0, 102.0, 104.0]
        elif column == "VIX":
            dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
            values = [10.2, 10.4, 10.6]
        else:
            dates = pd.bdate_range("2024-01-01", periods=4)
            values = list(range(100, 104))
        return SourceFetchResult(
            column=column,
            provider=str(spec.get("provider", "yahoo")),
            symbol=spec["symbol"],
            label=spec["label"],
            frame=pd.DataFrame({"date": dates, column: values}),
            status="ok",
        )

    df, metadata = fetch_market_data(config, fetcher=supplementing_fetcher)

    assert df.loc[df["date"] == pd.Timestamp("2024-01-03"), "VIX"].item() == 104.0
    assert df.loc[df["date"] == pd.Timestamp("2024-01-04"), "VIX"].item() == pytest.approx(106.0)
    source = next(item for item in metadata["sources"] if item["column"] == "VIX")
    assert source["status"] == "supplemented"
    assert source["lastDate"] == "2024-01-04"

def _long_market_frame(end: str = "2026-08-20", periods: int = 1600) -> pd.DataFrame:
    dates = pd.bdate_range(end=end, periods=periods)
    return pd.DataFrame(
        {
            "date": dates,
            "KOSPI": range(2000, 2000 + periods),
            "SPX": range(3000, 3000 + periods),
            "SOX": range(4000, 4000 + periods),
            "USDKRW": range(1000, 1000 + periods),
            "VIX": [20.0] * periods,
        }
    )


def test_market_data_file_is_incrementally_merged(monkeypatch, tmp_path):
    output = tmp_path / "market_data.csv"
    metadata_path = tmp_path / "market_data_sources.json"
    existing = _long_market_frame()
    existing.to_csv(output, index=False)
    observed = {}

    def fake_fetch(_config, range_value=None, start=None, end=None):
        observed.update(range_value=range_value, start=start, end=end)
        fetched = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-08-17", "2026-08-20", "2026-08-21"]),
                "KOSPI": [900.0, 999.0, 1001.0],
                "SPX": [800.0, 899.0, 901.0],
                "SOX": [700.0, 799.0, 801.0],
                "USDKRW": [1300.0, 1400.0, 1401.0],
                "VIX": [18.0, float("nan"), 19.0],
            }
        )
        return fetched, {
            "sources": [
                {
                    "column": "KOSPI",
                    "required": True,
                    "status": "ok",
                    "rows": 3,
                    "coverageRatio": 1.0,
                    "firstDate": "2026-08-17",
                    "lastDate": "2026-08-21",
                }
            ],
            "missingByColumn": {},
        }

    monkeypatch.setattr(
        market_data_fetcher, "load_source_config", lambda _path: fake_source_config()
    )
    monkeypatch.setattr(market_data_fetcher, "fetch_market_data", fake_fetch)

    merged, metadata = market_data_fetcher.fetch_and_save_market_data(
        "unused.yaml", output, metadata_path, min_rows=1500
    )

    assert observed["range_value"] is None
    assert observed["start"] == "2026-08-06"
    assert observed["end"] >= "2026-08-21"
    assert metadata["collectionMode"] == "incremental"
    assert metadata["cachedRows"] == 1600
    assert metadata["fetchedRows"] == 3
    assert metadata["overlapDays"] == 14
    source = metadata["sources"][0]
    assert source["collectionMode"] == "incremental"
    assert source["fetchedRows"] == 3
    assert source["rows"] == 1601
    assert source["firstDate"] == existing.iloc[0]["date"].date().isoformat()
    assert source["lastDate"] == "2026-08-21"
    assert merged.iloc[-1]["date"] == pd.Timestamp("2026-08-21")
    overlap = merged.loc[merged["date"] == pd.Timestamp("2026-08-20")].iloc[0]
    assert overlap["KOSPI"] == 999.0
    assert overlap["VIX"] == 20.0
    assert not list(tmp_path.glob("*.tmp*"))


def test_explicit_market_data_range_bypasses_incremental_cache(monkeypatch, tmp_path):
    output = tmp_path / "market_data.csv"
    metadata_path = tmp_path / "market_data_sources.json"
    _long_market_frame().to_csv(output, index=False)
    observed = {}

    def fake_fetch(_config, range_value=None, start=None, end=None):
        observed.update(range_value=range_value, start=start, end=end)
        fetched = _long_market_frame(end="2026-08-21", periods=5)
        return fetched, {"sources": [], "missingByColumn": {}}

    monkeypatch.setattr(
        market_data_fetcher, "load_source_config", lambda _path: fake_source_config()
    )
    monkeypatch.setattr(market_data_fetcher, "fetch_market_data", fake_fetch)

    frame, metadata = market_data_fetcher.fetch_and_save_market_data(
        "unused.yaml",
        output,
        metadata_path,
        start="2026-08-01",
        end="2026-08-21",
        min_rows=1,
    )

    assert observed == {
        "range_value": None,
        "start": "2026-08-01",
        "end": "2026-08-21",
    }
    assert len(frame) == 5
    assert metadata["collectionMode"] == "explicit-range"
    assert metadata["cachedRows"] == 0
