from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .models import (
    _calibrated_classifier_if_possible,
    eligible_crash_training_frame,
    feature_columns,
    make_model_candidates,
    predict_proba_for_class,
)
from .transformer_lab import (
    TransformerLabConfig,
    _fit_predict_fold_members,
    _import_torch,
    _prior_oos_sigmoid_calibration,
    _purged_train_end,
)
from .validation import crash_baseline_predictions, make_walk_forward_splits


TASKS = {
    "crash_5d_5pct": {
        "target_col": "target_crash_5d_5pct",
        "prob_col": "prob_crash_5d_5pct",
    },
    "crash_5d_10pct": {
        "target_col": "target_crash_5d_10pct",
        "prob_col": "prob_crash_5d_10pct",
    },
}

MODEL_PROBABILITY_COLUMNS = {
    "rf": "rf_probability",
    "transformer": "transformer_probability",
    "rule": "rule_probability",
}


@dataclass(frozen=True)
class EnsembleLabConfig:
    targets: tuple[str, ...] = ("crash_5d_5pct", "crash_5d_10pct")
    max_folds: int = 12
    sequence_length: int = 40
    epochs: int = 4
    batch_size: int = 64
    d_model: int = 32
    nhead: int = 4
    num_layers: int = 2
    dropout: float = 0.1
    learning_rate: float = 0.001
    random_state: int = 42
    device: str = "auto"
    pooling: str = "last"
    loss: str = "bce"
    focal_alpha: float = 0.75
    focal_gamma: float = 2.0
    pos_weight_cap: float = 0.0
    feature_selection: str = "all"
    max_features: int = 0
    fixed_rf_weight: float = 0.5
    fixed_transformer_weight: float = 0.3
    fixed_rule_weight: float = 0.2
    adaptive_weights: bool = True
    min_weight_selection_folds: int = 6
    min_weight_selection_events: int = 12
    weight_lookback_folds: int = 24
    max_brier_degradation: float = 0.05
    adaptive_weight_shrinkage: float = 0.6
    purge_days: int = 5
    positional_encoding: str = "none"
    gradient_clip_norm: float = 1.0
    weight_decay: float = 0.0001
    seed_count: int = 2
    prior_oos_calibration: bool = False
    min_calibration_folds: int = 4
    min_calibration_events: int = 8
    moderate_pooling: str | None = None
    severe_pooling: str | None = None
    moderate_loss: str | None = None
    severe_loss: str | None = None


def _validate_targets(targets: tuple[str, ...]) -> tuple[str, ...]:
    unknown = sorted(set(targets) - set(TASKS))
    if unknown:
        raise ValueError(f"지원하지 않는 ensemble target입니다: {', '.join(unknown)}")
    if not targets:
        raise ValueError("ensemble target은 하나 이상 필요합니다.")
    return targets


def _fixed_weights(lab_config: EnsembleLabConfig) -> dict[str, float]:
    weights = {
        "rf": float(lab_config.fixed_rf_weight),
        "transformer": float(lab_config.fixed_transformer_weight),
        "rule": float(lab_config.fixed_rule_weight),
    }
    total = sum(max(value, 0.0) for value in weights.values())
    if total <= 0:
        raise ValueError("ensemble 고정 가중치의 합은 0보다 커야 합니다.")
    return {name: max(value, 0.0) / total for name, value in weights.items()}


def _weight_grid(default_weights: dict[str, float]) -> list[dict[str, float]]:
    grid: list[dict[str, float]] = []
    for rf_weight in np.linspace(0, 1, 11):
        for transformer_weight in np.linspace(0, 1 - rf_weight, int(round((1 - rf_weight) * 10)) + 1):
            rule_weight = 1 - rf_weight - transformer_weight
            if rule_weight < -1e-9:
                continue
            weights = {
                "rf": round(float(rf_weight), 10),
                "transformer": round(float(transformer_weight), 10),
                "rule": round(float(rule_weight), 10),
            }
            if all(abs(weights[name] - default_weights[name]) <= 0.25 + 1e-9 for name in weights):
                grid.append(weights)
    if default_weights not in grid:
        grid.append(default_weights)
    return grid


