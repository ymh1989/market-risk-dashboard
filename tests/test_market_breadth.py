from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from kospi_risk.data_loader import load_frame
from kospi_risk.market_breadth import (
    add_market_state_flags,
    calculate_breadth_metrics,
    fetch_kospi_breadth,
    merge_vkospi,
    plot_breadth_dashboard,
    sanity_check_breadth,
    update_breadth_data,
    validate_breadth_quality,
)


def daily_ohlcv(changes: list[float], start_close: int = 10_000) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "종가": [start_close + index * 10 for index in range(len(changes))],
            "등락률": changes,
        },
        index=[f"{index + 1:06d}" for index in range(len(changes))],
    )


def test_fetch_kospi_breadth_skips_holidays_and_continues_after_failure():
    calls: dict[str, int] = {}

    def fetcher(date_key: str) -> pd.DataFrame:
        calls[date_key] = calls.get(date_key, 0) + 1
        if date_key == "20260102":
            return daily_ohlcv([1.2, -0.5, 0.0, np.nan])
        if date_key == "20260105":
            return pd.DataFrame()
        if date_key == "20260106":
            raise RuntimeError("임시 KRX 오류")
        return daily_ohlcv([2.0, 1.0, -1.0, 0.0])

    result = fetch_kospi_breadth(
        "2026-01-02",
        "2026-01-07",
        retries=2,
        sleep_seconds=0,
        ohlcv_fetcher=fetcher,
        sleep_fn=lambda _: None,
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-02", "2026-01-07"]
    assert result.iloc[0][["up", "down", "flat", "total"]].tolist() == [1, 1, 1, 3]
    assert calls["20260106"] == 2
    assert result.attrs["empty_dates"] == ["2026-01-05"]
    assert result.attrs["failed_dates"][0]["date"] == "2026-01-06"


def test_default_source_requires_krx_credentials_before_date_retries(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)

    with np.testing.assert_raises_regex(RuntimeError, "KRX_ID, KRX_PW"):
        fetch_kospi_breadth("2026-01-02", "2026-01-02", sleep_seconds=0)


def test_fetch_kospi_breadth_preserves_large_cap_context_returns():
    universe = pd.DataFrame(
        {"종가": [70_000, 190_000, 12_000], "등락률": [-1.0, -2.0, 1.5]},
        index=["005930", "000660", "000001"],
    )
    result = fetch_kospi_breadth(
        "2026-01-02",
        "2026-01-02",
        sleep_seconds=0,
        ohlcv_fetcher=lambda _: universe,
        sleep_fn=lambda _: None,
    )

    assert result.loc[0, "samsung_return"] == -0.01
    assert result.loc[0, "hynix_return"] == -0.02


def test_calculate_breadth_metrics_handles_zero_division_and_alignment():
    counts = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-02", periods=6, freq="B"),
            "up": [3, 1, 4, 2, 5, 2],
            "down": [0, 3, 1, 2, 0, 4],
            "flat": [2, 1, 0, 1, 1, 0],
            "total": [999] * 6,
        }
    )
    result = calculate_breadth_metrics(counts)

    assert np.isnan(result.loc[0, "ad_ratio"])
    assert result.loc[0, "breadth_pct"] == 1.0
    assert result.loc[0, "up_ratio"] == 1.0
    assert result.loc[0, "down_ratio"] == 0.0
    assert result.loc[0, "total"] == 5
    assert result["AD_line"].tolist() == [3, 1, 4, 4, 9, 7]
    assert np.isnan(result.loc[3, "AD_ma5"])
    assert result.loc[4, "AD_ma5"] == 4.2


def test_merge_vkospi_is_optional_and_never_fabricates_values():
    breadth = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-02", "2026-01-05"]), "breadth_pct": [0.2, -0.1]}
    )
    assert "vkospi" not in merge_vkospi(breadth, None).columns

    vkospi = pd.DataFrame({"date": ["2026-01-02", "2026-01-05"], "VKOSPI": [18.0, 20.0]})
    merged = merge_vkospi(breadth, vkospi)
    assert merged["vkospi"].tolist() == [18.0, 20.0]
    assert np.isnan(merged.loc[0, "vkospi_change"])
    assert merged.loc[1, "vkospi_change"] == 20 / 18 - 1


def test_market_state_flags_identify_panic_narrow_rally_and_sector_rotation():
    frame = pd.DataFrame(
        {
            "kospi_return": [0.01, -0.03, 0.002],
            "breadth_pct": [-0.2, -0.7, 0.25],
            "vkospi": [20.0, 25.0, 22.0],
            "vkospi_change": [-0.05, 0.25, -0.12],
            "samsung_return": [0.01, -0.04, -0.01],
            "hynix_return": [0.02, -0.05, -0.02],
        }
    )
    result = add_market_state_flags(frame)

    assert bool(result.loc[0, "narrow_rally"])
    assert bool(result.loc[1, "panic"])
    assert bool(result.loc[2, "sector_rotation"])


