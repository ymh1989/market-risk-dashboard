from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CachedHistory:
    frame: pd.DataFrame
    metadata: dict
    data_path: Path
    metadata_path: Path


def _safe_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
    if not key:
        raise ValueError("시계열 캐시 키가 비어 있습니다.")
    return key


def cache_paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    """지수별 CSV와 메타데이터 파일 경로를 반환한다."""

    safe_key = _safe_key(key)
    return cache_dir / f"{safe_key}.csv", cache_dir / f"{safe_key}.metadata.json"


def normalize_history(frame: pd.DataFrame | None) -> pd.DataFrame:
    """날짜·종가 열만 정렬하고 중복과 비정상 값을 제거한다."""

    if frame is None or frame.empty or not {"date", "close"}.issubset(frame.columns):
        return pd.DataFrame(columns=["date", "close"])
    normalized = frame[["date", "close"]].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.dropna(subset=["date", "close"])
    normalized = normalized.loc[normalized["close"] > 0]
    normalized["date"] = normalized["date"].dt.date.astype(str)
    return (
        normalized.drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def merge_histories(*frames: pd.DataFrame | None) -> pd.DataFrame:
    """기존 캐시와 신규 조회값을 날짜 기준으로 병합한다."""

    valid = [normalize_history(frame) for frame in frames]
    valid = [frame for frame in valid if not frame.empty]
    if not valid:
        return normalize_history(None)
    return normalize_history(pd.concat(valid, ignore_index=True))


def load_history(cache_dir: Path, key: str) -> CachedHistory:
    """저장된 장기 시계열과 적재 메타데이터를 읽는다."""

    data_path, metadata_path = cache_paths(cache_dir, key)
    frame = normalize_history(None)
    metadata = {}
    if data_path.exists():
        try:
            frame = normalize_history(pd.read_csv(data_path))
        except (OSError, ValueError, pd.errors.ParserError):
            frame = normalize_history(None)
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
    return CachedHistory(frame, metadata, data_path, metadata_path)


def incremental_start_date(frame: pd.DataFrame, overlap_days: int = 10) -> date | None:
    """수정 공시를 다시 덮어쓸 수 있도록 마지막 날짜보다 조금 앞에서 재조회한다."""

    normalized = normalize_history(frame)
    if normalized.empty:
        return None
    return date.fromisoformat(normalized.iloc[-1]["date"]) - timedelta(days=overlap_days)


def cache_is_fresh(metadata: dict, max_age_minutes: int, now: datetime | None = None) -> bool:
    """같은 배치 안에서 이미 갱신한 캐시인지 확인한다."""

    fetched_at = metadata.get("fetchedAt")
    if not fetched_at:
        return False
    try:
        timestamp = datetime.fromisoformat(str(fetched_at))
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=KST)
    current = (now or datetime.now(KST)).astimezone(KST)
    age = current - timestamp.astimezone(KST)
    return timedelta(0) <= age <= timedelta(minutes=max_age_minutes)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def save_history(
    cache_dir: Path,
    key: str,
    frame: pd.DataFrame,
    *,
    source: str,
    fetch_mode: str,
    fetched_rows: int,
    overlap_days: int,
    fetched_at: datetime | None = None,
) -> CachedHistory:
    """검증한 장기 시계열과 적재 이력을 원자적으로 저장한다."""

    normalized = normalize_history(frame)
    if normalized.empty:
        raise ValueError(f"{key}: 비어 있는 장기 시계열은 저장할 수 없습니다.")
    data_path, metadata_path = cache_paths(cache_dir, key)
    metadata = {
        "schemaVersion": SCHEMA_VERSION,
        "key": _safe_key(key),
        "source": source,
        "fetchMode": fetch_mode,
        "fetchedAt": (fetched_at or datetime.now(KST)).astimezone(KST).isoformat(timespec="seconds"),
        "firstDate": normalized.iloc[0]["date"],
        "lastDate": normalized.iloc[-1]["date"],
        "observations": int(len(normalized)),
        "fetchedRows": int(fetched_rows),
        "overlapDays": int(overlap_days),
    }
    _atomic_write_text(data_path, normalized.to_csv(index=False))
    _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
    return CachedHistory(normalized, metadata, data_path, metadata_path)
