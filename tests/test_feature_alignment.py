from __future__ import annotations

import numpy as np
import pandas as pd

from kospi_risk.data_loader import make_sample_market_data
from kospi_risk.feature_engineering import build_features_from_market_data


def test_features_use_only_current_and_past_rows():
    raw = make_sample_market_data(rows=180, seed=7)
    baseline = build_features_from_market_data(raw)
    changed_future = raw.copy()
    changed_future.loc[120:, "KOSPI"] *= 1.5
    mutated = build_features_from_market_data(changed_future)

    check_columns = ["kospi_log_ret_20d", "kospi_realized_vol_20d", "kospi_dist_high_60d", "kospi_ma_dist_60d"]
    for column in check_columns:
        assert np.isclose(baseline.loc[100, column], mutated.loc[100, column], equal_nan=True)


def test_log_return_feature_matches_price_history():
    raw = make_sample_market_data(rows=80, seed=8)
    features = build_features_from_market_data(raw)
    expected = np.log(raw.loc[40, "KOSPI"] / raw.loc[20, "KOSPI"])
    assert np.isclose(features.loc[40, "kospi_log_ret_20d"], expected)


def test_rolling_feature_alignment_on_deterministic_series():
    raw = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=80),
            "KOSPI": np.exp(np.linspace(0, 0.79, 80)) * 100,
            "SPX": np.exp(np.linspace(0, 0.40, 80)) * 200,
            "SOX": np.exp(np.linspace(0, 0.55, 80)) * 300,
            "USDKRW": np.exp(np.linspace(0, 0.08, 80)) * 1100,
        }
    )
    features = build_features_from_market_data(raw)
    t = 65
    returns = np.log(raw["KOSPI"] / raw["KOSPI"].shift(1))
    expected_vol = returns.iloc[t - 19 : t + 1].std(ddof=1) * np.sqrt(252)
    expected_high_distance = raw.loc[t, "KOSPI"] / raw.loc[t - 59 : t, "KOSPI"].max() - 1
    expected_return = np.log(raw.loc[t, "KOSPI"] / raw.loc[t - 20, "KOSPI"])

    assert np.isclose(features.loc[t, "kospi_realized_vol_20d"], expected_vol)
    assert np.isclose(features.loc[t, "kospi_dist_high_60d"], expected_high_distance)
    assert np.isclose(features.loc[t, "kospi_log_ret_20d"], expected_return)
