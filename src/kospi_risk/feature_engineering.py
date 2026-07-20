from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loader import load_market_data


CORE_MARKETS = ["KOSPI", "SPX", "SOX", "USDKRW"]
OPTIONAL_RETURN_COLUMNS = ["NASDAQ", "WTI", "COPPER", "GOLD", "NIKKEI225", "HSCEI", "CSI300"]


def _log_return(series: pd.Series, window: int = 1) -> pd.Series:
    current = series.where(series > 0)
    previous = series.shift(window).where(series.shift(window) > 0)
    return np.log(current / previous)


def _realized_vol(log_returns: pd.Series, window: int, annualization: int = 252) -> pd.Series:
    return log_returns.rolling(window=window, min_periods=window).std() * np.sqrt(annualization)


def _positive_part(series: pd.Series) -> pd.Series:
    return series.clip(lower=0)


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def _rolling_zscore(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    rolling = series.rolling(window=window, min_periods=min_periods)
    return (series - rolling.mean()) / rolling.std().replace(0, np.nan)


def build_features_from_market_data(df: pd.DataFrame, annualization: int = 252) -> pd.DataFrame:
    out = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True).copy()
    log_returns: dict[str, pd.Series] = {}

    for market in CORE_MARKETS:
        log_returns[market] = _log_return(out[market])
        for window in [1, 5, 20, 60]:
            out[f"{market.lower()}_log_ret_{window}d"] = _log_return(out[market], window)

    for market, windows in {"KOSPI": [5, 20, 60], "SPX": [20], "SOX": [20], "USDKRW": [20]}.items():
        for window in windows:
            out[f"{market.lower()}_realized_vol_{window}d"] = _realized_vol(log_returns[market], window, annualization)

    out["kospi_spx_corr_60d"] = log_returns["KOSPI"].rolling(60, min_periods=60).corr(log_returns["SPX"])
    out["kospi_sox_corr_60d"] = log_returns["KOSPI"].rolling(60, min_periods=60).corr(log_returns["SOX"])
    out["kospi_usdkrw_corr_60d"] = log_returns["KOSPI"].rolling(60, min_periods=60).corr(log_returns["USDKRW"])

    for window in [20, 60]:
        out[f"kospi_dist_high_{window}d"] = out["KOSPI"] / out["KOSPI"].rolling(window, min_periods=window).max() - 1
    for window in [20, 60, 120]:
        out[f"kospi_ma_dist_{window}d"] = out["KOSPI"] / out["KOSPI"].rolling(window, min_periods=window).mean() - 1

    vol_threshold_252d = out["kospi_realized_vol_20d"].rolling(252, min_periods=60).quantile(0.75).shift(1)
    high_vol = out["kospi_realized_vol_20d"] >= vol_threshold_252d
    weak_momentum = out["kospi_log_ret_20d"] <= -0.04
    strong_momentum = out["kospi_log_ret_20d"] >= 0.04
    out["kospi_momentum_vol_ratio_20d"] = out["kospi_log_ret_20d"] / out["kospi_realized_vol_20d"].replace(0, np.nan)
    out["baseline_high_vol_signal"] = high_vol.astype(float)
    out["baseline_weak_momentum_signal"] = weak_momentum.astype(float)
    out["baseline_risk_off_signal"] = (weak_momentum | high_vol).astype(float)
    out["baseline_risk_on_signal"] = (strong_momentum & (~high_vol)).astype(float)
    out["baseline_regime_score"] = np.select(
        [out["baseline_risk_off_signal"].eq(1), out["baseline_risk_on_signal"].eq(1)],
        [1.0, 0.0],
        default=0.5,
    )

    out["kospi_minus_spx_ret_20d"] = out["kospi_log_ret_20d"] - out["spx_log_ret_20d"]
    out["kospi_minus_sox_ret_20d"] = out["kospi_log_ret_20d"] - out["sox_log_ret_20d"]
    out["usdkrw_vol_20d"] = out["usdkrw_realized_vol_20d"]
    out["sox_minus_spx_ret_20d"] = out["sox_log_ret_20d"] - out["spx_log_ret_20d"]
    out["kospi_vol_spx_vol_ratio_20d"] = _safe_ratio(out["kospi_realized_vol_20d"], out["spx_realized_vol_20d"])
    out["kospi_vol_sox_vol_ratio_20d"] = _safe_ratio(out["kospi_realized_vol_20d"], out["sox_realized_vol_20d"])

    for column in ["VIX", "VKOSPI", "KOSPI_ATM_IV", "KOSPI_SKEW", "KOSPI_PUT_CALL_RATIO", "KOSPI_FUTURES_BASIS", "CREDIT_SPREAD_KR"]:
        if column in out.columns:
            key = column.lower()
            out[f"{key}_level"] = out[column]
            out[f"{key}_chg_5d"] = out[column] - out[column].shift(5)
            out[f"{key}_chg_20d"] = out[column] - out[column].shift(20)

    for column in ["FOREIGNER_KOSPI_NET_BUY", "FOREIGNER_FUTURES_NET_BUY", "INSTITUTION_KOSPI_NET_BUY"]:
        if column in out.columns:
            key = column.lower()
            out[f"{key}_sum_5d"] = out[column].rolling(5, min_periods=5).sum()
            out[f"{key}_sum_20d"] = out[column].rolling(20, min_periods=20).sum()

    for column in ["US10Y", "US2Y", "KR10Y", "US_YIELD_CURVE_10Y2Y"]:
        if column in out.columns:
            key = column.lower()
            out[f"{key}_level"] = out[column]
            out[f"{key}_chg_5d"] = out[column] - out[column].shift(5)
            out[f"{key}_chg_20d"] = out[column] - out[column].shift(20)
            out[f"{key}_z_252d"] = _rolling_zscore(out[column])

    for column in ["US_HIGH_YIELD_OAS", "US_FINANCIAL_STRESS_STLFSI", "US_FINANCIAL_CONDITIONS_NFCI"]:
        if column in out.columns:
            key = column.lower()
            out[f"{key}_level"] = out[column]
            out[f"{key}_chg_5d"] = out[column] - out[column].shift(5)
            out[f"{key}_chg_20d"] = out[column] - out[column].shift(20)
            out[f"{key}_z_252d"] = _rolling_zscore(out[column])

    for column in OPTIONAL_RETURN_COLUMNS:
        if column in out.columns:
            for window in [5, 20]:
                out[f"{column.lower()}_log_ret_{window}d"] = _log_return(out[column], window)

    if "nasdaq_log_ret_20d" in out.columns:
        out["kospi_minus_nasdaq_ret_20d"] = out["kospi_log_ret_20d"] - out["nasdaq_log_ret_20d"]
    if "nikkei225_log_ret_20d" in out.columns:
        out["kospi_minus_nikkei225_ret_20d"] = out["kospi_log_ret_20d"] - out["nikkei225_log_ret_20d"]
    if "csi300_log_ret_20d" in out.columns:
        out["kospi_minus_csi300_ret_20d"] = out["kospi_log_ret_20d"] - out["csi300_log_ret_20d"]

    global_growth_columns = [
        column
        for column in ["spx_log_ret_20d", "sox_log_ret_20d", "nasdaq_log_ret_20d", "nikkei225_log_ret_20d"]
        if column in out.columns
    ]
    if global_growth_columns:
        out["global_growth_log_ret_20d"] = out[global_growth_columns].mean(axis=1)
        out["kospi_minus_global_growth_ret_20d"] = out["kospi_log_ret_20d"] - out["global_growth_log_ret_20d"]

    kospi_down_20d = _positive_part(-out["kospi_log_ret_20d"])
    sox_down_20d = _positive_part(-out["sox_log_ret_20d"])
    usdkrw_up_20d = _positive_part(out["usdkrw_log_ret_20d"])
    out["usdkrw_up_kospi_down_stress_20d"] = usdkrw_up_20d * kospi_down_20d
    out["sox_down_kospi_down_stress_20d"] = sox_down_20d * kospi_down_20d
    out["kospi_high_vol_weak_momentum_20d"] = out["kospi_realized_vol_20d"] * kospi_down_20d
    if "vix_chg_20d" in out.columns:
        vix_up_20d = _positive_part(out["vix_chg_20d"])
        out["vix_up_kospi_down_stress_20d"] = vix_up_20d * kospi_down_20d
        out["vix_up_sox_down_stress_20d"] = vix_up_20d * sox_down_20d
    if "gold_log_ret_20d" in out.columns and "copper_log_ret_20d" in out.columns:
        out["gold_minus_copper_ret_20d"] = out["gold_log_ret_20d"] - out["copper_log_ret_20d"]
    if "wti_log_ret_20d" in out.columns and "usdkrw_log_ret_20d" in out.columns:
        out["oil_up_usdkrw_up_stress_20d"] = _positive_part(out["wti_log_ret_20d"]) * usdkrw_up_20d
    if "us_high_yield_oas_chg_20d" in out.columns:
        out["us_credit_tightening_kospi_down_stress_20d"] = (
            _positive_part(out["us_high_yield_oas_chg_20d"]) * kospi_down_20d
        )
    financial_tightening_columns = [
        column
        for column in ["us_financial_stress_stlfsi_chg_20d", "us_financial_conditions_nfci_chg_20d"]
        if column in out.columns
    ]
    if financial_tightening_columns:
        tightening = out[financial_tightening_columns].apply(_positive_part).mean(axis=1)
        out["us_financial_tightening_kospi_down_stress_20d"] = tightening * kospi_down_20d

    return out


def build_features(input_path: str, annualization: int = 252) -> pd.DataFrame:
    return build_features_from_market_data(load_market_data(input_path), annualization=annualization)
