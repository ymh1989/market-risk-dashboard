from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import load_config
from .data_loader import load_frame, make_sample_market_data, save_frame
from .ensemble_lab import EnsembleLabConfig, run_ensemble_lab, write_ensemble_lab_outputs
from .feature_engineering import build_features
from .market_data_fetcher import fetch_and_save_market_data
from .models import load_bundle, predict_bundle, save_bundle, train_bundle
from .reporting import write_backtest_report
from .scoring import add_els_scores, score_bucket_analysis
from .targets import add_targets
from .transformer_lab import (
    TransformerLabConfig,
    ensure_transformer_lab_available,
    run_transformer_lab_optimization,
    run_transformer_lab,
    write_transformer_lab_outputs,
)
from .validation import run_crash_walk_forward_backtest, run_walk_forward_backtest
from .visualization import create_backtest_visualizations


def _features_with_targets(input_path: str, config: dict) -> pd.DataFrame:
    features = build_features(input_path, annualization=int(config.get("annualization_factor", 252)))
    return add_targets(features, config)


def cmd_make_sample_data(args: argparse.Namespace) -> None:
    df = make_sample_market_data(rows=args.rows, seed=args.seed)
    save_frame(df, args.output)
    print(f"Wrote sample market data: {args.output} ({len(df)} rows)")


def cmd_fetch_market_data(args: argparse.Namespace) -> None:
    df, metadata = fetch_and_save_market_data(
        source_config_path=args.source_config,
        output_path=args.output,
        metadata_path=args.metadata,
        range_value=args.range,
        start=args.start,
        end=args.end,
        min_rows=args.min_rows,
    )
    optional_warnings = [source for source in metadata["sources"] if source["status"] != "ok" and not source["required"]]
    print(f"Wrote market data: {args.output} ({len(df)} rows, {metadata['firstDate']}~{metadata['lastDate']})")
    print(f"Wrote source metadata: {args.metadata}")
    if optional_warnings:
        print(f"Optional source warnings: {len(optional_warnings)}")


