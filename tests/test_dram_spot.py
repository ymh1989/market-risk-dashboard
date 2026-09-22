from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.update_market_risk import dram_spot_price_indicator
from kospi_risk.dram_spot import (
    PRODUCTS,
    build_payload,
    calculate_cycle_scores,
    normalize_history,
    parse_public_history_html,
    parse_trendforce_spot_html,
)


KST = timezone(timedelta(hours=9))


def official_html() -> str:
    rows = []
    values = {
        "DDR5_16Gb": (56.933, 1.67),
        "DDR4_16Gb": (85.0, -0.88),
        "DDR4_8Gb": (46.036, 0.0),
        "DDR3_4Gb": (13.78, 0.0),
    }
    for key, config in PRODUCTS.items():
        average, change = values[key]
        rows.append(
            f'<tr><td>{config["trendforceLabel"]}</td>'
            '<td class="lcd-num-l">1</td><td class="lcd-num-l">1</td>'
            '<td class="lcd-num-l">1</td><td class="lcd-num-l">1</td>'
            f'<td class="lcd-num-l">{average}</td>'
            f'<td class="percent-cell"><span>{change} %</span></td></tr>'
        )
    return '<p>Last Update 2026-09-21 18:10 (GMT+8)</p>' + "".join(rows)


def synthetic_rows(periods: int = 90) -> list[dict]:
    start = datetime(2025, 9, 22, tzinfo=KST)
    rows = []
    observed = start
    index = 0
    while index < periods:
        if observed.weekday() < 5:
            rows.append(
                {
                    "date": observed.date().isoformat(),
                    "pricesUsd": {
                        key: round((5 + product_index * 3) * (1 + index * 0.006), 4)
                        for product_index, key in enumerate(PRODUCTS)
                    },
                    "source": "test",
                }
            )
            index += 1
        observed += timedelta(days=1)
    return rows


def test_parse_trendforce_public_table_extracts_session_averages():
    parsed = parse_trendforce_spot_html(official_html())

    assert parsed["date"] == "2026-09-21"
    assert parsed["pricesUsd"]["DDR5_16Gb"] == pytest.approx(56.933)
    assert parsed["pricesUsd"]["DDR4_16Gb"] == pytest.approx(85.0)
    assert parsed["sessionChangesPct"]["DDR4_16Gb"] == pytest.approx(-0.88)


def test_parse_public_history_and_drop_weekend_rows():
    raw = [
        {"date": observed_date, "chip_type": key, "price_usd": 10 + index}
        for observed_date in ("2025-09-22", "2025-09-27")
        for index, key in enumerate(PRODUCTS)
    ]
    document = '<script>self.__next_f.push([1,"\\"dramHistoryAll\\":' + str(raw).replace("'", '\\"') + ',\\"next\\":1"])</script>'
    document = document.replace("None", "null")

    parsed = parse_public_history_html(document)
    normalized = normalize_history(parsed)

    assert len(parsed) == 8
    assert [row["date"] for row in normalized] == ["2025-09-22"]


def test_cycle_score_is_causal_and_bounded():
    base_rows = synthetic_rows(80)
    base = calculate_cycle_scores(base_rows)
    future_rows = base_rows + [
        {
            "date": "2026-02-02",
            "pricesUsd": {key: 9999.0 for key in PRODUCTS},
            "source": "future",
        }
    ]
    extended = calculate_cycle_scores(future_rows)

    assert extended[: len(base)] == base
    assert all(0 <= row["score"] <= 100 for row in extended)


def test_payload_starts_after_2025_and_is_observation_ready():
    payload = build_payload(
        synthetic_rows(),
        generated_at=datetime(2026, 9, 22, 10, 0, tzinfo=KST),
    )

    assert payload["historyStart"] >= "2025-01-01"
    assert payload["history"][0]["date"] == payload["historyStart"]
    assert len(payload["history"]) > len(payload["series"])
    assert payload["latest"]["score"] <= 100
    assert len(payload["latest"]["products"]) == 4
    assert payload["methodology"]["operatingRole"].endswith("가중치 0")


def test_dashboard_indicator_is_observation_only():
    payload = build_payload(
        synthetic_rows(),
        generated_at=datetime(2026, 9, 22, 10, 0, tzinfo=KST),
    )

    indicator = dram_spot_price_indicator(payload)

    assert indicator["id"] == "dram_spot_cycle_watch"
    assert indicator["role"] == "observation"
    assert indicator["weight"] == 0
    assert indicator["group"] == "ai_semi"
    assert indicator["asOf"] == payload["latest"]["date"]
    assert "DRAM" in indicator["source"]
