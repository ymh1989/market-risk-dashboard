import importlib.util
from pathlib import Path

import pandas as pd
import pytest


def load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_kospi_flow_final.py"
    spec = importlib.util.spec_from_file_location("verify_kospi_flow_final", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flow_frame(**overrides):
    row = {
        "date": "2026-08-25",
        "foreign_net_buy_value": -100_000_000,
        "institution_net_buy_value": 200_000_000,
        "financial_investment_net_buy_value": 50_000_000,
        "pension_net_buy_value": 25_000_000,
        "program_net_buy_value": -75_000_000,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_verify_final_flows_accepts_complete_expected_date():
    module = load_module()

    values = module.verify_final_flows(flow_frame(), "2026-08-25")

    assert values["foreign_net_buy_value"] == -100_000_000
    assert values["program_net_buy_value"] == -75_000_000


def test_verify_final_flows_rejects_unpublished_value():
    module = load_module()

    with pytest.raises(ValueError, match="아직 공개되지 않았습니다"):
        module.verify_final_flows(
            flow_frame(foreign_net_buy_value=float("nan")),
            "2026-08-25",
        )


def test_verify_final_flows_rejects_missing_expected_date():
    module = load_module()

    with pytest.raises(ValueError, match="기준일 행이 없습니다"):
        module.verify_final_flows(flow_frame(), "2026-08-26")
