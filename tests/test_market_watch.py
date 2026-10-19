"""관찰카드의 금리차 단위·관측일·누수·운영점수 보존 검증."""

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import market_watch as watch
from scripts import update_market_risk as risk


def rows(count=240, base=3.0):
    start = datetime(2025, 1, 1)
    return [{"date": (start + timedelta(days=i)).date().isoformat(),
             "close": base + math.sin(i / 13) * 0.1 + i * 0.002} for i in range(count)]


def test_clean_series_keeps_negative_and_zero_but_not_invalid():
    source = [{"date": "2026-10-15", "close": None}, {"date": "2026-10-14", "close": -0.1},
              {"date": "2026-10-15", "close": 0}, {"date": "bad", "close": 10},
              {"date": "2026-10-16", "close": float("nan")}, {"date": "2026-10-17", "close": 5}]
    assert watch.clean_series(source, "2026-10-16") == [
        {"date": "2026-10-14", "close": -0.1}, {"date": "2026-10-15", "close": 0}]


def test_spread_does_not_forward_fill_and_preserves_negative_bp():
    first = [{"date": "2026-10-15", "close": 3.89}, {"date": "2026-10-16", "close": 5}]
    second = [{"date": "2026-10-15", "close": 3.90}, {"date": "2026-10-19", "close": 3.90}]
    result = watch.difference(first, second)
    assert len(result) == 1
    assert result[0]["close"] == pytest.approx(-1)
    assert result[0]["date"] == "2026-10-15"


def test_domestic_short_history_is_raw_not_zero_score():
    rates = {"cp91d": 3.69, "cd91d": 3.25, "corporateAa3y": 4.535, "treasury3y": 3.845}
    result = watch.domestic_card(risk, {"series": [
        {"date": "2026-10-15", "ratesPct": rates, "retrievedAt": "2026-10-19 15:35:53 KST"},
        {"date": "2026-10-16", "ratesPct": {}}]})
    assert result["value"] is None and result["scoreSeries"] == []
    assert result["asOf"] == "2026-10-15"
    assert result["weight"] == 0
    assert result["observationCharts"][0]["points"][0]["close"] == pytest.approx(44)
    assert result["observationCharts"][1]["points"][0]["close"] == pytest.approx(69)
    assert result["sourceRecords"][0]["retrievedAt"] == "2026-10-19 15:35:53 KST"


def test_real_yield_uses_common_observation_day():
    series = {key: rows(base=base) for key, base in [("real10y", 2), ("breakeven10y", 2.3), ("nominal10y", 4.3)]}
    series["breakeven10y"].append({"date": "2026-10-19", "close": 100})
    result = watch.real_yield_card(risk, series, {})
    assert result["asOf"] == series["real10y"][-1]["date"]
    assert all(c["points"][-1]["date"] == result["asOf"] for c in result["observationCharts"])
    assert 0 <= result["value"] <= 100


def test_scores_are_causal_and_minimum_history_is_respected():
    source = rows()
    for size in (119, 120, 180):
        short = watch.joint_scores(risk, source[:size])
        full = watch.joint_scores(risk, source)
        assert short == [p for p in full if p["date"] <= source[size-1]["date"]]
    assert watch.joint_scores(risk, source[:119]) == []
    assert watch.rolling_mean(source[:4]) == []
    assert watch.rolling_mean(source[:5])[0]["close"] == pytest.approx(sum(p["close"] for p in source[:5])/5)


def test_dollar_score_does_not_use_future_policy_rate():
    series = {"sofr": rows(base=3.8), "iorb": rows(base=3.9), "sofr99": rows(base=4)}
    before = watch.dollar_funding_card(risk, series, {})
    series["iorb"].append({"date": "2026-10-19", "close": 99})
    after = watch.dollar_funding_card(risk, series, {})
    assert before == after
    assert before["observationCharts"][0]["points"][-1]["close"] == pytest.approx(-10)


def test_european_daily_equal_weight_returns_and_missing_member():
    series = {key: [{"date": "2026-10-01", "close": 100}, {"date": "2026-10-02", "close": value}]
              for key, value in [("bnp", 110), ("socgen", 100), ("creditagricole", 90), ("euro50", 105)]}
    result = watch.european_bank_context(series, {})
    assert result["observationCharts"][0]["points"][-1]["close"] == pytest.approx(100 / 1.05)
    assert result["metrics"] == {}
    series.pop("socgen")
    assert watch.european_bank_context(series, {})["observationCharts"][0]["points"] == []


def test_source_failure_is_explicit_and_keeps_prior_observation_date(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("network")
    monkeypatch.setattr(risk, "fetch_fred_series", fail)
    monkeypatch.setattr(watch, "fetch_bank_series", fail)
    old = {"real10y": {"rows": rows(), "source": {"lastDate": rows()[-1]["date"], "retrievedAt": "old"}}}
    result = watch.fetch_sources(risk, old, datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert result["real10y"]["source"]["fetchStatus"] == "cached"
    assert result["real10y"]["source"]["retrievedAt"] == "old"
    assert result["real10y"]["rows"] == rows()
    assert result["sofr"]["source"]["fetchStatus"] == "unavailable"
    assert result["sofr"]["rows"] == []
    assert old["real10y"]["source"].get("fetchStatus") is None


def test_bank_source_requires_eur_and_excludes_unfinished_session(monkeypatch):
    source = rows(240, 100)
    now = datetime.fromisoformat(source[-1]["date"] + "T14:00:00+00:00")
    payload = {"chart": {"result": [{"meta": {"currency": "EUR"},
        "timestamp": [datetime.fromisoformat(p["date"] + "T12:00:00+00:00").timestamp() for p in source],
        "indicators": {"adjclose": [{"adjclose": [p["close"] for p in source]}]}}]}}
    from io import StringIO
    monkeypatch.setattr(watch.urllib.request, "urlopen", lambda *a, **k: StringIO(json.dumps(payload)))
    result = watch.fetch_bank_series(risk, "BNP.PA", now)
    assert result[-1]["date"] == source[-2]["date"]
    payload["chart"]["result"][0]["meta"]["currency"] = "USD"
    with pytest.raises(RuntimeError):
        watch.fetch_bank_series(risk, "BNP.PA", now)


def test_overlay_is_idempotent_and_preserves_every_weighted_indicator(tmp_path, monkeypatch):
    paths = ("HISTORY_CACHE_FILE", "KB_MARKET_FUNDS_FILE", "DASHBOARD_FILE", "SNAPSHOT_FILE", "TIMESERIES_FILE")
    for key in paths:
        original = getattr(risk, key)
        target = tmp_path / original.name
        target.write_text(original.read_text())
        monkeypatch.setattr(risk, key, target)
    read = lambda: json.loads(risk.DASHBOARD_FILE.read_text())
    market = lambda: next(s for s in read()["sections"] if s["id"] == "market")
    before = copy.deepcopy([item for item in market()["indicators"] if item["weight"] > 0])
    score_before = market().get("score")
    now = datetime(2026, 10, 19, 17, tzinfo=risk.KST)
    first = watch.refresh_market_watch(risk, fetch=False, now=now)
    result = read()
    assert first == watch.refresh_market_watch(risk, fetch=False, now=now)
    assert read() == result
    assert [item for item in market()["indicators"] if item["weight"] > 0] == before
    assert market().get("score") == score_before
    assert all(item["weight"] == 0 for item in market()["rawObservationCards"])
