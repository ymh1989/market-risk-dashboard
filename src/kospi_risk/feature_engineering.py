from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loader import load_market_data


CORE_MARKETS = ["KOSPI", "SPX", "SOX", "USDKRW"]
OPTIONAL_RETURN_COLUMNS = ["WTI", "COPPER", "GOLD", "NIKKEI225", "HSCEI", "CSI300"]


def _log_return(series: pd.Series, window: int = 1) -> pd.Series:
    current = series.where(series > 0)
    previous = series.shift(window).where(series.shift(window) > 0)
    return np.log(current / previous)


def _realized_vol(log_returns: pd.Series, window: int, annualization: int = 252) -> pd.Series:
    return log_returns.rolling(window=window, min_periods=window).std() * np.sqrt(annualization)


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

    for column in ["US10Y", "US2Y", "KR10Y"]:
        if column in out.columns:
            out[f"{column.lower()}_chg_5d"] = out[column] - out[column].shift(5)
            out[f"{column.lower()}_chg_20d"] = out[column] - out[column].shift(20)

    for column in OPTIONAL_RETURN_COLUMNS:
        if column in out.columns:
            for window in [5, 20]:
                out[f"{column.lower()}_log_ret_{window}d"] = _log_return(out[column], window)

    return out


def build_features(input_path: str, annualization: int = 252) -> pd.DataFrame:
    return build_features_from_market_data(load_market_data(input_path), annualization=annualization)
