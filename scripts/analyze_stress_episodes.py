import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from backtest_market_risk import forward_max_drawdown_pct, weighted_dashboard_timeseries
from update_market_risk import (
    FRED_SERIES,
    KST,
    NAVER_SYMBOLS,
    ROOT,
    TICKERS,
    build_timeseries,
    fetch_fred_series_with_fallback,
    fetch_naver_chart,
    fetch_yahoo_chart,
    load_naver_market_index_cache,
    supplement_domestic_index_series,
)


STRESS_FILE = ROOT / "data" / "market-stress-episodes.json"
HISTORY_CACHE_FILE = ROOT / "data" / "market-history-cache.json"
CACHE_SCHEMA_VERSION = 2
START_DATE = "2020-01-01"
MODEL_SCORE_THRESHOLD = 75
KOSPI_DRAWDOWN_THRESHOLD = 10
MIN_TRADING_DAYS = 2
MAX_TRADING_DAY_GAP = 5
MAX_EPISODES = 10
HISTORICAL_SAMPLE_STEP = 10

NAMED_WINDOWS = [
    ("2020-02-01", "2020-04-30", "코로나19 급락"),
    ("2021-08-01", "2021-10-31", "중국 크레딧·금리 변동성"),
    ("2022-01-01", "2022-12-31", "인플레이션·긴축·강달러"),
    ("2023-03-01", "2023-04-30", "글로벌 은행권 스트레스"),
    ("2023-08-01", "2023-10-31", "미국 장기금리 재상승"),
    ("2024-07-01", "2024-09-30", "AI 반도체 변동성"),
    ("2026-03-01", "2026-06-30", "최근 AI 쏠림·금리/환율 스트레스"),
]


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def pct_change(start, end):
    if not start:
        return 0
    return round((end / start - 1) * 100, 2)


def rolling_high_drawdown_pct(values, index, window=252):
    start = max(0, index - window + 1)
    high = max(values[start : index + 1])
    if high <= 0:
        return 0
    return round((1 - values[index] / high) * 100, 2)


def overlap_days(start_date, end_date, window_start, window_end):
    left = max(parse_date(start_date), parse_date(window_start))
    right = min(parse_date(end_date), parse_date(window_end))
    return max(0, (right - left).days + 1)


def label_episode(start_date, end_date):
    matches = [
        (overlap_days(start_date, end_date, window_start, window_end), label)
        for window_start, window_end, label in NAMED_WINDOWS
    ]
    best_overlap, best_label = max(matches, key=lambda item: item[0])
    return best_label if best_overlap > 0 else f"모델 스트레스 구간 {start_date[:7]}"


def detect_episode_ranges(rows):
    ranges = []
    current = []
    previous_index = None

    for row in rows:
        is_stress = row["score"] >= MODEL_SCORE_THRESHOLD or row["kospiDrawdownFromHighPct"] >= KOSPI_DRAWDOWN_THRESHOLD
        if not is_stress:
            continue

        if current and previous_index is not None and row["rowIndex"] - previous_index > MAX_TRADING_DAY_GAP:
            if len(current) >= MIN_TRADING_DAYS:
                ranges.append(current)
            current = []

        current.append(row)
        previous_index = row["rowIndex"]

    if len(current) >= MIN_TRADING_DAYS:
        ranges.append(current)

    return ranges


def top_contributors_for_date(timeseries, indicators, date, limit=4):
    weights = {indicator["id"]: indicator["weight"] for indicator in indicators}
    weight_total = sum(
        weights[indicator_id]
        for indicator_id, points in timeseries["series"].items()
        if any(point["date"] == date for point in points) and indicator_id in weights
    )
    if weight_total <= 0:
        return []

    indicator_by_id = {indicator["id"]: indicator for indicator in indicators}
    contributors = []
    for indicator_id, points in timeseries["series"].items():
        indicator = indicator_by_id.get(indicator_id)
        if not indicator:
            continue
        point = next((item for item in points if item["date"] == date), None)
        if not point:
            continue
        contribution = point["value"] * indicator["weight"] / weight_total
        contributors.append(
            {
                "id": indicator_id,
                "name": indicator["name"],
                "group": indicator["group"],
                "score": round(point["value"], 1),
                "contribution": round(contribution, 2),
            }
        )

    return sorted(contributors, key=lambda item: item["contribution"], reverse=True)[:limit]


