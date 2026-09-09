from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from kospi_risk.kb_market_funds import (
    KbMarketFundsError,
    build_market_funds_payload,
    parse_market_funds_response,
    reconcile_kb_with_kofia,
)
from kospi_risk.kofia_market_funds import (
    AMOUNT_DIVISOR_KRW_MILLION,
    KofiaFreeSisClient,
    KofiaFreeSisConfig,
    KofiaMarketFundsError,
    incremental_start_date,
    merge_freesis_rows,
    parse_freesis_credit_rows,
    parse_freesis_funds_rows,
)
from scripts.update_kb_market_funds import latest_existing_kb_snapshot


KST = timezone(timedelta(hours=9))
RETRIEVED_AT = datetime(2026, 9, 9, 18, 0, tzinfo=KST)


def funds_payload() -> dict:
    return {
        "unit": "",
        "ds1": [
            {
                "TMPV1": "20260908",
                "TMPV2": 96986329,
                "TMPV3": 43160449,
                "TMPV4": 109451246,
                "TMPV5": 881867,
                "TMPV6": 3244,
                "TMPV7": 0.3,
            },
            {
                "TMPV1": "20260907",
                "TMPV2": 93925789,
                "TMPV3": 42406680,
                "TMPV4": 109126021,
                "TMPV5": 1146731,
                "TMPV6": 9074,
                "TMPV7": 0.9,
            },
        ],
        "dsmHeader": "",
    }


def credit_payload() -> dict:
    return {
        "unit": "",
        "ds1": [
            {"TMPV1": "20260908", "TMPV2": 32738698},
            {"TMPV1": "20260907", "TMPV2": 33190325},
        ],
        "dsmHeader": "",
    }


def kb_payload() -> dict:
    return {
        "dataHeader": {"processFlag": "A"},
        "dataBody": {
            "dt": "20260907",
            "cs_dpst": "93925",
            "cs_dpst_cmpr_amt": "375",
            "rcvamt": "1146",
            "rcvamt_cmpr_amt": "94",
            "crdt_blnc": "33190",
            "crdt_blnc_cmpr_amt": "-405",
            "fts_tfnd": "42406",
            "fts_tfnd_cmpr_amt": "1882",
            "cpbnd_yr3_aa_yld_p5": "4.581",
            "cpbnd_yr3_bbb_yld_p5": "10.388",
            "cd_dy91_thng_yld_p5": "3.120",
            "cp_dy91_thng_yld_p5": "3.230",
        },
    }


def merged_kofia_rows() -> list[dict]:
    funds = parse_freesis_funds_rows(funds_payload(), retrieved_at=RETRIEVED_AT)
    credit = parse_freesis_credit_rows(credit_payload(), retrieved_at=RETRIEVED_AT)
    return merge_freesis_rows(funds, credit)[0]


def test_freesis_parsers_merge_exact_krw_million_values():
    rows, diagnostics = merge_freesis_rows(
        parse_freesis_funds_rows(funds_payload(), retrieved_at=RETRIEVED_AT),
        parse_freesis_credit_rows(credit_payload(), retrieved_at=RETRIEVED_AT),
    )

    assert [row["date"] for row in rows] == ["2026-09-07", "2026-09-08"]
    assert rows[0]["amountsKrwMillion"] == {
        "customerDeposits": 93925789,
        "futuresDeposits": 42406680,
        "receivables": 1146731,
        "creditBalance": 33190325,
    }
    assert rows[0]["amountsKrwBillion"]["customerDeposits"] == 93925.789
    assert diagnostics["mergedRows"] == 2
    assert diagnostics["fundsOnlyDates"] == []


def test_freesis_parser_fails_on_unscaled_won_values():
    payload = funds_payload()
    payload["ds1"][0]["TMPV2"] = 96_986_329_000_000

    with pytest.raises(KofiaMarketFundsError, match="tmpV40"):
        parse_freesis_funds_rows(payload)


def test_kb_and_freesis_same_day_reconciliation_accepts_rounding_only():
    kofia_row = merged_kofia_rows()[0]
    kb_row = parse_market_funds_response(kb_payload(), retrieved_at=RETRIEVED_AT)

    result = reconcile_kb_with_kofia(kofia_row, kb_row)

    assert result["status"] == "matched"
    assert result["overlapDate"] == "2026-09-07"
    assert all(item["absoluteDifferenceKrwMillion"] < 1000 for item in result["fields"])


def test_kb_and_freesis_same_day_reconciliation_rejects_material_gap():
    kofia_row = merged_kofia_rows()[0]
    payload = kb_payload()
    payload["dataBody"]["crdt_blnc"] = "30000"
    kb_row = parse_market_funds_response(payload, retrieved_at=RETRIEVED_AT)

    with pytest.raises(KbMarketFundsError, match="creditBalance"):
        reconcile_kb_with_kofia(kofia_row, kb_row)


def test_combined_payload_prefers_freesis_precision_and_uses_kb_as_enrichment():
    kb_row = parse_market_funds_response(kb_payload(), retrieved_at=RETRIEVED_AT)

    payload = build_market_funds_payload(
        None,
        kofia_rows=merged_kofia_rows(),
        kb_snapshot=kb_row,
        generated_at=RETRIEVED_AT,
        source_status={"kofia": "direct", "kb": "direct"},
    )

    overlap = payload["series"][0]
    assert payload["schemaVersion"] == 2
    assert payload["reconciliation"]["status"] == "matched"
    assert overlap["amountsKrwMillion"]["customerDeposits"] == 93925789
    assert overlap["sourceProviders"] == ["KOFIA FreeSIS", "KB Securities OpenAPI"]
    assert overlap["sourceSnapshots"]["kb"]["amountsKrwMillion"]["customerDeposits"] == 93925000
    assert overlap["ratesPct"]["corporateAa3y"] == 4.581
    assert payload["latest"]["date"] == "2026-09-08"
    assert payload["latest"]["scoreMode"] == "fixed-anchor-bootstrap"


