"""게시 목록, Git 인덱스, 실제 배포 파일을 동일 manifest로 검증합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from prepare_atomic_publication import MANIFEST_PATH, PublicationError, verify_publication
except ModuleNotFoundError:
    from scripts.prepare_atomic_publication import MANIFEST_PATH, PublicationError, verify_publication


ROOT = Path(__file__).resolve().parents[1]
UI_ONLY_PATHS = {"index.html", "src/app.js", "src/styles.css", "README.md"}


def git(root: Path, *arguments: str) -> bytes:
    """인자를 셸로 재해석하지 않고 Git을 실행합니다."""
    try:
        return subprocess.run(
            ["git", "-C", str(root), *arguments], check=True, capture_output=True, timeout=120
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"") or b""
        raise PublicationError(f"Git 작업 실패 ({arguments[0]}): {detail.decode(errors='replace').strip()}") from error


def publication_files(root: Path) -> tuple[str, ...]:
    """검증된 manifest에 포함된 파일만 커밋 대상으로 반환합니다."""
    manifest = verify_publication(root)
    return tuple(item["path"] for item in manifest["artifacts"]) + (str(MANIFEST_PATH),)


def verify_staged_publication(root: Path) -> dict:
    """작업폴더가 아닌 실제 커밋 예정 파일의 누락·변조를 검사합니다."""
    expected = verify_publication(root)
    with tempfile.TemporaryDirectory(prefix="publication-index-") as temporary:
        staged_root = Path(temporary)
        for relative in publication_files(root):
            path = staged_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(git(root, "show", f":{relative}"))
        actual = verify_publication(staged_root, expected_run_id=expected["runId"])
        if actual != expected:
            raise PublicationError("Git 인덱스의 manifest가 검증된 게시 후보와 다릅니다.")
    return actual


def fetch_publication_bytes(url: str, timeout: float, byte_limit: int) -> bytes:
    """캐시 우회를 요청하고 응답 크기와 대기시간을 제한합니다."""
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(byte_limit + 1)
    if len(data) > byte_limit:
        raise PublicationError("게시 파일 응답이 예상 크기를 초과했습니다.")
    return data


def verify_remote_publication(root: Path, url: str, *, timeout: float = 15) -> dict:
    """실제 홈페이지의 모든 게시 파일을 로컬 봉인본과 대조합니다."""
    expected = verify_publication(root)
    prefix = url.rstrip("/") + "/"
    suffix = f"?publication_check={time.time_ns()}"
    try:
        remote = json.loads(fetch_publication_bytes(prefix + str(MANIFEST_PATH) + suffix, timeout, 1_000_000))
        if remote != expected:
            raise PublicationError("홈페이지 manifest가 이번 게시 후보와 다릅니다.")

        def check_artifact(artifact: dict) -> None:
            relative = artifact["path"]
            data = fetch_publication_bytes(prefix + relative + suffix, timeout, artifact["bytes"])
            if len(data) != artifact["bytes"] or hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                raise PublicationError(f"홈페이지 게시 파일 체크섬 불일치: {relative}")
            if relative.endswith(".json"):
                payload = json.loads(data)
                if (payload.get("publication") or {}).get("runId") != expected["runId"]:
                    raise PublicationError(f"홈페이지 게시 파일 실행번호 불일치: {relative}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(check_artifact, expected["artifacts"]))
    except PublicationError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise PublicationError(f"홈페이지 게시 검증 실패: {error}") from error
    return expected


def check_rebase_safety(root: Path, base: str, target: str) -> list[str]:
    """계산 코드나 데이터가 바뀐 경우 자동 병합을 차단합니다."""
    git(root, "merge-base", "--is-ancestor", base, target)
    changed = git(root, "diff", "--name-only", "--no-renames", "-z", base, target).decode().split("\0")
    changed = [path for path in changed if path]
    unsafe = [path for path in changed if path not in UI_ONLY_PATHS and not path.startswith(("docs/", "assets/"))]
    if unsafe:
        raise PublicationError(
            "계산 중 원격 코드·데이터가 변경돼 자동 게시를 중단합니다. 최신본 재계산 필요: "
            + ", ".join(unsafe[:8])
        )
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="게시 파일 목록과 실제 배포 결과를 검증합니다.")
    parser.add_argument("command", choices=["files", "verify-staged", "verify-remote", "check-rebase"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--url", default="https://ymh1989.github.io/market-risk-dashboard")
    parser.add_argument("--base", default="")
    parser.add_argument("--target", default="origin/main")
    args = parser.parse_args()
    try:
        if args.command == "files":
            print("\n".join(publication_files(args.root)))
        elif args.command == "check-rebase":
            if not args.base:
                raise PublicationError("계산 시작 커밋 --base가 필요합니다.")
            changed = check_rebase_safety(args.root, args.base, args.target)
            print(f"원격 변경 검증 완료: 화면·문서 변경 {len(changed)}개")
        else:
            manifest = (
                verify_staged_publication(args.root)
                if args.command == "verify-staged"
                else verify_remote_publication(args.root, args.url)
            )
            print(f"게시 검증 완료: {manifest['runId']} · {manifest['artifactCount']}개 파일")
    except PublicationError as error:
        raise SystemExit(f"게시 검증 실패: {error}") from None


if __name__ == "__main__":
    main()
