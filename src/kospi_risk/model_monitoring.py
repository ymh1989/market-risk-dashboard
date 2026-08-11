from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


TASKS = {
    "crash5d5pct": {
        "probability": "crash5d5pctProbabilityPct",
        "baseline": "baselineCrash5d5pctProbabilityPct",
        "target": "targetCrash5d5pct",
        "alert_threshold_pct": 50.0,
        "label": "5일 중 -5% 도달",
    },
    "crash5d10pct": {
        "probability": "crash5d10pctProbabilityPct",
        "baseline": "baselineCrash5d10pctProbabilityPct",
        "target": "targetCrash5d10pct",
        "alert_threshold_pct": 25.0,
        "label": "5일 중 -10% 도달",
    },
}


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_auc(target: pd.Series, probability: pd.Series) -> float | None:
    if target.nunique(dropna=True) < 2:
        return None
    return float(roc_auc_score(target, probability))


def _safe_ap(target: pd.Series, probability: pd.Series) -> float | None:
    if target.nunique(dropna=True) < 2:
        return None
    return float(average_precision_score(target, probability))


def enrich_walk_forward_results(
    rows: list[dict],
    market_series: list[dict],
) -> list[dict]:
    """OOS 예측에 5거래일 실제 결과와 과거값 기반 기준확률을 붙입니다."""

    market = sorted(
        [
            {"date": str(row.get("date")), "kospi": _finite(row.get("kospi"))}
            for row in market_series
            if row.get("date") and _finite(row.get("kospi")) is not None
        ],
        key=lambda row: row["date"],
    )
    date_index = {row["date"]: index for index, row in enumerate(market)}
    latest_market_date = market[-1]["date"] if market else ""
    enriched: list[dict] = []

    for source_row in rows:
        row = dict(source_row)
        date = str(row.get("date") or "")
        index = date_index.get(date)
        if index is not None and index >= 5:
            return_5d = market[index]["kospi"] / market[index - 5]["kospi"] - 1
            if _finite(row.get("baselineCrash5d5pctProbabilityPct")) is None:
                row["baselineCrash5d5pctProbabilityPct"] = float(
                    np.clip((-return_5d - 0.01) / 0.12, 0.01, 0.8) * 100
                )
            if _finite(row.get("baselineCrash5d10pctProbabilityPct")) is None:
                row["baselineCrash5d10pctProbabilityPct"] = float(
                    np.clip((-return_5d - 0.03) / 0.18, 0.005, 0.6) * 100
                )

        result_known_date = str(row.get("resultKnownThroughDate") or "")
        result_is_known = bool(result_known_date and result_known_date <= latest_market_date)
        if index is not None and index + 5 < len(market) and result_is_known:
            start = market[index]["kospi"]
            forward_prices = [point["kospi"] for point in market[index + 1 : index + 6]]
            forward_returns = [price / start - 1 for price in forward_prices]
            minimum_return = min(forward_returns)
            if _finite(row.get("forwardReturn5dPct")) is None:
                row["forwardReturn5dPct"] = forward_returns[-1] * 100
            if _finite(row.get("forwardMinReturn5dPct")) is None:
                row["forwardMinReturn5dPct"] = minimum_return * 100
            if row.get("targetCrash5d5pct") is None:
                row["targetCrash5d5pct"] = int(minimum_return <= -0.05)
            if row.get("targetCrash5d10pct") is None:
                row["targetCrash5d10pct"] = int(minimum_return <= -0.10)
        enriched.append(row)
    return enriched


def _window_metrics(frame: pd.DataFrame, task: dict, calendar_days: int) -> dict:
    end_date = frame["date"].max()
    start_date = end_date - timedelta(days=calendar_days - 1)
    window = frame.loc[frame["date"] >= start_date].copy()
    target_col = task["target"]
    probability_col = task["probability"]
    baseline_col = task["baseline"]
    window = window.dropna(subset=[target_col, probability_col, baseline_col])
    if window.empty:
        return {
            "startDate": start_date.date().isoformat(),
            "endDate": end_date.date().isoformat(),
            "observations": 0,
            "eventCount": 0,
            "eventRate": None,
            "auc": None,
            "averagePrecision": None,
            "brier": None,
            "baselineBrier": None,
            "dailyWinRate": None,
            "foldWinRate": None,
        }

    target = window[target_col].astype(int)
    probability = window[probability_col].astype(float) / 100
    baseline = window[baseline_col].astype(float) / 100
    model_error = np.square(probability - target)
    baseline_error = np.square(baseline - target)

    fold_scores = []
    if "fold" in window.columns:
        for _, fold in window.groupby("fold", dropna=True):
            if len(fold) < 5:
                continue
            fold_target = fold[target_col].astype(int)
            fold_model = fold[probability_col].astype(float) / 100
            fold_baseline = fold[baseline_col].astype(float) / 100
            fold_scores.append(
                brier_score_loss(fold_target, fold_model)
                < brier_score_loss(fold_target, fold_baseline)
            )

    return {
        "startDate": window["date"].min().date().isoformat(),
        "endDate": window["date"].max().date().isoformat(),
        "observations": int(len(window)),
        "eventCount": int(target.sum()),
        "eventRate": float(target.mean()),
        "auc": _safe_auc(target, probability),
        "averagePrecision": _safe_ap(target, probability),
        "brier": float(brier_score_loss(target, probability)),
        "baselineBrier": float(brier_score_loss(target, baseline)),
        "dailyWinRate": float((model_error < baseline_error).mean()),
        "foldWinRate": float(np.mean(fold_scores)) if fold_scores else None,
    }


