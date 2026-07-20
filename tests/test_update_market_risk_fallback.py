from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_market_risk.py"


def load_update_module():
    spec = importlib.util.spec_from_file_location("update_market_risk_fallback_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_history_cache_supplies_dgs2_when_raw_csv_has_no_fred_columns(tmp_path, monkeypatch):
    module = load_update_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "HISTORY_CACHE_FILE", tmp_path / "data" / "market-history-cache.json")

    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "market_data.csv").write_text("date,KOSPI\n2026-01-01,3000\n", encoding="utf-8")

    today = date.today()
    points = [
        {"date": (today - timedelta(days=119 - index)).isoformat(), "close": 4.0 + index / 1000}
        for index in range(120)
    ]
    module.HISTORY_CACHE_FILE.write_text(
        json.dumps({"schemaVersion": 2, "fred": {"us2y": points}}),
        encoding="utf-8",
    )

    series, source = module.load_cached_fred_series(module.FRED_SERIES["us2y"])

    assert source == "data/market-history-cache.json"
    assert len(series) == 120
    assert series[-1]["date"] == today.isoformat()
