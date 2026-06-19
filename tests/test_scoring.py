from __future__ import annotations

from kospi_risk.config import load_config
from kospi_risk.data_loader import make_sample_market_data
from kospi_risk.feature_engineering import build_features_from_market_data
from kospi_risk.models import predict_bundle, train_bundle
from kospi_risk.scoring import add_els_scores
from kospi_risk.targets import add_targets


def test_probability_and_score_ranges_are_bounded():
    config = load_config()
    config["models"]["rf_estimators"] = 10
    config["models"]["calibration_enabled"] = False
    raw = make_sample_market_data(rows=650, seed=12)
    df = add_targets(build_features_from_market_data(raw), config)
    bundle = train_bundle(df, config)
    sample = df.dropna(subset=["target_vol_20d"]).tail(20).reset_index(drop=True)
    predictions = predict_bundle(bundle, sample)
    scored = add_els_scores(predictions, sample, bundle.predicted_vol_history)

    probability_columns = [column for column in scored.columns if column.startswith("prob_")]
    for column in probability_columns:
        assert scored[column].between(0, 1).all()
    assert scored["els_risk_score"].between(0, 100).all()


def test_training_is_reproducible_with_fixed_random_state():
    config = load_config()
    config["models"]["rf_estimators"] = 10
    config["models"]["calibration_enabled"] = False
    raw = make_sample_market_data(rows=650, seed=13)
    df = add_targets(build_features_from_market_data(raw), config)
    first = predict_bundle(train_bundle(df, config), df.dropna(subset=["target_vol_20d"]).tail(5).reset_index(drop=True))
    second = predict_bundle(train_bundle(df, config), df.dropna(subset=["target_vol_20d"]).tail(5).reset_index(drop=True))
    assert first["pred_vol_20d"].round(10).equals(second["pred_vol_20d"].round(10))
