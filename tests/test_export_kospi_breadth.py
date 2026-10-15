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


def flow_frame() -> pd.DataFrame:
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
    frame["direct_flow_pressure"] = 68.75
    return frame


def test_build_payload_exports_direct_krx_flow_in_eok_units() -> None:
    frame = flow_frame()

    payload = build_breadth_payload(
        frame,
        {"investorFlowStatus": "available", "programFlowStatus": "available"},
    )

    assert payload["schemaVersion"] == 4
    assert payload["source"]["investorFlowStatus"] == "available"
    assert payload["latest"]["foreignNetBuyEok"] == -1200.0
    assert payload["latest"]["foreignNetBuy5dEok"] == -5000.0
    assert payload["latest"]["directFlowPressure"] == 68.8
    assert payload["series"][-1]["programNetBuy5dEok"] == -2000.0
    assert payload["directFlow"]["status"] == "complete"
    assert payload["directFlow"]["availableWeightPct"] == 100


def test_export_rejects_legacy_program_only_composite_and_labels_last_complete():
    frame = flow_frame()
    frame.loc[frame.index[-1], ["foreign_net_buy_value", "institution_net_buy_value"]] = float("nan")
    frame.loc[frame.index[-1], "direct_flow_pressure"] = 39.6
    payload = build_breadth_payload(frame)
    assert payload["latest"]["directFlowPressure"] is None
    assert payload["latest"]["foreignNetBuy5dEok"] is None
    assert payload["latest"]["foreignSellPressure"] is None
    assert payload["series"][-1]["directFlowPressure"] is None
    assert payload["quality"]["directFlowLatestMissing"] is True
    assert payload["directFlow"]["status"] == "partial"
    assert payload["directFlow"]["availableWeightPct"] == 20
    assert payload["directFlow"]["missingComponents"] == ["외국인", "기관"]
    assert payload["directFlow"]["lastComplete"]["date"] == frame.iloc[-2]["date"].date().isoformat()
    assert payload["directFlow"]["lastComplete"]["directFlowPressure"] == 68.8
    assert all("수급 중립" not in line for line in payload["latest"]["interpretation"])


@pytest.mark.parametrize("prefix", ["foreign", "institution", "program"])
@pytest.mark.parametrize("suffix", ["net_buy_value", "net_buy_5d", "sell_pressure"])
def test_each_missing_flow_input_blocks_latest_score(prefix, suffix):
    frame = flow_frame()
    frame.loc[frame.index[-1], f"{prefix}_{suffix}"] = float("nan")
    payload = build_breadth_payload(frame)
    assert payload["latest"]["directFlowPressure"] is None


def test_zero_flows_and_zero_pressure_are_valid_and_not_missing():
    frame = flow_frame()
    for prefix in ("foreign", "institution", "program"):
        for suffix in ("net_buy_value", "net_buy_5d", "sell_pressure"):
            frame[f"{prefix}_{suffix}"] = 0.0
    payload = build_breadth_payload(frame)
    assert payload["latest"]["directFlowPressure"] == 0
    assert payload["directFlow"]["status"] == "complete"


def test_missing_history_is_distinct_from_missing_daily_flows():
    frame = flow_frame()
    frame["program_sell_pressure"] = float("nan")
    payload = build_breadth_payload(frame)
    assert payload["directFlow"]["status"] == "insufficient_history"
    assert payload["directFlow"]["lastComplete"] is None
    assert payload["quality"]["directFlowLatestMissing"] is False


def test_build_payload_exports_vkospi_source_and_latest_value() -> None:
    frame = sample_frame()
    frame["vkospi"] = [20.0 + index * 0.1 for index in range(len(frame))]
    frame["vkospi_change"] = frame["vkospi"].pct_change()
    payload = build_breadth_payload(
        frame,
        {
            "vkospiMerged": True,
            "vkospiProvider": "Stockplus",
            "vkospiSource": "증권플러스 공개 시세",
            "vkospiSecurityId": "KOREA-O2901P",
            "vkospiSourceUrl": "https://www.stockplus.com/m/stocks/KOREA-O2901P",
            "vkospiLatestDate": frame.iloc[-1]["date"].date().isoformat(),
            "vkospiObservations": 22,
            "vkospiValueStatus": "eod",
            "vkospiQualityStatus": "ok",
        },
    )

    assert payload["source"]["vkospiStatus"] == "merged"
    assert payload["source"]["vkospiProvider"] == "Stockplus"
    assert payload["source"]["vkospiSecurityId"] == "KOREA-O2901P"
    assert payload["latest"]["vkospi"] == 22.1
    assert payload["series"][-1]["vkospi"] == 22.1
    assert any("VKOSPI 22.10" in item for item in payload["latest"]["interpretation"])