def _weighted_average(frame: pd.DataFrame, weights: dict[str, float]) -> np.ndarray:
    columns = [MODEL_PROBABILITY_COLUMNS[name] for name in ("rf", "transformer", "rule")]
    values = frame[columns].to_numpy(dtype=float)
    weight_vector = np.asarray([weights["rf"], weights["transformer"], weights["rule"]], dtype=float)
    available = np.isfinite(values)
    numerator = np.nansum(np.where(available, values, 0.0) * weight_vector, axis=1)
    denominator = np.sum(available * weight_vector, axis=1)
    out = np.divide(numerator, denominator, out=np.full(len(frame), np.nan), where=denominator > 0)
    return np.clip(out, 0, 1)


def _safe_auc(y_true: pd.Series, y_prob: pd.Series) -> float:
    if y_true.nunique(dropna=True) < 2:
        return float("nan")
    return float(roc_auc_score(y_true.astype(int), y_prob.astype(float)))


def _safe_average_precision(y_true: pd.Series, y_prob: pd.Series) -> float:
    if y_true.nunique(dropna=True) < 2:
        return float("nan")
    return float(average_precision_score(y_true.astype(int), y_prob.astype(float)))


def _top_decile_metrics(y_true: pd.Series, y_prob: pd.Series) -> tuple[float, float, float]:
    if len(y_prob) == 0:
        return float("nan"), float("nan"), float("nan")
    count = max(1, int(np.ceil(len(y_prob) * 0.1)))
    top_index = y_prob.nlargest(count).index
    hit_rate = float(y_true.loc[top_index].mean())
    event_rate = float(y_true.mean())
    lift = hit_rate / event_rate if event_rate > 0 else float("nan")
    capture = float(y_true.loc[top_index].sum() / y_true.sum()) if y_true.sum() > 0 else float("nan")
    return hit_rate, lift, capture


def _fold_win_rate(target_frame: pd.DataFrame, probability_col: str) -> tuple[float, int]:
    if probability_col == "rf_probability":
        return float("nan"), 0
    wins: list[bool] = []
    for _, fold_frame in target_frame.groupby("fold", sort=True):
        valid = fold_frame[["target", probability_col, "rf_probability"]].dropna()
        if valid.empty or valid["target"].nunique() < 2:
            continue
        y_true = valid["target"].astype(int)
        candidate_ap = average_precision_score(y_true, valid[probability_col].astype(float))
        rf_ap = average_precision_score(y_true, valid["rf_probability"].astype(float))
        wins.append(bool(candidate_ap > rf_ap))
    return (float(np.mean(wins)), len(wins)) if wins else (float("nan"), 0)


def _score_weight_candidate(prior: pd.DataFrame, weights: dict[str, float]) -> tuple[float, float]:
    probability = pd.Series(_weighted_average(prior, weights), index=prior.index)
    valid = probability.notna()
    if valid.sum() == 0 or prior.loc[valid, "target"].nunique(dropna=True) < 2:
        return float("nan"), float("inf")
    y_true = prior.loc[valid, "target"].astype(int)
    y_prob = probability.loc[valid].astype(float)
    return float(average_precision_score(y_true, y_prob)), float(brier_score_loss(y_true, y_prob))


