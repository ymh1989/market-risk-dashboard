from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from kospi_risk.kb_market_funds import (
    KbMarketFundsError,
    KbOpenApiClient,
    KbOpenApiConfig,
    SCORE_SMOOTHING_ALPHA,
    parse_market_funds_response,
    update_market_funds_payload,
)
from scripts.update_kb_market_funds import write_json_atomic
from scripts.update_market_risk import kb_market_funds_indicator


KST = timezone(timedelta(hours=9))


def api_payload(*, observed_date: str = "20260907", credit_balance: int = 33190) -> dict:
    return {
        "dataHeader": {"processFlag": "A"},
        "dataBody": {
            "dt": observed_date,
            "cs_dpst": "93925",
            "cs_dpst_cmpr_amt": "375",
            "rcvamt": "1146",
            "rcvamt_cmpr_amt": "94",
            "crdt_blnc": str(credit_balance),
            "crdt_blnc_cmpr_amt": "-405",
            "fts_tfnd": "42406",
            "fts_tfnd_cmpr_amt": "1882",
            "stk_typ_bnf_amt": "82041",
            "stk_typ_bnf_cmpr_amt": "120",
            "bnd_typ_bnf_amt": "182233",
            "bnd_typ_bnf_cmpr_amt": "-220",
            "mix_typ_bnf_amt": "25400",
            "mix_typ_bnf_cmpr_amt": "30",
            "mmf_amt": "266303",
            "mmf_cmpr_amt": "-785",
            "cpbnd_yr3_aa_yld_p5": "4.581",
            "cpbnd_yr3_bbb_yld_p5": "10.388",
            "cd_dy91_thng_yld_p5": "3.120",
            "cp_dy91_thng_yld_p5": "3.230",
            "ntnbnd_yr3_thng_yld_p5": "3.830",
            "ntnbnd_yr10_thng_yld_p5": "4.320",
        },
    }


def snapshot(*, observed_date: str = "20260907", credit_balance: int = 33190) -> dict:
    return parse_market_funds_response(
        api_payload(observed_date=observed_date, credit_balance=credit_balance),
        retrieved_at=datetime(2026, 9, 9, 9, 0, tzinfo=KST),
    )


def test_parse_market_funds_response_calculates_ratios_and_spreads():
    row = snapshot()

    assert row["date"] == "2026-09-07"
    assert row["amountsKrwBillion"]["customerDeposits"] == 93925
    assert row["derived"]["creditToDepositsPct"] == pytest.approx(35.3367048)
    assert row["derived"]["creditBalanceChangePct"] == pytest.approx(-1.2055365)
    assert row["derived"]["receivablesToDepositsPct"] == pytest.approx(1.2201224)
    assert row["derived"]["bbbAaSpreadPctp"] == pytest.approx(5.807)
    assert row["derived"]["cpCdSpreadPctp"] == pytest.approx(0.110)


@pytest.mark.parametrize(
    "field",
    [
        "cs_dpst",
        "cs_dpst_cmpr_amt",
        "rcvamt",
        "rcvamt_cmpr_amt",
        "crdt_blnc",
        "crdt_blnc_cmpr_amt",
    ],
)
def test_parse_market_funds_response_fails_fast_on_required_values(field):
    payload = api_payload()
    payload["dataBody"].pop(field)

    with pytest.raises(KbMarketFundsError, match=field):
        parse_market_funds_response(payload)


def test_expanding_score_does_not_rewrite_past_when_future_rows_are_added():
    first_rows = []
    for offset in range(65):
        observed = (datetime(2026, 5, 1, tzinfo=KST) + timedelta(days=offset)).strftime(
            "%Y%m%d"
        )
        first_rows.append(snapshot(observed_date=observed, credit_balance=30000 + offset * 15))

    base = None
    for row in first_rows:
        base = update_market_funds_payload(base, row)
    prior_scores = {row["date"]: row["score"] for row in base["series"]}

    future = snapshot(observed_date="20260720", credit_balance=50000)
    extended = update_market_funds_payload(base, future)

    assert {row["date"]: row["score"] for row in extended["series"][:-1]} == prior_scores
    assert extended["series"][59]["scoreMode"] == "expanding-hybrid"


def test_confirmed_score_uses_only_past_ewm_and_reduces_daily_noise():
    rows = []
    for offset in range(90):
        observed = (datetime(2026, 1, 1, tzinfo=KST) + timedelta(days=offset)).strftime(
            "%Y%m%d"
        )
        credit_balance = 30000 if offset % 2 == 0 else 39000
        rows.append(snapshot(observed_date=observed, credit_balance=credit_balance))

    payload = None
    for row in rows:
        payload = update_market_funds_payload(payload, row)

    series = payload["series"]
    expected = series[0]["rawScore"]
    assert series[0]["score"] == pytest.approx(expected, abs=0.1)
    for row in series[1:]:
        expected = (
            SCORE_SMOOTHING_ALPHA * row["rawScore"]
            + (1 - SCORE_SMOOTHING_ALPHA) * expected
        )
        assert row["score"] == pytest.approx(expected, abs=0.15)

    raw_variation = sum(
        abs(current["rawScore"] - previous["rawScore"])
        for previous, current in zip(series, series[1:])
    )
    confirmed_variation = sum(
        abs(current["score"] - previous["score"])
        for previous, current in zip(series, series[1:])
    )
    assert confirmed_variation < raw_variation * 0.5


def test_market_indicator_is_observation_only_and_keeps_market_aggregate_context():
    payload = update_market_funds_payload(None, snapshot())
    indicator = kb_market_funds_indicator(payload)

    assert indicator["id"] == "kb_domestic_funding_watch"
    assert indicator["role"] == "observation"
    assert indicator["weight"] == 0
    assert indicator["asOf"] == "2026-09-07"
    assert "고객예탁금 93.92조원" in indicator["detail"][0]
    assert "신용/예탁금 35.3%" in indicator["detail"][1]
    assert "개인" not in " ".join(indicator["detail"])


def test_client_uses_only_token_and_market_information_paths(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        calls.append((request.full_url, request.headers, body, timeout))
        if request.full_url.endswith("/oauth2/token"):
            return Response({"dataBody": {"access_token": "token-value"}})
        return Response(api_payload())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = KbOpenApiClient(
        KbOpenApiConfig(
            base_url="https://developer.kbsec.com:32484",
            app_key="app-key",
            app_secret="app-secret",
            timeout_seconds=7,
            retry_count=1,
        )
    )

    row = client.fetch_market_funds()

    assert row["date"] == "2026-09-07"
    assert [call[0].rsplit("/", 1)[-1] for call in calls] == ["token", "iva10370"]
    assert calls[1][2]["dataBody"] == {}
    assert calls[1][1]["Authorization"] == "bearer token-value"
    assert not any("order" in call[0].lower() for call in calls)


def test_atomic_writer_replaces_complete_json(tmp_path):
    output = tmp_path / "kb-market-funds.json"
    output.write_text('{"old": true}', encoding="utf-8")

    write_json_atomic(output, {"new": True})

    assert json.loads(output.read_text(encoding="utf-8")) == {"new": True}
    assert list(tmp_path.glob("*.tmp")) == []
