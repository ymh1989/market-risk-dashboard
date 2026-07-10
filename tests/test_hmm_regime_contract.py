from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HMM_REGIME_FILE = ROOT / "data" / "hmm-regime.json"


def test_hmm_regime_dashboard_contract():
    hmm_regime = json.loads(HMM_REGIME_FILE.read_text(encoding="utf-8"))

    assert hmm_regime["basket"]["regime"] in {"분산 안정", "고변동성 활황", "위험회피 확산"}
    assert len(hmm_regime["indices"]) == 5
    assert {item["regime"] for item in hmm_regime["indices"]} <= {"안정", "고변동성 활황", "위험회피"}

    for item in hmm_regime["indices"]:
        assert 0 <= item["issuerScore"] <= 100
        assert item["series"]
        assert 0 <= item["probabilities"]["위험회피"] <= 100
        assert 0 <= item["probabilities"]["고변동성 활황"] <= 100
        assert item["volSource"]
