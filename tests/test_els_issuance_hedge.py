import importlib.util
from pathlib import Path


def load_els_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_els_index_risk.py"
    spec = importlib.util.spec_from_file_location("export_els_index_risk", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_issuance_stance_covers_four_operating_zones():
    module = load_els_module()

    assert module._issuance_stance(75, 30)["label"] == "발행기회"
    assert module._issuance_stance(75, 55)["label"] == "헤지주의"
    assert module._issuance_stance(90, 85)["label"] == "발행부담"
    assert module._issuance_stance(45, 20)["label"] == "선별발행"


def test_issuance_hedge_item_is_bounded_and_explainable():
    module = load_els_module()
    item = module._issuance_hedge_item(
        {
            "id": "test",
            "label": "TEST",
            "name": "Test Index",
            "region": "테스트",
            "lastDate": "2026-07-20",
            "metrics": {
                "return20dPct": -12.0,
                "realizedVol20dPct": 38.0,
                "realizedVol60dPct": 24.0,
                "volPercentile252d": 90.0,
                "drawdown252dPct": -18.0,
                "maxAbsDailyMove20dPct": 5.0,
            },
        },
        correlation_score=60.0,
    )

    assert 0 <= item["opportunityScore"] <= 100
    assert 0 <= item["hedgeBurdenScore"] <= 100
    assert item["stance"] == "헤지주의"
    assert item["interpretation"]
    assert set(item["components"]) == {
        "volPercentileScore",
        "volLevelScore",
        "volShockScore",
        "downsideMomentumScore",
        "drawdownScore",
        "gapShockScore",
        "correlationScore",
    }
