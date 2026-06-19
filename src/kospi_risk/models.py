from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, f1_score, mean_absolute_error, mean_squared_error, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    from lightgbm import LGBMClassifier, LGBMRegressor
except Exception:  # pragma: no cover - optional dependency.
    LGBMClassifier = None
    LGBMRegressor = None


TARGET_COLUMNS = {
    "target_vol_20d",
    "target_regime",
    "target_risk_off_20d",
    "target_outperform_spx_20d",
    "target_outperform_sox_20d",
    "fwd_ret_20d",
    "fwd_max_drawdown_20d",
}


@dataclass
class ModelBundle:
    feature_columns: list[str]
    vol_model: Any
    regime_model: Any
    risk_off_model: Any | None
    outperform_spx_model: Any
    outperform_sox_model: Any
    config: dict
    train_end_date: str
    predicted_vol_history: list[float]
    selected_models: dict[str, str]
    model_selection_metrics: list[dict[str, float | str]]
    risk_off_threshold: float
    regime_strategy: str
    baseline_vol_threshold: float


def feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = TARGET_COLUMNS | {"date"}
    columns = []
    for column in df.columns:
        if column in excluded:
            continue
        if pd.api.types.is_numeric_dtype(df[column]):
            columns.append(column)
    return columns


def eligible_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "target_risk_off_20d" not in work.columns and "target_regime" in work.columns:
        work["target_risk_off_20d"] = (work["target_regime"] == "risk-off").astype("float")
        work.loc[work["target_regime"].isna(), "target_risk_off_20d"] = np.nan
    required = [
        "target_vol_20d",
        "target_regime",
        "target_risk_off_20d",
        "target_outperform_spx_20d",
        "target_outperform_sox_20d",
    ]
    return work.dropna(subset=required).reset_index(drop=True)


def _ridge(alpha: float) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=alpha)),
        ]
    )


def _logistic(random_state: int, max_iter: int) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    max_iter=max_iter,
                    random_state=random_state,
                    class_weight="balanced",
                    solver="liblinear",
                    C=0.2,
                ),
            ),
        ]
    )


def _binary_logistic(random_state: int, max_iter: int) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    max_iter=max_iter,
                    random_state=random_state,
                    class_weight="balanced",
                    solver="liblinear",
                    C=0.2,
                ),
            ),
        ]
    )


def _rf_regressor(config: dict) -> Pipeline:
    n_jobs = int(config["models"].get("rf_n_jobs", -1))
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestRegressor(
                    n_estimators=int(config["models"].get("rf_estimators", 120)),
                    min_samples_leaf=int(config["models"].get("rf_min_samples_leaf", 5)),
                    random_state=int(config.get("random_state", 42)),
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )


def _rf_classifier(config: dict) -> Pipeline:
    n_jobs = int(config["models"].get("rf_n_jobs", -1))
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=int(config["models"].get("rf_estimators", 120)),
                    min_samples_leaf=int(
                        config["models"].get(
                            "rf_classifier_min_samples_leaf",
                            config["models"].get("rf_min_samples_leaf", 5),
                        )
                    ),
                    random_state=int(config.get("random_state", 42)),
                    class_weight="balanced_subsample",
                    n_jobs=n_jobs,
                ),
            ),
        ]
    )


def make_model_candidates(config: dict) -> dict[str, dict[str, Any]]:
    random_state = int(config.get("random_state", 42))
    max_iter = int(config["models"].get("logistic_max_iter", 1000))
    alpha = float(config["models"].get("ridge_alpha", 1.0))
    candidates: dict[str, dict[str, Any]] = {
        "vol": {
            "ridge": _ridge(alpha),
            "random_forest": _rf_regressor(config),
        },
        "regime": {
            "logistic": _logistic(random_state, max_iter),
            "random_forest": _rf_classifier(config),
        },
        "outperform": {
            "logistic": _binary_logistic(random_state, max_iter),
            "random_forest": _rf_classifier(config),
        },
        "risk_off": {
            "logistic": _binary_logistic(random_state, max_iter),
            "random_forest": _rf_classifier(config),
        },
    }
    if LGBMRegressor is not None and LGBMClassifier is not None:
        candidates["vol"]["lightgbm"] = LGBMRegressor(random_state=random_state, verbosity=-1)
        candidates["regime"]["lightgbm"] = LGBMClassifier(random_state=random_state, verbosity=-1)
        candidates["outperform"]["lightgbm"] = LGBMClassifier(random_state=random_state, verbosity=-1)
        candidates["risk_off"]["lightgbm"] = LGBMClassifier(random_state=random_state, verbosity=-1)
    return candidates


