from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import export_hmm_regime
from scripts.index_history_cache import save_history


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
        if item["id"] == "kospi200":
            assert item["priceSource"].startswith(("Naver KPI200", "Yahoo Finance"))
        else:
            assert item["priceSource"].startswith("Yahoo Finance")
        assert 0 <= item["probabilities"]["위험회피"] <= 100
        assert 0 <= item["probabilities"]["고변동성 활황"] <= 100
        assert item["volSource"]

    els_risk = json.loads(ELS_INDEX_RISK_FILE.read_text(encoding="utf-8"))
    hmm_kospi200 = next(item for item in hmm_regime["indices"] if item["id"] == "kospi200")
    els_kospi200 = next(item for item in els_risk["indices"] if item["id"] == "kospi200")
    assert hmm_kospi200["lastDate"] >= els_kospi200["lastDate"]


def test_hmm_price_history_merges_bootstrap_and_els_cache(monkeypatch):
    dates = pd.date_range("2024-01-02", periods=340, freq="B")
    historical = pd.DataFrame(
        {"date": dates.strftime("%Y-%m-%d"), "close": np.linspace(100.0, 200.0, 340)}
    )
    cached_date = (dates[-1] + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
    cached = pd.DataFrame([{"date": cached_date, "close": 205.0}])

    monkeypatch.setattr(export_hmm_regime, "_fetch_yahoo", lambda *_args, **_kwargs: historical.copy())
    merged = export_hmm_regime._fetch_price_history("TEST", cached)

    assert len(merged) == 341
    assert merged.iloc[-1]["date"] == cached_date
    assert merged.iloc[-1]["close"] == 205.0


def test_kospi200_hmm_uses_naver_when_yahoo_history_fails(monkeypatch):
    naver = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-02", periods=400, freq="B").strftime("%Y-%m-%d"),
            "close": np.linspace(300.0, 370.0, 400),
        }
    )
    monkeypatch.setattr(
        export_hmm_regime,
        "_fetch_yahoo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("축약 응답")),
    )
    monkeypatch.setattr(export_hmm_regime, "_fetch_naver_index", lambda _symbol, **_kwargs: naver.copy())

    merged = export_hmm_regime._fetch_price_history("^KS200")

    assert len(merged) == 400
    assert merged.iloc[-1]["close"] == 370.0
    assert merged.attrs["priceSource"] == "Naver KPI200 + 증분 캐시"


def test_hmm_reuses_fresh_els_incremental_cache(monkeypatch, tmp_path):
    history = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-02", periods=400, freq="B").strftime("%Y-%m-%d"),
            "close": np.linspace(100.0, 180.0, 400),
        }
    )
    save_history(
        tmp_path,
        "spx",
        history,
        source="Yahoo Finance",
        fetch_mode="incremental",
        fetched_rows=65,
        overlap_days=10,
    )
    monkeypatch.setattr(export_hmm_regime, "INDEX_HISTORY_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        export_hmm_regime,
        "_fetch_yahoo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("재조회 금지")),
    )

    merged = export_hmm_regime._fetch_price_history("^GSPC", cache_key="spx")

    assert len(merged) == 400
    assert merged.attrs["historyCacheStatus"] == "shared-cache"
    assert merged.attrs["priceSource"] == "Yahoo Finance + 증분 캐시"
