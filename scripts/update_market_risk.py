import json
import math
import statistics
import ast
import csv
import io
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_FILE = ROOT / "data" / "risk-dashboard.json"
SNAPSHOT_FILE = ROOT / "data" / "market-risk-snapshot.json"
TIMESERIES_FILE = ROOT / "data" / "market-risk-timeseries.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_value}&interval=1d"
FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-risk-dashboard/0.1)"
KST = timezone(timedelta(hours=9))

TICKERS = {
    "kospi": {"symbol": "^KS11", "label": "KOSPI"},
    "kosdaq": {"symbol": "^KQ11", "label": "KOSDAQ"},
    "usdkrw": {"symbol": "KRW=X", "label": "USD/KRW"},
    "vix": {"symbol": "^VIX", "label": "VIX"},
    "us10y": {"symbol": "^TNX", "label": "US 10Y"},
    "kodex200": {"symbol": "069500.KS", "label": "KODEX 200"},
    "kodex_leverage": {"symbol": "122630.KS", "label": "KODEX Leverage"},
    "sox": {"symbol": "^SOX", "label": "Philadelphia Semiconductor Index"},
    "nvda": {"symbol": "NVDA", "label": "NVIDIA"},
    "tsm": {"symbol": "TSM", "label": "TSMC ADR"},
    "avgo": {"symbol": "AVGO", "label": "Broadcom"},
    "amd": {"symbol": "AMD", "label": "AMD"},
    "mu": {"symbol": "MU", "label": "Micron"},
    "asml": {"symbol": "ASML", "label": "ASML ADR"},
    "aapl": {"symbol": "AAPL", "label": "Apple"},
    "msft": {"symbol": "MSFT", "label": "Microsoft"},
    "googl": {"symbol": "GOOGL", "label": "Alphabet"},
    "meta": {"symbol": "META", "label": "Meta Platforms"},
    "amzn": {"symbol": "AMZN", "label": "Amazon"},
    "samsung": {"symbol": "005930.KS", "label": "Samsung Electronics"},
    "hynix": {"symbol": "000660.KS", "label": "SK hynix"},
    "hanmi": {"symbol": "042700.KS", "label": "Hanmi Semiconductor"},
    "dbhitek": {"symbol": "000990.KS", "label": "DB HiTek"},
    "leeno": {"symbol": "058470.KQ", "label": "Leeno Industrial"},
    "hyg": {"symbol": "HYG", "label": "iShares High Yield Corporate Bond ETF"},
    "lqd": {"symbol": "LQD", "label": "iShares Investment Grade Corporate Bond ETF"},
    "eem": {"symbol": "EEM", "label": "iShares MSCI Emerging Markets ETF"},
}

NAVER_SYMBOLS = {
    "samsung": {"symbol": "005930", "label": "Samsung Electronics"},
    "hynix": {"symbol": "000660", "label": "SK hynix"},
    "hanmi": {"symbol": "042700", "label": "Hanmi Semiconductor"},
    "dbhitek": {"symbol": "000990", "label": "DB HiTek"},
    "leeno": {"symbol": "058470", "label": "Leeno Industrial"},
    "kodex200": {"symbol": "069500", "label": "KODEX 200"},
    "kodex_leverage": {"symbol": "122630", "label": "KODEX Leverage"},
}

FRED_SERIES = {
    "us2y": {"series_id": "DGS2", "local_column": "US2Y", "label": "US 2Y Treasury"},
    "us_yield_curve_10y2y": {
        "series_id": "T10Y2Y",
        "local_column": "US_YIELD_CURVE_10Y2Y",
        "label": "US 10Y-2Y Treasury Spread",
    },
    "us_high_yield_oas": {
        "series_id": "BAMLH0A0HYM2",
        "local_column": "US_HIGH_YIELD_OAS",
        "label": "US High Yield OAS",
    },
    "us_financial_stress_stlfsi": {
        "series_id": "STLFSI4",
        "local_column": "US_FINANCIAL_STRESS_STLFSI",
        "label": "St. Louis Fed Financial Stress Index",
    },
    "us_financial_conditions_nfci": {
        "series_id": "NFCI",
        "local_column": "US_FINANCIAL_CONDITIONS_NFCI",
        "label": "Chicago Fed National Financial Conditions Index",
    },
}

RISK_GROUPS = {
    "crash": {"label": "Crash Stress", "weight": 0.18},
    "overheating": {"label": "Overheating", "weight": 0.09},
    "liquidity": {"label": "Liquidity", "weight": 0.07},
    "flow": {"label": "Flow", "weight": 0.1},
    "macro": {"label": "Macro", "weight": 0.3},
    "ai_semi": {"label": "AI Semi", "weight": 0.26},
}


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def round_score(value):
    return round(clamp(value), 1)


def fetch_yahoo_chart(symbol, range_value="2y"):
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    request = urllib.request.Request(
        YAHOO_CHART_URL.format(symbol=encoded_symbol, range_value=range_value),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"{symbol}: {chart['error']}")

    result = chart["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    series = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        volume = volumes[index] if index < len(volumes) else None
        series.append(
            {
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                "close": float(close),
                "volume": int(volume) if isinstance(volume, (int, float)) else None,
            }
        )

    if len(series) < 80:
        raise RuntimeError(f"{symbol}: not enough observations ({len(series)})")
    return series


def fetch_naver_chart(symbol, lookback_days=760, start_date=None, end_date=None):
    end_date = end_date or datetime.now(KST).strftime("%Y%m%d")
    start_date = start_date or (datetime.now(KST) - timedelta(days=lookback_days)).strftime("%Y%m%d")
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "requestType": 1,
            "startTime": start_date,
            "endTime": end_date,
            "timeframe": "day",
        }
    )
    request = urllib.request.Request(
        f"https://api.finance.naver.com/siseJson.naver?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8", errors="ignore").strip()

    rows = ast.literal_eval(text)
    series = []
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) < 7:
            continue
        date = datetime.strptime(str(row[0]), "%Y%m%d").date().isoformat()
        series.append(
            {
                "date": date,
                "close": float(row[4]),
                "volume": int(row[5]),
                "foreignOwnership": float(row[6]) if row[6] is not None else None,
            }
        )

    if len(series) < 80:
        raise RuntimeError(f"{symbol}: not enough Naver observations ({len(series)})")
    return series


def fetch_fred_series(series_id, lookback_days=1100, start_date=None, end_date=None):
    end_date = end_date or datetime.now(KST).date()
    start_date = start_date or (end_date - timedelta(days=lookback_days))
    params = urllib.parse.urlencode(
        {
            "id": series_id,
            "cosd": start_date.isoformat(),
            "coed": end_date.isoformat(),
        }
    )
    url = f"{FRED_GRAPH_URL}?{params}"
    curl_result = subprocess.run(
        ["curl", "-fsSL", "--max-time", "12", "-A", USER_AGENT, url],
        check=False,
        capture_output=True,
        text=True,
    )
    if curl_result.returncode == 0 and curl_result.stdout.strip():
        text = curl_result.stdout
    else:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            text = response.read().decode("utf-8", errors="ignore")

    series = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_value = row.get(series_id) or row.get("VALUE") or row.get("value")
        if raw_value in (None, "", "."):
            continue
        try:
            close = float(raw_value)
        except ValueError:
            continue
        series.append(
            {
                "date": row["DATE"],
                "close": close,
                "volume": None,
            }
        )

    if len(series) < 80:
        raise RuntimeError(f"{series_id}: not enough FRED observations ({len(series)})")
    return series


def load_local_fred_series(column):
    market_data_file = ROOT / "data" / "raw" / "market_data.csv"
    if not market_data_file.exists():
        raise RuntimeError(f"{market_data_file.relative_to(ROOT)} 파일이 없어 FRED fallback을 사용할 수 없습니다.")

    series = []
    with market_data_file.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            raw_value = row.get(column)
            if raw_value in (None, "", "."):
                continue
            try:
                close = float(raw_value)
            except ValueError:
                continue
            series.append({"date": row["date"], "close": close, "volume": None})

    if len(series) < 80:
        raise RuntimeError(f"{column}: not enough local FRED observations ({len(series)})")
    return series


