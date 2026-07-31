from __future__ import annotations

import json
from copy import deepcopy
from datetime import date

import numpy as np
import pandas as pd
import pytest

from m7_credit_proxy.pipeline import (
    DISCLAIMER,
    _atomic_write,
    build_proxy,
    calculate_proxy_history,
    extract_sec_financials,
    parse_ofr_csv,
    parse_stooq_csv,
    parse_treasury_xml,
    rolling_beta,
    rolling_percentile,
)
from scripts.update_market_risk import m7_credit_indicator


def test_parse_stooq_csv_validates_schema_and_sorting():
    rows = ["Date,Open,High,Low,Close,Volume"]
    for index, value_date in enumerate(pd.bdate_range("2025-01-01", periods=260)):
        close = 100 + index * 0.1
        rows.append(
            f"{value_date.date().isoformat()},{close},{close},{close},{close},1000"
        )
    frame = parse_stooq_csv(rows[0] + "\n" + "\n".join(reversed(rows[1:])))

    assert len(frame) == 260
    assert frame.index.is_monotonic_increasing
    assert frame["close"].min() > 0


def test_parse_stooq_csv_rejects_browser_challenge():
    with pytest.raises(ValueError, match="검증 화면"):
        parse_stooq_csv("<html>This site requires JavaScript</html>")


def test_parse_treasury_xml_extracts_required_tenors():
    xml = """
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
          xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
      <entry><content><m:properties>
        <d:NEW_DATE>2026-07-30T00:00:00</d:NEW_DATE>
        <d:BC_2YEAR>4.10</d:BC_2YEAR><d:BC_5YEAR>4.20</d:BC_5YEAR>
        <d:BC_10YEAR>4.35</d:BC_10YEAR><d:BC_30YEAR>4.80</d:BC_30YEAR>
      </m:properties></content></entry>
    </feed>
    """
    frame = parse_treasury_xml(xml)

    assert frame.index[0].date().isoformat() == "2026-07-30"
    assert frame.iloc[0]["treasury_10y"] == pytest.approx(4.35)


def test_parse_ofr_csv_keeps_official_components():
    frame = parse_ofr_csv(
        "Date,OFR FSI,Credit,Equity valuation,Safe assets,Funding,Volatility,"
        "United States,Other advanced economies,Emerging markets\n"
        "2026-07-28,-2.157,-1.125,-0.514,-0.325,-0.143,-0.050,-1.232,-0.428,-0.496\n"
    )

    assert frame.iloc[0]["ofr_fsi"] == pytest.approx(-2.157)
    assert frame.iloc[0]["ofr_credit"] == pytest.approx(-1.125)


def test_rolling_beta_does_not_use_future_observations():
    index = pd.bdate_range("2025-01-01", periods=90)
    benchmark = pd.Series(np.linspace(-0.02, 0.02, len(index)), index=index)
    asset = benchmark * 1.7 + np.sin(np.arange(len(index))) * 0.001
    base = rolling_beta(asset, benchmark, window=40, min_observations=20)

    future_index = index.append(pd.bdate_range(index[-1] + pd.Timedelta(days=1), periods=5))
    future_benchmark = benchmark.reindex(future_index)
    future_asset = asset.reindex(future_index)
    future_benchmark.iloc[-5:] = [0.1, -0.1, 0.2, -0.2, 0.3]
    future_asset.iloc[-5:] = [-0.4, 0.4, -0.5, 0.5, -0.6]
    extended = rolling_beta(future_asset, future_benchmark, window=40, min_observations=20)

    pd.testing.assert_series_equal(base, extended.loc[index], check_names=False)


def test_rolling_percentile_uses_current_and_past_only():
    values = pd.Series(np.arange(40, dtype=float))
    base = rolling_percentile(values, window=20, min_observations=10)
    changed_future = values.copy()
    changed_future.iloc[31:] = -999
    changed = rolling_percentile(changed_future, window=20, min_observations=10)

    pd.testing.assert_series_equal(base.iloc[:31], changed.iloc[:31])
    assert base.iloc[30] == pytest.approx(100.0)


