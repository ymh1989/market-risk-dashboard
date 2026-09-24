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


@pytest.mark.parametrize("reference,expected", [
    ("2026-09-23", "2026-09-23"),
    ("2026-09-24", "2026-09-23"),
    ("2026-09-25", "2026-09-23"),
    ("2026-09-26", "2026-09-23"),
    ("2026-09-27", "2026-09-23"),
    ("2026-09-28", "2026-09-28"),
    ("2026-05-01", "2026-04-30"),
    ("2026-08-17", "2026-08-14"),
    ("2026-10-05", "2026-10-02"),
    ("2026-12-31", "2026-12-30"),
    ("2027-01-01", "2026-12-30"),
])
def test_krx_calendar_resolves_holidays_not_just_weekends(reference, expected):
    assert load_module().resolve_krx_session_date(reference) == expected


def test_normal_session_cannot_silently_fall_back_to_stale_data():
    module = load_module()
    expected = module.resolve_krx_session_date("2026-09-28")
    with pytest.raises(ValueError, match="기준일 행이 없습니다"):
        module.verify_final_flows(flow_frame(date="2026-09-23"), expected)


def test_holiday_still_requires_complete_last_session_flows():
    module = load_module()
    expected = module.resolve_krx_session_date("2026-09-24")
    with pytest.raises(ValueError, match="아직 공개되지 않았습니다"):
        module.verify_final_flows(
            flow_frame(date="2026-09-23", program_net_buy_value=float("nan")), expected
        )


def test_calendar_unavailable_fails_instead_of_assuming_a_holiday(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "exchange_calendars", None)
    with pytest.raises(RuntimeError, match="거래일 달력이 없습니다"):
        load_module().resolve_krx_session_date("2026-09-24")


def test_calendar_out_of_range_fails_clearly():
    with pytest.raises(ValueError, match="거래일 달력 확인 불가"):
        load_module().resolve_krx_session_date("2100-01-01")


def test_resolve_cli_needs_no_stored_frame(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["verify_kospi_flow_final.py", "--date", "2026-09-24", "--resolve-date"])
    load_module().main()
    assert capsys.readouterr().out.strip() == "2026-09-23"