def _is_recent_fred_fallback(series, max_age_days=7):
    try:
        last_date = datetime.strptime(series[-1]["date"], "%Y-%m-%d").date()
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    return (datetime.now(KST).date() - last_date).days <= max_age_days


def fetch_fred_series_with_fallback(config):
    local_error = None
    try:
        local_series = load_local_fred_series(config["local_column"])
        if _is_recent_fred_fallback(local_series):
            return local_series
    except Exception as exc:
        local_error = exc

    try:
        return fetch_fred_series(config["series_id"])
    except Exception as exc:
        if local_error is not None:
            raise RuntimeError(
                f"FRED 직접 조회와 로컬 fallback이 모두 실패했습니다: {config['series_id']} / {config['local_column']}"
            ) from exc
        print(
            f"FRED 직접 조회 실패: {config['series_id']} ({exc}). "
            f"data/raw/market_data.csv의 {config['local_column']} 컬럼을 사용합니다."
        )
        return load_local_fred_series(config["local_column"])


def closes(series):
    return [point["close"] for point in series]


def pct_change(values, periods):
    if len(values) <= periods or values[-periods - 1] == 0:
        return 0.0
    return values[-1] / values[-periods - 1] - 1


def pct_change_at(values, periods, end_index):
    start_index = end_index - periods
    if start_index < 0 or values[start_index] == 0:
        return None
    return values[end_index] / values[start_index] - 1


def percentile_rank(sample, value):
    clean = [item for item in sample if item is not None and math.isfinite(item)]
    if not clean:
        return 50.0
    count = sum(1 for item in clean if item <= value)
    return 100 * count / len(clean)


def z_score(sample, value):
    clean = [item for item in sample if item is not None and math.isfinite(item)]
    if len(clean) < 2:
        return 0.0

    mean = statistics.fmean(clean)
    stdev = statistics.pstdev(clean)
    if stdev == 0:
        return 0.0
    return (value - mean) / stdev


def robust_z_score(sample, value):
    clean = sorted(item for item in sample if item is not None and math.isfinite(item))
    if len(clean) < 3:
        return 0.0

    median = statistics.median(clean)
    absolute_deviations = [abs(item - median) for item in clean]
    mad = statistics.median(absolute_deviations)
    if mad == 0:
        return 0.0
    return 0.6745 * (value - median) / mad


def z_score_to_percentile(z_value):
    return clamp(50 * (1 + math.erf(z_value / math.sqrt(2))))


def hybrid_standard_score(sample, value):
    percentile_score = percentile_rank(sample, value)
    z_percentile_score = z_score_to_percentile(z_score(sample, value))
    robust_z_percentile_score = z_score_to_percentile(robust_z_score(sample, value))
    return round_score(percentile_score * 0.4 + z_percentile_score * 0.3 + robust_z_percentile_score * 0.3)


def rolling_drawdowns(values, window=252):
    drawdowns = []
    for index, value in enumerate(values):
        start = max(0, index - window + 1)
        high = max(values[start : index + 1])
        drawdowns.append(0.0 if high == 0 else (1 - value / high) * 100)
    return drawdowns


def rolling_realized_vol(values, window=20):
    vols = []
    for index in range(len(values)):
        if index < window:
            vols.append(None)
            continue
        returns = []
        for point in range(index - window + 1, index + 1):
            previous = values[point - 1]
            if previous:
                returns.append(values[point] / previous - 1)
        vols.append(statistics.pstdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None)
    return vols


def rolling_positive_changes(values, periods=20):
    changes = []
    for index in range(len(values)):
        change = pct_change_at(values, periods, index)
        changes.append(max(0.0, change * 100) if change is not None else None)
    return changes


def rolling_negative_changes(values, periods=20):
    changes = []
    for index in range(len(values)):
        change = pct_change_at(values, periods, index)
        changes.append(max(0.0, -change * 100) if change is not None else None)
    return changes


def rolling_negative_point_changes(values, periods=20):
    changes = []
    for index, value in enumerate(values):
        if index < periods:
            changes.append(None)
            continue
        changes.append(max(0.0, values[index - periods] - value))
    return changes


def rolling_positive_point_changes(values, periods=20):
    changes = []
    for index, value in enumerate(values):
        if index < periods:
            changes.append(None)
            continue
        changes.append(max(0.0, value - values[index - periods]))
    return changes


def value_at(values, offset=0):
    index = len(values) - 1 - offset
    return values[max(0, index)]


def score_from_metrics(metrics):
    return round_score(sum(value * weight for value, weight in metrics))


def trend_from_scores(current, prior):
    if current - prior > 3:
        return "up"
    if prior - current > 3:
        return "down"
    return "flat"


def equity_stress_score(series):
    values = closes(series)
    drawdowns = rolling_drawdowns(values)
    vols = rolling_realized_vol(values)
    negative_changes = rolling_negative_changes(values)

    drawdown_score = hybrid_standard_score(drawdowns[-252:], drawdowns[-1])
    vol_score = hybrid_standard_score([value for value in vols[-252:] if value is not None], vols[-1])
    momentum_score = hybrid_standard_score([value for value in negative_changes[-252:] if value is not None], negative_changes[-1])
    score = score_from_metrics([(drawdown_score, 0.4), (vol_score, 0.35), (momentum_score, 0.25)])

    prior_offset = min(20, len(values) - 2)
    prior_score = score_from_metrics(
        [
            (hybrid_standard_score(drawdowns[-252:], value_at(drawdowns, prior_offset)), 0.4),
            (hybrid_standard_score([value for value in vols[-252:] if value is not None], value_at(vols, prior_offset)), 0.35),
            (
                hybrid_standard_score(
                    [value for value in negative_changes[-252:] if value is not None],
                    value_at(negative_changes, prior_offset),
                ),
                0.25,
            ),
        ]
    )

    return {
        "score": score,
        "trend": trend_from_scores(score, prior_score),
        "metrics": {
            "last": values[-1],
            "drawdownPct": round(drawdowns[-1], 2),
            "return20dPct": round(pct_change(values, 20) * 100, 2),
            "realizedVol20dPct": round(vols[-1], 2),
        },
    }


def equity_stress_score_at(series, end_index):
    if end_index < 60:
        return None

    values = closes(series[: end_index + 1])
    drawdowns = rolling_drawdowns(values)
    vols = rolling_realized_vol(values)
    negative_changes = rolling_negative_changes(values)

    if vols[-1] is None or negative_changes[-1] is None:
        return None

    return score_from_metrics(
        [
            (hybrid_standard_score(drawdowns[-252:], drawdowns[-1]), 0.4),
            (hybrid_standard_score([value for value in vols[-252:] if value is not None], vols[-1]), 0.35),
            (
                hybrid_standard_score(
                    [value for value in negative_changes[-252:] if value is not None],
                    negative_changes[-1],
                ),
                0.25,
            ),
        ]
    )


def equity_stress_score_from_components(drawdowns, vols, negative_changes, end_index):
    if end_index < 60 or vols[end_index] is None or negative_changes[end_index] is None:
        return None

    start_index = max(0, end_index - 251)
    return score_from_metrics(
        [
            (hybrid_standard_score(drawdowns[start_index : end_index + 1], drawdowns[end_index]), 0.4),
            (
                hybrid_standard_score(
                    [value for value in vols[start_index : end_index + 1] if value is not None],
                    vols[end_index],
                ),
                0.35,
            ),
            (
                hybrid_standard_score(
                    [value for value in negative_changes[start_index : end_index + 1] if value is not None],
                    negative_changes[end_index],
                ),
                0.25,
            ),
        ]
    )


def level_and_change_score(series, change_periods=20):
    values = closes(series)
    positive_changes = rolling_positive_changes(values, change_periods)
    level_score = hybrid_standard_score(values[-504:], values[-1])
    change_score = hybrid_standard_score([value for value in positive_changes[-252:] if value is not None], positive_changes[-1])
    score = score_from_metrics([(level_score, 0.55), (change_score, 0.45)])

    prior_offset = min(change_periods, len(values) - 2)
    prior_score = score_from_metrics(
        [
            (hybrid_standard_score(values[-504:], value_at(values, prior_offset)), 0.55),
            (
                hybrid_standard_score(
                    [value for value in positive_changes[-252:] if value is not None],
                    value_at(positive_changes, prior_offset),
                ),
                0.45,
            ),
        ]
    )

    return {
        "score": score,
        "trend": trend_from_scores(score, prior_score),
        "metrics": {
            "last": values[-1],
            "return20dPct": round(pct_change(values, change_periods) * 100, 2),
            "levelPercentile": round(level_score, 1),
        },
    }