def _select_adaptive_weights(
    prior_predictions: pd.DataFrame,
    default_weights: dict[str, float],
    min_weight_selection_folds: int,
    min_weight_selection_events: int = 1,
    weight_lookback_folds: int = 0,
    max_brier_degradation: float = 0.05,
    shrinkage: float = 1.0,
) -> dict[str, float]:
    if prior_predictions.empty or prior_predictions["fold"].nunique() < min_weight_selection_folds:
        return default_weights
    if prior_predictions["target"].nunique(dropna=True) < 2:
        return default_weights
    if weight_lookback_folds > 0:
        recent_folds = sorted(prior_predictions["fold"].unique())[-weight_lookback_folds:]
        prior_predictions = prior_predictions.loc[prior_predictions["fold"].isin(recent_folds)].copy()
    event_count = int(prior_predictions["target"].astype(int).sum())
    non_event_count = int(len(prior_predictions) - event_count)
    if min(event_count, non_event_count) < min_weight_selection_events:
        return default_weights

    default_ap, default_brier = _score_weight_candidate(prior_predictions, default_weights)
    if not np.isfinite(default_ap) or not np.isfinite(default_brier):
        return default_weights

    best_weights = default_weights
    best_average_precision = default_ap
    best_brier = default_brier
    for weights in _weight_grid(default_weights):
        average_precision, brier = _score_weight_candidate(prior_predictions, weights)
        if not np.isfinite(average_precision):
            continue
        if brier > default_brier * (1 + max(max_brier_degradation, 0.0)):
            continue
        if average_precision > best_average_precision or (
            np.isclose(average_precision, best_average_precision) and brier < best_brier
        ):
            best_weights = weights
            best_average_precision = average_precision
            best_brier = brier
    if best_weights == default_weights:
        return default_weights

    evidence = min(1.0, prior_predictions["fold"].nunique() / max(min_weight_selection_folds * 2, 1))
    effective_shrinkage = float(np.clip(shrinkage, 0, 1)) * evidence
    shrunk = {
        name: default_weights[name] + effective_shrinkage * (best_weights[name] - default_weights[name])
        for name in default_weights
    }
    total = sum(shrunk.values())
    return {name: value / total for name, value in shrunk.items()}


def _fit_rf_probabilities(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    cols: list[str],
    target_col: str,
    config: dict,
) -> np.ndarray:
    y_train = train_df[target_col].astype(int)
    if y_train.nunique(dropna=True) < 2:
        return np.full(len(test_df), float(y_train.mean()) if len(y_train) else 0.0)
    min_non_missing = max(30, int(len(train_df) * 0.2))
    usable_cols = [column for column in cols if int(train_df[column].notna().sum()) >= min_non_missing]
    if not usable_cols:
        return np.full(len(test_df), float(y_train.mean()))
    model = clone(make_model_candidates(config)["crash"]["random_forest"])
    fitted = _calibrated_classifier_if_possible(model, train_df[usable_cols], y_train, config)
    return np.clip(predict_proba_for_class(fitted, test_df[usable_cols], 1), 0, 1)


def _target_transformer_profile(lab_config: EnsembleLabConfig, target: str) -> tuple[str, str]:
    if target == "crash_5d_5pct":
        return lab_config.moderate_pooling or lab_config.pooling, lab_config.moderate_loss or lab_config.loss
    if target == "crash_5d_10pct":
        return lab_config.severe_pooling or lab_config.pooling, lab_config.severe_loss or lab_config.loss
    raise ValueError(f"지원하지 않는 ensemble target입니다: {target}")


