from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .config import yaml
from .data_schema import REQUIRED_COLUMNS, validate_market_columns
from .data_loader import save_frame

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FRED_GRAPH_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
NAVER_CHART_URL = "https://api.finance.naver.com/siseJson.naver"
DEFAULT_SOURCE_CONFIG = Path("configs/data_sources.yaml")
FRED_HISTORY_CACHE = Path(__file__).resolve().parents[2] / "data" / "market-history-cache.json"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INCREMENTAL_OVERLAP_DAYS = 14


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


def _local_env_value(name: str) -> str:
    """환경변수 또는 저장소의 비공개 .env에서 값을 읽습니다."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return ""
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == name:
            return raw_value.strip().strip('"').strip("'")
    return ""


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


def _range_start(range_value: str | None) -> date:
    value = str(range_value or "10y").strip().lower()
    multiplier = 365
    if value.endswith("mo"):
        multiplier = 30
        amount = value[:-2]
    elif value.endswith("y"):
        amount = value[:-1]
    else:
        amount = "10"
    try:
        periods = max(1, int(amount))
    except ValueError:
        periods = 10
    return date.today() - timedelta(days=periods * multiplier)


def fetch_naver_series(
    column: str,
    spec: dict[str, Any],
    fetch_config: dict[str, Any],
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> SourceFetchResult:
    symbol = str(spec["symbol"])
    start_date = date.fromisoformat(start) if start else _range_start(range_value or fetch_config.get("range"))
    end_date = date.fromisoformat(end) if end else date.today() + timedelta(days=2)
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "requestType": 1,
            "startTime": start_date.strftime("%Y%m%d"),
            "endTime": end_date.strftime("%Y%m%d"),
            "timeframe": "day",
        }
    )
    request = urllib.request.Request(
        f"{NAVER_CHART_URL}?{params}",
        headers={
            "User-Agent": str(fetch_config.get("user_agent", "Mozilla/5.0")),
            "Accept": "text/plain",
        },
    )
    timeout = int(fetch_config.get("timeout_seconds", 20))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        rows = ast.literal_eval(response.read().decode("utf-8", errors="ignore").strip())

    values = []
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) < 5 or row[4] is None:
            continue
        values.append(
            {
                "date": datetime.strptime(str(row[0]), "%Y%m%d"),
                column: float(row[4]),
            }
        )
    frame = pd.DataFrame(values)
    if frame.empty:
        raise RuntimeError(f"{symbol}: 유효한 Naver 종가 데이터가 없습니다.")
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return SourceFetchResult(
        column=column,
        provider="naver",
        symbol=symbol,
        label=str(spec.get("label", column)),
        frame=frame,
        status="ok",
    )


def _fred_api_frame(
    column: str,
    symbol: str,
    api_key: str,
    start: str | None,
    end: str | None,
    timeout: int,
    user_agent: str,
) -> pd.DataFrame:
    if len(api_key) != 32 or not api_key.isalnum() or api_key.lower() != api_key:
        raise RuntimeError("FRED_API_KEY 형식이 올바르지 않습니다(32자 영문 소문자·숫자).")
    params = {
        "series_id": symbol,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }
    if start:
        params["observation_start"] = start
    if end:
        params["observation_end"] = end
    request = urllib.request.Request(
        f"{FRED_API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    values = [
        {"date": item.get("date"), column: item.get("value")}
        for item in payload.get("observations", [])
    ]
    frame = pd.DataFrame(values, columns=["date", column])
    if frame.empty:
        raise RuntimeError(f"{symbol}: FRED API 관측치가 없습니다.")
    return frame


def _normalize_fred_frame(
    frame: pd.DataFrame,
    column: str,
    symbol: str,
    start: str | None,
    end: str | None,
) -> pd.DataFrame:
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", column]).sort_values("date").drop_duplicates("date", keep="last")
    if start:
        frame = frame.loc[frame["date"] >= pd.Timestamp(start)]
    if end:
        frame = frame.loc[frame["date"] <= pd.Timestamp(end)]
    frame = frame.reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"{symbol}: 유효한 FRED 데이터가 없습니다.")
    return frame


def fetch_fred_series(
    column: str,
    spec: dict[str, Any],
    fetch_config: dict[str, Any],
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> SourceFetchResult:
    del range_value
    symbol = str(spec["symbol"])
    timeout = int(fetch_config.get("timeout_seconds", 20))
    user_agent = str(
        fetch_config.get("user_agent", "Mozilla/5.0 (compatible; kospi-risk-regime-lab/0.1)")
    )
    api_key = _local_env_value("FRED_API_KEY")
    api_error = None

    if api_key:
        try:
            frame = _normalize_fred_frame(
                _fred_api_frame(column, symbol, api_key, start, end, timeout, user_agent),
                column,
                symbol,
                start,
                end,
            )
            return SourceFetchResult(
                column=column,
                provider="fred_api",
                symbol=symbol,
                label=str(spec.get("label", column)),
                frame=frame,
                status="ok",
            )
        except Exception as exc:
            api_error = str(exc).replace(api_key, "***")

    params = {"id": symbol}
    if start:
        params["cosd"] = start
    if end:
        params["coed"] = end
    url = f"{FRED_GRAPH_CSV_URL}?{urllib.parse.urlencode(params)}"
    csv_error = None
    try:
        response = subprocess.run(
            ["curl", "-L", "--silent", "--show-error", "--max-time", str(timeout), url],
            check=True,
            capture_output=True,
            text=True,
        )
        csv_text = response.stdout
    except Exception as curl_error:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": user_agent, "Accept": "text/csv,*/*", "Connection": "close"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                csv_text = response.read().decode("utf-8")
        except Exception as urllib_error:
            csv_error = RuntimeError(f"curl: {curl_error}; urllib: {urllib_error}")

    if csv_error is None:
        try:
            raw = pd.read_csv(io.StringIO(csv_text))
            date_column = "observation_date" if "observation_date" in raw.columns else "DATE"
            if date_column not in raw.columns or symbol not in raw.columns:
                raise RuntimeError(f"{symbol}: FRED CSV 형식이 예상과 다릅니다.")
            frame = _normalize_fred_frame(
                raw.rename(columns={date_column: "date", symbol: column})[["date", column]],
                column,
                symbol,
                start,
                end,
            )
            return SourceFetchResult(
                column=column,
                provider="fred_csv",
                symbol=symbol,
                label=str(spec.get("label", column)),
                frame=frame,
                status="fallback" if api_error is not None else "ok",
                error=f"FRED 정식 API 실패로 공개 CSV 사용: {api_error}" if api_error else None,
            )
        except Exception as parse_error:
            csv_error = parse_error

    details = f"FRED 공개 CSV 실패: {csv_error}"
    if api_error is not None:
        details = f"FRED 정식 API 실패: {api_error}; {details}"
    return load_cached_fred_result(column, spec, start, end, details)


def load_cached_fred_result(
    column: str,
    spec: dict[str, Any],
    start: str | None,
    end: str | None,
    direct_error: str,
) -> SourceFetchResult:
    cache_key = str(spec.get("cache_key", ""))
    if not cache_key or not FRED_HISTORY_CACHE.exists():
        raise RuntimeError(f"{spec['symbol']}: FRED 조회 실패 및 저장 캐시 없음 ({direct_error})")
    payload = json.loads(FRED_HISTORY_CACHE.read_text(encoding="utf-8"))
    rows = (payload.get("fred") or {}).get(cache_key, [])
    frame = pd.DataFrame(rows)
    if frame.empty or not {"date", "close"}.issubset(frame.columns):
        raise RuntimeError(f"{spec['symbol']}: FRED 조회 실패 및 저장 캐시 데이터 없음 ({direct_error})")
    frame = frame.rename(columns={"close": column})[["date", column]]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", column]).sort_values("date").drop_duplicates("date", keep="last")
    if start:
        frame = frame.loc[frame["date"] >= pd.Timestamp(start)]
    if end:
        frame = frame.loc[frame["date"] <= pd.Timestamp(end)]
    frame = frame.reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"{spec['symbol']}: 지정 기간의 저장 캐시 데이터 없음 ({direct_error})")
    return SourceFetchResult(
        column=column,
        provider="fred_cache",
        symbol=str(spec["symbol"]),
        label=str(spec.get("label", column)),
        frame=frame,
        status="cached",
        error=f"직접 조회 실패로 {FRED_HISTORY_CACHE.name} 사용: {direct_error}",
    )


def fetch_source_series(
    column: str,
    spec: dict[str, Any],
    fetch_config: dict[str, Any],
    range_value: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> SourceFetchResult:
    provider = str(spec.get("provider", fetch_config.get("provider", "yahoo")))
    if provider == "yahoo":
        return fetch_yahoo_series(column, spec, fetch_config, range_value, start, end)
    if provider == "naver":
        return fetch_naver_series(column, spec, fetch_config, range_value, start, end)
    if provider == "fred":
        return fetch_fred_series(column, spec, fetch_config, range_value, start, end)
    raise RuntimeError(f"지원하지 않는 provider입니다: {provider}")


def fetch_source_with_supplements(
    column: str,
    spec: dict[str, Any],
    fetch_config: dict[str, Any],
    range_value: str | None,
    start: str | None,
    end: str | None,
    fetcher: Callable[
        [str, dict[str, Any], dict[str, Any], str | None, str | None, str | None],
        SourceFetchResult,
    ],
) -> SourceFetchResult:
    primary = fetcher(column, spec, fetch_config, range_value, start, end)
    frames = [primary.frame]
    providers = [primary.provider]
    symbols = [primary.symbol]
    warnings = [primary.error] if primary.error else []

    for supplement in spec.get("supplements") or []:
        try:
            result = fetcher(column, supplement, fetch_config, range_value, start, end)
        except Exception as exc:
            warnings.append(f"{supplement.get('provider', '보강 원천')} 조회 실패: {exc}")
            continue
        supplement_frame = result.frame.copy()
        if supplement.get("rebase_to_primary"):
            overlap = primary.frame.merge(
                supplement_frame,
                on="date",
                how="inner",
                suffixes=("_primary", "_supplement"),
            ).dropna()
            if overlap.empty:
                warnings.append(f"{result.symbol} 보강값은 기준 시계열과 겹치는 날짜가 없어 제외")
                continue
            ratios = overlap[f"{column}_primary"] / overlap[f"{column}_supplement"]
            valid_ratios = ratios.replace([float("inf"), float("-inf")], pd.NA).dropna().tail(20)
            if valid_ratios.empty:
                warnings.append(f"{result.symbol} 보강값의 가격 레벨을 연결할 수 없어 제외")
                continue
            supplement_frame[column] = supplement_frame[column] * float(valid_ratios.median())
        if supplement.get("append_after_primary"):
            supplement_frame = supplement_frame.loc[
                supplement_frame["date"] > primary.frame["date"].max()
            ].copy()
            if supplement_frame.empty:
                continue
        frames.append(supplement_frame)
        providers.append(result.provider)
        symbols.append(result.symbol)
        if result.error:
            warnings.append(result.error)

    merged = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    supplemented = len(frames) > 1
    return SourceFetchResult(
        column=column,
        provider="+".join(dict.fromkeys(providers)),
        symbol="+".join(dict.fromkeys(symbols)),
        label=primary.label,
        frame=merged,
        status="supplemented" if supplemented else primary.status,
        error="; ".join(warnings) if warnings else None,
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
    fetcher: Callable[[str, dict[str, Any], dict[str, Any], str | None, str | None, str | None], SourceFetchResult] = fetch_source_series,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    fetch_config = source_config.get("fetch") or {}
    if not range_value and not start and not end and fetch_config.get("start"):
        start = str(fetch_config["start"])
        end = (date.today() + timedelta(days=2)).isoformat()
    frames: list[pd.DataFrame] = []
    sources = []
    required_failures = []

    for column, spec, required in _source_items(source_config):
        provider = str(spec.get("provider", fetch_config.get("provider", "yahoo")))
        try:
            result = fetch_source_with_supplements(
                column,
                spec,
                fetch_config,
                range_value,
                start,
                end,
                fetcher,
            )
            coverage_ratio = 1.0
            if frames and not required:
                existing_dates = set(pd.concat([frame[["date"]] for frame in frames], ignore_index=True)["date"])
                coverage_ratio = len(set(result.frame["date"]) & existing_dates) / max(len(existing_dates), 1)
            min_optional_coverage = float(
                spec.get("min_coverage_ratio", fetch_config.get("min_optional_coverage_ratio", 0.0))
            )
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
                    "warning": result.error,
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


def _load_incremental_market_base(path: Path, min_rows: int) -> pd.DataFrame | None:
    """검증된 기존 원자료가 있으면 증분 적재의 기준 원장으로 읽는다."""

    if not path.exists() or path.suffix != ".csv":
        return None
    try:
        frame = pd.read_csv(path)
        validate_market_columns(set(frame.columns))
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(frame) < min_rows:
        return None
    for column in frame.columns:
        if column != "date":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _merge_incremental_market_data(
    existing: pd.DataFrame,
    fetched: pd.DataFrame,
) -> pd.DataFrame:
    """신규 비결측값을 우선해 장기 원장과 겹친 최근 구간을 병합한다."""

    old = existing.copy()
    new = fetched.copy()
    old["date"] = pd.to_datetime(old["date"], errors="coerce")
    new["date"] = pd.to_datetime(new["date"], errors="coerce")
    old = old.dropna(subset=["date"]).drop_duplicates("date", keep="last").set_index("date")
    new = new.dropna(subset=["date"]).drop_duplicates("date", keep="last").set_index("date")
    merged = new.combine_first(old).sort_index().reset_index()
    ordered_columns = [
        "date",
        *[column for column in existing.columns if column != "date"],
        *[
            column
            for column in fetched.columns
            if column != "date" and column not in existing.columns
        ],
    ]
    return merged.loc[:, ordered_columns]


def _write_market_outputs_atomic(
    frame: pd.DataFrame,
    metadata: dict[str, Any],
    output_path: Path,
    metadata_path: Path,
) -> None:
    """중간 종료가 기존 원장과 메타데이터를 훼손하지 않도록 원자적으로 교체한다."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    frame_temp = output_path.with_name(
        f".{output_path.stem}.{os.getpid()}.tmp{output_path.suffix}"
    )
    metadata_temp = metadata_path.with_name(
        f".{metadata_path.stem}.{os.getpid()}.tmp{metadata_path.suffix}"
    )
    try:
        save_frame(frame, frame_temp)
        metadata_temp.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(frame_temp, output_path)
        os.replace(metadata_temp, metadata_path)
    finally:
        frame_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)


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
    output = Path(output_path)
    metadata_output = Path(metadata_path)
    automatic_window = range_value is None and start is None and end is None
    existing = _load_incremental_market_base(output, min_rows) if automatic_window else None
    overlap_days = max(
        1,
        int(
            (source_config.get("fetch") or {}).get(
                "incremental_overlap_days", DEFAULT_INCREMENTAL_OVERLAP_DAYS
            )
        ),
    )

    fetch_start = start
    fetch_end = end
    collection_mode = "bootstrap" if automatic_window else "explicit-range"
    if existing is not None:
        latest_date = pd.Timestamp(existing["date"].max()).date()
        fetch_start = (latest_date - timedelta(days=overlap_days)).isoformat()
        fetch_end = (date.today() + timedelta(days=2)).isoformat()
        collection_mode = "incremental"

    fetched, metadata = fetch_market_data(
        source_config,
        range_value=range_value,
        start=fetch_start,
        end=fetch_end,
    )
    df = (
        _merge_incremental_market_data(existing, fetched)
        if existing is not None
        else fetched
    )
    if len(df) < min_rows:
        raise RuntimeError(f"수집 데이터가 부족합니다: {len(df)} rows, 최소 {min_rows} rows 필요")

    for source in metadata.get("sources", []):
        source["collectionMode"] = collection_mode
        source["fetchedRows"] = int(source.get("rows") or 0)
        column = source.get("column")
        if collection_mode != "incremental" or column not in df.columns:
            continue
        valid = df.loc[df[column].notna(), ["date", column]]
        if valid.empty:
            continue
        source.update(
            {
                "rows": int(len(valid)),
                "coverageRatio": round(len(valid) / max(len(df), 1), 4),
                "firstDate": valid["date"].min().date().isoformat(),
                "lastDate": valid["date"].max().date().isoformat(),
            }
        )

    metadata.update(
        {
            "collectionMode": collection_mode,
            "overlapDays": overlap_days if collection_mode == "incremental" else 0,
            "cachedRows": int(len(existing)) if existing is not None else 0,
            "cachedLastDate": (
                existing["date"].max().date().isoformat()
                if existing is not None
                else None
            ),
            "fetchedRows": int(len(fetched)),
            "rows": int(len(df)),
            "columns": [column for column in df.columns if column != "date"],
            "firstDate": df["date"].min().date().isoformat(),
            "lastDate": df["date"].max().date().isoformat(),
            "missingByColumn": {
                column: int(df[column].isna().sum())
                for column in df.columns
                if column != "date"
            },
        }
    )
    _write_market_outputs_atomic(df, metadata, output, metadata_output)
    return df, metadata
