"""승인 DB의 격리 복사본으로 실제 후보 탐색을 검증합니다. 발송·승인·공개 게시 없음."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.issue_discovery import discover
from scripts.issue_radar_llm import KST
from scripts.issue_review_store import ReviewStore
from scripts.run_issue_review_bot import load_env


def check(path: Path, themes: list[dict], now: datetime, **kwargs) -> dict:
    """원본은 읽기 전용으로 열고 SQLite 백업 API로 일관된 시험용 복사본을 만듭니다."""
    with TemporaryDirectory(prefix="issue-discovery-check-") as directory:
        store = ReviewStore(Path(directory))
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(store.path) as target:
                source.backup(target)
                # 시험에서는 계정·수신 오프셋·등록 링크·발송 권한을 사용하지 않습니다.
                target.execute("DELETE FROM meta WHERE key IN ('ownerId','botId','pairing','telegramOffset')")
        before = store.public_snapshot()
        summary = discover(store, themes, now, force=True, **kwargs)
        if store.public_snapshot() != before:
            raise ValueError("격리 시험 중 승인 등록부 변경 감지")
        with store.transaction() as db:
            row = db.execute("SELECT decisions FROM discovery_runs WHERE attempted_at=?", (summary["attemptedAt"],)).fetchone()
        decisions = json.loads(row[0])
        for decision in decisions:
            decision.pop("proposalId", None)
        return {"checkedAt": now.isoformat(), "productionUnchanged": True, "summary": summary, "decisions": decisions}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("원본 DB를 결과 파일로 덮어쓸 수 없습니다")
    if args.env_file:
        load_env(args.env_file)
    themes = json.loads((Path(__file__).resolve().parents[1] / "config/market_issues.json").read_text())["issues"]
    result = check(args.input, themes, datetime.now(KST))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps(result["summary"], ensure_ascii=False))
    if result["summary"]["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
