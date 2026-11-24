from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.update_market_risk import reconcile_domestic_eod_prices
from scripts.audit_data_completeness import cross_artifact_checks


NOW = datetime(2026, 11, 25, 7, 30, tzinfo=ZoneInfo("Asia/Seoul"))


def data():
    yahoo = {"hanmi": [{"date": "2026-11-24", "close": 163600, "volume": 663129}]}
    naver = {"hanmi": [{"date": "2026-11-24", "close": 155800, "volume": 663129, "foreignOwnership": 7.7}]}
    return yahoo, naver


def official(close=163600):
    return pd.DataFrame({"종가": [close]}, index=["042700"])


def test_confirmed_krx_price_preserves_original_and_non_price_fields():
    yahoo, naver = data()
    before = deepcopy((yahoo, naver))
    calls = []

    def fetch(date, *, market):
        calls.append((date, market))
        return official()

    result, evidence = reconcile_domestic_eod_prices(yahoo, naver, now=NOW, fetch_official=fetch)
    assert calls == [("20261124", "KOSPI")]
    assert (yahoo, naver) == before
    point = result["hanmi"][-1]
    assert point["close"] == 163600
    assert point["originalClose"] == 155800
    assert point["volume"] == 663129
    assert point["foreignOwnership"] == 7.7
    assert point["closeSource"] == "KRX verified EOD"
    assert evidence[0]["krxClose"] == evidence[0]["yahooClose"] == 163600
    assert evidence[0]["naverOriginalClose"] == 155800


@pytest.mark.parametrize("close", [163200, 155800, 0, float("nan")])
def test_unconfirmed_official_price_cannot_override_or_hide_conflict(close):
    yahoo, naver = data()
    result, evidence = reconcile_domestic_eod_prices(
        yahoo, naver, now=NOW, fetch_official=lambda *a, **k: official(close)
    )
    assert result == naver
    assert evidence == []


@pytest.mark.parametrize("condition", ["intraday", "different-date", "small-gap", "missing", "invalid"])
def test_no_official_query_for_ineligible_comparisons(condition):
    yahoo, naver = data()
    if condition == "intraday":
        yahoo["hanmi"][-1]["date"] = naver["hanmi"][-1]["date"] = "2026-11-25"
    elif condition == "different-date":
        naver["hanmi"][-1]["date"] = "2026-11-23"
    elif condition == "small-gap":
        naver["hanmi"][-1]["close"] = 163000
    elif condition == "missing":
        naver["hanmi"] = []
    else:
        naver["hanmi"][-1]["close"] = float("inf")

    def unexpected(*args, **kwargs):
        pytest.fail("불필요한 KRX 조회")

    result, evidence = reconcile_domestic_eod_prices(yahoo, naver, now=NOW, fetch_official=unexpected)
    assert result == naver
    assert evidence == []


def test_failed_official_query_keeps_original_values():
    yahoo, naver = data()

    def failed(*args, **kwargs):
        raise RuntimeError("offline")

    result, evidence = reconcile_domestic_eod_prices(yahoo, naver, now=NOW, fetch_official=failed)
    assert result == naver
    assert evidence == []


def test_multiple_tickers_use_one_official_daily_request():
    yahoo, naver = data()
    yahoo["samsung"] = [{"date": "2026-11-24", "close": 200000}]
    naver["samsung"] = [{"date": "2026-11-24", "close": 180000}]
    calls = []

    def fetch(*args, **kwargs):
        calls.append(args)
        return pd.DataFrame({"종가": [163600, 200000]}, index=["042700", "005930"])

    _, evidence = reconcile_domestic_eod_prices(yahoo, naver, now=NOW, fetch_official=fetch)
    assert len(calls) == 1
    assert len(evidence) == 2


def test_duplicate_official_ticker_cannot_be_used():
    yahoo, naver = data()
    result, evidence = reconcile_domestic_eod_prices(
        yahoo, naver, now=NOW,
        fetch_official=lambda *a, **k: pd.concat([official(), official()]),
    )
    assert result == naver
    assert not evidence


def test_quality_gate_still_blocks_unresolved_conflict_and_records_confirmed_repair():
    yahoo, naver = data()
    repaired, evidence = reconcile_domestic_eod_prices(
        yahoo, naver, now=NOW, fetch_official=lambda *a, **k: official()
    )

    def checks(points, records):
        snapshot = {
            "yahooSymbols": {"hanmi": {"lastDate": "2026-11-24", "lastClose": 163600}},
            "naverSymbols": {"hanmi": {"lastDate": "2026-11-24", "lastClose": points["hanmi"][-1]["close"]}},
            "domesticPriceReconciliations": records,
        }
        return {item["id"]: item for item in cross_artifact_checks({"snapshot": snapshot}, now=NOW)}

    original = checks(naver, [])
    assert original["cross:naver-yahoo-domestic-values"]["status"] == "error"
    verified = checks(repaired, evidence)
    assert verified["cross:naver-yahoo-domestic-values"]["status"] == "ok"
    assert verified["cross:domestic-price-reconciliations"]["status"] == "warning"
    assert "155,800" in verified["cross:domestic-price-reconciliations"]["detail"]
    still_conflicted = checks(naver, evidence)
    assert still_conflicted["cross:naver-yahoo-domestic-values"]["status"] == "error"
