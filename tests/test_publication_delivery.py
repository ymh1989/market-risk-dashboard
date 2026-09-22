from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from scripts import publication_delivery as delivery
from scripts import retry_market_publication as recovery
from scripts.prepare_atomic_publication import PublicationError, prepare_publication


RUN_ID = "20260923T153500-1535-full"
STARTED = "2026-09-23 15:35:00 KST"


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    delivery.git(root, "init", "-b", "main")
    delivery.git(root, "config", "user.name", "test")
    delivery.git(root, "config", "user.email", "test@example.invalid")
    (root / "src").mkdir()
    (root / "src/app.js").write_text("original\n", encoding="utf-8")
    delivery.git(root, "add", ".")
    delivery.git(root, "commit", "-m", "initial")
    artifacts = (Path("data/dram-spot-prices.json"), Path("data/pipeline-status.json"), Path("reports/offline.html"))
    (root / "data").mkdir()
    for path in artifacts[:2]:
        (root / path).write_text(json.dumps({
            "generatedAt": "2026-09-23 15:36:00 KST",
            "current": {"runId": RUN_ID, "status": "success"},
            "value": 10,
        }), encoding="utf-8")
    (root / "reports").mkdir()
    (root / artifacts[-1]).write_text("<!doctype html><title>검증</title>", encoding="utf-8")
    prepare_publication(
        root, run_id=RUN_ID, mode="full", started_at=STARTED,
        artifact_paths=artifacts, prepared_at=datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc),
    )
    return root


def mock_remote(monkeypatch, root, replacements=None):
    replacements = replacements or {}

    def fetch(url, timeout, byte_limit):
        path = urlsplit(url).path.removeprefix("/dashboard/")
        return replacements.get(path, (root / path).read_bytes())

    monkeypatch.setattr(delivery, "fetch_publication_bytes", fetch)


def test_staging_uses_manifest_and_catches_omitted_dram(candidate):
    paths = delivery.publication_files(candidate)
    assert "data/dram-spot-prices.json" in paths
    assert "data/publication-manifest.json" in paths
    delivery.git(candidate, "add", "--", *(path for path in paths if "dram-spot" not in path))
    with pytest.raises(PublicationError):
        delivery.verify_staged_publication(candidate)
    delivery.git(candidate, "add", "--", *paths)
    assert delivery.verify_staged_publication(candidate)["runId"] == RUN_ID


def test_staging_rejects_old_index_even_when_worktree_is_correct(candidate):
    path = candidate / "data/dram-spot-prices.json"
    correct = path.read_bytes()
    path.write_bytes(correct.replace(b'"value": 10', b'"value": 11'))
    delivery.git(candidate, "add", "data", "reports")
    path.write_bytes(correct)
    with pytest.raises(PublicationError, match="체크섬"):
        delivery.verify_staged_publication(candidate)


def test_remote_requires_all_files_not_just_run_id(candidate, monkeypatch):
    mock_remote(monkeypatch, candidate)
    assert delivery.verify_remote_publication(candidate, "https://example.invalid/dashboard")["runId"] == RUN_ID
    relative = "data/dram-spot-prices.json"
    stale = (candidate / relative).read_bytes().replace(b'"value": 10', b'"value": 11')
    mock_remote(monkeypatch, candidate, {relative: stale})
    with pytest.raises(PublicationError, match="체크섬.*dram"):
        delivery.verify_remote_publication(candidate, "https://example.invalid/dashboard")


def test_remote_rejects_old_manifest_and_unreachable_files(candidate, monkeypatch):
    mock_remote(monkeypatch, candidate, {"data/publication-manifest.json": b'{"runId":"old"}'})
    with pytest.raises(PublicationError, match="manifest"):
        delivery.verify_remote_publication(candidate, "https://example.invalid/dashboard")

    def unavailable(*args):
        raise TimeoutError("연결 지연")

    monkeypatch.setattr(delivery, "fetch_publication_bytes", unavailable)
    with pytest.raises(PublicationError, match="연결 지연"):
        delivery.verify_remote_publication(candidate, "https://example.invalid/dashboard")


