from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any


TOKEN_PATH = "/oauth2/token"
MARKET_FUNDS_PATH = "/api/v1/iva10370"
KST = timezone(timedelta(hours=9))

AMOUNT_FIELDS = {
    "customerDeposits": "cs_dpst",
    "customerDepositsChange": "cs_dpst_cmpr_amt",
    "receivables": "rcvamt",
    "receivablesChange": "rcvamt_cmpr_amt",
    "creditBalance": "crdt_blnc",
    "creditBalanceChange": "crdt_blnc_cmpr_amt",
    "futuresDeposits": "fts_tfnd",
    "futuresDepositsChange": "fts_tfnd_cmpr_amt",
    "stockFunds": "stk_typ_bnf_amt",
    "stockFundsChange": "stk_typ_bnf_cmpr_amt",
    "bondFunds": "bnd_typ_bnf_amt",
    "bondFundsChange": "bnd_typ_bnf_cmpr_amt",
    "mixedFunds": "mix_typ_bnf_amt",
    "mixedFundsChange": "mix_typ_bnf_cmpr_amt",
    "mmf": "mmf_amt",
    "mmfChange": "mmf_cmpr_amt",
}

RATE_FIELDS = {
    "corporateAa3y": "cpbnd_yr3_aa_yld_p5",
    "corporateBbb3y": "cpbnd_yr3_bbb_yld_p5",
    "cd91d": "cd_dy91_thng_yld_p5",
    "cp91d": "cp_dy91_thng_yld_p5",
    "treasury3y": "ntnbnd_yr3_thng_yld_p5",
    "treasury10y": "ntnbnd_yr10_thng_yld_p5",
}


class KbMarketFundsError(RuntimeError):
    """외부에 표시해도 인증정보가 노출되지 않는 KB OpenAPI 오류입니다."""


class KbMarketFundsReconciliationError(KbMarketFundsError):
    """FreeSIS와 KB 동일일 값이 허용오차를 벗어난 경우의 구조화 오류입니다."""

    def __init__(self, message: str, reconciliation: dict[str, Any]) -> None:
        super().__init__(message)
        self.reconciliation = reconciliation


@dataclass(frozen=True)
class KbOpenApiConfig:
    base_url: str
    app_key: str
    app_secret: str
    ip_addr: str = "127.0.0.1"
    mac_addr: str = "00-00-00-00-00-00"
    timeout_seconds: int = 20
    retry_count: int = 3

    def __post_init__(self) -> None:
        if not self.base_url.lower().startswith("https://"):
            raise ValueError("KB OpenAPI base URL은 HTTPS여야 합니다.")
        try:
            ip_address(self.ip_addr)
        except ValueError:
            raise ValueError("KB_OPENAPI_IP_ADDR는 올바른 IP 주소여야 합니다.") from None
        if not re.fullmatch(r"[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5}", self.mac_addr):
            raise ValueError("KB_OPENAPI_MAC_ADDR는 00-00-00-00-00-00 형식이어야 합니다.")
        if self.timeout_seconds <= 0:
            raise ValueError("KB_OPENAPI_TIMEOUT_SECONDS는 양수여야 합니다.")
        if self.retry_count <= 0:
            raise ValueError("KB_OPENAPI_RETRY_COUNT는 양수여야 합니다.")


def _number(value: object, *, required: bool = False, field: str = "") -> float | None:
    text = "" if value is None else str(value).strip().replace(",", "")
    if not text:
        if required:
            raise KbMarketFundsError(f"KB 증시주변자금 응답에 {field} 값이 없습니다.")
        return None
    try:
        number = float(text)
    except ValueError:
        if required:
            raise KbMarketFundsError(f"KB 증시주변자금 {field} 값이 숫자가 아닙니다.") from None
        return None
    if not math.isfinite(number):
        raise KbMarketFundsError(f"KB 증시주변자금 {field} 값이 유효하지 않습니다.")
    return number


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _change_pct(current: float | None, change: float | None) -> float | None:
    if current is None or change is None:
        return None
    previous = current - change
    if previous == 0:
        return None
    return change / previous * 100


