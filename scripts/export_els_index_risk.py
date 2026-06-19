from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "data" / "els-index-risk.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_value}&interval=1d"
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-els-index-risk/0.1)"

INDICES = [
    {"id": "spx", "symbol": "^GSPC", "label": "SPX", "name": "S&P 500", "region": "미국"},
    {"id": "sx5e", "symbol": "^STOXX50E", "label": "SX5E", "name": "Euro Stoxx 50", "region": "유럽"},
    {"id": "nky", "symbol": "^N225", "label": "NKY", "name": "Nikkei 225", "region": "일본"},
    {"id": "hscei", "symbol": "^HSCE", "label": "HSCEI", "name": "Hang Seng China Enterprises", "region": "중국/HK"},
    {"id": "kospi200", "symbol": "^KS200", "label": "KOSPI200", "name": "KOSPI 200", "region": "한국"},
]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _bucket(score: float) -> dict[str, str]:
    if score < 30:
        return {"label": "낮음", "tone": "good"}
    if score < 50:
        return {"label": "정상", "tone": "watch"}
    if score < 65:
        return {"label": "주의", "tone": "caution"}
    if score < 80:
        return {"label": "높음", "tone": "danger"}
    return {"label": "경고", "tone": "danger"}


def _round(value: float | int | None, digits: int = 2) -> float | None:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _fetch_yahoo(symbol: str, range_value: str = "2y") -> pd.DataFrame:
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
    closes = result["indicators"]["quote"][0].get("close", [])
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        rows.append({"date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(), "close": float(close)})
    frame = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(frame) < 260:
        raise RuntimeError(f"{symbol}: ELS index risk needs at least 260 observations, got {len(frame)}")
    return frame


def _percentile_rank(values: pd.Series, value: float) -> float:
    clean = values.dropna().astype(float)
    if clean.empty or pd.isna(value):
        return 50.0
    return float((clean <= value).mean() * 100)


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ret_1d"] = out["close"].pct_change()
    out["ret_20d"] = out["close"] / out["close"].shift(20) - 1
    out["ret_60d"] = out["close"] / out["close"].shift(60) - 1
    out["realized_vol_20d"] = out["ret_1d"].rolling(20, min_periods=20).std() * np.sqrt(252)
    out["realized_vol_60d"] = out["ret_1d"].rolling(60, min_periods=60).std() * np.sqrt(252)
    out["drawdown_60d"] = out["close"] / out["close"].rolling(60, min_periods=20).max() - 1
    out["drawdown_252d"] = out["close"] / out["close"].rolling(252, min_periods=60).max() - 1
    out["max_abs_daily_move_20d"] = out["ret_1d"].abs().rolling(20, min_periods=20).max()

    vol_percentiles = []
    for index, row in out.iterrows():
        start = max(0, index - 252)
        vol_percentiles.append(_percentile_rank(out.loc[start:index, "realized_vol_20d"], row["realized_vol_20d"]))
    out["vol_percentile_252d"] = vol_percentiles

    downside_momentum = (-out["ret_20d"].clip(upper=0) / 0.15 * 100).clip(0, 100)
    drawdown_pressure = (-out["drawdown_252d"].clip(upper=0) / 0.30 * 100).clip(0, 100)
    short_drawdown_pressure = (-out["drawdown_60d"].clip(upper=0) / 0.18 * 100).clip(0, 100)
    gap_shock = (out["max_abs_daily_move_20d"] / 0.06 * 100).clip(0, 100)
    overheating = (out["ret_20d"].clip(lower=0) / 0.12 * 100).clip(0, 100) * (out["vol_percentile_252d"] / 100)

    out["els_risk_score"] = (
        0.35 * out["vol_percentile_252d"]
        + 0.22 * drawdown_pressure
        + 0.18 * downside_momentum
        + 0.15 * overheating
        + 0.06 * short_drawdown_pressure
        + 0.04 * gap_shock
    ).clip(0, 100)
    return out


def _reading(latest: pd.Series, score: float) -> str:
    ret20 = float(latest["ret_20d"] * 100)
    vol20 = float(latest["realized_vol_20d"] * 100)
    dd252 = float(latest["drawdown_252d"] * 100)
    if ret20 > 5 and vol20 >= 30:
        return "상승 모멘텀은 강하지만 변동성이 높아 발행 조건은 좋아질 수 있고, 동시에 헤지 비용 관리가 중요합니다."
    if dd252 <= -15:
        return "고점 대비 낙폭이 커져 쿠폰 매력도는 높아질 수 있지만, 순연 가능성과 헤지 비용 증가를 우선 점검해야 합니다."
    if ret20 < -5:
        return "단기 하락 모멘텀이 점수 상승을 주도합니다. 신규 발행 여지는 생기지만 기존 북의 순연·헤지 부담을 함께 봐야 합니다."
    if score >= 65:
        return "변동성 또는 낙폭 조건이 높아 발행 기회와 헤지 비용 부담이 동시에 커진 구간입니다."
    return "현재 점수는 중립권이며, 변동성 확대 여부를 중심으로 보면 됩니다."


def _index_payload(spec: dict[str, str]) -> tuple[dict, pd.DataFrame]:
    frame = _features(_fetch_yahoo(spec["symbol"]))
    latest = frame.dropna(subset=["els_risk_score"]).iloc[-1]
    score = float(latest["els_risk_score"])
    bucket = _bucket(score)
    series = []
    for row in frame.dropna(subset=["els_risk_score"]).tail(80).to_dict(orient="records"):
        series.append(
            {
                "date": row["date"],
                "close": _round(row["close"], 2),
                "score": _round(row["els_risk_score"], 2),
                "return20dPct": _round(row["ret_20d"] * 100, 2),
                "realizedVol20dPct": _round(row["realized_vol_20d"] * 100, 2),
                "drawdown252dPct": _round(row["drawdown_252d"] * 100, 2),
            }
        )
    return (
        {
            **spec,
            "lastDate": latest["date"],
            "lastClose": _round(latest["close"], 2),
            "score": _round(score, 2),
            "bucket": bucket["label"],
            "tone": bucket["tone"],
            "reading": _reading(latest, score),
            "metrics": {
                "return20dPct": _round(latest["ret_20d"] * 100, 2),
                "return60dPct": _round(latest["ret_60d"] * 100, 2),
                "realizedVol20dPct": _round(latest["realized_vol_20d"] * 100, 2),
                "realizedVol60dPct": _round(latest["realized_vol_60d"] * 100, 2),
                "volPercentile252d": _round(latest["vol_percentile_252d"], 1),
                "drawdown60dPct": _round(latest["drawdown_60d"] * 100, 2),
                "drawdown252dPct": _round(latest["drawdown_252d"] * 100, 2),
                "maxAbsDailyMove20dPct": _round(latest["max_abs_daily_move_20d"] * 100, 2),
            },
            "series": series,
        },
        frame[["date", "ret_1d"]].assign(index_id=spec["id"]),
    )


def _correlation_score(return_frames: list[pd.DataFrame]) -> float:
    pivot = pd.concat(return_frames).pivot(index="date", columns="index_id", values="ret_1d").tail(80)
    corr = pivot.corr().where(~np.eye(len(pivot.columns), dtype=bool)).stack()
    if corr.empty:
        return 50.0
    avg_corr = float(corr.mean())
    return _clamp((avg_corr - 0.2) / 0.6 * 100)


def build_payload() -> dict:
    indices = []
    return_frames = []
    for spec in INDICES:
        index_payload, returns = _index_payload(spec)
        indices.append(index_payload)
        return_frames.append(returns)

    ranked = sorted(indices, key=lambda item: item["score"], reverse=True)
    average_score = float(np.mean([item["score"] for item in indices]))
    correlation_score = _correlation_score(return_frames)
    basket_score = 0.5 * ranked[0]["score"] + 0.2 * ranked[1]["score"] + 0.15 * average_score + 0.15 * correlation_score
    basket_bucket = _bucket(basket_score)

    payload = {
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST"),
        "methodology": "각 지수별 20일 실현변동성, 252일 변동성 분위수, 20/60/252일 낙폭, 단기 하락 모멘텀, 고변동성 급등 신호를 0~100점으로 합성합니다. Basket 점수는 worst-of ELS 구조를 반영해 가장 높은 리스크 지수에 50% 가중합니다.",
        "basket": {
            "score": _round(basket_score, 2),
            "bucket": basket_bucket["label"],
            "tone": basket_bucket["tone"],
            "worstIndex": ranked[0]["label"],
            "secondWorstIndex": ranked[1]["label"],
            "averageIndexScore": _round(average_score, 2),
            "correlationScore": _round(correlation_score, 2),
            "interpretation": f"{ranked[0]['label']}가 basket 리스크를 가장 크게 끌어올리고, {ranked[1]['label']}가 두 번째 취약 지수입니다.",
        },
        "indices": indices,
    }
    return payload


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(build_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote ELS index risk: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
