from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import load_config
from .data_loader import load_frame, make_sample_market_data, save_frame
from .feature_engineering import build_features
from .market_data_fetcher import fetch_and_save_market_data
from .models import load_bundle, predict_bundle, save_bundle, train_bundle
from .reporting import write_backtest_report
from .scoring import add_els_scores, score_bucket_analysis
from .targets import add_targets
from .validation import run_walk_forward_backtest
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
    scored = add_els_scores(scored, scored, scored["pred_vol_20d"].dropna().tolist())

    metrics_path = Path(config["paths"]["metrics"])
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(metrics_path, index=False)
    predictions_path = Path(config["paths"]["walk_forward_predictions"])
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_columns = [
        "date",
        "fold",
        "prob_risk_off",
        "prob_crash_5d_5pct",
        "prob_crash_5d_10pct",
        "pred_regime",
        "target_regime",
        "target_crash_5d_5pct",
        "target_crash_5d_10pct",
        "fwd_ret_5d",
        "fwd_min_ret_5d",
        "fwd_ret_20d",
        "fwd_max_drawdown_20d",
        "target_vol_20d",
    ]
    walk_forward_predictions = (
        scored.sort_values(["date", "fold"])
        .drop_duplicates("date", keep="last")[prediction_columns]
        .reset_index(drop=True)
    )
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
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
