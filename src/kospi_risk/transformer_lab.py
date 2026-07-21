from __future__ import annotations

import json
import math
import warnings
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from .models import eligible_crash_training_frame, feature_columns
from .validation import make_walk_forward_splits


TARGET_MAP = {
    "crash_5d_5pct": "target_crash_5d_5pct",
    "crash_5d_10pct": "target_crash_5d_10pct",
}


@dataclass(frozen=True)
class TransformerLabConfig:
    target: str = "crash_5d_5pct"
    sequence_length: int = 20
    max_folds: int = 3
    epochs: int = 12
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
    purge_days: int = 5
    positional_encoding: str = "none"
    gradient_clip_norm: float = 1.0
    weight_decay: float = 0.0001
    seed_count: int = 1
    prior_oos_calibration: bool = False
    min_calibration_folds: int = 4
    min_calibration_events: int = 8


def _import_torch() -> tuple[Any, Any]:
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - depends on optional dependency.
        raise RuntimeError(
            "Transformer Lab은 선택 의존성 torch가 필요합니다. "
            "실행하려면 `python -m pip install -e '.[transformer]'` 후 다시 시도하세요."
        ) from exc
    return torch, nn


def _resolve_torch_device(torch: Any, requested_device: str = "auto") -> Any:
    device = requested_device.lower()
    if device == "auto":
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    if device == "mps":
        if getattr(torch.backends, "mps", None) is None or not torch.backends.mps.is_available():
            raise RuntimeError("요청한 device=mps를 사용할 수 없습니다. PyTorch MPS 사용 가능 여부를 확인하세요.")
        return torch.device("mps")
    if device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("요청한 device=cuda를 사용할 수 없습니다. CUDA 사용 가능 여부를 확인하세요.")
        return torch.device("cuda")
    if device == "cpu":
        return torch.device("cpu")
    raise ValueError(f"지원하지 않는 torch device입니다: {requested_device}")


def ensure_transformer_lab_available() -> None:
    _import_torch()


def _purged_train_end(split: Any, purge_days: int) -> int:
    if purge_days < 0:
        raise ValueError("purge_days는 0 이상이어야 합니다.")
    return max(int(split.train_start), int(split.train_end) - int(purge_days))


def _fold_seed(base_seed: int, fold: int, target: str, member: int = 0) -> int:
    target_offset = list(TARGET_MAP).index(target) * 104_729
    return int(base_seed + fold * 1_009 + member * 7_919 + target_offset)


def _prior_oos_sigmoid_calibration(
    current_probability: np.ndarray,
    prior_predictions: pd.DataFrame,
    min_folds: int,
    min_events: int,
) -> tuple[np.ndarray, str]:
    raw = np.clip(np.asarray(current_probability, dtype=float), 1e-5, 1 - 1e-5)
    required = {"fold", "target", "raw_probability"}
    if prior_predictions.empty or not required.issubset(prior_predictions.columns):
        return raw, "raw"
    prior = prior_predictions.loc[:, list(required)].dropna()
    events = int(prior["target"].astype(int).sum())
    non_events = int(len(prior) - events)
    if prior["fold"].nunique() < min_folds or events < min_events or non_events < min_events:
        return raw, "raw"

    prior_raw = np.clip(prior["raw_probability"].to_numpy(dtype=float), 1e-5, 1 - 1e-5)
    prior_logit = np.log(prior_raw / (1 - prior_raw)).reshape(-1, 1)
    current_logit = np.log(raw / (1 - raw)).reshape(-1, 1)
    calibrator = LogisticRegression(C=0.5, solver="lbfgs", random_state=42)
    calibrator.fit(prior_logit, prior["target"].astype(int))
    if float(calibrator.coef_[0, 0]) <= 0:
        return raw, "raw_non_monotonic_guard"
    calibrated = calibrator.predict_proba(current_logit)[:, 1]
    return np.clip(calibrated, 0, 1), "prior_oos_sigmoid"


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_prob))