def cmd_build_features(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    df = _features_with_targets(args.input, config)
    save_frame(df, args.output)
    print(f"Wrote feature dataset: {args.output} ({len(df)} rows)")


def cmd_train(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    df = load_frame(args.features)
    bundle = train_bundle(df, config)
    model_path = args.model_output or config["paths"]["model_bundle"]
    save_bundle(bundle, model_path)
    print(f"Wrote model bundle: {model_path}")


def cmd_backtest(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    df = load_frame(args.features)
    scored, metrics, matrices = run_walk_forward_backtest(df, config)
    crash_scored, crash_metrics = run_crash_walk_forward_backtest(df, config)
    broad_selection = metrics.attrs.get("model_selection")
    broad_splits = metrics.attrs.get("splits")
    crash_selection = crash_metrics.attrs.get("model_selection")
    crash_tasks = {"crash_5d_5pct", "crash_5d_10pct"}
    broad_metrics = metrics.loc[~metrics["task"].isin(crash_tasks)].copy()
    crash_metrics_values = crash_metrics.copy()
    broad_metrics.attrs = {}
    crash_metrics_values.attrs = {}
    metrics = pd.concat([broad_metrics, crash_metrics_values], ignore_index=True)
    selections = [value for value in (broad_selection, crash_selection) if isinstance(value, pd.DataFrame) and not value.empty]
    metrics.attrs["model_selection"] = pd.concat(selections, ignore_index=True) if selections else pd.DataFrame()
    metrics.attrs["splits"] = broad_splits
    scored = add_els_scores(scored, scored, scored["pred_vol_20d"].dropna().tolist())

    metrics_path = Path(config["paths"]["metrics"])
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(metrics_path, index=False)
    predictions_path = Path(config["paths"]["walk_forward_predictions"])
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    broad_prediction_columns = [
        "date",
        "prob_risk_off",
        "pred_regime",
        "target_regime",
        "fwd_ret_20d",
        "fwd_max_drawdown_20d",
        "target_vol_20d",
    ]
    broad_predictions = (
        scored.sort_values(["date", "fold"])
        .drop_duplicates("date", keep="last")[broad_prediction_columns]
        .reset_index(drop=True)
    )
    crash_prediction_columns = [
        "date",
        "fold",
        "prob_crash_5d_5pct",
        "prob_crash_5d_10pct",
        "target_crash_5d_5pct",
        "target_crash_5d_10pct",
        "fwd_ret_5d",
        "fwd_min_ret_5d",
    ]
    crash_predictions = (
        crash_scored.sort_values(["date", "fold"])
        .drop_duplicates("date", keep="last")[crash_prediction_columns]
        .reset_index(drop=True)
    )
    walk_forward_predictions = crash_predictions.merge(broad_predictions, on="date", how="left")
    walk_forward_predictions.to_csv(predictions_path, index=False)
    bucket = score_bucket_analysis(scored)
    bucket_path = Path(config["paths"]["score_bucket_analysis"])
    bucket_path.parent.mkdir(parents=True, exist_ok=True)
    bucket.to_csv(bucket_path, index=False)
    selection = metrics.attrs.get("model_selection")
    if isinstance(selection, pd.DataFrame) and not selection.empty:
        selection_path = Path("reports/model_selection_metrics.csv")
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        selection.to_csv(selection_path, index=False)
    figure_paths = create_backtest_visualizations(scored, output_dir=Path(args.output).parent / "figures")
    write_backtest_report(args.output, metrics, matrices, scored, figure_paths=figure_paths)
    print(f"Wrote backtest report: {args.output}")
    print(f"Wrote model metrics: {metrics_path}")
    print(f"Wrote walk-forward predictions: {predictions_path}")
    print(f"Wrote score bucket analysis: {bucket_path}")
    print(f"Wrote figures: {Path(args.output).parent / 'figures'}")


def cmd_predict_latest(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    df = load_frame(args.features)
    model_path = args.model or config["paths"]["model_bundle"]
    if Path(model_path).exists():
        bundle = load_bundle(model_path)
    else:
        bundle = train_bundle(df, config)
        save_bundle(bundle, model_path)
    latest = df.tail(1).reset_index(drop=True)
    predictions = predict_bundle(bundle, latest)
    scored = add_els_scores(predictions, latest, bundle.predicted_vol_history)
    scored["top_positive_features"] = ""
    scored["top_negative_features"] = ""
    columns = [
        "date",
        "pred_vol_20d",
        "pred_regime",
        "prob_risk_on",
        "prob_neutral",
        "prob_risk_off",
        "prob_kospi_outperform_spx_20d",
        "prob_kospi_outperform_sox_20d",
        "prob_crash_5d_5pct",
        "prob_crash_5d_10pct",
        "els_risk_score",
        "els_risk_bucket",
        "top_positive_features",
        "top_negative_features",
    ]
    output = scored[columns]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)
    print(f"Wrote latest signal: {args.output}")


def cmd_transformer_lab(args: argparse.Namespace) -> None:
    try:
        ensure_transformer_lab_available()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    df = load_frame(args.features)
    lab_config = TransformerLabConfig(
        target=args.target,
        sequence_length=args.sequence_length,
        max_folds=args.max_folds,
        epochs=args.epochs,
        batch_size=args.batch_size,
        d_model=args.d_model,
        nhead=args.nhead,
        num_layers=args.num_layers,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        random_state=args.seed,
        device=args.device,
        pooling=args.pooling,
        loss=args.loss,
        focal_alpha=args.focal_alpha,
        focal_gamma=args.focal_gamma,
        pos_weight_cap=args.pos_weight_cap,
        feature_selection=args.feature_selection,
        max_features=args.max_features,
        purge_days=args.purge_days,
        positional_encoding=args.positional_encoding,
        gradient_clip_norm=args.gradient_clip_norm,
        weight_decay=args.weight_decay,
        seed_count=args.seed_count,
        prior_oos_calibration=args.prior_oos_calibration,
        min_calibration_folds=args.min_calibration_folds,
        min_calibration_events=args.min_calibration_events,
    )
    try:
        predictions, metrics = run_transformer_lab(df, lab_config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    write_transformer_lab_outputs(predictions, metrics, args.output, args.metrics_output)
    print(f"Wrote transformer lab predictions: {args.output}")
    print(f"Wrote transformer lab metrics: {args.metrics_output}")


def _csv_ints(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def _csv_strings(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def cmd_transformer_lab_optimize(args: argparse.Namespace) -> None:
    try:
        ensure_transformer_lab_available()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    df = load_frame(args.features)
    results = run_transformer_lab_optimization(
        df=df,
        targets=_csv_strings(args.targets),
        sequence_lengths=_csv_ints(args.sequence_lengths),
        d_models=_csv_ints(args.d_models),
        num_layers_values=_csv_ints(args.num_layers),
        epochs_values=_csv_ints(args.epochs),
        max_folds=args.max_folds,
        batch_size=args.batch_size,
        nhead=args.nhead,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        random_state=args.seed,
        device=args.device,
        pooling_values=_csv_strings(args.pooling_values),
        loss_values=_csv_strings(args.loss_values),
        max_features_values=_csv_ints(args.max_features_values),
        feature_selection=args.feature_selection,
        focal_alpha=args.focal_alpha,
        focal_gamma=args.focal_gamma,
        pos_weight_cap=args.pos_weight_cap,
        progress_output=args.output,
        purge_days=args.purge_days,
        positional_encoding=args.positional_encoding,
        gradient_clip_norm=args.gradient_clip_norm,
        weight_decay=args.weight_decay,
        seed_count=args.seed_count,
        prior_oos_calibration=args.prior_oos_calibration,
        min_calibration_folds=args.min_calibration_folds,
        min_calibration_events=args.min_calibration_events,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output_path, index=False)
    print(f"Wrote transformer lab optimization: {output_path}")
    ok = results.loc[results.get("status", "") == "ok"] if "status" in results.columns else results
    if not ok.empty and "averagePrecision" in ok.columns:
        best = ok.sort_values(["target", "averagePrecision", "brier"], ascending=[True, False, True]).groupby("target").head(1)
        print(best[["target", "sequenceLength", "dModel", "numLayers", "epochs", "averagePrecision", "auc", "brier"]].to_string(index=False))


def cmd_ensemble_lab(args: argparse.Namespace) -> None:
    df = load_frame(args.features)
    config = load_config(args.config)
    lab_config = EnsembleLabConfig(
        targets=tuple(_csv_strings(args.targets)),
        max_folds=args.max_folds,
        sequence_length=args.sequence_length,
        epochs=args.epochs,
        batch_size=args.batch_size,
        d_model=args.d_model,
        nhead=args.nhead,
        num_layers=args.num_layers,
        dropout=args.dropout,
        learning_rate=args.learning_rate,
        random_state=args.seed,
        device=args.device,
        pooling=args.pooling,
        loss=args.loss,
        focal_alpha=args.focal_alpha,
        focal_gamma=args.focal_gamma,
        pos_weight_cap=args.pos_weight_cap,
        feature_selection=args.feature_selection,
        max_features=args.max_features,
        fixed_rf_weight=args.rf_weight,
        fixed_transformer_weight=args.transformer_weight,
        fixed_rule_weight=args.rule_weight,
        adaptive_weights=not args.fixed_only,
        min_weight_selection_folds=args.min_weight_selection_folds,
        min_weight_selection_events=args.min_weight_selection_events,
        weight_lookback_folds=args.weight_lookback_folds,
        max_brier_degradation=args.max_brier_degradation,
        adaptive_weight_shrinkage=args.adaptive_weight_shrinkage,
        purge_days=args.purge_days,
        positional_encoding=args.positional_encoding,
        gradient_clip_norm=args.gradient_clip_norm,
        weight_decay=args.weight_decay,
        seed_count=args.seed_count,
        prior_oos_calibration=args.prior_oos_calibration,
        min_calibration_folds=args.min_calibration_folds,
        min_calibration_events=args.min_calibration_events,
        moderate_pooling=args.moderate_pooling,
        severe_pooling=args.severe_pooling,
        moderate_loss=args.moderate_loss,
        severe_loss=args.severe_loss,
    )
    try:
        predictions, metrics, weights = run_ensemble_lab(df, config, lab_config)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    write_ensemble_lab_outputs(
        predictions,
        metrics,
        weights,
        args.output,
        args.metrics_output,
        args.weights_output,
        report_output=args.report_output,
        lab_config=lab_config,
    )
    print(f"Wrote ensemble lab predictions: {args.output}")
    print(f"Wrote ensemble lab metrics: {args.metrics_output}")
    print(f"Wrote ensemble lab weights: {args.weights_output}")
    print(f"Wrote ensemble lab report: {args.report_output}")
    summary = metrics.loc[metrics["model"].str.startswith("ensemble")].copy()
    if not summary.empty:
        print(summary[["task", "model", "observations", "event_count", "average_precision", "auc", "brier", "top_decile_lift"]].to_string(index=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m kospi_risk.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-market-data")
    fetch.add_argument("--source-config", default="configs/data_sources.yaml")
    fetch.add_argument("--output", default="data/raw/market_data.csv")
    fetch.add_argument("--metadata", default="data/raw/market_data_sources.json")
    fetch.add_argument("--range", default=None, help="Yahoo range 예: 5y, 10y, max")
    fetch.add_argument("--start", default=None, help="YYYY-MM-DD. --end와 함께 사용")
    fetch.add_argument("--end", default=None, help="YYYY-MM-DD. --start와 함께 사용")
    fetch.add_argument("--min-rows", type=int, default=1500)
    fetch.set_defaults(func=cmd_fetch_market_data)

    sample = subparsers.add_parser("make-sample-data")
    sample.add_argument("--output", default="data/raw/market_data.csv")
    sample.add_argument("--rows", type=int, default=1800)
    sample.add_argument("--seed", type=int, default=42)
    sample.set_defaults(func=cmd_make_sample_data)

    features = subparsers.add_parser("build-features")
    features.add_argument("--input", default="data/raw/market_data.csv")
    features.add_argument("--output", default="data/processed/features.parquet")
    features.add_argument("--config", default="configs/base.yaml")
    features.set_defaults(func=cmd_build_features)

    train = subparsers.add_parser("train")
    train.add_argument("--features", default="data/processed/features.parquet")
    train.add_argument("--config", default="configs/base.yaml")
    train.add_argument("--model-output", default=None)
    train.set_defaults(func=cmd_train)

    backtest = subparsers.add_parser("backtest")
    backtest.add_argument("--features", default="data/processed/features.parquet")
    backtest.add_argument("--config", default="configs/base.yaml")
    backtest.add_argument("--output", default="reports/backtest_report.md")
    backtest.set_defaults(func=cmd_backtest)

    latest = subparsers.add_parser("predict-latest")
    latest.add_argument("--features", default="data/processed/features.parquet")
    latest.add_argument("--config", default="configs/base.yaml")
    latest.add_argument("--model", default=None)
    latest.add_argument("--output", default="reports/latest_signal.csv")
    latest.set_defaults(func=cmd_predict_latest)

    transformer = subparsers.add_parser("transformer-lab")
    transformer.add_argument("--features", default="data/processed/features.parquet")
    transformer.add_argument("--target", choices=["crash_5d_5pct", "crash_5d_10pct"], default="crash_5d_5pct")
    transformer.add_argument("--output", default="reports/transformer_lab_predictions.csv")
    transformer.add_argument("--metrics-output", default="reports/transformer_lab_metrics.json")
    transformer.add_argument("--sequence-length", type=int, default=20)
    transformer.add_argument("--max-folds", type=int, default=3)
    transformer.add_argument("--epochs", type=int, default=12)
    transformer.add_argument("--batch-size", type=int, default=64)
    transformer.add_argument("--d-model", type=int, default=32)
    transformer.add_argument("--nhead", type=int, default=4)
    transformer.add_argument("--num-layers", type=int, default=2)
    transformer.add_argument("--dropout", type=float, default=0.1)
    transformer.add_argument("--learning-rate", type=float, default=0.001)
    transformer.add_argument("--seed", type=int, default=42)
    transformer.add_argument("--device", choices=["auto", "mps", "cpu", "cuda"], default="auto")
    transformer.add_argument("--pooling", choices=["last", "mean", "attention"], default="last")
    transformer.add_argument("--loss", choices=["bce", "focal"], default="bce")
    transformer.add_argument("--focal-alpha", type=float, default=0.75)
    transformer.add_argument("--focal-gamma", type=float, default=2.0)
    transformer.add_argument("--pos-weight-cap", type=float, default=0.0)
    transformer.add_argument("--feature-selection", choices=["all", "spearman"], default="all")
    transformer.add_argument("--max-features", type=int, default=0)
    transformer.add_argument("--purge-days", type=int, default=5)
    transformer.add_argument("--positional-encoding", choices=["sinusoidal", "none"], default="none")
    transformer.add_argument("--gradient-clip-norm", type=float, default=1.0)
    transformer.add_argument("--weight-decay", type=float, default=0.0001)
    transformer.add_argument("--seed-count", type=int, default=1)
    transformer.add_argument("--prior-oos-calibration", action="store_true")
    transformer.add_argument("--min-calibration-folds", type=int, default=4)
    transformer.add_argument("--min-calibration-events", type=int, default=8)
    transformer.set_defaults(func=cmd_transformer_lab)

    transformer_opt = subparsers.add_parser("transformer-lab-optimize")
    transformer_opt.add_argument("--features", default="data/processed/features.parquet")
    transformer_opt.add_argument("--targets", default="crash_5d_5pct,crash_5d_10pct")
    transformer_opt.add_argument("--output", default="reports/transformer_lab_optimization.csv")
    transformer_opt.add_argument("--sequence-lengths", default="10,20,40")
    transformer_opt.add_argument("--d-models", default="16,32")
    transformer_opt.add_argument("--num-layers", default="1,2")
    transformer_opt.add_argument("--epochs", default="4")
    transformer_opt.add_argument("--max-folds", type=int, default=3)
    transformer_opt.add_argument("--batch-size", type=int, default=64)
    transformer_opt.add_argument("--nhead", type=int, default=4)
    transformer_opt.add_argument("--dropout", type=float, default=0.1)
    transformer_opt.add_argument("--learning-rate", type=float, default=0.001)
    transformer_opt.add_argument("--seed", type=int, default=42)
    transformer_opt.add_argument("--device", choices=["auto", "mps", "cpu", "cuda"], default="auto")
    transformer_opt.add_argument("--pooling-values", default="last")
    transformer_opt.add_argument("--loss-values", default="bce")
    transformer_opt.add_argument("--max-features-values", default="0")
    transformer_opt.add_argument("--feature-selection", choices=["all", "spearman"], default="all")
    transformer_opt.add_argument("--focal-alpha", type=float, default=0.75)
    transformer_opt.add_argument("--focal-gamma", type=float, default=2.0)
    transformer_opt.add_argument("--pos-weight-cap", type=float, default=0.0)
    transformer_opt.add_argument("--purge-days", type=int, default=5)
    transformer_opt.add_argument("--positional-encoding", choices=["sinusoidal", "none"], default="none")
    transformer_opt.add_argument("--gradient-clip-norm", type=float, default=1.0)
    transformer_opt.add_argument("--weight-decay", type=float, default=0.0001)
    transformer_opt.add_argument("--seed-count", type=int, default=1)
    transformer_opt.add_argument("--prior-oos-calibration", action="store_true")
    transformer_opt.add_argument("--min-calibration-folds", type=int, default=4)
    transformer_opt.add_argument("--min-calibration-events", type=int, default=8)
    transformer_opt.set_defaults(func=cmd_transformer_lab_optimize)

    ensemble = subparsers.add_parser("ensemble-lab")
    ensemble.add_argument("--features", default="data/processed/features.parquet")
    ensemble.add_argument("--config", default="configs/base.yaml")
    ensemble.add_argument("--targets", default="crash_5d_5pct,crash_5d_10pct")
    ensemble.add_argument("--output", default="reports/ensemble_lab_predictions.csv")
    ensemble.add_argument("--metrics-output", default="reports/ensemble_lab_metrics.csv")
    ensemble.add_argument("--weights-output", default="reports/ensemble_lab_weights.csv")
    ensemble.add_argument("--report-output", default="reports/research/transformer_ensemble_lab_report.md")
    ensemble.add_argument("--max-folds", type=int, default=12)
    ensemble.add_argument("--sequence-length", type=int, default=40)
    ensemble.add_argument("--epochs", type=int, default=4)
    ensemble.add_argument("--batch-size", type=int, default=64)
    ensemble.add_argument("--d-model", type=int, default=32)
    ensemble.add_argument("--nhead", type=int, default=4)
    ensemble.add_argument("--num-layers", type=int, default=2)
    ensemble.add_argument("--dropout", type=float, default=0.1)
    ensemble.add_argument("--learning-rate", type=float, default=0.001)
    ensemble.add_argument("--seed", type=int, default=42)
    ensemble.add_argument("--device", choices=["auto", "mps", "cpu", "cuda"], default="auto")
    ensemble.add_argument("--pooling", choices=["last", "mean", "attention"], default="last")
    ensemble.add_argument("--loss", choices=["bce", "focal"], default="bce")
    ensemble.add_argument("--focal-alpha", type=float, default=0.75)
    ensemble.add_argument("--focal-gamma", type=float, default=2.0)
    ensemble.add_argument("--pos-weight-cap", type=float, default=0.0)
    ensemble.add_argument("--feature-selection", choices=["all", "spearman"], default="all")
    ensemble.add_argument("--max-features", type=int, default=0)
    ensemble.add_argument("--rf-weight", type=float, default=0.5)
    ensemble.add_argument("--transformer-weight", type=float, default=0.3)
    ensemble.add_argument("--rule-weight", type=float, default=0.2)
    ensemble.add_argument("--fixed-only", action="store_true")
    ensemble.add_argument("--min-weight-selection-folds", type=int, default=6)
    ensemble.add_argument("--min-weight-selection-events", type=int, default=12)
    ensemble.add_argument("--weight-lookback-folds", type=int, default=24)
    ensemble.add_argument("--max-brier-degradation", type=float, default=0.05)
    ensemble.add_argument("--adaptive-weight-shrinkage", type=float, default=0.6)
    ensemble.add_argument("--purge-days", type=int, default=5)
    ensemble.add_argument("--positional-encoding", choices=["sinusoidal", "none"], default="none")
    ensemble.add_argument("--gradient-clip-norm", type=float, default=1.0)
    ensemble.add_argument("--weight-decay", type=float, default=0.0001)
    ensemble.add_argument("--seed-count", type=int, default=2)
    ensemble.add_argument("--prior-oos-calibration", action="store_true")
    ensemble.add_argument("--min-calibration-folds", type=int, default=4)
    ensemble.add_argument("--min-calibration-events", type=int, default=8)
    ensemble.add_argument("--moderate-pooling", choices=["last", "mean", "attention"], default=None)
    ensemble.add_argument("--severe-pooling", choices=["last", "mean", "attention"], default=None)
    ensemble.add_argument("--moderate-loss", choices=["bce", "focal"], default=None)
    ensemble.add_argument("--severe-loss", choices=["bce", "focal"], default=None)
    ensemble.set_defaults(func=cmd_ensemble_lab)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
