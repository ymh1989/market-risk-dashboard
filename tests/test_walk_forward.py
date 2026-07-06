from __future__ import annotations

from kospi_risk.config import load_config
from kospi_risk.data_loader import make_sample_market_data
from kospi_risk.feature_engineering import build_features_from_market_data
from kospi_risk.targets import add_targets
from kospi_risk.validation import make_walk_forward_splits, run_crash_walk_forward_backtest, run_walk_forward_backtest


def small_config():
    config = load_config()
    config["validation"]["initial_train_days"] = 520
    config["validation"]["test_days"] = 40
    config["validation"]["step_days"] = 40
    config["models"]["rf_estimators"] = 10
    config["models"]["calibration_enabled"] = False
    return config


def test_walk_forward_splits_do_not_overlap_train_and_test():
    splits = make_walk_forward_splits(720, small_config())
    assert splits
    for split in splits:
        assert split.train_end == split.test_start
        assert split.train_end <= split.test_end


def test_walk_forward_backtest_outputs_metrics():
    config = small_config()
    raw = make_sample_market_data(rows=720, seed=11)
    df = add_targets(build_features_from_market_data(raw), config)
    scored, metrics, matrices = run_walk_forward_backtest(df, config)
    assert not scored.empty
    assert {"model", "task", "metric", "value"} <= set(metrics.columns)
    assert "risk_off_binary" in set(metrics["task"])
    assert "ml_selected_regime_confusion_matrix" in matrices
    assert "baseline_regime_confusion_matrix" in matrices
    splits = metrics.attrs["splits"]
    selection = metrics.attrs["model_selection"]
    assert (splits["train_end_date"] < splits["test_start_date"]).all()
    assert "regime_strategy" in set(selection["task"])


def test_crash_walk_forward_uses_five_day_target_tail():
    config = small_config()
    raw = make_sample_market_data(rows=720, seed=12)
    df = add_targets(build_features_from_market_data(raw), config)
    broad_scored, _, _ = run_walk_forward_backtest(df, config)
    crash_scored, crash_metrics = run_crash_walk_forward_backtest(df, config)

    expected_end = df.loc[df["target_crash_5d_5pct"].notna(), "date"].max()
    assert crash_scored["date"].max() == expected_end
    assert crash_scored["date"].max() > broad_scored["date"].max()
    assert {"crash_5d_5pct", "crash_5d_10pct"} == set(crash_metrics["task"])
    assert (crash_metrics.attrs["splits"]["train_end_date"] < crash_metrics.attrs["splits"]["test_start_date"]).all()
