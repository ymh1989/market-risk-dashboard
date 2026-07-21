from __future__ import annotations

import pandas as pd

from kospi_risk.ensemble_lab import (
    EnsembleLabConfig,
    _select_adaptive_weights,
    _target_transformer_profile,
    _weighted_average,
)


def test_adaptive_weights_are_selected_from_prior_oos_only():
    prior = pd.DataFrame(
        {
            "fold": [1, 1, 2, 2, 3, 3],
            "target": [1, 0, 1, 0, 1, 0],
            "rf_probability": [0.9, 0.1, 0.85, 0.15, 0.8, 0.2],
            "transformer_probability": [0.2, 0.8, 0.25, 0.75, 0.3, 0.7],
            "rule_probability": [0.4, 0.6, 0.45, 0.55, 0.5, 0.5],
        }
    )
    default = {"rf": 0.5, "transformer": 0.3, "rule": 0.2}

    weights = _select_adaptive_weights(prior, default, min_weight_selection_folds=3)

    assert weights["rf"] > weights["transformer"]
    assert weights["rf"] > weights["rule"]


def test_adaptive_weights_fall_back_until_enough_prior_folds_exist():
    prior = pd.DataFrame(
        {
            "fold": [1, 1, 2, 2],
            "target": [1, 0, 1, 0],
            "rf_probability": [0.9, 0.1, 0.9, 0.1],
            "transformer_probability": [0.1, 0.9, 0.1, 0.9],
            "rule_probability": [0.5, 0.5, 0.5, 0.5],
        }
    )
    default = {"rf": 0.5, "transformer": 0.3, "rule": 0.2}

    weights = _select_adaptive_weights(prior, default, min_weight_selection_folds=3)

    assert weights == default


def test_adaptive_weights_fall_back_when_prior_event_count_is_too_small():
    prior = pd.DataFrame(
        {
            "fold": [1, 1, 2, 2, 3, 3],
            "target": [1, 0, 0, 0, 0, 0],
            "rf_probability": [0.9, 0.1, 0.2, 0.1, 0.2, 0.1],
            "transformer_probability": [0.8, 0.2, 0.3, 0.2, 0.3, 0.2],
            "rule_probability": [0.4, 0.2, 0.2, 0.2, 0.2, 0.2],
        }
    )
    default = {"rf": 0.5, "transformer": 0.3, "rule": 0.2}

    weights = _select_adaptive_weights(
        prior,
        default,
        min_weight_selection_folds=3,
        min_weight_selection_events=2,
    )

    assert weights == default


def test_weighted_average_renormalizes_missing_transformer_predictions():
    frame = pd.DataFrame(
        {
            "rf_probability": [0.8],
            "transformer_probability": [float("nan")],
            "rule_probability": [0.2],
        }
    )
    weights = {"rf": 0.5, "transformer": 0.3, "rule": 0.2}

    probability = _weighted_average(frame, weights)

    assert probability[0] == (0.8 * 0.5 + 0.2 * 0.2) / 0.7


def test_target_specific_transformer_profile_overrides_shared_defaults():
    config = EnsembleLabConfig(
        pooling="last",
        loss="bce",
        moderate_pooling="attention",
        moderate_loss="focal",
    )

    assert _target_transformer_profile(config, "crash_5d_5pct") == ("attention", "focal")
    assert _target_transformer_profile(config, "crash_5d_10pct") == ("last", "bce")
