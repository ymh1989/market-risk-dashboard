from __future__ import annotations

import pandas as pd
import pytest

from kospi_risk.market_data_fetcher import SourceFetchResult, fetch_market_data


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
