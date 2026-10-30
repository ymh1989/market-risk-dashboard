"""진단 도구가 운영 원본·계정정보를 변경하거나 노출하지 않는지 확인합니다."""

import copy
from datetime import datetime
import json
import sqlite3

from scripts import check_issue_radar_llm as checker
from scripts.issue_radar_llm import KST


def test_sqlite_diagnostic_only_reads_public_radar(tmp_path):
    path = tmp_path / "review.sqlite3"
    radar = {"issues": []}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.executemany("INSERT INTO meta VALUES (?,?)", [("latestRadar", json.dumps(radar)), ("ownerId", '"SECRET"')])
    before = path.read_bytes()
    assert checker.load_radar(path) == radar
    assert path.read_bytes() == before


def test_selection_diagnostic_never_mutates_source(monkeypatch):
    original = {"generatedAt": "2026-10-30T12:00:00+09:00", "issues": []}
    before = copy.deepcopy(original)
    def enrich(radar, previous, now):
        radar["llm"] = {"status": "generated"}
    monkeypatch.setattr(checker, "enrich_radar", enrich)
    result = checker.check(original, datetime(2026, 10, 30, 13, tzinfo=KST))
    assert result["scoresUnchanged"] and original == before
