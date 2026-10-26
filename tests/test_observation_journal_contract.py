"""관찰일지의 표시 정밀도 경계와 계산 오류 차단을 검증합니다."""

import importlib.util
import random
from pathlib import Path

import pytest

from scripts.update_market_risk import build_observation_journal


SPEC = importlib.util.spec_from_file_location("journal_smoke", Path(__file__).with_name("smoke_test.py"))
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


@pytest.fixture
def indicators():
    ids = [
        "bigtech_ai_demand_pressure", "global_ai_semiconductor_stress",
        "korea_ai_semiconductor_concentration", "energy_import_cost_pressure",
        "broad_reinflation_watch", "rates_pressure", "us_financial_conditions_stress",
        "volatility_term_structure_watch", "us_market_breadth_watch",
        "trading_activity_heat", "kb_domestic_funding_watch", "foreign_ownership_pressure",
    ]
    return {key: {"id": key, "name": key, "value": 42.0} for key in ids}


@pytest.mark.parametrize("journal_id,values", [
    ("ai-roi", {"bigtech_ai_demand_pressure": 62.3,
                "global_ai_semiconductor_stress": 43.7,
                "korea_ai_semiconductor_concentration": 86.8}),
    ("flow-deleveraging", {"volatility_term_structure_watch": 3.3,
                          "us_market_breadth_watch": 61.9, "trading_activity_heat": 38.0}),
    ("geopolitics-reinflation", {"energy_import_cost_pressure": 56.3,
                               "broad_reinflation_watch": 87.3}),
])
def test_valid_rounding_boundary_no_longer_blocks_publication(indicators, journal_id, values):
    for key, value in values.items():
        indicators[key]["value"] = value
    journal = build_observation_journal(list(indicators.values()))
    item = next(item for item in journal if item["id"] == journal_id)

    # 기존 검증은 정상 산출물의 양방향 0.06점 표시 오차도 실패로 판정했습니다.
    assert abs(sum(component["contribution"] for component in item["components"]) - item["score"]) >= 0.06
    SMOKE.assert_observation_journal_scores(item, indicators)


def test_all_journals_pass_across_score_range(indicators):
    rng = random.Random(42)
    for index in range(502):
        for indicator in indicators.values():
            indicator["value"] = (0.0 if index == 0 else 100.0 if index == 1 else rng.randrange(1001) / 10)
        for item in build_observation_journal(list(indicators.values())):
            if item["score"] is not None:
                SMOKE.assert_observation_journal_scores(item, indicators)


@pytest.mark.parametrize("field,value,message", [
    ("contribution", 23.11, "기여도 불일치"),
    ("value", 42.1, "원점수 불일치"),
    ("weight", -0.55, "가중치 범위 오류"),
    ("weight", float("nan"), "가중치 범위 오류"),
    ("contribution", float("nan"), "기여도 불일치"),
    ("contribution", float("inf"), "기여도 불일치"),
    ("id", "missing", "원천 지표가 없습니다"),
])
def test_invalid_component_is_rejected_with_context(indicators, field, value, message):
    item = build_observation_journal(list(indicators.values()))[0]
    item["components"][0][field] = value
    with pytest.raises(AssertionError, match=f"관찰일지 ai-roi.*{message}"):
        SMOKE.assert_observation_journal_scores(item, indicators)


@pytest.mark.parametrize("score", [42.1, 41.9, 42.01, float("nan"), float("inf"), -1, 101])
def test_invalid_total_is_rejected(indicators, score):
    item = build_observation_journal(list(indicators.values()))[0]
    item["score"] = score
    with pytest.raises(AssertionError, match="관찰일지 ai-roi.*합계 점수"):
        SMOKE.assert_observation_journal_scores(item, indicators)


def test_offsetting_contribution_errors_are_not_hidden(indicators):
    item = build_observation_journal(list(indicators.values()))[0]
    item["components"][0]["contribution"] += 0.01
    item["components"][1]["contribution"] -= 0.01
    with pytest.raises(AssertionError, match="기여도 불일치"):
        SMOKE.assert_observation_journal_scores(item, indicators)


def test_duplicate_component_is_rejected(indicators):
    item = build_observation_journal(list(indicators.values()))[0]
    item["components"].append(dict(item["components"][0]))
    with pytest.raises(AssertionError, match="구성항목이 중복"):
        SMOKE.assert_observation_journal_scores(item, indicators)


def test_incorrect_weight_total_is_rejected(indicators):
    item = build_observation_journal(list(indicators.values()))[0]
    component = item["components"][0]
    component["weight"] = 0.56
    component["contribution"] = round(component["value"] * component["weight"], 2)
    with pytest.raises(AssertionError, match="가중치 합계 오류"):
        SMOKE.assert_observation_journal_scores(item, indicators)
