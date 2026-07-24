import json

from update_market_risk import (
    NAVER_DOMESTIC_INDEXES,
    ROOT,
    TICKERS,
    fetch_naver_chart,
    fetch_yahoo_chart,
)


BACKTEST_FILE = ROOT / "data" / "market-risk-backtest.json"
TIMESERIES_FILE = ROOT / "data" / "market-risk-timeseries.json"


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


def fetch_kospi_with_supplement():
    yahoo = fetch_yahoo_chart(TICKERS["kospi"]["symbol"])
    try:
        naver = fetch_naver_chart(NAVER_DOMESTIC_INDEXES["kospi"]["symbol"])
    except Exception as exc:
        print(f"Naver KOSPI 보강 조회 실패로 Yahoo 데이터만 사용합니다: {exc}")
        return yahoo
    merged = {point["date"]: point for point in yahoo}
    merged.update({point["date"]: point for point in naver})
    return [merged[date] for date in sorted(merged)]


def main():
    dashboard = json.loads((ROOT / "data" / "risk-dashboard.json").read_text(encoding="utf-8"))
    market = next(section for section in dashboard["sections"] if section["id"] == "market")

    timeseries = json.loads(TIMESERIES_FILE.read_text(encoding="utf-8"))
    score_points = weighted_dashboard_timeseries(timeseries, market["indicators"])

    kospi = fetch_kospi_with_supplement()
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
