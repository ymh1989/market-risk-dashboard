from __future__ import annotations

import numpy as np
import pandas as pd

from kospi_risk.transformer_lab import (
    TransformerLabConfig,
    _fold_seed,
    _prior_oos_sigmoid_calibration,
    _purged_train_end,
    _select_feature_columns,
)
from kospi_risk.validation import WalkForwardSplit


def test_spearman_feature_selection_uses_training_frame_only():
    train = pd.DataFrame(
        {
            "good_signal": [0, 0, 0, 1, 1, 1] * 10,
            "noise": np.tile([0.2, -0.1, 0.4, -0.3, 0.0, 0.1], 10),
            "target_crash_5d_5pct": [0, 0, 0, 1, 1, 1] * 10,
        }
    )
    config = TransformerLabConfig(feature_selection="spearman", max_features=1)

    selected = _select_feature_columns(train, ["noise", "good_signal"], "target_crash_5d_5pct", config)

    assert selected == ["good_signal"]


def test_feature_selection_defaults_to_all_features():
    train = pd.DataFrame(
        {
            "first": [1, 2, 3],
            "second": [3, 2, 1],
            "target_crash_5d_5pct": [0, 1, 0],
        }
    )
    config = TransformerLabConfig()

    selected = _select_feature_columns(train, ["first", "second"], "target_crash_5d_5pct", config)

    assert selected == ["first", "second"]


def test_five_day_target_boundary_is_purged_before_test_fold():
    split = WalkForwardSplit(train_start=0, train_end=100, test_start=100, test_end=121)

    assert _purged_train_end(split, purge_days=5) == 95


def test_fold_member_seeds_are_reproducible_and_distinct():
    first = _fold_seed(42, fold=3, target="crash_5d_5pct", member=0)
    repeated = _fold_seed(42, fold=3, target="crash_5d_5pct", member=0)
    another_member = _fold_seed(42, fold=3, target="crash_5d_5pct", member=1)

    assert first == repeated
    assert first != another_member


def test_prior_oos_calibration_falls_back_until_enough_events_exist():
    current = np.array([0.2, 0.8])
    prior = pd.DataFrame(
        {
            "fold": [1, 1, 2, 2],
            "target": [0, 0, 1, 0],
            "raw_probability": [0.1, 0.2, 0.7, 0.3],
        }
    )

    calibrated, source = _prior_oos_sigmoid_calibration(current, prior, min_folds=2, min_events=2)

    assert source == "raw"
    np.testing.assert_allclose(calibrated, current)


def test_prior_oos_calibration_never_reverses_risk_ranking():
    current = np.array([0.2, 0.8])
    prior = pd.DataFrame(
        {
            "fold": [1, 1, 2, 2, 3, 3, 4, 4],
            "target": [1, 0, 1, 0, 1, 0, 1, 0],
            "raw_probability": [0.1, 0.9, 0.15, 0.85, 0.2, 0.8, 0.25, 0.75],
        }
    )

    calibrated, source = _prior_oos_sigmoid_calibration(current, prior, min_folds=4, min_events=4)

    assert source == "raw_non_monotonic_guard"
    np.testing.assert_allclose(calibrated, current)
