from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .models import (
    eligible_crash_training_frame,
    eligible_training_frame,
    predict_bundle,
    predict_crash_bundle,
    train_bundle,
    train_crash_bundle,
)


CRASH_TASKS = [
    ("crash_5d_5pct", "target_crash_5d_5pct", "prob_crash_5d_5pct"),
    ("crash_5d_10pct", "target_crash_5d_10pct", "prob_crash_5d_10pct"),
]


@dataclass
class WalkForwardSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def make_walk_forward_splits(n_rows: int, config: dict) -> list[WalkForwardSplit]:
    validation = config.get("validation", {})
    initial_train = int(validation.get("initial_train_days", 1260))
    test_days = int(validation.get("test_days", 63))
    step_days = int(validation.get("step_days", 21))
    if n_rows <= initial_train:
        min_train = int(validation.get("min_train_rows", 252))
        initial_train = min(max(min_train, n_rows // 2), max(n_rows - test_days, min_train))
    splits = []
    train_end = initial_train
    while train_end + 1 < n_rows:
        test_end = min(train_end + test_days, n_rows)
        if test_end <= train_end:
            break
        splits.append(WalkForwardSplit(0, train_end, train_end, test_end))
        train_end += step_days
    max_folds = int(validation.get("max_backtest_folds", 0) or 0)
    if max_folds > 0 and len(splits) > max_folds:
        splits = splits[-max_folds:]
    return splits


def qlike(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    eps = 1e-8
    true_var = np.square(np.maximum(y_true, eps))
    pred_var = np.square(np.maximum(y_pred, eps))
    return float(np.mean(true_var / pred_var - np.log(true_var / pred_var) - 1))


def _safe_auc(y_true: pd.Series, y_prob: pd.Series) -> float:
    if y_true.nunique(dropna=True) < 2:
        return math.nan
    return float(roc_auc_score(y_true, y_prob))


def _safe_average_precision(y_true: pd.Series, y_prob: pd.Series) -> float:
    if y_true.nunique(dropna=True) < 2:
        return math.nan
    return float(average_precision_score(y_true, y_prob))


def _top_decile_metrics(y_true: pd.Series, y_prob: pd.Series) -> tuple[float, float]:
    count = max(1, math.ceil(len(y_prob) * 0.1))
    top_index = y_prob.nlargest(count).index
    hit_rate = float(y_true.loc[top_index].mean())
    event_rate = float(y_true.mean())
    lift = hit_rate / event_rate if event_rate > 0 else math.nan
    return hit_rate, lift


def calibration_table(y_true: pd.Series, y_prob: pd.Series, buckets: int = 5) -> pd.DataFrame:
    frame = pd.DataFrame({"y_true": y_true.astype(float), "y_prob": y_prob.astype(float)})
    frame["bucket"] = pd.cut(frame["y_prob"], bins=np.linspace(0, 1, buckets + 1), include_lowest=True)
    return frame.groupby("bucket", observed=False).agg(count=("y_true", "size"), avg_prob=("y_prob", "mean"), hit_rate=("y_true", "mean")).reset_index()


def baseline_predictions(train_df: pd.DataFrame, test_df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({"date": test_df["date"].values})
    vol_threshold = train_df["kospi_realized_vol_20d"].dropna().quantile(0.75)
    result["pred_vol_20d"] = test_df["kospi_realized_vol_20d"].fillna(train_df["target_vol_20d"].median()).to_numpy()

    risk_off = (test_df["kospi_log_ret_20d"] <= -0.04) | (test_df["kospi_realized_vol_20d"] >= vol_threshold)
    risk_on = (test_df["kospi_log_ret_20d"] >= 0.04) & (test_df["kospi_realized_vol_20d"] < vol_threshold)
    result["pred_regime"] = np.select([risk_off, risk_on], ["risk-off", "risk-on"], default="neutral")
    result["prob_risk_on"] = np.where(result["pred_regime"] == "risk-on", 0.6, 0.2)
    result["prob_neutral"] = np.where(result["pred_regime"] == "neutral", 0.6, 0.2)
    result["prob_risk_off"] = np.where(result["pred_regime"] == "risk-off", 0.6, 0.2)
    regime_sum = result[["prob_risk_on", "prob_neutral", "prob_risk_off"]].sum(axis=1)
    result[["prob_risk_on", "prob_neutral", "prob_risk_off"]] = result[["prob_risk_on", "prob_neutral", "prob_risk_off"]].div(regime_sum, axis=0)

    result["prob_kospi_outperform_spx_20d"] = np.where(test_df["kospi_minus_spx_ret_20d"] > 0, 0.6, 0.4)
    result["prob_kospi_outperform_sox_20d"] = np.where(test_df["kospi_minus_sox_ret_20d"] > 0, 0.6, 0.4)
    recent_return_5d = np.expm1(test_df["kospi_log_ret_5d"].astype(float))
    result["prob_crash_5d_5pct"] = np.clip((-recent_return_5d - 0.01) / 0.12, 0.01, 0.8)
    result["prob_crash_5d_10pct"] = np.clip((-recent_return_5d - 0.03) / 0.18, 0.005, 0.6)
    return result


def crash_baseline_predictions(test_df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({"date": test_df["date"].values})
    recent_return_5d = np.expm1(test_df["kospi_log_ret_5d"].astype(float))
    result["prob_crash_5d_5pct"] = np.clip((-recent_return_5d - 0.01) / 0.12, 0.01, 0.8)
    result["prob_crash_5d_10pct"] = np.clip((-recent_return_5d - 0.03) / 0.18, 0.005, 0.6)
    return result


def _binary_task_metric_rows(
    scored: pd.DataFrame,
    model_name: str,
    tasks: list[tuple[str, str, str]],
) -> list[dict[str, float | str]]:
    metrics: list[dict[str, float | str]] = []
    for name, target, prob in tasks:
        y = scored[target].astype(int)
        p = scored[prob].astype(float)
        pred = p >= 0.5
        top_decile_hit_rate, top_decile_lift = _top_decile_metrics(y, p)
        metrics.extend(
            [
                {"model": model_name, "task": name, "metric": "auc", "value": _safe_auc(y, p)},
                {"model": model_name, "task": name, "metric": "average_precision", "value": _safe_average_precision(y, p)},
                {"model": model_name, "task": name, "metric": "event_count", "value": int(y.sum())},
                {"model": model_name, "task": name, "metric": "event_rate", "value": float(y.mean())},
                {"model": model_name, "task": name, "metric": "top_decile_hit_rate", "value": top_decile_hit_rate},
                {"model": model_name, "task": name, "metric": "top_decile_lift", "value": top_decile_lift},
                {"model": model_name, "task": name, "metric": "accuracy", "value": accuracy_score(y, pred)},
                {"model": model_name, "task": name, "metric": "precision", "value": precision_score(y, pred, zero_division=0)},
                {"model": model_name, "task": name, "metric": "recall", "value": recall_score(y, pred, zero_division=0)},
                {"model": model_name, "task": name, "metric": "f1", "value": f1_score(y, pred, zero_division=0)},
                {"model": model_name, "task": name, "metric": "brier", "value": brier_score_loss(y, p)},
            ]
        )
    return metrics


def evaluate_predictions(scored: pd.DataFrame, model_name: str = "ml_selected") -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    metrics: list[dict[str, float | str]] = []
    y_vol = scored["target_vol_20d"].to_numpy(dtype=float)
    p_vol = scored["pred_vol_20d"].to_numpy(dtype=float)
    metrics.extend(
        [
            {"model": model_name, "task": "vol", "metric": "mae", "value": mean_absolute_error(y_vol, p_vol)},
            {"model": model_name, "task": "vol", "metric": "rmse", "value": float(np.sqrt(mean_squared_error(y_vol, p_vol)))},
            {"model": model_name, "task": "vol", "metric": "correlation", "value": np.corrcoef(y_vol, p_vol)[0, 1] if len(scored) > 2 else math.nan},
            {"model": model_name, "task": "vol", "metric": "qlike", "value": qlike(y_vol, p_vol)},
        ]
    )

    y_regime = scored["target_regime"]
    p_regime = scored["pred_regime"]
    labels = ["risk-on", "neutral", "risk-off"]
    metrics.extend(
        [
            {"model": model_name, "task": "regime", "metric": "accuracy", "value": accuracy_score(y_regime, p_regime)},
            {"model": model_name, "task": "regime", "metric": "balanced_accuracy", "value": balanced_accuracy_score(y_regime, p_regime)},
            {"model": model_name, "task": "regime", "metric": "macro_f1", "value": f1_score(y_regime, p_regime, labels=labels, average="macro", zero_division=0)},
            {"model": model_name, "task": "regime", "metric": "risk_off_precision", "value": precision_score(y_regime, p_regime, labels=["risk-off"], average="macro", zero_division=0)},
            {"model": model_name, "task": "regime", "metric": "risk_off_recall", "value": recall_score(y_regime, p_regime, labels=["risk-off"], average="macro", zero_division=0)},
        ]
    )
    matrices = {f"{model_name}_regime_confusion_matrix": confusion_matrix(y_regime, p_regime, labels=labels)}

    y_risk_off = (y_regime == "risk-off").astype(int)
    p_risk_off_label = (p_regime == "risk-off").astype(int)
    p_risk_off_prob = scored["prob_risk_off"].astype(float)
    metrics.extend(
        [
            {"model": model_name, "task": "risk_off_binary", "metric": "precision", "value": precision_score(y_risk_off, p_risk_off_label, zero_division=0)},
            {"model": model_name, "task": "risk_off_binary", "metric": "recall", "value": recall_score(y_risk_off, p_risk_off_label, zero_division=0)},
            {"model": model_name, "task": "risk_off_binary", "metric": "f1", "value": f1_score(y_risk_off, p_risk_off_label, zero_division=0)},
            {"model": model_name, "task": "risk_off_binary", "metric": "brier", "value": brier_score_loss(y_risk_off, p_risk_off_prob)},
            {"model": model_name, "task": "risk_off_binary", "metric": "auc", "value": _safe_auc(y_risk_off, p_risk_off_prob)},
        ]
    )

    binary_tasks = [
        ("outperform_spx", "target_outperform_spx_20d", "prob_kospi_outperform_spx_20d"),
        ("outperform_sox", "target_outperform_sox_20d", "prob_kospi_outperform_sox_20d"),
    ] + CRASH_TASKS
    metrics.extend(_binary_task_metric_rows(scored, model_name, binary_tasks))
    return pd.DataFrame(metrics), matrices


def run_walk_forward_backtest(df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, np.ndarray]]:
    clean = eligible_training_frame(df)
    warning_rows = int(config.get("validation", {}).get("reliability_warning_rows", 1500))
    if len(clean) < warning_rows:
        warnings.warn(
            f"Walk-forward reliability is limited with fewer than {warning_rows} observations: {len(clean)} rows.",
            RuntimeWarning,
            stacklevel=2,
        )
    splits = make_walk_forward_splits(len(clean), config)
    if not splits:
        raise ValueError("Insufficient history for walk-forward validation.")
    scored_parts = []
    baseline_parts = []
    selection_rows = []
    split_rows = []
    for fold, split in enumerate(splits, start=1):
        train_df = clean.iloc[split.train_start : split.train_end].reset_index(drop=True)
        test_df = clean.iloc[split.test_start : split.test_end].reset_index(drop=True)
        if pd.to_datetime(train_df["date"].iloc[-1]) >= pd.to_datetime(test_df["date"].iloc[0]):
            raise ValueError("Walk-forward split is invalid: train end date must be before test start date.")
        fold_config = {**config, "_suppress_reliability_warning": True}
        bundle = train_bundle(train_df, fold_config)
        predictions = predict_bundle(bundle, test_df)
        baseline = baseline_predictions(train_df, test_df)
        ml_scored = pd.concat([test_df.reset_index(drop=True), predictions.drop(columns=["date"])], axis=1)
        baseline_scored = pd.concat([test_df.reset_index(drop=True), baseline.drop(columns=["date"])], axis=1)
        ml_scored["fold"] = fold
        baseline_scored["fold"] = fold
        scored_parts.append(ml_scored)
        baseline_parts.append(baseline_scored)
        for row in bundle.model_selection_metrics:
            selected = bundle.selected_models.get(str(row["task"]), "")
            if row["task"] == "outperform_spx":
                selected = bundle.selected_models.get("outperform_spx", "")
            if row["task"] == "outperform_sox":
                selected = bundle.selected_models.get("outperform_sox", "")
            if row["task"] == "regime_strategy":
                selected = bundle.selected_models.get("regime_strategy", "")
            selection_rows.append({**row, "fold": fold, "selected": str(row["candidate"]) == selected})
        split_rows.append(
            {
                "fold": fold,
                "train_start_date": train_df["date"].iloc[0],
                "train_end_date": train_df["date"].iloc[-1],
                "test_start_date": test_df["date"].iloc[0],
                "test_end_date": test_df["date"].iloc[-1],
                "train_rows": len(train_df),
                "test_rows": len(test_df),
            }
        )
    scored = pd.concat(scored_parts, ignore_index=True)
    baseline_scored = pd.concat(baseline_parts, ignore_index=True)
    ml_metrics, ml_matrices = evaluate_predictions(scored, model_name="ml_selected")
    baseline_metrics, baseline_matrices = evaluate_predictions(baseline_scored, model_name="baseline")
    metrics = pd.concat([ml_metrics, baseline_metrics], ignore_index=True)
    metrics.attrs["model_selection"] = pd.DataFrame(selection_rows)
    metrics.attrs["splits"] = pd.DataFrame(split_rows)
    matrices = {**ml_matrices, **baseline_matrices}
    return scored, metrics, matrices


def run_crash_walk_forward_backtest(df: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    clean = eligible_crash_training_frame(df)
    splits = make_walk_forward_splits(len(clean), config)
    if not splits:
        raise ValueError("Insufficient history for crash walk-forward validation.")

    scored_parts = []
    baseline_parts = []
    selection_rows = []
    split_rows = []
    for fold, split in enumerate(splits, start=1):
        train_df = clean.iloc[split.train_start : split.train_end].reset_index(drop=True)
        test_df = clean.iloc[split.test_start : split.test_end].reset_index(drop=True)
        if pd.to_datetime(train_df["date"].iloc[-1]) >= pd.to_datetime(test_df["date"].iloc[0]):
            raise ValueError("Crash walk-forward split is invalid: train end date must be before test start date.")
        fold_config = {**config, "_suppress_reliability_warning": True}
        bundle = train_crash_bundle(train_df, fold_config)
        predictions = predict_crash_bundle(bundle, test_df)
        baseline = crash_baseline_predictions(test_df)
        ml_scored = pd.concat([test_df.reset_index(drop=True), predictions.drop(columns=["date"])], axis=1)
        baseline_scored = pd.concat([test_df.reset_index(drop=True), baseline.drop(columns=["date"])], axis=1)
        ml_scored["fold"] = fold
        baseline_scored["fold"] = fold
        scored_parts.append(ml_scored)
        baseline_parts.append(baseline_scored)
        for row in bundle.model_selection_metrics:
            selection_rows.append(
                {**row, "fold": fold, "selected": str(row["candidate"]) == bundle.selected_models.get(str(row["task"]), "")}
            )
        split_rows.append(
            {
                "fold": fold,
                "train_start_date": train_df["date"].iloc[0],
                "train_end_date": train_df["date"].iloc[-1],
                "test_start_date": test_df["date"].iloc[0],
                "test_end_date": test_df["date"].iloc[-1],
                "train_rows": len(train_df),
                "test_rows": len(test_df),
            }
        )

    scored = pd.concat(scored_parts, ignore_index=True)
    baseline_scored = pd.concat(baseline_parts, ignore_index=True)
    metrics = pd.DataFrame(
        _binary_task_metric_rows(scored, "ml_selected", CRASH_TASKS)
        + _binary_task_metric_rows(baseline_scored, "baseline", CRASH_TASKS)
    )
    metrics.attrs["model_selection"] = pd.DataFrame(selection_rows)
    metrics.attrs["splits"] = pd.DataFrame(split_rows)
    return scored, metrics
