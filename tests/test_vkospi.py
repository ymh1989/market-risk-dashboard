from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from kospi_risk.vkospi import (
    fetch_stockplus_vkospi,
    parse_stockplus_day_candles,
    update_vkospi_data,
)


def candle(date: str, value: float, change: float = 0.0) -> dict[str, object]:
    return {
        "date": f"{date}T00:00:00.000+00:00",
        "tradePrice": value,
        "changePrice": change,
        "changePriceRate": change / max(value - change, 1),
        "change": "RISE" if change > 0 else "FALL",
    }


def test_parse_stockplus_candles_sorts_deduplicates_and_rejects_invalid_rows():
    payload = {
        "dayCandles": [
            candle("2026-09-09", 49.23, 1.89),
            candle("2026-09-08", 47.34, 4.67),
            candle("2026-09-09", 49.25, 1.91),
            {"date": "invalid", "tradePrice": None},
        ]
    }

    frame = parse_stockplus_day_candles(payload)

    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-09-08", "2026-09-09"]
    assert frame["vkospi"].tolist() == [47.34, 49.25]


def test_fetch_stockplus_vkospi_paginates_exclusively_without_duplicates():
    calls: list[str] = []

    def fetcher(to_date: str, limit: int):
        calls.append(to_date)
        assert limit == 2
        if to_date == "2026-09-11":
            return {"dayCandles": [candle("2026-09-10", 48.45), candle("2026-09-09", 49.23)]}
        if to_date == "2026-09-09":
            return {"dayCandles": [candle("2026-09-08", 47.34), candle("2026-09-07", 42.67)]}
        raise AssertionError(f"예상하지 못한 커서: {to_date}")

    frame = fetch_stockplus_vkospi(
        "2026-09-08",
        "2026-09-10",
        page_limit=2,
        sleep_seconds=0,
        page_fetcher=fetcher,
        sleep_fn=lambda _: None,
    )

    assert calls == ["2026-09-11", "2026-09-09"]
    assert frame["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
    ]


def test_update_vkospi_uses_overlap_and_preserves_cache_on_source_failure(tmp_path: Path):
    output = tmp_path / "vkospi.csv"
    metadata = tmp_path / "vkospi.metadata.json"
    pd.DataFrame(
        {
            "date": ["2026-09-08", "2026-09-09"],
            "vkospi": [47.34, 49.23],
            "change_price": [4.67, 1.89],
            "change_rate": [0.10, 0.04],
            "change": ["RISE", "RISE"],
        }
    ).to_csv(output, index=False)

    failed = update_vkospi_data(
        output,
        metadata_path=metadata,
        start_date="2026-09-01",
        end_date="2026-09-10",
        overlap_days=3,
        retries=1,
        sleep_seconds=0,
        page_fetcher=lambda *_: (_ for _ in ()).throw(RuntimeError("일시 장애")),
        sleep_fn=lambda _: None,
    )
    payload = json.loads(metadata.read_text(encoding="utf-8"))

    assert len(failed) == 2
    assert payload["update"]["mode"] == "cache-fallback"
    assert payload["update"]["requestedStartDate"] == "2026-09-06"
    assert payload["quality"]["status"] == "warning"
    assert "일시 장애" in payload["quality"]["sourceError"]


def test_fetch_stockplus_vkospi_rejects_missing_contract():
    with pytest.raises(RuntimeError, match="dayCandles"):
        fetch_stockplus_vkospi(
            "2026-09-08",
            "2026-09-10",
            retries=1,
            sleep_seconds=0,
            page_fetcher=lambda *_: {"unexpected": []},
            sleep_fn=lambda _: None,
        )
