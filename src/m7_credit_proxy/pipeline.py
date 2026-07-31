from __future__ import annotations

import argparse
import io
import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "configs" / "m7_credit_proxy.yaml"
PUBLIC_FILE = ROOT / "data" / "m7-credit-proxy.json"
PROCESSED_FILE = ROOT / "data" / "processed" / "m7_credit_proxy_daily.csv"
MEMBERS_FILE = ROOT / "data" / "processed" / "m7_credit_members_latest.json"
QUALITY_FILE = ROOT / "data" / "quality" / "m7_credit_source_status.json"
RAW_DIR = Path(
    os.environ.get("M7_CREDIT_RAW_DIR", ROOT / "data" / "raw" / "m7_credit_proxy")
)

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
M7_MEMBERS = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")
PRICE_ASSETS = (*M7_MEMBERS, "QQQ", "IGIB", "LQD", "HYG", "IEF")
DISCLAIMER = (
    "본 지표는 CDS 스프레드가 아니며, 공개 시장자료를 이용해 산출한 "
    "상대적 신용 스트레스 프록시입니다."
)
STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
YAHOO_URLS = (
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?range={history_range}&interval=1d&events=history&includeAdjustedClose=true",
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?range={history_range}&interval=1d&events=history&includeAdjustedClose=true",
)
OFR_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    "?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
)
SEC_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_CIKS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
}
SOURCE_URLS = {
    "ofr_fsi": OFR_URL,
    "treasury_yield_curve": TREASURY_URL.format(year="{year}"),
    "sec_company_facts": "https://data.sec.gov/api/xbrl/companyfacts/",
}


def load_config(path: Path = CONFIG_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"M7 설정 파일이 올바르지 않습니다: {path}")
    weights = config.get("weights") or {}
    if not weights or not math.isclose(sum(float(value) for value in weights.values()), 1.0):
        raise ValueError("M7 구성요소 가중치 합계는 1이어야 합니다.")
    blocked_access = {
        "internal use only",
        "pre-approval required",
        "paid subscription",
        "login required",
        "automated access prohibited",
    }
    for source_id, source in (config.get("sources") or {}).items():
        if not source.get("enabled", False):
            continue
        blocked_reason = None
        if source.get("paid_subscription_required"):
            blocked_reason = "paid subscription"
        elif str(source.get("access_type", "")).lower() in blocked_access:
            blocked_reason = source.get("access_type")
        elif str(source.get("redistribution_status", "")).lower() in {
            "redistribution prohibited",
            "pre-approval required",
        }:
            blocked_reason = source.get("redistribution_status")
        if blocked_reason:
            raise ValueError(f"{source_id}: 허용되지 않은 데이터 원천 상태 {blocked_reason}")
    return config


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _now_kst_text() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _http_get(url: str, *, headers: dict[str, str] | None = None, attempts: int = 3) -> bytes:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; market-lab-risk-dashboard/1.0)",
        "Accept": "*/*",
        **(headers or {}),
    }
    errors: list[str] = []
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as error:  # pragma: no cover - 네트워크별 예외가 달라 넓게 처리
            errors.append(str(error))
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"공개 데이터 조회 실패: {url} ({'; '.join(errors)})")


def _validate_price_frame(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    required = {"close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"{label}: 필수 가격 컬럼이 없습니다.")
    normalized = frame.copy()
    normalized.index = pd.to_datetime(normalized.index, errors="coerce").normalize()
    normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
    normalized = normalized.loc[normalized.index.notna(), ["close"]].dropna()
    normalized = normalized[~normalized.index.duplicated(keep="last")].sort_index()
    if len(normalized) < 252:
        raise ValueError(f"{label}: 관측치가 252개보다 적습니다 ({len(normalized)}개).")
    if (normalized["close"] <= 0).any():
        raise ValueError(f"{label}: 0 이하 가격이 포함되어 있습니다.")
    returns = normalized["close"].pct_change()
    if (returns.abs() > 0.35).any():
        normalized.attrs["corporate_action_warning"] = True
    return normalized


def parse_stooq_csv(content: bytes | str, label: str = "Stooq") -> pd.DataFrame:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    if "<html" in text.lower() or "requires javascript" in text.lower():
        raise ValueError(f"{label}: CSV 대신 자동요청 검증 화면이 반환됐습니다.")
    try:
        raw = pd.read_csv(io.StringIO(text))
    except Exception as error:
        raise ValueError(f"{label}: Stooq CSV 파싱 실패") from error
    if not {"Date", "Close"}.issubset(raw.columns):
        raise ValueError(f"{label}: Stooq 필수 컬럼 Date, Close가 없습니다.")
    frame = raw.rename(columns={"Date": "date", "Close": "close"}).set_index("date")
    return _validate_price_frame(frame, label)


def _parse_yahoo_chart(content: bytes, label: str) -> pd.DataFrame:
    payload = json.loads(content.decode("utf-8"))
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"{label}: 가격 응답 오류 {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"{label}: 가격 응답이 비었습니다.")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adjusted = ((indicators.get("adjclose") or [{}])[0].get("adjclose") or [])
    closes = ((indicators.get("quote") or [{}])[0].get("close") or [])
    rows = []
    for index, timestamp in enumerate(timestamps):
        value = adjusted[index] if index < len(adjusted) else None
        if value is None and index < len(closes):
            value = closes[index]
        if value is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(timestamp, UTC).date().isoformat(),
                "close": value,
            }
        )
    if not rows:
        raise ValueError(f"{label}: 유효한 조정종가가 없습니다.")
    return _validate_price_frame(pd.DataFrame(rows).set_index("date"), label)


