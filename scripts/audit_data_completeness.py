from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kospi_risk.market_data_fetcher import load_source_config

try:
    from scripts.update_market_risk import (
        FRED_SERIES,
        NAVER_LATEST_INDEX_IDS,
        NAVER_MARKET_INDEXES,
        NAVER_SYMBOLS,
        TICKERS,
    )
except ModuleNotFoundError:
    from update_market_risk import (
        FRED_SERIES,
        NAVER_LATEST_INDEX_IDS,
        NAVER_MARKET_INDEXES,
        NAVER_SYMBOLS,
        TICKERS,
    )


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT = ROOT / "data" / "data-quality.json"
ML_SOURCE_METADATA_FILE = ROOT / "data" / "raw" / "market_data_sources.json"

ARTIFACTS = {
    "dashboard": ("시장리스크", ROOT / "data" / "risk-dashboard.json"),
    "snapshot": ("원천 스냅샷", ROOT / "data" / "market-risk-snapshot.json"),
    "timeseries": ("시장리스크 시계열", ROOT / "data" / "market-risk-timeseries.json"),
    "backtest": ("시장 백테스트", ROOT / "data" / "market-risk-backtest.json"),
    "stress": ("스트레스 이력", ROOT / "data" / "market-stress-episodes.json"),
    "els": ("ELS 지수위험", ROOT / "data" / "els-index-risk.json"),
    "hmm": ("HMM 레짐", ROOT / "data" / "hmm-regime.json"),
    "ml": ("ML 위험신호", ROOT / "data" / "ml-risk-signal.json"),
    "marketHistory": ("장기 시장 캐시", ROOT / "data" / "market-history-cache.json"),
    "naverMarketHistory": ("Naver 시장지표 캐시", ROOT / "data" / "naver-marketindex-history.json"),
}

STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def artifact_generated_at(payload: dict[str, Any]) -> Any:
    return payload.get("generatedAt") or (payload.get("metadata") or {}).get("generatedAt")


def business_day_lag(last_date: date, reference_date: date) -> int:
    if last_date >= reference_date:
        return 0
    lag = 0
    cursor = last_date + timedelta(days=1)
    while cursor <= reference_date:
        if cursor.weekday() < 5:
            lag += 1
        cursor += timedelta(days=1)
    return lag


def worst_status(statuses: list[str]) -> str:
    return max(statuses or ["ok"], key=lambda item: STATUS_RANK.get(item, 2))


def make_check(
    check_id: str,
    category: str,
    label: str,
    status: str,
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "category": category,
        "label": label,
        "status": status,
        "detail": detail,
        **extra,
    }


def source_freshness_status(lag: int, warning_lag: int, error_lag: int) -> str:
    if lag > error_lag:
        return "error"
    if lag > warning_lag:
        return "warning"
    return "ok"


