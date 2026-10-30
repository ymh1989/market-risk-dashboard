"""저장된 공개 레이더로 로컬 모델을 재검증합니다. 운영 데이터·승인함·Telegram은 변경하지 않습니다."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.issue_radar_llm import KST, candidates, enrich_radar
from scripts.run_issue_review_bot import load_env


def load_radar(path: Path) -> dict:
    """공개 JSON 또는 승인 DB의 공개 레이더 필드만 읽기 전용으로 조회합니다."""
    if path.suffix == ".sqlite3":
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key='latestRadar'").fetchone()
            if row is None:
                raise ValueError("저장된 시장 이슈 레이더가 없습니다")
            return json.loads(row[0])
    return json.loads(path.read_text())


def check(source: dict, now: datetime) -> dict:
    """원래 판독·지표를 보존했는지 확인하고 선택 기사만 비교합니다."""
    old = source if "issues" in source else next(s for s in source["sections"] if s["id"] == "market")["issueRadar"]
    revised = copy.deepcopy(old)
    enrich_radar(revised, None, now)
    rows = []
    for before, after in zip(old["issues"], revised["issues"]):
        for key in ("reading", "metrics", "weight"):
            if before.get(key) != after.get(key):
                raise ValueError("원래 시장 판독·지표·가중치가 변경되었습니다")
        def selected(issue):
            selection = issue.get("analysis", {}).get("selection") or {}
            ids = selection.get("articleIds", [])
            by_id = {a.get("id"): a for a in issue["news"]["articles"]}
            return [{k: by_id[i][k] for k in ("title", "url", "source", "publishedAt")} for i in ids if i in by_id]
        rows.append({"id": after["id"], "title": after["title"],
                     "inputCount": len(candidates(after, now)), "status": after["analysis"]["status"],
                     "reason": after["analysis"].get("reason"), "before": selected(before), "after": selected(after)})
    return {"checkedAt": now.isoformat(), "scoresUnchanged": True, "llm": revised["llm"], "issues": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("입력 원본과 결과 저장 경로는 달라야 합니다")
    if args.env_file:
        load_env(args.env_file)
    result = check(load_radar(args.input), datetime.now(KST))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["llm"]["status"], "scoresUnchanged": True,
                      "topics": [{"title": r["title"], "selected": len(r["after"]), "reason": r["reason"]} for r in result["issues"]]}, ensure_ascii=False))
    if result["llm"]["status"] != "generated":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