def _calibration_buckets(frame: pd.DataFrame, task: dict) -> list[dict]:
    bins = [0, 10, 25, 50, 75, 100.000001]
    labels = ["0-10", "10-25", "25-50", "50-75", "75-100"]
    probability_col = task["probability"]
    target_col = task["target"]
    valid = frame.dropna(subset=[probability_col, target_col]).copy()
    valid["bucket"] = pd.cut(
        valid[probability_col].astype(float),
        bins=bins,
        labels=labels,
        include_lowest=True,
        right=False,
    )
    grouped = valid.groupby("bucket", observed=False)
    result = []
    for label, bucket in grouped:
        result.append(
            {
                "label": str(label),
                "observations": int(len(bucket)),
                "averageProbabilityPct": None
                if bucket.empty
                else float(bucket[probability_col].mean()),
                "actualFrequencyPct": None
                if bucket.empty
                else float(bucket[target_col].mean() * 100),
            }
        )
    return result


def _alert_cases(frame: pd.DataFrame, task: dict, limit: int = 8) -> dict:
    threshold = float(task["alert_threshold_pct"])
    probability_col = task["probability"]
    target_col = task["target"]
    valid = frame.dropna(subset=[probability_col, target_col]).copy()
    valid["alert"] = valid[probability_col].astype(float) >= threshold

    def records(filtered: pd.DataFrame) -> list[dict]:
        return [
            {
                "date": row["date"].date().isoformat(),
                "probabilityPct": float(row[probability_col]),
                "forwardMinReturn5dPct": _finite(row.get("forwardMinReturn5dPct")),
            }
            for row in filtered.sort_values("date", ascending=False).head(limit).to_dict(orient="records")
        ]

    actual_events = valid[target_col].astype(int) == 1
    false_positive = valid[valid["alert"] & ~actual_events]
    missed = valid[~valid["alert"] & actual_events]
    hits = valid[valid["alert"] & actual_events]
    return {
        "alertThresholdPct": threshold,
        "alerts": int(valid["alert"].sum()),
        "actualEvents": int(actual_events.sum()),
        "hits": int(len(hits)),
        "falsePositives": int(len(false_positive)),
        "misses": int(len(missed)),
        "hitDates": records(hits),
        "falsePositiveDates": records(false_positive),
        "missedDates": records(missed),
    }


def _operating_status(metrics: dict) -> dict:
    recent = metrics.get("3m") or {}
    observations = int(recent.get("observations") or 0)
    events = int(recent.get("eventCount") or 0)
    if observations < 30 or events < 3:
        return {
            "label": "표본 부족",
            "tone": "watch",
            "researchOnly": False,
            "reason": "최근 3개월 급락 표본 3건 미만",
        }

    auc = _finite(recent.get("auc"))
    ap = _finite(recent.get("averagePrecision"))
    event_rate = _finite(recent.get("eventRate"))
    brier = _finite(recent.get("brier"))
    baseline_brier = _finite(recent.get("baselineBrier"))
    fold_win_rate = _finite(recent.get("foldWinRate"))
    weak_ranking = auc is not None and auc < 0.52
    weak_precision = ap is not None and event_rate is not None and ap <= event_rate
    weak_calibration = (
        brier is not None
        and baseline_brier is not None
        and brier > baseline_brier * 1.10
    )
    weak_fold_stability = fold_win_rate is not None and fold_win_rate < 0.40
    degraded = weak_ranking or weak_precision or weak_calibration or weak_fold_stability
    if degraded:
        return {
            "label": "연구용 전환 경고",
            "tone": "danger",
            "researchOnly": True,
            "reason": "최근 선별력 또는 확률오차가 운영 기준 하회",
        }

    healthy = (
        auc is not None
        and auc >= 0.60
        and ap is not None
        and event_rate is not None
        and ap > event_rate
        and brier is not None
        and baseline_brier is not None
        and brier <= baseline_brier
        and (fold_win_rate is None or fold_win_rate >= 0.60)
    )
    return {
        "label": "운영 유지" if healthy else "주의 관찰",
        "tone": "good" if healthy else "caution",
        "researchOnly": False,
        "reason": "최근 성능이 운영 기준 충족" if healthy else "최근 지표 혼조 · 누적 관찰 필요",
    }


def build_model_monitoring(rows: list[dict], market_series: list[dict]) -> dict:
    enriched_rows = enrich_walk_forward_results(rows, market_series)
    frame = pd.DataFrame(enriched_rows)
    if frame.empty:
        return {"status": {"label": "산출 대기", "tone": "watch"}, "tasks": {}}
    frame["date"] = pd.to_datetime(frame["date"])

    task_payload = {}
    for task_id, task in TASKS.items():
        for column in (task["probability"], task["baseline"], task["target"]):
            if column not in frame.columns:
                frame[column] = np.nan
        metrics = {
            "3m": _window_metrics(frame, task, 93),
            "6m": _window_metrics(frame, task, 186),
        }
        task_payload[task_id] = {
            "label": task["label"],
            "metrics": metrics,
            "calibrationBuckets": _calibration_buckets(frame, task),
            "cases": _alert_cases(frame, task),
            "status": _operating_status(metrics),
        }

    primary_status = task_payload["crash5d5pct"]["status"]
    return {
        "generatedFrom": "walk-forward OOS predictions only",
        "status": primary_status,
        "tasks": task_payload,
    }