def _transformer_fold_predictions(
    torch: Any,
    nn: Any,
    clean: pd.DataFrame,
    cols: list[str],
    target: str,
    split: Any,
    fold: int,
    lab_config: EnsembleLabConfig,
    prior_predictions: pd.DataFrame,
) -> pd.DataFrame:
    pooling, loss = _target_transformer_profile(lab_config, target)
    transformer_config = TransformerLabConfig(
        target=target,
        sequence_length=lab_config.sequence_length,
        max_folds=lab_config.max_folds,
        epochs=lab_config.epochs,
        batch_size=lab_config.batch_size,
        d_model=lab_config.d_model,
        nhead=lab_config.nhead,
        num_layers=lab_config.num_layers,
        dropout=lab_config.dropout,
        learning_rate=lab_config.learning_rate,
        random_state=lab_config.random_state,
        device=lab_config.device,
        pooling=pooling,
        loss=loss,
        focal_alpha=lab_config.focal_alpha,
        focal_gamma=lab_config.focal_gamma,
        pos_weight_cap=lab_config.pos_weight_cap,
        feature_selection=lab_config.feature_selection,
        max_features=lab_config.max_features,
        purge_days=lab_config.purge_days,
        positional_encoding=lab_config.positional_encoding,
        gradient_clip_norm=lab_config.gradient_clip_norm,
        weight_decay=lab_config.weight_decay,
        seed_count=lab_config.seed_count,
        prior_oos_calibration=lab_config.prior_oos_calibration,
        min_calibration_folds=lab_config.min_calibration_folds,
        min_calibration_events=lab_config.min_calibration_events,
    )
    prediction = _fit_predict_fold_members(torch, nn, clean, cols, target, split, transformer_config, fold)
    if prediction.empty:
        return pd.DataFrame(columns=["date", "transformer_raw_probability", "transformer_probability"])
    prior_for_calibration = pd.DataFrame()
    if not prior_predictions.empty and {"fold", "target", "transformer_raw_probability"}.issubset(prior_predictions.columns):
        prior_for_calibration = prior_predictions[
            ["fold", "target", "transformer_raw_probability"]
        ].rename(columns={"transformer_raw_probability": "raw_probability"})
    if lab_config.prior_oos_calibration:
        calibrated, source = _prior_oos_sigmoid_calibration(
            prediction["raw_probability"].to_numpy(dtype=float),
            prior_for_calibration,
            lab_config.min_calibration_folds,
            lab_config.min_calibration_events,
        )
    else:
        calibrated = prediction["raw_probability"].to_numpy(dtype=float)
        source = "disabled"
    return pd.DataFrame(
        {
            "date": prediction["date"],
            "transformer_raw_probability": prediction["raw_probability"],
            "transformer_probability": calibrated,
            "transformer_seed_std": prediction["seed_probability_std"],
            "transformer_calibration": source,
            "selected_feature_count": prediction["selected_feature_count"],
        }
    )


def _metric_rows(predictions: pd.DataFrame) -> list[dict[str, float | int | str]]:
    rows: list[dict[str, float | int | str]] = []
    model_columns = {
        "rf": "rf_probability",
        "transformer_raw": "transformer_raw_probability",
        "transformer": "transformer_probability",
        "rule": "rule_probability",
        "ensemble_fixed": "ensemble_fixed_probability",
        "ensemble_adaptive": "ensemble_adaptive_probability",
    }
    for target, target_frame in predictions.groupby("task", sort=True):
        for model_name, probability_col in model_columns.items():
            valid = target_frame[["target", probability_col]].dropna()
            if valid.empty:
                continue
            y_true = valid["target"].astype(int)
            y_prob = valid[probability_col].astype(float)
            top_decile_hit_rate, top_decile_lift, top_decile_event_capture = _top_decile_metrics(y_true, y_prob)
            fold_win_rate, comparable_folds = _fold_win_rate(target_frame, probability_col)
            brier = float(brier_score_loss(y_true, y_prob))
            climatology_brier = float(y_true.mean() * (1 - y_true.mean()))
            rows.append(
                {
                    "task": str(target),
                    "model": model_name,
                    "observations": int(len(valid)),
                    "event_count": int(y_true.sum()),
                    "event_rate": float(y_true.mean()),
                    "auc": _safe_auc(y_true, y_prob),
                    "average_precision": _safe_average_precision(y_true, y_prob),
                    "brier": brier,
                    "brier_skill": 1 - brier / climatology_brier if climatology_brier > 0 else float("nan"),
                    "top_decile_hit_rate": top_decile_hit_rate,
                    "top_decile_lift": top_decile_lift,
                    "top_decile_event_capture": top_decile_event_capture,
                    "fold_win_rate_vs_rf": fold_win_rate,
                    "comparable_folds": comparable_folds,
                    "first_date": str(target_frame["date"].min()),
                    "last_date": str(target_frame["date"].max()),
                }
            )
    return rows