def _price_cache_path(symbol: str) -> Path:
    return RAW_DIR / "prices" / f"{symbol.lower()}.csv"


def _read_price_cache(symbol: str) -> pd.DataFrame:
    path = _price_cache_path(symbol)
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path).set_index("date")
    return _validate_price_frame(frame, f"{symbol} 저장값")


def _write_price_cache(symbol: str, frame: pd.DataFrame) -> None:
    output = frame.reset_index().rename(columns={"index": "date"})
    output["date"] = pd.to_datetime(output["date"]).dt.strftime("%Y-%m-%d")
    _atomic_write(_price_cache_path(symbol), output.to_csv(index=False).encode("utf-8"))


def _fetch_yahoo_price(symbol: str, history_range: str) -> pd.DataFrame:
    errors = []
    encoded = urllib.parse.quote(symbol, safe="")
    for template in YAHOO_URLS:
        url = template.format(symbol=encoded, history_range=history_range)
        try:
            return _parse_yahoo_chart(_http_get(url, attempts=2), symbol)
        except Exception as error:
            errors.append(str(error))
    raise RuntimeError(f"{symbol}: 기존 대시보드 가격 피드 조회 실패 ({'; '.join(errors)})")


def _probe_stooq(stooq_symbol: str) -> tuple[pd.DataFrame | None, str | None]:
    try:
        content = _http_get(STOOQ_URL.format(symbol=stooq_symbol), attempts=1)
        return parse_stooq_csv(content, stooq_symbol), None
    except Exception as error:
        return None, str(error)


def fetch_prices(config: dict[str, Any]) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    price_config = config["price"]
    symbols = price_config["symbols"]
    history_range = str(price_config.get("history_range", "5y"))
    qqq_frame, stooq_error = _probe_stooq(str(symbols["QQQ"]))
    use_stooq = qqq_frame is not None
    statuses: list[dict[str, Any]] = []
    frames: dict[str, pd.DataFrame] = {}

    def fetch_one(asset: str) -> tuple[str, pd.DataFrame, str, str | None]:
        if asset == "QQQ" and qqq_frame is not None:
            return asset, qqq_frame, "stooq", None
        if use_stooq:
            try:
                content = _http_get(
                    STOOQ_URL.format(symbol=str(symbols[asset])), attempts=2
                )
                return asset, parse_stooq_csv(content, asset), "stooq", None
            except Exception as error:
                primary_error = str(error)
        else:
            primary_error = stooq_error
        try:
            frame = _fetch_yahoo_price(asset, history_range)
            return asset, frame, "dashboard_yahoo", primary_error
        except Exception as live_error:
            try:
                frame = _read_price_cache(asset)
                return asset, frame, "local_cache", f"{primary_error}; {live_error}"
            except Exception as cache_error:
                raise RuntimeError(
                    f"{asset}: 실시간·저장 가격 모두 사용할 수 없습니다 "
                    f"({primary_error}; {live_error}; {cache_error})"
                ) from live_error

    with ThreadPoolExecutor(max_workers=int(price_config.get("max_workers", 6))) as executor:
        futures = {executor.submit(fetch_one, asset): asset for asset in PRICE_ASSETS}
        for future in as_completed(futures):
            asset, frame, source, warning = future.result()
            frames[asset] = frame
            if source != "local_cache":
                _write_price_cache(asset, frame)
            last_date = frame.index[-1].date().isoformat()
            statuses.append(
                {
                    "source": f"price:{asset}",
                    "status": "ok" if source == "stooq" else "warning",
                    "provider": source,
                    "last_value_date": last_date,
                    "retrieved_at": _now_utc().isoformat(timespec="seconds"),
                    "stale_business_days": 0,
                    "row_count": len(frame),
                    "duplicate_count": 0,
                    "null_count": int(frame["close"].isna().sum()),
                    "warning_message": (
                        None
                        if source == "stooq"
                        else (
                            "Stooq 자동 CSV를 사용할 수 없어 기존 대시보드 가격 피드를 사용했습니다."
                            if source == "dashboard_yahoo"
                            else f"신규 조회 실패로 로컬 정상 파일을 유지했습니다: {warning}"
                        )
                    ),
                }
            )
    missing = sorted(set(PRICE_ASSETS) - set(frames))
    if missing:
        raise RuntimeError(f"M7 필수 가격 누락: {', '.join(missing)}")
    return frames, sorted(statuses, key=lambda item: item["source"])