def level_and_change_score_at(series, end_index, change_periods=20):
    if end_index < 60:
        return None

    values = closes(series[: end_index + 1])
    positive_changes = rolling_positive_changes(values, change_periods)
    if positive_changes[-1] is None:
        return None

    return score_from_metrics(
        [
            (hybrid_standard_score(values[-504:], values[-1]), 0.55),
            (
                hybrid_standard_score(
                    [value for value in positive_changes[-252:] if value is not None],
                    positive_changes[-1],
                ),
                0.45,
            ),
        ]
    )


def level_and_point_change_score(series, change_periods=20, direction="up"):
    values = closes(series)
    if direction not in ("up", "down"):
        raise ValueError(f"지원하지 않는 direction입니다: {direction}")

    risk_levels = values if direction == "up" else [-value for value in values]
    point_changes = (
        rolling_positive_point_changes(values, change_periods)
        if direction == "up"
        else rolling_negative_point_changes(values, change_periods)
    )
    level_score = hybrid_standard_score(risk_levels[-504:], risk_levels[-1])
    change_score = hybrid_standard_score([value for value in point_changes[-252:] if value is not None], point_changes[-1])
    score = score_from_metrics([(level_score, 0.55), (change_score, 0.45)])

    prior_offset = min(change_periods, len(values) - 2)
    prior_score = score_from_metrics(
        [
            (hybrid_standard_score(risk_levels[-504:], value_at(risk_levels, prior_offset)), 0.55),
            (
                hybrid_standard_score(
                    [value for value in point_changes[-252:] if value is not None],
                    value_at(point_changes, prior_offset),
                ),
                0.45,
            ),
        ]
    )

    point_change = values[-1] - values[-change_periods - 1] if len(values) > change_periods else 0.0
    return {
        "score": score,
        "trend": trend_from_scores(score, prior_score),
        "metrics": {
            "last": values[-1],
            "changePoints": round(point_change, 3),
            "riskLevelPercentile": round(level_score, 1),
            "changePeriods": change_periods,
        },
    }


def level_and_point_change_score_at(series, end_index, change_periods=20, direction="up"):
    if end_index < max(60, change_periods):
        return None

    values = closes(series[: end_index + 1])
    if direction not in ("up", "down"):
        raise ValueError(f"지원하지 않는 direction입니다: {direction}")

    risk_levels = values if direction == "up" else [-value for value in values]
    point_changes = (
        rolling_positive_point_changes(values, change_periods)
        if direction == "up"
        else rolling_negative_point_changes(values, change_periods)
    )
    if point_changes[-1] is None:
        return None

    return score_from_metrics(
        [
            (hybrid_standard_score(risk_levels[-504:], risk_levels[-1]), 0.55),
            (
                hybrid_standard_score(
                    [value for value in point_changes[-252:] if value is not None],
                    point_changes[-1],
                ),
                0.45,
            ),
        ]
    )


def make_ratio_series(numerator_series, denominator_series):
    numerator_by_date = {point["date"]: point["close"] for point in numerator_series}
    denominator_by_date = {point["date"]: point["close"] for point in denominator_series}
    dates = sorted(set(numerator_by_date).intersection(denominator_by_date))
    return [
        {
            "date": date,
            "close": numerator_by_date[date] / denominator_by_date[date],
            "volume": None,
        }
        for date in dates
        if denominator_by_date[date] != 0
    ]


def rolling_average(values, window):
    averages = []
    for index, _ in enumerate(values):
        start = max(0, index - window + 1)
        sample = values[start : index + 1]
        averages.append(statistics.fmean(sample) if sample else None)
    return averages


def volume_pressure_score(series):
    volumes = [point["volume"] for point in series if point.get("volume") is not None]
    averages = rolling_average(volumes, 60)
    pressure = []
    for volume, average in zip(volumes, averages):
        if average is None or average <= 0:
            pressure.append(None)
        else:
            pressure.append(abs(volume / average - 1) * 100)

    score = hybrid_standard_score([value for value in pressure[-252:] if value is not None], pressure[-1])
    prior_offset = min(20, len(pressure) - 2)
    prior_score = hybrid_standard_score(
        [value for value in pressure[-252:] if value is not None],
        value_at(pressure, prior_offset),
    )
    return {
        "score": score,
        "trend": trend_from_scores(score, prior_score),
        "metrics": {
            "lastVolume": volumes[-1],
            "volumeVs60dAvgPct": round((volumes[-1] / averages[-1] - 1) * 100, 2),
        },
    }


def volume_pressure_score_at(series, end_index):
    if end_index < 60:
        return None
    volumes = [point["volume"] for point in series[: end_index + 1] if point.get("volume") is not None]
    averages = rolling_average(volumes, 60)
    pressure = [
        abs(volume / average - 1) * 100 if average is not None and average > 0 else None
        for volume, average in zip(volumes, averages)
    ]
    if pressure[-1] is None:
        return None
    return hybrid_standard_score([value for value in pressure[-252:] if value is not None], pressure[-1])


def basket_volume_pressure_score(naver_map, keys):
    scored = [volume_pressure_score(naver_map[key]) for key in keys]
    score = round_score(sum(item["score"] for item in scored) / len(scored))
    prior_proxy = round_score(
        sum(item["score"] - (4 if item["trend"] == "down" else -4 if item["trend"] == "up" else 0) for item in scored)
        / len(scored)
    )
    return {
        "score": score,
        "trend": trend_from_scores(score, prior_proxy),
        "metrics": {
            "maxVolumeVs60dAvgPct": max(item["metrics"]["volumeVs60dAvgPct"] for item in scored),
            "avgVolumeVs60dAvgPct": round(statistics.fmean(item["metrics"]["volumeVs60dAvgPct"] for item in scored), 2),
        },
    }


def basket_volume_timeseries(naver_map, keys, limit=120, step=1):
    indexes = {key: index_by_date(naver_map[key]) for key in keys}
    points = []
    for date in sampled_recent(common_dates(naver_map, keys), limit, step):
        scores = [volume_pressure_score_at(naver_map[key], indexes[key][date]) for key in keys]
        if any(score is None for score in scores):
            continue
        points.append({"date": date, "value": round_score(sum(scores) / len(scores))})
    return points


def foreign_ownership_pressure_score(naver_map, keys):
    scored = []
    details = []
    for key in keys:
        series = naver_map[key]
        ownership = [point["foreignOwnership"] for point in series if point.get("foreignOwnership") is not None]
        drops = rolling_negative_point_changes(ownership, 20)
        score = hybrid_standard_score([value for value in drops[-252:] if value is not None], drops[-1])
        prior_offset = min(20, len(drops) - 2)
        prior_score = hybrid_standard_score([value for value in drops[-252:] if value is not None], value_at(drops, prior_offset))
        scored.append({"score": score, "trend": trend_from_scores(score, prior_score)})
        details.append(
            {
                "key": key,
                "last": ownership[-1],
                "change20d": round(ownership[-1] - ownership[-21], 2) if len(ownership) > 21 else 0,
            }
        )

    score = round_score(sum(item["score"] for item in scored) / len(scored))
    prior_proxy = round_score(
        sum(item["score"] - (4 if item["trend"] == "down" else -4 if item["trend"] == "up" else 0) for item in scored)
        / len(scored)
    )
    return {
        "score": score,
        "trend": trend_from_scores(score, prior_proxy),
        "metrics": {
            "details": details,
            "max20dDropPctp": abs(min(item["change20d"] for item in details)),
        },
    }


def foreign_ownership_pressure_score_at(naver_map, keys, date):
    indexes = {key: index_by_date(naver_map[key]) for key in keys}
    scores = []
    for key in keys:
        index = indexes[key][date]
        if index < 60:
            return None
        ownership = [
            point["foreignOwnership"]
            for point in naver_map[key][: index + 1]
            if point.get("foreignOwnership") is not None
        ]
        drops = rolling_negative_point_changes(ownership, 20)
        if drops[-1] is None:
            return None
        scores.append(hybrid_standard_score([value for value in drops[-252:] if value is not None], drops[-1]))
    return round_score(sum(scores) / len(scores))


