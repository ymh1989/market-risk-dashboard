from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import yaml
from .data_schema import REQUIRED_COLUMNS, validate_market_columns
from .data_loader import save_frame

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_SOURCE_CONFIG = Path("configs/data_sources.yaml")


@dataclass
class SourceFetchResult:
    column: str
    provider: str
    symbol: str
    label: str
    frame: pd.DataFrame
    status: str
    error: str | None = None


def _utc_timestamp(value: str) -> int:
    dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_source_config(path: str | Path = DEFAULT_SOURCE_CONFIG) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"데이터 소스 설정 파일을 찾을 수 없습니다: {config_path}")
    if yaml is None:
        raise RuntimeError("YAML 설정을 읽으려면 PyYAML이 필요합니다.")
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def fetch_yahoo_series(
    column: str,
    spec: dict[str, Any],
    fetch_config: dict[str, Any],
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> SourceFetchResult:
    symbol = str(spec["symbol"])
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    params: dict[str, str | int] = {"interval": str(fetch_config.get("interval", "1d"))}
    if start or end:
        if not start or not end:
            raise ValueError("--start와 --end는 함께 지정해야 합니다.")
        params["period1"] = _utc_timestamp(start)
        params["period2"] = _utc_timestamp(end)
    else:
        params["range"] = range_value or str(fetch_config.get("range", "10y"))

    url = f"{YAHOO_CHART_URL.format(symbol=encoded_symbol)}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": str(fetch_config.get("user_agent", "Mozilla/5.0 (compatible; kospi-risk-regime-lab/0.1)")),
            "Accept": "application/json",
        },
    )
    timeout = int(fetch_config.get("timeout_seconds", 20))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    chart = payload.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"{symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"{symbol}: Yahoo 응답에 result가 없습니다.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    rows = []
    for index, timestamp in enumerate(timestamps):
        if index >= len(closes) or closes[index] is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                column: float(closes[index]),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"{symbol}: 유효한 close 데이터가 없습니다.")
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return SourceFetchResult(
        column=column,
        provider="yahoo",
        symbol=symbol,
        label=str(spec.get("label", column)),
        frame=frame,
        status="ok",
    )


def _source_items(source_config: dict[str, Any]) -> list[tuple[str, dict[str, Any], bool]]:
    items: list[tuple[str, dict[str, Any], bool]] = []
    for column, spec in (source_config.get("required") or {}).items():
        items.append((column, spec, True))
    for column, spec in (source_config.get("optional") or {}).items():
        items.append((column, spec, False))
    return items


def fetch_market_data(
    source_config: dict[str, Any],
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
    fetcher: Callable[[str, dict[str, Any], dict[str, Any], str | None, str | None, str | None], SourceFetchResult] = fetch_yahoo_series,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    fetch_config = source_config.get("fetch") or {}
    frames: list[pd.DataFrame] = []
    sources = []
    required_failures = []

    for column, spec, required in _source_items(source_config):
        provider = str(spec.get("provider", fetch_config.get("provider", "yahoo")))
        if provider != "yahoo":
            error = f"지원하지 않는 provider입니다: {provider}"
            if required:
                required_failures.append(f"{column}: {error}")
            sources.append({"column": column, "provider": provider, "symbol": spec.get("symbol"), "required": required, "status": "failed", "error": error})
            continue
        try:
            result = fetcher(column, spec, fetch_config, range_value, start, end)
            coverage_ratio = 1.0
            if frames and not required:
                existing_dates = set(pd.concat([frame[["date"]] for frame in frames], ignore_index=True)["date"])
                coverage_ratio = len(set(result.frame["date"]) & existing_dates) / max(len(existing_dates), 1)
            min_optional_coverage = float(fetch_config.get("min_optional_coverage_ratio", 0.0))
            if not required and coverage_ratio < min_optional_coverage:
                sources.append(
                    {
                        "column": column,
                        "provider": result.provider,
                        "symbol": result.symbol,
                        "label": result.label,
                        "required": required,
                        "status": "skipped_low_coverage",
                        "rows": len(result.frame),
                        "coverageRatio": round(coverage_ratio, 4),
                        "firstDate": result.frame["date"].min().date().isoformat(),
                        "lastDate": result.frame["date"].max().date().isoformat(),
                    }
                )
                continue
            frames.append(result.frame)
            sources.append(
                {
                    "column": column,
                    "provider": result.provider,
                    "symbol": result.symbol,
                    "label": result.label,
                    "required": required,
                    "status": result.status,
                    "rows": len(result.frame),
                    "coverageRatio": round(coverage_ratio, 4),
                    "firstDate": result.frame["date"].min().date().isoformat(),
                    "lastDate": result.frame["date"].max().date().isoformat(),
                }
            )
        except Exception as exc:
            if required:
                required_failures.append(f"{column}: {exc}")
            sources.append({"column": column, "provider": provider, "symbol": spec.get("symbol"), "required": required, "status": "failed", "error": str(exc)})

    if required_failures:
        raise RuntimeError("필수 데이터 수집 실패: " + "; ".join(required_failures))
    if not frames:
        raise RuntimeError("수집된 데이터가 없습니다.")

    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how=str(fetch_config.get("alignment", "outer")))
    merged = merged.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    validate_market_columns(set(merged.columns))

    metadata = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "range": range_value or fetch_config.get("range"),
        "start": start,
        "end": end,
        "alignment": fetch_config.get("alignment", "outer"),
        "rows": len(merged),
        "firstDate": merged["date"].min().date().isoformat(),
        "lastDate": merged["date"].max().date().isoformat(),
        "requiredColumns": REQUIRED_COLUMNS,
        "columns": [column for column in merged.columns if column != "date"],
        "missingByColumn": {column: int(merged[column].isna().sum()) for column in merged.columns if column != "date"},
        "sources": sources,
    }
    return merged, metadata


def fetch_and_save_market_data(
    source_config_path: str | Path,
    output_path: str | Path,
    metadata_path: str | Path,
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
    min_rows: int = 1500,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source_config = load_source_config(source_config_path)
    df, metadata = fetch_market_data(source_config, range_value=range_value, start=start, end=end)
    if len(df) < min_rows:
        raise RuntimeError(f"수집 데이터가 부족합니다: {len(df)} rows, 최소 {min_rows} rows 필요")
    save_frame(df, output_path)
    metadata_output = Path(metadata_path)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return df, metadata
