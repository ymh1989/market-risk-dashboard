from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
REQUIRED_CANDIDATE_FILES = (
    Path("raw/market_data.csv"),
    Path("raw/market_data_sources.json"),
    Path("processed/features.parquet"),
    Path("models/model_bundle.joblib"),
    Path("dashboard/market-history-cache.json"),
    Path("dashboard/naver-marketindex-history.json"),
    Path("dashboard/els-index-risk.json"),
    Path("dashboard/hmm-regime.json"),
    Path("dashboard/ml-risk-signal.json"),
    Path("dashboard/data-quality.json"),
    Path("quality/overnight-source-quality.json"),
)
CORE_US_COLUMNS = ("SPX", "SOX", "VIX", "NASDAQ", "US10Y")


class OvernightCandidateError(RuntimeError):
    """야간 후보가 재사용 가능한 상태가 아닐 때 발생한다."""


def _now_kst() -> datetime:
    return datetime.now(KST)


def parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith(" KST"):
        text = text[:-4] + "+09:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def overnight_schedule(now: Optional[datetime] = None) -> dict[str, Any]:
    """뉴욕 DST 상태에 맞는 한국시간 실행 슬롯과 대상 미국장 날짜를 반환한다."""

    now_kst = (now or _now_kst()).astimezone(KST)
    now_new_york = now_kst.astimezone(NEW_YORK)
    daylight_saving = bool(now_new_york.dst() and now_new_york.dst() != timedelta(0))
    return {
        "eligible": now_kst.weekday() in {1, 2, 3, 4, 5},
        "kstDate": now_kst.date().isoformat(),
        "kstTime": now_kst.strftime("%H:%M"),
        "slot": "05:30" if daylight_saving else "06:30",
        "usMarketDate": now_new_york.date().isoformat(),
        "newYorkOffset": now_new_york.strftime("%z"),
        "season": "summer" if daylight_saving else "standard",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise OvernightCandidateError(f"필수 파일이 없습니다: {path}") from error
    except json.JSONDecodeError as error:
        raise OvernightCandidateError(f"JSON이 손상됐습니다: {path} ({error})") from error
    if not isinstance(payload, dict):
        raise OvernightCandidateError(f"JSON 최상위 형식이 객체가 아닙니다: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(checks: list[dict[str, Any]], check_id: str, status: str, message: str, **details: Any) -> None:
    item = {"id": check_id, "status": status, "message": message}
    if details:
        item["details"] = details
    checks.append(item)


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in checks}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


def _load_market_frame(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
    except FileNotFoundError as error:
        raise OvernightCandidateError(f"시장 원자료가 없습니다: {path}") from error
    if "date" not in frame.columns:
        raise OvernightCandidateError(f"시장 원자료에 date 컬럼이 없습니다: {path}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].isna().any():
        raise OvernightCandidateError("시장 원자료에 해석할 수 없는 날짜가 있습니다.")
    return frame.sort_values("date").reset_index(drop=True)


def _revision_summary(current: pd.DataFrame, previous: pd.DataFrame) -> dict[str, Any]:
    cutoff = current["date"].max() - pd.Timedelta(days=10)
    columns = sorted((set(current.columns) & set(previous.columns)) - {"date"})
    merged = previous[["date", *columns]].merge(
        current[["date", *columns]], on="date", how="inner", suffixes=("_old", "_new")
    )
    merged = merged.loc[merged["date"] < cutoff]
    changed_cells = 0
    changed_columns = []
    max_relative_change = 0.0
    for column in columns:
        old = pd.to_numeric(merged[f"{column}_old"], errors="coerce")
        new = pd.to_numeric(merged[f"{column}_new"], errors="coerce")
        valid = old.notna() & new.notna()
        denominator = old.abs().clip(lower=1e-12)
        relative = ((new - old).abs() / denominator).where(valid)
        changed = valid & ((new - old).abs() > denominator * 1e-6)
        count = int(changed.sum())
        if count:
            changed_cells += count
            changed_columns.append({"column": column, "count": count})
            max_relative_change = max(max_relative_change, float(relative.loc[changed].max()))
    return {
        "comparedRows": int(len(merged)),
        "changedCells": changed_cells,
        "changedColumns": changed_columns,
        "maxRelativeChange": round(max_relative_change, 8),
    }


def audit_market_sources(
    current_data: str | Path,
    metadata_path: str | Path,
    *,
    previous_data: str | Path | None = None,
    expected_us_market_date: str | None = None,
    minimum_rows: int = 1500,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """수집 결과의 완전성·신선도·과거 수정 여부를 점검한다."""

    current_path = Path(current_data)
    metadata = _read_json(Path(metadata_path))
    current = _load_market_frame(current_path)
    checks: list[dict[str, Any]] = []

    if len(current) >= minimum_rows:
        _check(checks, "rows", "ok", f"관측치 {len(current):,}개")
    else:
        _check(checks, "rows", "error", f"관측치가 부족합니다: {len(current):,} < {minimum_rows:,}")

    duplicate_dates = int(current["date"].duplicated().sum())
    _check(
        checks,
        "dates",
        "error" if duplicate_dates else "ok",
        "중복 날짜 없음" if not duplicate_dates else f"중복 날짜 {duplicate_dates}개",
    )

    required_columns = [str(value) for value in metadata.get("requiredColumns") or []]
    missing_required = [column for column in required_columns if column not in current.columns]
    _check(
        checks,
        "required-columns",
        "error" if missing_required else "ok",
        "필수 컬럼 확인" if not missing_required else f"필수 컬럼 누락: {', '.join(missing_required)}",
    )

    sources = metadata.get("sources") or []
    source_rows = []
    required_failures = []
    optional_warnings = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("status") or "unknown")
        required = bool(source.get("required"))
        row = {
            "column": source.get("column"),
            "provider": source.get("provider"),
            "symbol": source.get("symbol"),
            "required": required,
            "status": status,
            "lastDate": source.get("lastDate"),
            "warning": source.get("warning") or source.get("error"),
        }
        source_rows.append(row)
        if required and status in {"failed", "unknown", "skipped_low_coverage"}:
            required_failures.append(str(source.get("column") or "unknown"))
        if not required and status not in {"ok", "supplemented"}:
            optional_warnings.append(str(source.get("column") or "unknown"))
    _check(
        checks,
        "required-sources",
        "error" if required_failures else "ok",
        "필수 원천 정상" if not required_failures else f"필수 원천 실패: {', '.join(required_failures)}",
    )
    if optional_warnings:
        _check(checks, "optional-sources", "warning", f"선택 원천 확인: {', '.join(optional_warnings)}")
    else:
        _check(checks, "optional-sources", "ok", "선택 원천 정상")

    expected_date = pd.Timestamp(expected_us_market_date).date() if expected_us_market_date else None
    stale_core = []
    delayed_core = []
    source_by_column = {str(item.get("column")): item for item in source_rows}
    if expected_date:
        for column in CORE_US_COLUMNS:
            source = source_by_column.get(column)
            if not source or not source.get("lastDate"):
                delayed_core.append(column)
                continue
            observed = pd.Timestamp(source["lastDate"]).date()
            lag_days = (expected_date - observed).days
            if lag_days > 4:
                stale_core.append(f"{column} {source['lastDate']}")
            elif lag_days > 0:
                delayed_core.append(f"{column} {source['lastDate']}")
    if stale_core:
        _check(checks, "us-eod-freshness", "error", f"미국장 EOD 장기 지연: {', '.join(stale_core)}")
    elif delayed_core:
        _check(
            checks,
            "us-eod-freshness",
            "warning",
            f"휴장·제공처 지연 확인: {', '.join(delayed_core)}",
        )
    else:
        _check(checks, "us-eod-freshness", "ok", "미국장 핵심 EOD 날짜 확인")

    revision = None
    previous_path = Path(previous_data) if previous_data else None
    if previous_path and previous_path.exists():
        previous = _load_market_frame(previous_path)
        if len(current) < len(previous):
            _check(
                checks,
                "row-regression",
                "error",
                f"행 수가 감소했습니다: {len(previous):,} -> {len(current):,}",
            )
        else:
            _check(checks, "row-regression", "ok", f"행 수 유지·증가: {len(previous):,} -> {len(current):,}")
        revision = _revision_summary(current, previous)
        revision_warning = revision["changedCells"] > 5 and revision["maxRelativeChange"] > 0.005
        _check(
            checks,
            "historical-revisions",
            "warning" if revision_warning else "ok",
            (
                f"과거 구간 수정 {revision['changedCells']}건 · 최대 {revision['maxRelativeChange']:.2%}"
                if revision["changedCells"]
                else "최근 10일 이전 과거값 수정 없음"
            ),
        )
    else:
        _check(checks, "historical-revisions", "warning", "직전 원자료가 없어 과거값 대조를 생략했습니다.")

    latest_values = {}
    for column in [*required_columns, *CORE_US_COLUMNS]:
        if column == "date" or column not in current.columns or column in latest_values:
            continue
        series = pd.to_numeric(current[column], errors="coerce").dropna()
        if series.empty:
            continue
        latest_values[column] = {
            "value": round(float(series.iloc[-1]), 6),
            "change1dPct": round(float(series.pct_change().iloc[-1] * 100), 4) if len(series) > 1 else None,
        }

    generated_at = (now or _now_kst()).astimezone(KST)
    status = _overall_status(checks)
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at.strftime("%Y-%m-%d %H:%M:%S KST"),
        "status": status,
        "expectedUsMarketDate": expected_us_market_date,
        "dataPeriod": {
            "firstDate": current["date"].min().date().isoformat(),
            "lastDate": current["date"].max().date().isoformat(),
            "rows": int(len(current)),
        },
        "summary": {
            "ok": sum(item["status"] == "ok" for item in checks),
            "warning": sum(item["status"] == "warning" for item in checks),
            "error": sum(item["status"] == "error" for item in checks),
        },
        "checks": checks,
        "sources": source_rows,
        "historicalRevision": revision,
        "latestValues": latest_values,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def seal_candidate(
    candidate_root: str | Path,
    *,
    source_commit: str,
    slot: str,
    us_market_date: str,
    started_at: str,
    prepared_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """필수 후보 파일을 체크섬 manifest로 봉인한다."""

    root = Path(candidate_root)
    rows = []
    for relative in REQUIRED_CANDIDATE_FILES:
        path = root / relative
        if not path.exists() or path.stat().st_size <= 0:
            raise OvernightCandidateError(f"후보 파일이 없거나 비어 있습니다: {relative}")
        rows.append(
            {
                "path": str(relative),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    quality = _read_json(root / "quality/overnight-source-quality.json")
    if quality.get("status") == "error":
        raise OvernightCandidateError("원천 품질 판정이 error라 후보를 봉인할 수 없습니다.")
    prepared = (prepared_at or _now_kst()).astimezone(KST)
    manifest = {
        "schemaVersion": 1,
        "status": "ready",
        "preparedAt": prepared.strftime("%Y-%m-%d %H:%M:%S KST"),
        "startedAt": parse_datetime(started_at).strftime("%Y-%m-%d %H:%M:%S KST"),
        "sourceCommit": source_commit,
        "schedule": {"slot": slot, "usMarketDate": us_market_date},
        "qualityStatus": quality.get("status"),
        "marketDataSha256": next(row["sha256"] for row in rows if row["path"] == "raw/market_data.csv"),
        "artifacts": rows,
    }
    write_json(root / "manifest.json", manifest)
    return verify_candidate(root, expected_commit=source_commit)


def verify_candidate(
    candidate_root: str | Path,
    *,
    expected_commit: str | None = None,
    max_age_hours: float | None = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """후보 manifest, 코드 버전, 나이와 파일 체크섬을 검증한다."""

    root = Path(candidate_root)
    manifest = _read_json(root / "manifest.json")
    if manifest.get("status") != "ready":
        raise OvernightCandidateError("야간 후보가 ready 상태가 아닙니다.")
    if expected_commit and manifest.get("sourceCommit") != expected_commit:
        raise OvernightCandidateError(
            f"야간 후보 코드가 현재 코드와 다릅니다: {manifest.get('sourceCommit')} != {expected_commit}"
        )
    if max_age_hours is not None:
        prepared = parse_datetime(str(manifest.get("preparedAt") or ""))
        age_hours = ((now or _now_kst()).astimezone(KST) - prepared).total_seconds() / 3600
        if age_hours < -0.1 or age_hours > max_age_hours:
            raise OvernightCandidateError(f"야간 후보가 오래됐습니다: {age_hours:.1f}시간")
    artifacts = manifest.get("artifacts") or []
    if len(artifacts) != len(REQUIRED_CANDIDATE_FILES):
        raise OvernightCandidateError("야간 후보 파일 개수가 manifest와 다릅니다.")
    for row in artifacts:
        if not isinstance(row, dict):
            raise OvernightCandidateError("야간 후보 manifest 형식이 올바르지 않습니다.")
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise OvernightCandidateError(f"후보 파일 경로가 안전하지 않습니다: {relative}")
        path = root / relative
        if not path.exists() or path.stat().st_size != int(row.get("bytes") or -1):
            raise OvernightCandidateError(f"후보 파일 크기가 다릅니다: {relative}")
        if _sha256(path) != row.get("sha256"):
            raise OvernightCandidateError(f"후보 파일 체크섬이 다릅니다: {relative}")
    return manifest


def publish_candidate(staging_root: str | Path, current_root: str | Path) -> dict[str, Any]:
    """검증된 후보 디렉터리를 교체하며 실패 시 직전 후보를 복구한다."""

    staging = Path(staging_root)
    current = Path(current_root)
    manifest = verify_candidate(staging)
    current.parent.mkdir(parents=True, exist_ok=True)
    backup = current.with_name(f".{current.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    had_current = current.exists()
    if had_current:
        os.replace(current, backup)
    try:
        os.replace(staging, current)
    except Exception:
        if had_current and backup.exists() and not current.exists():
            os.replace(backup, current)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return verify_candidate(current, expected_commit=str(manifest["sourceCommit"]))


def _schedule_command(args: argparse.Namespace) -> int:
    now = parse_datetime(args.now) if args.now else None
    payload = overnight_schedule(now)
    if args.format == "shell":
        print(
            "|".join(
                [
                    "1" if payload["eligible"] else "0",
                    payload["slot"],
                    payload["usMarketDate"],
                    payload["season"],
                ]
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


def _audit_command(args: argparse.Namespace) -> int:
    payload = audit_market_sources(
        args.current_data,
        args.metadata,
        previous_data=args.previous_data,
        expected_us_market_date=args.expected_us_market_date,
        minimum_rows=args.minimum_rows,
    )
    write_json(Path(args.output), payload)
    print(
        f"야간 원천 품질: {payload['status']} · "
        f"정상 {payload['summary']['ok']} · 주의 {payload['summary']['warning']} · 오류 {payload['summary']['error']}"
    )
    return 1 if payload["status"] == "error" else 0


def _seal_command(args: argparse.Namespace) -> int:
    manifest = seal_candidate(
        args.root,
        source_commit=args.source_commit,
        slot=args.slot,
        us_market_date=args.us_market_date,
        started_at=args.started_at,
    )
    print(f"야간 후보 봉인 완료: {manifest['sourceCommit']} · {manifest['qualityStatus']}")
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    manifest = verify_candidate(
        args.root,
        expected_commit=args.expected_commit or None,
        max_age_hours=args.max_age_hours,
    )
    if args.format == "sha":
        print(manifest["marketDataSha256"])
    else:
        print(
            f"야간 후보 검증 완료: {manifest['sourceCommit']} · "
            f"{manifest['schedule']['slot']} · {manifest['qualityStatus']}"
        )
    return 0


def _publish_command(args: argparse.Namespace) -> int:
    manifest = publish_candidate(args.staging, args.current)
    print(
        f"야간 후보 교체 완료: {manifest['sourceCommit']} · "
        f"{manifest['schedule']['usMarketDate']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="미국장 마감 후 야간 후보와 원천 품질을 관리합니다.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--now", default="")
    schedule.add_argument("--format", choices=["json", "shell"], default="json")
    schedule.set_defaults(func=_schedule_command)

    audit = subparsers.add_parser("audit")
    audit.add_argument("--current-data", required=True)
    audit.add_argument("--metadata", required=True)
    audit.add_argument("--previous-data", default="")
    audit.add_argument("--expected-us-market-date", default="")
    audit.add_argument("--minimum-rows", type=int, default=1500)
    audit.add_argument("--output", required=True)
    audit.set_defaults(func=_audit_command)

    seal = subparsers.add_parser("seal")
    seal.add_argument("--root", required=True)
    seal.add_argument("--source-commit", required=True)
    seal.add_argument("--slot", required=True)
    seal.add_argument("--us-market-date", required=True)
    seal.add_argument("--started-at", required=True)
    seal.set_defaults(func=_seal_command)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", required=True)
    verify.add_argument("--expected-commit", default="")
    verify.add_argument("--max-age-hours", type=float, default=None)
    verify.add_argument("--format", choices=["text", "sha"], default="text")
    verify.set_defaults(func=_verify_command)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--staging", required=True)
    publish.add_argument("--current", required=True)
    publish.set_defaults(func=_publish_command)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        exit_code = args.func(args)
    except OvernightCandidateError as error:
        raise SystemExit(f"야간 준비 실패: {error}") from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