def parse_ofr_csv(content: bytes | str) -> pd.DataFrame:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    raw = pd.read_csv(io.StringIO(text))
    required = {
        "Date",
        "OFR FSI",
        "Credit",
        "Funding",
        "Volatility",
        "United States",
    }
    if not required.issubset(raw.columns):
        missing = ", ".join(sorted(required - set(raw.columns)))
        raise ValueError(f"OFR CSV 필수 컬럼 누락: {missing}")
    rename = {
        "Date": "date",
        "OFR FSI": "ofr_fsi",
        "Credit": "ofr_credit",
        "Funding": "ofr_funding",
        "Volatility": "ofr_volatility",
        "United States": "ofr_us",
        "Equity valuation": "ofr_equity_valuation",
        "Safe assets": "ofr_safe_assets",
        "Other advanced economies": "ofr_other_advanced",
        "Emerging markets": "ofr_emerging",
    }
    frame = raw.rename(columns=rename).set_index("date")
    frame.index = pd.to_datetime(frame.index, errors="coerce").normalize()
    frame = frame.loc[frame.index.notna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.empty or frame["ofr_fsi"].isna().all():
        raise ValueError("OFR FSI 유효 관측치가 없습니다.")
    return frame


def fetch_ofr() -> tuple[pd.DataFrame, dict[str, Any]]:
    path = RAW_DIR / "ofr" / "fsi.csv"
    warning = None
    try:
        content = _http_get(OFR_URL)
        frame = parse_ofr_csv(content)
        _atomic_write(path, content)
        provider = "ofr"
    except Exception as error:
        if not path.exists():
            raise
        frame = parse_ofr_csv(path.read_bytes())
        provider = "local_cache"
        warning = f"OFR 신규 조회 실패로 기존 정상 파일을 유지했습니다: {error}"
    latest = frame.index[-1].date()
    lag = business_days_between(latest, date.today())
    return frame, {
        "source": "ofr_fsi",
        "status": "ok" if provider == "ofr" and lag <= 5 else "warning",
        "provider": provider,
        "last_value_date": latest.isoformat(),
        "retrieved_at": _now_utc().isoformat(timespec="seconds"),
        "stale_business_days": lag,
        "row_count": len(frame),
        "duplicate_count": 0,
        "null_count": int(frame["ofr_fsi"].isna().sum()),
        "warning_message": warning or (f"발표시차 {lag}영업일" if lag > 5 else None),
    }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_treasury_xml(content: bytes | str) -> pd.DataFrame:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    root = ET.fromstring(raw)
    rows = []
    for entry in root.iter():
        if _local_name(entry.tag) != "properties":
            continue
        values = {_local_name(child.tag): child.text for child in entry}
        raw_date = values.get("NEW_DATE") or values.get("Date")
        if not raw_date:
            continue
        rows.append(
            {
                "date": str(raw_date)[:10],
                "treasury_2y": values.get("BC_2YEAR"),
                "treasury_5y": values.get("BC_5YEAR"),
                "treasury_10y": values.get("BC_10YEAR"),
                "treasury_30y": values.get("BC_30YEAR"),
            }
        )
    if not rows:
        raise ValueError("Treasury XML에 수익률 관측치가 없습니다.")
    frame = pd.DataFrame(rows).set_index("date")
    frame.index = pd.to_datetime(frame.index, errors="coerce").normalize()
    frame = frame.loc[frame.index.notna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    for column in frame.columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required = ("treasury_2y", "treasury_5y", "treasury_10y", "treasury_30y")
    if any(frame[column].isna().all() for column in required):
        raise ValueError("Treasury XML 테너 숫자 변환에 실패했습니다.")
    return frame


def fetch_treasury(start_year: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    current_year = date.today().year
    frames = []
    warnings = []
    live_years = 0
    for year in range(start_year, current_year + 1):
        path = RAW_DIR / "treasury" / f"yield_curve_{year}.xml"
        try:
            content = _http_get(TREASURY_URL.format(year=year), attempts=2)
            frame = parse_treasury_xml(content)
            _atomic_write(path, content)
            live_years += 1
        except Exception as error:
            if not path.exists():
                warnings.append(f"{year}: {error}")
                continue
            frame = parse_treasury_xml(path.read_bytes())
            warnings.append(f"{year}: 신규 조회 실패, 저장값 사용")
        frames.append(frame)
    if not frames:
        raise RuntimeError("Treasury 수익률을 한 해도 확보하지 못했습니다.")
    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    latest = combined.index[-1].date()
    lag = business_days_between(latest, date.today())
    return combined, {
        "source": "treasury_yield_curve",
        "status": "ok" if live_years == len(frames) and lag <= 3 else "warning",
        "provider": "treasury",
        "last_value_date": latest.isoformat(),
        "retrieved_at": _now_utc().isoformat(timespec="seconds"),
        "stale_business_days": lag,
        "row_count": len(combined),
        "duplicate_count": 0,
        "null_count": int(combined.isna().sum().sum()),
        "warning_message": "; ".join(warnings) or None,
    }


def business_days_between(start: date, end: date) -> int:
    if start >= end:
        return 0
    return int(np.busday_count(start.isoformat(), (end + timedelta(days=1)).isoformat()))


def normalize_source_statuses(
    statuses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = []
    for item in statuses:
        source_id = str(item.get("source") or "")
        provider = str(item.get("provider") or "")
        if source_id.startswith("price:"):
            source_url = (
                "https://stooq.com/"
                if provider == "stooq"
                else "https://query2.finance.yahoo.com/"
            )
        elif source_id.startswith("sec:"):
            source_url = SOURCE_URLS["sec_company_facts"]
        else:
            source_url = SOURCE_URLS.get(source_id)
        lag = item.get("stale_business_days")
        normalized.append(
            {
                **item,
                "source_url": source_url,
                "available_at_utc": item.get("available_at_utc"),
                "is_stale": isinstance(lag, int) and lag > 3,
                "quality_flag": item.get("status") or "unknown",
            }
        )
    return normalized


def rolling_beta(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int = 120,
    min_observations: int = 60,
) -> pd.Series:
    aligned = pd.concat(
        [asset_returns.rename("asset"), benchmark_returns.rename("benchmark")], axis=1
    )
    covariance = aligned["asset"].rolling(window, min_periods=min_observations).cov(
        aligned["benchmark"]
    )
    variance = aligned["benchmark"].rolling(
        window, min_periods=min_observations
    ).var()
    beta = covariance / variance.replace(0, np.nan)
    beta.name = "beta"
    return beta


def rolling_percentile(
    series: pd.Series,
    window: int = 756,
    min_observations: int = 252,
) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")

    def current_average_rank(window_values: np.ndarray) -> float:
        clean = window_values[np.isfinite(window_values)]
        if len(clean) < min_observations:
            return np.nan
        current = window_values[-1]
        if not np.isfinite(current):
            return np.nan
        less = np.sum(clean < current)
        equal = np.sum(clean == current)
        average_rank = less + (equal + 1) / 2
        return float(average_rank / len(clean) * 100)

    return values.rolling(window, min_periods=min_observations).apply(
        current_average_rank, raw=True
    )


def _score_band(score: float | None, bands: list[dict[str, Any]]) -> tuple[str, str]:
    if score is None or not np.isfinite(score):
        return "Unavailable", "산출불가"
    for band in bands:
        if float(band["min"]) <= score < float(band["max"]):
            return str(band["id"]), str(band["label"])
    return "Unavailable", "산출불가"


def _price_matrix(prices: dict[str, pd.DataFrame], asof: date | None = None) -> pd.DataFrame:
    calendar = prices["QQQ"].index
    if asof:
        calendar = calendar[calendar.date <= asof]
    matrix = pd.DataFrame(index=calendar)
    for asset in PRICE_ASSETS:
        series = prices[asset]["close"]
        if asof:
            series = series[series.index.date <= asof]
        matrix[asset] = series.reindex(calendar)
    return matrix


def calculate_proxy_history(
    prices: dict[str, pd.DataFrame],
    ofr: pd.DataFrame,
    treasury: pd.DataFrame,
    config: dict[str, Any],
    *,
    asof: date | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    calculation = config["calculation"]
    matrix = _price_matrix(prices, asof)
    returns = np.log(matrix / matrix.shift(1))
    beta_window = int(calculation["beta_window"])
    beta_min = int(calculation["beta_min_observations"])
    residual_window = int(calculation["residual_window"])
    minimum_members = int(calculation["minimum_member_coverage"])

    residual_1d = pd.DataFrame(index=matrix.index)
    residual_5d = pd.DataFrame(index=matrix.index)
    betas = pd.DataFrame(index=matrix.index)
    drawdowns = pd.DataFrame(index=matrix.index)
    for member in M7_MEMBERS:
        beta = rolling_beta(returns[member], returns["QQQ"], beta_window, beta_min)
        residual = returns[member] - beta * returns["QQQ"]
        betas[member] = beta
        residual_1d[member] = residual
        residual_5d[member] = residual.rolling(
            residual_window, min_periods=residual_window
        ).sum()
        rolling_high = matrix[member].rolling(
            int(calculation["drawdown_window"]), min_periods=20
        ).max()
        drawdowns[member] = 1 - matrix[member] / rolling_high

    member_count = residual_5d.notna().sum(axis=1)
    valid_members = member_count >= minimum_members
    idio_loss = -residual_5d.median(axis=1, skipna=True).where(valid_members)
    worst_member_loss = -residual_5d.min(axis=1, skipna=True).where(valid_members)
    dispersion = (
        residual_5d.quantile(0.75, axis=1) - residual_5d.quantile(0.25, axis=1)
    ).where(valid_members)
    median_drawdown = drawdowns.median(axis=1, skipna=True).where(valid_members)

    credit_residuals = []
    for asset in ("IGIB", "LQD"):
        beta = rolling_beta(returns[asset], returns["IEF"], beta_window, beta_min)
        residual = returns[asset] - beta * returns["IEF"]
        credit_residuals.append(
            residual.rolling(residual_window, min_periods=residual_window)
            .sum()
            .rename(asset)
        )
    credit_frame = pd.concat(credit_residuals, axis=1)
    ig_credit_loss = -credit_frame.mean(axis=1, skipna=True)
    ig_credit_loss = ig_credit_loss.where(credit_frame.notna().sum(axis=1) >= 1)
    hyg_5d = matrix["HYG"].pct_change(residual_window, fill_method=None)
    lqd_5d = matrix["LQD"].pct_change(residual_window, fill_method=None)
    hy_ig_relative = -(hyg_5d - lqd_5d)

    macro_fill = int(calculation["macro_forward_fill_days"])
    ofr_aligned = ofr.reindex(matrix.index).ffill(limit=macro_fill)
    treasury_aligned = treasury.reindex(matrix.index).ffill(limit=macro_fill)
    history = pd.DataFrame(
        {
            "m7_idio_loss_5d": idio_loss,
            "worst_member_loss_5d": worst_member_loss,
            "m7_dispersion_5d": dispersion,
            "m7_median_drawdown_60d": median_drawdown,
            "ig_credit_loss_5d": ig_credit_loss,
            "hy_ig_relative_5d": hy_ig_relative,
            "ofr_fsi": ofr_aligned.get("ofr_fsi"),
            "ofr_credit": ofr_aligned.get("ofr_credit"),
            "treasury_2y": treasury_aligned.get("treasury_2y"),
            "treasury_5y": treasury_aligned.get("treasury_5y"),
            "treasury_10y": treasury_aligned.get("treasury_10y"),
            "treasury_30y": treasury_aligned.get("treasury_30y"),
            "m7_member_count": member_count,
        },
        index=matrix.index,
    )
    history["treasury_2s10s"] = history["treasury_10y"] - history["treasury_2y"]
    history["treasury_5s30s"] = history["treasury_30y"] - history["treasury_5y"]
    history["treasury_10y_change_1d"] = history["treasury_10y"].diff()
    history["treasury_10y_change_5d"] = history["treasury_10y"].diff(5)

    percentile_window = int(calculation["percentile_window"])
    percentile_min = int(calculation["percentile_min_observations"])
    component_scores = {}
    for component in config["weights"]:
        component_scores[component] = rolling_percentile(
            history[component], percentile_window, percentile_min
        )
        history[f"{component}_score"] = component_scores[component]

    weighted = pd.DataFrame(component_scores).mul(
        pd.Series(config["weights"], dtype=float), axis=1
    )
    available_weights = pd.DataFrame(
        {
            component: score.notna().astype(float) * float(config["weights"][component])
            for component, score in component_scores.items()
        }
    )
    coverage = available_weights.sum(axis=1)
    combined = weighted.sum(axis=1, min_count=1) / coverage.replace(0, np.nan)
    combined = combined.where(
        coverage >= float(calculation["minimum_score_coverage"])
    )
    history["combined_score"] = combined.clip(0, 100)
    history["component_coverage"] = coverage
    history["effective_weight"] = coverage
    history["score_change_1d"] = history["combined_score"].diff(1)
    history["score_change_5d"] = history["combined_score"].diff(5)
    history["score_band"] = [
        _score_band(value, config["bands"])[0]
        for value in history["combined_score"].to_numpy()
    ]
    history["score_quality"] = np.select(
        [
            history["combined_score"].isna(),
            (history["component_coverage"] >= 0.999)
            & (history["m7_member_count"] == len(M7_MEMBERS)),
        ],
        ["unavailable", "complete"],
        default="partial",
    )
    history.index.name = "value_date"

    valid_dates = history["combined_score"].dropna().index
    if valid_dates.empty:
        raise RuntimeError(
            "M7 점수를 산출하지 못했습니다. 최소 252개 과거 관측치와 70% 구성요소가 필요합니다."
        )
    latest_date = valid_dates[-1]
    member_rows = []
    for member in M7_MEMBERS:
        residual_value = residual_5d.at[latest_date, member]
        member_rows.append(
            {
                "ticker": member,
                "close": _finite_or_none(matrix.at[latest_date, member]),
                "return_1d": _finite_or_none(matrix[member].pct_change().at[latest_date]),
                "return_5d": _finite_or_none(
                    matrix[member].pct_change(residual_window).at[latest_date]
                ),
                "rolling_beta": _finite_or_none(betas.at[latest_date, member]),
                "residual_1d": _finite_or_none(residual_1d.at[latest_date, member]),
                "residual_5d": _finite_or_none(residual_value),
                "drawdown_60d": _finite_or_none(drawdowns.at[latest_date, member]),
                "quality_flag": "ok" if np.isfinite(residual_value) else "missing",
            }
        )
    ranked = sorted(
        member_rows,
        key=lambda item: (
            item["residual_5d"] is None,
            item["residual_5d"] if item["residual_5d"] is not None else math.inf,
        ),
    )
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
    members = {item["ticker"]: item for item in ranked}
    return history, {"as_of": latest_date.date().isoformat(), "members": members}


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest_fact(
    payload: dict[str, Any],
    tags: tuple[str, ...],
    asof: date,
) -> dict[str, Any] | None:
    us_gaap = ((payload.get("facts") or {}).get("us-gaap") or {})
    candidates = []
    for tag in tags:
        fact = us_gaap.get(tag) or {}
        for item in ((fact.get("units") or {}).get("USD") or []):
            form = item.get("form")
            filed_text = item.get("filed")
            if form not in {"10-Q", "10-K"} or not filed_text:
                continue
            try:
                filed = date.fromisoformat(str(filed_text)[:10])
            except ValueError:
                continue
            if filed > asof or item.get("val") is None:
                continue
            candidates.append(
                {
                    "tag": tag,
                    "value": float(item["val"]),
                    "form": form,
                    "filing_date": filed.isoformat(),
                    "period_end": item.get("end"),
                    "accession_number": item.get("accn"),
                }
            )
    return max(
        candidates,
        key=lambda item: (item["filing_date"], item.get("period_end") or ""),
        default=None,
    )


def extract_sec_financials(
    payload: dict[str, Any],
    *,
    asof: date | None = None,
) -> dict[str, Any]:
    asof = asof or date.today()
    cash = _latest_fact(
        payload,
        (
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalentsAtCarryingValue",
        ),
        asof,
    )
    investments = _latest_fact(payload, ("ShortTermInvestments",), asof)
    debt_total = _latest_fact(payload, ("LongTermDebt",), asof)
    debt_current = _latest_fact(payload, ("LongTermDebtCurrent",), asof)
    debt_noncurrent = _latest_fact(payload, ("LongTermDebtNoncurrent",), asof)
    borrowings = _latest_fact(payload, ("ShortTermBorrowings",), asof)

    cash_value = (cash or {}).get("value", 0.0) + (investments or {}).get("value", 0.0)
    if debt_total:
        debt_value = debt_total["value"] + (borrowings or {}).get("value", 0.0)
        debt_facts = [debt_total, borrowings]
    else:
        debt_value = sum(
            (item or {}).get("value", 0.0)
            for item in (debt_current, debt_noncurrent, borrowings)
        )
        debt_facts = [debt_current, debt_noncurrent, borrowings]
    used = [item for item in [cash, investments, *debt_facts] if item]
    latest_filing = max(
        (item["filing_date"] for item in used),
        default=None,
    )
    return {
        "cash_and_short_term_investments": cash_value if cash_value else None,
        "total_debt": debt_value if debt_value else None,
        "net_debt": debt_value - cash_value if cash_value or debt_value else None,
        "debt_to_cash": debt_value / cash_value if cash_value > 0 else None,
        "latest_filing_date": latest_filing,
        "days_since_filing": (
            (asof - date.fromisoformat(latest_filing)).days if latest_filing else None
        ),
        "provenance": used,
    }


def fetch_sec_financials(asof: date | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        return {}, [
            {
                "source": "sec_company_facts",
                "status": "disabled",
                "provider": "sec",
                "last_value_date": None,
                "retrieved_at": _now_utc().isoformat(timespec="seconds"),
                "stale_business_days": None,
                "row_count": 0,
                "duplicate_count": 0,
                "null_count": 0,
                "warning_message": "SEC_USER_AGENT 미설정으로 저빈도 재무 오버레이를 건너뛰었습니다.",
            }
        ]
    financials: dict[str, Any] = {}
    statuses = []
    for ticker, cik in SEC_CIKS.items():
        path = RAW_DIR / "sec" / f"{ticker.lower()}.json"
        warning = None
        try:
            content = _http_get(
                SEC_URL.format(cik=cik),
                headers={"User-Agent": user_agent, "Accept": "application/json"},
                attempts=2,
            )
            payload = json.loads(content.decode("utf-8"))
            _atomic_write(path, content)
            provider = "sec"
        except Exception as error:
            if not path.exists():
                statuses.append(
                    {
                        "source": f"sec:{ticker}",
                        "status": "warning",
                        "provider": "sec",
                        "last_value_date": None,
                        "retrieved_at": _now_utc().isoformat(timespec="seconds"),
                        "stale_business_days": None,
                        "row_count": 0,
                        "duplicate_count": 0,
                        "null_count": 1,
                        "warning_message": str(error),
                    }
                )
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            provider = "local_cache"
            warning = f"SEC 신규 조회 실패로 저장값 사용: {error}"
        financial = extract_sec_financials(payload, asof=asof)
        financials[ticker] = financial
        statuses.append(
            {
                "source": f"sec:{ticker}",
                "status": "ok" if provider == "sec" else "warning",
                "provider": provider,
                "last_value_date": financial.get("latest_filing_date"),
                "retrieved_at": _now_utc().isoformat(timespec="seconds"),
                "stale_business_days": None,
                "row_count": 1,
                "duplicate_count": 0,
                "null_count": int(financial.get("cash_and_short_term_investments") is None),
                "warning_message": warning,
            }
        )
    return financials, statuses


def _serialize_number(value: Any, digits: int = 6) -> float | None:
    number = _finite_or_none(value)
    return round(number, digits) if number is not None else None


def _latest_driver(row: pd.Series, weights: dict[str, float]) -> dict[str, Any] | None:
    drivers = []
    for component, weight in weights.items():
        score = _finite_or_none(row.get(f"{component}_score"))
        if score is None:
            continue
        drivers.append(
            {
                "id": component,
                "score": round(score, 1),
                "weight": float(weight),
                "weighted_points": round(score * float(weight), 2),
            }
        )
    return max(drivers, key=lambda item: item["weighted_points"], default=None)


def build_proxy(
    prices: dict[str, pd.DataFrame],
    ofr: pd.DataFrame,
    treasury: pd.DataFrame,
    config: dict[str, Any],
    *,
    source_statuses: list[dict[str, Any]] | None = None,
    financials: dict[str, Any] | None = None,
    asof: date | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    history, member_payload = calculate_proxy_history(
        prices, ofr, treasury, config, asof=asof
    )
    latest_date = pd.Timestamp(member_payload["as_of"])
    latest = history.loc[latest_date]
    score = float(latest["combined_score"])
    band_id, band_label = _score_band(score, config["bands"])
    ranked_members = sorted(
        member_payload["members"].values(), key=lambda item: item["rank"]
    )
    worst_member = ranked_members[0] if ranked_members else {}
    driver = _latest_driver(latest, config["weights"])
    statuses = source_statuses or []
    price_fallback = any(
        item.get("source", "").startswith("price:")
        and item.get("provider") != "stooq"
        for item in statuses
    )
    if price_fallback:
        history.loc[
            history["score_quality"] == "complete", "score_quality"
        ] = "partial"
        latest = history.loc[latest_date]
    quality = str(latest["score_quality"])

    public_columns = [
        "combined_score",
        "score_change_1d",
        "score_change_5d",
        "component_coverage",
        "m7_member_count",
        "m7_idio_loss_5d_score",
        "worst_member_loss_5d_score",
        "m7_median_drawdown_60d_score",
        "ig_credit_loss_5d_score",
        "hy_ig_relative_5d_score",
        "ofr_fsi_score",
        "treasury_5y",
        "treasury_10y",
        "treasury_2s10s",
    ]
    valid_history = history.loc[history["combined_score"].notna(), public_columns].tail(800)
    series = []
    for value_date, row in valid_history.iterrows():
        series.append(
            {
                "date": value_date.date().isoformat(),
                **{
                    column: _serialize_number(row[column], 4)
                    for column in public_columns
                },
            }
        )

    latest_summary = {
        "as_of": latest_date.date().isoformat(),
        "updated_at_kst": _now_kst_text(),
        "score": round(score, 1),
        "score_band": band_id,
        "score_band_label": band_label,
        "change_1d": _serialize_number(latest["score_change_1d"], 1),
        "change_5d": _serialize_number(latest["score_change_5d"], 1),
        "worst_member": worst_member.get("ticker"),
        "worst_member_residual_5d": _serialize_number(
            worst_member.get("residual_5d"), 6
        ),
        "m7_idio_loss_5d": _serialize_number(latest["m7_idio_loss_5d"], 6),
        "m7_dispersion_5d": _serialize_number(latest["m7_dispersion_5d"], 6),
        "ig_credit_loss_5d": _serialize_number(latest["ig_credit_loss_5d"], 6),
        "hy_ig_relative_5d": _serialize_number(latest["hy_ig_relative_5d"], 6),
        "ofr_fsi": _serialize_number(latest["ofr_fsi"], 4),
        "treasury_5y": _serialize_number(latest["treasury_5y"], 4),
        "treasury_10y": _serialize_number(latest["treasury_10y"], 4),
        "treasury_2s10s": _serialize_number(latest["treasury_2s10s"], 4),
        "coverage": _serialize_number(latest["component_coverage"], 3),
        "member_coverage": int(latest["m7_member_count"]),
        "quality": quality,
        "top_driver": driver,
        "disclaimer": DISCLAIMER,
    }
    public_payload = {
        "schemaVersion": 1,
        "name": "M7 Credit Stress Proxy",
        "generatedAt": _now_kst_text(),
        "latest": latest_summary,
        "series": series,
        "members": ranked_members,
        "financials": financials or {},
        "sources": statuses,
        "methodology": {
            "beta": "QQQ 대비 120거래일 rolling beta, 최소 60일",
            "horizon": "최근 5거래일 잔차손실과 신용 ETF 상대약세",
            "normalization": "756거래일 rolling percentile, 최소 252일",
            "weights": config["weights"],
            "minimumScoreCoverage": config["calculation"]["minimum_score_coverage"],
            "operatingRole": "시장리스크 관찰카드 · 종합점수 가중치 0",
        },
        "disclaimer": DISCLAIMER,
    }
    return history, member_payload, public_payload


def write_outputs(
    history: pd.DataFrame,
    member_payload: dict[str, Any],
    public_payload: dict[str, Any],
    source_statuses: list[dict[str, Any]],
) -> None:
    output = history.reset_index()
    output["value_date"] = pd.to_datetime(output["value_date"]).dt.strftime("%Y-%m-%d")
    _atomic_write(PROCESSED_FILE, output.to_csv(index=False).encode("utf-8"))
    _atomic_write_json(
        MEMBERS_FILE,
        {
            "as_of": member_payload["as_of"],
            "members": sorted(
                member_payload["members"].values(), key=lambda item: item["rank"]
            ),
        },
    )
    _atomic_write_json(
        QUALITY_FILE,
        {
            "generatedAt": _now_kst_text(),
            "sources": source_statuses,
            "summary": {
                "status": (
                    "warning"
                    if any(item.get("status") != "ok" for item in source_statuses)
                    else "ok"
                ),
                "sourceCount": len(source_statuses),
                "warningCount": sum(
                    item.get("status") not in {"ok", "disabled"}
                    for item in source_statuses
                ),
            },
        },
    )
    _atomic_write_json(PUBLIC_FILE, public_payload)


def run_pipeline(
    *,
    config_path: Path = CONFIG_FILE,
    asof: date | None = None,
    include_sec: bool = True,
) -> dict[str, Any]:
    config = load_config(config_path)
    prices, price_statuses = fetch_prices(config)
    minimum_price_date = min(frame.index[0].date() for frame in prices.values())
    ofr, ofr_status = fetch_ofr()
    treasury, treasury_status = fetch_treasury(minimum_price_date.year)
    financials, sec_statuses = (
        fetch_sec_financials(asof=asof) if include_sec else ({}, [])
    )
    statuses = normalize_source_statuses(
        [*price_statuses, ofr_status, treasury_status, *sec_statuses]
    )
    history, members, public_payload = build_proxy(
        prices,
        ofr,
        treasury,
        config,
        source_statuses=statuses,
        financials=financials,
        asof=asof,
    )
    write_outputs(history, members, public_payload, statuses)
    return public_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="M7 공개시장 신용스트레스 프록시를 산출합니다."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true", help="전체 과거 이력을 다시 산출합니다.")
    mode.add_argument(
        "--update-latest", action="store_true", help="최신 공개 데이터를 반영합니다."
    )
    mode.add_argument("--asof", type=date.fromisoformat, help="해당 날짜까지로 잘라 산출합니다.")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE)
    parser.add_argument(
        "--no-sec",
        action="store_true",
        help="SEC 재무 오버레이 조회를 생략합니다.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="신규 수집 실패 시 기존 공개 파일이 있어도 오류로 종료합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        payload = run_pipeline(
            config_path=args.config,
            asof=args.asof,
            include_sec=not args.no_sec,
        )
    except Exception as error:
        if PUBLIC_FILE.exists() and not args.strict:
            print(
                "M7 신규 산출에 실패해 기존 정상 공개 파일을 유지합니다: "
                f"{error}",
                flush=True,
            )
            return
        raise
    latest = payload["latest"]
    print(
        "M7 Credit Stress Proxy "
        f"{latest['score']:.1f}점 · {latest['score_band_label']} · "
        f"기준일 {latest['as_of']} · 품질 {latest['quality']}",
        flush=True,
    )
    print(f"Wrote {PUBLIC_FILE.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
