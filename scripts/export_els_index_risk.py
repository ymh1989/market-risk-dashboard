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
    latest_year = int(str(latest["date"])[:4])
    ytd_prices = frame.loc[pd.to_datetime(frame["date"]).dt.year == latest_year, ["date", "close"]]
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
            "ytdPriceSeries": [
                {"date": row["date"], "close": _round(row["close"], 2)}
                for row in ytd_prices.to_dict(orient="records")
            ],
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


def _issuance_stance(opportunity_score: float, hedge_burden_score: float) -> dict[str, str]:
    if hedge_burden_score >= 80:
        return {"label": "발행부담", "tone": "danger"}
    if opportunity_score >= 65 and hedge_burden_score >= 45:
        return {"label": "헤지주의", "tone": "caution"}
    if opportunity_score >= 65:
        return {"label": "발행기회", "tone": "good"}
    return {"label": "선별발행", "tone": "watch"}


def _issuance_hedge_item(index: dict, correlation_score: float) -> dict:
    metrics = index["metrics"]
    realized_vol_20d = float(metrics["realizedVol20dPct"])
    realized_vol_60d = max(float(metrics["realizedVol60dPct"]), 1.0)
    vol_percentile = float(metrics["volPercentile252d"])
    vol_level_score = _clamp((realized_vol_20d - 8.0) / 42.0 * 100)
    vol_shock_score = _clamp((realized_vol_20d / realized_vol_60d - 0.8) / 0.8 * 100)
    downside_momentum_score = _clamp(-float(metrics["return20dPct"]) / 20.0 * 100)
    drawdown_score = _clamp(-float(metrics["drawdown252dPct"]) / 30.0 * 100)
    gap_shock_score = _clamp(float(metrics["maxAbsDailyMove20dPct"]) / 8.0 * 100)

    opportunity_score = 0.55 * vol_percentile + 0.30 * vol_level_score + 0.15 * vol_shock_score
    hedge_burden_score = (
        0.25 * downside_momentum_score
        + 0.25 * drawdown_score
        + 0.25 * vol_level_score
        + 0.15 * gap_shock_score
        + 0.10 * correlation_score
    )
    stance = _issuance_stance(opportunity_score, hedge_burden_score)
    balance_score = opportunity_score - 0.65 * hedge_burden_score

    if stance["label"] == "발행기회":
        interpretation = "상대적 쿠폰 여력이 높고 현재 낙폭·방향성 부담은 제한적입니다. 신규 발행 후보군으로 우선 검토할 수 있습니다."
    elif stance["label"] == "헤지주의":
        interpretation = "발행 조건 개선 여지는 크지만 변동성과 하락 경로가 헤지비용을 높입니다. 한도·만기·기초자산 집중을 함께 관리해야 합니다."
    elif stance["label"] == "발행부담":
        interpretation = "쿠폰 여력보다 기존 북의 순연, 감마·베가와 낙인 접근 부담이 더 큽니다. 신규 발행보다 익스포저 축소가 우선입니다."
    else:
        interpretation = "헤지부담은 통제 가능하지만 쿠폰 여력이 제한적일 수 있습니다. 구조와 만기를 선별해 상대가치를 확인해야 합니다."

    return {
        "id": index["id"],
        "label": index["label"],
        "name": index["name"],
        "region": index["region"],
        "lastDate": index["lastDate"],
        "opportunityScore": _round(opportunity_score, 2),
        "hedgeBurdenScore": _round(hedge_burden_score, 2),
        "balanceScore": _round(balance_score, 2),
        "stance": stance["label"],
        "tone": stance["tone"],
        "interpretation": interpretation,
        "components": {
            "volPercentileScore": _round(vol_percentile, 1),
            "volLevelScore": _round(vol_level_score, 1),
            "volShockScore": _round(vol_shock_score, 1),
            "downsideMomentumScore": _round(downside_momentum_score, 1),
            "drawdownScore": _round(drawdown_score, 1),
            "gapShockScore": _round(gap_shock_score, 1),
            "correlationScore": _round(correlation_score, 1),
        },
        "metrics": {
            "return20dPct": metrics["return20dPct"],
            "realizedVol20dPct": metrics["realizedVol20dPct"],
            "drawdown252dPct": metrics["drawdown252dPct"],
        },
    }


def _issuance_hedge_map(indices: list[dict], correlation_score: float) -> dict:
    items = [_issuance_hedge_item(index, correlation_score) for index in indices]
    opportunity_ranked = sorted(items, key=lambda item: item["opportunityScore"], reverse=True)
    burden_ranked = sorted(items, key=lambda item: item["hedgeBurdenScore"], reverse=True)
    average_opportunity = float(np.mean([item["opportunityScore"] for item in items]))
    average_burden = float(np.mean([item["hedgeBurdenScore"] for item in items]))
    basket_opportunity = (
        0.40 * opportunity_ranked[0]["opportunityScore"]
        + 0.25 * opportunity_ranked[1]["opportunityScore"]
        + 0.35 * average_opportunity
    )
    basket_burden = (
        0.50 * burden_ranked[0]["hedgeBurdenScore"]
        + 0.20 * burden_ranked[1]["hedgeBurdenScore"]
        + 0.15 * average_burden
        + 0.15 * correlation_score
    )
    basket_stance = _issuance_stance(basket_opportunity, basket_burden)

    return {
        "methodology": {
            "opportunity": "252일 변동성 분위수 55%, 20일 변동성 수준 30%, 20일/60일 변동성 충격 15%를 합성합니다.",
            "hedgeBurden": "20일 하락모멘텀 25%, 252일 고점대비 낙폭 25%, 20일 변동성 수준 25%, 최근 일간 충격 15%, 지수 동조화 10%를 합성합니다.",
            "classification": "헤지부담 80점 이상은 발행부담, 발행기회 65점 이상이면서 헤지부담 45점 이상은 헤지주의, 발행기회 65점 이상이면서 부담이 낮으면 발행기회, 나머지는 선별발행입니다.",
        },
        "basket": {
            "opportunityScore": _round(basket_opportunity, 2),
            "hedgeBurdenScore": _round(basket_burden, 2),
            "stance": basket_stance["label"],
            "tone": basket_stance["tone"],
            "topOpportunityIndex": opportunity_ranked[0]["label"],
            "topBurdenIndex": burden_ranked[0]["label"],
            "interpretation": f"{opportunity_ranked[0]['label']}의 변동성에서 발행 조건 개선 여지가 가장 크고, {burden_ranked[0]['label']}가 기존 북의 헤지부담을 가장 크게 높입니다.",
        },
        "items": sorted(items, key=lambda item: item["balanceScore"], reverse=True),
        "limitations": "공개 종가지수의 실현변동성·낙폭을 사용한 상대평가입니다. 실제 발행 판단에는 만기별 내재변동성, skew·상관 smile, 금리·배당·조달비용, 기발행 재고와 상품별 delta·gamma·vega를 추가해야 합니다.",
    }


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
    issuance_hedge_map = _issuance_hedge_map(indices, correlation_score)

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
        "issuanceHedgeMap": issuance_hedge_map,
        "indices": indices,
    }
    return payload


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(build_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote ELS index risk: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
