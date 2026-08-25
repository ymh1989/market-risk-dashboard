from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


REQUIRED_FLOW_COLUMNS = (
    "foreign_net_buy_value",
    "institution_net_buy_value",
    "financial_investment_net_buy_value",
    "pension_net_buy_value",
    "program_net_buy_value",
)


def load_frame(path: str | Path) -> pd.DataFrame:
    """CSV 또는 Parquet 시장 내부강도 파일을 읽는다."""

    source = Path(path)
    if source.suffix.lower() == ".parquet":
        return pd.read_parquet(source)
    return pd.read_csv(source)


def verify_final_flows(frame: pd.DataFrame, expected_date: str) -> dict[str, float]:
    """기준일의 KRX 직접 수급 확정치가 모두 존재하는지 검증한다."""

    required = {"date", *REQUIRED_FLOW_COLUMNS}
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        raise ValueError(f"KRX 확정치 필수 컬럼 누락: {', '.join(missing_columns)}")

    expected = pd.Timestamp(expected_date).normalize()
    dated = frame.copy()
    dated["date"] = pd.to_datetime(dated["date"], errors="coerce").dt.normalize()
    row = dated.loc[dated["date"] == expected]
    if row.empty:
        raise ValueError(f"KRX 확정치 기준일 행이 없습니다: {expected.date().isoformat()}")

    latest = row.iloc[-1]
    missing_values = [column for column in REQUIRED_FLOW_COLUMNS if pd.isna(latest[column])]
    if missing_values:
        raise ValueError(
            "KRX 당일 확정치가 아직 공개되지 않았습니다: "
            f"{expected.date().isoformat()} · {', '.join(missing_values)}"
        )
    return {column: float(latest[column]) for column in REQUIRED_FLOW_COLUMNS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KRX 당일 직접 수급 확정치를 검증합니다.")
    parser.add_argument("--input", required=True, help="시장 내부강도 CSV 또는 Parquet")
    parser.add_argument("--date", required=True, help="확정치 기준일 YYYY-MM-DD")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    values = verify_final_flows(load_frame(args.input), args.date)
    print(
        "KRX 직접 수급 확정치 확인: "
        f"{args.date} · 외국인 {values['foreign_net_buy_value'] / 100_000_000:.1f}억원 · "
        f"기관 {values['institution_net_buy_value'] / 100_000_000:.1f}억원 · "
        f"프로그램 {values['program_net_buy_value'] / 100_000_000:.1f}억원"
    )


if __name__ == "__main__":
    main()