def test_rebase_allows_ui_but_blocks_calculation_or_newer_data(candidate):
    base = delivery.git(candidate, "rev-parse", "HEAD").decode().strip()
    (candidate / "src/app.js").write_text("new UI\n", encoding="utf-8")
    delivery.git(candidate, "add", "src/app.js")
    delivery.git(candidate, "commit", "-m", "UI only")
    assert delivery.check_rebase_safety(candidate, base, "HEAD") == ["src/app.js"]
    delivery.git(candidate, "add", "data", "reports")
    delivery.git(candidate, "commit", "-m", "newer data")
    with pytest.raises(PublicationError, match="최신본 재계산"):
        delivery.check_rebase_safety(candidate, base, "HEAD")


@pytest.mark.parametrize("path", ["configs/base.yaml", "scripts/update_market_risk.py", "src/kospi_risk/models.py"])
def test_rebase_rejects_calculation_changes(candidate, path):
    base = delivery.git(candidate, "rev-parse", "HEAD").decode().strip()
    file = candidate / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("changed", encoding="utf-8")
    delivery.git(candidate, "add", path)
    delivery.git(candidate, "commit", "-m", "calculation change")
    with pytest.raises(PublicationError, match="최신본 재계산"):
        delivery.check_rebase_safety(candidate, base, "HEAD")


def make_record(candidate, tmp_path):
    runtime = tmp_path / "runtime"
    return recovery.save_recovery_record(
        candidate, runtime, run_id=RUN_ID,
        source_commit=delivery.git(candidate, "rev-parse", "HEAD").decode().strip(),
        stage="GitHub Pages 확인", remote="origin", branch="main", scheduled_time="15:35", started_at=STARTED,
    )


def test_recovery_default_is_read_only_and_checks_seal(candidate, tmp_path):
    record = make_record(candidate, tmp_path)
    before = delivery.git(candidate, "rev-parse", "HEAD")
    assert recovery.retry_publication(record)["runId"] == RUN_ID
    assert delivery.git(candidate, "rev-parse", "HEAD") == before
    with (candidate / "data/dram-spot-prices.json").open("a") as handle:
        handle.write(" ")
    with pytest.raises(PublicationError, match="크기"):
        recovery.retry_publication(record)


def test_recovery_refuses_modified_calculation_code(candidate, tmp_path):
    record = make_record(candidate, tmp_path)
    (candidate / "src/app.js").write_text("changed", encoding="utf-8")
    with pytest.raises(PublicationError, match="코드가 변경"):
        recovery.retry_publication(record)


