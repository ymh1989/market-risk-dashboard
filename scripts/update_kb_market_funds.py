from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kospi_risk.kofia_market_funds import (
    KofiaFreeSisClient,
    KofiaFreeSisConfig,
    KofiaMarketFundsError,
    incremental_start_date,
)
from kospi_risk.kb_market_funds import (
    KbMarketFundsError,
    KbMarketFundsReconciliationError,
    KbOpenApiClient,
    KbOpenApiConfig,
    build_market_funds_payload,
    load_existing_payload,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "kb-market-funds.json"
KST = timezone(timedelta(hours=9))


def load_env_file(path: Path | None) -> None:
    """이미 설정된 환경변수를 덮어쓰지 않고 단순 KEY=VALUE 파일을 읽습니다."""
    if path is None or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def build_config() -> KbOpenApiConfig:
    return KbOpenApiConfig(
        base_url=os.getenv("KB_OPENAPI_BASE_URL", "https://developer.kbsec.com:32484").strip(),
        app_key=os.getenv("KB_OPENAPI_APP_KEY", "").strip(),
        app_secret=os.getenv("KB_OPENAPI_APP_SECRET", "").strip(),
        ip_addr=os.getenv("KB_OPENAPI_IP_ADDR", "127.0.0.1").strip(),
        mac_addr=os.getenv("KB_OPENAPI_MAC_ADDR", "00-00-00-00-00-00").strip(),
        timeout_seconds=int(os.getenv("KB_OPENAPI_TIMEOUT_SECONDS", "20")),
        retry_count=int(os.getenv("KB_OPENAPI_RETRY_COUNT", "3")),
    )


def build_kofia_config() -> KofiaFreeSisConfig:
    return KofiaFreeSisConfig(
        base_url=os.getenv("KOFIA_FREESIS_BASE_URL", "https://freesis.kofia.or.kr").strip(),
        timeout_seconds=int(os.getenv("KOFIA_FREESIS_TIMEOUT_SECONDS", "25")),
        retry_count=int(os.getenv("KOFIA_FREESIS_RETRY_COUNT", "3")),
        request_delay_seconds=float(os.getenv("KOFIA_FREESIS_REQUEST_DELAY_SECONDS", "0.25")),
    )


def write_json_atomic(path: Path, payload: dict) -> None:
    """완성된 JSON만 기존 파일과 원자적으로 교체합니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def latest_existing_kb_snapshot(existing: dict | None) -> dict | None:
    """KB 조회를 생략할 때 원장에 남아 있는 가장 최근 KB 검증행을 반환합니다."""
    candidates = []
    for row in (existing or {}).get("series", []):
        if not isinstance(row, dict) or not row.get("date"):
            continue
        kb_source = (row.get("sourceSnapshots") or {}).get("kb")
        if isinstance(kb_source, dict) and kb_source.get("amountsKrwMillion"):
            snapshot = copy.deepcopy(row)
            snapshot.update(
                {
                    "date": kb_source.get("date") or row["date"],
                    "retrievedAt": kb_source.get("retrievedAt") or row.get("retrievedAt"),
                    "amountsKrwMillion": copy.deepcopy(kb_source["amountsKrwMillion"]),
                    "amountsKrwBillion": {
                        key: (float(value) / 1000 if value is not None else None)
                        for key, value in kb_source["amountsKrwMillion"].items()
                    },
                    "ratesPct": copy.deepcopy(kb_source.get("ratesPct") or {}),
                    "sourceProviders": ["KB Securities OpenAPI"],
                }
            )
            candidates.append(snapshot)
        elif (existing or {}).get("schemaVersion") == 1:
            candidates.append(row)
    return max(candidates, key=lambda row: row["date"]) if candidates else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FreeSIS 과거 원장과 KB증권 최종일을 결합해 증시주변자금을 저장합니다."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="KB_OPENAPI_*를 읽을 별도 환경파일 경로",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="요청한 원천 조회나 동일일 대조가 실패하면 종료",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--kofia-only",
        action="store_true",
        help="인증 없는 FreeSIS 과거 원장만 갱신",
    )
    source_group.add_argument(
        "--skip-kofia",
        action="store_true",
        help="FreeSIS를 건너뛰고 KB 최종일만 갱신",
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=None,
        help="FreeSIS 강제 조회 시작일(YYYY-MM-DD); 생략 시 최초 5년·이후 증분 조회",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(ROOT / ".env")
    env_file = args.env_file
    if env_file is None and os.getenv("KB_OPENAPI_ENV_FILE", "").strip():
        env_file = Path(os.environ["KB_OPENAPI_ENV_FILE"]).expanduser()
    load_env_file(env_file)

    existing = load_existing_payload(args.output)
    now = datetime.now(KST)
    source_status: dict[str, str] = {}
    source_errors: dict[str, str] = {}
    kofia_rows = []
    history_diagnostics = {}
    kb_snapshot = None

    if not args.skip_kofia:
        start_date = args.start_date or incremental_start_date(
            existing, reference_date=now.date()
        )
        try:
            kofia_rows, history_diagnostics = KofiaFreeSisClient(
                build_kofia_config()
            ).fetch_history(
                start_date=start_date,
                end_date=now.date(),
                retrieved_at=now,
            )
            source_status["kofia"] = "direct"
        except (KofiaMarketFundsError, ValueError) as error:
            source_status["kofia"] = "stale-fallback" if existing else "unavailable"
            source_errors["kofia"] = str(error)
            if args.strict:
                raise SystemExit(str(error)) from None
            print(f"FreeSIS 조회 실패로 기존 과거 원장을 유지합니다: {error}")

    if not args.kofia_only:
        try:
            kb_snapshot = KbOpenApiClient(build_config()).fetch_market_funds()
            source_status["kb"] = "direct"
        except (KbMarketFundsError, ValueError) as error:
            source_status["kb"] = "stale-fallback" if existing else "unavailable"
            source_errors["kb"] = str(error)
            if args.strict:
                raise SystemExit(str(error)) from None
            kb_snapshot = latest_existing_kb_snapshot(existing)
            print(f"KB 최종일 조회 실패로 FreeSIS 원장만 갱신합니다: {error}")
    else:
        prior_kb_status = ((existing or {}).get("sourceStatus") or {}).get("kbLatest")
        if prior_kb_status == "reconciliation-failed":
            source_status["kb"] = prior_kb_status
            prior_error = ((existing or {}).get("sourceErrors") or {}).get("kb")
            if prior_error:
                source_errors["kb"] = prior_error
        else:
            kb_snapshot = latest_existing_kb_snapshot(existing)
            source_status["kb"] = "reused" if kb_snapshot else "not-requested"

    if not existing and not kofia_rows and kb_snapshot is None:
        messages = " | ".join(source_errors.values()) or "조회 가능한 원천이 없습니다."
        raise SystemExit(messages)

    try:
        payload = build_market_funds_payload(
            existing,
            kofia_rows=kofia_rows,
            kb_snapshot=kb_snapshot,
            generated_at=now,
            source_status=source_status,
            source_errors=source_errors,
            history_diagnostics=history_diagnostics,
        )
        if (
            args.kofia_only
            and source_status.get("kb") == "reconciliation-failed"
            and (existing or {}).get("reconciliation")
        ):
            payload["reconciliation"] = existing["reconciliation"]
    except KbMarketFundsError as error:
        if args.strict or kb_snapshot is None:
            raise SystemExit(str(error)) from None
        source_status["kb"] = "reconciliation-failed"
        source_errors["kb"] = str(error)
        payload = build_market_funds_payload(
            existing,
            kofia_rows=kofia_rows,
            kb_snapshot=None,
            generated_at=now,
            source_status=source_status,
            source_errors=source_errors,
            history_diagnostics=history_diagnostics,
        )
        payload["reconciliation"] = (
            error.reconciliation
            if isinstance(error, KbMarketFundsReconciliationError)
            else {
                "status": "failed",
                "overlapDate": kb_snapshot.get("date"),
                "fields": [],
                "error": str(error),
            }
        )
        print(f"KB 동일일 대조 실패로 KB 보강값을 제외했습니다: {error}")

    write_json_atomic(args.output, payload)
    latest = payload["latest"]
    values = latest["amountsKrwBillion"]
    print(
        f"증시주변자금 {latest['date']} · 고객예탁금 {values['customerDeposits']:,.0f}십억원 · "
        f"신용잔고 {values['creditBalance']:,.0f}십억원 · 관찰점수 {latest['score']:.1f}"
    )
    print(
        f"FreeSIS {payload['sourceStatus']['kofiaHistory']} · "
        f"KB {payload['sourceStatus']['kbLatest']} · {len(payload['series']):,}개 관측"
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
