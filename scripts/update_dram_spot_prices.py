#!/usr/bin/env python3
"""공개 DRAM 현물가격을 증분 수집해 관찰지표 파일을 갱신합니다."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kospi_risk.dram_spot import (
    DramSpotDataError,
    build_payload,
    merge_history,
    parse_trendforce_spot_html,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "dram-spot-prices.json"
TREND_FORCE_URL = "https://www.trendforce.com/price/dram/lpddr_spot"
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-risk-dashboard/0.1)"
KST = timezone(timedelta(hours=9))


def fetch_text(url: str, *, attempts: int = 3, timeout: int = 25) -> str:
    """일시 오류를 재시도하며 UTF-8 HTML을 조회합니다."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:  # 네트워크 오류 종류를 한 곳에서 재시도
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 1.5)
    raise DramSpotDataError(f"공개 페이지 조회 실패: {url} · {last_error}")


def load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DramSpotDataError(f"기존 DRAM 파일을 읽지 못했습니다: {exc}") from exc


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def update(
    output: Path,
    *,
    official_html: str | None = None,
    price_history_payload: dict | None = None,
) -> dict:
    """공식 관측을 증분 적재하고 표시용 가격 이력은 점수와 분리해 보존합니다."""
    existing = load_existing(output)
    existing_rows = [
        row
        for row in (existing.get("history") or [])
        if row.get("source") == "trendforce-official"
    ]

    official_latest = None
    official_status = "direct"
    try:
        official_latest = parse_trendforce_spot_html(
            official_html if official_html is not None else fetch_text(TREND_FORCE_URL)
        )
    except DramSpotDataError:
        if not existing_rows:
            raise
        official_status = "stored-fallback"

    merged_rows = merge_history(existing_rows, [], official_latest)
    imported = price_history_payload or {}
    display_rows = [
        *(imported.get("priceHistory") or imported.get("history") or []),
        *(existing.get("priceHistory") or []),
    ]
    payload = build_payload(
        merged_rows,
        generated_at=datetime.now(KST),
        official_latest=official_latest,
        official_status=official_status,
        price_history=display_rows,
    )
    provenance = existing.get("priceHistoryProvenance")
    if price_history_payload is not None:
        provenance = {
            "sources": imported.get("sources") or {},
            "snapshotGeneratedAt": imported.get("generatedAt"),
            "usage": "price-chart-only",
        }
    if provenance:
        payload["priceHistoryProvenance"] = provenance
    write_json_atomic(output, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DRAM 현물가격 관찰지표를 갱신합니다.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--official-html", type=Path, help="테스트용 TrendForce HTML 파일")
    parser.add_argument("--import-price-history", help="차트 전용 과거 가격 JSON 경로. '-'는 표준입력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    price_history_payload = None
    if args.import_price_history:
        price_history_payload = (
            json.load(sys.stdin)
            if args.import_price_history == "-"
            else load_existing(Path(args.import_price_history))
        )
        if not isinstance(price_history_payload, dict) or not (
            price_history_payload.get("priceHistory") or price_history_payload.get("history")
        ):
            raise DramSpotDataError("가져올 가격 이력 JSON에 history 또는 priceHistory가 필요합니다.")
    payload = update(
        args.output,
        official_html=args.official_html.read_text(encoding="utf-8") if args.official_html else None,
        price_history_payload=price_history_payload,
    )
    latest = payload["latest"]
    quality = payload.get("qualityChecks") or {}
    score = latest.get("score")
    score_text = (
        f"{score:.1f}점"
        if isinstance(score, (int, float))
        else f"공식 관측 {quality.get('officialObservationCount', 0)}개 · 점수 준비 중"
    )
    print(f"DRAM 관찰지표 갱신: {latest['date']} · {score_text}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