def run_ensemble_lab(df: pd.DataFrame, config: dict, lab_config: EnsembleLabConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    targets = _validate_targets(lab_config.targets)
    pooling_values = [lab_config.pooling, lab_config.moderate_pooling, lab_config.severe_pooling]
    loss_values = [lab_config.loss, lab_config.moderate_loss, lab_config.severe_loss]
    if any(value not in {"last", "mean", "attention"} for value in pooling_values if value is not None):
        raise ValueError("ensemble pooling은 last, mean, attention 중 하나여야 합니다.")
    if any(value not in {"bce", "focal"} for value in loss_values if value is not None):
        raise ValueError("ensemble loss는 bce 또는 focal이어야 합니다.")
    if lab_config.seed_count < 1:
        raise ValueError("seed_count는 1 이상이어야 합니다.")
    default_weights = _fixed_weights(lab_config)
    torch, nn = _import_torch()
    np.random.seed(lab_config.random_state)
    torch.manual_seed(lab_config.random_state)

    clean = eligible_crash_training_frame(df)
    cols = feature_columns(clean)
    splits = make_walk_forward_splits(len(clean), config)
    if lab_config.max_folds > 0:
        splits = splits[-lab_config.max_folds :]
    if not splits:
        raise ValueError("ensemble lab을 실행할 walk-forward split이 부족합니다.")

    prediction_parts: list[pd.DataFrame] = []
    weight_rows: list[dict[str, float | int | str]] = []
    for fold, split in enumerate(splits, start=1):
        train_end = _purged_train_end(split, lab_config.purge_days)
        train_df = clean.iloc[split.train_start : train_end].reset_index(drop=True)
        test_df = clean.iloc[split.test_start : split.test_end].reset_index(drop=True)
        if train_df.empty or test_df.empty:
            continue
        if pd.to_datetime(train_df["date"].iloc[-1]) >= pd.to_datetime(test_df["date"].iloc[0]):
            raise ValueError("Ensemble split is invalid: train end date must be before test start date.")

        rule_predictions = crash_baseline_predictions(test_df)
        for target in targets:
            target_col = TASKS[target]["target_col"]
            prob_col = TASKS[target]["prob_col"]
            rf_probability = _fit_rf_probabilities(train_df, test_df, cols, target_col, config)
            prior = pd.concat(prediction_parts, ignore_index=True) if prediction_parts else pd.DataFrame()
            prior = prior.loc[prior.get("task", pd.Series(dtype=str)) == target] if not prior.empty else prior
            transformer_prediction = _transformer_fold_predictions(
                torch,
                nn,
                clean,
                cols,
                target,
                split,
                fold,
                lab_config,
                prior,
            )

            frame = pd.DataFrame(
                {
                    "date": test_df["date"].astype(str).to_numpy(),
                    "fold": fold,
                    "task": target,
                    "target": test_df[target_col].astype(int).to_numpy(),
                    "rf_probability": rf_probability,
                    "rule_probability": rule_predictions[prob_col].astype(float).to_numpy(),
                }
            )
            frame = frame.merge(transformer_prediction, on="date", how="left")
            frame["ensemble_fixed_probability"] = _weighted_average(frame, default_weights)

            adaptive_weights = (
                _select_adaptive_weights(
                    prior,
                    default_weights,
                    lab_config.min_weight_selection_folds,
                    min_weight_selection_events=lab_config.min_weight_selection_events,
                    weight_lookback_folds=lab_config.weight_lookback_folds,
                    max_brier_degradation=lab_config.max_brier_degradation,
                    shrinkage=lab_config.adaptive_weight_shrinkage,
                )
                if lab_config.adaptive_weights
                else default_weights
            )
            frame["ensemble_adaptive_probability"] = _weighted_average(frame, adaptive_weights)
            prediction_parts.append(frame)
            weight_rows.append(
                {
                    "fold": fold,
                    "task": target,
                    "train_start_date": str(train_df["date"].iloc[0]),
                    "train_end_date": str(train_df["date"].iloc[-1]),
                    "test_start_date": str(test_df["date"].iloc[0]),
                    "test_end_date": str(test_df["date"].iloc[-1]),
                    "purge_days": lab_config.purge_days,
                    "rf_weight": adaptive_weights["rf"],
                    "transformer_weight": adaptive_weights["transformer"],
                    "rule_weight": adaptive_weights["rule"],
                    "weight_source": "prior_oos" if adaptive_weights != default_weights else "fixed_default",
                }
            )

    predictions = pd.concat(prediction_parts, ignore_index=True)
    metrics = pd.DataFrame(_metric_rows(predictions))
    weights = pd.DataFrame(weight_rows)
    return predictions, metrics, weights


def write_ensemble_lab_outputs(
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    weights: pd.DataFrame,
    predictions_output: str | Path,
    metrics_output: str | Path,
    weights_output: str | Path,
    report_output: str | Path | None = None,
    lab_config: EnsembleLabConfig | None = None,
) -> None:
    predictions_path = Path(predictions_output)
    metrics_path = Path(metrics_output)
    weights_path = Path(weights_output)
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(predictions_path, index=False)
    metrics.to_csv(metrics_path, index=False)
    weights.to_csv(weights_path, index=False)
    if report_output is not None:
        write_ensemble_lab_report(metrics, weights, lab_config or EnsembleLabConfig(), report_output)


def _metric_text(value: Any, digits: int = 3) -> str:
    number = float(value)
    return "-" if not np.isfinite(number) else f"{number:.{digits}f}"


def write_ensemble_lab_report(
    metrics: pd.DataFrame,
    weights: pd.DataFrame,
    lab_config: EnsembleLabConfig,
    report_output: str | Path,
) -> None:
    output_path = Path(report_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model_labels = {
        "rf": "RF",
        "transformer_raw": "Transformer 원확률",
        "transformer": "Transformer OOS 보정" if lab_config.prior_oos_calibration else "Transformer",
        "rule": "Rule",
        "ensemble_fixed": "고정 앙상블",
        "ensemble_adaptive": "적응 앙상블",
    }
    lines = [
        "# Transformer·앙상블 급락 탐지 실험 결과",
        "",
        f"작성시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 실행 요약",
        "",
        f"- 대상: {', '.join(lab_config.targets)}",
        f"- Walk-forward: 최근 {lab_config.max_folds}개 fold, fold별 미래 데이터 미사용",
        f"- 타깃 경계: 향후 5일 라벨과 테스트 구간이 겹치지 않도록 train 말단 {lab_config.purge_days}일 제거",
        (
            f"- Transformer: {lab_config.positional_encoding} 위치 인코딩, "
            f"-5% {lab_config.moderate_pooling or lab_config.pooling}/{lab_config.moderate_loss or lab_config.loss}, "
            f"-10% {lab_config.severe_pooling or lab_config.pooling}/{lab_config.severe_loss or lab_config.loss}, "
            f"seed {lab_config.seed_count}개 평균"
        ),
        f"- 확률 보정: {'이전 fold OOS sigmoid' if lab_config.prior_oos_calibration else '미사용'}",
        "- 운영 반영: 미반영. 본 결과는 challenger 연구용입니다.",
        "",
        "## OOS 성능",
        "",
    ]

    for task, task_metrics in metrics.groupby("task", sort=True):
        rf = task_metrics.loc[task_metrics["model"] == "rf"].iloc[0]
        event_rate = float(rf["event_rate"])
        lines.extend(
            [
                f"### {task}",
                "",
                f"검증기간 {rf['first_date']} ~ {rf['last_date']}, 관측치 {int(rf['observations'])}개, 이벤트 {int(rf['event_count'])}개({event_rate:.1%})",
                "",
                "| 모델 | AP | AUC | Brier | Brier skill | 상위 10% 적중률 | 이벤트 포착률 | RF 대비 fold 승률 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for _, row in task_metrics.iterrows():
            if not lab_config.prior_oos_calibration and row["model"] == "transformer_raw":
                continue
            win_rate = "-" if int(row["comparable_folds"]) == 0 else f"{float(row['fold_win_rate_vs_rf']):.1%}"
            lines.append(
                f"| {model_labels.get(str(row['model']), row['model'])} | {_metric_text(row['average_precision'])} | "
                f"{_metric_text(row['auc'])} | {_metric_text(row['brier'])} | {_metric_text(row['brier_skill'])} | "
                f"{float(row['top_decile_hit_rate']):.1%} | {float(row['top_decile_event_capture']):.1%} | {win_rate} |"
            )

        candidates = task_metrics.loc[task_metrics["model"].isin(["transformer", "ensemble_fixed", "ensemble_adaptive"])].copy()
        brier_limit = float(rf["brier"]) * 1.05
        balanced = candidates.loc[candidates["brier"] <= brier_limit]
        if balanced.empty:
            recommendation = "RF 유지: AP 개선 후보의 확률 보정이 RF 대비 5% 이내 조건을 충족하지 못했습니다."
        else:
            best = balanced.sort_values(["average_precision", "brier"], ascending=[False, True]).iloc[0]
            ap_change = float(best["average_precision"]) / max(float(rf["average_precision"]), 1e-9) - 1
            if ap_change > 0:
                promotion_gaps: list[str] = []
                if int(best["observations"]) < 500:
                    promotion_gaps.append(f"OOS {int(best['observations'])}개로 500개 기준 미달")
                required_events = 50 if task == "crash_5d_5pct" else 30
                if int(best["event_count"]) < required_events:
                    promotion_gaps.append(f"이벤트 {int(best['event_count'])}개로 {required_events}개 기준 미달")
                fold_win_rate = float(best["fold_win_rate_vs_rf"])
                if not np.isfinite(fold_win_rate) or fold_win_rate < 0.6:
                    promotion_gaps.append(
                        "RF 대비 fold 승률이 "
                        + (f"{fold_win_rate:.1%}" if np.isfinite(fold_win_rate) else "계산 불가")
                        + "로 60% 기준 미달"
                    )
                recommendation = (
                    f"연구상 균형 후보는 {model_labels.get(str(best['model']), best['model'])}입니다. "
                    f"RF 대비 AP가 {ap_change:+.1%}이고 Brier 허용범위 안입니다."
                )
                if promotion_gaps:
                    recommendation += " 다만 " + ", ".join(promotion_gaps) + "입니다."
            else:
                recommendation = "RF 유지: Brier 조건을 만족하면서 RF의 AP를 넘는 challenger가 없습니다."
        lines.extend(["", f"판단: {recommendation}", ""])

    adaptive_rows = weights.loc[weights["weight_source"] == "prior_oos"] if not weights.empty else pd.DataFrame()
    lines.extend(
        [
            "## 해석과 제한",
            "",
            f"적응 가중치는 충분한 이전 OOS fold와 사건이 있을 때만 사용했으며 실제 적용 fold는 {len(adaptive_rows)}개입니다.",
            "AP는 희소 급락을 위험 순위 상단에 모으는 능력, AUC는 전체 순위 분리력, Brier는 확률 정확도를 뜻합니다.",
            "검증기간과 급락 이벤트 수가 작아 한두 사건에 수치가 크게 움직일 수 있습니다. 특히 -10% 결과는 방향성 증거로만 봐야 합니다.",
            "구조 선택과 seed 평균은 초기화 변동성을 줄이지만, 미래 성능 개선을 보장하지 않습니다.",
            "여러 구조를 같은 OOS 구간에서 비교했으므로 선택 편향이 남아 있습니다. 다음 신규 데이터는 손대지 않은 forward 표본으로 평가해야 합니다.",
            "운영 승격 전에는 최소 500영업일 OOS, -10% 이벤트 30건 이상, 비용을 반영한 경보 효용 검증이 필요합니다.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")
