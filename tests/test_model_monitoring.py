from __future__ import annotations

from datetime import date, timedelta

from kospi_risk.model_monitoring import build_model_monitoring, enrich_walk_forward_results


def synthetic_rows(days: int = 140) -> tuple[list[dict], list[dict]]:
    start = date(2026, 1, 2)
    prices = []
    value = 100.0
    for index in range(days + 6):
        if index in {55, 95, 125}:
            value *= 0.92
        else:
            value *= 1.001
        prices.append({"date": (start + timedelta(days=index)).isoformat(), "kospi": value})

    rows = []
    for index in range(5, days):
        event_soon = any(crash in range(index + 1, index + 6) for crash in {55, 95, 125})
        rows.append(
            {
                "date": prices[index]["date"],
                "resultKnownThroughDate": prices[index + 5]["date"],
                "crash5d5pctProbabilityPct": 80.0 if event_soon else 15.0,
                "crash5d10pctProbabilityPct": 20.0 if event_soon else 2.0,
                "fold": index // 30,
            }
        )
    return rows, prices


def test_monitoring_enrichment_uses_only_prices_after_prediction_for_target():
    rows, prices = synthetic_rows()
    rows[0]["baselineCrash5d5pctProbabilityPct"] = None
    rows[0]["targetCrash5d5pct"] = None
    enriched = enrich_walk_forward_results(rows, prices)
    assert enriched[0]["baselineCrash5d5pctProbabilityPct"] is not None
    event_row = next(row for row in enriched if row["targetCrash5d5pct"] == 1)
    assert event_row["forwardMinReturn5dPct"] <= -5
    assert 0 <= event_row["baselineCrash5d5pctProbabilityPct"] <= 80


def test_monitoring_reports_rolling_metrics_cases_and_calibration():
    rows, prices = synthetic_rows()
    payload = build_model_monitoring(rows, prices)
    task = payload["tasks"]["crash5d5pct"]

    assert task["metrics"]["3m"]["observations"] > 30
    assert task["metrics"]["6m"]["eventCount"] == 15
    assert task["metrics"]["6m"]["auc"] > 0.9
    assert sum(bucket["observations"] for bucket in task["calibrationBuckets"]) == len(rows)
    assert task["cases"]["hits"] == 15
    assert task["cases"]["misses"] == 0
    assert payload["generatedFrom"] == "walk-forward OOS predictions only"
    assert "series" not in payload
