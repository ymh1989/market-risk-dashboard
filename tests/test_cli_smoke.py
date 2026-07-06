from __future__ import annotations

from pathlib import Path

import pandas as pd

from kospi_risk.cli import main


def write_config(path: Path) -> None:
    metrics = path.parent / "model_metrics.csv"
    buckets = path.parent / "score_bucket_analysis.csv"
    predictions = path.parent / "walk_forward_predictions.csv"
    path.write_text(
        f"""
random_state: 42
paths:
  metrics: {metrics}
  score_bucket_analysis: {buckets}
  walk_forward_predictions: {predictions}
validation:
  initial_train_days: 520
  test_days: 40
  step_days: 40
  min_train_rows: 500
models:
  rf_estimators: 10
  calibration_enabled: false
""".strip(),
        encoding="utf-8",
    )


def test_cli_pipeline_smoke(tmp_path):
    raw = tmp_path / "market_data.csv"
    features = tmp_path / "features.parquet"
    config = tmp_path / "config.yaml"
    model = tmp_path / "model.joblib"
    report = tmp_path / "backtest_report.md"
    latest = tmp_path / "latest_signal.csv"
    write_config(config)

    main(["make-sample-data", "--output", str(raw), "--rows", "720", "--seed", "14"])
    main(["build-features", "--input", str(raw), "--output", str(features), "--config", str(config)])
    main(["train", "--features", str(features), "--config", str(config), "--model-output", str(model)])
    main(["backtest", "--features", str(features), "--config", str(config), "--output", str(report)])
    main(["predict-latest", "--features", str(features), "--config", str(config), "--model", str(model), "--output", str(latest)])

    assert raw.exists()
    assert features.exists()
    assert model.exists()
    assert report.exists()
    signal = pd.read_csv(latest)
    walk_forward = pd.read_csv(tmp_path / "walk_forward_predictions.csv")
    assert len(signal) == 1
    assert walk_forward["date"].is_unique
    assert walk_forward["prob_risk_off"].dropna().between(0, 1).all()
    assert walk_forward["prob_crash_5d_5pct"].between(0, 1).all()
    assert walk_forward["prob_crash_5d_10pct"].between(0, 1).all()
    assert walk_forward["date"].max() > walk_forward.loc[walk_forward["prob_risk_off"].notna(), "date"].max()
    assert signal.loc[0, "els_risk_score"] >= 0
    assert signal.loc[0, "els_risk_score"] <= 100
