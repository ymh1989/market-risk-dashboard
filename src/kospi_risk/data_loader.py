from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .data_schema import PRICE_COLUMNS, validate_market_columns


def load_market_data(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input data file not found: {input_path}")
    df = pd.read_csv(input_path)
    validate_market_columns(set(df.columns))
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    numeric_columns = [column for column in df.columns if column != "date"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    fill_columns = [column for column in PRICE_COLUMNS if column in df.columns]
    missing_before = int(df[fill_columns].isna().sum().sum()) if fill_columns else 0
    if missing_before:
        warnings.warn(
            f"Forward-filling {missing_before} missing market observations before return calculation.",
            RuntimeWarning,
            stacklevel=2,
        )
        df[fill_columns] = df[fill_columns].ffill()
    return df


def save_frame(df: pd.DataFrame, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix == ".csv":
        df.to_csv(output_path, index=False)
        return
    try:
        df.to_parquet(output_path, index=False)
    except Exception:
        df.to_pickle(output_path)


def load_frame(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"Data file not found: {input_path}")
    if input_path.suffix == ".csv":
        df = pd.read_csv(input_path)
    else:
        try:
            df = pd.read_parquet(input_path)
        except Exception:
            df = pd.read_pickle(input_path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def make_sample_market_data(rows: int = 1800, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-02", periods=rows)
    common = rng.normal(0.00015, 0.008, rows)
    kospi_ret = common + rng.normal(0, 0.006, rows)
    spx_ret = 0.7 * common + rng.normal(0.00012, 0.006, rows)
    sox_ret = 1.1 * common + rng.normal(0.0002, 0.012, rows)
    fx_ret = -0.25 * common + rng.normal(0.00002, 0.004, rows)
    vix_ret = -2.5 * common + rng.normal(0, 0.035, rows)

    def price(start: float, returns: np.ndarray) -> np.ndarray:
        return start * np.exp(np.cumsum(returns))

    df = pd.DataFrame(
        {
            "date": dates,
            "KOSPI": price(2200, kospi_ret),
            "SPX": price(2500, spx_ret),
            "SOX": price(1200, sox_ret),
            "USDKRW": price(1130, fx_ret),
            "NASDAQ": price(6500, 0.8 * common + rng.normal(0.00015, 0.008, rows)),
            "VIX": np.clip(price(15, vix_ret), 8, 80),
            "VKOSPI": np.clip(price(14, -2.0 * common + rng.normal(0, 0.03, rows)), 7, 70),
            "KOSPI_PUT_CALL_RATIO": np.clip(1.0 + rng.normal(0, 0.12, rows), 0.5, 2.0),
            "KOSPI_FUTURES_BASIS": rng.normal(0.1, 0.8, rows),
            "FOREIGNER_FUTURES_NET_BUY": rng.normal(0, 2500, rows),
            "FOREIGNER_KOSPI_NET_BUY": rng.normal(0, 4000, rows),
            "KOSPI_ATM_IV": np.clip(0.16 + rng.normal(0, 0.025, rows), 0.05, 0.6),
            "KOSPI_SKEW": np.clip(0.03 + rng.normal(0, 0.015, rows), -0.05, 0.15),
        }
    )
    stress_idx = np.arange(700, min(760, rows))
    if len(stress_idx):
        df.loc[stress_idx, "KOSPI"] *= np.linspace(1.0, 0.78, len(stress_idx))
        df.loc[stress_idx, "USDKRW"] *= np.linspace(1.0, 1.12, len(stress_idx))
        df.loc[stress_idx, "VIX"] *= np.linspace(1.2, 2.3, len(stress_idx))
    return df
