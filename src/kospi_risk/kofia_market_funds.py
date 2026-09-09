from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.cookiejar import CookieJar
from typing import Any


KST = timezone(timedelta(hours=9))
FREESIS_BASE_URL = "https://freesis.kofia.or.kr"
FUNDS_SERVICE_ID = "STATSCU0100000060"
CREDIT_SERVICE_ID = "STATSCU0100000070"
FUNDS_OBJECT_NAME = f"{FUNDS_SERVICE_ID}BO"
CREDIT_OBJECT_NAME = f"{CREDIT_SERVICE_ID}BO"
AMOUNT_DIVISOR_KRW_MILLION = "1000000"


class KofiaMarketFundsError(RuntimeError):
    """인증정보를 포함하지 않는 금융투자협회 FreeSIS 조회 오류입니다."""


@dataclass(frozen=True)
class KofiaFreeSisConfig:
    base_url: str = FREESIS_BASE_URL
    timeout_seconds: int = 25
    retry_count: int = 3
    request_delay_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not self.base_url.lower().startswith("https://"):
            raise ValueError("FreeSIS base URL은 HTTPS여야 합니다.")
        if self.timeout_seconds <= 0:
            raise ValueError("FreeSIS timeout_seconds는 양수여야 합니다.")
        if self.retry_count <= 0:
            raise ValueError("FreeSIS retry_count는 양수여야 합니다.")
        if self.request_delay_seconds < 0:
            raise ValueError("FreeSIS request_delay_seconds는 0 이상이어야 합니다.")


def _parse_date(value: object, *, field: str) -> date:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise KofiaMarketFundsError(f"FreeSIS {field}가 YYYYMMDD 형식이 아닙니다.") from None


def _amount_krw_million(value: object, *, field: str) -> float:
    text = str(value or "").strip().replace(",", "")
    try:
        amount = float(text)
    except ValueError:
        raise KofiaMarketFundsError(f"FreeSIS {field} 값이 숫자가 아닙니다.") from None
    if not math.isfinite(amount) or amount < 0:
        raise KofiaMarketFundsError(f"FreeSIS {field} 값이 유효하지 않습니다.")
    if amount > 500_000_000:
        raise KofiaMarketFundsError(
            f"FreeSIS {field} 값이 백만원 단위 범위를 벗어났습니다. tmpV40 배율을 확인하세요."
        )
    return amount


