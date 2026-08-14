from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .data_loader import load_frame, save_frame


LOGGER = logging.getLogger(__name__)
KOSPI_INDEX_TICKER = "1001"
BREADTH_COUNT_COLUMNS = ["date", "up", "down", "flat", "total"]
BREADTH_CONTEXT_COLUMNS = ["samsung_return", "hynix_return"]
BREADTH_DAILY_COLUMNS = BREADTH_COUNT_COLUMNS + BREADTH_CONTEXT_COLUMNS
INVESTOR_FLOW_COLUMNS = [
    "foreign_net_buy_value",
    "institution_net_buy_value",
    "financial_investment_net_buy_value",
    "pension_net_buy_value",
]
PROGRAM_FLOW_COLUMNS = ["program_net_buy_value"]
PROGRAM_FLOW_BACKFILL_OBSERVATIONS = 80
DIRECT_FLOW_COLUMNS = INVESTOR_FLOW_COLUMNS + PROGRAM_FLOW_COLUMNS
BREADTH_OUTPUT_COLUMNS = [
    "date",
    "kospi_close",
    "kospi_return",
    "samsung_return",
    "hynix_return",
    "up",
    "down",
    "flat",
    "total",
    "net_breadth",
    "ad_ratio",
    "breadth_pct",
    "up_ratio",
    "down_ratio",
    "AD_line",
    "AD_ma5",
    "AD_ma20",
    "breadth_ma5",
    "breadth_ma20",
]

OhlcvFetcher = Callable[[str], pd.DataFrame]
IndexFetcher = Callable[[str, str], pd.DataFrame]
InvestorFlowFetcher = Callable[[str, str], pd.DataFrame]
ProgramFlowFetcher = Callable[[str], pd.DataFrame]


def _as_timestamp(value: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    parsed = pd.Timestamp(value).normalize()
    if pd.isna(parsed):
        raise ValueError(f"유효한 날짜가 아닙니다: {value}")
    return parsed


def _date_key(value: str | date | datetime | pd.Timestamp) -> str:
    return _as_timestamp(value).strftime("%Y%m%d")


def _load_pykrx_stock():
    try:
        from pykrx import stock
    except ImportError as error:
        raise RuntimeError(
            "pykrx가 설치되어 있지 않습니다. `pip install pykrx` 후 다시 실행하세요."
        ) from error
    except Exception as error:
        raise RuntimeError(
            "pykrx KRX 로그인 또는 세션 초기화에 실패했습니다. 잠시 뒤 다시 시도하세요."
        ) from error
    return stock


def _require_krx_credentials() -> None:
    missing = [
        name for name in ["KRX_ID", "KRX_PW"] if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "KOSPI 전 종목 KRX 조회에는 환경변수가 필요합니다: "
            f"{', '.join(missing)}. 저장소 .env에 입력하거나 실행 환경에 설정하세요."
        )


def _default_ohlcv_fetcher(date_key: str) -> pd.DataFrame:
    stock = _load_pykrx_stock()
    try:
        return stock.get_market_ohlcv(date_key, market="KOSPI")
    except (AttributeError, TypeError):
        if hasattr(stock, "get_market_ohlcv_by_ticker"):
            return stock.get_market_ohlcv_by_ticker(date_key, market="KOSPI")
        raise


def _default_index_fetcher(start_key: str, end_key: str) -> pd.DataFrame:
    stock = _load_pykrx_stock()
    if hasattr(stock, "get_index_ohlcv_by_date"):
        return stock.get_index_ohlcv_by_date(start_key, end_key, KOSPI_INDEX_TICKER)
    return stock.get_index_ohlcv(start_key, end_key, KOSPI_INDEX_TICKER)


def _default_investor_flow_fetcher(start_key: str, end_key: str) -> pd.DataFrame:
    stock = _load_pykrx_stock()
    return stock.get_market_trading_value_by_date(
        start_key,
        end_key,
        "KOSPI",
        on="순매수",
        detail=True,
    )


def _default_program_flow_fetcher(date_key: str) -> pd.DataFrame:
    try:
        from pykrx.website.krx.krxio import KrxWebIo
    except ImportError as error:
        raise RuntimeError("pykrx KRX 프로그램매매 인터페이스를 불러올 수 없습니다.") from error
    except Exception as error:
        raise RuntimeError(
            "KRX 프로그램매매 로그인 또는 세션 초기화에 실패했습니다."
        ) from error

    class ProgramTradingQuery(KrxWebIo):
        @property
        def bld(self) -> str:
            return "dbms/MDC/STAT/standard/MDCSTAT02601"

        def fetch(self, observed_key: str) -> dict:
            return self.read(
                strtDd=observed_key,
                endDd=observed_key,
                mktId="STK",
            )

    try:
        response = ProgramTradingQuery().fetch(date_key)
    except Exception as error:
        raise RuntimeError(
            "KRX 프로그램매매가 JSON을 반환하지 않았습니다. 세션 또는 호출 제한을 확인하세요."
        ) from error
    if not isinstance(response, dict):
        raise RuntimeError("KRX 프로그램매매 응답 형식이 올바르지 않습니다.")
    return pd.DataFrame(response.get("output", []))


def _column(frame: pd.DataFrame, candidates: list[str]) -> pd.Series:
    for candidate in candidates:
        if candidate in frame.columns:
            return pd.to_numeric(frame[candidate], errors="coerce")
    raise ValueError(f"필수 컬럼이 없습니다: {', '.join(candidates)}")


def _normalize_daily_universe(frame: pd.DataFrame, observed_date: pd.Timestamp) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["date", "ticker", "close", "change_pct"])

    close = _column(frame, ["종가", "close", "Close"])
    change_pct = _column(frame, ["등락률", "등락율", "change_pct", "Change"])
    ticker = pd.Series(frame.index.astype(str), index=frame.index)
    normalized = pd.DataFrame(
        {
            "date": observed_date,
            "ticker": ticker.to_numpy(),
            "close": close.to_numpy(),
            "change_pct": change_pct.to_numpy(),
        }
    )
    return normalized.loc[
        normalized["close"].notna()
        & normalized["change_pct"].notna()
        & (normalized["close"] > 0)
    ].reset_index(drop=True)


