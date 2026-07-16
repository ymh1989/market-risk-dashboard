import json
from pathlib import Path

from update_market_risk import (
    FRED_SERIES,
    ROOT,
    TICKERS,
    fetch_yahoo_chart,
    build_timeseries,
    fetch_fred_series_with_fallback,
    fetch_naver_chart,
    NAVER_SYMBOLS,
    clamp,
)


BACKTEST_FILE = ROOT / "data" / "market-risk-backtest.json"


def weighted_dashboard_timeseries(timeseries, indicators):
    indicator_by_id = {indicator["id"]: indicator for indicator in indicators}
    date_scores = {}
    date_weights = {}

    for indicator_id, points in timeseries["series"].items():
        indicator = indicator_by_id.get(indicator_id)
        if not indicator:
            continue
        weight = indicator["weight"]
        for point in points:
            date = point["date"]
            date_scores[date] = date_scores.get(date, 0) + point["value"] * weight
            date_weights[date] = date_weights.get(date, 0) + weight

    return [
        {"date": date, "score": round(date_scores[date] / date_weights[date], 1)}
        for date in sorted(date_scores)
        if date_weights[date] > 0.7
    ]


def forward_max_drawdown_pct(values, start_index, horizon=20):
    start_value = values[start_index]
    if start_value <= 0:
        return 0
    end_index = min(len(values), start_index + horizon + 1)
    future_low = min(values[start_index:end_index])
    return round((future_low / start_value - 1) * 100, 2)


def bucket(score):
    if score >= 75:
        return "경고"
    if score >= 55:
        return "주의"
    if score >= 35:
        return "관심"
    return "정상"


def summarize(samples):
    if not samples:
        return {"count": 0, "avgForwardMaxDrawdownPct": None, "hitRateDrawdownOver5Pct": None}
    drawdowns = [sample["forwardMaxDrawdownPct"] for sample in samples]
    hit_count = sum(1 for value in drawdowns if value <= -5)
    return {
        "count": len(samples),
        "avgForwardMaxDrawdownPct": round(sum(drawdowns) / len(drawdowns), 2),
        "worstForwardMaxDrawdownPct": min(drawdowns),
        "hitRateDrawdownOver5Pct": round(hit_count / len(samples) * 100, 1),
    }


def main():
    dashboard = json.loads((ROOT / "data" / "risk-dashboard.json").read_text(encoding="utf-8"))
    market = next(section for section in dashboard["sections"] if section["id"] == "market")

    series_map = {key: fetch_yahoo_chart(config["symbol"]) for key, config in TICKERS.items()}
    naver_map = {key: fetch_naver_chart(config["symbol"]) for key, config in NAVER_SYMBOLS.items()}
    fred_map = {key: fetch_fred_series_with_fallback(config) for key, config in FRED_SERIES.items()}
    timeseries = {
        "series": build_timeseries(series_map, naver_map, fred_map),
    }
    score_points = weighted_dashboard_timeseries(timeseries, market["indicators"])

    kospi = series_map["kospi"]
    kospi_dates = {point["date"]: index for index, point in enumerate(kospi)}
    kospi_values = [point["close"] for point in kospi]

    samples = []
    for point in score_points:
        index = kospi_dates.get(point["date"])
        if index is None or index + 1 >= len(kospi):
            continue
        samples.append(
            {
                "date": point["date"],
                "score": point["score"],
                "bucket": bucket(point["score"]),
                "forwardMaxDrawdownPct": forward_max_drawdown_pct(kospi_values, index),
            }
        )

    by_bucket = {}
    for sample in samples:
        by_bucket.setdefault(sample["bucket"], []).append(sample)

    result = {
        "generatedAt": dashboard["metadata"]["generatedAt"],
        "target": "KOSPI forward 20 trading-day maximum drawdown",
        "sampleCount": len(samples),
        "overall": summarize(samples),
        "byBucket": {name: summarize(items) for name, items in by_bucket.items()},
        "recentSamples": samples[-20:],
        "notes": [
            "This is a lightweight rolling diagnostic, not a full historical validation.",
            "Scores use the current model over the recent dashboard timeseries window.",
        ],
    }
    BACKTEST_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {BACKTEST_FILE.relative_to(ROOT)}")
    print(json.dumps(result["byBucket"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