def parse_freesis_funds_rows(
    payload: dict[str, Any],
    *,
    retrieved_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """증시자금추이 응답에서 예탁금·선물예수금·미수금을 읽습니다."""
    raw_rows = payload.get("ds1")
    if not isinstance(raw_rows, list):
        raise KofiaMarketFundsError("FreeSIS 증시자금추이 응답에 ds1 목록이 없습니다.")
    retrieved_at = retrieved_at or datetime.now(KST)
    retrieved_text = retrieved_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        observed = _parse_date(raw.get("TMPV1"), field="증시자금 기준일")
        amounts = {
            "customerDeposits": _amount_krw_million(
                raw.get("TMPV2"), field="투자자예탁금"
            ),
            "futuresDeposits": _amount_krw_million(
                raw.get("TMPV3"), field="장내파생상품 거래예수금"
            ),
            "receivables": _amount_krw_million(raw.get("TMPV5"), field="위탁매매 미수금"),
        }
        rows.append(
            {
                "date": observed.isoformat(),
                "retrievedAt": retrieved_text,
                "amountsKrwMillion": amounts,
                "sourceProviders": ["KOFIA FreeSIS"],
            }
        )
    if not rows:
        raise KofiaMarketFundsError("FreeSIS 증시자금추이 응답에 유효한 행이 없습니다.")
    return rows


def parse_freesis_credit_rows(
    payload: dict[str, Any],
    *,
    retrieved_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """신용공여 잔고 추이 응답에서 시장 전체 신용거래융자를 읽습니다."""
    raw_rows = payload.get("ds1")
    if not isinstance(raw_rows, list):
        raise KofiaMarketFundsError("FreeSIS 신용공여 응답에 ds1 목록이 없습니다.")
    retrieved_at = retrieved_at or datetime.now(KST)
    retrieved_text = retrieved_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    rows = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        observed = _parse_date(raw.get("TMPV1"), field="신용공여 기준일")
        rows.append(
            {
                "date": observed.isoformat(),
                "retrievedAt": retrieved_text,
                "amountsKrwMillion": {
                    "creditBalance": _amount_krw_million(
                        raw.get("TMPV2"), field="신용거래융자 전체"
                    )
                },
                "sourceProviders": ["KOFIA FreeSIS"],
            }
        )
    if not rows:
        raise KofiaMarketFundsError("FreeSIS 신용공여 응답에 유효한 행이 없습니다.")
    return rows


def merge_freesis_rows(
    funds_rows: list[dict[str, Any]],
    credit_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """두 FreeSIS 서비스의 공통 영업일만 사용해 완전한 시장자금 행을 만듭니다."""
    funds_by_date = {row["date"]: row for row in funds_rows}
    credit_by_date = {row["date"]: row for row in credit_rows}
    common_dates = sorted(set(funds_by_date) & set(credit_by_date))
    rows = []
    for observed_date in common_dates:
        fund_row = funds_by_date[observed_date]
        credit_row = credit_by_date[observed_date]
        amounts = {
            **fund_row["amountsKrwMillion"],
            **credit_row["amountsKrwMillion"],
        }
        rows.append(
            {
                "date": observed_date,
                "retrievedAt": max(fund_row["retrievedAt"], credit_row["retrievedAt"]),
                "amountsKrwMillion": amounts,
                "amountsKrwBillion": {
                    key: round(float(value) / 1000, 6) for key, value in amounts.items()
                },
                "sourceProviders": ["KOFIA FreeSIS"],
            }
        )
    if not rows:
        raise KofiaMarketFundsError("FreeSIS 두 서비스에 공통 기준일이 없습니다.")
    diagnostics = {
        "fundsRows": len(funds_rows),
        "creditRows": len(credit_rows),
        "mergedRows": len(rows),
        "fundsOnlyDates": sorted(set(funds_by_date) - set(credit_by_date)),
        "creditOnlyDates": sorted(set(credit_by_date) - set(funds_by_date)),
        "firstDate": rows[0]["date"],
        "lastDate": rows[-1]["date"],
    }
    return rows, diagnostics


def five_year_start(reference_date: date) -> date:
    """윤년을 고려한 5년 전 날짜를 반환합니다."""
    try:
        return reference_date.replace(year=reference_date.year - 5)
    except ValueError:
        return reference_date.replace(year=reference_date.year - 5, day=28)


def incremental_start_date(
    existing: dict[str, Any] | None,
    *,
    reference_date: date,
    overlap_days: int = 14,
) -> date:
    """최초에는 5년, 이후에는 기존 FreeSIS 마지막 날과 겹치게 다시 조회합니다."""
    dates = []
    for row in (existing or {}).get("series", []):
        if not isinstance(row, dict) or "KOFIA FreeSIS" not in (
            row.get("sourceProviders") or []
        ):
            continue
        try:
            dates.append(date.fromisoformat(str(row.get("date"))))
        except ValueError:
            continue
    if not dates:
        return five_year_start(reference_date)
    return max(five_year_start(reference_date), max(dates) - timedelta(days=overlap_days))


class KofiaFreeSisClient:
    """FreeSIS 공개 시장 집계만 조회하는 읽기 전용 클라이언트입니다."""

    def __init__(self, config: KofiaFreeSisConfig | None = None) -> None:
        self.config = config or KofiaFreeSisConfig()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        self._session_ready = False

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _request(self, request: urllib.request.Request, *, expect_json: bool) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count):
            try:
                if self.config.request_delay_seconds:
                    time.sleep(self.config.request_delay_seconds)
                with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                    raw = response.read()
                if not expect_json:
                    return raw
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise KofiaMarketFundsError("FreeSIS 응답이 JSON 객체가 아닙니다.")
                return payload
            except KofiaMarketFundsError:
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                if attempt + 1 < self.config.retry_count:
                    time.sleep(1.5 * (attempt + 1))
        raise KofiaMarketFundsError(
            f"FreeSIS 연결에 실패했습니다 ({type(last_error).__name__}: {last_error})."
        ) from None

    def _open_session(self) -> None:
        if self._session_ready:
            return
        query = urllib.parse.urlencode(
            {
                "parentDivId": "MSIS10000000000000",
                "serviceId": FUNDS_SERVICE_ID,
            }
        )
        request = urllib.request.Request(
            self._url(f"/stat/FreeSIS.do?{query}"),
            headers={"User-Agent": "market-lab/1.0"},
        )
        self._request(request, expect_json=False)
        self._session_ready = True

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "market-lab/1.0",
                "Referer": self._url("/stat/main.do"),
            },
            method="POST",
        )
        return self._request(request, expect_json=True)

    def _latest_date(self, service_id: str) -> date:
        payload = self._post_json(
            "/meta/getSrvData.do",
            {
                "dmSearchData": {
                    "strSvrId": service_id,
                    "strDivId": "",
                    "app_peron_yn": "Y",
                    "language_gb": "KOR",
                    "strGetCode": "N",
                }
            },
        )
        for row in payload.get("dsLatestDate") or []:
            if isinstance(row, dict) and row.get("TMPV1") == "RD":
                return _parse_date(row.get("TMPV2"), field=f"{service_id} 최신일")
        raise KofiaMarketFundsError(f"FreeSIS {service_id} 최신 일별 기준일이 없습니다.")

    def _fetch_service(
        self,
        service_id: str,
        object_name: str,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        return self._post_json(
            "/meta/getMetaDataList.do",
            {
                "dmSearch": {
                    "tmpV1": "D",
                    "tmpV45": start_date.strftime("%Y%m%d"),
                    "tmpV46": end_date.strftime("%Y%m%d"),
                    # 화면 코드 "06"이 아니라 백만원 환산 배율 자체를 전달해야 합니다.
                    "tmpV40": AMOUNT_DIVISOR_KRW_MILLION,
                    "tmpV41": "",
                    "OBJ_NM": object_name,
                }
            },
        )

    def fetch_history(
        self,
        *,
        start_date: date,
        end_date: date,
        retrieved_at: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """예탁금·미수금·신용잔고의 공통 일별 시계열을 조회합니다."""
        if start_date > end_date:
            raise ValueError("FreeSIS 조회 시작일은 종료일보다 늦을 수 없습니다.")
        self._open_session()
        funds_latest = self._latest_date(FUNDS_SERVICE_ID)
        credit_latest = self._latest_date(CREDIT_SERVICE_ID)
        effective_end = min(end_date, funds_latest, credit_latest)
        if start_date > effective_end:
            raise KofiaMarketFundsError(
                f"FreeSIS 조회 시작일 {start_date}이 공통 최신일 {effective_end}보다 늦습니다."
            )
        funds_payload = self._fetch_service(
            FUNDS_SERVICE_ID,
            FUNDS_OBJECT_NAME,
            start_date=start_date,
            end_date=effective_end,
        )
        credit_payload = self._fetch_service(
            CREDIT_SERVICE_ID,
            CREDIT_OBJECT_NAME,
            start_date=start_date,
            end_date=effective_end,
        )
        funds_rows = parse_freesis_funds_rows(funds_payload, retrieved_at=retrieved_at)
        credit_rows = parse_freesis_credit_rows(credit_payload, retrieved_at=retrieved_at)
        rows, diagnostics = merge_freesis_rows(funds_rows, credit_rows)
        diagnostics.update(
            {
                "requestedStartDate": start_date.isoformat(),
                "requestedEndDate": end_date.isoformat(),
                "fundsLatestDate": funds_latest.isoformat(),
                "creditLatestDate": credit_latest.isoformat(),
                "effectiveEndDate": effective_end.isoformat(),
                "amountUnit": "KRW million",
                "amountDivisor": int(AMOUNT_DIVISOR_KRW_MILLION),
            }
        )
        return rows, diagnostics
