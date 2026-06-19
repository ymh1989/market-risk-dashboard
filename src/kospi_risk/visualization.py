from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def create_backtest_visualizations(scored: pd.DataFrame, output_dir: str | Path = "reports/figures") -> list[Path]:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    try:
        font_manager.findfont("AppleGothic", fallback_to_default=False)
        font_family = "AppleGothic"
    except Exception:
        font_family = "DejaVu Sans"
    plt.rcParams["font.family"] = font_family
    plt.rcParams["axes.unicode_minus"] = False

    figure_dir = Path(output_dir)
    figure_dir.mkdir(parents=True, exist_ok=True)
    frame = scored.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    paths: list[Path] = []

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(frame["date"], frame["els_risk_score"], color="#b42318", linewidth=1.4)
    ax.axhspan(0, 30, color="#dcfce7", alpha=0.45)
    ax.axhspan(30, 60, color="#fef9c3", alpha=0.45)
    ax.axhspan(60, 80, color="#fed7aa", alpha=0.45)
    ax.axhspan(80, 100, color="#fecaca", alpha=0.45)
    ax.set_title("ELS 리스크 점수 추이")
    ax.set_ylabel("점수")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.25)
    path = figure_dir / "els_score_timeseries.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(path)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(frame["target_vol_20d"], frame["pred_vol_20d"], s=12, alpha=0.55, color="#1d4ed8")
    low = float(np.nanmin([frame["target_vol_20d"].min(), frame["pred_vol_20d"].min()]))
    high = float(np.nanmax([frame["target_vol_20d"].max(), frame["pred_vol_20d"].max()]))
    ax.plot([low, high], [low, high], color="#111827", linestyle="--", linewidth=1)
    ax.set_title("20일 변동성 예측 vs 실현")
    ax.set_xlabel("실현 변동성")
    ax.set_ylabel("예측 변동성")
    ax.grid(True, alpha=0.25)
    path = figure_dir / "vol_prediction_scatter.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(path)

    bucket = pd.cut(frame["els_risk_score"], bins=[-0.01, 30, 60, 80, 100], labels=["0-30", "30-60", "60-80", "80-100"])
    bucket_counts = bucket.value_counts(sort=False)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(bucket_counts.index.astype(str), bucket_counts.values, color=["#22c55e", "#eab308", "#f97316", "#dc2626"])
    ax.set_title("ELS 점수 구간별 관측치 수")
    ax.set_xlabel("점수 구간")
    ax.set_ylabel("관측치 수")
    ax.grid(True, axis="y", alpha=0.25)
    path = figure_dir / "els_bucket_counts.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(path)

    risk_off = frame.assign(is_risk_off=(frame["target_regime"] == "risk-off").astype(float)).groupby(bucket, observed=False)["is_risk_off"].mean()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(risk_off.index.astype(str), risk_off.values, color="#7c3aed")
    ax.set_title("ELS 점수 구간별 Risk-off 빈도")
    ax.set_xlabel("점수 구간")
    ax.set_ylabel("Risk-off 빈도")
    ax.set_ylim(0, 1)
    ax.grid(True, axis="y", alpha=0.25)
    path = figure_dir / "risk_off_frequency_by_bucket.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    paths.append(path)

    return paths
