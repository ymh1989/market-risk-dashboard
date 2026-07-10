from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "data" / "hmm-regime.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_value}&interval=1d"
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-hmm-regime/0.1)"
RANGE_VALUE = "5y"
FIT_WINDOW = 756
SERIES_TAIL = 140
N_STATES = 3
RANDOM_STATE = 42

INDICES = [
    {"id": "spx", "symbol": "^GSPC", "volSymbols": ["^VIX"], "label": "SPX", "name": "S&P 500", "region": "미국", "volLabel": "VIX"},
    {"id": "sx5e", "symbol": "^STOXX50E", "volSymbols": ["^V2TX"], "label": "SX5E", "name": "Euro Stoxx 50", "region": "유럽", "volLabel": "VSTOXX"},
    {"id": "nky", "symbol": "^N225", "volSymbols": ["^JNIV"], "label": "NKY", "name": "Nikkei 225", "region": "일본", "volLabel": "Nikkei VI"},
    {"id": "hscei", "symbol": "^HSCE", "volSymbols": ["^VHSI"], "label": "HSCEI", "name": "Hang Seng China Enterprises", "region": "중국/HK", "volLabel": "VHSI"},
    {"id": "kospi200", "symbol": "^KS200", "volSymbols": ["^VKOSPI"], "label": "KOSPI200", "name": "KOSPI 200", "region": "한국", "volLabel": "VKOSPI"},
]


@dataclass
class FittedHMM:
    startprob: np.ndarray
    transmat: np.ndarray
    means: np.ndarray
    covars: np.ndarray


