from __future__ import annotations

import argparse
import logging
from pathlib import Path

from kospi_risk.vkospi import DEFAULT_START_DATE, update_vkospi_data


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "vkospi" / "stockplus_vkospi.csv"
DEFAULT_METADATA = ROOT / "data" / "raw" / "vkospi" / "stockplus_vkospi.metadata.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="증권플러스 VKOSPI 일봉 증분 수집")
    parser.add_argument("--start", default=DEFAULT_START_DATE, help="최초 보관 시작일 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="조회 종료일 YYYY-MM-DD, 기본값 오늘")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--overlap-days", type=int, default=10)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--retries", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    frame = update_vkospi_data(
        args.output,
        metadata_path=args.metadata,
        start_date=args.start,
        end_date=args.end,
        overlap_days=args.overlap_days,
        sleep_seconds=args.sleep,
        retries=args.retries,
    )
    source = frame.attrs.get("metadata", {})
    latest = source.get("latest", {})
    quality = source.get("quality", {})
    print(
        f"VKOSPI 저장: {args.output} · {latest.get('date')} "
        f"{latest.get('value')} · {len(frame)}개 · {quality.get('status')}"
    )


if __name__ == "__main__":
    main()