def _fit_classifier(model: Any, x: pd.DataFrame, y: pd.Series) -> Any:
    if y.nunique(dropna=True) < 2:
        raise ValueError("Classifier target has fewer than two classes.")
    return model.fit(x, y)


def _selection_split(train_df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    validation = config.get("validation", {})
    fraction = float(validation.get("model_selection_fraction", 0.2))
    min_rows = int(validation.get("model_selection_min_rows", 126))
    holdout = max(min_rows, int(len(train_df) * fraction))
    holdout = min(max(holdout, 1), max(len(train_df) // 3, 1))
    return train_df.iloc[:-holdout].reset_index(drop=True), train_df.iloc[-holdout:].reset_index(drop=True)


def _safe_auc(y_true: pd.Series, y_prob: np.ndarray) -> float:
    if y_true.nunique(dropna=True) < 2:
        return float("nan")
    return float(roc_auc_score(y_true.astype(int), y_prob))


def _probability_for_class(model: Any, x: pd.DataFrame, class_label: str | int) -> np.ndarray:
    probabilities = model.predict_proba(x)
    classes = list(model.classes_)
    if class_label not in classes:
        return np.zeros(len(x), dtype=float)
    return probabilities[:, classes.index(class_label)]


def _baseline_vol_threshold(train_df: pd.DataFrame) -> float:
    threshold = train_df["kospi_realized_vol_20d"].dropna().quantile(0.75)
    if pd.isna(threshold):
        threshold = train_df["target_vol_20d"].dropna().median()
    return float(threshold)


def _baseline_regime_prediction(df: pd.DataFrame, vol_threshold: float) -> np.ndarray:
    risk_off = (df["kospi_log_ret_20d"] <= -0.04) | (df["kospi_realized_vol_20d"] >= vol_threshold)
    risk_on = (df["kospi_log_ret_20d"] >= 0.04) & (df["kospi_realized_vol_20d"] < vol_threshold)
    return np.select([risk_off, risk_on], ["risk-off", "risk-on"], default="neutral")


def _baseline_regime_probabilities(pred: np.ndarray) -> pd.DataFrame:
    result = pd.DataFrame({"pred_regime": pred})
    result["prob_risk_on"] = np.where(result["pred_regime"] == "risk-on", 0.6, 0.2)
    result["prob_neutral"] = np.where(result["pred_regime"] == "neutral", 0.6, 0.2)
    result["prob_risk_off"] = np.where(result["pred_regime"] == "risk-off", 0.6, 0.2)
    regime_sum = result[["prob_risk_on", "prob_neutral", "prob_risk_off"]].sum(axis=1)
    result[["prob_risk_on", "prob_neutral", "prob_risk_off"]] = result[["prob_risk_on", "prob_neutral", "prob_risk_off"]].div(regime_sum, axis=0)
    return result


def _risk_off_selection_weight(config: dict) -> float:
    return float(config.get("models", {}).get("risk_off_recall_weight", 0.65))


def _risk_off_score(y_true: pd.Series, pred: np.ndarray, config: dict) -> dict[str, float]:
    y_binary = (pd.Series(y_true).reset_index(drop=True) == "risk-off").astype(int)
    p_binary = (pd.Series(pred).reset_index(drop=True) == "risk-off").astype(int)
    true_positive = int(((y_binary == 1) & (p_binary == 1)).sum())
    false_positive = int(((y_binary == 0) & (p_binary == 1)).sum())
    false_negative = int(((y_binary == 1) & (p_binary == 0)).sum())
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    macro_f1 = float(f1_score(y_true, pred, labels=["risk-on", "neutral", "risk-off"], average="macro", zero_division=0))
    weight = _risk_off_selection_weight(config)
    score = weight * recall + (1 - weight) * macro_f1
    return {
        "macro_f1": macro_f1,
        "risk_off_precision": precision,
        "risk_off_recall": recall,
        "selection_score": score,
    }


def _risk_off_thresholds(config: dict) -> np.ndarray:
    model_config = config.get("models", {})
    low = float(model_config.get("risk_off_threshold_min", 0.2))
    high = float(model_config.get("risk_off_threshold_max", 0.65))
    steps = int(model_config.get("risk_off_threshold_steps", 19))
    return np.linspace(low, high, max(2, steps))


def _apply_risk_off_overlay(regime_pred: np.ndarray, risk_off_prob: np.ndarray, threshold: float) -> np.ndarray:
    pred = np.asarray(regime_pred, dtype=object).copy()
    risk_off = np.asarray(risk_off_prob, dtype=float) >= threshold
    pred[risk_off] = "risk-off"
    pred[(pred == "risk-off") & (~risk_off)] = "neutral"
    return pred


def _best_risk_off_threshold(y_true: pd.Series, base_regime_pred: np.ndarray, risk_off_prob: np.ndarray, config: dict) -> tuple[float, dict[str, float]]:
    best_threshold = 0.5
    best_scores = {"macro_f1": float("-inf"), "risk_off_precision": 0.0, "risk_off_recall": 0.0, "selection_score": float("-inf")}
    for threshold in _risk_off_thresholds(config):
        pred = _apply_risk_off_overlay(base_regime_pred, risk_off_prob, float(threshold))
        scores = _risk_off_score(y_true, pred, config)
        if scores["selection_score"] > best_scores["selection_score"]:
            best_threshold = float(threshold)
            best_scores = scores
    return best_threshold, best_scores


def _select_regressor(candidates: dict[str, Any], train_x: pd.DataFrame, train_y: pd.Series, valid_x: pd.DataFrame, valid_y: pd.Series) -> tuple[str, list[dict[str, float | str]]]:
    rows: list[dict[str, float | str]] = []
    for name, model in candidates.items():
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RuntimeWarning)
                fitted = model.fit(train_x, train_y)
                pred = np.clip(fitted.predict(valid_x), 0, None)
            rows.append(
                {
                    "task": "vol",
                    "candidate": name,
                    "rmse": float(np.sqrt(mean_squared_error(valid_y, pred))),
                    "mae": float(mean_absolute_error(valid_y, pred)),
                    "correlation": float(np.corrcoef(valid_y, pred)[0, 1]) if len(valid_y) > 2 else float("nan"),
                    "status": "ok",
                }
            )
        except (RuntimeWarning, FloatingPointError, ValueError) as exc:
            rows.append({"task": "vol", "candidate": name, "rmse": float("inf"), "mae": float("inf"), "correlation": float("nan"), "status": f"failed: {exc}"})
    valid_rows = [row for row in rows if np.isfinite(float(row["rmse"]))]
    if not valid_rows:
        raise ValueError("No valid volatility model candidate remained after numerical checks.")
    best = min(valid_rows, key=lambda row: row["rmse"])
    return str(best["candidate"]), rows


def _select_classifier(
    task: str,
    candidates: dict[str, Any],
    train_x: pd.DataFrame,
    train_y: pd.Series,
    valid_x: pd.DataFrame,
    valid_y: pd.Series,
    positive_class: str | int | None = None,
) -> tuple[str, list[dict[str, float | str]]]:
    rows: list[dict[str, float | str]] = []
    for name, model in candidates.items():
        if train_y.nunique(dropna=True) < 2:
            continue
        row: dict[str, float | str] = {"task": task, "candidate": name}
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RuntimeWarning)
                fitted = model.fit(train_x, train_y)
                pred = fitted.predict(valid_x)
                if positive_class is None:
                    row["macro_f1"] = float(f1_score(valid_y, pred, average="macro", zero_division=0))
                    row["risk_off_recall"] = float(np.mean(pred[valid_y.to_numpy() == "risk-off"] == "risk-off")) if (valid_y == "risk-off").any() else float("nan")
                else:
                    prob = _probability_for_class(fitted, valid_x, positive_class)
                    row["brier"] = float(brier_score_loss(valid_y.astype(int), prob))
                    row["auc"] = _safe_auc(valid_y, prob)
            row["status"] = "ok"
        except (RuntimeWarning, FloatingPointError, ValueError) as exc:
            if positive_class is None:
                row["macro_f1"] = float("-inf")
                row["risk_off_recall"] = float("nan")
            else:
                row["brier"] = float("inf")
                row["auc"] = float("nan")
            row["status"] = f"failed: {exc}"
        rows.append(row)
    if not rows:
        raise ValueError(f"{task} target has fewer than two classes.")
    if positive_class is None:
        valid_rows = [row for row in rows if np.isfinite(float(row.get("macro_f1", float("-inf"))))]
        if not valid_rows:
            raise ValueError(f"No valid {task} model candidate remained after numerical checks.")
        best = max(valid_rows, key=lambda row: row.get("macro_f1", float("-inf")))
    else:
        valid_rows = [row for row in rows if np.isfinite(float(row.get("brier", float("inf"))))]
        if not valid_rows:
            raise ValueError(f"No valid {task} model candidate remained after numerical checks.")
        best = min(valid_rows, key=lambda row: row.get("brier", float("inf")))
    return str(best["candidate"]), rows


def _select_risk_off_classifier(
    candidates: dict[str, Any],
    train_x: pd.DataFrame,
    train_y: pd.Series,
    valid_x: pd.DataFrame,
    valid_regime_y: pd.Series,
    base_regime_pred: np.ndarray,
    config: dict,
) -> tuple[str, float, list[dict[str, float | str]]]:
    rows: list[dict[str, float | str]] = []
    valid_y = (valid_regime_y == "risk-off").astype(int)
    for name, model in candidates.items():
        if train_y.nunique(dropna=True) < 2:
            continue
        row: dict[str, float | str] = {"task": "risk_off", "candidate": name}
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("error", category=RuntimeWarning)
                fitted = model.fit(train_x, train_y.astype(int))
                prob = _probability_for_class(fitted, valid_x, 1)
                threshold, scores = _best_risk_off_threshold(valid_regime_y, base_regime_pred, prob, config)
            row.update(scores)
            row["threshold"] = threshold
            row["brier"] = float(brier_score_loss(valid_y, prob))
            row["auc"] = _safe_auc(valid_y, prob)
            row["status"] = "ok"
        except (RuntimeWarning, FloatingPointError, ValueError) as exc:
            row.update(
                {
                    "macro_f1": float("-inf"),
                    "risk_off_precision": float("nan"),
                    "risk_off_recall": float("nan"),
                    "selection_score": float("-inf"),
                    "threshold": float("nan"),
                    "brier": float("inf"),
                    "auc": float("nan"),
                    "status": f"failed: {exc}",
                }
            )
        rows.append(row)
    if not rows:
        raise ValueError("risk_off target has fewer than two classes.")
    valid_rows = [row for row in rows if np.isfinite(float(row.get("selection_score", float("-inf"))))]
    if not valid_rows:
        raise ValueError("No valid risk_off model candidate remained after numerical checks.")
    best = max(valid_rows, key=lambda row: row.get("selection_score", float("-inf")))
    return str(best["candidate"]), float(best["threshold"]), rows


def _calibrated_classifier_if_possible(model: Any, x: pd.DataFrame, y: pd.Series, config: dict) -> Any:
    if not bool(config.get("models", {}).get("calibration_enabled", True)):
        return model.fit(x, y)
    n_splits = int(config.get("models", {}).get("calibration_n_splits", 3))
    if len(x) < (n_splits + 1) * 30 or y.nunique(dropna=True) < 2:
        return model.fit(x, y)
    splitter = TimeSeriesSplit(n_splits=n_splits)
    try:
        try:
            calibrated = CalibratedClassifierCV(estimator=model, cv=splitter, method="sigmoid")
        except TypeError:  # pragma: no cover - older scikit-learn compatibility.
            calibrated = CalibratedClassifierCV(base_estimator=model, cv=splitter, method="sigmoid")
        with warnings.catch_warnings():
            warnings.filterwarnings("error", category=RuntimeWarning)
            return calibrated.fit(x, y)
    except (Exception, RuntimeWarning) as exc:
        if bool(config.get("models", {}).get("calibration_warn_on_failure", False)):
            warnings.warn(
                f"Time-series calibration failed; using uncalibrated classifier. Reason: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
        return model.fit(x, y)


def train_bundle(df: pd.DataFrame, config: dict) -> ModelBundle:
    train_df = eligible_training_frame(df)
    min_rows = max(500, int(config.get("validation", {}).get("min_train_rows", 500)))
    if len(train_df) < min_rows:
        raise ValueError(f"Insufficient training history: {len(train_df)} rows, need at least {min_rows}.")
    warning_rows = int(config.get("validation", {}).get("reliability_warning_rows", 1500))
    if len(train_df) < warning_rows and not bool(config.get("_suppress_reliability_warning", False)):
        warnings.warn(
            f"Walk-forward reliability is limited with fewer than {warning_rows} observations: {len(train_df)} rows.",
            RuntimeWarning,
            stacklevel=2,
        )

    cols = feature_columns(train_df)
    candidates = make_model_candidates(config)
    select_train, select_valid = _selection_split(train_df, config)
    select_train_x = select_train[cols]
    select_valid_x = select_valid[cols]
    selection_rows: list[dict[str, float | str]] = []

    best_vol, rows = _select_regressor(candidates["vol"], select_train_x, select_train["target_vol_20d"], select_valid_x, select_valid["target_vol_20d"])
    selection_rows.extend(rows)
    best_regime, rows = _select_classifier("regime", candidates["regime"], select_train_x, select_train["target_regime"], select_valid_x, select_valid["target_regime"])
    selection_rows.extend(rows)
    validation_regime_model = candidates["regime"][best_regime].fit(select_train_x, select_train["target_regime"])
    validation_regime_pred = validation_regime_model.predict(select_valid_x)
    best_risk_off, risk_off_threshold, rows = _select_risk_off_classifier(
        candidates["risk_off"],
        select_train_x,
        select_train["target_risk_off_20d"].astype(int),
        select_valid_x,
        select_valid["target_regime"],
        validation_regime_pred,
        config,
    )
    selection_rows.extend(rows)
    validation_risk_off_model = candidates["risk_off"][best_risk_off].fit(select_train_x, select_train["target_risk_off_20d"].astype(int))
    validation_risk_off_prob = _probability_for_class(validation_risk_off_model, select_valid_x, 1)
    ml_regime_pred = _apply_risk_off_overlay(validation_regime_pred, validation_risk_off_prob, risk_off_threshold)
    ml_regime_scores = _risk_off_score(select_valid["target_regime"], ml_regime_pred, config)
    selection_baseline_threshold = _baseline_vol_threshold(select_train)
    baseline_regime_pred = _baseline_regime_prediction(select_valid, selection_baseline_threshold)
    baseline_regime_scores = _risk_off_score(select_valid["target_regime"], baseline_regime_pred, config)
    baseline_advantage = float(config.get("models", {}).get("regime_ml_min_score_advantage", 0.0))
    fallback_enabled = bool(config.get("models", {}).get("regime_baseline_fallback_enabled", True))
    regime_strategy = "ml_binary_overlay"
    if fallback_enabled and ml_regime_scores["selection_score"] < baseline_regime_scores["selection_score"] + baseline_advantage:
        regime_strategy = "baseline_fallback"
    selection_rows.extend(
        [
            {
                "task": "regime_strategy",
                "candidate": "ml_binary_overlay",
                "macro_f1": ml_regime_scores["macro_f1"],
                "risk_off_precision": ml_regime_scores["risk_off_precision"],
                "risk_off_recall": ml_regime_scores["risk_off_recall"],
                "selection_score": ml_regime_scores["selection_score"],
                "threshold": risk_off_threshold,
                "status": "ok",
            },
            {
                "task": "regime_strategy",
                "candidate": "baseline_fallback",
                "macro_f1": baseline_regime_scores["macro_f1"],
                "risk_off_precision": baseline_regime_scores["risk_off_precision"],
                "risk_off_recall": baseline_regime_scores["risk_off_recall"],
                "selection_score": baseline_regime_scores["selection_score"],
                "threshold": selection_baseline_threshold,
                "status": "ok",
            },
        ]
    )
    best_spx, rows = _select_classifier(
        "outperform_spx",
        candidates["outperform"],
        select_train_x,
        select_train["target_outperform_spx_20d"].astype(int),
        select_valid_x,
        select_valid["target_outperform_spx_20d"].astype(int),
        positive_class=1,
    )
    selection_rows.extend(rows)
    best_sox, rows = _select_classifier(
        "outperform_sox",
        candidates["outperform"],
        select_train_x,
        select_train["target_outperform_sox_20d"].astype(int),
        select_valid_x,
        select_valid["target_outperform_sox_20d"].astype(int),
        positive_class=1,
    )
    selection_rows.extend(rows)

    x = train_df[cols]
    vol_model = candidates["vol"][best_vol].fit(x, train_df["target_vol_20d"])
    regime_model = _calibrated_classifier_if_possible(candidates["regime"][best_regime], x, train_df["target_regime"], config)
    risk_off_model = _calibrated_classifier_if_possible(candidates["risk_off"][best_risk_off], x, train_df["target_risk_off_20d"].astype(int), config)
    outperform_spx_model = _calibrated_classifier_if_possible(candidates["outperform"][best_spx], x, train_df["target_outperform_spx_20d"].astype(int), config)
    outperform_sox_model = _calibrated_classifier_if_possible(candidates["outperform"][best_sox], x, train_df["target_outperform_sox_20d"].astype(int), config)

    predicted_vol_history = np.asarray(vol_model.predict(x), dtype=float)
    baseline_vol_threshold = _baseline_vol_threshold(train_df)
    return ModelBundle(
        feature_columns=cols,
        vol_model=vol_model,
        regime_model=regime_model,
        risk_off_model=risk_off_model,
        outperform_spx_model=outperform_spx_model,
        outperform_sox_model=outperform_sox_model,
        config=config,
        train_end_date=str(pd.to_datetime(train_df["date"].iloc[-1]).date()),
        predicted_vol_history=predicted_vol_history.tolist(),
        selected_models={
            "vol": best_vol,
            "regime": best_regime,
            "risk_off": best_risk_off,
            "regime_strategy": regime_strategy,
            "outperform_spx": best_spx,
            "outperform_sox": best_sox,
        },
        model_selection_metrics=selection_rows,
        risk_off_threshold=risk_off_threshold,
        regime_strategy=regime_strategy,
        baseline_vol_threshold=baseline_vol_threshold,
    )


def save_bundle(bundle: ModelBundle, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, output_path)


def load_bundle(path: str | Path) -> ModelBundle:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"Model bundle not found: {input_path}")
    return joblib.load(input_path)


def predict_proba_for_class(model: Any, x: pd.DataFrame, class_label: str | int) -> np.ndarray:
    if not hasattr(model, "predict_proba"):
        predictions = model.predict(x)
        return (predictions == class_label).astype(float)
    probabilities = model.predict_proba(x)
    classes = list(model.classes_)
    if class_label not in classes:
        return np.zeros(len(x), dtype=float)
    return probabilities[:, classes.index(class_label)]


def predict_bundle(bundle: ModelBundle, df: pd.DataFrame) -> pd.DataFrame:
    x = df[bundle.feature_columns]
    result = pd.DataFrame({"date": df["date"].values})
    result["pred_vol_20d"] = np.clip(bundle.vol_model.predict(x), 0, None)
    if getattr(bundle, "regime_strategy", "ml_binary_overlay") == "baseline_fallback":
        baseline = _baseline_regime_probabilities(_baseline_regime_prediction(df, float(bundle.baseline_vol_threshold)))
        result = pd.concat([result, baseline], axis=1)
    else:
        base_regime_pred = bundle.regime_model.predict(x)
        base_prob_risk_on = np.clip(predict_proba_for_class(bundle.regime_model, x, "risk-on"), 0, 1)
        base_prob_neutral = np.clip(predict_proba_for_class(bundle.regime_model, x, "neutral"), 0, 1)
        if getattr(bundle, "risk_off_model", None) is None:
            risk_off_prob = np.clip(predict_proba_for_class(bundle.regime_model, x, "risk-off"), 0, 1)
        else:
            risk_off_prob = np.clip(predict_proba_for_class(bundle.risk_off_model, x, 1), 0, 1)
        regime_pred = _apply_risk_off_overlay(base_regime_pred, risk_off_prob, float(getattr(bundle, "risk_off_threshold", 0.5)))
        non_risk_total = base_prob_risk_on + base_prob_neutral
        non_risk_share = np.clip(1 - risk_off_prob, 0, 1)
        result["pred_regime"] = regime_pred
        result["prob_risk_off"] = risk_off_prob
        result["prob_risk_on"] = np.where(non_risk_total > 0, base_prob_risk_on / non_risk_total * non_risk_share, non_risk_share / 2)
        result["prob_neutral"] = np.where(non_risk_total > 0, base_prob_neutral / non_risk_total * non_risk_share, non_risk_share / 2)
    result["prob_kospi_outperform_spx_20d"] = np.clip(
        predict_proba_for_class(bundle.outperform_spx_model, x, 1), 0, 1
    )
    result["prob_kospi_outperform_sox_20d"] = np.clip(
        predict_proba_for_class(bundle.outperform_sox_model, x, 1), 0, 1
    )
    return result
