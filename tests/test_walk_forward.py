from __future__ import annotations

import pandas as pd

from kospi_risk.config import load_config
from kospi_risk.data_loader import make_sample_market_data
from kospi_risk.feature_engineering import build_features_from_market_data
from kospi_risk.models import eligible_training_frame
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


def test_walk_forward_cache_reuses_identical_folds(tmp_path):
    config = small_config()
    config["validation"]["max_backtest_folds"] = 3
    raw = make_sample_market_data(rows=720, seed=21)
    df = add_targets(build_features_from_market_data(raw), config)
    cache_path = tmp_path / "walk_forward.joblib"

    first_scored, first_metrics, _ = run_walk_forward_backtest(df, config, cache_path=cache_path)
    first_crash, first_crash_metrics = run_crash_walk_forward_backtest(df, config, cache_path=cache_path)
    second_scored, second_metrics, _ = run_walk_forward_backtest(df, config, cache_path=cache_path)
    second_crash, second_crash_metrics = run_crash_walk_forward_backtest(df, config, cache_path=cache_path)

    assert first_metrics.attrs["cache"] == {
        "enabled": True,
        "hits": 0,
        "misses": 3,
        "folds": 3,
        "refreshed": False,
    }
    assert second_metrics.attrs["cache"]["hits"] == 3
    assert second_metrics.attrs["cache"]["misses"] == 0
    assert first_crash_metrics.attrs["cache"]["misses"] == 3
    assert second_crash_metrics.attrs["cache"]["hits"] == 3
    pd.testing.assert_frame_equal(first_scored, second_scored)
    pd.testing.assert_frame_equal(first_crash, second_crash)


def test_walk_forward_cache_retrains_only_changed_tail_fold(tmp_path):
    config = small_config()
    config["validation"]["max_backtest_folds"] = 3
    raw = make_sample_market_data(rows=720, seed=22)
    df = eligible_training_frame(add_targets(build_features_from_market_data(raw), config))
    cache_path = tmp_path / "walk_forward.joblib"

    _, initial_metrics, _ = run_walk_forward_backtest(df, config, cache_path=cache_path)
    added_row = df.tail(1).copy()
    added_row["date"] = pd.to_datetime(df["date"].max()) + pd.offsets.BDay(1)
    extended = pd.concat([df, added_row], ignore_index=True)
    _, extended_metrics, _ = run_walk_forward_backtest(extended, config, cache_path=cache_path)

    assert initial_metrics.attrs["cache"]["misses"] == 3
    assert extended_metrics.attrs["cache"]["hits"] == 2
    assert extended_metrics.attrs["cache"]["misses"] == 1


def test_walk_forward_cache_invalidates_historical_revision(tmp_path):
    config = small_config()
    config["validation"]["max_backtest_folds"] = 3
    raw = make_sample_market_data(rows=720, seed=23)
    df = eligible_training_frame(add_targets(build_features_from_market_data(raw), config))
    cache_path = tmp_path / "walk_forward.joblib"

    run_walk_forward_backtest(df, config, cache_path=cache_path)
    revised = df.copy()
    revised.loc[100, "kospi_log_ret_5d"] = float(revised.loc[100, "kospi_log_ret_5d"]) + 0.001
    _, revised_metrics, _ = run_walk_forward_backtest(revised, config, cache_path=cache_path)

    assert revised_metrics.attrs["cache"]["hits"] == 0
    assert revised_metrics.attrs["cache"]["misses"] == 3
