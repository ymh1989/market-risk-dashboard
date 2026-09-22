"""DRAM 현물가격 관찰지표 수집·정규화 모듈."""

from __future__ import annotations

import html
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
                "source": row.get("source") or "trendforce-official",
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
    official_status: str = "direct",
) -> dict[str, Any]:
    """TrendForce 공식 일별 관측만 저장하고 충분한 이력이 쌓이면 점수를 계산합니다."""
    normalized_rows = list(rows)
    if not normalized_rows:
        raise DramSpotDataError("TrendForce 공식 DRAM 관측값이 없습니다.")

    scored: list[dict[str, Any]] = []
    if len(normalized_rows) >= 21:
        scored = calculate_cycle_scores(normalized_rows)

    if scored:
        latest = dict(scored[-1])
        prior = scored[-2] if len(scored) > 1 else latest
        latest["change1d"] = round(latest["score"] - prior["score"], 1)
    else:
        latest_row = normalized_rows[-1]
        latest = {
            "date": latest_row["date"],
            "score": None,
            "rawScore": None,
            "pricesUsd": {
                key: round(float(latest_row["pricesUsd"][key]), 4)
                for key in PRODUCTS
            },
            "returns20dPct": {key: None for key in PRODUCTS},
            "returns60dPct": {key: None for key in PRODUCTS},
            "highGapPct": {key: None for key in PRODUCTS},
            "positiveBreadthPct": None,
            "source": latest_row.get("source") or "trendforce-official",
            "change1d": None,
        }

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

    observation_count = len(normalized_rows)
    score_ready = bool(scored)
    return {
        "schemaVersion": 2,
        "generatedAt": generated_at.isoformat(),
        "historyStart": normalized_rows[0]["date"],
        "scoreStart": scored[0]["date"] if scored else None,
        "latest": latest,
        "history": normalized_rows,
        "series": scored,
        "sources": {
            "officialLatest": {
                "provider": "TrendForce",
                "displayLabel": "TrendForce 공식 최신값",
                "url": "https://www.trendforce.com/price/dram/lpddr_spot",
                "role": "공개 세션 평균 최신값 · 일별 자체 적재",
                "status": official_status,
            }
        },
        "qualityChecks": {
            "officialObservationCount": observation_count,
            "minimumScoreObservations": 21,
            "scoreStatus": "ready" if score_ready else "building-official-history",
        },
        "methodology": {
            "label": "DRAM 현물가격 사이클 과열",
            "formula": "20일 모멘텀 35% + 60일 모멘텀 25% + 보유 이력 고점 근접도 25% + 4개 제품 상승 확산도 15%",
            "smoothing": "과거값만 사용하는 5일 지수평활",
            "direction": "상승 시 메모리 공급사 가격결정력과 수요기업 원가 부담이 함께 확대",
            "operatingRole": "시장리스크 관찰카드 · 종합점수 가중치 0",
            "readiness": "TrendForce 공식 관측 21개부터 점수 산출",
        },
        "limitations": [
            "TrendForce 공개 페이지의 최신값만 매일 자체 적재하므로 초기에는 시계열과 점수를 제공하지 않습니다.",
            "제품별 현물가격은 실제 계약가격·HBM 가격과 다르며 주가 방향을 직접 예측하지 않습니다.",
            "고점 근접도는 자체 적재를 시작한 이후의 보유 이력 안에서만 계산합니다.",
        ],
    }
