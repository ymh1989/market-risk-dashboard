"""개인 이슈 승인함을 단일 LaunchAgent로 설치합니다. 인증키는 plist에 넣지 않습니다."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

LABEL = "com.marketlab.issue-review"


def reload_agent(domain: str, target: Path) -> None:
    """기존 서비스 종료 직후 발생하는 일시적인 등록 지연만 제한적으로 재시도합니다."""
    subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for attempt in range(5):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True)
        if result.returncode == 0:
            return
        if result.returncode != 5 or attempt == 4:
            raise RuntimeError(f"승인 서비스 등록 실패 · 종료코드 {result.returncode} · launchctl 상태 확인 필요")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--write-only", action="store_true")
    args = parser.parse_args()
    script = Path(__file__).resolve().with_name("run_issue_review_bot.py")
    if str(script).startswith(("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")):
        raise SystemExit("정리될 수 있는 임시폴더에는 승인함을 설치하지 않습니다. 영구 작업폴더에서 실행하세요.")
    state = args.state_dir.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    target = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    content = {"Label": LABEL, "ProgramArguments": [sys.executable, str(script), "--env-file", str(args.env_file.resolve()),
               "--state-dir", str(state), "--runtime", str(args.runtime.resolve())],
               "WorkingDirectory": str(script.parents[1]), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
               "ProcessType": "Background", "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "TZ": "Asia/Seoul"},
               "StandardOutPath": str(state / "bot.log"), "StandardErrorPath": str(state / "bot.err.log")}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plistlib.dumps(content))
    target.chmod(0o600)
    if not args.write_only:
        domain = f"gui/{os.getuid()}"
        reload_agent(domain, target)
    print(f"개인 이슈 승인함 설치 완료: {target}")


if __name__ == "__main__":
    main()