def synthetic_inputs(periods: int = 160):
    index = pd.bdate_range("2025-01-02", periods=periods)
    phase = np.arange(periods)
    qqq_returns = 0.0004 + np.sin(phase / 9) * 0.003
    qqq = 100 * np.exp(np.cumsum(qqq_returns))
    prices = {"QQQ": pd.DataFrame({"close": qqq}, index=index)}
    member_multipliers = {
        "AAPL": 0.9,
        "MSFT": 1.0,
        "GOOGL": 1.05,
        "AMZN": 1.2,
        "META": 1.3,
        "NVDA": 1.6,
        "TSLA": 1.8,
    }
    for offset, (ticker, multiplier) in enumerate(member_multipliers.items()):
        residual = np.sin((phase + offset) / 13) * 0.0015
        returns = qqq_returns * multiplier + residual
        if ticker == "TSLA":
            returns[-5:] -= 0.015
        prices[ticker] = pd.DataFrame(
            {"close": (80 + offset * 5) * np.exp(np.cumsum(returns))},
            index=index,
        )
    ief_returns = 0.0001 + np.sin(phase / 17) * 0.0008
    prices["IEF"] = pd.DataFrame(
        {"close": 95 * np.exp(np.cumsum(ief_returns))}, index=index
    )
    for ticker, spread in {"IGIB": -0.00005, "LQD": -0.00008, "HYG": -0.00015}.items():
        stress = np.zeros(periods)
        stress[-5:] = spread * 8
        prices[ticker] = pd.DataFrame(
            {"close": 100 * np.exp(np.cumsum(ief_returns + spread + stress))},
            index=index,
        )
    ofr = pd.DataFrame(
        {
            "ofr_fsi": np.linspace(-1.0, 1.2, periods),
            "ofr_credit": np.linspace(-0.4, 0.5, periods),
        },
        index=index,
    )
    treasury = pd.DataFrame(
        {
            "treasury_2y": np.linspace(3.8, 4.1, periods),
            "treasury_5y": np.linspace(3.9, 4.2, periods),
            "treasury_10y": np.linspace(4.0, 4.4, periods),
            "treasury_30y": np.linspace(4.4, 4.8, periods),
        },
        index=index,
    )
    config = {
        "calculation": {
            "beta_window": 30,
            "beta_min_observations": 15,
            "residual_window": 5,
            "drawdown_window": 20,
            "percentile_window": 80,
            "percentile_min_observations": 30,
            "minimum_member_coverage": 6,
            "minimum_score_coverage": 0.70,
            "macro_forward_fill_days": 3,
            "ofr_max_stale_business_days": 5,
        },
        "weights": {
            "m7_idio_loss_5d": 0.30,
            "worst_member_loss_5d": 0.15,
            "m7_median_drawdown_60d": 0.10,
            "ig_credit_loss_5d": 0.20,
            "hy_ig_relative_5d": 0.10,
            "ofr_fsi": 0.15,
        },
        "bands": [
            {"min": 0, "max": 40, "id": "Low", "label": "낮음"},
            {"min": 40, "max": 60, "id": "Normal", "label": "보통"},
            {"min": 60, "max": 75, "id": "Watch", "label": "관찰"},
            {"min": 75, "max": 90, "id": "High", "label": "높음"},
            {"min": 90, "max": 101, "id": "Severe", "label": "심각"},
        ],
    }
    return prices, ofr, treasury, config


def test_calculation_aligns_five_day_residual_and_member_ranking():
    prices, ofr, treasury, config = synthetic_inputs()
    history, latest = calculate_proxy_history(prices, ofr, treasury, config)

    row = history.loc[pd.Timestamp(latest["as_of"])]
    assert row["m7_member_count"] == 7
    assert 0 <= row["combined_score"] <= 100
    assert latest["members"]["TSLA"]["rank"] == 1
    assert latest["members"]["TSLA"]["residual_5d"] < 0


def test_calculation_is_unchanged_when_future_rows_are_added():
    prices, ofr, treasury, config = synthetic_inputs()
    cutoff = prices["QQQ"].index[-10].date()
    base, _ = calculate_proxy_history(prices, ofr, treasury, config, asof=cutoff)

    changed_prices = deepcopy(prices)
    for frame in changed_prices.values():
        frame.iloc[-9:, 0] *= np.linspace(0.5, 1.5, 9)
    changed_ofr = ofr.copy()
    changed_treasury = treasury.copy()
    changed_ofr.loc[changed_ofr.index.date > cutoff] *= 10
    changed_treasury.loc[changed_treasury.index.date > cutoff] *= 2
    changed, _ = calculate_proxy_history(
        changed_prices, changed_ofr, changed_treasury, config, asof=cutoff
    )

    pd.testing.assert_series_equal(
        base["combined_score"], changed["combined_score"], check_names=False
    )


