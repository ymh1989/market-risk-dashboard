"""공개 시세만 넣은 로컬 전용 ELS 조건 점검 화면을 생성합니다."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def public_quotes(payload: dict) -> list[dict]:
    """공개 종가·관측일만 추려 상품 입력값과 분리합니다."""
    quotes = []
    for asset in [*payload.get("indices", []), *payload.get("singleStocks", [])]:
        rows = asset.get("sixMonthPriceSeries") or asset.get("ytdPriceSeries") or []
        if not rows:
            continue
        last = rows[-1]
        close = last.get("close")
        if not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            continue
        quotes.append({"id": asset["id"], "label": asset["label"], "date": last["date"], "close": close})
    if not quotes:
        raise ValueError("점검에 사용할 유효 기초자산 종가가 없습니다.")
    return quotes


def build_local_page(source: Path, output: Path) -> Path:
    """입력값을 저장하거나 전송하지 않는 독립형 HTML을 생성합니다."""
    payload = json.loads(source.read_text(encoding="utf-8"))
    template = (ROOT / "scripts/templates/private_els_check.html").read_text(encoding="utf-8")
    data = json.dumps(public_quotes(payload), ensure_ascii=False).replace("<", "\\u003c")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__PUBLIC_QUOTES__", data), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 전용 ELS 상품 조건 점검 화면 생성")
    parser.add_argument("--input", type=Path, default=ROOT / "data/els-index-risk.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/private/els-product-check.html")
    args = parser.parse_args()
    print(build_local_page(args.input, args.output).resolve())


if __name__ == "__main__":
    main()