def _round(value: float | int | None, digits: int = 2) -> float | None:
    if value is None or pd.isna(value) or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _fetch_yahoo(symbol: str, range_value: str = RANGE_VALUE, min_rows: int = 120) -> pd.DataFrame:
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
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{symbol}: Yahoo 응답에 result가 없습니다.")

    result = results[0]
    timestamps = result.get("timestamp", [])
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        rows.append({"date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(), "close": float(close)})
    frame = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(frame) < min_rows:
        raise RuntimeError(f"{symbol}: 관측치가 부족합니다: {len(frame)}")
    return frame


def _fetch_first_available(symbols: list[str]) -> tuple[pd.DataFrame | None, str | None]:
    for symbol in symbols:
        try:
            return _fetch_yahoo(symbol, min_rows=80), symbol
        except Exception:
            continue
    return None, None


def _percentile_rank(values: pd.Series, value: float) -> float:
    clean = values.dropna().astype(float)
    if clean.empty or pd.isna(value):
        return 50.0
    return float((clean <= value).mean() * 100)


def _rolling_percentile(series: pd.Series, window: int = 252) -> list[float]:
    ranks = []
    for index, value in enumerate(series):
        start = max(0, index - window + 1)
        ranks.append(_percentile_rank(series.iloc[start : index + 1], value))
    return ranks


def _features(price: pd.DataFrame, vol: pd.DataFrame | None) -> pd.DataFrame:
    out = price.rename(columns={"close": "price"}).copy()
    if vol is not None:
        vol_frame = vol.rename(columns={"close": "vol_proxy"})[["date", "vol_proxy"]]
        out = out.merge(vol_frame, on="date", how="left")
        out["vol_proxy"] = out["vol_proxy"].ffill()
        out["vol_proxy_source"] = "listed_vol_index"
    else:
        out["vol_proxy"] = np.nan
        out["vol_proxy_source"] = "realized_vol_fallback"

    out["ret_1d"] = out["price"].pct_change()
    out["ret_5d"] = out["price"] / out["price"].shift(5) - 1
    out["ret_20d"] = out["price"] / out["price"].shift(20) - 1
    out["realized_vol_20d"] = out["ret_1d"].rolling(20, min_periods=20).std() * np.sqrt(252)
    out["realized_vol_60d"] = out["ret_1d"].rolling(60, min_periods=60).std() * np.sqrt(252)
    out["drawdown_60d"] = out["price"] / out["price"].rolling(60, min_periods=20).max() - 1
    out["drawdown_252d"] = out["price"] / out["price"].rolling(252, min_periods=60).max() - 1
    out["vol_proxy"] = out["vol_proxy"].fillna(out["realized_vol_20d"] * 100)
    out["vol_proxy_change_20d"] = out["vol_proxy"] / out["vol_proxy"].shift(20) - 1
    out["realized_vol_rank_252d"] = _rolling_percentile(out["realized_vol_20d"], 252)
    out["vol_proxy_rank_252d"] = _rolling_percentile(out["vol_proxy"], 252)
    return out


def _logsumexp(values: np.ndarray, axis: int | None = None) -> np.ndarray:
    if axis is None:
        max_value = np.max(values)
        if not np.isfinite(max_value):
            max_value = 0.0
        return max_value + np.log(np.sum(np.exp(values - max_value)))
    max_value = np.max(values, axis=axis, keepdims=True)
    max_value[~np.isfinite(max_value)] = 0
    result = max_value + np.log(np.sum(np.exp(values - max_value), axis=axis, keepdims=True))
    return np.squeeze(result, axis=axis)


def _log_gaussian_diag(x: np.ndarray, means: np.ndarray, covars: np.ndarray) -> np.ndarray:
    covars = np.clip(covars, 1e-4, None)
    diff = x[:, None, :] - means[None, :, :]
    return -0.5 * (
        np.sum(np.log(2 * np.pi * covars), axis=1)[None, :]
        + np.sum((diff * diff) / covars[None, :, :], axis=2)
    )


def _forward_backward(x: np.ndarray, model: FittedHMM) -> tuple[float, np.ndarray, np.ndarray]:
    log_start = np.log(np.clip(model.startprob, 1e-12, 1))
    log_trans = np.log(np.clip(model.transmat, 1e-12, 1))
    log_emit = _log_gaussian_diag(x, model.means, model.covars)
    n_obs, n_states = log_emit.shape

    log_alpha = np.zeros((n_obs, n_states))
    log_alpha[0] = log_start + log_emit[0]
    for t in range(1, n_obs):
        log_alpha[t] = log_emit[t] + _logsumexp(log_alpha[t - 1][:, None] + log_trans, axis=0)

    log_likelihood = float(_logsumexp(log_alpha[-1], axis=0))
    log_beta = np.zeros((n_obs, n_states))
    for t in range(n_obs - 2, -1, -1):
        log_beta[t] = _logsumexp(log_trans + log_emit[t + 1][None, :] + log_beta[t + 1][None, :], axis=1)

    log_gamma = log_alpha + log_beta
    log_gamma = log_gamma - _logsumexp(log_gamma, axis=1)[:, None]
    gamma = np.exp(log_gamma)
    xi_sum = np.zeros((n_states, n_states))
    for t in range(n_obs - 1):
        log_xi = (
            log_alpha[t][:, None]
            + log_trans
            + log_emit[t + 1][None, :]
            + log_beta[t + 1][None, :]
        )
        log_xi = log_xi - _logsumexp(log_xi, axis=None)
        xi_sum += np.exp(log_xi)
    return log_likelihood, gamma, xi_sum


def _fit_hmm(x: np.ndarray, n_states: int = N_STATES, max_iter: int = 0) -> tuple[FittedHMM, np.ndarray]:
    if len(x) < n_states * 40:
        raise ValueError("HMM 학습 관측치가 부족합니다.")

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        labels = KMeans(n_clusters=n_states, random_state=RANDOM_STATE, n_init=10).fit_predict(x)
    means = np.vstack([x[labels == state].mean(axis=0) for state in range(n_states)])
    global_var = np.var(x, axis=0) + 1e-3
    covars = np.vstack(
        [
            np.var(x[labels == state], axis=0) + 1e-3 if np.any(labels == state) else global_var
            for state in range(n_states)
        ]
    )
    startprob = np.full(n_states, 1e-3)
    startprob[labels[0]] += 1
    startprob = startprob / startprob.sum()
    transmat = np.ones((n_states, n_states))
    for left, right in zip(labels[:-1], labels[1:]):
        transmat[left, right] += 1
    transmat += np.eye(n_states) * 3
    transmat = transmat / transmat.sum(axis=1, keepdims=True)
    model = FittedHMM(startprob=startprob, transmat=transmat, means=means, covars=covars)

    previous_ll = -np.inf
    for _ in range(max_iter):
        ll, gamma, xi_sum = _forward_backward(x, model)
        weights = gamma.sum(axis=0) + 1e-8
        means = (gamma.T @ x) / weights[:, None]
        covars = np.vstack(
            [
                (gamma[:, state][:, None] * (x - means[state]) ** 2).sum(axis=0) / weights[state] + 1e-3
                for state in range(n_states)
            ]
        )
        startprob = gamma[0] + 1e-6
        startprob = startprob / startprob.sum()
        transmat = xi_sum + 1e-4 + np.eye(n_states) * 1e-3
        transmat = transmat / transmat.sum(axis=1, keepdims=True)
        model = FittedHMM(startprob=startprob, transmat=transmat, means=means, covars=covars)
        if abs(ll - previous_ll) < 1e-4:
            break
        previous_ll = ll

    _, posterior, _ = _forward_backward(x, model)
    return model, posterior


def _viterbi(x: np.ndarray, model: FittedHMM) -> np.ndarray:
    log_start = np.log(np.clip(model.startprob, 1e-12, 1))
    log_trans = np.log(np.clip(model.transmat, 1e-12, 1))
    log_emit = _log_gaussian_diag(x, model.means, model.covars)
    n_obs, n_states = log_emit.shape
    score = np.zeros((n_obs, n_states))
    back = np.zeros((n_obs, n_states), dtype=int)
    score[0] = log_start + log_emit[0]
    for t in range(1, n_obs):
        candidates = score[t - 1][:, None] + log_trans
        back[t] = np.argmax(candidates, axis=0)
        score[t] = np.max(candidates, axis=0) + log_emit[t]
    path = np.zeros(n_obs, dtype=int)
    path[-1] = int(np.argmax(score[-1]))
    for t in range(n_obs - 2, -1, -1):
        path[t] = back[t + 1, path[t + 1]]
    return path


def _robust_scale(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    values = frame[columns].astype(float).replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(0)
    median = values.median()
    mad = (values - median).abs().median().replace(0, np.nan).fillna(values.std()).replace(0, 1)
    return ((values - median) / mad).clip(-6, 6).to_numpy(dtype=float)


def _pressure(value: float, threshold: float) -> float:
    return _clamp(value / threshold * 100)


def _state_labels(frame: pd.DataFrame, path: np.ndarray) -> dict[int, str]:
    stats = {}
    for state in range(N_STATES):
        subset = frame.loc[path == state]
        if subset.empty:
            subset = frame
        ret20 = float(subset["ret_20d"].mean() * 100)
        vol_rank = float(subset["realized_vol_rank_252d"].mean())
        vol_proxy_rank = float(subset["vol_proxy_rank_252d"].mean())
        dd60 = float(subset["drawdown_60d"].mean() * 100)
        downside = _pressure(max(-ret20, 0), 10)
        upside = _pressure(max(ret20, 0), 10)
        drawdown = _pressure(max(-dd60, 0), 12)
        risk_score = 0.28 * vol_rank + 0.24 * vol_proxy_rank + 0.28 * downside + 0.20 * drawdown
        bull_score = 0.36 * vol_rank + 0.28 * vol_proxy_rank + 0.36 * upside - 0.25 * drawdown
        stable_score = -0.55 * vol_rank - 0.45 * drawdown
        stats[state] = {"risk": risk_score, "bull": bull_score, "stable": stable_score}

    risk_state = max(stats, key=lambda state: stats[state]["risk"])
    bull_candidates = [state for state in stats if state != risk_state]
    bull_state = max(bull_candidates or list(stats), key=lambda state: stats[state]["bull"])
    stable_candidates = [state for state in stats if state not in {risk_state, bull_state}]
    stable_state = max(stable_candidates or list(stats), key=lambda state: stats[state]["stable"])
    return {
        stable_state: "안정",
        bull_state: "고변동성 활황",
        risk_state: "위험회피",
    }


def _tone(regime: str) -> str:
    if regime == "위험회피":
        return "danger"
    if regime == "고변동성 활황":
        return "caution"
    return "good"


def _reading(regime: str, latest: pd.Series, probabilities: dict[str, float], vol_source_label: str) -> str:
    ret20 = float(latest["ret_20d"] * 100)
    vol20 = float(latest["realized_vol_20d"] * 100)
    dd60 = float(latest["drawdown_60d"] * 100)
    if regime == "고변동성 활황":
        return (
            f"20일 수익률 {ret20:+.1f}%, 실현변동성 {vol20:.1f}%입니다. 단순 고변동성 주의가 아니라 "
            "신규 발행 조건과 헤지 비용이 동시에 커지는 활황성 변동성 구간으로 해석합니다."
        )
    if regime == "위험회피":
        return (
            f"60일 고점 대비 {dd60:.1f}%이고 {vol_source_label} 기반 변동성 압력이 높습니다. "
            "기존 북의 순연·델타/베가 헤지 부담을 우선 점검할 구간입니다."
        )
    return "가격 모멘텀과 변동성 압력이 상대적으로 안정적입니다. 발행 조건보다 기존 북 관리 부담이 낮은 구간입니다."


def _index_payload(spec: dict) -> dict:
    price = _fetch_yahoo(spec["symbol"], min_rows=320)
    vol, vol_symbol = _fetch_first_available(spec.get("volSymbols", []))
    frame = _features(price, vol)
    columns = ["ret_20d", "realized_vol_20d", "drawdown_60d", "vol_proxy_rank_252d", "vol_proxy_change_20d"]
    clean = frame.dropna(subset=columns + ["drawdown_252d"]).reset_index(drop=True)
    train = clean.tail(FIT_WINDOW).reset_index(drop=True)
    x = _robust_scale(train, columns)
    model, posterior = _fit_hmm(x)
    path = _viterbi(x, model)
    labels = _state_labels(train, path)
    latest = train.iloc[-1]
    latest_posterior = posterior[-1]
    probabilities = {"안정": 0.0, "고변동성 활황": 0.0, "위험회피": 0.0}
    for state, probability in enumerate(latest_posterior):
        probabilities[labels.get(state, "안정")] += float(probability)
    regime = max(probabilities, key=probabilities.get)
    issuer_score = _clamp(probabilities["위험회피"] * 100 + probabilities["고변동성 활황"] * 45)
    vol_source = spec["volLabel"] if vol is not None else "20D 실현변동성"
    series = []
    for row, state, posterior_row in zip(train.to_dict(orient="records"), path, posterior):
        state_probabilities = {"안정": 0.0, "고변동성 활황": 0.0, "위험회피": 0.0}
        for state_index, probability in enumerate(posterior_row):
            state_probabilities[labels.get(state_index, "안정")] += float(probability)
        row_regime = labels.get(int(state), "안정")
        series.append(
            {
                "date": row["date"],
                "regime": row_regime,
                "tone": _tone(row_regime),
                "issuerScore": _round(_clamp(state_probabilities["위험회피"] * 100 + state_probabilities["고변동성 활황"] * 45), 2),
                "riskOffProbabilityPct": _round(state_probabilities["위험회피"] * 100, 2),
                "highVolBullProbabilityPct": _round(state_probabilities["고변동성 활황"] * 100, 2),
                "return20dPct": _round(row["ret_20d"] * 100, 2),
                "realizedVol20dPct": _round(row["realized_vol_20d"] * 100, 2),
            }
        )

    return {
        **{key: spec[key] for key in ["id", "label", "name", "region"]},
        "symbol": spec["symbol"],
        "volSymbol": vol_symbol,
        "volSource": vol_source,
        "lastDate": latest["date"],
        "lastClose": _round(latest["price"], 2),
        "regime": regime,
        "tone": _tone(regime),
        "confidencePct": _round(max(probabilities.values()) * 100, 1),
        "issuerScore": _round(issuer_score, 2),
        "probabilities": {key: _round(value * 100, 2) for key, value in probabilities.items()},
        "metrics": {
            "return5dPct": _round(latest["ret_5d"] * 100, 2),
            "return20dPct": _round(latest["ret_20d"] * 100, 2),
            "realizedVol20dPct": _round(latest["realized_vol_20d"] * 100, 2),
            "realizedVolRank252d": _round(latest["realized_vol_rank_252d"], 1),
            "drawdown60dPct": _round(latest["drawdown_60d"] * 100, 2),
            "drawdown252dPct": _round(latest["drawdown_252d"] * 100, 2),
            "volProxy": _round(latest["vol_proxy"], 2),
            "volProxyRank252d": _round(latest["vol_proxy_rank_252d"], 1),
            "volProxyChange20dPct": _round(latest["vol_proxy_change_20d"] * 100, 2),
        },
        "reading": _reading(regime, latest, probabilities, vol_source),
        "series": series[-SERIES_TAIL:],
    }


def build_payload() -> dict:
    indices = []
    for spec in INDICES:
        indices.append(_index_payload(spec))

    risk_off_count = sum(1 for item in indices if item["regime"] == "위험회피")
    bull_count = sum(1 for item in indices if item["regime"] == "고변동성 활황")
    stable_count = sum(1 for item in indices if item["regime"] == "안정")
    highest_risk = max(indices, key=lambda item: item["probabilities"]["위험회피"])
    highest_issuer = max(indices, key=lambda item: item["issuerScore"])
    average_issuer_score = float(np.mean([item["issuerScore"] for item in indices]))
    if risk_off_count >= 2:
        basket_regime = "위험회피 확산"
        tone = "danger"
    elif bull_count >= 2:
        basket_regime = "고변동성 활황"
        tone = "caution"
    else:
        basket_regime = "분산 안정"
        tone = "good"

    return {
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST"),
        "methodology": (
            "최근 3년 내외 관측치만 사용해 3상태 Gaussian HMM을 적합합니다. 상태는 단순 변동성 크기가 아니라 "
            "20일 수익률, 60일 낙폭, 20일 실현변동성, 국가별 변동성지수 또는 실현변동성 대체값으로 "
            "안정·고변동성 활황·위험회피로 사후 매핑합니다."
        ),
        "designNote": (
            "전 기간 변동성 분위수만 쓰면 최근 KOSPI처럼 상승 모멘텀과 고변동성이 같이 나타나는 장을 항상 주의로 볼 수 있어, "
            "상승 모멘텀과 낙폭을 함께 넣어 발행 기회성 고변동성과 위험회피를 분리합니다."
        ),
        "basket": {
            "regime": basket_regime,
            "tone": tone,
            "riskOffCount": risk_off_count,
            "highVolBullCount": bull_count,
            "stableCount": stable_count,
            "averageIssuerScore": _round(average_issuer_score, 2),
            "highestRiskOffIndex": highest_risk["label"],
            "highestRiskOffProbabilityPct": highest_risk["probabilities"]["위험회피"],
            "highestIssuerScoreIndex": highest_issuer["label"],
            "interpretation": (
                f"{highest_risk['label']}의 위험회피 확률이 가장 높고, {highest_issuer['label']}가 발행/헤지 관점의 "
                "종합 부담을 가장 크게 만듭니다."
            ),
        },
        "indices": indices,
    }


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(build_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote HMM regime: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
