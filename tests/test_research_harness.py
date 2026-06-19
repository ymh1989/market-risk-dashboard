from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from kospi_risk.config import load_config
from kospi_risk.feature_engineering import build_features_from_market_data
from kospi_risk.models import train_bundle
from kospi_risk.targets import add_targets
from kospi_risk.validation import run_walk_forward_backtest


def harness_config():
    config = load_config()
    config["validation"]["initial_train_days"] = 560
    config["validation"]["test_days"] = 80
    config["validation"]["step_days"] = 80
    config["models"]["rf_estimators"] = 8
    config["models"]["calibration_enabled"] = False
    return config


def synthetic_unpredictable_market(rows: int = 760, seed: int = 123) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=rows)
    hidden_future_shocks = rng.choice([-0.012, 0.012], size=rows)
    kospi = 2500 * np.exp(np.cumsum(hidden_future_shocks))
    spx = 3200 * np.exp(np.cumsum(rng.normal(0.0001, 0.008, rows)))
    sox = 1800 * np.exp(np.cumsum(rng.normal(0.0001, 0.012, rows)))
    usdkrw = 1150 * np.exp(np.cumsum(rng.normal(0.0, 0.004, rows)))
    return pd.DataFrame({"date": dates, "KOSPI": kospi, "SPX": spx, "SOX": sox, "USDKRW": usdkrw})


def test_leakage_harness_blocks_unrealistically_perfect_performance():
    config = harness_config()
    df = add_targets(build_features_from_market_data(synthetic_unpredictable_market()), config)
    scored, metrics, _ = run_walk_forward_backtest(df, config)

    regime_accuracy = metrics[
        (metrics["model"] == "ml_selected") & (metrics["task"] == "regime") & (metrics["metric"] == "accuracy")
    ]["value"].iloc[0]
    spx_auc = metrics[
        (metrics["model"] == "ml_selected") & (metrics["task"] == "outperform_spx") & (metrics["metric"] == "auc")
    ]["value"].iloc[0]

    assert len(scored) > 0
    assert regime_accuracy < 0.95
    assert np.isnan(spx_auc) or spx_auc < 0.95


def test_training_fails_with_fewer_than_500_observations():
    config = load_config()
    raw = synthetic_unpredictable_market(rows=510, seed=321)
    df = add_targets(build_features_from_market_data(raw), config)
    with pytest.raises(ValueError, match="Insufficient training history"):
        train_bundle(df, config)


def test_model_bundle_uses_sklearn_pipelines_for_preprocessing():
    config = harness_config()
    df = add_targets(build_features_from_market_data(synthetic_unpredictable_market(rows=650, seed=456)), config)
    bundle = train_bundle(df, config)
    assert isinstance(bundle.vol_model, Pipeline)
    assert "imputer" in bundle.vol_model.named_steps
    assert bundle.selected_models["vol"] in {"ridge", "random_forest", "lightgbm"}