def _safe_average_precision(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(average_precision_score(y_true, y_prob))


def _sequence_arrays(
    transformed_features: np.ndarray,
    targets: np.ndarray,
    dates: pd.Series,
    row_indices: range,
    sequence_length: int,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    xs: list[np.ndarray] = []
    ys: list[float] = []
    sequence_dates: list[str] = []
    for idx in row_indices:
        start = idx - sequence_length + 1
        if start < 0:
            continue
        target_value = targets[idx]
        if not np.isfinite(target_value):
            continue
        xs.append(transformed_features[start : idx + 1])
        ys.append(float(target_value))
        sequence_dates.append(pd.to_datetime(dates.iloc[idx]).date().isoformat())
    if not xs:
        return (
            np.empty((0, sequence_length, transformed_features.shape[1]), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            [],
        )
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32), sequence_dates


def _iter_batches(torch: Any, x: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool, device: Any | None = None) -> Any:
    indices = np.arange(len(x))
    if shuffle:
        np.random.shuffle(indices)
    for start in range(0, len(indices), batch_size):
        batch_idx = indices[start : start + batch_size]
        batch_x = torch.tensor(x[batch_idx], dtype=torch.float32)
        batch_y = torch.tensor(y[batch_idx], dtype=torch.float32)
        if device is not None:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
        yield batch_x, batch_y


def _pos_weight(torch: Any, y: np.ndarray, cap: float = 0.0) -> Any:
    positives = float(np.sum(y == 1))
    negatives = float(np.sum(y == 0))
    if positives <= 0:
        return torch.tensor(1.0, dtype=torch.float32)
    weight = max(1.0, negatives / positives)
    if cap > 0:
        weight = min(weight, cap)
    return torch.tensor(weight, dtype=torch.float32)


def _select_feature_columns(train_df: pd.DataFrame, cols: list[str], target_col: str, lab_config: TransformerLabConfig) -> list[str]:
    if lab_config.feature_selection == "all" or lab_config.max_features <= 0 or lab_config.max_features >= len(cols):
        return cols
    if lab_config.feature_selection != "spearman":
        raise ValueError(f"지원하지 않는 feature_selection입니다: {lab_config.feature_selection}")

    target = train_df[target_col].astype(float)
    features = train_df[cols].astype(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        correlations = features.corrwith(target, axis=0, method="spearman").abs().fillna(0.0)
    valid_counts = features.notna().sum(axis=0)
    unique_counts = features.nunique(dropna=True)
    correlations.loc[(valid_counts < 30) | (unique_counts < 2)] = 0.0
    scores = [(float(correlations.get(column, 0.0)), column) for column in cols]
    selected = [column for _, column in sorted(scores, key=lambda item: (-item[0], item[1]))[: lab_config.max_features]]
    return selected or cols


def _loss_function(torch: Any, nn: Any, train_y: np.ndarray, lab_config: TransformerLabConfig, device: Any) -> Any:
    if lab_config.loss == "bce":
        return nn.BCEWithLogitsLoss(pos_weight=_pos_weight(torch, train_y, lab_config.pos_weight_cap).to(device))
    if lab_config.loss == "focal":
        alpha = float(lab_config.focal_alpha)
        gamma = float(lab_config.focal_gamma)

        class FocalLoss(nn.Module):
            def forward(self, logits: Any, targets: Any) -> Any:
                bce = nn.functional.binary_cross_entropy_with_logits(logits, targets, reduction="none")
                probability = torch.sigmoid(logits)
                pt = torch.where(targets == 1, probability, 1 - probability)
                alpha_t = torch.where(targets == 1, torch.tensor(alpha, device=targets.device), torch.tensor(1 - alpha, device=targets.device))
                return (alpha_t * torch.pow(1 - pt, gamma) * bce).mean()

        return FocalLoss()
    raise ValueError(f"지원하지 않는 Transformer loss입니다: {lab_config.loss}")


def _build_model(torch: Any, nn: Any, input_dim: int, lab_config: TransformerLabConfig) -> Any:
    class SinusoidalPositionalEncoding(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            positions = torch.arange(lab_config.sequence_length, dtype=torch.float32).unsqueeze(1)
            frequency = torch.exp(
                torch.arange(0, lab_config.d_model, 2, dtype=torch.float32)
                * (-math.log(10_000.0) / lab_config.d_model)
            )
            encoding = torch.zeros(lab_config.sequence_length, lab_config.d_model, dtype=torch.float32)
            encoding[:, 0::2] = torch.sin(positions * frequency)
            odd_width = encoding[:, 1::2].shape[1]
            encoding[:, 1::2] = torch.cos(positions * frequency[:odd_width])
            self.register_buffer("encoding", encoding.unsqueeze(0), persistent=False)

        def forward(self, x: Any) -> Any:
            return x + self.encoding[:, : x.shape[1], :]

    class TabularTransformer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.input_projection = nn.Linear(input_dim, lab_config.d_model)
            self.positional_encoding = SinusoidalPositionalEncoding()
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=lab_config.d_model,
                nhead=lab_config.nhead,
                dim_feedforward=lab_config.d_model * 4,
                dropout=lab_config.dropout,
                batch_first=True,
                activation="gelu",
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=lab_config.num_layers)
            self.attention_pool = nn.Linear(lab_config.d_model, 1)
            self.head = nn.Sequential(
                nn.LayerNorm(lab_config.d_model),
                nn.Linear(lab_config.d_model, 1),
            )

        def forward(self, x: Any) -> Any:
            projected = self.input_projection(x)
            if lab_config.positional_encoding == "sinusoidal":
                projected = self.positional_encoding(projected)
            elif lab_config.positional_encoding != "none":
                raise ValueError(f"지원하지 않는 positional_encoding입니다: {lab_config.positional_encoding}")
            encoded = self.encoder(projected)
            if lab_config.pooling == "last":
                pooled = encoded[:, -1, :]
            elif lab_config.pooling == "mean":
                pooled = encoded.mean(dim=1)
            elif lab_config.pooling == "attention":
                weights = torch.softmax(self.attention_pool(encoded).squeeze(-1), dim=1)
                pooled = torch.sum(encoded * weights.unsqueeze(-1), dim=1)
            else:
                raise ValueError(f"지원하지 않는 Transformer pooling입니다: {lab_config.pooling}")
            return self.head(pooled).squeeze(-1)

    torch.manual_seed(lab_config.random_state)
    return TabularTransformer()


def _prepare_fold_arrays(
    clean: pd.DataFrame,
    cols: list[str],
    target_col: str,
    split: Any,
    lab_config: TransformerLabConfig,
) -> dict[str, Any] | None:
    train_end = _purged_train_end(split, lab_config.purge_days)
    train_df = clean.iloc[split.train_start : train_end].reset_index(drop=True)
    if train_df.empty:
        return None
    selected_cols = _select_feature_columns(train_df, cols, target_col, lab_config)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    train_features = train_df[selected_cols]
    imputer.fit(train_features)
    scaler.fit(imputer.transform(train_features))

    all_features = scaler.transform(imputer.transform(clean[selected_cols]))
    targets = clean[target_col].to_numpy(dtype=float)
    dates = clean["date"]

    train_x, train_y, _ = _sequence_arrays(
        all_features,
        targets,
        dates,
        range(split.train_start, train_end),
        lab_config.sequence_length,
    )
    test_x, test_y, test_dates = _sequence_arrays(
        all_features,
        targets,
        dates,
        range(split.test_start, split.test_end),
        lab_config.sequence_length,
    )
    if len(train_x) == 0 or len(test_x) == 0 or len(np.unique(train_y)) < 2:
        return None
    return {
        "train_x": train_x,
        "train_y": train_y,
        "test_x": test_x,
        "test_y": test_y,
        "test_dates": test_dates,
        "selected_feature_count": len(selected_cols),
        "train_end_date": pd.to_datetime(clean["date"].iloc[train_end - 1]).date().isoformat(),
    }


def _fit_predict_prepared(
    torch: Any,
    nn: Any,
    prepared: dict[str, Any],
    lab_config: TransformerLabConfig,
) -> np.ndarray:
    device = _resolve_torch_device(torch, lab_config.device)
    train_x = prepared["train_x"]
    train_y = prepared["train_y"]
    test_x = prepared["test_x"]
    test_y = prepared["test_y"]

    model = _build_model(torch, nn, train_x.shape[-1], lab_config).to(device)
    criterion = _loss_function(torch, nn, train_y, lab_config, device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=lab_config.learning_rate,
        weight_decay=lab_config.weight_decay,
    )

    model.train()
    for _ in range(lab_config.epochs):
        for batch_x, batch_y in _iter_batches(torch, train_x, train_y, lab_config.batch_size, shuffle=True, device=device):
            optimizer.zero_grad()
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            if lab_config.gradient_clip_norm > 0:
                nn.utils.clip_grad_norm_(model.parameters(), lab_config.gradient_clip_norm)
            optimizer.step()

    model.eval()
    probabilities: list[np.ndarray] = []
    with torch.no_grad():
        for batch_x, _ in _iter_batches(torch, test_x, test_y, lab_config.batch_size, shuffle=False, device=device):
            probabilities.append(torch.sigmoid(model(batch_x)).cpu().numpy())
    return np.concatenate(probabilities) if probabilities else np.array([], dtype=float)


def _fit_predict_fold(
    torch: Any,
    nn: Any,
    clean: pd.DataFrame,
    cols: list[str],
    target_col: str,
    split: Any,
    lab_config: TransformerLabConfig,
    fold: int,
) -> pd.DataFrame:
    prepared = _prepare_fold_arrays(clean, cols, target_col, split, lab_config)
    if prepared is None:
        return pd.DataFrame()
    prob = _fit_predict_prepared(torch, nn, prepared, lab_config)
    return pd.DataFrame(
        {
            "date": prepared["test_dates"],
            "fold": fold,
            "target": prepared["test_y"].astype(int),
            "probability": prob,
            "selected_feature_count": prepared["selected_feature_count"],
            "train_end_date": prepared["train_end_date"],
        }
    )


def _fit_predict_fold_members(
    torch: Any,
    nn: Any,
    clean: pd.DataFrame,
    cols: list[str],
    target: str,
    split: Any,
    lab_config: TransformerLabConfig,
    fold: int,
) -> pd.DataFrame:
    prepared = _prepare_fold_arrays(clean, cols, TARGET_MAP[target], split, lab_config)
    if prepared is None:
        return pd.DataFrame()
    parts: list[tuple[int, pd.DataFrame]] = []
    for member in range(lab_config.seed_count):
        member_config = replace(
            lab_config,
            random_state=_fold_seed(lab_config.random_state, fold, target, member),
        )
        np.random.seed(member_config.random_state)
        torch.manual_seed(member_config.random_state)
        probability = _fit_predict_prepared(torch, nn, prepared, member_config)
        prediction = pd.DataFrame(
            {
                "date": prepared["test_dates"],
                "fold": fold,
                "target": prepared["test_y"].astype(int),
                "probability": probability,
                "selected_feature_count": prepared["selected_feature_count"],
                "train_end_date": prepared["train_end_date"],
            }
        )
        if prediction.empty:
            continue
        parts.append((member, prediction.rename(columns={"probability": f"member_{member}_probability"})))
    if not parts:
        return pd.DataFrame()

    merged = parts[0][1]
    for member, part in parts[1:]:
        merged = merged.merge(
            part[["date", f"member_{member}_probability"]],
            on="date",
            how="inner",
        )
    probability_columns = [column for column in merged.columns if column.startswith("member_")]
    merged["raw_probability"] = merged[probability_columns].mean(axis=1)
    merged["seed_probability_std"] = merged[probability_columns].std(axis=1, ddof=0)
    return merged.drop(columns=probability_columns)


def run_transformer_lab(df: pd.DataFrame, lab_config: TransformerLabConfig) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    if lab_config.target not in TARGET_MAP:
        raise ValueError(f"지원하지 않는 Transformer Lab target입니다: {lab_config.target}")
    if lab_config.sequence_length < 2:
        raise ValueError("sequence_length는 2 이상이어야 합니다.")
    if lab_config.pooling not in {"last", "mean", "attention"}:
        raise ValueError(f"지원하지 않는 Transformer pooling입니다: {lab_config.pooling}")
    if lab_config.loss not in {"bce", "focal"}:
        raise ValueError(f"지원하지 않는 Transformer loss입니다: {lab_config.loss}")
    if lab_config.feature_selection not in {"all", "spearman"}:
        raise ValueError(f"지원하지 않는 feature_selection입니다: {lab_config.feature_selection}")
    if lab_config.positional_encoding not in {"none", "sinusoidal"}:
        raise ValueError(f"지원하지 않는 positional_encoding입니다: {lab_config.positional_encoding}")
    if lab_config.seed_count < 1:
        raise ValueError("seed_count는 1 이상이어야 합니다.")
    if lab_config.d_model % lab_config.nhead != 0:
        raise ValueError("d_model은 nhead로 나누어떨어져야 합니다.")

    torch, nn = _import_torch()
    device = _resolve_torch_device(torch, lab_config.device)
    np.random.seed(lab_config.random_state)
    torch.manual_seed(lab_config.random_state)

    clean = eligible_crash_training_frame(df)
    cols = feature_columns(clean)
    target_col = TARGET_MAP[lab_config.target]
    splits = make_walk_forward_splits(len(clean), {"validation": {"initial_train_days": 1260, "test_days": 21, "step_days": 21}})
    if lab_config.max_folds > 0:
        splits = splits[-lab_config.max_folds :]
    if not splits:
        raise ValueError("Transformer Lab을 실행할 walk-forward split이 부족합니다.")

    prediction_parts: list[pd.DataFrame] = []
    for fold, split in enumerate(splits, start=1):
        prediction = _fit_predict_fold_members(torch, nn, clean, cols, lab_config.target, split, lab_config, fold)
        if not prediction.empty:
            prior = pd.concat(prediction_parts, ignore_index=True) if prediction_parts else pd.DataFrame()
            if lab_config.prior_oos_calibration:
                calibrated, calibration_source = _prior_oos_sigmoid_calibration(
                    prediction["raw_probability"].to_numpy(dtype=float),
                    prior,
                    lab_config.min_calibration_folds,
                    lab_config.min_calibration_events,
                )
            else:
                calibrated = prediction["raw_probability"].to_numpy(dtype=float)
                calibration_source = "disabled"
            prediction["probability"] = calibrated
            prediction["calibration_source"] = calibration_source
            prediction_parts.append(prediction)
    if not prediction_parts:
        raise ValueError("Transformer Lab 예측을 만들 수 없습니다. 표본 또는 클래스 분포를 확인하세요.")

    predictions = pd.concat(prediction_parts, ignore_index=True)
    y_true = predictions["target"].to_numpy(dtype=int)
    y_prob = predictions["probability"].to_numpy(dtype=float)
    y_raw_prob = predictions["raw_probability"].to_numpy(dtype=float)
    metrics: dict[str, float | int | str] = {
        "model": "transformer_encoder",
        "target": lab_config.target,
        "sequenceLength": lab_config.sequence_length,
        "folds": int(predictions["fold"].nunique()),
        "observations": int(len(predictions)),
        "eventCount": int(y_true.sum()),
        "eventRate": float(y_true.mean()),
        "auc": _safe_auc(y_true, y_prob),
        "averagePrecision": _safe_average_precision(y_true, y_prob),
        "brier": float(brier_score_loss(y_true, y_prob)),
        "rawAveragePrecision": _safe_average_precision(y_true, y_raw_prob),
        "rawBrier": float(brier_score_loss(y_true, y_raw_prob)),
        "firstDate": str(predictions["date"].iloc[0]),
        "lastDate": str(predictions["date"].iloc[-1]),
        "device": str(device),
        "pooling": lab_config.pooling,
        "loss": lab_config.loss,
        "featureSelection": lab_config.feature_selection,
        "maxFeatures": int(lab_config.max_features),
        "posWeightCap": float(lab_config.pos_weight_cap),
        "purgeDays": int(lab_config.purge_days),
        "positionalEncoding": lab_config.positional_encoding,
        "seedCount": int(lab_config.seed_count),
        "priorOosCalibration": bool(lab_config.prior_oos_calibration),
        "averageSelectedFeatures": float(predictions["selected_feature_count"].mean()),
        "averageSeedProbabilityStd": float(predictions["seed_probability_std"].mean()),
    }
    return predictions, metrics


def write_transformer_lab_outputs(
    predictions: pd.DataFrame,
    metrics: dict[str, float | int | str],
    predictions_output: str | Path,
    metrics_output: str | Path,
) -> None:
    predictions_path = Path(predictions_output)
    metrics_path = Path(metrics_output)
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(predictions_path, index=False)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def run_transformer_lab_optimization(
    df: pd.DataFrame,
    targets: list[str],
    sequence_lengths: list[int],
    d_models: list[int],
    num_layers_values: list[int],
    epochs_values: list[int],
    max_folds: int,
    batch_size: int,
    nhead: int,
    dropout: float,
    learning_rate: float,
    random_state: int,
    device: str = "auto",
    pooling_values: list[str] | None = None,
    loss_values: list[str] | None = None,
    max_features_values: list[int] | None = None,
    feature_selection: str = "all",
    focal_alpha: float = 0.75,
    focal_gamma: float = 2.0,
    pos_weight_cap: float = 0.0,
    progress_output: str | Path | None = None,
    purge_days: int = 5,
    positional_encoding: str = "none",
    gradient_clip_norm: float = 1.0,
    weight_decay: float = 0.0001,
    seed_count: int = 1,
    prior_oos_calibration: bool = False,
    min_calibration_folds: int = 4,
    min_calibration_events: int = 8,
) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []
    pooling_values = pooling_values or ["last"]
    loss_values = loss_values or ["bce"]
    max_features_values = max_features_values or [0]

    def write_progress() -> None:
        if progress_output is None:
            return
        progress_path = Path(progress_output)
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(progress_path, index=False)

    for target in targets:
        for sequence_length in sequence_lengths:
            for d_model in d_models:
                if d_model % nhead != 0:
                    rows.append(
                        {
                            "target": target,
                            "sequenceLength": sequence_length,
                            "dModel": d_model,
                            "numLayers": "",
                            "epochs": "",
                            "status": f"skipped: d_model({d_model}) must be divisible by nhead({nhead})",
                        }
                    )
                    continue
                for num_layers in num_layers_values:
                    for epochs in epochs_values:
                        for pooling in pooling_values:
                            for loss in loss_values:
                                for max_features in max_features_values:
                                    config = TransformerLabConfig(
                                        target=target,
                                        sequence_length=sequence_length,
                                        max_folds=max_folds,
                                        epochs=epochs,
                                        batch_size=batch_size,
                                        d_model=d_model,
                                        nhead=nhead,
                                        num_layers=num_layers,
                                        dropout=dropout,
                                        learning_rate=learning_rate,
                                        random_state=random_state,
                                        device=device,
                                        pooling=pooling,
                                        loss=loss,
                                        focal_alpha=focal_alpha,
                                        focal_gamma=focal_gamma,
                                        pos_weight_cap=pos_weight_cap,
                                        feature_selection=feature_selection,
                                        max_features=max_features,
                                        purge_days=purge_days,
                                        positional_encoding=positional_encoding,
                                        gradient_clip_norm=gradient_clip_norm,
                                        weight_decay=weight_decay,
                                        seed_count=seed_count,
                                        prior_oos_calibration=prior_oos_calibration,
                                        min_calibration_folds=min_calibration_folds,
                                        min_calibration_events=min_calibration_events,
                                    )
                                    try:
                                        _, metrics = run_transformer_lab(df, config)
                                        rows.append({**metrics, "dModel": d_model, "numLayers": num_layers, "epochs": epochs, "status": "ok"})
                                    except Exception as exc:  # pragma: no cover - defensive experiment logging.
                                        rows.append(
                                            {
                                                "target": target,
                                                "sequenceLength": sequence_length,
                                                "dModel": d_model,
                                                "numLayers": num_layers,
                                                "epochs": epochs,
                                                "pooling": pooling,
                                                "loss": loss,
                                                "featureSelection": feature_selection,
                                                "maxFeatures": max_features,
                                                "status": f"failed: {exc}",
                                            }
                                        )
                                    write_progress()
    result = pd.DataFrame(rows)
    if result.empty or "averagePrecision" not in result.columns:
        return result
    result["_rankAveragePrecision"] = pd.to_numeric(result["averagePrecision"], errors="coerce").fillna(-np.inf)
    result["_rankBrier"] = pd.to_numeric(result["brier"], errors="coerce").fillna(np.inf)
    result = result.sort_values(["target", "_rankAveragePrecision", "_rankBrier"], ascending=[True, False, True]).drop(
        columns=["_rankAveragePrecision", "_rankBrier"]
    )
    return result.reset_index(drop=True)
