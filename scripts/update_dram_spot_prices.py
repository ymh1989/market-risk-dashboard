#!/usr/bin/env python3
"""공개 DRAM 현물가격을 증분 수집해 관찰지표 파일을 갱신합니다."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kospi_risk.dram_spot import (
    DramSpotDataError,
    build_payload,
    merge_history,
    normalize_history,
    parse_public_history_html,
    parse_trendforce_spot_html,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "dram-spot-prices.json"
TREND_FORCE_URL = "https://www.trendforce.com/price/dram/lpddr_spot"
PUBLIC_HISTORY_URL = "https://shoulder-project.vercel.app/"
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


def update(output: Path, *, official_html: str | None = None, history_html: str | None = None) -> dict:
    """기존 이력을 보존하고 공식 최신값과 신규 보조 이력을 추가합니다."""
    existing = load_existing(output)
    existing_rows = existing.get("history") or existing.get("series") or []

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

    fetched_rows = []
    history_status = "direct"
    try:
        long_rows = parse_public_history_html(
            history_html if history_html is not None else fetch_text(PUBLIC_HISTORY_URL)
        )
        fetched_rows = normalize_history(long_rows)
    except DramSpotDataError:
        if not existing_rows:
            raise
        history_status = "stored-fallback"

    merged_rows = merge_history(existing_rows, fetched_rows, official_latest)
    payload = build_payload(
        merged_rows,
        generated_at=datetime.now(KST),
        official_latest=official_latest,
        history_status=history_status,
        official_status=official_status,
    )
    write_json_atomic(output, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DRAM 현물가격 관찰지표를 갱신합니다.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--official-html", type=Path, help="테스트용 TrendForce HTML 파일")
    parser.add_argument("--history-html", type=Path, help="테스트용 공개 이력 HTML 파일")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = update(
        args.output,
        official_html=args.official_html.read_text(encoding="utf-8") if args.official_html else None,
        history_html=args.history_html.read_text(encoding="utf-8") if args.history_html else None,
    )
    latest = payload["latest"]
    quality = (payload.get("qualityChecks") or {}).get("officialVsHistory") or {}
    print(
        f"DRAM 관찰지표 갱신: {latest['date']} · {latest['score']:.1f}점 · "
        f"공식대조 {quality.get('status') or '기준일 불일치'}"
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