def fetch_source_map(source_name, configs, fetcher, max_workers=8):
    results = {}
    print(f"Fetching {source_name} series: {len(configs)} symbols", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetcher, key, config): key for key, config in configs.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as error:
                print(f"  {source_name}: {key} retry after {error}", flush=True)
                time.sleep(1.5)
                results[key] = fetcher(key, configs[key])
            print(f"  {source_name}: {key} ({len(results)}/{len(configs)})", flush=True)
    return results


def should_refresh_cache():
    return os.environ.get("REFRESH_STRESS_CACHE", "").strip().lower() in {"1", "true", "yes", "y"}


def load_history_cache():
    if should_refresh_cache() or not HISTORY_CACHE_FILE.exists():
        return None

    payload = json.loads(HISTORY_CACHE_FILE.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != CACHE_SCHEMA_VERSION:
        return None
    if set(payload.get("yahoo", {}).keys()) != set(TICKERS.keys()):
        return None
    if set(payload.get("naver", {}).keys()) != set(NAVER_SYMBOLS.keys()):
        return None
    if set(payload.get("fred", {}).keys()) != set(FRED_SERIES.keys()):
        return None

    print(f"Using cached history: {HISTORY_CACHE_FILE.relative_to(ROOT)}", flush=True)
    return payload["yahoo"], payload["naver"], payload["fred"]


def write_history_cache(series_map, naver_map, fred_map):
    payload = {
        "schemaVersion": CACHE_SCHEMA_VERSION,
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "Yahoo Finance, Naver Finance equity/market-index, and FRED endpoints",
        "startDate": START_DATE,
        "yahoo": series_map,
        "naver": naver_map,
        "fred": fred_map,
    }
    HISTORY_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {HISTORY_CACHE_FILE.relative_to(ROOT)}", flush=True)


def load_or_fetch_history():
    cached = load_history_cache()
    if cached:
        return cached

    series_map = fetch_source_map(
        "Yahoo",
        TICKERS,
        lambda _key, config: fetch_yahoo_chart(config["symbol"], range_value="7y"),
    )
    series_map, _ = supplement_domestic_index_series(series_map)
    naver_map = fetch_source_map(
        "Naver",
        NAVER_SYMBOLS,
        lambda _key, config: fetch_naver_chart(config["symbol"], start_date="20200101"),
        max_workers=2,
    )
    fred_map = fetch_source_map(
        "FRED",
        FRED_SERIES,
        lambda _key, config: fetch_fred_series_with_fallback(
            config,
            start_date=parse_date(START_DATE) - timedelta(days=400),
        ),
        max_workers=3,
    )
    write_history_cache(series_map, naver_map, fred_map)
    return series_map, naver_map, fred_map


def summarize_episode(rows, kospi, kospi_dates, kospi_values, timeseries, indicators):
    start_date = rows[0]["date"]
    end_date = rows[-1]["date"]
    peak = max(rows, key=lambda row: row["score"])
    kospi_start = kospi_values[kospi_dates[start_date]]
    kospi_end = kospi_values[kospi_dates[end_date]]
    episode_values = [kospi_values[kospi_dates[row["date"]]] for row in rows if row["date"] in kospi_dates]
    kospi_low = min(episode_values)
    peak_index = kospi_dates[peak["date"]]

    return {
        "id": f"{start_date}_{end_date}",
        "label": label_episode(start_date, end_date),
        "startDate": start_date,
        "endDate": end_date,
        "tradingDays": len(rows),
        "peakDate": peak["date"],
        "peakScore": round(peak["score"], 1),
        "averageScore": round(sum(row["score"] for row in rows) / len(rows), 1),
        "kospiStart": round(kospi_start, 2),
        "kospiEnd": round(kospi_end, 2),
        "kospiLow": round(kospi_low, 2),
        "kospiEpisodeReturnPct": pct_change(kospi_start, kospi_end),
        "kospiMaxDrawdownFromHighPct": round(max(row["kospiDrawdownFromHighPct"] for row in rows), 2),
        "kospiLowFromStartPct": pct_change(kospi_start, kospi_low),
        "forward20dMaxDrawdownFromPeakPct": forward_max_drawdown_pct(kospi_values, peak_index),
        "trigger": "modelScore" if peak["score"] >= MODEL_SCORE_THRESHOLD else "kospiDrawdown",
        "topContributors": top_contributors_for_date(timeseries, indicators, peak["date"]),
    }


def main():
    dashboard = json.loads((ROOT / "data" / "risk-dashboard.json").read_text(encoding="utf-8"))
    market = next(section for section in dashboard["sections"] if section["id"] == "market")

    series_map, naver_map, fred_map = load_or_fetch_history()
    market_index_map = load_naver_market_index_cache()
    print("Building historical indicator scores", flush=True)
    timeseries = {
        "series": build_timeseries(
            series_map,
            naver_map,
            fred_map,
            market_index_map,
            limit=1800,
            step=HISTORICAL_SAMPLE_STEP,
        ),
    }
    score_points = [
        point
        for point in weighted_dashboard_timeseries(timeseries, market["indicators"])
        if point["date"] >= START_DATE
    ]

    kospi = series_map["kospi"]
    kospi_dates = {point["date"]: index for index, point in enumerate(kospi)}
    kospi_values = [point["close"] for point in kospi]

    rows = []
    for row_index, point in enumerate(score_points):
        kospi_index = kospi_dates.get(point["date"])
        if kospi_index is None:
            continue
        rows.append(
            {
                "rowIndex": row_index,
                "date": point["date"],
                "score": point["score"],
                "kospiClose": round(kospi_values[kospi_index], 2),
                "kospiDrawdownFromHighPct": rolling_high_drawdown_pct(kospi_values, kospi_index),
            }
        )

    episodes = [
        summarize_episode(group, kospi, kospi_dates, kospi_values, timeseries, market["indicators"])
        for group in detect_episode_ranges(rows)
    ]
    episodes = [
        episode
        for episode in episodes
        if episode["peakScore"] >= 55 or episode["kospiMaxDrawdownFromHighPct"] >= 8
    ]
    episodes = sorted(episodes, key=lambda episode: episode["startDate"], reverse=True)[:MAX_EPISODES]

    result = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "Yahoo Finance, Naver Finance equity/market-index, and FRED endpoints",
        "historyCache": str(HISTORY_CACHE_FILE.relative_to(ROOT)),
        "methodology": (
            "2020년 이후 동일 시장리스크 모델 점수와 KOSPI 252일 고점대비 낙폭을 함께 평가해 "
            "모델 점수 75 이상 또는 KOSPI 낙폭 10% 이상인 거래일을 주요 스트레스 후보로 묶습니다."
        ),
        "sampleStart": rows[0]["date"] if rows else None,
        "sampleEnd": rows[-1]["date"] if rows else None,
        "sampleCount": len(rows),
        "thresholds": {
            "modelScore": MODEL_SCORE_THRESHOLD,
            "kospiDrawdownFromHighPct": KOSPI_DRAWDOWN_THRESHOLD,
            "minTradingDays": MIN_TRADING_DAYS,
            "sampleStepTradingDays": HISTORICAL_SAMPLE_STEP,
        },
        "episodeCount": len(episodes),
        "episodes": episodes,
        "notes": [
            "에피소드 명칭은 날짜가 겹치는 대표 시장 이벤트를 붙인 보조 라벨입니다.",
            "모든 수치는 공개 chart endpoint 기반 proxy이며 내부 포지션/유동성 데이터는 포함하지 않습니다.",
        ],
    }
    STRESS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {STRESS_FILE.relative_to(ROOT)}")
    print(json.dumps({"episodeCount": result["episodeCount"], "sampleCount": result["sampleCount"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
