from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is listed as a dependency.
    yaml = None


DEFAULT_CONFIG: dict[str, Any] = {
    "random_state": 42,
    "horizon": 20,
    "annualization_factor": 252,
    "paths": {
        "model_bundle": "models/model_bundle.joblib",
        "metrics": "reports/model_metrics.csv",
        "score_bucket_analysis": "reports/score_bucket_analysis.csv",
        "walk_forward_predictions": "reports/walk_forward_predictions.csv",
    },
        "validation": {
            "initial_train_days": 1260,
            "test_days": 21,
            "step_days": 21,
            "max_backtest_folds": 84,
            "min_train_rows": 500,
            "reliability_warning_rows": 1500,
            "model_selection_fraction": 0.2,
            "model_selection_min_rows": 126,
        },
    "regime": {
        "risk_off_return_threshold": -0.04,
        "risk_off_drawdown_threshold": -0.06,
        "risk_on_return_threshold": 0.04,
        "risk_on_drawdown_threshold": -0.04,
        "vol_top_percentile": 0.75,
        "min_history_for_vol_percentile": 60,
    },
    "crash": {
        "horizon_days": 5,
        "moderate_threshold": -0.05,
        "severe_threshold": -0.10,
    },
    "models": {
        "rf_estimators": 80,
        "rf_n_jobs": -1,
        "rf_min_samples_leaf": 5,
        "rf_classifier_min_samples_leaf": 20,
        "ridge_alpha": 1.0,
        "logistic_max_iter": 1000,
        "calibration_enabled": True,
        "calibration_n_splits": 2,
        "calibration_warn_on_failure": False,
        "risk_off_threshold_min": 0.2,
        "risk_off_threshold_max": 0.65,
        "risk_off_threshold_steps": 19,
        "risk_off_recall_weight": 0.65,
        "regime_baseline_fallback_enabled": True,
        "regime_ml_min_score_advantage": 0.0,
    },
}


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_update(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        return deepcopy(DEFAULT_CONFIG)
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    if yaml is None:
        raise RuntimeError("PyYAML is required to read YAML config files.")
    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return _deep_update(DEFAULT_CONFIG, loaded)
