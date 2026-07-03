from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .scoring import score_bucket_analysis


def _markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    safe = df.copy()
    safe = safe.where(pd.notna(safe), "")
    headers = [str(column) for column in safe.columns]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for _, row in safe.iterrows():
        values = [str(row[column]) for column in safe.columns]
        lines.append("| " + " | ".join(value.replace("|", "\\|") for value in values) + " |")
    return "\n".join(lines)


def _fmt(value: object, digits: int = 4) -> str:
    if pd.isna(value):
        return "NA"
    if isinstance(value, (float, np.floating)):
        return f"{value:.{digits}f}"
    return str(value)


def _metric_value(metrics: pd.DataFrame, model: str, task: str, metric: str) -> float:
    rows = metrics[(metrics["model"] == model) & (metrics["task"] == task) & (metrics["metric"] == metric)]
    if rows.empty:
        return float("nan")
    return float(rows["value"].iloc[0])


def _baseline_comparison(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    comparisons = [
        ("변동성", "vol", "rmse", "낮을수록 우수"),
        ("변동성", "vol", "mae", "낮을수록 우수"),
        ("변동성", "vol", "correlation", "높을수록 우수"),
        ("Regime", "regime", "macro_f1", "높을수록 우수"),
        ("Regime", "regime", "risk_off_recall", "높을수록 우수"),
        ("Risk-off 이진", "risk_off_binary", "recall", "높을수록 우수"),
        ("Risk-off 이진", "risk_off_binary", "precision", "높을수록 우수"),
        ("Risk-off 이진", "risk_off_binary", "brier", "낮을수록 우수"),
        ("Risk-off 이진", "risk_off_binary", "auc", "높을수록 우수"),
        ("5D -5% 급락", "crash_5d_5pct", "average_precision", "높을수록 우수"),
        ("5D -5% 급락", "crash_5d_5pct", "top_decile_lift", "높을수록 우수"),
        ("5D -5% 급락", "crash_5d_5pct", "brier", "낮을수록 우수"),
        ("5D -5% 급락", "crash_5d_5pct", "auc", "높을수록 우수"),
        ("5D -10% 급락", "crash_5d_10pct", "average_precision", "높을수록 우수"),
        ("5D -10% 급락", "crash_5d_10pct", "top_decile_lift", "높을수록 우수"),
        ("5D -10% 급락", "crash_5d_10pct", "brier", "낮을수록 우수"),
        ("5D -10% 급락", "crash_5d_10pct", "auc", "높을수록 우수"),
        ("KOSPI > SPX", "outperform_spx", "brier", "낮을수록 우수"),
        ("KOSPI > SPX", "outperform_spx", "auc", "높을수록 우수"),
        ("KOSPI > SOX", "outperform_sox", "brier", "낮을수록 우수"),
        ("KOSPI > SOX", "outperform_sox", "auc", "높을수록 우수"),
    ]
    for label, task, metric, direction in comparisons:
        rows.append(
            {
                "항목": label,
                "지표": metric,
                "ML 선택모델": _fmt(_metric_value(metrics, "ml_selected", task, metric)),
                "기준모델": _fmt(_metric_value(metrics, "baseline", task, metric)),
                "판정 기준": direction,
            }
        )
    return pd.DataFrame(rows)


def _latest_summary(scored: pd.DataFrame) -> pd.DataFrame:
    latest = scored.sort_values("date").iloc[-1]
    return pd.DataFrame(
        [
            {"항목": "기준일", "값": str(pd.to_datetime(latest["date"]).date())},
            {"항목": "예측 20영업일 변동성", "값": _fmt(latest["pred_vol_20d"])},
            {"항목": "예측 Regime", "값": latest["pred_regime"]},
            {"항목": "Risk-off 확률", "값": _fmt(latest["prob_risk_off"])},
            {"항목": "5D -5% 급락확률", "값": _fmt(latest["prob_crash_5d_5pct"])},
            {"항목": "5D -10% 급락확률", "값": _fmt(latest["prob_crash_5d_10pct"])},
            {"항목": "KOSPI > SPX 확률", "값": _fmt(latest["prob_kospi_outperform_spx_20d"])},
            {"항목": "KOSPI > SOX 확률", "값": _fmt(latest["prob_kospi_outperform_sox_20d"])},
            {"항목": "ELS 리스크 점수", "값": _fmt(latest["els_risk_score"], digits=2)},
            {"항목": "ELS 리스크 구간", "값": latest["els_risk_bucket"]},
        ]
    )


def _executive_summary(metrics: pd.DataFrame, scored: pd.DataFrame) -> list[str]:
    latest = scored.sort_values("date").iloc[-1]
    vol_rmse = _metric_value(metrics, "ml_selected", "vol", "rmse")
    regime_f1 = _metric_value(metrics, "ml_selected", "regime", "macro_f1")
    risk_off_recall = _metric_value(metrics, "ml_selected", "regime", "risk_off_recall")
    return [
        f"- 최신 기준일은 {pd.to_datetime(latest['date']).date()}이며, ELS 리스크 점수는 {_fmt(latest['els_risk_score'], 2)}점입니다.",
        f"- 선택 ML 모델의 변동성 RMSE는 {_fmt(vol_rmse)}, regime macro F1은 {_fmt(regime_f1)}, risk-off recall은 {_fmt(risk_off_recall)}입니다.",
        "- 기준모델 대비 성과는 아래 비교표에서 확인합니다. 단일 지표가 아니라 변동성 오차, regime 안정성, risk-off 민감도를 함께 봐야 합니다.",
    ]


def write_backtest_report(
    path: str | Path,
    metrics: pd.DataFrame,
    matrices: dict[str, np.ndarray],
    scored: pd.DataFrame,
    figure_paths: list[Path] | None = None,
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bucket = score_bucket_analysis(scored)
    selection = metrics.attrs.get("model_selection", pd.DataFrame())
    splits = metrics.attrs.get("splits", pd.DataFrame())
    data_start = pd.to_datetime(scored["date"].min()).date()
    data_end = pd.to_datetime(scored["date"].max()).date()
    latest_table = _latest_summary(scored)
    baseline = _baseline_comparison(metrics)
    report_metrics = metrics.rename(columns={"model": "모델", "task": "작업", "metric": "지표", "value": "값"}).copy()
    if "모델" in report_metrics.columns:
        report_metrics["모델"] = report_metrics["모델"].replace({"ml_selected": "ML 선택모델", "baseline": "기준모델"})
    report_selection = selection.rename(
        columns={
            "task": "작업",
            "candidate": "후보모델",
            "status": "상태",
            "fold": "fold",
            "selected": "선택여부",
        }
    )
    report_splits = splits.rename(
        columns={
            "fold": "fold",
            "train_start_date": "학습 시작일",
            "train_end_date": "학습 종료일",
            "test_start_date": "검증 시작일",
            "test_end_date": "검증 종료일",
            "train_rows": "학습 관측치",
            "test_rows": "검증 관측치",
        }
    )
    figure_paths = figure_paths or []
    figure_lines = []
    for figure_path in figure_paths:
        rel = Path(figure_path)
        try:
            rel = rel.relative_to(output_path.parent)
        except ValueError:
            pass
        figure_lines.extend([f"![{Path(figure_path).stem}]({rel.as_posix()})", ""])
    lines = [
        "# KOSPI 리스크 Regime 백테스트 보고서",
        "",
        "## 1. 핵심 요약",
        "",
        "\n".join(_executive_summary(metrics, scored)),
        "",
        "## 2. 데이터 기간 및 관측치",
        "",
        _markdown_table(
            pd.DataFrame(
                [
                    {"항목": "백테스트 시작일", "값": str(data_start)},
                    {"항목": "백테스트 종료일", "값": str(data_end)},
                    {"항목": "백테스트 관측치 수", "값": len(scored)},
                    {"항목": "Walk-forward fold 수", "값": scored["fold"].nunique() if "fold" in scored.columns else "NA"},
                ]
            )
        ),
        "",
        "## 3. 백테스트 내 최신 Signal 요약",
        "",
        _markdown_table(latest_table),
        "",
        "## 4. 모델 지표",
        "",
        _markdown_table(report_metrics),
        "",
        "## 5. 기준모델 비교",
        "",
        _markdown_table(baseline),
        "",
        "## 6. 모델 선택 로그",
        "",
        _markdown_table(report_selection.head(80) if not report_selection.empty else report_selection),
        "",
        "## 7. Walk-forward 분할 점검",
        "",
        _markdown_table(report_splits if not report_splits.empty else report_splits),
        "",
        "## 8. Regime Confusion Matrix",
        "",
        "라벨 순서: risk-on, neutral, risk-off",
        "",
        "### ML 선택모델",
        "",
        "```text",
        str(matrices.get("ml_selected_regime_confusion_matrix", "")),
        "```",
        "",
        "### 기준모델",
        "",
        "```text",
        str(matrices.get("baseline_regime_confusion_matrix", "")),
        "```",
        "",
        "## 9. ELS 점수 구간 검증",
        "",
        _markdown_table(bucket),
        "",
        "## 10. 시각화",
        "",
        *(figure_lines or ["시각화 파일이 생성되지 않았습니다.", ""]),
        "## 11. 누수 방지 및 품질 관리",
        "",
        "- Feature는 각 기준일 t까지 관측 가능한 rolling/lagged 값만 사용합니다.",
        "- Target은 t+1부터 t+20까지의 미래 구간으로만 계산합니다.",
        "- Walk-forward 검증은 expanding train window와 미래 test window를 사용하며 shuffle을 사용하지 않습니다.",
        "- Imputer, scaler, classifier calibration은 sklearn Pipeline 또는 fold 내부 학습으로 제한합니다.",
        "- 500개 미만 학습 관측치는 학습 실패, 1,500개 미만 관측치는 walk-forward 신뢰도 경고로 처리합니다.",
        "",
        "## 12. 한계",
        "",
        "- 샘플 데이터 기반 결과는 운영 판단에 사용할 수 없습니다.",
        "- Regime label은 사후 정의된 supervised label이므로 실제 위기 판정 체계와 별도 검증이 필요합니다.",
        "- Probability calibration은 time-series split 기반으로 시도하지만, fold별 클래스 부족 시 비보정 모델로 대체됩니다.",
        "- 운영 전 KRX, ECOS, KOFIA, 내부 수급/파생 포지션 데이터로 원천을 교체해야 합니다.",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
