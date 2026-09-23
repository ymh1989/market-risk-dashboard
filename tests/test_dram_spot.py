from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from io import StringIO

import pytest

from scripts.update_dram_spot_prices import main, update
from scripts.update_market_risk import dram_spot_price_indicator
from kospi_risk.dram_spot import (
    PRODUCTS,
    DramSpotDataError,
    build_payload,
    calculate_cycle_scores,
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
    assert payload["sources"]["officialLatest"]["displayLabel"] == "TrendForce 공식 최신값"
    assert set(payload["sources"]) == {"officialLatest"}
    assert payload["qualityChecks"]["officialObservationCount"] == 90
    assert payload["qualityChecks"]["scoreStatus"] == "ready"


def test_payload_waits_for_official_history_before_scoring():
    payload = build_payload(
        synthetic_rows(1),
        generated_at=datetime(2026, 9, 22, 10, 0, tzinfo=KST),
    )

    assert payload["latest"]["score"] is None
    assert payload["series"] == []
    assert payload["scoreStart"] is None
    assert payload["qualityChecks"]["scoreStatus"] == "building-official-history"
    assert payload["qualityChecks"]["minimumScoreObservations"] == 21


def test_updater_removes_non_trendforce_history(tmp_path):
    output = tmp_path / "dram.json"
    public_row = synthetic_rows(1)[0]
    public_row["source"] = "public-history"
    official_row = {
        **synthetic_rows(1)[0],
        "date": "2026-09-21",
        "source": "trendforce-official",
    }
    output.write_text(
        json.dumps({"history": [public_row, official_row]}, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = update(output, official_html=official_html())

    assert len(payload["history"]) == 1
    assert payload["history"][0]["date"] == "2026-09-21"
    assert payload["history"][0]["source"] == "trendforce-official"
    assert set(payload["sources"]) == {"officialLatest"}


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


def test_price_history_is_chart_only_and_official_overlap_wins():
    official = parse_trendforce_spot_html(official_html())
    archive = synthetic_rows(90)
    stale_overlap = {**archive[0], "date": official["date"]}
    payload = build_payload(
        [official],
        generated_at=datetime(2026, 9, 22, tzinfo=KST),
        price_history=[stale_overlap, *reversed(archive), archive[0]],
    )

    assert payload["history"] == [official]
    assert payload["priceHistory"][-1]["pricesUsd"] == official["pricesUsd"]
    assert len(payload["priceHistory"]) == 91
    dates = [row["date"] for row in payload["priceHistory"]]
    assert dates == sorted(set(dates))
    assert payload["qualityChecks"]["officialObservationCount"] == 1
    assert payload["qualityChecks"]["priceObservationCount"] == 91
    assert payload["series"] == []
    assert payload["latest"]["score"] is None
    assert set(payload["sources"]) == {"officialLatest"}


def test_price_history_does_not_change_existing_scores():
    rows = synthetic_rows(90)
    generated_at = datetime(2026, 9, 22, tzinfo=KST)
    original = build_payload(rows, generated_at=generated_at)
    extended = build_payload(
        rows,
        generated_at=generated_at,
        price_history=[{
            "date": "2025-01-02",
            "pricesUsd": {key: 999999 for key in PRODUCTS},
            "source": "public-history",
        }],
    )

    assert extended["series"] == original["series"]
    assert extended["latest"] == original["latest"]
    assert extended["history"] == original["history"]
    assert len(extended["priceHistory"]) == 91


def test_price_history_excludes_future_and_before_2025():
    official = parse_trendforce_spot_html(official_html())
    payload = build_payload(
        [official],
        generated_at=datetime(2026, 9, 22, tzinfo=KST),
        price_history=[
            {**official, "date": "2024-12-31"},
            {**official, "date": "2026-09-22"},
        ],
    )

    assert [row["date"] for row in payload["priceHistory"]] == [official["date"]]
    assert payload["priceHistory"][0]["pricesUsd"] == official["pricesUsd"]


def test_updater_preserves_imported_history_between_runs(tmp_path):
    output = tmp_path / "dram.json"
    archive = {
        "history": synthetic_rows(90),
        "sources": {"archive": {"provider": "보관 원천"}},
        "generatedAt": "2026-09-21T19:00:00+09:00",
    }
    first = update(output, official_html=official_html(), price_history_payload=archive)
    second = update(output, official_html=official_html())

    assert second["priceHistory"] == first["priceHistory"]
    assert second["history"] == first["history"]
    assert second["priceHistoryProvenance"]["sources"] == archive["sources"]
    assert second["priceHistoryProvenance"]["usage"] == "price-chart-only"
    assert set(second["sources"]) == {"officialLatest"}
    assert len(second["priceHistory"]) == 91

    fallback = update(output, official_html="일시적인 원천 조회 실패")
    assert fallback["priceHistory"] == second["priceHistory"]
    assert fallback["sources"]["officialLatest"]["status"] == "stored-fallback"


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), 0, -1, None])
def test_invalid_price_history_does_not_overwrite_existing_file(tmp_path, bad_price):
    output = tmp_path / "dram.json"
    update(output, official_html=official_html())
    before = output.read_bytes()
    row = synthetic_rows(1)[0]
    row["pricesUsd"]["DDR5_16Gb"] = bad_price

    with pytest.raises(DramSpotDataError, match="가격"):
        update(output, official_html=official_html(), price_history_payload={"history": [row]})

    assert output.read_bytes() == before


def test_cli_imports_price_history_from_stdin(tmp_path, monkeypatch):
    output = tmp_path / "dram.json"
    html = tmp_path / "official.html"
    html.write_text(official_html(), encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "update_dram_spot_prices.py", "--output", str(output),
        "--official-html", str(html), "--import-price-history", "-",
    ])
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps({"history": synthetic_rows(90)})))

    main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["priceHistory"]) == 91
    assert len(payload["history"]) == 1
