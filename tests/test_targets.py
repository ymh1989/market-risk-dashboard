from __future__ import annotations

import numpy as np
import pandas as pd

from kospi_risk.config import load_config
from kospi_risk.data_loader import make_sample_market_data
from kospi_risk.feature_engineering import build_features_from_market_data
from kospi_risk.targets import add_targets


def test_targets_use_strictly_future_rows_and_leave_tail_empty():
    config = load_config()
    raw = make_sample_market_data(rows=120, seed=9)
    features = build_features_from_market_data(raw)
    targeted = add_targets(features, config)

    future_returns = np.log(raw["KOSPI"] / raw["KOSPI"].shift(1)).iloc[11:31]
    expected_vol = future_returns.std(ddof=1) * np.sqrt(252)
    assert np.isclose(targeted.loc[10, "target_vol_20d"], expected_vol)
    assert targeted.tail(20)["target_vol_20d"].isna().all()
    assert targeted.tail(20)["target_regime"].isna().all()
    assert targeted.tail(20)["target_risk_off_20d"].isna().all()
    assert targeted.tail(5)["target_downside_5d"].isna().all()
    assert targeted.tail(20)["target_downside_20d"].isna().all()


def test_target_alignment_on_deterministic_price_series():
    config = load_config()
    raw = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=50),
            "KOSPI": np.linspace(100, 149, 50),
            "SPX": np.linspace(200, 249, 50),
            "SOX": np.linspace(300, 349, 50),
            "USDKRW": np.linspace(1100, 1149, 50),
        }
    )
    targeted = add_targets(build_features_from_market_data(raw), config)
    t = 5
    assert np.isclose(targeted.loc[t, "fwd_ret_20d"], raw.loc[t + 20, "KOSPI"] / raw.loc[t, "KOSPI"] - 1)
    assert np.isclose(targeted.loc[t, "fwd_ret_5d"], raw.loc[t + 5, "KOSPI"] / raw.loc[t, "KOSPI"] - 1)
    future_returns = np.log(raw["KOSPI"] / raw["KOSPI"].shift(1)).iloc[t + 1 : t + 21]
    assert np.isclose(targeted.loc[t, "target_vol_20d"], future_returns.std(ddof=1) * np.sqrt(252))
    assert targeted.tail(20)["target_vol_20d"].isna().all()


def test_outperformance_targets_are_binary_or_nan():
    config = load_config()
    raw = make_sample_market_data(rows=100, seed=10)
    targeted = add_targets(build_features_from_market_data(raw), config)
    values = set(targeted["target_outperform_spx_20d"].dropna().unique())
    assert values <= {0.0, 1.0}
    risk_off_values = set(targeted["target_risk_off_20d"].dropna().unique())
    assert risk_off_values <= {0.0, 1.0}
    assert set(targeted["target_downside_5d"].dropna().unique()) <= {0.0, 1.0}
    assert set(targeted["target_downside_20d"].dropna().unique()) <= {0.0, 1.0}


def test_crash_targets_use_configured_large_declines():
    config = load_config()
    assert config["downside"]["return_threshold_5d"] == -0.03
    assert config["downside"]["return_threshold_20d"] == -0.05

    raw = make_sample_market_data(rows=80, seed=11)
    targeted = add_targets(build_features_from_market_data(raw), config)
    valid_5d = targeted["fwd_ret_5d"].notna()
    valid_20d = targeted["fwd_ret_20d"].notna()
    expected_5d = (targeted.loc[valid_5d, "fwd_ret_5d"] <= -0.03).astype(float)
    expected_20d = (targeted.loc[valid_20d, "fwd_ret_20d"] <= -0.05).astype(float)
    pd.testing.assert_series_equal(targeted.loc[valid_5d, "target_downside_5d"], expected_5d, check_names=False)
    pd.testing.assert_series_equal(targeted.loc[valid_20d, "target_downside_20d"], expected_20d, check_names=False)
