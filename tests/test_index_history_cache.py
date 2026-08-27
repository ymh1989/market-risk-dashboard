from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.index_history_cache import (
    cache_is_fresh,
    incremental_start_date,
    load_history,
    merge_histories,
    save_history,
)


KST = ZoneInfo("Asia/Seoul")


def _history(start: str, periods: int, first: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=periods, freq="B").strftime("%Y-%m-%d"),
            "close": np.linspace(first, first + periods - 1, periods),
        }
    )


def test_history_cache_is_sorted_deduplicated_and_atomic(tmp_path):
    base = _history("2024-01-02", 300)
    duplicate = pd.DataFrame([{"date": base.iloc[-1]["date"], "close": 999.0}])
    merged = merge_histories(base, duplicate)

    saved = save_history(
        tmp_path,
        "KOSPI200",
        merged,
        source="Naver KPI200",
        fetch_mode="bootstrap",
        fetched_rows=300,
        overlap_days=10,
        fetched_at=datetime(2026, 8, 27, 17, 0, tzinfo=KST),
    )
    loaded = load_history(tmp_path, "KOSPI200")

    assert saved.data_path.exists()
    assert loaded.frame.equals(saved.frame)
    assert loaded.frame.iloc[-1]["close"] == 999.0
    assert loaded.metadata["observations"] == 300
    assert loaded.metadata["firstDate"] == base.iloc[0]["date"]
    assert loaded.metadata["lastDate"] == base.iloc[-1]["date"]
    assert not list(tmp_path.glob("*.tmp"))


def test_incremental_start_rewinds_ten_calendar_days():
    frame = pd.DataFrame([{"date": "2026-08-27", "close": 100.0}])

    assert incremental_start_date(frame, overlap_days=10).isoformat() == "2026-08-17"


def test_cache_freshness_prevents_duplicate_fetch_in_same_batch():
    metadata = {"fetchedAt": "2026-08-27T17:00:00+09:00"}

    assert cache_is_fresh(metadata, 30, datetime(2026, 8, 27, 17, 29, tzinfo=KST))
    assert not cache_is_fresh(metadata, 30, datetime(2026, 8, 27, 17, 31, tzinfo=KST))