def _write_raw_daily(frame: pd.DataFrame, raw_output_dir: Path, observed_date: pd.Timestamp) -> None:
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    save_frame(frame, raw_output_dir / f"{observed_date.date().isoformat()}.parquet")


def fetch_kospi_breadth(
    start_date: str | date | datetime | pd.Timestamp,
    end_date: str | date | datetime | pd.Timestamp,
    *,
    sleep_seconds: float = 0.4,
    retries: int = 3,
    retry_backoff: float = 1.8,
    ohlcv_fetcher: OhlcvFetcher | None = None,
    raw_output_dir: str | Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """pykrx로 KOSPI 전 종목을 조회해 거래일별 상승·하락 종목 수를 집계합니다."""

    start = _as_timestamp(start_date)
    end = _as_timestamp(end_date)
    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if retries < 1:
        raise ValueError("retries는 1 이상이어야 합니다.")

    if ohlcv_fetcher is None:
        _require_krx_credentials()
        _load_pykrx_stock()
    fetcher = ohlcv_fetcher or _default_ohlcv_fetcher
    raw_dir = Path(raw_output_dir) if raw_output_dir else None
    rows: list[dict[str, object]] = []
    failed_dates: list[dict[str, str]] = []
    empty_dates: list[str] = []

    for observed_date in pd.bdate_range(start, end):
        date_key = observed_date.strftime("%Y%m%d")
        daily_frame: pd.DataFrame | None = None
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                daily_frame = fetcher(date_key)
                last_error = None
                break
            except Exception as error:
                last_error = error
                LOGGER.warning(
                    "KOSPI breadth 조회 실패 · %s · %s/%s회 · %s",
                    observed_date.date().isoformat(),
                    attempt,
                    retries,
                    error,
                )
                if attempt < retries:
                    sleep_fn(max(0.0, sleep_seconds) * retry_backoff ** (attempt - 1))

        if last_error is not None:
            failed_dates.append(
                {"date": observed_date.date().isoformat(), "error": str(last_error)}
            )
            continue
        if daily_frame is None or daily_frame.empty:
            empty_dates.append(observed_date.date().isoformat())
            LOGGER.info("KOSPI 휴장 또는 빈 응답 · %s", observed_date.date().isoformat())
            if sleep_seconds > 0:
                sleep_fn(sleep_seconds)
            continue

        try:
            universe = _normalize_daily_universe(daily_frame, observed_date)
        except Exception as error:
            failed_dates.append(
                {"date": observed_date.date().isoformat(), "error": str(error)}
            )
            LOGGER.warning("KOSPI breadth 컬럼 정규화 실패 · %s · %s", observed_date.date(), error)
            continue
        if universe.empty:
            empty_dates.append(observed_date.date().isoformat())
            continue

        if raw_dir is not None:
            _write_raw_daily(universe, raw_dir, observed_date)
        change = universe["change_pct"]
        returns_by_ticker = universe.set_index("ticker")["change_pct"].div(100.0)
        up = int((change > 0).sum())
        down = int((change < 0).sum())
        flat = int((change == 0).sum())
        rows.append(
            {
                "date": observed_date,
                "up": up,
                "down": down,
                "flat": flat,
                "total": up + down + flat,
                "samsung_return": returns_by_ticker.get("005930", np.nan),
                "hynix_return": returns_by_ticker.get("000660", np.nan),
            }
        )
        if sleep_seconds > 0:
            sleep_fn(sleep_seconds)

    result = pd.DataFrame(rows, columns=BREADTH_DAILY_COLUMNS)
    result.attrs["failed_dates"] = failed_dates
    result.attrs["empty_dates"] = empty_dates
    result.attrs["requested_start_date"] = start.date().isoformat()
    result.attrs["requested_end_date"] = end.date().isoformat()
    return result


def calculate_breadth_metrics(daily_counts: pd.DataFrame) -> pd.DataFrame:
    """일별 종목 수에서 breadth 비율, AD Line과 이동평균을 계산합니다."""

    missing = sorted(set(BREADTH_COUNT_COLUMNS) - set(daily_counts.columns))
    if missing:
        raise ValueError(f"breadth 계산 필수 컬럼 누락: {', '.join(missing)}")
    context_columns = [
        column for column in BREADTH_CONTEXT_COLUMNS if column in daily_counts.columns
    ]
    frame = daily_counts[BREADTH_COUNT_COLUMNS + context_columns].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").reset_index(drop=True)
    for column in ["up", "down", "flat", "total"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["up", "down", "flat"]).copy()
    frame[["up", "down", "flat"]] = frame[["up", "down", "flat"]].astype(int)
    frame["total"] = frame[["up", "down", "flat"]].sum(axis=1).astype(int)
    for column in context_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    active = frame["up"] + frame["down"]
    frame["net_breadth"] = frame["up"] - frame["down"]
    frame["ad_ratio"] = np.divide(
        frame["up"],
        frame["down"],
        out=np.full(len(frame), np.nan, dtype=float),
        where=frame["down"].to_numpy() != 0,
    )
    for output, numerator in [
        ("breadth_pct", frame["net_breadth"]),
        ("up_ratio", frame["up"]),
        ("down_ratio", frame["down"]),
    ]:
        frame[output] = np.divide(
            numerator,
            active,
            out=np.full(len(frame), np.nan, dtype=float),
            where=active.to_numpy() != 0,
        )

    # AD Line은 저장된 첫 관측일부터 누적하므로 조회 시작일에 따라 절대수준이 달라집니다.
    frame["AD_line"] = frame["net_breadth"].cumsum()
    frame["AD_ma5"] = frame["AD_line"].rolling(5, min_periods=5).mean()
    frame["AD_ma20"] = frame["AD_line"].rolling(20, min_periods=20).mean()
    frame["breadth_ma5"] = frame["breadth_pct"].rolling(5, min_periods=5).mean()
    frame["breadth_ma20"] = frame["breadth_pct"].rolling(20, min_periods=20).mean()
    return frame


def fetch_kospi_index(
    start_date: str | date | datetime | pd.Timestamp,
    end_date: str | date | datetime | pd.Timestamp,
    *,
    retries: int = 3,
    sleep_seconds: float = 0.4,
    retry_backoff: float = 1.8,
    index_fetcher: IndexFetcher | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """같은 기간의 KOSPI 지수 종가와 일간 수익률을 조회합니다."""

    start = _as_timestamp(start_date)
    end = _as_timestamp(end_date)
    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if retries < 1:
        raise ValueError("retries는 1 이상이어야 합니다.")
    if index_fetcher is None:
        _require_krx_credentials()
        _load_pykrx_stock()
    fetcher = index_fetcher or _default_index_fetcher
    last_error: Exception | None = None
    raw: pd.DataFrame | None = None
    for attempt in range(1, retries + 1):
        try:
            raw = fetcher(_date_key(start), _date_key(end))
            last_error = None
            break
        except Exception as error:
            last_error = error
            LOGGER.warning("KOSPI 지수 조회 실패 · %s/%s회 · %s", attempt, retries, error)
            if attempt < retries:
                sleep_fn(max(0.0, sleep_seconds) * retry_backoff ** (attempt - 1))
    if last_error is not None:
        raise RuntimeError(f"KOSPI 지수 조회에 실패했습니다: {last_error}") from last_error
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "kospi_close", "kospi_return"])

    close = _column(raw, ["종가", "close", "Close"])
    dates = pd.to_datetime(raw.index, errors="coerce")
    frame = pd.DataFrame({"date": dates, "kospi_close": close.to_numpy()})
    frame = frame.dropna(subset=["date", "kospi_close"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last").reset_index(drop=True)
    frame["kospi_return"] = frame["kospi_close"].pct_change()
    return frame


def _retry_range_fetch(
    fetcher: InvestorFlowFetcher,
    start_key: str,
    end_key: str,
    *,
    retries: int,
    sleep_seconds: float,
    retry_backoff: float,
    sleep_fn: Callable[[float], None],
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fetcher(start_key, end_key)
        except Exception as error:
            last_error = error
            LOGGER.warning(
                "KOSPI 투자자 순매수 조회 실패 · %s/%s회 · %s",
                attempt,
                retries,
                error,
            )
            if attempt < retries:
                sleep_fn(max(0.0, sleep_seconds) * retry_backoff ** (attempt - 1))
    raise RuntimeError(f"KOSPI 투자자 순매수 조회에 실패했습니다: {last_error}") from last_error


def fetch_kospi_investor_flows(
    start_date: str | date | datetime | pd.Timestamp,
    end_date: str | date | datetime | pd.Timestamp,
    *,
    retries: int = 3,
    sleep_seconds: float = 0.4,
    retry_backoff: float = 1.8,
    investor_flow_fetcher: InvestorFlowFetcher | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """KRX에서 외국인·기관 세부 투자자의 KOSPI 일별 순매수 거래대금을 조회합니다."""

    start = _as_timestamp(start_date)
    end = _as_timestamp(end_date)
    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if investor_flow_fetcher is None:
        _require_krx_credentials()
    fetcher = investor_flow_fetcher or _default_investor_flow_fetcher
    raw = _retry_range_fetch(
        fetcher,
        _date_key(start),
        _date_key(end),
        retries=retries,
        sleep_seconds=sleep_seconds,
        retry_backoff=retry_backoff,
        sleep_fn=sleep_fn,
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", *INVESTOR_FLOW_COLUMNS])

    data = raw.copy()
    data.index = pd.to_datetime(data.index, errors="coerce")
    data = data.loc[data.index.notna()]
    institution_candidates = [
        "금융투자",
        "보험",
        "투신",
        "사모",
        "은행",
        "기타금융",
        "연기금",
    ]
    institution_columns = [column for column in institution_candidates if column in data.columns]
    if not institution_columns:
        raise ValueError("KRX 투자자 순매수 응답에 기관 세부 컬럼이 없습니다.")

    if "외국인합계" in data.columns:
        foreign = pd.to_numeric(data["외국인합계"], errors="coerce")
    elif "외국인" in data.columns:
        foreign_columns = [
            column for column in ["외국인", "기타외국인"] if column in data.columns
        ]
        foreign = data[foreign_columns].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    else:
        raise ValueError("KRX 투자자 순매수 응답에 외국인 컬럼이 없습니다.")

    institutions = data[institution_columns].apply(pd.to_numeric, errors="coerce")
    financial_investment = (
        pd.to_numeric(data["금융투자"], errors="coerce")
        if "금융투자" in data.columns
        else pd.Series(np.nan, index=data.index)
    )
    pension = (
        pd.to_numeric(data["연기금"], errors="coerce")
        if "연기금" in data.columns
        else pd.Series(np.nan, index=data.index)
    )
    result = pd.DataFrame(
        {
            "date": data.index,
            "foreign_net_buy_value": foreign.to_numpy(),
            "institution_net_buy_value": institutions.sum(axis=1).to_numpy(),
            "financial_investment_net_buy_value": financial_investment.to_numpy(),
            "pension_net_buy_value": pension.to_numpy(),
        }
    )
    return result.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _normalize_program_flow(raw: pd.DataFrame, observed_date: pd.Timestamp) -> dict[str, object] | None:
    if raw is None or raw.empty:
        return None
    name_column = next(
        (column for column in ["ITM_TP_NM", "구분"] if column in raw.columns),
        None,
    )
    value_column = next(
        (column for column in ["NETBID_TRDVAL", "순매수거래대금"] if column in raw.columns),
        None,
    )
    if name_column is None or value_column is None:
        raise ValueError("KRX 프로그램매매 응답에 구분 또는 순매수 거래대금 컬럼이 없습니다.")
    total = raw.loc[raw[name_column].astype(str).str.strip() == "전체"]
    if total.empty:
        raise ValueError("KRX 프로그램매매 응답에 전체 합계 행이 없습니다.")
    value = pd.to_numeric(
        total.iloc[-1][value_column].replace(",", "")
        if isinstance(total.iloc[-1][value_column], str)
        else total.iloc[-1][value_column],
        errors="coerce",
    )
    if pd.isna(value):
        raise ValueError("KRX 프로그램 순매수 거래대금을 숫자로 변환할 수 없습니다.")
    return {"date": observed_date, "program_net_buy_value": float(value)}


def fetch_kospi_program_flows(
    dates: list[str | date | datetime | pd.Timestamp] | pd.Series | pd.DatetimeIndex,
    *,
    retries: int = 3,
    sleep_seconds: float = 0.25,
    retry_backoff: float = 1.8,
    program_flow_fetcher: ProgramFlowFetcher | None = None,
    max_consecutive_failed_dates: int = 3,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """KRX 프로그램매매 화면에서 거래일별 차익·비차익 합계 순매수를 조회합니다."""

    if program_flow_fetcher is None:
        _require_krx_credentials()
    fetcher = program_flow_fetcher or _default_program_flow_fetcher
    observed_dates = sorted({_as_timestamp(value) for value in dates}, reverse=True)
    rows: list[dict[str, object]] = []
    failed_dates: list[dict[str, str]] = []
    empty_dates: list[str] = []
    consecutive_failed_dates = 0
    for date_index, observed_date in enumerate(observed_dates):
        last_error: Exception | None = None
        raw: pd.DataFrame | None = None
        for attempt in range(1, retries + 1):
            try:
                raw = fetcher(_date_key(observed_date))
                last_error = None
                break
            except Exception as error:
                last_error = error
                LOGGER.warning(
                    "KOSPI 프로그램 순매수 조회 실패 · %s · %s/%s회 · %s",
                    observed_date.date().isoformat(),
                    attempt,
                    retries,
                    error,
                )
                if attempt < retries:
                    sleep_fn(max(0.0, sleep_seconds) * retry_backoff ** (attempt - 1))
        if last_error is not None:
            failed_dates.append(
                {"date": observed_date.date().isoformat(), "error": str(last_error)}
            )
            consecutive_failed_dates += 1
            if consecutive_failed_dates >= max_consecutive_failed_dates:
                remaining = observed_dates[date_index + 1 :]
                if remaining:
                    failed_dates.append(
                        {
                            "date": (
                                f"{min(remaining).date().isoformat()}~"
                                f"{max(remaining).date().isoformat()}"
                            ),
                            "error": "연속 실패로 프로그램매매 조회를 중단했습니다.",
                            "scope": "source-circuit-breaker",
                        }
                    )
                break
            continue
        try:
            row = _normalize_program_flow(raw, observed_date)
        except ValueError as error:
            failed_dates.append(
                {"date": observed_date.date().isoformat(), "error": str(error)}
            )
            consecutive_failed_dates += 1
            if consecutive_failed_dates >= max_consecutive_failed_dates:
                break
            continue
        if row is None:
            empty_dates.append(observed_date.date().isoformat())
        else:
            rows.append(row)
            consecutive_failed_dates = 0
        if sleep_seconds > 0:
            sleep_fn(sleep_seconds)

    result = pd.DataFrame(rows, columns=["date", *PROGRAM_FLOW_COLUMNS])
    result = result.sort_values("date").reset_index(drop=True)
    result.attrs["failed_dates"] = failed_dates
    result.attrs["empty_dates"] = empty_dates
    return result


def _rolling_percentile_last(series: pd.Series, window: int = 252, minimum: int = 60) -> pd.Series:
    def percentile(values: np.ndarray) -> float:
        if len(values) == 0 or not np.isfinite(values[-1]):
            return np.nan
        finite = values[np.isfinite(values)]
        if len(finite) == 0:
            return np.nan
        current = finite[-1]
        below = np.count_nonzero(finite < current)
        equal = np.count_nonzero(finite == current)
        return float((below + 0.5 * equal) / len(finite) * 100.0)

    return series.rolling(window, min_periods=minimum).apply(percentile, raw=True)


def calculate_direct_flow_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """직접 순매수의 5거래일 누계와 과거 정보만 사용한 매도압력 분위수를 계산합니다."""

    result = frame.copy()
    if not any(column in result.columns for column in DIRECT_FLOW_COLUMNS):
        return result
    for column in DIRECT_FLOW_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")

    pressure_specs = [
        ("foreign", "foreign_net_buy_value"),
        ("institution", "institution_net_buy_value"),
        ("program", "program_net_buy_value"),
    ]
    for prefix, value_column in pressure_specs:
        cumulative_column = f"{prefix}_net_buy_5d"
        pressure_column = f"{prefix}_sell_pressure"
        result[cumulative_column] = result[value_column].rolling(5, min_periods=5).sum()
        result[pressure_column] = _rolling_percentile_last(-result[cumulative_column])

    pressure_columns = [
        ("foreign_sell_pressure", 0.45),
        ("institution_sell_pressure", 0.35),
        ("program_sell_pressure", 0.20),
    ]
    weighted = pd.Series(0.0, index=result.index)
    available_weight = pd.Series(0.0, index=result.index)
    for column, weight in pressure_columns:
        available = result[column].notna()
        weighted = weighted.add(result[column].fillna(0.0) * weight)
        available_weight = available_weight.add(available.astype(float) * weight)
    result["direct_flow_pressure"] = weighted.div(available_weight.replace(0.0, np.nan))
    return result


def merge_vkospi(
    breadth_frame: pd.DataFrame,
    vkospi: pd.DataFrame | str | Path | None,
) -> pd.DataFrame:
    """선택적으로 외부 VKOSPI dataframe 또는 파일을 날짜 기준으로 결합합니다."""

    result = breadth_frame.copy()
    if vkospi is None:
        return result
    vol_frame = load_frame(vkospi) if isinstance(vkospi, (str, Path)) else vkospi.copy()
    if "date" not in vol_frame.columns:
        raise ValueError("VKOSPI 데이터에 date 컬럼이 필요합니다.")
    value_column = next(
        (column for column in ["VKOSPI", "vkospi", "vkospi_close", "close"] if column in vol_frame.columns),
        None,
    )
    if value_column is None:
        raise ValueError("VKOSPI 데이터에 VKOSPI, vkospi, vkospi_close 또는 close 컬럼이 필요합니다.")
    vol_frame = vol_frame[["date", value_column]].rename(columns={value_column: "vkospi"})
    vol_frame["date"] = pd.to_datetime(vol_frame["date"], errors="coerce")
    vol_frame["vkospi"] = pd.to_numeric(vol_frame["vkospi"], errors="coerce")
    vol_frame = vol_frame.dropna(subset=["date", "vkospi"])
    vol_frame = vol_frame.drop_duplicates("date", keep="last").sort_values("date")
    result["date"] = pd.to_datetime(result["date"])
    result = result.merge(vol_frame, on="date", how="left")
    result["vkospi_change"] = result["vkospi"].pct_change()
    return result


def add_market_state_flags(frame: pd.DataFrame) -> pd.DataFrame:
    """KOSPI·VKOSPI·breadth 조합으로 A~D 시장상태 관찰 플래그를 추가합니다."""

    result = frame.copy()
    breadth_change = result["breadth_pct"].diff()
    has_vkospi = {"vkospi", "vkospi_change"} <= set(result.columns)
    if has_vkospi:
        result["healthy_risk_on"] = (
            (result["kospi_return"] > 0)
            & (result["vkospi_change"] < 0)
            & (result["breadth_pct"] > 0)
            & (breadth_change > 0)
        )
        result["panic"] = (
            (result["kospi_return"] < 0)
            & (result["vkospi_change"] > 0)
            & (result["breadth_pct"] <= -0.4)
        )
    else:
        result["healthy_risk_on"] = False
        result["panic"] = False
    result["narrow_rally"] = (result["kospi_return"] > 0) & (result["breadth_pct"] < 0)
    if {"samsung_return", "hynix_return"} <= set(result.columns) and has_vkospi:
        result["sector_rotation"] = (
            (result["samsung_return"] < 0)
            & (result["hynix_return"] < 0)
            & (result["kospi_return"] >= 0)
            & (result["breadth_pct"] > 0)
            & (result["vkospi_change"] < 0)
        )
    else:
        result["sector_rotation"] = False
    return result


def validate_breadth_quality(frame: pd.DataFrame, lookback: int = 10) -> dict[str, object]:
    """최근 종목 수 급변, 합계 불일치와 비정상적인 universe 축소를 검사합니다."""

    required = {"date", "up", "down", "flat", "total"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"breadth 품질검사 필수 컬럼 누락: {', '.join(missing)}")
    recent = frame.sort_values("date").tail(max(2, lookback)).copy()
    count_sum = recent[["up", "down", "flat"]].sum(axis=1)
    equation_failures = recent.loc[count_sum != recent["total"], "date"]
    total_change = recent["total"].pct_change().abs()
    median_total = float(recent["total"].median()) if not recent.empty else math.nan
    abnormal = recent.loc[
        (total_change > 0.12)
        | (recent["total"] < max(100, median_total * 0.75 if math.isfinite(median_total) else 100)),
        ["date", "total"],
    ]
    anomalies = [
        {"date": pd.Timestamp(row["date"]).date().isoformat(), "total": int(row["total"])}
        for row in abnormal.to_dict(orient="records")
    ]
    status = "error" if len(equation_failures) else "warning" if anomalies else "ok"
    return {
        "status": status,
        "lookbackRows": int(len(recent)),
        "latestDate": None if recent.empty else pd.Timestamp(recent["date"].iloc[-1]).date().isoformat(),
        "latestTotal": None if recent.empty else int(recent["total"].iloc[-1]),
        "minTotal": None if recent.empty else int(recent["total"].min()),
        "maxTotal": None if recent.empty else int(recent["total"].max()),
        "equationFailureDates": [pd.Timestamp(value).date().isoformat() for value in equation_failures],
        "anomalies": anomalies,
    }


def sanity_check_breadth(
    frame: pd.DataFrame,
    observed_date: str | date | datetime | pd.Timestamp,
    reference_counts: dict[str, int] | None = None,
    *,
    tolerance_ratio: float = 0.08,
) -> dict[str, object]:
    """한 날짜의 집계값을 KRX·Naver 화면 수치와 수동 비교할 수 있게 정리합니다."""

    target = _as_timestamp(observed_date)
    matched = frame.loc[pd.to_datetime(frame["date"]) == target]
    if matched.empty:
        raise ValueError(f"breadth 데이터에 해당 날짜가 없습니다: {target.date()}")
    row = matched.iloc[-1]
    result: dict[str, object] = {
        "date": target.date().isoformat(),
        "calculated": {column: int(row[column]) for column in ["up", "down", "flat", "total"]},
        "reference": reference_counts,
        "withinTolerance": None,
        "note": (
            "우선주·SPAC·REIT 등 pykrx KOSPI universe 포함범위와 "
            "화면 분류시각에 따라 차이가 날 수 있습니다."
        ),
    }
    if reference_counts:
        keys = [key for key in ["up", "down", "flat", "total"] if key in reference_counts]
        differences = {
            key: int(row[key]) - int(reference_counts[key])
            for key in keys
        }
        allowed = max(5, round(int(row["total"]) * tolerance_ratio))
        result["differences"] = differences
        result["withinTolerance"] = all(abs(value) <= allowed for value in differences.values())
        result["allowedCountDifference"] = allowed
    return result


def _merge_counts_and_index(counts: pd.DataFrame, index_frame: pd.DataFrame) -> pd.DataFrame:
    metrics = calculate_breadth_metrics(counts)
    index_values = (
        index_frame[["date", "kospi_close"]].copy()
        if not index_frame.empty
        else pd.DataFrame(columns=["date", "kospi_close"])
    )
    index_values["date"] = pd.to_datetime(index_values["date"])
    result = metrics.merge(index_values, on="date", how="left")
    result = result.sort_values("date").reset_index(drop=True)
    result["kospi_return"] = result["kospi_close"].pct_change()
    ordered = [column for column in BREADTH_OUTPUT_COLUMNS if column in result.columns]
    return result[ordered]


def _update_metadata(
    path: Path,
    frame: pd.DataFrame,
    quality: dict[str, object],
    fetch_attrs: dict[str, object],
    *,
    vkospi_merged: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    latest = frame.sort_values("date").iloc[-1] if not frame.empty else None
    investor_flow_available = bool(
        latest is not None
        and all(
            column in frame.columns and pd.notna(latest[column])
            for column in ["foreign_net_buy_value", "institution_net_buy_value"]
        )
    )
    program_flow_available = bool(
        latest is not None
        and "program_net_buy_value" in frame.columns
        and pd.notna(latest["program_net_buy_value"])
    )
    program_flow_dates = (
        frame.loc[frame["program_net_buy_value"].notna(), "date"]
        if "program_net_buy_value" in frame.columns
        else pd.Series(dtype="datetime64[ns]")
    )
    payload = {
        "generatedAt": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST"),
        "source": "pykrx stock.get_market_ohlcv(date, market='KOSPI')",
        "rows": int(len(frame)),
        "firstDate": None if frame.empty else frame["date"].min().date().isoformat(),
        "lastDate": None if frame.empty else frame["date"].max().date().isoformat(),
        "failedDates": fetch_attrs.get("failed_dates", []),
        "emptyDates": fetch_attrs.get("empty_dates", []),
        "indexError": fetch_attrs.get("index_error"),
        "investorFlowStatus": "available" if investor_flow_available else "not_available",
        "programFlowStatus": "available" if program_flow_available else "not_available",
        "programFlowCoverageStart": (
            None if program_flow_dates.empty else program_flow_dates.min().date().isoformat()
        ),
        "programFlowObservations": int(len(program_flow_dates)),
        "investorFlowError": fetch_attrs.get("investor_flow_error"),
        "programFlowFailedDates": fetch_attrs.get("program_flow_failed_dates", []),
        "programFlowEmptyDates": fetch_attrs.get("program_flow_empty_dates", []),
        "vkospiMerged": vkospi_merged,
        "adLineBase": "저장된 첫 관측일의 net_breadth부터 누적",
        "quality": quality,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_breadth_data(
    output_path: str | Path,
    *,
    start_date: str | date | datetime | pd.Timestamp | None = None,
    end_date: str | date | datetime | pd.Timestamp | None = None,
    vkospi: pd.DataFrame | str | Path | None = None,
    raw_output_dir: str | Path | None = None,
    metadata_path: str | Path | None = None,
    refresh_from_start: bool = False,
    sleep_seconds: float = 0.4,
    retries: int = 3,
    fetch_flows: bool = True,
    fetch_program: bool = True,
    ohlcv_fetcher: OhlcvFetcher | None = None,
    index_fetcher: IndexFetcher | None = None,
    investor_flow_fetcher: InvestorFlowFetcher | None = None,
    program_flow_fetcher: ProgramFlowFetcher | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    """기존 파일 다음 날짜만 조회해 breadth 데이터를 정렬·중복제거 후 저장합니다."""

    output = Path(output_path)
    try:
        existing = load_frame(output) if output.exists() else pd.DataFrame()
    except KeyError as error:
        raise ValueError(f"기존 breadth 파일에 date 컬럼이 필요합니다: {output}") from error
    if not existing.empty:
        missing = sorted(set(BREADTH_COUNT_COLUMNS) - set(existing.columns))
        if missing:
            raise ValueError(f"기존 breadth 파일 필수 컬럼 누락: {', '.join(missing)}")
    end = _as_timestamp(end_date or pd.Timestamp.today())
    if existing.empty:
        if start_date is None:
            raise ValueError("최초 실행에는 start_date가 필요합니다.")
        fetch_start = _as_timestamp(start_date)
    else:
        existing_last = pd.to_datetime(existing["date"]).max().normalize()
        requested_start = _as_timestamp(start_date) if start_date is not None else existing_last + timedelta(days=1)
        fetch_start = requested_start if refresh_from_start else max(existing_last + timedelta(days=1), requested_start)

    fetch_attrs: dict[str, object] = {"failed_dates": [], "empty_dates": []}
    new_counts = pd.DataFrame(columns=BREADTH_DAILY_COLUMNS)
    new_index = pd.DataFrame(columns=["date", "kospi_close", "kospi_return"])
    if fetch_start <= end:
        try:
            new_counts = fetch_kospi_breadth(
                fetch_start,
                end,
                sleep_seconds=sleep_seconds,
                retries=retries,
                ohlcv_fetcher=ohlcv_fetcher,
                raw_output_dir=raw_output_dir,
                sleep_fn=sleep_fn,
            )
            fetch_attrs = dict(new_counts.attrs)
        except Exception as error:
            # KRX 로그인·네트워크 전체 장애 때도 기존 확정 EOD는 보존합니다.
            LOGGER.warning("KOSPI breadth 원천 조회 실패 · 기존 데이터 보존 · %s", error)
            fetch_attrs = {
                "failed_dates": [
                    {
                        "date": f"{fetch_start.date().isoformat()}~{end.date().isoformat()}",
                        "error": str(error),
                        "scope": "source",
                    }
                ],
                "empty_dates": [],
            }
        if not new_counts.empty:
            try:
                new_index = fetch_kospi_index(
                    fetch_start,
                    end,
                    retries=retries,
                    sleep_seconds=sleep_seconds,
                    index_fetcher=index_fetcher,
                    sleep_fn=sleep_fn,
                )
            except RuntimeError as error:
                LOGGER.warning("KOSPI 지수 결합 실패 · breadth 집계는 보존합니다 · %s", error)
                fetch_attrs["index_error"] = str(error)

    existing_counts = (
        existing[
            BREADTH_COUNT_COLUMNS
            + [column for column in BREADTH_CONTEXT_COLUMNS if column in existing.columns]
        ].copy()
        if not existing.empty
        else pd.DataFrame(columns=BREADTH_DAILY_COLUMNS)
    )
    count_frames = [frame for frame in [existing_counts, new_counts] if not frame.empty]
    all_counts = (
        pd.concat(count_frames, ignore_index=True)
        if count_frames
        else pd.DataFrame(columns=BREADTH_DAILY_COLUMNS)
    )
    if all_counts.empty:
        failed_count = len(fetch_attrs.get("failed_dates", []))
        quality = {
            "status": "error",
            "lookbackRows": 0,
            "latestDate": None,
            "latestTotal": None,
            "minTotal": None,
            "maxTotal": None,
            "equationFailureDates": [],
            "anomalies": [],
            "reason": "저장 가능한 거래일이 없습니다.",
        }
        if metadata_path:
            _update_metadata(
                Path(metadata_path),
                pd.DataFrame(columns=["date"]),
                quality,
                fetch_attrs,
                vkospi_merged=False,
            )
        raise RuntimeError(
            f"저장할 KOSPI breadth 거래일이 없습니다. 조회 실패일 {failed_count}개를 확인하세요."
        )
    all_counts["date"] = pd.to_datetime(all_counts["date"])
    all_counts = all_counts.drop_duplicates("date", keep="last").sort_values("date")

    existing_index = (
        existing[["date", "kospi_close"]].copy()
        if not existing.empty and "kospi_close" in existing.columns
        else pd.DataFrame(columns=["date", "kospi_close"])
    )
    index_frames = [
        frame
        for frame in [existing_index, new_index[["date", "kospi_close"]]]
        if not frame.empty
    ]
    all_index = (
        pd.concat(index_frames, ignore_index=True)
        if index_frames
        else pd.DataFrame(columns=["date", "kospi_close"])
    )
    all_index["date"] = pd.to_datetime(all_index["date"])
    all_index = all_index.drop_duplicates("date", keep="last").sort_values("date")

    result = _merge_counts_and_index(all_counts, all_index)

    existing_flow = (
        existing[["date", *[column for column in DIRECT_FLOW_COLUMNS if column in existing.columns]]]
        .copy()
        if not existing.empty
        else pd.DataFrame(columns=["date", *DIRECT_FLOW_COLUMNS])
    )
    if not existing_flow.empty:
        existing_flow["date"] = pd.to_datetime(existing_flow["date"])
        result = result.merge(existing_flow, on="date", how="left")

    if fetch_flows and not result.empty:
        target_dates = pd.DatetimeIndex(pd.to_datetime(result["date"]).dropna().unique()).sort_values()
        investor_missing = result.loc[
            result.get("foreign_net_buy_value", pd.Series(np.nan, index=result.index)).isna()
            | result.get("institution_net_buy_value", pd.Series(np.nan, index=result.index)).isna(),
            "date",
        ]
        if not investor_missing.empty:
            try:
                investor_flow = fetch_kospi_investor_flows(
                    investor_missing.min(),
                    investor_missing.max(),
                    retries=retries,
                    sleep_seconds=sleep_seconds,
                    investor_flow_fetcher=investor_flow_fetcher,
                    sleep_fn=sleep_fn,
                )
                if not investor_flow.empty:
                    keep_columns = ["date", *INVESTOR_FLOW_COLUMNS]
                    current = result.drop(columns=INVESTOR_FLOW_COLUMNS, errors="ignore")
                    cached_investor = existing_flow.reindex(columns=keep_columns).dropna(
                        subset=INVESTOR_FLOW_COLUMNS,
                        how="all",
                    )
                    investor_frames = [investor_flow[keep_columns]]
                    if not cached_investor.empty:
                        investor_frames.insert(0, cached_investor)
                    combined = pd.concat(
                        investor_frames,
                        ignore_index=True,
                    )
                    combined = combined.drop_duplicates("date", keep="last")
                    result = current.merge(combined, on="date", how="left")
            except Exception as error:
                fetch_attrs["investor_flow_error"] = str(error)
                LOGGER.warning("KOSPI 투자자 순매수 결합 실패 · 기존 값 보존 · %s", error)

        program_existing = result.get(
            "program_net_buy_value", pd.Series(np.nan, index=result.index)
        )
        program_missing_dates = result.loc[program_existing.isna(), "date"]
        program_missing_set = set(pd.to_datetime(program_missing_dates))
        recent_program_dates = set(target_dates[-PROGRAM_FLOW_BACKFILL_OBSERVATIONS:])
        program_missing_dates = [
            value
            for value in target_dates
            if value in program_missing_set and value in recent_program_dates
        ]
        if fetch_program and program_missing_dates:
            try:
                program_flow = fetch_kospi_program_flows(
                    program_missing_dates,
                    retries=retries,
                    sleep_seconds=min(sleep_seconds, 0.25),
                    program_flow_fetcher=program_flow_fetcher,
                    sleep_fn=sleep_fn,
                )
                fetch_attrs["program_flow_failed_dates"] = program_flow.attrs.get(
                    "failed_dates", []
                )
                fetch_attrs["program_flow_empty_dates"] = program_flow.attrs.get(
                    "empty_dates", []
                )
                if not program_flow.empty:
                    current = result.drop(columns=PROGRAM_FLOW_COLUMNS, errors="ignore")
                    cached_program = existing_flow.reindex(
                        columns=["date", *PROGRAM_FLOW_COLUMNS]
                    ).dropna(subset=PROGRAM_FLOW_COLUMNS, how="all")
                    program_frames = [program_flow[["date", *PROGRAM_FLOW_COLUMNS]]]
                    if not cached_program.empty:
                        program_frames.insert(0, cached_program)
                    combined = pd.concat(
                        program_frames,
                        ignore_index=True,
                    )
                    combined = combined.drop_duplicates("date", keep="last")
                    result = current.merge(combined, on="date", how="left")
            except Exception as error:
                fetch_attrs["program_flow_failed_dates"] = [
                    {
                        "date": (
                            f"{program_missing_dates[0].date().isoformat()}~"
                            f"{program_missing_dates[-1].date().isoformat()}"
                        ),
                        "error": str(error),
                    }
                ]
                LOGGER.warning("KOSPI 프로그램 순매수 결합 실패 · 기존 값 보존 · %s", error)

    result = calculate_direct_flow_metrics(result)
    vkospi_source = vkospi
    if vkospi_source is None and not existing.empty and "vkospi" in existing.columns:
        vkospi_source = existing[["date", "vkospi"]]
    result = merge_vkospi(result, vkospi_source)
    result = add_market_state_flags(result)
    result = result.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    quality = validate_breadth_quality(result)
    if fetch_attrs.get("failed_dates") or fetch_attrs.get("index_error"):
        quality = {
            **quality,
            "status": "warning" if quality.get("status") != "error" else "error",
            "fetchFailureCount": len(fetch_attrs.get("failed_dates", [])),
        }
    latest_result = result.iloc[-1]
    direct_flow_missing = not all(
        column in result.columns and pd.notna(latest_result[column])
        for column in [
            "foreign_net_buy_value",
            "institution_net_buy_value",
            "program_net_buy_value",
        ]
    )
    if fetch_flows and direct_flow_missing:
        quality = {
            **quality,
            "status": "warning" if quality.get("status") != "error" else "error",
            "directFlowLatestMissing": True,
        }
    save_frame(result, output)
    if metadata_path:
        _update_metadata(
            Path(metadata_path),
            result,
            quality,
            fetch_attrs,
            vkospi_merged="vkospi" in result.columns,
        )
    result.attrs.update(fetch_attrs)
    result.attrs["quality"] = quality
    return result


def _configure_korean_matplotlib() -> None:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for candidate in ["AppleGothic", "Malgun Gothic", "NanumGothic"]:
        try:
            font_manager.findfont(candidate, fallback_to_default=False)
            plt.rcParams["font.family"] = candidate
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def plot_breadth_dashboard(
    frame: pd.DataFrame,
    output_dir: str | Path = "reports/figures/kospi_breadth",
) -> list[Path]:
    """KOSPI, breadth, AD Line과 선택적 VKOSPI 비교 차트를 PNG로 저장합니다."""

    import matplotlib
    import matplotlib.dates as mdates

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    required = {"date", "kospi_close", "breadth_pct", "AD_line", "AD_ma20"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"breadth 차트 필수 컬럼 누락: {', '.join(missing)}")
    _configure_korean_matplotlib()
    chart_frame = frame.copy()
    chart_frame["date"] = pd.to_datetime(chart_frame["date"])
    chart_frame = chart_frame.dropna(subset=["date"]).sort_values("date")
    if chart_frame.empty:
        raise ValueError("차트에 사용할 breadth 관측치가 없습니다.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def format_date_axis(axis) -> None:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
        axis.xaxis.set_major_locator(locator)
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, height_ratios=[1.15, 1])
    axes[0].plot(chart_frame["date"], chart_frame["kospi_close"], color="#2563eb", linewidth=1.5)
    axes[0].set_title("KOSPI와 Market Breadth")
    axes[0].set_ylabel("KOSPI")
    axes[1].plot(chart_frame["date"], chart_frame["breadth_pct"], color="#0f766e", linewidth=1.2, label="Breadth %")
    axes[1].plot(chart_frame["date"], chart_frame["breadth_ma20"], color="#d97706", linewidth=1.1, label="20D 평균")
    axes[1].axhline(0, color="#475569", linestyle="--", linewidth=1)
    axes[1].fill_between(
        chart_frame["date"],
        0,
        chart_frame["breadth_pct"],
        where=chart_frame["breadth_pct"] >= 0,
        color="#16a34a",
        alpha=0.12,
    )
    axes[1].fill_between(
        chart_frame["date"],
        0,
        chart_frame["breadth_pct"],
        where=chart_frame["breadth_pct"] < 0,
        color="#dc2626",
        alpha=0.12,
    )
    axes[1].set_ylabel("Breadth %")
    axes[1].legend(loc="upper left")
    for axis in axes:
        axis.grid(True, alpha=0.22)
    format_date_axis(axes[-1])
    path = output / "kospi_breadth_pct.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, height_ratios=[1.1, 1])
    axes[0].plot(chart_frame["date"], chart_frame["kospi_close"], color="#2563eb", linewidth=1.5)
    axes[0].set_title("KOSPI와 Advance-Decline Line")
    axes[0].set_ylabel("KOSPI")
    axes[1].plot(chart_frame["date"], chart_frame["AD_line"], color="#0f766e", linewidth=1.3, label="AD Line")
    axes[1].plot(chart_frame["date"], chart_frame["AD_ma20"], color="#d97706", linewidth=1.2, label="AD Line 20D")
    axes[1].set_ylabel("누적 net breadth")
    axes[1].legend(loc="upper left")
    for axis in axes:
        axis.grid(True, alpha=0.22)
    format_date_axis(axes[-1])
    path = output / "kospi_ad_line.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    paths.append(path)

    if "vkospi" in chart_frame.columns and chart_frame["vkospi"].notna().any():
        fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, height_ratios=[1, 0.85, 1])
        axes[0].plot(chart_frame["date"], chart_frame["kospi_close"], color="#2563eb", linewidth=1.4)
        axes[0].set_title("KOSPI · VKOSPI · Market Breadth")
        axes[0].set_ylabel("KOSPI")
        axes[1].plot(chart_frame["date"], chart_frame["vkospi"], color="#dc2626", linewidth=1.3)
        axes[1].set_ylabel("VKOSPI")
        axes[2].plot(chart_frame["date"], chart_frame["breadth_pct"], color="#0f766e", linewidth=1.2)
        axes[2].plot(chart_frame["date"], chart_frame["breadth_ma20"], color="#d97706", linewidth=1.0)
        axes[2].axhline(0, color="#475569", linestyle="--", linewidth=1)
        axes[2].set_ylabel("Breadth %")
        signal_specs = [
            ("healthy_risk_on", "A", "#16a34a"),
            ("narrow_rally", "B", "#d97706"),
            ("panic", "C", "#dc2626"),
            ("sector_rotation", "D", "#7c3aed"),
        ]
        for column, label, color in signal_specs:
            if column not in chart_frame.columns:
                continue
            signal_rows = chart_frame.loc[chart_frame[column].fillna(False)]
            axes[2].scatter(signal_rows["date"], signal_rows["breadth_pct"], s=22, color=color, label=label, zorder=4)
        if any(column in chart_frame.columns and chart_frame[column].any() for column, _, _ in signal_specs):
            axes[2].legend(loc="upper left", ncol=4)
        for axis in axes:
            axis.grid(True, alpha=0.22)
        format_date_axis(axes[-1])
        path = output / "kospi_vkospi_breadth.png"
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        paths.append(path)
    return paths
