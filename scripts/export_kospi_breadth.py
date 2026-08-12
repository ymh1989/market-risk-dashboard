from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "processed" / "kospi_breadth.parquet"
DEFAULT_METADATA = ROOT / "data" / "quality" / "kospi_breadth_update.json"
DEFAULT_OUTPUT = ROOT / "data" / "kospi-breadth.json"
REQUIRED_COLUMNS = {
    "date",
    "kospi_close",
    "kospi_return",
    "up",
    "down",
    "flat",
    "total",
    "net_breadth",
    "ad_ratio",
    "breadth_pct",
    "AD_line",
    "AD_ma5",
    "AD_ma20",
    "breadth_ma5",
    "breadth_ma20",
}


def _number(value: object, digits: int = 4) -> float | int | None:
    """JSON에 안전한 유한 숫자만 반환합니다."""

    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, digits)


def _percent(value: object, digits: int = 2) -> float | None:
    number = _number(value, digits + 4)
    return None if number is None else round(float(number) * 100, digits)


def _read_metadata(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _state(latest: pd.Series) -> dict[str, str]:
    breadth = float(latest["breadth_pct"])
    breadth_ma20 = float(latest["breadth_ma20"])
    ad_line = float(latest["AD_line"])
    ad_ma20 = float(latest["AD_ma20"])

    if float(latest["kospi_return"]) > 0 and breadth < 0:
        return {"id": "narrow_rally", "label": "대형주 편중", "tone": "caution"}

    if breadth > 0 and breadth > breadth_ma20 and ad_line > ad_ma20:
        return {"id": "expansion", "label": "확산 우위", "tone": "good"}
    if breadth < 0 and breadth < breadth_ma20 and ad_line < ad_ma20:
        return {"id": "contraction", "label": "위축 우위", "tone": "danger"}
    return {"id": "mixed", "label": "혼조", "tone": "watch"}


def build_breadth_payload(frame: pd.DataFrame, metadata: dict | None = None) -> dict:
    """운영 홈페이지용 Market Breadth JSON을 생성합니다."""

    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"Market Breadth 필수 컬럼 누락: {', '.join(missing)}")

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=["date"]).sort_values("date")
    data = data.drop_duplicates("date", keep="last").reset_index(drop=True)
    if data.empty:
        raise ValueError("Market Breadth 데이터가 비어 있습니다.")
    if not data["date"].is_monotonic_increasing or not data["date"].is_unique:
        raise ValueError("Market Breadth 날짜 정렬 또는 중복 제거에 실패했습니다.")

    count_total = data[["up", "down", "flat"]].sum(axis=1)
    if not count_total.equals(data["total"]):
        raise ValueError("up + down + flat과 total이 일치하지 않습니다.")

    metadata = metadata or {}
    quality = metadata.get("quality") or {}
    latest = data.iloc[-1]
    state = _state(latest)
    vkospi_merged = bool(metadata.get("vkospiMerged")) and "vkospi" in data.columns
    latest_date = latest["date"].date().isoformat()
    first_date = data.iloc[0]["date"].date().isoformat()
    generated_at = metadata.get("generatedAt") or datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%Y-%m-%d %H:%M:%S KST")

    interpretations = [
        f"상승 {int(latest['up'])} · 하락 {int(latest['down'])} · 보합 {int(latest['flat'])}",
        f"일간 확산도 {_percent(latest['breadth_pct']):+.1f}% · 20일 평균 {_percent(latest['breadth_ma20']):+.1f}%",
        (
            "AD Line이 20일선 위 · 누적 확산 흐름 우세"
            if float(latest["AD_line"]) >= float(latest["AD_ma20"])
            else "AD Line이 20일선 아래 · 누적 위축 흐름 우세"
        ),
    ]
    if float(latest["kospi_return"]) > 0 and float(latest["breadth_pct"]) < 0:
        interpretations.append(
            f"KOSPI {_percent(latest['kospi_return']):+.1f}% 상승에도 하락 종목 우세 · 대형주 편중 가능성"
        )
    if not vkospi_merged:
        interpretations.append("VKOSPI 미결합 · 공포지수 조합 판정은 보류")

    series = []
    for row in data.itertuples(index=False):
        item = {
            "date": row.date.date().isoformat(),
            "kospiClose": _number(row.kospi_close, 2),
            "kospiReturnPct": _percent(row.kospi_return),
            "up": int(row.up),
            "down": int(row.down),
            "flat": int(row.flat),
            "total": int(row.total),
            "netBreadth": int(row.net_breadth),
            "adRatio": _number(row.ad_ratio, 3),
            "breadthPct": _percent(row.breadth_pct),
            "breadthMa5Pct": _percent(row.breadth_ma5),
            "breadthMa20Pct": _percent(row.breadth_ma20),
            "adLine": _number(row.AD_line, 2),
            "adMa5": _number(row.AD_ma5, 2),
            "adMa20": _number(row.AD_ma20, 2),
        }
        if vkospi_merged:
            item["vkospi"] = _number(getattr(row, "vkospi", None), 2)
        series.append(item)

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "source": {
            "provider": "KRX",
            "library": "pykrx",
            "endpoint": "stock.get_market_ohlcv(date, market='KOSPI')",
            "frequency": "EOD",
            "universe": "pykrx KOSPI 주식 응답 · ETF/ETN 제외",
            "vkospiStatus": "merged" if vkospi_merged else "not_available",
        },
        "period": {
            "startDate": first_date,
            "endDate": latest_date,
            "observations": int(len(data)),
            "adLineBaseDate": first_date,
        },
        "quality": {
            "status": quality.get("status", "unknown"),
            "lookbackRows": quality.get("lookbackRows"),
            "latestTotal": quality.get("latestTotal", int(latest["total"])),
            "minRecentTotal": quality.get("minTotal"),
            "maxRecentTotal": quality.get("maxTotal"),
            "equationFailureDates": quality.get("equationFailureDates", []),
            "anomalies": quality.get("anomalies", []),
            "failedDates": metadata.get("failedDates", []),
            "emptyDatesCount": len(metadata.get("emptyDates", [])),
        },
        "latest": {
            "date": latest_date,
            "state": state,
            "kospiClose": _number(latest["kospi_close"], 2),
            "kospiReturnPct": _percent(latest["kospi_return"]),
            "up": int(latest["up"]),
            "down": int(latest["down"]),
            "flat": int(latest["flat"]),
            "total": int(latest["total"]),
            "netBreadth": int(latest["net_breadth"]),
            "adRatio": _number(latest["ad_ratio"], 3),
            "breadthPct": _percent(latest["breadth_pct"]),
            "breadthMa5Pct": _percent(latest["breadth_ma5"]),
            "breadthMa20Pct": _percent(latest["breadth_ma20"]),
            "adLine": _number(latest["AD_line"], 2),
            "adMa5": _number(latest["AD_ma5"], 2),
            "adMa20": _number(latest["AD_ma20"], 2),
            "adDistance20": _number(latest["AD_line"] - latest["AD_ma20"], 2),
            "interpretation": interpretations,
        },
        "methodology": [
            "breadth_pct = (상승 - 하락) / (상승 + 하락)",
            "AD Line = 저장 시작일부터 일별 (상승 - 하락) 누적",
            "AD Line 절대값은 시작일에 종속 · 방향과 20일선 비교 중심",
            "장 마감 EOD 기준 · 장중 판정 아님",
        ],
        "series": series,
    }


def export_breadth_json(input_path: Path, metadata_path: Path | None, output_path: Path) -> dict:
    frame = pd.read_parquet(input_path)
    payload = build_breadth_payload(frame, _read_metadata(metadata_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KOSPI Market Breadth 운영 JSON 생성")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = export_breadth_json(args.input, args.metadata, args.output)
    latest = payload["latest"]
    print(
        f"Market Breadth JSON 저장: {args.output} · "
        f"{latest['date']} · {latest['state']['label']} · {payload['period']['observations']}개"
    )


if __name__ == "__main__":
    main()