def test_update_breadth_data_only_fetches_dates_after_existing_tail(tmp_path: Path):
    output = tmp_path / "kospi_breadth.parquet"
    metadata = tmp_path / "breadth_quality.json"
    requested_dates: list[str] = []

    def breadth_fetcher(date_key: str) -> pd.DataFrame:
        requested_dates.append(date_key)
        return daily_ohlcv([1, 1, -1, 0, -2])

    def index_fetcher(start_key: str, end_key: str) -> pd.DataFrame:
        dates = pd.bdate_range(pd.Timestamp(start_key), pd.Timestamp(end_key))
        return pd.DataFrame({"종가": np.linspace(2500, 2500 + len(dates) - 1, len(dates))}, index=dates)

    first = update_breadth_data(
        output,
        start_date="2026-01-02",
        end_date="2026-01-05",
        metadata_path=metadata,
        sleep_seconds=0,
        ohlcv_fetcher=breadth_fetcher,
        index_fetcher=index_fetcher,
        sleep_fn=lambda _: None,
    )
    assert len(first) == 2
    requested_dates.clear()

    second = update_breadth_data(
        output,
        start_date="2025-01-01",
        end_date="2026-01-07",
        metadata_path=metadata,
        sleep_seconds=0,
        ohlcv_fetcher=breadth_fetcher,
        index_fetcher=index_fetcher,
        sleep_fn=lambda _: None,
    )

    assert requested_dates == ["20260106", "20260107"]
    assert len(second) == 4
    assert second["date"].is_monotonic_increasing
    assert second["date"].is_unique
    assert load_frame(output).shape[0] == 4
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["lastDate"] == "2026-01-07"
    assert payload["quality"]["status"] == "warning"


def test_update_writes_failure_metadata_when_every_date_fails(tmp_path: Path):
    output = tmp_path / "kospi_breadth.parquet"
    metadata = tmp_path / "breadth_quality.json"

    def failed_fetcher(_: str) -> pd.DataFrame:
        raise RuntimeError("KRX 연결 실패")

    with np.testing.assert_raises(RuntimeError):
        update_breadth_data(
            output,
            start_date="2026-01-02",
            end_date="2026-01-02",
            metadata_path=metadata,
            retries=1,
            sleep_seconds=0,
            ohlcv_fetcher=failed_fetcher,
            sleep_fn=lambda _: None,
        )

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert payload["rows"] == 0
    assert payload["quality"]["status"] == "error"
    assert payload["failedDates"][0]["date"] == "2026-01-02"


def test_quality_sanity_flags_and_plots(tmp_path: Path):
    dates = pd.bdate_range("2026-01-02", periods=30)
    counts = pd.DataFrame(
        {
            "date": dates,
            "up": [520 + (index % 15) for index in range(30)],
            "down": [390 - (index % 10) for index in range(30)],
            "flat": [20] * 30,
            "total": [0] * 30,
        }
    )
    frame = calculate_breadth_metrics(counts)
    frame.insert(1, "kospi_close", np.linspace(2500, 2650, len(frame)))
    frame.insert(2, "kospi_return", frame["kospi_close"].pct_change())
    frame = merge_vkospi(frame, pd.DataFrame({"date": dates, "vkospi": np.linspace(24, 17, len(frame))}))
    frame = add_market_state_flags(frame)

    quality = validate_breadth_quality(frame)
    sanity = sanity_check_breadth(
        frame,
        dates[-1],
        {"up": int(frame.iloc[-1]["up"]), "down": int(frame.iloc[-1]["down"])},
    )
    paths = plot_breadth_dashboard(frame, tmp_path / "figures")

    assert quality["status"] == "ok"
    assert sanity["withinTolerance"] is True
    assert len(paths) == 3
    assert all(path.exists() and path.stat().st_size > 1_000 for path in paths)

def test_incremental_failure_preserves_existing_data_and_marks_warning(tmp_path: Path):
    output = tmp_path / "kospi_breadth.parquet"
    metadata = tmp_path / "breadth_quality.json"

    def index_fetcher(start_key: str, end_key: str) -> pd.DataFrame:
        dates = pd.bdate_range(pd.Timestamp(start_key), pd.Timestamp(end_key))
        return pd.DataFrame({"종가": [2500.0] * len(dates)}, index=dates)

    update_breadth_data(
        output,
        start_date="2026-01-02",
        end_date="2026-01-02",
        metadata_path=metadata,
        sleep_seconds=0,
        ohlcv_fetcher=lambda _: daily_ohlcv([1, -1, 0]),
        index_fetcher=index_fetcher,
        sleep_fn=lambda _: None,
    )

    preserved = update_breadth_data(
        output,
        end_date="2026-01-05",
        metadata_path=metadata,
        retries=1,
        sleep_seconds=0,
        ohlcv_fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("KRX 임시 장애")),
        index_fetcher=index_fetcher,
        sleep_fn=lambda _: None,
    )

    payload = json.loads(metadata.read_text(encoding="utf-8"))
    assert len(preserved) == 1
    assert preserved["date"].max() == pd.Timestamp("2026-01-02")
    assert preserved.attrs["quality"]["status"] == "warning"
    assert payload["quality"]["fetchFailureCount"] == 1
    assert payload["failedDates"][0]["date"] == "2026-01-05"