def foreign_ownership_timeseries(naver_map, keys, limit=120, step=1):
    points = []
    for date in sampled_recent(common_dates(naver_map, keys), limit, step):
        score = foreign_ownership_pressure_score_at(naver_map, keys, date)
        if score is None:
            continue
        points.append({"date": date, "value": score})
    return points


def semiconductor_global_score(series_map):
    asset_keys = ("sox", "nvda", "tsm", "avgo", "amd", "mu", "asml")
    scored_assets = [equity_stress_score(series_map[key]) for key in asset_keys]
    score = round_score(sum(item["score"] for item in scored_assets) / len(scored_assets))
    prior_proxy = round_score(
        sum(item["score"] - (4 if item["trend"] == "down" else -4 if item["trend"] == "up" else 0) for item in scored_assets)
        / len(scored_assets)
    )
    sox_values = closes(series_map["sox"])

    return {
        "score": score,
        "trend": trend_from_scores(score, prior_proxy),
        "metrics": {
            "soxLast": sox_values[-1],
            "soxReturn20dPct": round(pct_change(sox_values, 20) * 100, 2),
            "nvdaScore": scored_assets[1]["score"],
            "tsmScore": scored_assets[2]["score"],
            "basketSize": len(asset_keys),
        },
    }


def single_name_semiconductor_leverage_points(series_map):
    keys = ("samsung", "hynix")
    all_keys = ("kospi", *keys)
    indexes = {key: index_by_date(series_map[key]) for key in all_keys}
    values = {key: closes(series_map[key]) for key in all_keys}
    vols = {key: rolling_realized_vol(values[key]) for key in all_keys}
    stress_components = {
        key: {
            "drawdowns": rolling_drawdowns(values[key]),
            "negative_changes": rolling_negative_changes(values[key]),
        }
        for key in keys
    }
    ratio_history = {key: [] for key in keys}
    points = []

    for date in common_dates(series_map, all_keys):
        kospi_index = indexes["kospi"][date]
        stock_indexes = {key: indexes[key][date] for key in keys}
        if min([kospi_index, *stock_indexes.values()]) < 60:
            continue

        stock_scores = [
            equity_stress_score_from_components(
                stress_components[key]["drawdowns"],
                vols[key],
                stress_components[key]["negative_changes"],
                stock_indexes[key],
            )
            for key in keys
        ]
        if any(score is None for score in stock_scores):
            continue

        current_ratios = {}
        ratio_scores = []
        for key in keys:
            stock_vol = vols[key][stock_indexes[key]]
            kospi_vol = vols["kospi"][kospi_index]
            if stock_vol is None or kospi_vol is None or kospi_vol <= 0:
                continue
            ratio = stock_vol / kospi_vol
            ratio_history[key].append(ratio)
            current_ratios[key] = ratio
            ratio_scores.append(hybrid_standard_score(ratio_history[key][-252:], ratio))

        if len(ratio_scores) != len(keys):
            continue

        points.append(
            {
                "date": date,
                "value": score_from_metrics(
                    [
                        (statistics.fmean(stock_scores), 0.45),
                        (max(stock_scores), 0.25),
                        (statistics.fmean(ratio_scores), 0.3),
                    ]
                ),
                "metrics": {
                    "samsungLast": values["samsung"][stock_indexes["samsung"]],
                    "hynixLast": values["hynix"][stock_indexes["hynix"]],
                    "samsungReturn20dPct": round(
                        (pct_change_at(values["samsung"], 20, stock_indexes["samsung"]) or 0) * 100,
                        2,
                    ),
                    "hynixReturn20dPct": round(
                        (pct_change_at(values["hynix"], 20, stock_indexes["hynix"]) or 0) * 100,
                        2,
                    ),
                    "samsungVolRatioToKospi": round(current_ratios["samsung"], 2),
                    "hynixVolRatioToKospi": round(current_ratios["hynix"], 2),
                    "samsungStressScore": stock_scores[0],
                    "hynixStressScore": stock_scores[1],
                },
            }
        )

    return points


def single_name_semiconductor_leverage_timeseries(series_map, limit=120, step=1):
    sampled_dates = set(sampled_recent(common_dates(series_map, ("kospi", "samsung", "hynix")), limit, step))
    return [
        {"date": point["date"], "value": point["value"]}
        for point in single_name_semiconductor_leverage_points(series_map)
        if point["date"] in sampled_dates
    ]


def single_name_semiconductor_leverage_score(series_map):
    points = single_name_semiconductor_leverage_points(series_map)
    if not points:
        raise RuntimeError("삼성전자·SK하이닉스 단일종목 레버리지 점수를 계산할 공통 날짜가 없습니다.")

    latest = points[-1]
    prior = points[max(0, len(points) - 21)]

    return {
        "score": latest["value"],
        "trend": trend_from_scores(latest["value"], prior["value"]),
        "metrics": latest["metrics"],
    }


def bigtech_ai_demand_pressure_points(series_map):
    demand_keys = ("aapl", "msft", "googl", "meta", "amzn")
    memory_keys = ("samsung", "hynix", "mu")
    all_keys = (*demand_keys, *memory_keys)
    indexes = {key: index_by_date(series_map[key]) for key in all_keys}
    values = {key: closes(series_map[key]) for key in all_keys}
    vols = {key: rolling_realized_vol(values[key]) for key in demand_keys}
    stress_components = {
        key: {
            "drawdowns": rolling_drawdowns(values[key]),
            "negative_changes": rolling_negative_changes(values[key]),
        }
        for key in demand_keys
    }
    gap_history = []
    points = []

    for date in common_dates(series_map, all_keys):
        demand_indexes = {key: indexes[key][date] for key in demand_keys}
        memory_indexes = {key: indexes[key][date] for key in memory_keys}
        if min([*demand_indexes.values(), *memory_indexes.values()]) < 60:
            continue

        demand_scores = [
            equity_stress_score_from_components(
                stress_components[key]["drawdowns"],
                vols[key],
                stress_components[key]["negative_changes"],
                demand_indexes[key],
            )
            for key in demand_keys
        ]
        if any(score is None for score in demand_scores):
            continue

        demand_returns = [
            (pct_change_at(values[key], 20, demand_indexes[key]) or 0) * 100
            for key in demand_keys
        ]
        memory_returns = [
            (pct_change_at(values[key], 20, memory_indexes[key]) or 0) * 100
            for key in memory_keys
        ]
        demand_return = statistics.fmean(demand_returns)
        memory_return = statistics.fmean(memory_returns)
        cost_pressure_gap = memory_return - demand_return
        positive_gap = max(0.0, cost_pressure_gap)
        gap_history.append(positive_gap)
        gap_score = hybrid_standard_score(gap_history[-252:], positive_gap)

        points.append(
            {
                "date": date,
                "value": score_from_metrics(
                    [
                        (statistics.fmean(demand_scores), 0.45),
                        (demand_scores[0], 0.25),
                        (gap_score, 0.3),
                    ]
                ),
                "metrics": {
                    "appleLast": values["aapl"][demand_indexes["aapl"]],
                    "appleReturn20dPct": round(demand_returns[0], 2),
                    "bigtechReturn20dPct": round(demand_return, 2),
                    "memorySupplierReturn20dPct": round(memory_return, 2),
                    "costPressureGapPct": round(cost_pressure_gap, 2),
                    "appleStressScore": demand_scores[0],
                    "gapScore": gap_score,
                },
            }
        )

    return points


def bigtech_ai_demand_pressure_timeseries(series_map, limit=120, step=1):
    sampled_dates = set(
        sampled_recent(
            common_dates(series_map, ("aapl", "msft", "googl", "meta", "amzn", "samsung", "hynix", "mu")),
            limit,
            step,
        )
    )
    return [
        {"date": point["date"], "value": point["value"]}
        for point in bigtech_ai_demand_pressure_points(series_map)
        if point["date"] in sampled_dates
    ]


