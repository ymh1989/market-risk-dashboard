from __future__ import annotations

import numpy as np
import pandas as pd


REGIME_CLASSES = ["risk-on", "neutral", "risk-off"]


def _future_realized_vol(prices: pd.Series, horizon: int, annualization: int) -> pd.Series:
    log_returns = np.log(prices / prices.shift(1)).to_numpy(dtype=float)
    values = np.full(len(prices), np.nan)
    for idx in range(len(prices) - horizon):
        window = log_returns[idx + 1 : idx + horizon + 1]
        if np.isfinite(window).sum() == horizon:
            values[idx] = np.nanstd(window, ddof=1) * np.sqrt(annualization)
    return pd.Series(values, index=prices.index)


def _future_max_drawdown(prices: pd.Series, horizon: int) -> pd.Series:
    arr = prices.to_numpy(dtype=float)
    values = np.full(len(prices), np.nan)
    for idx in range(len(prices) - horizon):
        path = arr[idx + 1 : idx + horizon + 1]
        if np.isfinite(path).sum() != horizon:
            continue
        peaks = np.maximum.accumulate(path)
        drawdowns = path / peaks - 1
        values[idx] = np.nanmin(drawdowns)
    return pd.Series(values, index=prices.index)


def _future_min_return(prices: pd.Series, horizon: int) -> pd.Series:
    future_prices = pd.concat([prices.shift(-offset) for offset in range(1, horizon + 1)], axis=1)
    return future_prices.min(axis=1, skipna=False) / prices - 1


def add_targets(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = df.copy()
    horizon = int(config.get("horizon", 20))
    annualization = int(config.get("annualization_factor", 252))
    regime_config = config.get("regime", {})
    crash_config = config.get("crash", {})
    crash_horizon = int(crash_config.get("horizon_days", 5))

    out["target_vol_20d"] = _future_realized_vol(out["KOSPI"], horizon, annualization)
    out["fwd_ret_5d"] = out["KOSPI"].shift(-5) / out["KOSPI"] - 1
    out["fwd_min_ret_5d"] = _future_min_return(out["KOSPI"], crash_horizon)
    out["fwd_ret_20d"] = out["KOSPI"].shift(-horizon) / out["KOSPI"] - 1
    out["fwd_max_drawdown_20d"] = _future_max_drawdown(out["KOSPI"], horizon)
    out["target_outperform_spx_20d"] = (out["fwd_ret_20d"] > (out["SPX"].shift(-horizon) / out["SPX"] - 1)).astype("float")
    out["target_outperform_sox_20d"] = (out["fwd_ret_20d"] > (out["SOX"].shift(-horizon) / out["SOX"] - 1)).astype("float")

    invalid_future = out["fwd_ret_20d"].isna()
    out.loc[invalid_future, ["target_outperform_spx_20d", "target_outperform_sox_20d"]] = np.nan

    out["target_crash_5d_5pct"] = (
        out["fwd_min_ret_5d"] <= float(crash_config.get("moderate_threshold", -0.05))
    ).astype("float")
    out["target_crash_5d_10pct"] = (
        out["fwd_min_ret_5d"] <= float(crash_config.get("severe_threshold", -0.10))
    ).astype("float")
    invalid_crash_future = out["fwd_min_ret_5d"].isna()
    out.loc[invalid_crash_future, ["target_crash_5d_5pct", "target_crash_5d_10pct"]] = np.nan

    min_history = int(regime_config.get("min_history_for_vol_percentile", 60))
    vol_threshold = (
        out["target_vol_20d"]
        .rolling(window=max(min_history, 1), min_periods=min_history)
        .quantile(float(regime_config.get("vol_top_percentile", 0.75)))
        .shift(1)
    )
    high_future_vol = out["target_vol_20d"] >= vol_threshold
    high_future_vol = high_future_vol.fillna(False)

    risk_off = (
        (out["fwd_ret_20d"] <= float(regime_config.get("risk_off_return_threshold", -0.04)))
        | (out["fwd_max_drawdown_20d"] <= float(regime_config.get("risk_off_drawdown_threshold", -0.06)))
        | high_future_vol
    )
    risk_on = (
        (out["fwd_ret_20d"] >= float(regime_config.get("risk_on_return_threshold", 0.04)))
        & (out["fwd_max_drawdown_20d"] > float(regime_config.get("risk_on_drawdown_threshold", -0.04)))
        & (~high_future_vol)
    )
    out["target_regime"] = np.select([risk_off, risk_on], ["risk-off", "risk-on"], default="neutral")
    out.loc[invalid_future | out["target_vol_20d"].isna(), "target_regime"] = np.nan
    out["target_risk_off_20d"] = (out["target_regime"] == "risk-off").astype("float")
    out.loc[out["target_regime"].isna(), "target_risk_off_20d"] = np.nan
    return out


def target_columns() -> list[str]:
    return [
        "target_vol_20d",
        "target_regime",
        "target_risk_off_20d",
        "target_crash_5d_5pct",
        "target_crash_5d_10pct",
        "target_outperform_spx_20d",
        "target_outperform_sox_20d",
    ]
