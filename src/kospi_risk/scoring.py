from __future__ import annotations

import numpy as np
import pandas as pd


def clip01(value: float) -> float:
    if pd.isna(value):
        return 0.5
    return float(np.clip(value, 0, 1))


def percentile_rank(value: float, history: list[float] | pd.Series | np.ndarray) -> float:
    arr = np.asarray(history, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0 or not np.isfinite(value):
        return 0.5
    return float(np.mean(arr <= value))


def _scale_negative(value: float, threshold: float) -> float:
    if pd.isna(value):
        return 0.5
    return float(np.clip(-value / abs(threshold), 0, 1))


def _scale_positive(value: float, threshold: float) -> float:
    if pd.isna(value):
        return 0.5
    return float(np.clip(value / abs(threshold), 0, 1))


def downside_momentum_score(row: pd.Series) -> float:
    components = [
        _scale_negative(row.get("kospi_log_ret_5d"), 0.03),
        _scale_negative(row.get("kospi_log_ret_20d"), 0.08),
        _scale_negative(row.get("kospi_dist_high_60d"), 0.12),
    ]
    return clip01(np.nanmean(components))


def fx_pressure_score(row: pd.Series) -> float:
    components = [
        _scale_positive(row.get("usdkrw_log_ret_5d"), 0.025),
        _scale_positive(row.get("usdkrw_log_ret_20d"), 0.06),
        _scale_positive(row.get("usdkrw_vol_20d"), 0.18),
    ]
    return clip01(np.nanmean(components))


def global_risk_score(row: pd.Series) -> float:
    components = [
        _scale_negative(row.get("spx_log_ret_20d"), 0.08),
        _scale_negative(row.get("sox_log_ret_20d"), 0.12),
    ]
    if "nasdaq_log_ret_20d" in row.index:
        components.append(_scale_negative(row.get("nasdaq_log_ret_20d"), 0.1))
    if "vix_level" in row.index:
        components.append(_scale_positive(row.get("vix_level", np.nan) - 20, 20))
    return clip01(np.nanmean(components))


def liquidity_or_basis_stress_score(row: pd.Series) -> float:
    components = []
    if "kospi_futures_basis_level" in row.index:
        components.append(_scale_negative(row.get("kospi_futures_basis_level"), 1.5))
    if "kospi_put_call_ratio_level" in row.index:
        components.append(_scale_positive(row.get("kospi_put_call_ratio_level", np.nan) - 1.0, 0.6))
    if "kospi_skew_level" in row.index:
        components.append(_scale_positive(row.get("kospi_skew_level", np.nan), 0.08))
    if "foreigner_futures_net_buy_sum_5d" in row.index:
        components.append(_scale_negative(row.get("foreigner_futures_net_buy_sum_5d"), 12000))
    if not components:
        return 0.5
    return clip01(np.nanmean(components))


def score_bucket(score: float) -> str:
    if score < 30:
        return "우호 / 낮은 위험"
    if score < 60:
        return "중립 / 정상 모니터링"
    if score < 80:
        return "위험 상승"
    return "스트레스 / 발행 주의"


def add_els_scores(predictions: pd.DataFrame, features: pd.DataFrame, predicted_vol_history: list[float]) -> pd.DataFrame:
    out = predictions.copy()
    rows = []
    for idx, row in features.reset_index(drop=True).iterrows():
        pred = out.iloc[idx]
        underperformance = 1 - np.nanmean(
            [
                pred.get("prob_kospi_outperform_spx_20d", 0.5),
                pred.get("prob_kospi_outperform_sox_20d", 0.5),
            ]
        )
        components = {
            "predicted_probability_risk_off": clip01(pred.get("prob_risk_off", 0.5)),
            "predicted_vol_percentile": clip01(percentile_rank(pred.get("pred_vol_20d"), predicted_vol_history)),
            "downside_momentum_score": downside_momentum_score(row),
            "fx_pressure_score": fx_pressure_score(row),
            "global_risk_score": global_risk_score(row),
            "kospi_underperformance_score": clip01(underperformance),
            "liquidity_or_basis_stress_score": liquidity_or_basis_stress_score(row),
        }
        score = (
            30 * components["predicted_probability_risk_off"]
            + 20 * components["predicted_vol_percentile"]
            + 15 * components["downside_momentum_score"]
            + 10 * components["fx_pressure_score"]
            + 10 * components["global_risk_score"]
            + 10 * components["kospi_underperformance_score"]
            + 5 * components["liquidity_or_basis_stress_score"]
        )
        components["els_risk_score"] = float(np.clip(score, 0, 100))
        components["els_risk_bucket"] = score_bucket(components["els_risk_score"])
        rows.append(components)
    scored = pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    return scored


def score_bucket_analysis(scored: pd.DataFrame) -> pd.DataFrame:
    frame = scored.copy()
    frame["score_bucket"] = pd.cut(frame["els_risk_score"], bins=[-0.01, 30, 60, 80, 100], labels=["0-30", "30-60", "60-80", "80-100"])
    return (
        frame.groupby("score_bucket", observed=False)
        .agg(
            observations=("els_risk_score", "size"),
            average_score=("els_risk_score", "mean"),
            average_next_20d_kospi_return=("fwd_ret_20d", "mean"),
            median_next_20d_kospi_return=("fwd_ret_20d", "median"),
            average_realized_vol=("target_vol_20d", "mean"),
            risk_off_label_frequency=("target_regime", lambda x: float(np.mean(x == "risk-off")) if len(x) else np.nan),
            average_drawdown=("fwd_max_drawdown_20d", "mean"),
            worst_drawdown=("fwd_max_drawdown_20d", "min"),
        )
        .reset_index()
    )
