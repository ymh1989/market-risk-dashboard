"""기존 뉴스 코드를 변경하지 않고 특별 거래일의 마감 브리핑을 순연한다."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
from datetime import datetime

try:
    from market_session_schedule import KST, shifted_times
except ModuleNotFoundError:
    from scripts.market_session_schedule import KST, shifted_times

NEWS_TIMES = {
    "NEWS_DIGEST_TIMES": "08:30,12:30,16:10",
    "MARKET_COMMENTARY_TIMES": "08:30,12:30,16:10",
    "NEWS_DIGEST_DASHBOARD_CARD_TIMES": "08:30,16:10",
    "LOCAL_MARKET_UPDATE_FULL_TIMES": "15:35",
}


def news_environment(env_file: Path, now: datetime, environ: dict) -> dict:
    """시각 설정만 읽는다. 인증정보와 다른 환경변수는 건드리지 않는다."""
    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() in NEWS_TIMES:
                values[key.strip()] = value.strip().strip('"').strip("'")
    day = now.astimezone(KST).date()
    return {
        key: shifted_times(environ.get(key, values.get(key, default)), day,
                           "market" if key == "LOCAL_MARKET_UPDATE_FULL_TIMES" else "news")
        for key, default in NEWS_TIMES.items()
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("실행할 뉴스 스크립트 경로가 필요합니다.")
    script = Path(sys.argv[1]).resolve(strict=True)
    os.environ.update(news_environment(script.parent.parent / ".env", datetime.now(KST), os.environ))
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