def bigtech_ai_demand_pressure_score(series_map):
    points = bigtech_ai_demand_pressure_points(series_map)
    if not points:
        raise RuntimeError("빅테크 AI 수요 우려 점수를 계산할 공통 날짜가 없습니다.")

    latest = points[-1]
    prior = points[max(0, len(points) - 21)]
    return {
        "score": latest["value"],
        "trend": trend_from_scores(latest["value"], prior["value"]),
        "metrics": latest["metrics"],
    }


def _component_score_points(series, score_at_fn):
    points = []
    for index, point in enumerate(series):
        score = score_at_fn(series, index)
        if score is None:
            continue
        points.append({"date": point["date"], "value": score})
    return points


def _asof_score(points, date):
    latest = None
    for point in points:
        if point["date"] <= date:
            latest = point["value"]
        else:
            break
    return latest


def _weighted_asof_score_points(component_points, limit=120, step=1):
    dates = sorted({point["date"] for config in component_points.values() for point in config["points"]})
    points = []
    for date in sampled_recent(dates, limit, step):
        weighted_sum = 0.0
        total_weight = 0.0
        for config in component_points.values():
            score = _asof_score(config["points"], date)
            if score is None:
                total_weight = 0.0
                break
            weighted_sum += score * config["weight"]
            total_weight += config["weight"]
        if total_weight <= 0:
            continue
        points.append({"date": date, "value": round_score(weighted_sum / total_weight)})
    return points


def us_credit_spread_score(fred_map):
    score = level_and_point_change_score(fred_map["us_high_yield_oas"], change_periods=20, direction="up")
    return {
        "score": score["score"],
        "trend": score["trend"],
        "metrics": {
            "highYieldOasLast": score["metrics"]["last"],
            "highYieldOasChange20Obs": score["metrics"]["changePoints"],
            "highYieldOasRiskPercentile": score["metrics"]["riskLevelPercentile"],
        },
    }


def us_credit_spread_score_at(fred_map, date):
    indexes = index_by_date(fred_map["us_high_yield_oas"])
    eligible_dates = [item for item in indexes if item <= date]
    if not eligible_dates:
        return None
    return level_and_point_change_score_at(
        fred_map["us_high_yield_oas"],
        indexes[max(eligible_dates)],
        change_periods=20,
        direction="up",
    )


def us_credit_spread_timeseries(fred_map, limit=120, step=1):
    return single_indicator_timeseries(
        fred_map["us_high_yield_oas"],
        lambda series, index: level_and_point_change_score_at(series, index, change_periods=20, direction="up"),
        limit=limit,
        step=step,
    )


def us_financial_conditions_component_points(fred_map, limit=120, step=1):
    component_points = {
        "stlfsi": {
            "weight": 0.3,
            "points": _component_score_points(
                fred_map["us_financial_stress_stlfsi"],
                lambda series, index: level_and_point_change_score_at(series, index, change_periods=4, direction="up"),
            ),
        },
        "nfci": {
            "weight": 0.3,
            "points": _component_score_points(
                fred_map["us_financial_conditions_nfci"],
                lambda series, index: level_and_point_change_score_at(series, index, change_periods=4, direction="up"),
            ),
        },
        "us2y": {
            "weight": 0.2,
            "points": _component_score_points(
                fred_map["us2y"],
                lambda series, index: level_and_point_change_score_at(series, index, change_periods=20, direction="up"),
            ),
        },
        "curve": {
            "weight": 0.2,
            "points": _component_score_points(
                fred_map["us_yield_curve_10y2y"],
                lambda series, index: level_and_point_change_score_at(series, index, change_periods=20, direction="down"),
            ),
        },
    }
    return _weighted_asof_score_points(component_points, limit=limit, step=step)


def us_financial_conditions_score(fred_map):
    points = us_financial_conditions_component_points(fred_map, limit=504, step=1)
    if not points:
        raise RuntimeError("미국 금융여건 긴축 압력 점수를 계산할 FRED 공통 데이터가 없습니다.")

    latest = points[-1]
    prior = points[max(0, len(points) - 21)]
    stlfsi = level_and_point_change_score(fred_map["us_financial_stress_stlfsi"], change_periods=4, direction="up")
    nfci = level_and_point_change_score(fred_map["us_financial_conditions_nfci"], change_periods=4, direction="up")
    us2y = level_and_point_change_score(fred_map["us2y"], change_periods=20, direction="up")
    curve = level_and_point_change_score(fred_map["us_yield_curve_10y2y"], change_periods=20, direction="down")

    return {
        "score": latest["value"],
        "trend": trend_from_scores(latest["value"], prior["value"]),
        "metrics": {
            "stlfsiLast": stlfsi["metrics"]["last"],
            "stlfsiChange4Obs": stlfsi["metrics"]["changePoints"],
            "nfciLast": nfci["metrics"]["last"],
            "nfciChange4Obs": nfci["metrics"]["changePoints"],
            "us2yLast": us2y["metrics"]["last"],
            "us2yChange20Obs": us2y["metrics"]["changePoints"],
            "curveLast": curve["metrics"]["last"],
            "curveChange20Obs": curve["metrics"]["changePoints"],
        },
    }


def index_by_date(series):
    return {point["date"]: index for index, point in enumerate(series)}


def common_dates(series_map, keys):
    date_sets = [{point["date"] for point in series_map[key]} for key in keys]
    return sorted(set.intersection(*date_sets))


def sampled_recent(values, limit, step=1):
    recent = values[-limit:]
    if step <= 1 or len(recent) <= 1:
        return recent
    sampled = []
    for value in recent:
        try:
            ordinal = datetime.strptime(value, "%Y-%m-%d").date().toordinal()
        except (TypeError, ValueError):
            ordinal = len(sampled)
        if ordinal % step == 0:
            sampled.append(value)
    if not sampled:
        sampled = [recent[-1]]
    elif sampled[-1] != recent[-1]:
        sampled.append(recent[-1])
    return sampled


def single_indicator_timeseries(series, score_fn, limit=120, step=1):
    points = []
    start_index = max(0, len(series) - limit)
    indexes = []
    for index in range(start_index, len(series)):
        if step <= 1:
            indexes.append(index)
            continue
        ordinal = datetime.strptime(series[index]["date"], "%Y-%m-%d").date().toordinal()
        if ordinal % step == 0:
            indexes.append(index)
    if indexes and indexes[-1] != len(series) - 1:
        indexes.append(len(series) - 1)
    if not indexes and series:
        indexes.append(len(series) - 1)
    for index in indexes:
        point = series[index]
        score = score_fn(series, index)
        if score is None:
            continue
        points.append({"date": point["date"], "value": score})
    return points


def global_ai_timeseries(series_map, limit=120, step=1):
    keys = ("sox", "nvda", "tsm", "avgo", "amd", "mu", "asml")
    indexes = {key: index_by_date(series_map[key]) for key in keys}
    points = []
    for date in sampled_recent(common_dates(series_map, keys), limit, step):
        scores = [equity_stress_score_at(series_map[key], indexes[key][date]) for key in keys]
        if any(score is None for score in scores):
            continue
        points.append({"date": date, "value": round_score(sum(scores) / len(scores))})
    return points


def korea_ai_timeseries(series_map, limit=120, step=1):
    keys = ("kospi", "samsung", "hynix", "hanmi", "dbhitek", "leeno")
    indexes = {key: index_by_date(series_map[key]) for key in keys}
    points = []

    for date in sampled_recent(common_dates(series_map, keys), limit, step):
        kospi_index = indexes["kospi"][date]
        asset_indexes = [indexes[key][date] for key in keys if key != "kospi"]
        if min([kospi_index, *asset_indexes]) < 60:
            continue

        asset_scores = [equity_stress_score_at(series_map[key], indexes[key][date]) for key in keys if key != "kospi"]
        if any(score is None for score in asset_scores):
            continue

        kospi_values = closes(series_map["kospi"][: kospi_index + 1])
        asset_returns = [
            pct_change(closes(series_map[key][: indexes[key][date] + 1]), 60) * 100
            for key in keys
            if key != "kospi"
        ]
        kospi_60d = pct_change(kospi_values, 60) * 100
        crowding_score = clamp(abs(statistics.fmean(asset_returns) - kospi_60d) / 30 * 100)

        points.append(
            {
                "date": date,
                "value": score_from_metrics([(statistics.fmean(asset_scores), 0.7), (crowding_score, 0.3)]),
            }
        )
    return points


