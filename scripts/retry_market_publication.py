"""실패한 게시 후보를 보관하고, 재계산 없이 검증·게시를 재시도합니다."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from publication_delivery import git, publication_files, verify_remote_publication, verify_staged_publication
    from prepare_atomic_publication import MANIFEST_PATH, PublicationError, verify_publication
except ModuleNotFoundError:
    from scripts.publication_delivery import git, publication_files, verify_remote_publication, verify_staged_publication
    from scripts.prepare_atomic_publication import MANIFEST_PATH, PublicationError, verify_publication


KST = timezone(timedelta(hours=9))


def save_recovery_record(
    root: Path, runtime_root: Path, *, run_id: str, source_commit: str,
    stage: str, remote: str, branch: str, scheduled_time: str, started_at: str,
) -> Path:
    """비밀정보 없이 실패 후보 경로와 실행 정보를 별도 원장에 보관합니다."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise PublicationError("복구 실행번호 형식이 올바르지 않습니다.")
    path = runtime_root / "logs" / "publication-recovery" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "runId": run_id, "worktree": str(root.resolve()), "runtimeRoot": str(runtime_root.resolve()),
        "sourceCommit": source_commit, "stage": stage, "remote": remote, "branch": branch,
        "scheduledTime": scheduled_time, "startedAt": started_at,
        "savedAt": datetime.now(KST).isoformat(), "status": "pending",
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def validate_recovery_record(record: dict) -> tuple[Path, dict]:
    """원래 실행번호와 계산 코드가 보존된 봉인본만 재사용합니다."""
    root = Path(record["worktree"]).resolve()
    if not root.is_dir():
        raise PublicationError("보관된 작업폴더가 없습니다. 전체 갱신이 필요합니다.")
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise PublicationError("복구 후보가 독립 작업폴더가 아닙니다.")
    manifest = verify_publication(root, expected_run_id=record["runId"])
    allowed = set(publication_files(root))
    changed = git(root, "diff", "--name-only", "-z", record["sourceCommit"]).decode().split("\0")
    if any(path and path not in allowed for path in changed):
        raise PublicationError("계산 시작 후 게시 파일 외의 코드가 변경돼 재계산이 필요합니다.")
    return root, manifest


def retry_publication(
    record_path: Path, *, publish: bool = False,
    url: str = "https://ymh1989.github.io/market-risk-dashboard",
    attempts: int = 12, interval: float = 10,
) -> dict:
    """명시적 게시 요청일 때만 커밋·푸시하며 최신 데이터 덮어쓰기를 차단합니다."""
    if attempts < 1 or interval < 0:
        raise PublicationError("게시 확인 횟수는 1 이상, 대기시간은 0 이상이어야 합니다.")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    root, manifest = validate_recovery_record(record)
    if not publish:
        return manifest
    lock = Path(record["runtimeRoot"]) / "logs" / ".local-market-update.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise PublicationError("정기 갱신이 실행 중입니다. 종료 후 게시를 재시도하세요.") from None
    try:
        remote, branch = record["remote"], record["branch"]
        git(root, "fetch", remote, branch)
        target = git(root, "rev-parse", f"{remote}/{branch}").decode().strip()
        try:
            remote_manifest = json.loads(git(root, "show", f"{target}:{MANIFEST_PATH}"))
        except (PublicationError, ValueError):
            remote_manifest = None
        if remote_manifest != manifest:
            if target != record["sourceCommit"]:
                raise PublicationError("원격 저장소가 계산 시작 이후 변경됐습니다. 이전 후보를 덮어 게시하지 않습니다. 전체 갱신이 필요합니다.")
            git(root, "add", "--", *publication_files(root))
            verify_staged_publication(root)
            if git(root, "diff", "--cached", "--name-only"):
                git(root, "-c", "user.name=local-market-risk-bot", "-c",
                    "user.email=local-market-risk-bot@users.noreply.github.com",
                    "commit", "-m", "Recover verified market publication")
            git(root, "push", remote, f"HEAD:{branch}")
        last_error = None
        for attempt in range(attempts):
            try:
                verify_remote_publication(root, url)
                last_error = None
                break
            except PublicationError as error:
                last_error = error
                if attempt + 1 < attempts:
                    time.sleep(interval)
        if last_error is not None:
            raise last_error
        record["status"] = "recovered"
        record["recoveredAt"] = datetime.now(KST).isoformat()
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        scheduled = record.get("scheduledTime", "")
        if re.fullmatch(r"\d{2}:\d{2}", scheduled):
            day = datetime.fromisoformat(record["startedAt"][:10]).date().isoformat()
            state = Path(record["runtimeRoot"]) / "logs" / "local-market-update-state"
            state.mkdir(parents=True, exist_ok=True)
            (state / f"{day}-{scheduled}.done").write_text(
                f"{record['recoveredAt']} · 게시 복구 · {record['runId']}\n", encoding="utf-8"
            )
        return manifest
    finally:
        lock.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description="실패 게시 후보 보관·검증·재시도")
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save")
    save.add_argument("--root", type=Path, required=True)
    save.add_argument("--runtime-root", type=Path, required=True)
    for name in ("run-id", "source-commit", "stage", "remote", "branch", "started-at"):
        save.add_argument(f"--{name}", required=True)
    save.add_argument("--scheduled-time", default="")
    retry = commands.add_parser("retry")
    retry.add_argument("record", type=Path)
    retry.add_argument("--publish", action="store_true", help="검증 후 실제 커밋·푸시까지 실행")
    retry.add_argument("--url", default="https://ymh1989.github.io/market-risk-dashboard")
    args = parser.parse_args()
    try:
        if args.command == "save":
            values = vars(args).copy()
            values.pop("command")
            print(save_recovery_record(**values))
        else:
            result = retry_publication(args.record, publish=args.publish, url=args.url)
            action = "게시 복구 완료" if args.publish else "게시 후보 검증 완료 (실제 게시 없음)"
            print(f"{action}: {result['runId']}")
    except (PublicationError, OSError, ValueError, KeyError) as error:
        raise SystemExit(f"게시 복구 실패: {error}") from None


if __name__ == "__main__":
    main()
