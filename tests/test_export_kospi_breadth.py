from __future__ import annotations

import pandas as pd
import pytest

from scripts.export_kospi_breadth import build_breadth_payload


def sample_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=22)
    up = [500 + index for index in range(22)]
    down = [400 - index for index in range(22)]
    flat = [40] * 22
    frame = pd.DataFrame(
        {
            "date": dates,
            "kospi_close": [3000 + index * 2 for index in range(22)],
            "kospi_return": [0.0] + [0.001] * 21,
            "up": up,
            "down": down,
            "flat": flat,
            "total": [up_value + down_value + 40 for up_value, down_value in zip(up, down)],
            "net_breadth": [up_value - down_value for up_value, down_value in zip(up, down)],
            "ad_ratio": [up_value / down_value for up_value, down_value in zip(up, down)],
            "breadth_pct": [
                (up_value - down_value) / (up_value + down_value)
                for up_value, down_value in zip(up, down)
            ],
        }
    )
    frame["AD_line"] = frame["net_breadth"].cumsum()
    frame["AD_ma5"] = frame["AD_line"].rolling(5).mean()
    frame["AD_ma20"] = frame["AD_line"].rolling(20).mean()
    frame["breadth_ma5"] = frame["breadth_pct"].rolling(5).mean()
    frame["breadth_ma20"] = frame["breadth_pct"].rolling(20).mean()
    return frame


def test_build_payload_preserves_counts_and_eod_source() -> None:
    frame = sample_frame()
    metadata = {
        "generatedAt": "2026-02-02 16:10:00 KST",
        "quality": {
            "status": "ok",
            "lookbackRows": 10,
            "latestTotal": 940,
            "minTotal": 940,
            "maxTotal": 940,
        },
        "vkospiMerged": False,
    }

    payload = build_breadth_payload(frame, metadata)

    assert payload["source"]["provider"] == "KRX"
    assert payload["source"]["frequency"] == "EOD"
    assert payload["source"]["vkospiStatus"] == "not_available"
    assert payload["period"]["observations"] == 22
    assert payload["latest"]["total"] == 940
    assert payload["latest"]["up"] + payload["latest"]["down"] + payload["latest"]["flat"] == 940
    assert payload["latest"]["state"]["id"] == "expansion"
    assert payload["quality"]["status"] == "ok"
    assert len(payload["series"]) == 22


def test_build_payload_rejects_invalid_count_equation() -> None:
    frame = sample_frame()
    frame.loc[0, "total"] += 1

    with pytest.raises(ValueError, match="total"):
        build_breadth_payload(frame)


def test_ad_line_base_date_is_explicit() -> None:
    payload = build_breadth_payload(sample_frame())

    assert payload["period"]["adLineBaseDate"] == payload["period"]["startDate"]
    assert any("시작일에 종속" in item for item in payload["methodology"])


def test_build_payload_exports_direct_krx_flow_in_eok_units() -> None:
    frame = sample_frame()
    frame["foreign_net_buy_value"] = -120_000_000_000
    frame["institution_net_buy_value"] = 80_000_000_000
    frame["financial_investment_net_buy_value"] = 30_000_000_000
    frame["pension_net_buy_value"] = 50_000_000_000
    frame["program_net_buy_value"] = -40_000_000_000
    frame["foreign_net_buy_5d"] = -500_000_000_000
    frame["institution_net_buy_5d"] = 100_000_000_000
    frame["program_net_buy_5d"] = -200_000_000_000
    frame["foreign_sell_pressure"] = 90.0
    frame["institution_sell_pressure"] = 35.0
    frame["program_sell_pressure"] = 80.0
    frame["direct_flow_pressure"] = 72.75

    payload = build_breadth_payload(
        frame,
        {"investorFlowStatus": "available", "programFlowStatus": "available"},
    )

    assert payload["schemaVersion"] == 2
    assert payload["source"]["investorFlowStatus"] == "available"
    assert payload["latest"]["foreignNetBuyEok"] == -1200.0
    assert payload["latest"]["foreignNetBuy5dEok"] == -5000.0
    assert payload["latest"]["directFlowPressure"] == 72.8
    assert payload["series"][-1]["programNetBuy5dEok"] == -2000.0
