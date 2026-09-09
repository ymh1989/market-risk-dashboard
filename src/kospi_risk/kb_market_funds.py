from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
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
        "ratesPct": rates,
        "derived": derived,
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


def update_market_funds_payload(
    existing: dict[str, Any] | None,
    snapshot: dict[str, Any],
    *,
    generated_at: datetime | None = None,
    fetch_status: str = "direct",
    last_fetch_error: str | None = None,
) -> dict[str, Any]:
    """기존 일별 기록에 새 최종일을 병합하고 날짜 중복을 제거합니다."""
    rows_by_date = {
        str(row.get("date")): row
        for row in (existing or {}).get("series", [])
        if isinstance(row, dict) and row.get("date")
    }
    rows_by_date[snapshot["date"]] = snapshot
    rows = score_market_funds_series(list(rows_by_date.values()))[-1100:]
    generated_at = generated_at or datetime.now(KST)
    return {
        "schemaVersion": 1,
        "name": "KB 증시주변자금동향",
        "generatedAt": generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "fetchStatus": fetch_status,
        "lastFetchError": last_fetch_error,
        "source": {
            "provider": "KB증권 OpenAPI",
            "api": "IVA10370",
            "endpoint": MARKET_FUNDS_PATH,
            "frequency": "일별 최종일",
            "amountUnit": "KRW billion",
            "amountUnitNote": "금액 원천값은 십억원 단위로 보존하며 비율 산식은 단위에 영향을 받지 않습니다.",
        },
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
            "bootstrap": "60개 미만은 고정 위험구간, 이후에는 각 시점까지의 expanding 혼합 정규화",
            "leakageControl": "날짜 t의 점수는 t까지 저장된 관측치만 사용",
        },
        "limitations": [
            "KB API는 최종일 한 건만 제공하므로 연결일 이후부터 시계열이 누적됩니다.",
            "신용잔고 감소는 부담 완화일 수도 있고 급락 중 강제 청산 결과일 수도 있어 가격·breadth와 함께 봅니다.",
            "가중치 0의 관찰지표이며 충분한 OOS 검증 전에는 종합점수에 반영하지 않습니다.",
        ],
    }


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
