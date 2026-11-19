"""기존 뉴스 LaunchAgent에 특별 거래일 처리만 연결한다."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import plistlib
import re
import subprocess

try:
    from market_session_schedule import KST, SESSION_EXCEPTIONS
except ModuleNotFoundError:
    from scripts.market_session_schedule import KST, SESSION_EXCEPTIONS


def adjusted_plist(payload: dict, wrapper: Path, today: date, prepare_minutes: int = 30) -> dict:
    """기존 실행 경로·인자를 보존하고 추가 기동은 중복하지 않는다."""
    result = dict(payload)
    args = list(payload["ProgramArguments"])
    if Path(args[1]).name != "run_session_news.py":
        args.insert(1, str(wrapper.resolve()))
    else:
        args[1] = str(wrapper.resolve())
    result["ProgramArguments"] = args
    calendar = payload["StartCalendarInterval"]
    intervals = list(calendar) if isinstance(calendar, list) else [calendar]
    preparation = payload["Label"].endswith(".prepare")
    for day_key, exception in SESSION_EXCEPTIONS.items():
        day = date.fromisoformat(day_key)
        if day < today:
            continue
        for value in exception["news"].values():
            target = datetime.fromisoformat(f"{day_key}T{value}")
            if preparation:
                target -= timedelta(minutes=prepare_minutes)
            entry = {"Month": target.month, "Day": target.day, "Hour": target.hour, "Minute": target.minute}
            if entry not in intervals:
                intervals.append(entry)
    result["StartCalendarInterval"] = intervals
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--news-root", type=Path, required=True)
    parser.add_argument("--prepare-minutes", type=int, default=30)
    parser.add_argument("--label", choices=("com.marketlab.news-digest", "com.marketlab.news-digest.prepare"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.prepare_minutes < 1440:
        raise SystemExit("사전 준비 간격은 0~1439분이어야 합니다.")
    root = args.news_root.resolve()
    wrapper = root / "scripts/run_session_news.py"
    if not wrapper.is_file() or not (wrapper.parent / "market_session_schedule.py").is_file():
        raise SystemExit("뉴스 실행기에 필요한 특별 거래일 모듈을 먼저 설치하세요.")
    plans = []
    labels = (args.label,) if args.label else ("com.marketlab.news-digest", "com.marketlab.news-digest.prepare")
    for label in labels:
        path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        raw = path.read_bytes()
        payload = plistlib.loads(raw)
        if payload.get("Label") != label:
            raise SystemExit(f"LaunchAgent 이름 불일치: {path}")
        state = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True)
        if re.search(r"^\s*pid = \d+", state.stdout, re.MULTILINE):
            raise SystemExit(f"실행 중인 작업은 중단하지 않습니다: {label}")
        plans.append((label, path, raw, adjusted_plist(payload, wrapper, datetime.now(KST).date(), args.prepare_minutes)))
    for label, path, raw, payload in plans:
        if args.apply:
            backup = root / "logs/schedule-backups" / datetime.now(KST).strftime("%Y%m%dT%H%M%S") / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            candidate = path.with_suffix(".plist.pending")
            candidate.write_bytes(plistlib.dumps(payload))
            candidate.replace(path)
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
            try:
                subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
                subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{label}"], check=True)
            except subprocess.CalledProcessError:
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
                path.write_bytes(raw)
                subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True)
                raise
        print(f"{label}: {'설치 완료' if args.apply else '설치 예정'} · 수능일 마감 브리핑 17:10 / 사전 준비 {args.prepare_minutes}분 전")


if __name__ == "__main__":
    main()