def assess_source_group(
    group_id: str,
    label: str,
    items: dict[str, Any],
    expected: dict[str, Any],
    reference_date: date,
    *,
    warning_lag: int,
    error_lag: int,
    min_observations: int,
    weekly_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    weekly_ids = weekly_ids or set()
    checks = []
    fresh_count = 0
    fallback_count = 0
    last_dates = []

    for item_id, config in expected.items():
        item = items.get(item_id)
        item_label = config.get("label", item_id)
        if not item:
            checks.append(
                make_check(
                    f"source:{group_id}:{item_id}",
                    "source",
                    item_label,
                    "error",
                    "필수 시계열이 스냅샷에 없습니다.",
                    groupId=group_id,
                )
            )
            continue

        last_date = parse_date(item.get("lastDate"))
        observations = int(item.get("observations") or 0)
        fetch_status = str(item.get("fetchStatus") or "live")
        fallback = (
            "fallback" in fetch_status
            or "cached" in fetch_status
            or fetch_status.startswith("yahoo_only")
        )
        fallback_count += int(fallback)
        if last_date:
            last_dates.append(last_date)

        item_warning_lag = warning_lag
        item_error_lag = error_lag
        if item_id in weekly_ids:
            item_warning_lag = max(item_warning_lag, 10)
            item_error_lag = max(item_error_lag, 15)

        if not last_date:
            status = "error"
            detail = "최신 관측일이 없습니다."
        elif observations < min_observations:
            status = "error"
            detail = f"관측치 {observations}개로 최소 {min_observations}개보다 적습니다."
        else:
            lag = business_day_lag(last_date, reference_date)
            status = source_freshness_status(lag, item_warning_lag, item_error_lag)
            if fallback and status == "ok":
                status = "warning"
            detail = (
                f"최신 {last_date.isoformat()} · 영업일 시차 {lag}일 · "
                f"관측치 {observations}개 · {fetch_status}"
            )
            fresh_count += int(lag <= item_warning_lag)

        checks.append(
            make_check(
                f"source:{group_id}:{item_id}",
                "source",
                item_label,
                status,
                detail,
                groupId=group_id,
                lastDate=last_date.isoformat() if last_date else None,
                observations=observations,
                fetchStatus=fetch_status,
            )
        )

    statuses = [item["status"] for item in checks]
    present_count = sum(item_id in items for item_id in expected)
    oldest = min(last_dates).isoformat() if last_dates else None
    latest = max(last_dates).isoformat() if last_dates else None
    group = {
        "id": group_id,
        "label": label,
        "status": worst_status(statuses),
        "seriesExpected": len(expected),
        "seriesPresent": present_count,
        "freshCount": fresh_count,
        "staleCount": max(0, present_count - fresh_count),
        "fallbackCount": fallback_count,
        "oldestLastDate": oldest,
        "latestLastDate": latest,
        "detail": (
            f"{present_count}/{len(expected)}개 수집 · 허용시차 내 {fresh_count}개"
            + (f" · 대체값 {fallback_count}개" if fallback_count else "")
        ),
    }
    return group, checks


def assess_ml_source_group(
    metadata: dict[str, Any],
    reference_date: date,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_source_config(ROOT / "configs" / "data_sources.yaml")
    expected = {
        column: {**spec, "required": required}
        for required, section in [(True, "required"), (False, "optional")]
        for column, spec in (config.get(section) or {}).items()
    }
    items = {item.get("column"): item for item in metadata.get("sources", []) if item.get("column")}
    checks = []
    fresh_count = 0
    fallback_count = 0
    dates = []

    for column, spec in expected.items():
        item = items.get(column)
        required = bool(spec["required"])
        label = spec.get("label", column)
        if not item:
            checks.append(
                make_check(
                    f"source:ml:{column}",
                    "source",
                    label,
                    "error" if required else "warning",
                    "ML 원천 메타데이터에 항목이 없습니다.",
                    groupId="ml-input",
                )
            )
            continue

        source_status = str(item.get("status") or "failed")
        last_date = parse_date(item.get("lastDate"))
        observations = int(item.get("rows") or 0)
        fallback = source_status in {"cached", "supplemented"}
        fallback_count += int(fallback)
        if last_date:
            dates.append(last_date)
        lag = business_day_lag(last_date, reference_date) if last_date else None

        if source_status == "failed":
            status = "error" if required else "warning"
        elif source_status == "skipped_low_coverage":
            status = "warning"
        elif not last_date or (required and observations < 1500):
            status = "error" if required else "warning"
        elif lag is not None and lag > (6 if required else 10):
            status = "error" if required else "warning"
        elif item.get("warning"):
            status = "warning"
        elif fallback and source_status == "cached":
            status = "warning"
        else:
            status = "ok"
            fresh_count += 1

        detail = (
            f"{item.get('provider', spec.get('provider', '-'))} · {source_status} · "
            f"최신 {item.get('lastDate', '-')} · 관측치 {observations}개"
        )
        if item.get("coverageRatio") is not None:
            detail += f" · 커버리지 {float(item['coverageRatio']) * 100:.1f}%"
        checks.append(
            make_check(
                f"source:ml:{column}",
                "source",
                label,
                status,
                detail,
                groupId="ml-input",
                lastDate=item.get("lastDate"),
                observations=observations,
                fetchStatus=source_status,
                required=required,
            )
        )

    statuses = [item["status"] for item in checks]
    present_count = sum(column in items for column in expected)
    group = {
        "id": "ml-input",
        "label": "ML 입력 원천",
        "status": worst_status(statuses),
        "seriesExpected": len(expected),
        "seriesPresent": present_count,
        "freshCount": fresh_count,
        "staleCount": max(0, present_count - fresh_count),
        "fallbackCount": fallback_count,
        "oldestLastDate": min(dates).isoformat() if dates else None,
        "latestLastDate": max(dates).isoformat() if dates else None,
        "detail": (
            f"{present_count}/{len(expected)}개 수집 · 사용 가능 {fresh_count}개"
            + (f" · 보강/대체 {fallback_count}개" if fallback_count else "")
        ),
    }
    return group, checks


def validate_series_rows(
    check_id: str,
    label: str,
    rows: list[dict[str, Any]],
    *,
    min_rows: int,
    value_key: str,
) -> dict[str, Any]:
    dates = [str(row.get("date") or "") for row in rows]
    numeric_values = []
    for row in rows:
        try:
            numeric_values.append(float(row.get(value_key)))
        except (TypeError, ValueError):
            continue
    invalid_values = sum(not math.isfinite(value) for value in numeric_values)
    sorted_unique = dates == sorted(dates) and len(dates) == len(set(dates)) and all(dates)

    if len(rows) < min_rows or not sorted_unique or len(numeric_values) != len(rows) or invalid_values:
        status = "error"
    else:
        status = "ok"
    detail = (
        f"{len(rows)}개 관측치 · 날짜 정렬/중복 {'정상' if sorted_unique else '오류'} · "
        f"유효값 {len(numeric_values)}/{len(rows)}개"
    )
    return make_check(check_id, "series", label, status, detail)


def artifact_checks(
    data: dict[str, dict[str, Any]],
    reference_date: date,
) -> list[dict[str, Any]]:
    checks = []
    for artifact_id, (label, path) in ARTIFACTS.items():
        payload = data.get(artifact_id)
        if payload is None:
            checks.append(
                make_check(
                    f"artifact:{artifact_id}",
                    "artifact",
                    label,
                    "error",
                    f"{path.relative_to(ROOT)} 파일을 읽지 못했습니다.",
                )
            )
            continue
        generated_at = artifact_generated_at(payload)
        generated_date = parse_date(generated_at)
        if not generated_date:
            status = "error"
            detail = "생성시각 메타데이터가 없습니다."
        else:
            lag = business_day_lag(generated_date, reference_date)
            status = source_freshness_status(lag, 1, 3)
            detail = f"생성시각 {generated_at} · 영업일 시차 {lag}일"
        checks.append(
            make_check(
                f"artifact:{artifact_id}",
                "artifact",
                label,
                status,
                detail,
                generatedAt=generated_at,
            )
        )
    return checks


def cross_artifact_checks(data: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    dashboard = data.get("dashboard") or {}
    timeseries = data.get("timeseries") or {}
    market_section = next(
        (section for section in dashboard.get("sections", []) if section.get("id") == "market"),
        {},
    )
    indicator_ids = {item.get("id") for item in market_section.get("indicators", []) if item.get("id")}
    timeseries_ids = set((timeseries.get("series") or {}).keys())
    missing_trends = sorted(indicator_ids - timeseries_ids)
    checks.append(
        make_check(
            "cross:indicator-timeseries",
            "alignment",
            "시장지표와 추이 시계열",
            "error" if missing_trends else "ok",
            f"지표 {len(indicator_ids)}개 · 추이 {len(timeseries_ids)}개"
            + (f" · 누락 {', '.join(missing_trends)}" if missing_trends else " · 누락 없음"),
        )
    )

    for item_id, rows in (timeseries.get("series") or {}).items():
        checks.append(
            validate_series_rows(
                f"series:market:{item_id}",
                f"{item_id} 추이",
                rows,
                min_rows=60,
                value_key="value",
            )
        )

    els_indices = {item.get("id"): item for item in (data.get("els") or {}).get("indices", [])}
    hmm_indices = {item.get("id"): item for item in (data.get("hmm") or {}).get("indices", [])}
    required_indices = {"spx", "sx5e", "nky", "hscei", "kospi200"}
    for artifact_id, indices in [("els", els_indices), ("hmm", hmm_indices)]:
        missing = sorted(required_indices - set(indices))
        misaligned = sorted(
            item_id
            for item_id, item in indices.items()
            if not item.get("series") or item.get("lastDate") != item["series"][-1].get("date")
        )
        status = "error" if missing or misaligned else "ok"
        checks.append(
            make_check(
                f"cross:{artifact_id}-indices",
                "alignment",
                f"{artifact_id.upper()} 지수 시계열",
                status,
                f"필수지수 {len(required_indices) - len(missing)}/{len(required_indices)}개"
                + (f" · 누락 {', '.join(missing)}" if missing else "")
                + (f" · 말단불일치 {', '.join(misaligned)}" if misaligned else " · 말단일치"),
            )
        )

    els_kospi = els_indices.get("kospi200", {})
    hmm_kospi = hmm_indices.get("kospi200", {})
    els_date = parse_date(els_kospi.get("lastDate"))
    hmm_date = parse_date(hmm_kospi.get("lastDate"))
    kospi_status = "ok" if els_date and hmm_date and hmm_date >= els_date else "error"
    checks.append(
        make_check(
            "cross:kospi200-hmm-els",
            "alignment",
            "KOSPI200 HMM·ELS 기준일",
            kospi_status,
            f"HMM {hmm_kospi.get('lastDate', '-')} · ELS {els_kospi.get('lastDate', '-')}",
        )
    )

    snapshot_kospi = ((data.get("snapshot") or {}).get("yahooSymbols") or {}).get("kospi", {})
    ml_latest = (data.get("ml") or {}).get("latest") or {}
    snapshot_date = parse_date(snapshot_kospi.get("lastDate"))
    ml_date = parse_date(ml_latest.get("date"))
    ml_status = "ok" if snapshot_date and ml_date and ml_date >= snapshot_date else "warning"
    checks.append(
        make_check(
            "cross:kospi-ml-snapshot",
            "alignment",
            "KOSPI 시장지표·ML 기준일",
            ml_status,
            f"시장지표 {snapshot_kospi.get('lastDate', '-')} · ML {ml_latest.get('date', '-')}",
        )
    )

    snapshot_value = snapshot_kospi.get("lastClose")
    ml_value = ml_latest.get("kospi")
    if snapshot_date and ml_date and snapshot_date == ml_date and snapshot_value and ml_value:
        deviation_pct = abs(float(snapshot_value) / float(ml_value) - 1) * 100
        value_status = "error" if deviation_pct > 3 else "warning" if deviation_pct > 1 else "ok"
        checks.append(
            make_check(
                "cross:kospi-ml-value",
                "alignment",
                "KOSPI 시장지표·ML 가격",
                value_status,
                f"시장지표 {float(snapshot_value):,.2f} · ML {float(ml_value):,.2f} · 편차 {deviation_pct:.2f}%",
            )
        )

    naver_symbols = (data.get("snapshot") or {}).get("naverSymbols") or {}
    yahoo_symbols = (data.get("snapshot") or {}).get("yahooSymbols") or {}
    duplicate_deviations = []
    for item_id in sorted(set(naver_symbols) & set(yahoo_symbols)):
        yahoo_item = yahoo_symbols[item_id]
        naver_item = naver_symbols[item_id]
        if (
            yahoo_item.get("lastDate") != naver_item.get("lastDate")
            or not yahoo_item.get("lastClose")
            or not naver_item.get("lastClose")
        ):
            continue
        deviation = abs(float(yahoo_item["lastClose"]) / float(naver_item["lastClose"]) - 1) * 100
        duplicate_deviations.append((item_id, deviation))
    max_item, max_deviation = max(duplicate_deviations, key=lambda item: item[1], default=("-", 0.0))
    duplicate_status = "error" if max_deviation > 5 else "warning" if max_deviation > 2 else "ok"
    checks.append(
        make_check(
            "cross:naver-yahoo-domestic-values",
            "alignment",
            "국내주식 Yahoo·Naver 가격",
            duplicate_status,
            f"동일 기준일 비교 {len(duplicate_deviations)}개 · 최대 {max_item} {max_deviation:.2f}%",
        )
    )

    els_close = els_kospi.get("lastClose")
    hmm_close = hmm_kospi.get("lastClose")
    if els_date and hmm_date and els_date == hmm_date and els_close and hmm_close:
        deviation_pct = abs(float(els_close) / float(hmm_close) - 1) * 100
        value_status = "error" if deviation_pct > 3 else "warning" if deviation_pct > 1 else "ok"
        checks.append(
            make_check(
                "cross:kospi200-hmm-els-value",
                "alignment",
                "KOSPI200 HMM·ELS 가격",
                value_status,
                f"HMM {float(hmm_close):,.2f} · ELS {float(els_close):,.2f} · 편차 {deviation_pct:.2f}%",
            )
        )
    return checks


def cache_series_checks(
    data: dict[str, dict[str, Any]],
    reference_date: date,
) -> list[dict[str, Any]]:
    checks = []
    history = data.get("marketHistory") or {}
    for group_id, expected, min_rows in [
        ("yahoo", TICKERS, 80),
        ("naver", NAVER_SYMBOLS, 80),
        ("fred", FRED_SERIES, 60),
    ]:
        rows_by_id = history.get(group_id) or {}
        missing = sorted(set(expected) - set(rows_by_id))
        checks.append(
            make_check(
                f"cache:{group_id}:coverage",
                "cache",
                f"{group_id} 장기 캐시",
                "error" if missing else "ok",
                f"{len(rows_by_id)}/{len(expected)}개"
                + (f" · 누락 {', '.join(missing)}" if missing else " · 누락 없음"),
            )
        )
        for item_id, rows in rows_by_id.items():
            checks.append(
                validate_series_rows(
                    f"cache:{group_id}:{item_id}",
                    f"{group_id}/{item_id}",
                    rows,
                    min_rows=min_rows,
                    value_key="close",
                )
            )

    market_history = data.get("naverMarketHistory") or {}
    rows_by_id = market_history.get("series") or {}
    missing = sorted(set(NAVER_MARKET_INDEXES) - set(rows_by_id))
    checks.append(
        make_check(
            "cache:naver-market:coverage",
            "cache",
            "Naver 시장지표 캐시",
            "error" if missing else "ok",
            f"{len(rows_by_id)}/{len(NAVER_MARKET_INDEXES)}개"
            + (f" · 누락 {', '.join(missing)}" if missing else " · 누락 없음"),
        )
    )
    for item_id, rows in rows_by_id.items():
        minimum = int(NAVER_MARKET_INDEXES.get(item_id, {}).get("min_observations", 60))
        checks.append(
            validate_series_rows(
                f"cache:naver-market:{item_id}",
                f"Naver 시장지표/{item_id}",
                rows,
                min_rows=minimum,
                value_key="close",
            )
        )

    live_snapshots = market_history.get("liveSnapshots") or {}
    missing_live = sorted(set(NAVER_LATEST_INDEX_IDS) - set(live_snapshots))
    checks.append(
        make_check(
            "cache:naver-market-live:coverage",
            "cache",
            "시장지표 최신 스냅샷",
            "warning" if missing_live else "ok",
            f"{len(live_snapshots)}/{len(NAVER_LATEST_INDEX_IDS)}개"
            + (f" · 누락 {', '.join(missing_live)}" if missing_live else " · 누락 없음"),
        )
    )
    for item_id, snapshot in live_snapshots.items():
        observed_date = parse_date(snapshot.get("observedAt"))
        current_value = snapshot.get("close")
        previous_close = snapshot.get("previousClose")
        malformed = (
            item_id not in NAVER_LATEST_INDEX_IDS
            or observed_date is None
            or not isinstance(current_value, (int, float))
            or not isinstance(previous_close, (int, float))
        )
        lag = business_day_lag(observed_date, reference_date) if observed_date else 999
        allowed_lag = (
            7
            if NAVER_MARKET_INDEXES.get(item_id, {}).get("frequency") == "weekly"
            else 1
        )
        status = "error" if malformed else ("warning" if lag > allowed_lag else "ok")
        detail = (
            "필수값 누락"
            if malformed
            else f"{observed_date.isoformat()} · {float(current_value):,.4f}"
            f" · {snapshot.get('displayStatus', '상태 미상')}"
            f" · {snapshot.get('fetchStatus', '상태 미상')}"
        )
        checks.append(
            make_check(
                f"cache:naver-market-live:{item_id}",
                "cache",
                f"시장지표 최신값/{item_id}",
                status,
                detail,
                lastDate=observed_date.isoformat() if observed_date else None,
                lagBusinessDays=lag,
            )
        )
    return checks


def build_report(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(KST)
    data: dict[str, dict[str, Any]] = {}
    read_errors = []
    for artifact_id, (_, path) in ARTIFACTS.items():
        try:
            data[artifact_id] = read_json(path)
        except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
            read_errors.append(
                make_check(
                    f"artifact:{artifact_id}:read",
                    "artifact",
                    path.name,
                    "error",
                    f"JSON 읽기 실패: {exc}",
                )
            )

    snapshot = data.get("snapshot") or {}
    reference_date = parse_date(snapshot.get("generatedAt")) or now.date()
    groups = []
    checks = list(read_errors)

    group_specs = [
        (
            "yahoo",
            "Yahoo Finance",
            snapshot.get("yahooSymbols") or {},
            TICKERS,
            2,
            5,
            80,
            set(),
        ),
        (
            "naver-equity",
            "Naver 국내주식",
            snapshot.get("naverSymbols") or {},
            NAVER_SYMBOLS,
            2,
            5,
            80,
            set(),
        ),
        (
            "fred",
            "FRED",
            snapshot.get("fredSeries") or {},
            FRED_SERIES,
            3,
            10,
            60,
            {"us_financial_stress_stlfsi", "us_financial_conditions_nfci"},
        ),
        (
            "naver-market-index",
            "Naver 시장지표",
            snapshot.get("naverMarketIndexes") or {},
            NAVER_MARKET_INDEXES,
            3,
            10,
            60,
            {"scfi"},
        ),
    ]
    for args in group_specs:
        group, group_checks = assess_source_group(
            args[0],
            args[1],
            args[2],
            args[3],
            reference_date,
            warning_lag=args[4],
            error_lag=args[5],
            min_observations=args[6],
            weekly_ids=args[7],
        )
        groups.append(group)
        checks.extend(group_checks)

    try:
        ml_source_metadata = read_json(ML_SOURCE_METADATA_FILE)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        ml_source_metadata = {}
        checks.append(
            make_check(
                "source:ml:metadata",
                "source",
                "ML 입력 원천 메타데이터",
                "error",
                f"{ML_SOURCE_METADATA_FILE.relative_to(ROOT)} 읽기 실패: {exc}",
            )
        )
    if ml_source_metadata:
        ml_group, ml_checks = assess_ml_source_group(ml_source_metadata, reference_date)
        groups.append(ml_group)
        checks.extend(ml_checks)

    checks.extend(artifact_checks(data, reference_date))
    checks.extend(cross_artifact_checks(data))
    checks.extend(cache_series_checks(data, reference_date))

    counts = {status: sum(item["status"] == status for item in checks) for status in STATUS_RANK}
    status = worst_status([item["status"] for item in checks])
    quality_points = counts["ok"] + counts["warning"] * 0.5
    score = round(100 * quality_points / max(len(checks), 1), 1)
    source_expected = sum(group["seriesExpected"] for group in groups)
    source_present = sum(group["seriesPresent"] for group in groups)
    fresh_series = sum(group["freshCount"] for group in groups)
    fallback_series = sum(group["fallbackCount"] for group in groups)
    issues = [item for item in checks if item["status"] != "ok"]

    return {
        "schemaVersion": 1,
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S KST"),
        "referenceDate": reference_date.isoformat(),
        "status": status,
        "score": score,
        "summary": {
            "checks": len(checks),
            "ok": counts["ok"],
            "warning": counts["warning"],
            "error": counts["error"],
            "sourceSeriesExpected": source_expected,
            "sourceSeriesPresent": source_present,
            "freshSeries": fresh_series,
            "staleSeries": max(0, source_present - fresh_series),
            "fallbackSeries": fallback_series,
        },
        "sourceGroups": groups,
        "issues": issues,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="대시보드 데이터 완비성과 최신성을 검사합니다.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--strict", action="store_true", help="오류가 있으면 종료 코드 1을 반환합니다.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(
        f"데이터 완비성 {report['score']:.1f}점 · "
        f"정상 {summary['ok']} · 확인 {summary['warning']} · 오류 {summary['error']}"
    )
    print(f"Wrote {output}")
    return 1 if args.strict and report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
