from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts import export_hmm_regime


ROOT = Path(__file__).resolve().parents[1]
HMM_REGIME_FILE = ROOT / "data" / "hmm-regime.json"
ELS_INDEX_RISK_FILE = ROOT / "data" / "els-index-risk.json"


def test_hmm_regime_dashboard_contract():
    hmm_regime = json.loads(HMM_REGIME_FILE.read_text(encoding="utf-8"))

    assert hmm_regime["basket"]["regime"] in {"분산 안정", "고변동성 활황", "위험회피 확산"}
    assert len(hmm_regime["indices"]) == 5
    assert {item["regime"] for item in hmm_regime["indices"]} <= {"안정", "고변동성 활황", "위험회피"}

    for item in hmm_regime["indices"]:
        assert 0 <= item["issuerScore"] <= 100
        assert item["series"]
        assert item["lastDate"] == item["series"][-1]["date"]
        assert item["priceSource"] == "Yahoo Finance 다중 구간 + ELS 가격 캐시"
        assert 0 <= item["probabilities"]["위험회피"] <= 100
        assert 0 <= item["probabilities"]["고변동성 활황"] <= 100
        assert item["volSource"]

    els_risk = json.loads(ELS_INDEX_RISK_FILE.read_text(encoding="utf-8"))
    hmm_kospi200 = next(item for item in hmm_regime["indices"] if item["id"] == "kospi200")
    els_kospi200 = next(item for item in els_risk["indices"] if item["id"] == "kospi200")
    assert hmm_kospi200["lastDate"] >= els_kospi200["lastDate"]


def test_hmm_price_history_merges_fresh_short_range_and_cache(monkeypatch):
    historical = pd.DataFrame(
        [{"date": "2026-07-15", "close": 100.0}, {"date": "2026-07-16", "close": 101.0}]
    )
    recent = pd.DataFrame(
        [{"date": "2026-07-16", "close": 101.5}, {"date": "2026-07-23", "close": 108.0}]
    )
    cached = pd.DataFrame(
        [{"date": "2026-07-22", "close": 106.0}, {"date": "2026-07-23", "close": 107.5}]
    )

    def fake_fetch(symbol, range_value=export_hmm_regime.RANGE_VALUE, min_rows=120):
        assert symbol == "^KS200"
        return recent.copy() if range_value == "2y" else historical.copy()

    monkeypatch.setattr(export_hmm_regime, "_fetch_yahoo", fake_fetch)
    merged = export_hmm_regime._fetch_price_history("^KS200", cached)

    assert merged["date"].tolist() == ["2026-07-15", "2026-07-16", "2026-07-22", "2026-07-23"]
    assert merged.iloc[-1]["close"] == 108.0
