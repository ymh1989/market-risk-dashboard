"""DRAM 현물가격 관찰지표 수집·정규화 모듈."""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any


HISTORY_START_DATE = "2025-01-01"
SCORE_SMOOTHING_ALPHA = 2 / 6

PRODUCTS = {
    "DDR5_16Gb": {
        "label": "DDR5 16Gb",
        "spec": "2Gx8 4800/5600",
        "trendforceLabel": "DDR5 16Gb (2Gx8) 4800/5600",
    },
    "DDR4_16Gb": {
        "label": "DDR4 16Gb",
        "spec": "2Gx8 3200",
        "trendforceLabel": "DDR4 16Gb (2Gx8) 3200",
    },
    "DDR4_8Gb": {
        "label": "DDR4 8Gb",
        "spec": "1Gx8 3200",
        "trendforceLabel": "DDR4 8Gb (1Gx8) 3200",
    },
    "DDR3_4Gb": {
        "label": "DDR3 4Gb",
        "spec": "512Mx8 1600/1866",
        "trendforceLabel": "DDR3 4Gb 512Mx8 1600/1866",
    },
}


class DramSpotDataError(RuntimeError):
    """DRAM 공개 데이터가 예상 스키마와 다를 때 발생합니다."""


def _strip_tags(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def parse_trendforce_spot_html(document: str) -> dict[str, Any]:
    """TrendForce 공개 표에서 기준일과 4개 제품의 세션 평균을 추출합니다."""
    date_match = re.search(r"Last Update\s+(\d{4}-\d{2}-\d{2})", document)
    if not date_match:
        raise DramSpotDataError("TrendForce 최종 업데이트 일자를 찾지 못했습니다.")

    prices: dict[str, float] = {}
    session_changes: dict[str, float] = {}
    for product_id, config in PRODUCTS.items():
        row_match = re.search(
            rf"<tr[^>]*>(?:(?!</tr>).)*{re.escape(config['trendforceLabel'])}"
            rf"(?:(?!</tr>).)*</tr>",
            document,
            flags=re.DOTALL,
        )
        if not row_match:
            raise DramSpotDataError(f"TrendForce 제품 행을 찾지 못했습니다: {product_id}")
        row = row_match.group(0)
        numbers = [
            float(value)
            for value in re.findall(
                r'<td[^>]*class="[^"]*lcd-num-l[^"]*"[^>]*>\s*([0-9.]+)\s*</td>',
                row,
            )
        ]
        if len(numbers) < 5 or numbers[4] <= 0:
            raise DramSpotDataError(f"TrendForce 세션 평균값이 유효하지 않습니다: {product_id}")
        prices[product_id] = numbers[4]

        percent_match = re.search(r'class="percent-cell"[^>]*>(.*?)</td>', row, re.DOTALL)
        percent_text = _strip_tags(percent_match.group(1)) if percent_match else ""
        change_match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", percent_text)
        session_changes[product_id] = float(change_match.group(1)) if change_match else 0.0

    return {
        "date": date_match.group(1),
        "pricesUsd": prices,
        "sessionChangesPct": session_changes,
    }


def parse_public_history_html(document: str) -> list[dict[str, Any]]:
    """공개 페이지의 Next.js payload에서 52주 제품별 이력을 추출합니다."""
    match = re.search(
        r'\\"dramHistoryAll\\":(\[.*?\]),\\"[^\\"]+\\":',
        document,
        flags=re.DOTALL,
    )
    if not match:
        match = re.search(r'"dramHistoryAll":(\[.*?\]),"[^\"]+":', document, re.DOTALL)
    if not match:
        raise DramSpotDataError("공개 52주 DRAM 이력 payload를 찾지 못했습니다.")

    raw_json = match.group(1).replace(r'\"', '"')
    try:
        rows = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise DramSpotDataError("공개 DRAM 이력 JSON을 해석하지 못했습니다.") from exc

    if not isinstance(rows, list) or not rows:
        raise DramSpotDataError("공개 DRAM 이력이 비어 있습니다.")
    return rows


def normalize_history(
    rows: Iterable[Mapping[str, Any]],
    *,
    start_date: str = HISTORY_START_DATE,
) -> list[dict[str, Any]]:
    """제품별 long 형식 이력을 평일 단위 wide 형식으로 변환합니다."""
    by_date: dict[str, dict[str, float]] = {}
    for row in rows:
        observed_date = str(row.get("date") or "")
        product_id = str(row.get("chip_type") or "")
        value = row.get("price_usd")
        if observed_date < start_date or product_id not in PRODUCTS:
            continue
        try:
            parsed_date = date.fromisoformat(observed_date)
            price = float(value)
        except (TypeError, ValueError):
            continue
        if parsed_date.weekday() >= 5 or not math.isfinite(price) or price <= 0:
            continue
        by_date.setdefault(observed_date, {})[product_id] = price

    complete_rows = []
    required = set(PRODUCTS)
    for observed_date in sorted(by_date):
        prices = by_date[observed_date]
        if set(prices) != required:
            continue
        complete_rows.append(
            {
                "date": observed_date,
                "pricesUsd": {key: round(prices[key], 4) for key in PRODUCTS},
                "source": "public-history",
            }
        )
    if not complete_rows:
        raise DramSpotDataError("4개 제품이 모두 있는 평일 DRAM 이력이 없습니다.")
    return complete_rows


def merge_history(
    existing_rows: Iterable[Mapping[str, Any]],
    fetched_rows: Iterable[Mapping[str, Any]],
    official_latest: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """기존 확정 이력을 보존하면서 신규 날짜와 공식 최신값을 병합합니다."""
    merged: dict[str, dict[str, Any]] = {}
    for row in existing_rows:
        observed_date = str(row.get("date") or "")
        prices = row.get("pricesUsd")
        if observed_date >= HISTORY_START_DATE and isinstance(prices, Mapping):
            merged[observed_date] = {
                "date": observed_date,
                "pricesUsd": {key: float(prices[key]) for key in PRODUCTS},
                "source": row.get("source") or "stored-history",
            }
    for row in fetched_rows:
        observed_date = str(row.get("date") or "")
        if observed_date and observed_date not in merged:
            merged[observed_date] = dict(row)

    if official_latest:
        observed_date = str(official_latest.get("date") or "")
        prices = official_latest.get("pricesUsd")
        if observed_date >= HISTORY_START_DATE and isinstance(prices, Mapping):
            parsed_date = date.fromisoformat(observed_date)
            if parsed_date.weekday() < 5:
                merged[observed_date] = {
                    "date": observed_date,
                    "pricesUsd": {key: round(float(prices[key]), 4) for key in PRODUCTS},
                    "source": "trendforce-official",
                }
    return [merged[key] for key in sorted(merged)]


def _return_pct(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-lookback - 1] <= 0:
        return None
    return (values[-1] / values[-lookback - 1] - 1) * 100


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def calculate_cycle_scores(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """현재와 과거값만 사용해 DRAM 가격사이클 관찰점수를 계산합니다."""
    histories: dict[str, list[float]] = {key: [] for key in PRODUCTS}
    output: list[dict[str, Any]] = []
    smoothed_score: float | None = None

    for row in sorted(rows, key=lambda item: str(item.get("date") or "")):
        prices = row.get("pricesUsd") or {}
        if any(key not in prices for key in PRODUCTS):
            continue
        for key in PRODUCTS:
            histories[key].append(float(prices[key]))
        if min(len(values) for values in histories.values()) < 21:
            continue

        returns_20d = {key: _return_pct(values, 20) for key, values in histories.items()}
        returns_60d = {
            key: _return_pct(values, 60) if len(values) > 60 else returns_20d[key]
            for key, values in histories.items()
        }
        high_gaps = {
            key: (values[-1] / max(values[-252:]) - 1) * 100
            for key, values in histories.items()
        }
        average_20d = sum(float(value) for value in returns_20d.values()) / len(PRODUCTS)
        average_60d = sum(float(value) for value in returns_60d.values()) / len(PRODUCTS)
        average_high_gap = sum(high_gaps.values()) / len(PRODUCTS)
        positive_breadth = sum(float(value) > 0 for value in returns_20d.values()) / len(PRODUCTS)

        momentum_20_score = _clamp(50 + average_20d * 1.25)
        momentum_60_score = _clamp(50 + average_60d * 0.625)
        high_position_score = _clamp(100 + average_high_gap * 2)
        breadth_score = positive_breadth * 100
        raw_score = (
            momentum_20_score * 0.35
            + momentum_60_score * 0.25
            + high_position_score * 0.25
            + breadth_score * 0.15
        )
        smoothed_score = (
            raw_score
            if smoothed_score is None
            else SCORE_SMOOTHING_ALPHA * raw_score
            + (1 - SCORE_SMOOTHING_ALPHA) * smoothed_score
        )
        output.append(
            {
                "date": row["date"],
                "score": round(smoothed_score, 1),
                "rawScore": round(raw_score, 1),
                "pricesUsd": {key: round(float(prices[key]), 4) for key in PRODUCTS},
                "returns20dPct": {key: round(float(returns_20d[key]), 2) for key in PRODUCTS},
                "returns60dPct": {key: round(float(returns_60d[key]), 2) for key in PRODUCTS},
                "highGapPct": {key: round(float(high_gaps[key]), 2) for key in PRODUCTS},
                "positiveBreadthPct": round(positive_breadth * 100, 1),
                "source": row.get("source") or "public-history",
            }
        )
    if not output:
        raise DramSpotDataError("DRAM 관찰점수 계산에 필요한 20개 초과 관측이 없습니다.")
    return output


def build_payload(
    rows: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime,
    official_latest: Mapping[str, Any] | None = None,
    history_status: str = "direct",
    official_status: str = "direct",
) -> dict[str, Any]:
    """저장·대시보드 연동용 DRAM 관찰지표 payload를 생성합니다."""
    normalized_rows = list(rows)
    scored = calculate_cycle_scores(normalized_rows)
    latest = dict(scored[-1])
    prior = scored[-2] if len(scored) > 1 else latest
    latest["change1d"] = round(latest["score"] - prior["score"], 1)
    latest["products"] = [
        {
            "id": key,
            "label": config["label"],
            "spec": config["spec"],
            "priceUsd": latest["pricesUsd"][key],
            "return20dPct": latest["returns20dPct"][key],
            "return60dPct": latest["returns60dPct"][key],
            "highGapPct": latest["highGapPct"][key],
        }
        for key, config in PRODUCTS.items()
    ]

    comparison = None
    if official_latest and official_latest.get("date") == latest["date"]:
        differences = []
        for key in PRODUCTS:
            official = float(official_latest["pricesUsd"][key])
            stored = float(latest["pricesUsd"][key])
            differences.append(abs(stored / official - 1) * 100)
        comparison = {
            "date": latest["date"],
            "maxDifferencePct": round(max(differences), 4),
            "status": "matched" if max(differences) <= 0.1 else "mismatch",
        }

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at.isoformat(),
        "historyStart": normalized_rows[0]["date"],
        "scoreStart": scored[0]["date"],
        "latest": latest,
        "history": normalized_rows,
        "series": scored,
        "sources": {
            "officialLatest": {
                "provider": "TrendForce",
                "displayLabel": "TrendForce 공식 최신값",
                "url": "https://www.trendforce.com/price/dram/lpddr_spot",
                "role": "공개 세션 평균 최신값",
                "status": official_status,
            },
            "publicHistory": {
                "provider": "어깨에서 팔기 프로젝트",
                "displayLabel": "공개 DRAM 52주 이력(보조)",
                "providerNote": "제공: 어깨에서 팔기 프로젝트",
                "url": "https://shoulder-project.vercel.app/",
                "role": "2025-09-22 이후 공개 52주 이력 보조",
                "status": history_status,
            },
        },
        "qualityChecks": {"officialVsHistory": comparison},
        "methodology": {
            "label": "DRAM 현물가격 사이클 과열",
            "formula": "20일 모멘텀 35% + 60일 모멘텀 25% + 52주 고점 근접도 25% + 4개 제품 상승 확산도 15%",
            "smoothing": "과거값만 사용하는 5일 지수평활",
            "direction": "상승 시 메모리 공급사 가격결정력과 수요기업 원가 부담이 함께 확대",
            "operatingRole": "시장리스크 관찰카드 · 종합점수 가중치 0",
        },
        "limitations": [
            "공식 공개 페이지는 최신값만 제공하므로 과거 이력은 공개 보조 페이지를 사용합니다.",
            "제품별 현물가격은 실제 계약가격·HBM 가격과 다르며 주가 방향을 직접 예측하지 않습니다.",
            "시계열 시작 이전 고점은 알 수 없어 52주 고점 근접도는 보유 이력 안에서 계산합니다.",
        ],
    }