def test_incremental_start_uses_five_year_backfill_then_overlap():
    reference = date(2026, 9, 9)
    assert incremental_start_date(None, reference_date=reference) == date(2021, 9, 9)

    existing = {
        "series": [
            {
                "date": "2026-09-08",
                "sourceProviders": ["KOFIA FreeSIS"],
            }
        ]
    }
    assert incremental_start_date(existing, reference_date=reference) == date(2026, 8, 25)


def test_existing_kb_snapshot_is_reused_when_only_freesis_is_refreshed():
    existing = {
        "schemaVersion": 2,
        "series": [
            {
                "date": "2026-09-07",
                "ratesPct": {"corporateAa3y": 4.581},
                "sourceProviders": ["KOFIA FreeSIS", "KB Securities OpenAPI"],
                "sourceSnapshots": {
                    "kb": {
                        "date": "2026-09-07",
                        "retrievedAt": "2026-09-09 09:00:00 KST",
                        "amountsKrwMillion": {
                            "customerDeposits": 93925000,
                            "creditBalance": 33190000,
                        },
                        "ratesPct": {"corporateAa3y": 4.581},
                    }
                },
            },
            {
                "date": "2026-09-08",
                "ratesPct": {},
                "sourceProviders": ["KOFIA FreeSIS"],
            },
        ],
    }

    snapshot = latest_existing_kb_snapshot(existing)
    assert snapshot["date"] == "2026-09-07"
    assert snapshot["amountsKrwMillion"]["customerDeposits"] == 93925000


def test_stored_kb_snapshot_can_be_reconciled_after_freesis_catches_up():
    kb_row = parse_market_funds_response(kb_payload(), retrieved_at=RETRIEVED_AT)
    kb_only = build_market_funds_payload(
        None,
        kb_snapshot=kb_row,
        source_status={"kb": "direct"},
    )
    stored_snapshot = latest_existing_kb_snapshot(kb_only)

    combined = build_market_funds_payload(
        kb_only,
        kofia_rows=merged_kofia_rows(),
        kb_snapshot=stored_snapshot,
        source_status={"kofia": "direct", "kb": "stale-fallback"},
    )

    assert combined["reconciliation"]["status"] == "matched"
    assert combined["reconciliation"]["overlapDate"] == "2026-09-07"
    assert combined["series"][0]["amountsKrwMillion"]["customerDeposits"] == 93925789


def test_future_observation_does_not_change_past_scores():
    start = date(2026, 5, 1)
    rows = []
    for offset in range(70):
        observed = start + timedelta(days=offset)
        rows.append(
            {
                "date": observed.isoformat(),
                "retrievedAt": "2026-09-09 18:00:00 KST",
                "amountsKrwMillion": {
                    "customerDeposits": 70_000_000 + offset * 100_000,
                    "creditBalance": 20_000_000 + offset * 30_000,
                    "receivables": 700_000 + offset * 1_000,
                    "futuresDeposits": 30_000_000 + offset * 40_000,
                },
                "sourceProviders": ["KOFIA FreeSIS"],
            }
        )

    before = build_market_funds_payload(None, kofia_rows=rows)
    future = {
        **rows[-1],
        "date": (start + timedelta(days=70)).isoformat(),
        "amountsKrwMillion": {
            "customerDeposits": 20_000_000,
            "creditBalance": 60_000_000,
            "receivables": 10_000_000,
            "futuresDeposits": 5_000_000,
        },
    }
    after = build_market_funds_payload(None, kofia_rows=[*rows, future])

    assert [row["score"] for row in before["series"]] == [
        row["score"] for row in after["series"][:-1]
    ]


def test_freesis_client_uses_public_services_and_numeric_million_divisor():
    calls = []
    responses = [
        b"<html></html>",
        {"dsLatestDate": [{"TMPV1": "RD", "TMPV2": "20260908"}]},
        {"dsLatestDate": [{"TMPV1": "RD", "TMPV2": "20260908"}]},
        funds_payload(),
        credit_payload(),
    ]

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            if isinstance(self.payload, bytes):
                return self.payload
            return json.dumps(self.payload).encode("utf-8")

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return Response(responses.pop(0))

    client = KofiaFreeSisClient(
        KofiaFreeSisConfig(retry_count=1, request_delay_seconds=0)
    )
    client._opener = Opener()

    rows, diagnostics = client.fetch_history(
        start_date=date(2026, 9, 7),
        end_date=date(2026, 9, 9),
        retrieved_at=RETRIEVED_AT,
    )

    assert len(calls) == 5
    assert len(rows) == 2
    assert diagnostics["effectiveEndDate"] == "2026-09-08"
    list_requests = [json.loads(call[0].data) for call in calls if call[0].data][-2:]
    assert {item["dmSearch"]["OBJ_NM"] for item in list_requests} == {
        "STATSCU0100000060BO",
        "STATSCU0100000070BO",
    }
    assert all(
        item["dmSearch"]["tmpV40"] == AMOUNT_DIVISOR_KRW_MILLION
        for item in list_requests
    )
