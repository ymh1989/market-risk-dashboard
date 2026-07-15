from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
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


def ensure_transformer_lab_available() -> None:
    _import_torch()


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


def _iter_batches(torch: Any, x: np.ndarray, y: np.ndarray, batch_size: int, shuffle: bool) -> Any:
    indices = np.arange(len(x))
    if shuffle:
        np.random.shuffle(indices)
    for start in range(0, len(indices), batch_size):
        batch_idx = indices[start : start + batch_size]
        yield torch.tensor(x[batch_idx], dtype=torch.float32), torch.tensor(y[batch_idx], dtype=torch.float32)


def _pos_weight(torch: Any, y: np.ndarray) -> Any:
    positives = float(np.sum(y == 1))
    negatives = float(np.sum(y == 0))
    if positives <= 0:
        return torch.tensor(1.0, dtype=torch.float32)
    return torch.tensor(max(1.0, negatives / positives), dtype=torch.float32)


def _build_model(torch: Any, nn: Any, input_dim: int, lab_config: TransformerLabConfig) -> Any:
    class TabularTransformer(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.input_projection = nn.Linear(input_dim, lab_config.d_model)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=lab_config.d_model,
                nhead=lab_config.nhead,
                dim_feedforward=lab_config.d_model * 4,
                dropout=lab_config.dropout,
                batch_first=True,
                activation="gelu",
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=lab_config.num_layers)
            self.head = nn.Sequential(
                nn.LayerNorm(lab_config.d_model),
                nn.Linear(lab_config.d_model, 1),
            )

        def forward(self, x: Any) -> Any:
            encoded = self.encoder(self.input_projection(x))
            return self.head(encoded[:, -1, :]).squeeze(-1)

    torch.manual_seed(lab_config.random_state)
    return TabularTransformer()


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
    train_df = clean.iloc[split.train_start : split.train_end].reset_index(drop=True)
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    train_features = train_df[cols]
    imputer.fit(train_features)
    scaler.fit(imputer.transform(train_features))

    all_features = scaler.transform(imputer.transform(clean[cols]))
    targets = clean[target_col].to_numpy(dtype=float)
    dates = clean["date"]

    train_x, train_y, _ = _sequence_arrays(
        all_features,
        targets,
        dates,
        range(split.train_start, split.train_end),
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
        return pd.DataFrame()

    model = _build_model(torch, nn, train_x.shape[-1], lab_config)
    criterion = nn.BCEWithLogitsLoss(pos_weight=_pos_weight(torch, train_y))
    optimizer = torch.optim.AdamW(model.parameters(), lr=lab_config.learning_rate)

    model.train()
    for _ in range(lab_config.epochs):
        for batch_x, batch_y in _iter_batches(torch, train_x, train_y, lab_config.batch_size, shuffle=True):
            optimizer.zero_grad()
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()

    model.eval()
    probabilities: list[np.ndarray] = []
    with torch.no_grad():
        for batch_x, _ in _iter_batches(torch, test_x, test_y, lab_config.batch_size, shuffle=False):
            probabilities.append(torch.sigmoid(model(batch_x)).cpu().numpy())
    prob = np.concatenate(probabilities) if probabilities else np.array([], dtype=float)
    return pd.DataFrame(
        {
            "date": test_dates,
            "fold": fold,
            "target": test_y.astype(int),
            "probability": prob,
        }
    )


def run_transformer_lab(df: pd.DataFrame, lab_config: TransformerLabConfig) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    if lab_config.target not in TARGET_MAP:
        raise ValueError(f"지원하지 않는 Transformer Lab target입니다: {lab_config.target}")
    if lab_config.sequence_length < 2:
        raise ValueError("sequence_length는 2 이상이어야 합니다.")

    torch, nn = _import_torch()
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

    prediction_parts = []
    for fold, split in enumerate(splits, start=1):
        prediction = _fit_predict_fold(torch, nn, clean, cols, target_col, split, lab_config, fold)
        if not prediction.empty:
            prediction_parts.append(prediction)
    if not prediction_parts:
        raise ValueError("Transformer Lab 예측을 만들 수 없습니다. 표본 또는 클래스 분포를 확인하세요.")

    predictions = pd.concat(prediction_parts, ignore_index=True)
    y_true = predictions["target"].to_numpy(dtype=int)
    y_prob = predictions["probability"].to_numpy(dtype=float)
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
        "firstDate": str(predictions["date"].iloc[0]),
        "lastDate": str(predictions["date"].iloc[-1]),
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