def build_timeseries(series_map, naver_map, fred_map, limit=120, step=1):
    return {
        "kospi_price_stress": single_indicator_timeseries(
            series_map["kospi"], equity_stress_score_at, limit=limit, step=step
        ),
        "kosdaq_growth_stress": single_indicator_timeseries(
            series_map["kosdaq"], equity_stress_score_at, limit=limit, step=step
        ),
        "usdkrw_fx_pressure": single_indicator_timeseries(
            series_map["usdkrw"], level_and_change_score_at, limit=limit, step=step
        ),
        "global_volatility_pressure": single_indicator_timeseries(
            series_map["vix"], level_and_change_score_at, limit=limit, step=step
        ),
        "rates_pressure": single_indicator_timeseries(
            series_map["us10y"], level_and_change_score_at, limit=limit, step=step
        ),
        "us_credit_spread_stress": us_credit_spread_timeseries(fred_map, limit=limit, step=step),
        "us_financial_conditions_stress": us_financial_conditions_component_points(
            fred_map, limit=limit, step=step
        ),
        "global_ai_semiconductor_stress": global_ai_timeseries(series_map, limit=limit, step=step),
        "bigtech_ai_demand_pressure": bigtech_ai_demand_pressure_timeseries(
            series_map, limit=limit, step=step
        ),
        "korea_ai_semiconductor_concentration": korea_ai_timeseries(series_map, limit=limit, step=step),
        "foreign_ownership_pressure": foreign_ownership_timeseries(
            naver_map, ("samsung", "hynix", "hanmi"), limit=limit, step=step
        ),
        "trading_activity_heat": basket_volume_timeseries(
            naver_map,
            ("samsung", "hynix", "hanmi", "kodex200", "kodex_leverage"),
            limit=limit,
            step=step,
        ),
        "single_name_semiconductor_leverage": single_name_semiconductor_leverage_timeseries(
            series_map, limit=limit, step=step
        ),
        "global_credit_proxy_stress": single_indicator_timeseries(
            make_ratio_series(series_map["hyg"], series_map["lqd"]),
            equity_stress_score_at,
            limit=limit,
            step=step,
        ),
        "emerging_market_stress": single_indicator_timeseries(
            series_map["eem"], equity_stress_score_at, limit=limit, step=step
        ),
    }


def korean_ai_semiconductor_score(series_map):
    asset_keys = ("samsung", "hynix", "hanmi", "dbhitek", "leeno")
    asset_scores = [equity_stress_score(series_map[key]) for key in asset_keys]
    kospi_values = closes(series_map["kospi"])
    asset_values = {key: closes(series_map[key]) for key in asset_keys}

    kospi_60d = pct_change(kospi_values, 60) * 100
    asset_60d = [pct_change(values, 60) * 100 for values in asset_values.values()]
    semi_relative_60d = statistics.fmean(asset_60d) - kospi_60d
    crowding_score = clamp(abs(semi_relative_60d) / 30 * 100)
    average_asset_score = statistics.fmean(item["score"] for item in asset_scores)
    score = score_from_metrics([(average_asset_score, 0.7), (crowding_score, 0.3)])
    prior_proxy = score_from_metrics(
        [
            (
                statistics.fmean(
                    item["score"] - (4 if item["trend"] == "down" else -4 if item["trend"] == "up" else 0)
                    for item in asset_scores
                ),
                0.7,
            ),
            (crowding_score, 0.3),
        ]
    )

    return {
        "score": score,
        "trend": trend_from_scores(score, prior_proxy),
        "metrics": {
            "samsungLast": asset_values["samsung"][-1],
            "hynixLast": asset_values["hynix"][-1],
            "basketSize": len(asset_keys),
            "kospiReturn60dPct": round(kospi_60d, 2),
            "semiRelative60dPct": round(semi_relative_60d, 2),
            "crowdingScore": round(crowding_score, 1),
        },
    }


def fmt_number(value):
    return f"{value:,.2f}"


def fmt_pct(value):
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def enrich_indicators(indicators):
    total_weight = sum(indicator["weight"] for indicator in indicators)
    if total_weight <= 0:
        return indicators, []

    enriched = []
    for indicator in indicators:
        contribution = indicator["value"] * indicator["weight"] / total_weight
        enriched.append(
            {
                **indicator,
                "contribution": round(contribution, 2),
                "contributionPct": round(indicator["weight"] / total_weight * 100, 1),
            }
        )

    group_scores = []
    for group_id, config in RISK_GROUPS.items():
        members = [indicator for indicator in enriched if indicator["group"] == group_id]
        if not members:
            continue
        group_weight = sum(indicator["weight"] for indicator in members)
        group_score = sum(indicator["value"] * indicator["weight"] for indicator in members) / group_weight
        group_contribution = sum(indicator["contribution"] for indicator in members)
        group_scores.append(
            {
                "id": group_id,
                "label": config["label"],
                "score": round_score(group_score),
                "weight": round(group_weight / total_weight, 3),
                "contribution": round(group_contribution, 2),
                "indicatorCount": len(members),
            }
        )

    return enriched, sorted(group_scores, key=lambda group: group["contribution"], reverse=True)


