from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.prepare_atomic_publication import (
    PublicationError,
    prepare_publication,
    verify_publication,
)


KST = timezone(timedelta(hours=9))
RUN_ID = "20260819T123000-1230-fast"
STARTED_AT = "2026-08-19 12:30:00 KST"
PREPARED_AT = datetime(2026, 8, 19, 12, 35, tzinfo=KST)
ARTIFACTS = (
    Path("data/pipeline-status.json"),
    Path("data/data-quality.json"),
    Path("data/risk-dashboard.json"),
    Path("data/market-stress-episodes.json"),
    Path("reports/market-risk-dashboard-offline.html"),
)


def write_json(root: Path, relative: Path, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_publication_candidate(root: Path) -> None:
    write_json(
        root,
        Path("data/pipeline-status.json"),
        {
            "generatedAt": "2026-08-19 12:34:00 KST",
            "current": {"runId": RUN_ID, "status": "success"},
        },
    )
    write_json(
        root,
        Path("data/data-quality.json"),
        {"generatedAt": "2026-08-19 12:33:00 KST", "status": "ok"},
    )
    write_json(
        root,
        Path("data/risk-dashboard.json"),
        {
            "metadata": {
                "generatedAt": "2026-08-19 12:32 KST",
                "asOf": "2026-08-19",
            },
            "sections": [],
        },
    )
    write_json(
        root,
        Path("data/market-stress-episodes.json"),
        {
            "generatedAt": "2026-08-18 15:36:00 KST",
            "sampleEnd": "2026-08-18",
            "episodes": [],
        },
    )
    offline = root / "reports/market-risk-dashboard-offline.html"
    offline.parent.mkdir(parents=True, exist_ok=True)
    offline.write_text("<!doctype html><title>snapshot</title>", encoding="utf-8")


def test_prepare_publication_stamps_and_verifies_one_run(tmp_path):
    make_publication_candidate(tmp_path)

    manifest = prepare_publication(
        tmp_path,
        run_id=RUN_ID,
        mode="fast",
        started_at=STARTED_AT,
        artifact_paths=ARTIFACTS,
        reused_paths=[Path("data/market-stress-episodes.json")],
        prepared_at=PREPARED_AT,
    )

    assert manifest["status"] == "ready"
    assert manifest["artifactCount"] == len(ARTIFACTS)
    assert manifest["generatedCount"] == 4
    assert manifest["reusedCount"] == 1
    assert all(manifest["checks"].values())
    assert verify_publication(tmp_path, expected_run_id=RUN_ID) == manifest

    for relative in ARTIFACTS:
        if relative.suffix != ".json":
            continue
        payload = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
        assert payload["publication"]["runId"] == RUN_ID
    stress = json.loads(
        (tmp_path / "data/market-stress-episodes.json").read_text(encoding="utf-8")
    )
    assert stress["publication"]["state"] == "reused"
    dashboard_text = (tmp_path / "data/risk-dashboard.json").read_text(encoding="utf-8")
    assert "\n" not in dashboard_text


def test_prepare_publication_accepts_live_mode_with_reused_eod_artifacts(tmp_path):
    make_publication_candidate(tmp_path)

    manifest = prepare_publication(
        tmp_path,
        run_id=RUN_ID,
        mode="live",
        started_at=STARTED_AT,
        artifact_paths=ARTIFACTS,
        reused_paths=[Path("data/market-stress-episodes.json")],
        prepared_at=PREPARED_AT,
    )

    assert manifest["mode"] == "live"
    assert manifest["reusedCount"] == 1
    assert verify_publication(tmp_path, expected_run_id=RUN_ID)["status"] == "ready"


def test_prepare_failure_does_not_touch_existing_files(tmp_path):
    make_publication_candidate(tmp_path)
    dashboard_path = tmp_path / "data/risk-dashboard.json"
    dashboard_before = dashboard_path.read_bytes()
    missing_artifacts = ARTIFACTS + (Path("data/missing.json"),)

    with pytest.raises(PublicationError, match="필수 게시 파일"):
        prepare_publication(
            tmp_path,
            run_id=RUN_ID,
            mode="fast",
            started_at=STARTED_AT,
            artifact_paths=missing_artifacts,
            reused_paths=[Path("data/market-stress-episodes.json")],
            prepared_at=PREPARED_AT,
        )

    assert dashboard_path.read_bytes() == dashboard_before
    assert not (tmp_path / "data/publication-manifest.json").exists()


def test_prepare_rejects_stale_file_not_declared_as_reused(tmp_path):
    make_publication_candidate(tmp_path)

    with pytest.raises(PublicationError, match="신규 산출물이 아닙니다"):
        prepare_publication(
            tmp_path,
            run_id=RUN_ID,
            mode="fast",
            started_at=STARTED_AT,
            artifact_paths=ARTIFACTS,
            prepared_at=PREPARED_AT,
        )


def test_verify_detects_file_changed_after_manifest(tmp_path):
    make_publication_candidate(tmp_path)
    prepare_publication(
        tmp_path,
        run_id=RUN_ID,
        mode="fast",
        started_at=STARTED_AT,
        artifact_paths=ARTIFACTS,
        reused_paths=[Path("data/market-stress-episodes.json")],
        prepared_at=PREPARED_AT,
    )
    dashboard_path = tmp_path / "data/risk-dashboard.json"
    dashboard_path.write_text(
        dashboard_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="크기가 manifest와 다릅니다"):
        verify_publication(tmp_path, expected_run_id=RUN_ID)


def test_verify_rejects_artifact_path_outside_project(tmp_path):
    make_publication_candidate(tmp_path)
    prepare_publication(
        tmp_path,
        run_id=RUN_ID,
        mode="fast",
        started_at=STARTED_AT,
        artifact_paths=ARTIFACTS,
        reused_paths=[Path("data/market-stress-episodes.json")],
        prepared_at=PREPARED_AT,
    )
    manifest_path = tmp_path / "data/publication-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PublicationError, match="프로젝트 내부 상대경로"):
        verify_publication(tmp_path, expected_run_id=RUN_ID)
