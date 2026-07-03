from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd

from kospi_risk.models import predict_bundle
from kospi_risk.scoring import add_els_scores


ROOT = Path(__file__).resolve().parents[1]
FEATURES_FILE = ROOT / "data" / "processed" / "features.parquet"
LATEST_SIGNAL_FILE = ROOT / "reports" / "latest_signal.csv"
METRICS_FILE = ROOT / "reports" / "model_metrics.csv"
WALK_FORWARD_FILE = ROOT / "reports" / "walk_forward_predictions.csv"
MODEL_FILE = ROOT / "models" / "model_bundle.joblib"
OUTPUT_FILE = ROOT / "data" / "ml-risk-signal.json"


def _metric(metrics: pd.DataFrame, model: str, task: str, metric: str) -> float | None:
    row = metrics[(metrics["model"] == model) & (metrics["task"] == task) & (metrics["metric"] == metric)]
    if row.empty:
        return None
    value = float(row["value"].iloc[0])
    return value if np.isfinite(value) else None


def _pct_from_log(value: float | int | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float((np.exp(float(value)) - 1) * 100)


def _pct(value: float | int | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value) * 100


def _level(probability: float, momentum_pct: float | None, realized_vol_pct: float | None) -> str:
    if probability >= 0.75 and (momentum_pct or 0) > 0 and (realized_vol_pct or 0) >= 35:
        return "고변동성 활황"
    if probability >= 0.75:
        return "Risk-off 우세"
    if probability >= 0.55:
        return "경계"
    return "중립"


def build_payload() -> dict:
    if not FEATURES_FILE.exists():
        raise FileNotFoundError(f"features file not found: {FEATURES_FILE}")
    if not MODEL_FILE.exists():
        raise FileNotFoundError(f"model bundle not found: {MODEL_FILE}")

    features = pd.read_parquet(FEATURES_FILE).sort_values("date").reset_index(drop=True)
    bundle = joblib.load(MODEL_FILE)
    feature_dates = pd.to_datetime(features["date"])
    latest_year = int(feature_dates.iloc[-1].year)
    ytd_features = features.loc[feature_dates.dt.year == latest_year].reset_index(drop=True)
    if len(ytd_features) < 2:
        ytd_features = features.tail(80).reset_index(drop=True)
    ytd_predictions = predict_bundle(bundle, ytd_features)
    ytd_scored = add_els_scores(ytd_predictions, ytd_features, bundle.predicted_vol_history)

    latest_row = features.iloc[-1]
    latest_signal = pd.read_csv(LATEST_SIGNAL_FILE).iloc[-1].to_dict() if LATEST_SIGNAL_FILE.exists() else ytd_scored.iloc[-1].to_dict()
    metrics = pd.read_csv(METRICS_FILE) if METRICS_FILE.exists() else pd.DataFrame(columns=["model", "task", "metric", "value"])
    walk_forward_series = []
    if WALK_FORWARD_FILE.exists():
        walk_forward = pd.read_csv(WALK_FORWARD_FILE)
        walk_forward["date"] = pd.to_datetime(walk_forward["date"])
        walk_forward = walk_forward.loc[walk_forward["date"].dt.year == latest_year].sort_values("date")
        walk_forward_series = [
            {
                "date": row["date"].date().isoformat(),
                "riskOffProbabilityPct": _pct(row["prob_risk_off"]),
                "downside5dProbabilityPct": _pct(row.get("prob_downside_5d")),
                "downside20dProbabilityPct": _pct(row.get("prob_downside_20d")),
                "fold": int(row["fold"]),
            }
            for row in walk_forward.to_dict(orient="records")
        ]

    momentum_pct = _pct_from_log(latest_row.get("kospi_log_ret_20d"))
    realized_vol_pct = _pct(latest_row.get("kospi_realized_vol_20d"))
    probability = float(latest_signal.get("prob_risk_off", ytd_scored.iloc[-1]["prob_risk_off"]))
    level = _level(probability, momentum_pct, realized_vol_pct)

    series = []
    merged = pd.concat(
        [
            ytd_features[
                [
                    "date",
                    "KOSPI",
                    "kospi_log_ret_20d",
                    "kospi_realized_vol_20d",
                    "kospi_dist_high_60d",
                    "baseline_risk_off_signal",
                ]
            ].reset_index(drop=True),
            ytd_scored[["prob_risk_off", "prob_downside_5d", "prob_downside_20d", "els_risk_score"]].reset_index(drop=True),
        ],
        axis=1,
    )
    for row in merged.to_dict(orient="records"):
        series.append(
            {
                "date": pd.to_datetime(row["date"]).date().isoformat(),
                "kospi": round(float(row["KOSPI"]), 2),
                "kospiReturn20dPct": _pct_from_log(row.get("kospi_log_ret_20d")),
                "realizedVol20dPct": _pct(row.get("kospi_realized_vol_20d")),
                "drawdownFrom60dHighPct": _pct(row.get("kospi_dist_high_60d")),
                "riskOffProbabilityPct": _pct(row.get("prob_risk_off")),
                "downside5dProbabilityPct": _pct(row.get("prob_downside_5d")),
                "downside20dProbabilityPct": _pct(row.get("prob_downside_20d")),
                "elsRiskScore": float(row["els_risk_score"]),
                "baselineRiskOffSignal": int(row.get("baseline_risk_off_signal") or 0),
            }
        )

    payload = {
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST"),
        "source": {
            "features": "data/processed/features.parquet",
            "model": "models/model_bundle.joblib",
            "latestSignal": "reports/latest_signal.csv",
            "metrics": "reports/model_metrics.csv",
            "walkForwardPredictions": "reports/walk_forward_predictions.csv",
            "seriesWindow": "YTD",
        },
        "latest": {
            "date": str(latest_signal.get("date", pd.to_datetime(latest_row["date"]).date())),
            "regime": str(latest_signal.get("pred_regime", "")),
            "riskOffProbabilityPct": round(probability * 100, 2),
            "downside5dProbabilityPct": round(float(latest_signal.get("prob_downside_5d", ytd_scored.iloc[-1]["prob_downside_5d"])) * 100, 2),
            "downside20dProbabilityPct": round(float(latest_signal.get("prob_downside_20d", ytd_scored.iloc[-1]["prob_downside_20d"])) * 100, 2),
            "elsRiskScore": round(float(latest_signal.get("els_risk_score", ytd_scored.iloc[-1]["els_risk_score"])), 2),
            "elsRiskBucket": str(latest_signal.get("els_risk_bucket", "")),
            "kospi": round(float(latest_row["KOSPI"]), 2),
            "kospiReturn20dPct": None if momentum_pct is None else round(momentum_pct, 2),
            "realizedVol20dPct": None if realized_vol_pct is None else round(realized_vol_pct, 2),
            "drawdownFrom60dHighPct": None if pd.isna(latest_row.get("kospi_dist_high_60d")) else round(float(latest_row["kospi_dist_high_60d"]) * 100, 2),
            "baselineRiskOffSignal": int(latest_row.get("baseline_risk_off_signal") or 0),
            "interpretationLevel": level,
        },
        "metrics": {
            "ml": {
                "regimeAccuracy": _metric(metrics, "ml_selected", "regime", "accuracy"),
                "regimeMacroF1": _metric(metrics, "ml_selected", "regime", "macro_f1"),
                "riskOffRecall": _metric(metrics, "ml_selected", "risk_off_binary", "recall"),
                "riskOffPrecision": _metric(metrics, "ml_selected", "risk_off_binary", "precision"),
                "riskOffAuc": _metric(metrics, "ml_selected", "risk_off_binary", "auc"),
                "riskOffBrier": _metric(metrics, "ml_selected", "risk_off_binary", "brier"),
            },
            "baseline": {
                "regimeAccuracy": _metric(metrics, "baseline", "regime", "accuracy"),
                "regimeMacroF1": _metric(metrics, "baseline", "regime", "macro_f1"),
                "riskOffRecall": _metric(metrics, "baseline", "risk_off_binary", "recall"),
                "riskOffPrecision": _metric(metrics, "baseline", "risk_off_binary", "precision"),
                "riskOffAuc": _metric(metrics, "baseline", "risk_off_binary", "auc"),
                "riskOffBrier": _metric(metrics, "baseline", "risk_off_binary", "brier"),
            },
            "downside5d": {
                "auc": _metric(metrics, "ml_selected", "downside_5d", "auc"),
                "brier": _metric(metrics, "ml_selected", "downside_5d", "brier"),
                "precision": _metric(metrics, "ml_selected", "downside_5d", "precision"),
                "recall": _metric(metrics, "ml_selected", "downside_5d", "recall"),
                "baselineAuc": _metric(metrics, "baseline", "downside_5d", "auc"),
                "baselineBrier": _metric(metrics, "baseline", "downside_5d", "brier"),
            },
            "downside20d": {
                "auc": _metric(metrics, "ml_selected", "downside_20d", "auc"),
                "brier": _metric(metrics, "ml_selected", "downside_20d", "brier"),
                "precision": _metric(metrics, "ml_selected", "downside_20d", "precision"),
                "recall": _metric(metrics, "ml_selected", "downside_20d", "recall"),
                "baselineAuc": _metric(metrics, "baseline", "downside_20d", "auc"),
                "baselineBrier": _metric(metrics, "baseline", "downside_20d", "brier"),
            },
        },
        "thresholds": {
            "riskOffDecisionThresholdPct": round(float(getattr(bundle, "risk_off_threshold", 0.5)) * 100, 2),
            "riskOffProbabilityWatchPct": 55,
            "riskOffProbabilityHighPct": 75,
            "realizedVolHighPct": 35,
            "elsRiskCautionScore": 60,
            "downside5dReturnPct": round(float(bundle.config.get("downside", {}).get("return_threshold_5d", -0.02)) * 100, 2),
            "downside20dReturnPct": round(float(bundle.config.get("downside", {}).get("return_threshold_20d", -0.03)) * 100, 2),
        },
        "interpretation": [
            "현재 시장 스트레스는 이미 관측된 가격·변동성·수급 부담이고, ML 확률은 현재 수준에서 향후 20영업일 동안 추가로 악화될 가능성입니다.",
            "급락 직후에는 충격이 이미 가격에 반영됐다고 학습해 ML 확률이 낮아질 수 있으므로, 확률 하락을 현재 위험 해소로 해석하면 안 됩니다.",
            "활황 국면에서도 변동성이 높으면 신규 발행 조건은 매력적일 수 있지만, 기존 북의 순연 가능성과 헤지 비용 부담이 커질 수 있습니다.",
            "하방확률은 변동성 조건을 제외하고 KOSPI가 5일 -2% 또는 20일 -3% 이하로 하락할 가능성만 별도로 추정합니다.",
        ],
        "walkForwardSeries": walk_forward_series,
        "series": series,
    }
    return payload


def main() -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(build_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote ML risk signal: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