def build_indicators(series_map, naver_map, fred_map):
    kospi = equity_stress_score(series_map["kospi"])
    kosdaq = equity_stress_score(series_map["kosdaq"])
    usdkrw = level_and_change_score(series_map["usdkrw"])
    vix = level_and_change_score(series_map["vix"])
    us10y = level_and_change_score(series_map["us10y"])
    us_credit_spread = us_credit_spread_score(fred_map)
    us_financial_conditions = us_financial_conditions_score(fred_map)
    global_ai = semiconductor_global_score(series_map)
    bigtech_demand = bigtech_ai_demand_pressure_score(series_map)
    korea_ai = korean_ai_semiconductor_score(series_map)
    foreign_ownership = foreign_ownership_pressure_score(naver_map, ("samsung", "hynix", "hanmi"))
    trading_activity = basket_volume_pressure_score(
        naver_map,
        ("samsung", "hynix", "hanmi", "kodex200", "kodex_leverage"),
    )
    single_name_leverage = single_name_semiconductor_leverage_score(series_map)
    global_credit = equity_stress_score(make_ratio_series(series_map["hyg"], series_map["lqd"]))
    emerging_market = equity_stress_score(series_map["eem"])

    return [
        {
            "id": "kospi_price_stress",
            "name": "KOSPI 가격 스트레스",
            "category": "한국주식",
            "group": "crash",
            "value": kospi["score"],
            "unit": "score",
            "weight": 0.11,
            "trend": kospi["trend"],
            "detail": (
                f"KOSPI {fmt_number(kospi['metrics']['last'])}, 20일 수익률 {fmt_pct(kospi['metrics']['return20dPct'])}, "
                f"20일 변동성 {fmt_pct(kospi['metrics']['realizedVol20dPct'])}, 고점대비 -{kospi['metrics']['drawdownPct']:.2f}%"
            ),
            "source": "Yahoo Finance chart: ^KS11",
        },
        {
            "id": "kosdaq_growth_stress",
            "name": "KOSDAQ 성장주 스트레스",
            "category": "한국주식",
            "group": "crash",
            "value": kosdaq["score"],
            "unit": "score",
            "weight": 0.07,
            "trend": kosdaq["trend"],
            "detail": (
                f"KOSDAQ {fmt_number(kosdaq['metrics']['last'])}, 20일 수익률 {fmt_pct(kosdaq['metrics']['return20dPct'])}, "
                f"20일 변동성 {fmt_pct(kosdaq['metrics']['realizedVol20dPct'])}"
            ),
            "source": "Yahoo Finance chart: ^KQ11",
        },
        {
            "id": "usdkrw_fx_pressure",
            "name": "원/달러 환율 압력",
            "category": "외환",
            "group": "macro",
            "value": usdkrw["score"],
            "unit": "score",
            "weight": 0.07,
            "trend": usdkrw["trend"],
            "detail": (
                f"USD/KRW {fmt_number(usdkrw['metrics']['last'])}, 20일 변화율 {fmt_pct(usdkrw['metrics']['return20dPct'])}, "
                f"2년 분위 {usdkrw['metrics']['levelPercentile']:.1f}"
            ),
            "source": "Yahoo Finance chart: KRW=X",
        },
        {
            "id": "global_volatility_pressure",
            "name": "글로벌 변동성 압력",
            "category": "변동성",
            "group": "macro",
            "value": vix["score"],
            "unit": "score",
            "weight": 0.06,
            "trend": vix["trend"],
            "detail": (
                f"VIX {fmt_number(vix['metrics']['last'])}, 20일 변화율 {fmt_pct(vix['metrics']['return20dPct'])}, "
                f"2년 분위 {vix['metrics']['levelPercentile']:.1f}"
            ),
            "source": "Yahoo Finance chart: ^VIX",
        },
        {
            "id": "rates_pressure",
            "name": "글로벌 금리 압력",
            "category": "금리",
            "group": "macro",
            "value": us10y["score"],
            "unit": "score",
            "weight": 0.04,
            "trend": us10y["trend"],
            "detail": (
                f"미 10년 금리 proxy {fmt_number(us10y['metrics']['last'])}, 20일 변화율 "
                f"{fmt_pct(us10y['metrics']['return20dPct'])}, 2년 분위 {us10y['metrics']['levelPercentile']:.1f}"
            ),
            "source": "Yahoo Finance chart: ^TNX",
        },
        {
            "id": "us_credit_spread_stress",
            "name": "미국 신용스프레드 스트레스",
            "category": "신용/크레딧",
            "group": "macro",
            "value": us_credit_spread["score"],
            "unit": "score",
            "weight": 0.06,
            "trend": us_credit_spread["trend"],
            "detail": (
                f"미국 하이일드 OAS {us_credit_spread['metrics']['highYieldOasLast']:.2f}%, "
                f"최근 20개 관측치 변화 {us_credit_spread['metrics']['highYieldOasChange20Obs']:+.2f}%p, "
                f"위험분위 {us_credit_spread['metrics']['highYieldOasRiskPercentile']:.1f}"
            ),
            "source": "FRED: BAMLH0A0HYM2",
        },
        {
            "id": "us_financial_conditions_stress",
            "name": "미국 금융여건 긴축 압력",
            "category": "금융여건",
            "group": "macro",
            "value": us_financial_conditions["score"],
            "unit": "score",
            "weight": 0.04,
            "trend": us_financial_conditions["trend"],
            "detail": (
                f"STLFSI {us_financial_conditions['metrics']['stlfsiLast']:.2f}, "
                f"NFCI {us_financial_conditions['metrics']['nfciLast']:.2f}, "
                f"미 2년 {us_financial_conditions['metrics']['us2yLast']:.2f}%, "
                f"10Y-2Y {us_financial_conditions['metrics']['curveLast']:.2f}%p"
            ),
            "source": "FRED: STLFSI4, NFCI, DGS2, T10Y2Y",
        },
        {
            "id": "global_ai_semiconductor_stress",
            "name": "글로벌 AI 반도체 스트레스",
            "category": "AI 반도체",
            "group": "ai_semi",
            "value": global_ai["score"],
            "unit": "score",
            "weight": 0.1,
            "trend": global_ai["trend"],
            "detail": (
                f"{global_ai['metrics']['basketSize']}개 글로벌 AI 반도체 basket, SOX {fmt_number(global_ai['metrics']['soxLast'])}, SOX 20일 수익률 "
                f"{fmt_pct(global_ai['metrics']['soxReturn20dPct'])}, NVDA 점수 {global_ai['metrics']['nvdaScore']:.1f}, "
                f"TSM 점수 {global_ai['metrics']['tsmScore']:.1f}"
            ),
            "source": "Yahoo Finance chart: ^SOX, NVDA, TSM, AVGO, AMD, MU, ASML",
        },
        {
            "id": "bigtech_ai_demand_pressure",
            "name": "빅테크 AI 수요 우려",
            "category": "AI 수요",
            "group": "ai_semi",
            "value": bigtech_demand["score"],
            "unit": "score",
            "weight": 0.06,
            "trend": bigtech_demand["trend"],
            "detail": (
                f"Apple {fmt_number(bigtech_demand['metrics']['appleLast'])}, Apple 20일 수익률 "
                f"{fmt_pct(bigtech_demand['metrics']['appleReturn20dPct'])}, 빅테크 바스켓 "
                f"{fmt_pct(bigtech_demand['metrics']['bigtechReturn20dPct'])}, 메모리 공급자 바스켓 "
                f"{fmt_pct(bigtech_demand['metrics']['memorySupplierReturn20dPct'])}, 비용압력 격차 "
                f"{fmt_pct(bigtech_demand['metrics']['costPressureGapPct'])}"
            ),
            "source": "Yahoo Finance chart: AAPL, MSFT, GOOGL, META, AMZN, 005930.KS, 000660.KS, MU",
        },
        {
            "id": "korea_ai_semiconductor_concentration",
            "name": "한국 AI 반도체 쏠림/스트레스",
            "category": "AI 반도체",
            "group": "ai_semi",
            "value": korea_ai["score"],
            "unit": "score",
            "weight": 0.1,
            "trend": korea_ai["trend"],
            "detail": (
                f"{korea_ai['metrics']['basketSize']}개 한국 반도체 basket, 삼성전자 {fmt_number(korea_ai['metrics']['samsungLast'])}, "
                f"SK하이닉스 {fmt_number(korea_ai['metrics']['hynixLast'])}, "
                f"반도체 60일 상대성과 {fmt_pct(korea_ai['metrics']['semiRelative60dPct'])}, "
                f"쏠림점수 {korea_ai['metrics']['crowdingScore']:.1f}"
            ),
            "source": "Yahoo Finance chart: 005930.KS, 000660.KS, 042700.KS, 000990.KS, 058470.KQ, ^KS11",
        },
        {
            "id": "foreign_ownership_pressure",
            "name": "외국인 소진율 이탈 압력",
            "category": "수급",
            "group": "flow",
            "value": foreign_ownership["score"],
            "unit": "score",
            "weight": 0.1,
            "trend": foreign_ownership["trend"],
            "detail": (
                f"대형 메모리 2개사·수급 민감 장비주 한미반도체의 외국인소진율 20일 %p 변화 기준, "
                f"최대 이탈폭 {foreign_ownership['metrics']['max20dDropPctp']:.2f}%p"
            ),
            "source": "Naver Finance chart: 005930, 000660, 042700",
        },
        {
            "id": "trading_activity_heat",
            "name": "거래량 과열/위축 압력",
            "category": "유동성",
            "group": "liquidity",
            "value": trading_activity["score"],
            "unit": "score",
            "weight": 0.07,
            "trend": trading_activity["trend"],
            "detail": (
                f"주요 반도체·KODEX ETF 거래량의 60일 평균 대비 평균 괴리 "
                f"{fmt_pct(trading_activity['metrics']['avgVolumeVs60dAvgPct'])}, 최대 괴리 "
                f"{fmt_pct(trading_activity['metrics']['maxVolumeVs60dAvgPct'])}"
            ),
            "source": "Naver Finance chart: 005930, 000660, 042700, 069500, 122630",
        },
        {
            "id": "single_name_semiconductor_leverage",
            "name": "삼성전자·하이닉스 단일종목 레버리지",
            "category": "단일종목/레버리지",
            "group": "overheating",
            "value": single_name_leverage["score"],
            "unit": "score",
            "weight": 0.04,
            "trend": single_name_leverage["trend"],
            "detail": (
                f"삼성전자 {fmt_number(single_name_leverage['metrics']['samsungLast'])}, "
                f"SK하이닉스 {fmt_number(single_name_leverage['metrics']['hynixLast'])}, "
                f"20일 수익률 삼성 {fmt_pct(single_name_leverage['metrics']['samsungReturn20dPct'])}·"
                f"하이닉스 {fmt_pct(single_name_leverage['metrics']['hynixReturn20dPct'])}, "
                f"KOSPI 대비 변동성 배율 삼성 {single_name_leverage['metrics']['samsungVolRatioToKospi']:.2f}x·"
                f"하이닉스 {single_name_leverage['metrics']['hynixVolRatioToKospi']:.2f}x"
            ),
            "source": "Yahoo Finance chart: 005930.KS, 000660.KS, ^KS11",
        },
        {
            "id": "global_credit_proxy_stress",
            "name": "글로벌 신용스프레드 proxy",
            "category": "신용/크레딧",
            "group": "macro",
            "value": global_credit["score"],
            "unit": "score",
            "weight": 0.03,
            "trend": global_credit["trend"],
            "detail": (
                f"HYG/LQD 상대가격 기준, 20일 수익률 {fmt_pct(global_credit['metrics']['return20dPct'])}, "
                f"고점대비 -{global_credit['metrics']['drawdownPct']:.2f}%"
            ),
            "source": "Yahoo Finance chart: HYG, LQD",
        },
        {
            "id": "emerging_market_stress",
            "name": "신흥국 위험선호 스트레스",
            "category": "글로벌",
            "group": "overheating",
            "value": emerging_market["score"],
            "unit": "score",
            "weight": 0.05,
            "trend": emerging_market["trend"],
            "detail": (
                f"EEM {fmt_number(emerging_market['metrics']['last'])}, 20일 수익률 "
                f"{fmt_pct(emerging_market['metrics']['return20dPct'])}, 고점대비 "
                f"-{emerging_market['metrics']['drawdownPct']:.2f}%"
            ),
            "source": "Yahoo Finance chart: EEM",
        },
    ]


