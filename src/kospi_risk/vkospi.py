from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


LOGGER = logging.getLogger(__name__)
STOCKPLUS_SECURITY_ID = "KOREA-O2901P"
STOCKPLUS_PAGE_URL = f"https://www.stockplus.com/m/stocks/{STOCKPLUS_SECURITY_ID}"
STOCKPLUS_API_URL = (
    "https://mweb-api.stockplus.com/api/securities/"
    f"{STOCKPLUS_SECURITY_ID}/day_candles.json"
)
DEFAULT_START_DATE = "2009-01-01"
DEFAULT_PAGE_LIMIT = 500
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-vkospi/1.0)"

StockplusPageFetcher = Callable[[str, int], Mapping[str, Any]]


def _timestamp(value: str | datetime | pd.Timestamp, label: str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{label} 날짜 형식이 올바르지 않습니다: {value}")
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.tz_localize(None)
    return pd.Timestamp(parsed).normalize()


def parse_stockplus_day_candles(payload: Mapping[str, Any]) -> pd.DataFrame:
    """증권플러스 일봉 응답을 정렬·중복 제거한 VKOSPI dataframe으로 변환합니다."""

    rows = payload.get("dayCandles")
    if not isinstance(rows, list):
        raise ValueError("증권플러스 응답에 dayCandles 배열이 없습니다.")

    parsed_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        date_value = pd.to_datetime(row.get("date"), errors="coerce", utc=True)
        close_value = pd.to_numeric(row.get("tradePrice"), errors="coerce")
        if pd.isna(date_value) or pd.isna(close_value) or float(close_value) <= 0:
            continue
        parsed_rows.append(
            {
                "date": date_value.tz_convert(None).normalize(),
                "vkospi": float(close_value),
                "change_price": pd.to_numeric(row.get("changePrice"), errors="coerce"),
                "change_rate": pd.to_numeric(row.get("changePriceRate"), errors="coerce"),
                "change": str(row.get("change") or ""),
            }
        )

    columns = ["date", "vkospi", "change_price", "change_rate", "change"]
    if not parsed_rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(parsed_rows, columns=columns)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def _fetch_stockplus_page(to_date: str, limit: int) -> Mapping[str, Any]:
    params = urllib.parse.urlencode({"limit": limit, "to": to_date})
    request = urllib.request.Request(
        f"{STOCKPLUS_API_URL}?{params}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": STOCKPLUS_PAGE_URL,
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("증권플러스 VKOSPI 응답이 JSON 객체가 아닙니다.")
    return payload


def fetch_stockplus_vkospi(
    start_date: str | datetime | pd.Timestamp = DEFAULT_START_DATE,
    end_date: str | datetime | pd.Timestamp | None = None,
    *,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    retries: int = 3,
    sleep_seconds: float = 0.15,
    page_fetcher: StockplusPageFetcher | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """증권플러스 공개 일봉 API에서 VKOSPI 과거 시계열을 페이지 단위로 조회합니다."""

    start = _timestamp(start_date, "시작")
    end = _timestamp(
        end_date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "종료",
    )
    if start > end:
        raise ValueError("VKOSPI 시작일은 종료일보다 늦을 수 없습니다.")
    if not 1 <= page_limit <= DEFAULT_PAGE_LIMIT:
        raise ValueError(f"페이지 크기는 1~{DEFAULT_PAGE_LIMIT} 범위여야 합니다.")
    if retries < 1:
        raise ValueError("재시도 횟수는 1 이상이어야 합니다.")

    fetcher = page_fetcher or _fetch_stockplus_page
    cursor = (end + pd.Timedelta(days=1)).date().isoformat()
    seen_cursors: set[str] = set()
    pages: list[pd.DataFrame] = []

    while cursor not in seen_cursors:
        seen_cursors.add(cursor)
        last_error: Exception | None = None
        page = pd.DataFrame()
        for attempt in range(1, retries + 1):
            try:
                page = parse_stockplus_day_candles(fetcher(cursor, page_limit))
                last_error = None
                break
            except Exception as error:  # 원천·네트워크 예외를 같은 재시도 정책으로 관리
                last_error = error
                if attempt < retries:
                    sleep_fn(sleep_seconds * attempt)
        if last_error is not None:
            raise RuntimeError(
                f"증권플러스 VKOSPI 조회 실패 · to={cursor} · {last_error}"
            ) from last_error
        if page.empty:
            break

        pages.append(page)
        oldest = pd.Timestamp(page["date"].min()).normalize()
        if oldest <= start or len(page) < page_limit:
            break
        cursor = oldest.date().isoformat()
        sleep_fn(sleep_seconds)

    if not pages:
        raise RuntimeError(f"증권플러스 VKOSPI 관측치가 없습니다: {start.date()}~{end.date()}")
    result = pd.concat(pages, ignore_index=True)
    result = result.loc[result["date"].between(start, end)]
    result = result.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if result.empty:
        raise RuntimeError(f"요청 기간 안에 VKOSPI 관측치가 없습니다: {start.date()}~{end.date()}")
    result.attrs.update(
        {
            "source": "증권플러스 공개 시세",
            "source_url": STOCKPLUS_PAGE_URL,
            "security_id": STOCKPLUS_SECURITY_ID,
            "page_count": len(pages),
        }
    )
    return result


def _load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "vkospi", "change_price", "change_rate", "change"])
    frame = pd.read_csv(path)
    required = {"date", "vkospi"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"기존 VKOSPI 파일 필수 컬럼 누락: {', '.join(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["vkospi"] = pd.to_numeric(frame["vkospi"], errors="coerce")
    return frame.dropna(subset=["date", "vkospi"])


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    serializable = frame.copy()
    serializable["date"] = pd.to_datetime(serializable["date"]).dt.strftime("%Y-%m-%d")
    serializable.to_csv(temporary, index=False)
    temporary.replace(path)


def _write_json_atomic(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def update_vkospi_data(
    output_path: str | Path,
    *,
    metadata_path: str | Path | None = None,
    start_date: str | datetime | pd.Timestamp = DEFAULT_START_DATE,
    end_date: str | datetime | pd.Timestamp | None = None,
    overlap_days: int = 10,
    retries: int = 3,
    sleep_seconds: float = 0.15,
    page_fetcher: StockplusPageFetcher | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """기존 VKOSPI 캐시를 보존하면서 필요한 최근 구간만 중첩 조회해 갱신합니다."""

    output = Path(output_path)
    metadata = Path(metadata_path) if metadata_path else output.with_suffix(".metadata.json")
    requested_start = _timestamp(start_date, "시작")
    requested_end = _timestamp(
        end_date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "종료",
    )
    existing = _load_existing(output)
    fetch_start = requested_start
    fetch_mode = "bootstrap"
    if not existing.empty:
        fetch_start = max(requested_start, existing["date"].max() - pd.Timedelta(days=overlap_days))
        fetch_mode = "incremental"

    source_error: str | None = None
    try:
        fetched = fetch_stockplus_vkospi(
            fetch_start,
            requested_end,
            retries=retries,
            sleep_seconds=sleep_seconds,
            page_fetcher=page_fetcher,
            sleep_fn=sleep_fn,
        )
    except Exception as error:
        if existing.empty:
            raise
        fetched = pd.DataFrame(columns=existing.columns)
        source_error = str(error)
        fetch_mode = "cache-fallback"
        LOGGER.warning("VKOSPI 조회 실패 · 직전 캐시 보존 · %s", error)

    frames = [frame for frame in [existing, fetched] if not frame.empty]
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    combined["vkospi"] = pd.to_numeric(combined["vkospi"], errors="coerce")
    combined = combined.dropna(subset=["date", "vkospi"])
    combined = combined.loc[combined["date"].between(requested_start, requested_end)]
    combined = combined.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if combined.empty:
        raise RuntimeError("저장할 VKOSPI 관측치가 없습니다.")
    if (combined["vkospi"] <= 0).any() or not combined["date"].is_unique:
        raise ValueError("VKOSPI 값 또는 날짜 중복 품질검사에 실패했습니다.")

    now = datetime.now(ZoneInfo("Asia/Seoul"))
    latest_date = pd.Timestamp(combined["date"].max())
    age_days = max(0, (requested_end - latest_date).days)
    value_status = (
        "provisional"
        if latest_date.date() == now.date() and now.strftime("%H:%M") < "16:00"
        else "eod"
    )
    quality_status = "warning" if source_error or age_days > 7 else "ok"
    quality_notes = []
    if source_error:
        quality_notes.append("원천 조회 실패 · 직전 검증 캐시 사용")
    if age_days > 7:
        quality_notes.append(f"요청 종료일 대비 최근 관측일 {age_days}일 지연")

    _write_csv_atomic(combined, output)
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": {
            "provider": "Stockplus",
            "label": "증권플러스 공개 시세",
            "securityId": STOCKPLUS_SECURITY_ID,
            "pageUrl": STOCKPLUS_PAGE_URL,
            "apiUrl": STOCKPLUS_API_URL,
            "frequency": "daily",
            "role": "KRX 지수 재배포 시세",
        },
        "period": {
            "startDate": combined["date"].min().date().isoformat(),
            "endDate": latest_date.date().isoformat(),
            "observations": int(len(combined)),
        },
        "update": {
            "mode": fetch_mode,
            "requestedStartDate": fetch_start.date().isoformat(),
            "requestedEndDate": requested_end.date().isoformat(),
            "fetchedRows": int(len(fetched)),
            "overlapDays": overlap_days,
        },
        "quality": {
            "status": quality_status,
            "latestValueStatus": value_status,
            "calendarAgeDays": age_days,
            "sourceError": source_error,
            "notes": quality_notes,
        },
        "latest": {
            "date": latest_date.date().isoformat(),
            "value": round(float(combined.iloc[-1]["vkospi"]), 4),
            "changePrice": (
                None
                if pd.isna(combined.iloc[-1].get("change_price"))
                else round(float(combined.iloc[-1]["change_price"]), 4)
            ),
            "changePct": (
                None
                if pd.isna(combined.iloc[-1].get("change_rate"))
                else round(float(combined.iloc[-1]["change_rate"]) * 100, 4)
            ),
        },
    }
    _write_json_atomic(payload, metadata)
    combined.attrs["metadata"] = payload
    return combined