def test_recovery_publishes_to_local_remote_and_marks_done(candidate, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    delivery.git(remote, "init", "--bare")
    delivery.git(candidate, "remote", "add", "origin", str(remote))
    delivery.git(candidate, "push", "origin", "HEAD:main")
    record = make_record(candidate, tmp_path)
    checks = []
    monkeypatch.setattr(recovery, "verify_remote_publication", lambda root, url: checks.append(root))
    recovery.retry_publication(record, publish=True, attempts=1)
    assert checks == [candidate]
    assert json.loads(record.read_text())["status"] == "recovered"
    assert (tmp_path / "runtime/logs/local-market-update-state/2026-09-23-15:35.done").exists()
    assert not (tmp_path / "runtime/logs/.local-market-update.lock").exists()
    manifest = json.loads(delivery.git(remote, "show", "main:data/publication-manifest.json"))
    assert manifest["runId"] == RUN_ID


def test_recovery_refuses_newer_remote(candidate, tmp_path):
    record = make_record(candidate, tmp_path)
    remote = tmp_path / "remote.git"
    remote.mkdir()
    delivery.git(remote, "init", "--bare")
    delivery.git(candidate, "remote", "add", "origin", str(remote))
    delivery.git(candidate, "commit", "--allow-empty", "-m", "newer remote")
    delivery.git(candidate, "push", "origin", "HEAD:main")
    with pytest.raises(PublicationError, match="변경"):
        recovery.retry_publication(record, publish=True, attempts=1)
    assert not (tmp_path / "runtime/logs/.local-market-update.lock").exists()


def test_recovery_after_push_failure_waits_for_verified_pages(candidate, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    delivery.git(remote, "init", "--bare")
    delivery.git(candidate, "remote", "add", "origin", str(remote))
    delivery.git(candidate, "push", "origin", "HEAD:main")
    record = make_record(candidate, tmp_path)

    def incomplete(root, url):
        raise PublicationError("홈페이지 파일 일부가 아직 이전 버전")

    monkeypatch.setattr(recovery, "verify_remote_publication", incomplete)
    with pytest.raises(PublicationError, match="이전 버전"):
        recovery.retry_publication(record, publish=True, attempts=1)
    pushed_head = delivery.git(candidate, "rev-parse", "HEAD")
    done = tmp_path / "runtime/logs/local-market-update-state/2026-09-23-15:35.done"
    assert not done.exists()
    assert json.loads(record.read_text())["status"] == "pending"
    monkeypatch.setattr(recovery, "verify_remote_publication", lambda root, url: {})
    recovery.retry_publication(record, publish=True, attempts=1)
    assert delivery.git(candidate, "rev-parse", "HEAD") == pushed_head
    assert done.exists()


def test_recovery_never_bypasses_scheduled_update_lock(candidate, tmp_path):
    record = make_record(candidate, tmp_path)
    lock = tmp_path / "runtime/logs/.local-market-update.lock"
    lock.mkdir()
    with pytest.raises(PublicationError, match="정기 갱신이 실행 중"):
        recovery.retry_publication(record, publish=True, attempts=1)
    assert lock.exists()


def test_workflow_refreshes_dram_before_sealing_and_uses_shared_publisher():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/update-market-risk.yml").read_text())
    steps = workflow["jobs"]["update-market-risk"]["steps"]
    refresh = next(step["run"] for step in steps if step["name"] == "Refresh market risk data")
    assert refresh.index("update_dram_spot_prices.py") < refresh.index("make update-market-risk")
    commit = next(step["run"] for step in steps if step["name"] == "Commit refreshed data")
    assert "publication_delivery.py files" in commit
    assert commit.index("verify-staged") < commit.index("git commit")


def test_workflow_requests_pages_build_after_token_push_before_verification():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/update-market-risk.yml").read_text())
    steps = workflow["jobs"]["update-market-risk"]["steps"]
    names = [step["name"] for step in steps]
    assert names.index("Commit refreshed data") < names.index("Request Pages build")
    assert names.index("Request Pages build") < names.index("Verify published snapshot")
    build = steps[names.index("Request Pages build")]
    assert workflow["permissions"]["pages"] == "write"
    assert build["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert '--method POST "repos/${GITHUB_REPOSITORY}/pages/builds"' in build["run"]
    verify = steps[names.index("Verify published snapshot")]
    assert "publication_delivery.py verify-remote" in verify["run"]
    assert "seq 1 60" in verify["run"]
    assert verify["timeout-minutes"] == 15


@pytest.mark.parametrize("valid", [True, False])
def test_runtime_sync_checks_watchdog_before_replacing_files(tmp_path, valid):
    project = Path(__file__).resolve().parents[1]
    script = (project / "scripts/run_local_market_update.sh").read_text()
    upstream = tmp_path / "upstream"
    (upstream / "scripts").mkdir(parents=True)
    (upstream / "scripts/run_local_market_update.sh").write_text(script)
    monitor = "status = 'new'\n" if valid else "def (\n"
    (upstream / "scripts/monitor_local_market_update.py").write_text(monitor)
    (upstream / "scripts/send_operations_alert.py").write_text("status = 'new'\n")
    delivery.git(upstream, "init", "-b", "main")
    delivery.git(upstream, "config", "user.name", "test")
    delivery.git(upstream, "config", "user.email", "test@example.invalid")
    delivery.git(upstream, "add", ".")
    delivery.git(upstream, "commit", "-m", "runtime")
    runtime = tmp_path / "runtime"
    delivery.git(tmp_path, "clone", str(upstream), str(runtime))
    for name in ("monitor_local_market_update.py", "send_operations_alert.py"):
        (runtime / "scripts" / name).write_text("status = 'old'\n")
    function = "refresh_runtime_entrypoint() {" + script.split("refresh_runtime_entrypoint() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    result = subprocess.run(
        ["/bin/bash", "-c", "set -Eeuo pipefail\n" + function + "refresh_runtime_entrypoint"],
        env={**os.environ, "ROOT": str(runtime), "REMOTE": "origin", "BRANCH": "main",
             "RUNTIME_SELF_UPDATE": "1", "PYTHON_BIN": sys.executable},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == (0 if valid else 1), result.stderr
    expected = "status = 'new'\n" if valid else "status = 'old'\n"
    for name in ("monitor_local_market_update.py", "send_operations_alert.py"):
        assert (runtime / "scripts" / name).read_text() == expected
    assert (runtime / "scripts/run_local_market_update.sh").read_text() == script