def test_less_than_seventy_percent_component_coverage_fails_score():
    prices, ofr, treasury, config = synthetic_inputs()
    prices["IGIB"]["close"] = np.nan
    prices["LQD"]["close"] = np.nan
    ofr["ofr_fsi"] = np.nan

    with pytest.raises(RuntimeError, match="70%"):
        calculate_proxy_history(prices, ofr, treasury, config)


def test_atomic_write_replaces_only_after_complete_write(tmp_path):
    target = tmp_path / "payload.json"
    target.write_text('{"status":"old"}', encoding="utf-8")

    _atomic_write(target, b'{"status":"new"}')

    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "new"
    assert not list(tmp_path.glob("*.tmp"))


def sec_payload():
    return {
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "val": 10,
                                "form": "10-Q",
                                "filed": "2026-05-01",
                                "end": "2026-03-31",
                                "accn": "new",
                            },
                            {
                                "val": 7,
                                "form": "10-K",
                                "filed": "2025-02-01",
                                "end": "2024-12-31",
                                "accn": "old",
                            },
                        ]
                    }
                },
                "ShortTermInvestments": {
                    "units": {
                        "USD": [
                            {
                                "val": 5,
                                "form": "10-Q",
                                "filed": "2026-05-01",
                                "end": "2026-03-31",
                                "accn": "new",
                            }
                        ]
                    }
                },
                "LongTermDebt": {
                    "units": {
                        "USD": [
                            {
                                "val": 9,
                                "form": "10-Q",
                                "filed": "2026-05-01",
                                "end": "2026-03-31",
                                "accn": "new",
                            }
                        ]
                    }
                },
                "LongTermDebtCurrent": {
                    "units": {
                        "USD": [
                            {
                                "val": 2,
                                "form": "10-Q",
                                "filed": "2026-05-01",
                                "end": "2026-03-31",
                                "accn": "new",
                            }
                        ]
                    }
                },
            }
        }
    }


def test_sec_parser_avoids_total_debt_double_counting():
    result = extract_sec_financials(sec_payload(), asof=date(2026, 7, 31))

    assert result["cash_and_short_term_investments"] == 15
    assert result["total_debt"] == 9
    assert result["debt_to_cash"] == pytest.approx(0.6)
    assert {item["tag"] for item in result["provenance"]} == {
        "CashAndCashEquivalentsAtCarryingValue",
        "ShortTermInvestments",
        "LongTermDebt",
    }


def test_sec_parser_applies_filing_date_asof():
    result = extract_sec_financials(sec_payload(), asof=date(2026, 3, 1))

    assert result["cash_and_short_term_investments"] == 7
    assert result["latest_filing_date"] == "2025-02-01"


def test_build_proxy_emits_public_schema_and_disclaimer():
    prices, ofr, treasury, config = synthetic_inputs()
    history, members, payload = build_proxy(
        prices,
        ofr,
        treasury,
        config,
        source_statuses=[
            {
                "source": "price:AAPL",
                "provider": "dashboard_yahoo",
                "status": "warning",
            }
        ],
    )

    assert not history.empty
    assert members["as_of"] == payload["latest"]["as_of"]
    assert payload["latest"]["quality"] == "partial"
    assert payload["latest"]["disclaimer"] == DISCLAIMER
    assert round(payload["series"][-1]["combined_score"], 1) == payload["latest"]["score"]


def test_market_indicator_is_observation_only_and_explains_source():
    prices, ofr, treasury, config = synthetic_inputs()
    _, _, payload = build_proxy(prices, ofr, treasury, config)
    indicator = m7_credit_indicator(payload)

    assert indicator["name"] == "M7 Credit Stress Proxy"
    assert indicator["role"] == "observation"
    assert indicator["weight"] == 0.0
    assert any("CDS 스프레드가 아니며" in item for item in indicator["detail"])