def parse_market_funds_response(
    payload: dict[str, Any],
    *,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """IVA10370 응답을 공개 가능한 시장 집계 레코드로 변환합니다."""
    header = payload.get("dataHeader")
    if isinstance(header, dict):
        result_code = str(header.get("resultCode") or "").strip()
        process_flag = str(header.get("processFlag") or "").strip()
        if result_code not in {"", "200"} or process_flag not in {"", "A"}:
            code = str(header.get("processCode") or result_code or "-").strip()
            raise KbMarketFundsError(f"KB 증시주변자금 업무 처리가 실패했습니다 [{code}].")

    body = payload.get("dataBody")
    if not isinstance(body, dict):
        raise KbMarketFundsError("KB 증시주변자금 응답의 dataBody 형식이 올바르지 않습니다.")

    raw_date = str(body.get("dt") or "").strip()
    try:
        observed_date = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
    except ValueError:
        raise KbMarketFundsError("KB 증시주변자금 기준일이 YYYYMMDD 형식이 아닙니다.") from None

    amounts = {
        name: _number(
            body.get(field),
            required=name
            in {
                "customerDeposits",
                "customerDepositsChange",
                "receivables",
                "receivablesChange",
                "creditBalance",
                "creditBalanceChange",
            },
            field=field,
        )
        for name, field in AMOUNT_FIELDS.items()
    }
    rates = {name: _number(body.get(field), field=field) for name, field in RATE_FIELDS.items()}

    customer_deposits = amounts["customerDeposits"]
    credit_balance = amounts["creditBalance"]
    receivables = amounts["receivables"]
    derived = {
        "creditToDepositsPct": (_ratio(credit_balance, customer_deposits) or 0.0) * 100,
        "receivablesToDepositsPct": (_ratio(receivables, customer_deposits) or 0.0) * 100,
        "customerDepositsChangePct": _change_pct(
            customer_deposits, amounts["customerDepositsChange"]
        ),
        "creditBalanceChangePct": _change_pct(
            credit_balance, amounts["creditBalanceChange"]
        ),
        "receivablesChangePct": _change_pct(receivables, amounts["receivablesChange"]),
        "futuresDepositsChangePct": _change_pct(
            amounts["futuresDeposits"], amounts["futuresDepositsChange"]
        ),
        "bbbAaSpreadPctp": (
            rates["corporateBbb3y"] - rates["corporateAa3y"]
            if rates["corporateBbb3y"] is not None and rates["corporateAa3y"] is not None
            else None
        ),
        "cpCdSpreadPctp": (
            rates["cp91d"] - rates["cd91d"]
            if rates["cp91d"] is not None and rates["cd91d"] is not None
            else None
        ),
    }
    retrieved_at = retrieved_at or datetime.now(KST)
    return {
        "date": observed_date,
        "retrievedAt": retrieved_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "amountsKrwBillion": amounts,
        "amountsKrwMillion": {
            key: (round(float(value) * 1000, 6) if value is not None else None)
            for key, value in amounts.items()
        },
        "ratesPct": rates,
        "derived": derived,
        "sourceProviders": ["KB Securities OpenAPI"],
    }


def _fixed_anchor_score(value: float, safe: float, stress: float) -> float:
    if safe == stress:
        return 50.0
    return max(0.0, min(100.0, (value - safe) / (stress - safe) * 100))


def _historical_score(values: list[float], current: float, *, inverse: bool = False) -> float:
    ordered = sorted(values)
    percentile = 100 * sum(value <= current for value in ordered) / len(ordered)
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    z_score = 50.0 if stdev == 0 else 100 * 0.5 * (1 + math.erf((current - mean) / stdev / math.sqrt(2)))
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    robust_scale = 1.4826 * mad
    robust_score = (
        50.0
        if robust_scale == 0
        else 100 * 0.5 * (1 + math.erf((current - median) / robust_scale / math.sqrt(2)))
    )
    score = percentile * 0.4 + z_score * 0.3 + robust_score * 0.3
    return 100 - score if inverse else score


def score_market_funds_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """각 날짜까지의 정보만 사용해 레버리지·대기자금 관찰점수를 계산합니다."""
    scored: list[dict[str, Any]] = []
    component_specs = {
        "creditToDepositsPct": {"weight": 0.45, "safe": 20.0, "stress": 45.0, "inverse": False},
        "creditBalanceChangePct": {"weight": 0.25, "safe": -2.0, "stress": 2.0, "inverse": False},
        "receivablesToDepositsPct": {"weight": 0.20, "safe": 0.5, "stress": 2.0, "inverse": False},
        "customerDepositsChangePct": {"weight": 0.10, "safe": 2.0, "stress": -2.0, "inverse": True},
    }
    history: dict[str, list[float]] = {name: [] for name in component_specs}

    for row in sorted(rows, key=lambda item: item["date"]):
        components = []
        for name, spec in component_specs.items():
            value = (row.get("derived") or {}).get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            numeric = float(value)
            history[name].append(numeric)
            if len(history[name]) >= 60:
                score = _historical_score(
                    history[name],
                    numeric,
                    inverse=bool(spec["inverse"]),
                )
                mode = "expanding-hybrid"
            else:
                score = _fixed_anchor_score(numeric, float(spec["safe"]), float(spec["stress"]))
                mode = "fixed-anchor-bootstrap"
            components.append(
                {
                    "id": name,
                    "value": round(numeric, 4),
                    "score": round(score, 1),
                    "weight": spec["weight"],
                }
            )

        total_weight = sum(float(item["weight"]) for item in components)
        if total_weight <= 0:
            raise KbMarketFundsError(f"{row['date']} KB 시장자금 관찰점수를 계산할 수 없습니다.")
        score = sum(float(item["score"]) * float(item["weight"]) for item in components) / total_weight
        scored.append(
            {
                **row,
                "score": round(max(0.0, min(100.0, score)), 1),
                "scoreMode": mode,
                "scoreComponents": components,
            }
        )
    return scored


CORE_AMOUNT_FIELDS = (
    "customerDeposits",
    "receivables",
    "creditBalance",
    "futuresDeposits",
)
CHANGE_FIELDS = {
    "customerDeposits": "customerDepositsChange",
    "receivables": "receivablesChange",
    "creditBalance": "creditBalanceChange",
    "futuresDeposits": "futuresDepositsChange",
}


def _amounts_in_million(row: dict[str, Any]) -> dict[str, float | None]:
    million = row.get("amountsKrwMillion")
    if isinstance(million, dict):
        return {
            key: (float(value) if isinstance(value, (int, float)) else None)
            for key, value in million.items()
        }
    billion = row.get("amountsKrwBillion") or {}
    return {
        key: (float(value) * 1000 if isinstance(value, (int, float)) else None)
        for key, value in billion.items()
    }


def _refresh_units_and_derived(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed = []
    previous_amounts: dict[str, float | None] | None = None
    for source_row in sorted(rows, key=lambda item: item["date"]):
        row = deepcopy(source_row)
        amounts_million = _amounts_in_million(row)
        amounts_billion = {
            key: (round(value / 1000, 6) if value is not None else None)
            for key, value in amounts_million.items()
        }

        if previous_amounts is not None:
            for amount_field, change_field in CHANGE_FIELDS.items():
                current = amounts_million.get(amount_field)
                previous = previous_amounts.get(amount_field)
                if current is not None and previous is not None:
                    change = current - previous
                    amounts_million[change_field] = change
                    amounts_billion[change_field] = round(change / 1000, 6)

        customer_deposits = amounts_million.get("customerDeposits")
        credit_balance = amounts_million.get("creditBalance")
        receivables = amounts_million.get("receivables")
        rates = row.get("ratesPct") or {}
        row["amountsKrwMillion"] = amounts_million
        row["amountsKrwBillion"] = amounts_billion
        row["derived"] = {
            "creditToDepositsPct": (_ratio(credit_balance, customer_deposits) or 0.0) * 100,
            "receivablesToDepositsPct": (_ratio(receivables, customer_deposits) or 0.0) * 100,
            "customerDepositsChangePct": _change_pct(
                customer_deposits, amounts_million.get("customerDepositsChange")
            ),
            "creditBalanceChangePct": _change_pct(
                credit_balance, amounts_million.get("creditBalanceChange")
            ),
            "receivablesChangePct": _change_pct(
                receivables, amounts_million.get("receivablesChange")
            ),
            "futuresDepositsChangePct": _change_pct(
                amounts_million.get("futuresDeposits"),
                amounts_million.get("futuresDepositsChange"),
            ),
            "bbbAaSpreadPctp": (
                rates.get("corporateBbb3y") - rates.get("corporateAa3y")
                if isinstance(rates.get("corporateBbb3y"), (int, float))
                and isinstance(rates.get("corporateAa3y"), (int, float))
                else None
            ),
            "cpCdSpreadPctp": (
                rates.get("cp91d") - rates.get("cd91d")
                if isinstance(rates.get("cp91d"), (int, float))
                and isinstance(rates.get("cd91d"), (int, float))
                else None
            ),
        }
        row["sourceProviders"] = list(
            dict.fromkeys(row.get("sourceProviders") or ["KB Securities OpenAPI"])
        )
        refreshed.append(row)
        previous_amounts = amounts_million
    return refreshed


def reconcile_kb_with_kofia(
    kofia_row: dict[str, Any],
    kb_row: dict[str, Any],
    *,
    absolute_tolerance_krw_million: float = 1000,
    relative_tolerance_pct: float = 0.1,
) -> dict[str, Any]:
    """동일 기준일의 KB 반올림값이 FreeSIS 고정밀 값과 허용오차 내인지 확인합니다."""
    if kofia_row.get("date") != kb_row.get("date"):
        raise KbMarketFundsError("KB와 FreeSIS 기준일이 달라 동일일 대조를 수행할 수 없습니다.")
    kofia_amounts = _amounts_in_million(kofia_row)
    kb_amounts = _amounts_in_million(kb_row)
    fields = []
    failures = []
    for field in CORE_AMOUNT_FIELDS:
        kofia_value = kofia_amounts.get(field)
        kb_value = kb_amounts.get(field)
        if kofia_value is None or kb_value is None:
            continue
        absolute = abs(kofia_value - kb_value)
        relative = absolute / abs(kofia_value) * 100 if kofia_value else 0.0
        matched = absolute <= absolute_tolerance_krw_million or relative <= relative_tolerance_pct
        detail = {
            "field": field,
            "kofiaKrwMillion": round(kofia_value, 3),
            "kbKrwMillion": round(kb_value, 3),
            "absoluteDifferenceKrwMillion": round(absolute, 3),
            "relativeDifferencePct": round(relative, 6),
            "matched": matched,
        }
        fields.append(detail)
        if not matched:
            failures.append(detail)
    if not fields:
        raise KbMarketFundsError("KB와 FreeSIS에서 대조 가능한 공통 금액 필드를 찾지 못했습니다.")
    if failures:
        labels = ", ".join(item["field"] for item in failures)
        raise KbMarketFundsReconciliationError(
            f"KB와 FreeSIS 동일일 금액 대조가 허용오차를 벗어났습니다: {labels}",
            {
                "status": "failed",
                "overlapDate": kofia_row["date"],
                "absoluteToleranceKrwMillion": absolute_tolerance_krw_million,
                "relativeTolerancePct": relative_tolerance_pct,
                "fields": fields,
                "failedFields": [item["field"] for item in failures],
            },
        )
    return {
        "status": "matched",
        "overlapDate": kofia_row["date"],
        "absoluteToleranceKrwMillion": absolute_tolerance_krw_million,
        "relativeTolerancePct": relative_tolerance_pct,
        "fields": fields,
    }


def _merge_source_rows(
    existing: dict[str, Any] | None,
    kofia_rows: list[dict[str, Any]],
    kb_snapshot: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows_by_date = {
        str(row.get("date")): deepcopy(row)
        for row in (existing or {}).get("series", [])
        if isinstance(row, dict) and row.get("date")
    }
    if (existing or {}).get("schemaVersion") == 1:
        for row in rows_by_date.values():
            row["sourceProviders"] = ["KB Securities OpenAPI"]
            row["sourceSnapshots"] = {
                "kb": {
                    "date": row.get("date"),
                    "retrievedAt": row.get("retrievedAt"),
                    "amountsKrwMillion": _amounts_in_million(row),
                    "ratesPct": deepcopy(row.get("ratesPct") or {}),
                }
            }
    for row in kofia_rows:
        observed_date = str(row["date"])
        prior = rows_by_date.get(observed_date, {})
        merged = {**prior, **deepcopy(row)}
        merged["ratesPct"] = prior.get("ratesPct") or row.get("ratesPct") or {}
        providers = ["KOFIA FreeSIS"]
        prior_providers = prior.get("sourceProviders") or []
        if "KB Securities OpenAPI" in prior_providers:
            providers.append("KB Securities OpenAPI")
        merged["sourceProviders"] = providers
        rows_by_date[observed_date] = merged

    reconciliation = {
        "status": "not-requested" if kb_snapshot is None else "no-overlap",
        "overlapDate": None,
        "fields": [],
    }
    if kb_snapshot is not None:
        observed_date = str(kb_snapshot["date"])
        if observed_date in rows_by_date and "KOFIA FreeSIS" in (
            rows_by_date[observed_date].get("sourceProviders") or []
        ):
            kofia_row = rows_by_date[observed_date]
            reconciliation = reconcile_kb_with_kofia(kofia_row, kb_snapshot)
            kofia_million = _amounts_in_million(kofia_row)
            kb_million = _amounts_in_million(kb_snapshot)
            for field, value in kb_million.items():
                if field not in CORE_AMOUNT_FIELDS and field not in CHANGE_FIELDS.values():
                    kofia_million[field] = value
            kofia_row["amountsKrwMillion"] = kofia_million
            kofia_row["ratesPct"] = deepcopy(kb_snapshot.get("ratesPct") or {})
            kofia_row["retrievedAt"] = max(
                str(kofia_row.get("retrievedAt") or ""),
                str(kb_snapshot.get("retrievedAt") or ""),
            )
            kofia_row["sourceProviders"] = ["KOFIA FreeSIS", "KB Securities OpenAPI"]
            kofia_row.setdefault("sourceSnapshots", {})["kb"] = {
                "date": kb_snapshot.get("date"),
                "retrievedAt": kb_snapshot.get("retrievedAt"),
                "amountsKrwMillion": _amounts_in_million(kb_snapshot),
                "ratesPct": deepcopy(kb_snapshot.get("ratesPct") or {}),
            }
        else:
            kb_row = deepcopy(kb_snapshot)
            kb_row.setdefault("sourceSnapshots", {})["kb"] = {
                "date": kb_snapshot.get("date"),
                "retrievedAt": kb_snapshot.get("retrievedAt"),
                "amountsKrwMillion": _amounts_in_million(kb_snapshot),
                "ratesPct": deepcopy(kb_snapshot.get("ratesPct") or {}),
            }
            rows_by_date[observed_date] = kb_row

    return list(rows_by_date.values()), reconciliation


def build_market_funds_payload(
    existing: dict[str, Any] | None,
    *,
    kofia_rows: list[dict[str, Any]] | None = None,
    kb_snapshot: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    source_status: dict[str, str] | None = None,
    source_errors: dict[str, str] | None = None,
    history_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """FreeSIS 과거 원장과 KB 최신값을 검증·병합해 공개 산출물을 만듭니다."""
    merged_rows, reconciliation = _merge_source_rows(
        existing, kofia_rows or [], kb_snapshot
    )
    refreshed = _refresh_units_and_derived(merged_rows)
    if not refreshed:
        raise KbMarketFundsError("시장자금 원장에 게시 가능한 관측치가 없습니다.")
    rows = score_market_funds_series(refreshed)[-1400:]
    generated_at = generated_at or datetime.now(KST)
    source_status = source_status or {}
    source_errors = source_errors or {}
    successful = [status for status in source_status.values() if status == "direct"]
    fetch_status = "direct" if successful else "stale-fallback"
    error_text = " | ".join(
        f"{source}: {message}" for source, message in source_errors.items() if message
    ) or None
    ledger_diagnostics = {
        **(history_diagnostics or {}),
        "ledgerRows": len(rows),
        "ledgerFirstDate": rows[0]["date"],
        "ledgerLastDate": rows[-1]["date"],
    }
    return {
        "schemaVersion": 2,
        "name": "국내 증시주변자금 원장",
        "generatedAt": generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "fetchStatus": fetch_status,
        "lastFetchError": error_text,
        "sourceStatus": {
            "kofiaHistory": source_status.get("kofia", "not-requested"),
            "kbLatest": source_status.get("kb", "not-requested"),
        },
        "sourceErrors": source_errors,
        "source": {
            "provider": "금융투자협회 FreeSIS + KB증권 OpenAPI",
            "historyProvider": "금융투자협회 FreeSIS",
            "historyServices": ["STATSCU0100000060", "STATSCU0100000070"],
            "historyUrl": "https://freesis.kofia.or.kr/stat/main.do",
            "latestProvider": "KB증권 OpenAPI",
            "latestApi": "IVA10370",
            "latestEndpoint": MARKET_FUNDS_PATH,
            "latestUrl": "https://openapi.kbsec.com/apidoc_b2c",
            "frequency": "일별",
            "amountUnit": "KRW million",
            "amountUnitNote": "FreeSIS 원장은 백만원, KB 최종일은 십억원을 백만원으로 환산해 동일일 대조합니다.",
            "selectionRule": "동일일 핵심 금액은 FreeSIS 고정밀 값 우선, KB는 최신일·부가 금리 보강",
        },
        "historyDiagnostics": ledger_diagnostics,
        "reconciliation": reconciliation,
        "latest": rows[-1],
        "series": rows,
        "methodology": {
            "role": "observation",
            "aggregateWeight": 0,
            "components": [
                {"id": "creditToDepositsPct", "weight": 0.45, "meaning": "신용잔고/고객예탁금"},
                {"id": "creditBalanceChangePct", "weight": 0.25, "meaning": "신용잔고 일간 증감률"},
                {"id": "receivablesToDepositsPct", "weight": 0.20, "meaning": "미수금/고객예탁금"},
                {"id": "customerDepositsChangePct", "weight": 0.10, "meaning": "고객예탁금 일간 감소"},
            ],
            "normalization": "최소 60개부터 각 시점까지의 expanding 분위수 40%·z 30%·robust z 30%",
            "bootstrap": "60개 미만은 고정 위험구간",
            "leakageControl": "날짜 t의 점수는 t까지 저장된 관측치만 사용",
            "reconciliation": "동일일 핵심 금액은 절대 10억원 또는 상대 0.1% 이내일 때만 결합",
        },
        "limitations": [
            "FreeSIS 예탁금·신용융자는 결제일·집계 기준의 일별 확정치로 장중 수급과 시차가 있습니다.",
            "KB API는 최종일 한 건만 제공하며 FreeSIS보다 늦게 갱신될 수 있습니다.",
            "신용잔고 감소는 부담 완화일 수도 있고 급락 중 강제 청산 결과일 수도 있어 가격·breadth와 함께 봅니다.",
            "가중치 0의 관찰지표이며 OOS 검증 전에는 종합점수에 반영하지 않습니다.",
        ],
    }


def update_market_funds_payload(
    existing: dict[str, Any] | None,
    snapshot: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    fetch_status: str = "direct",
    last_fetch_error: str | None = None,
) -> dict[str, Any]:
    """기존 호출부 호환용 KB 최종일 병합 함수입니다."""
    return build_market_funds_payload(
        existing,
        kb_snapshot=snapshot,
        generated_at=generated_at,
        source_status={"kb": fetch_status},
        source_errors={"kb": last_fetch_error} if last_fetch_error else {},
    )


class KbOpenApiClient:
    """주문 기능을 포함하지 않는 KB증권 시장 집계 전용 읽기 클라이언트입니다."""

    def __init__(self, config: KbOpenApiConfig) -> None:
        if not config.app_key or not config.app_secret:
            raise ValueError("KB OpenAPI app key와 app secret이 필요합니다.")
        self.config = config
        self._token = ""

    def _safe_message(self, message: object) -> str:
        safe = str(message)[:240]
        for secret in (self.config.app_key, self.config.app_secret, self._token):
            if secret:
                safe = safe.replace(secret, "<credential>")
        return safe

    def _post(self, path: str, data_body: dict[str, object], headers: dict[str, str]) -> dict[str, Any]:
        request_body = json.dumps(
            {
                "dataHeader": {
                    "ipAddr": self.config.ip_addr,
                    "macAddr": self.config.mac_addr,
                },
                "dataBody": data_body,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}{path}",
            data=request_body,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(max(1, self.config.retry_count)):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise KbMarketFundsError("KB OpenAPI 응답이 JSON 객체가 아닙니다.")
                return payload
            except KbMarketFundsError:
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                if attempt + 1 < max(1, self.config.retry_count):
                    time.sleep(1.5 * (attempt + 1))
        raise KbMarketFundsError(
            f"KB OpenAPI 연결에 실패했습니다 ({type(last_error).__name__}: {self._safe_message(last_error)})."
        ) from None

    def _access_token(self) -> str:
        payload = self._post(
            TOKEN_PATH,
            {
                "appKey": self.config.app_key,
                "appSecret": self.config.app_secret,
                "grantType": "client_credentials",
            },
            {},
        )
        body = payload.get("dataBody", payload)
        if not isinstance(body, dict):
            raise KbMarketFundsError("KB OpenAPI 인증 응답 형식이 올바르지 않습니다.")
        token = body.get("access_token") or body.get("accessToken")
        if not isinstance(token, str) or not token:
            raise KbMarketFundsError("KB OpenAPI Access Token을 발급받지 못했습니다.")
        self._token = token
        return token

    def fetch_market_funds(self) -> dict[str, Any]:
        """증시주변자금동향 최종일 시장 집계값을 조회합니다."""
        token = self._access_token()
        payload = self._post(
            MARKET_FUNDS_PATH,
            {},
            {
                "appKey": self.config.app_key,
                "Authorization": f"bearer {token}",
            },
        )
        return parse_market_funds_response(payload)


def load_existing_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KbMarketFundsError(f"기존 KB 시장자금 파일을 읽지 못했습니다: {error}") from None
    if not isinstance(payload, dict):
        raise KbMarketFundsError("기존 KB 시장자금 파일 형식이 올바르지 않습니다.")
    return payload