def update_dashboard(series_map, fred_map, indicators):
    dashboard = json.loads(DASHBOARD_FILE.read_text(encoding="utf-8"))
    generated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    as_of = max(
        [point["date"] for series in series_map.values() for point in series[-1:]]
        + [point["date"] for series in fred_map.values() for point in series[-1:]]
    )
    enriched_indicators, group_scores = enrich_indicators(indicators)

    dashboard["metadata"]["asOf"] = as_of
    dashboard["metadata"]["generatedAt"] = generated_at
    dashboard["metadata"]["source"] = "Yahoo Finance, Naver Finance, and FRED endpoints via scripts/update_market_risk.py"

    market = next(section for section in dashboard["sections"] if section["id"] == "market")
    market["description"] = (
        "KOSPI/KOSDAQ, 원달러 환율, 글로벌 변동성·금리·크레딧, 미국 신용스프레드·금융여건, "
        "외국인 보유비중, 거래량, "
        "대형 반도체 단일종목 레버리지성 스트레스, 빅테크 AI 수요 우려, "
        "AI 반도체 밸류체인 가격 신호를 표준화한 시장 조기경보 모듈입니다."
    )
    market["model"]["version"] = "market-risk-v4-fred-credit-financial-conditions"
    market["model"]["methodology"] = (
        "각 시계열의 2년 히스토리에서 레벨, 20일 변화율, 20일 실현변동성, 252일 고점대비 낙폭을 "
        "분위수 점수, z-score 정규분포 변환 점수, median/MAD 기반 robust z-score 변환 점수로 "
        "각각 0~100 표준화하고, 하위 리스크 그룹별 기여도를 함께 산출합니다."
    )
    market["model"]["normalization"] = {
        "percentileWeight": 0.4,
        "zScoreWeight": 0.3,
        "robustZScoreWeight": 0.3,
        "zScoreMapping": "normalCDF",
        "robustZScore": "median/MAD",
        "scoreRange": "0-100",
    }
    market["model"]["riskGroups"] = RISK_GROUPS
    market["model"]["dataSources"] = [
        "Yahoo Finance chart endpoint",
        "Naver Finance chart endpoint",
        "KOSPI/KOSDAQ price series",
        "USD/KRW, VIX, US 10Y proxy",
        "FRED US 2Y, 10Y-2Y spread, high yield OAS, STLFSI, NFCI",
        "SOX, NVIDIA, TSMC ADR, Broadcom, AMD, Micron, ASML",
        "Apple, Microsoft, Alphabet, Meta Platforms, Amazon",
        "Samsung Electronics, SK hynix, Hanmi Semiconductor, DB HiTek, Leeno Industrial",
        "Naver foreign ownership ratio and trading volume",
        "HYG/LQD credit proxy, EEM emerging market proxy",
    ]
    market["model"]["references"] = [
        {
            "label": "Bank of Korea FSI/FVI composite index approach",
            "url": "https://www.bok.or.kr/portal/bbs/B0000347/view.do?menuNo=201106&nttId=10077975&pageIndex=1",
        },
        {
            "label": "Yahoo Finance chart endpoint",
            "url": "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?range=2y&interval=1d",
        },
        {
            "label": "Naver Finance chart endpoint",
            "url": "https://api.finance.naver.com/siseJson.naver?symbol=005930&requestType=1&timeframe=day",
        },
        {
            "label": "FRED high yield OAS",
            "url": "https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
        },
        {
            "label": "FRED financial conditions and stress indices",
            "url": "https://fred.stlouisfed.org/series/NFCI",
        },
    ]
    market["groupScores"] = group_scores
    market["indicators"] = enriched_indicators
    market["actions"] = [
        "scripts/update_market_risk.py를 일 단위로 실행해 data/risk-dashboard.json과 market-risk-snapshot.json을 갱신합니다.",
        "점수 75 이상 또는 핵심 지표 2개 이상 경고 시 투자위원회 보고 대상을 자동 지정합니다.",
        "운영 배포에서는 Yahoo/Naver proxy를 KRX, 한국은행 ECOS, 금융투자협회, 내부 포지션/외국인 수급 데이터로 교체할 수 있습니다.",
    ]

    DASHBOARD_FILE.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_snapshot(series_map, naver_map, fred_map, indicators):
    enriched_indicators, group_scores = enrich_indicators(indicators)
    snapshot = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "Yahoo Finance, Naver Finance, and FRED endpoints",
        "yahooSymbols": {
            key: {
                "symbol": config["symbol"],
                "label": config["label"],
                "lastDate": series_map[key][-1]["date"],
                "lastClose": round(series_map[key][-1]["close"], 4),
                "observations": len(series_map[key]),
            }
            for key, config in TICKERS.items()
        },
        "naverSymbols": {
            key: {
                "symbol": config["symbol"],
                "label": config["label"],
                "lastDate": naver_map[key][-1]["date"],
                "lastClose": round(naver_map[key][-1]["close"], 4),
                "lastVolume": naver_map[key][-1]["volume"],
                "lastForeignOwnership": naver_map[key][-1]["foreignOwnership"],
                "observations": len(naver_map[key]),
            }
            for key, config in NAVER_SYMBOLS.items()
        },
        "fredSeries": {
            key: {
                "seriesId": config["series_id"],
                "label": config["label"],
                "lastDate": fred_map[key][-1]["date"],
                "lastClose": round(fred_map[key][-1]["close"], 4),
                "observations": len(fred_map[key]),
            }
            for key, config in FRED_SERIES.items()
        },
        "groupScores": group_scores,
        "indicators": enriched_indicators,
    }
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_timeseries(series_map, naver_map, fred_map):
    timeseries = {
        "generatedAt": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "Yahoo Finance, Naver Finance, and FRED endpoints",
        "window": "recent 120 observations per indicator",
        "unit": "risk score",
        "series": build_timeseries(series_map, naver_map, fred_map),
    }
    TIMESERIES_FILE.write_text(json.dumps(timeseries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    series_map = {key: fetch_yahoo_chart(config["symbol"]) for key, config in TICKERS.items()}
    naver_map = {key: fetch_naver_chart(config["symbol"]) for key, config in NAVER_SYMBOLS.items()}
    fred_map = {key: fetch_fred_series_with_fallback(config) for key, config in FRED_SERIES.items()}
    indicators = build_indicators(series_map, naver_map, fred_map)
    update_dashboard(series_map, fred_map, indicators)
    write_snapshot(series_map, naver_map, fred_map, indicators)
    write_timeseries(series_map, naver_map, fred_map)

    total_weight = sum(indicator["weight"] for indicator in indicators)
    weighted_score = sum(indicator["value"] * indicator["weight"] for indicator in indicators) / total_weight
    print(f"Updated market risk indicators: {round_score(weighted_score)} / 100")
    print(f"Wrote {DASHBOARD_FILE.relative_to(ROOT)}")
    print(f"Wrote {SNAPSHOT_FILE.relative_to(ROOT)}")
    print(f"Wrote {TIMESERIES_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
