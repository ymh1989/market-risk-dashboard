from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.overnight_market_prepare import (
    OvernightCandidateError,
    audit_market_sources,
    overnight_schedule,
    publish_candidate,
    seal_candidate,
    verify_candidate,
)
from scripts.send_operations_alert import format_alert


KST = ZoneInfo("Asia/Seoul")
SOURCE_COMMIT = "a" * 40


def test_overnight_schedule_uses_new_york_dst_and_skips_monday():
    summer = overnight_schedule(datetime(2026, 8, 19, 5, 40, tzinfo=KST))
    winter = overnight_schedule(datetime(2026, 1, 14, 6, 40, tzinfo=KST))
    monday = overnight_schedule(datetime(2026, 8, 17, 5, 40, tzinfo=KST))

    assert summer["slot"] == "05:40"
    assert summer["season"] == "summer"
    assert summer["usMarketDate"] == "2026-08-18"
    assert winter["slot"] == "06:40"
    assert winter["season"] == "standard"
    assert monday["eligible"] is False


def make_market_files(root: Path, rows: int = 20) -> tuple[Path, Path]:
    dates = pd.bdate_range("2026-07-23", periods=rows)
    frame = pd.DataFrame(
        {
            "date": dates,
            "KOSPI": range(3000, 3000 + rows),
            "SPX": range(6000, 6000 + rows),
            "SOX": range(5000, 5000 + rows),
            "USDKRW": range(1300, 1300 + rows),
            "VIX": range(20, 20 + rows),
            "NASDAQ": range(20000, 20000 + rows),
            "US10Y": [4.0 + index / 100 for index in range(rows)],
        }
    )
    data_path = root / "market_data.csv"
    metadata_path = root / "market_data_sources.json"
    root.mkdir(parents=True, exist_ok=True)
    frame.to_csv(data_path, index=False)
    last_date = dates[-1].date().isoformat()
    sources = []
    for column in ["KOSPI", "SPX", "SOX", "USDKRW", "VIX", "NASDAQ", "US10Y"]:
        sources.append(
            {
                "column": column,
                "provider": "test",
                "symbol": column,
                "required": column in {"KOSPI", "SPX", "SOX", "USDKRW"},
                "status": "ok",
                "lastDate": last_date,
            }
        )
    metadata_path.write_text(
        json.dumps(
            {
                "requiredColumns": ["date", "KOSPI", "SPX", "SOX", "USDKRW"],
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return data_path, metadata_path


def test_source_audit_checks_freshness_and_historical_revisions(tmp_path):
    previous_data, metadata = make_market_files(tmp_path / "previous")
    current_data, current_metadata = make_market_files(tmp_path / "current")
    current = pd.read_csv(current_data)
    current.loc[0, "SPX"] += 1
    current.to_csv(current_data, index=False)

    payload = audit_market_sources(
        current_data,
        current_metadata,
        previous_data=previous_data,
        expected_us_market_date="2026-08-19",
        minimum_rows=10,
        now=datetime(2026, 8, 20, 5, 35, tzinfo=KST),
    )

    assert payload["status"] == "ok"
    assert payload["historicalRevision"]["changedCells"] == 1
    assert payload["summary"]["error"] == 0


def test_source_audit_fails_when_required_source_fails(tmp_path):
    data_path, metadata_path = make_market_files(tmp_path)
    metadata = json.loads(metadata_path.read_text())
    next(item for item in metadata["sources"] if item["column"] == "SPX")["status"] = "failed"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    payload = audit_market_sources(
        data_path,
        metadata_path,
        expected_us_market_date="2026-08-19",
        minimum_rows=10,
    )

    assert payload["status"] == "error"
    assert any(item["id"] == "required-sources" and item["status"] == "error" for item in payload["checks"])


def make_candidate(root: Path) -> None:
    files = {
        "raw/market_data.csv": b"date,KOSPI\n2026-08-19,3000\n",
        "raw/market_data_sources.json": b'{"status":"ok"}',
        "processed/features.parquet": b"features",
        "models/model_bundle.joblib": b"model",
        "dashboard/market-history-cache.json": b'{"status":"ok"}',
        "dashboard/naver-marketindex-history.json": b'{"status":"ok"}',
        "dashboard/els-index-risk.json": b'{"status":"ok"}',
        "dashboard/hmm-regime.json": b'{"status":"ok"}',
        "dashboard/ml-risk-signal.json": b'{"status":"ok"}',
        "dashboard/data-quality.json": b'{"status":"ok"}',
        "quality/overnight-source-quality.json": b'{"status":"ok"}',
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_candidate_seal_verify_publish_and_tamper_detection(tmp_path):
    staging = tmp_path / "staging"
    current = tmp_path / "current"
    make_candidate(staging)
    seal_candidate(
        staging,
        source_commit=SOURCE_COMMIT,
        slot="05:40",
        us_market_date="2026-08-18",
        started_at="2026-08-19 05:40:00 KST",
        prepared_at=datetime(2026, 8, 19, 5, 40, tzinfo=KST),
    )

    manifest = publish_candidate(staging, current)
    assert manifest["status"] == "ready"
    assert verify_candidate(
        current,
        expected_commit=SOURCE_COMMIT,
        max_age_hours=4,
        now=datetime(2026, 8, 19, 7, 30, tzinfo=KST),
    )["marketDataSha256"]

    (current / "models/model_bundle.joblib").write_bytes(b"changed")
    with pytest.raises(OvernightCandidateError, match="크기|체크섬"):
        verify_candidate(current)


def test_operations_alert_is_short_and_codex_ready(tmp_path):
    log_file = tmp_path / "overnight.log"
    log_file.write_text("수집 시작\nHTTP 503\n필수 원천 SPX 실패\n", encoding="utf-8")

    message = format_alert(
        job="미국장 EOD 사전준비",
        stage="원천 품질·과거값 대조",
        exit_code=1,
        command="python audit.py",
        log_file=str(log_file),
        occurred_at=datetime(2026, 8, 19, 5, 40, tzinfo=KST),
    )

    assert "Codex 붙여넣기" in message
    assert "필수 원천 SPX 실패" in message
    assert "단계=원천 품질·과거값 대조" in message
    assert len(message) < 3000
