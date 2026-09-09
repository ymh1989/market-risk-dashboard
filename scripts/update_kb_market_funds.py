from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kospi_risk.kb_market_funds import (
    KbMarketFundsError,
    KbOpenApiClient,
    KbOpenApiConfig,
    load_existing_payload,
    update_market_funds_payload,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KB증권 증시주변자금 최종일을 누적 저장합니다.")
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
        help="조회 실패 시 기존 파일이 있어도 실패 코드로 종료",
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
    try:
        snapshot = KbOpenApiClient(build_config()).fetch_market_funds()
    except (KbMarketFundsError, ValueError) as error:
        if existing and not args.strict:
            latest = existing.get("latest") or {}
            fallback = update_market_funds_payload(
                existing,
                latest,
                generated_at=datetime.now(KST),
                fetch_status="stale-fallback",
                last_fetch_error=str(error),
            )
            write_json_atomic(args.output, fallback)
            print(
                "KB 증시주변자금 조회 실패로 직전 검증본을 대체값으로 유지합니다: "
                f"{error}"
            )
            return 0
        raise SystemExit(str(error)) from None

    payload = update_market_funds_payload(existing, snapshot)
    write_json_atomic(args.output, payload)
    latest = payload["latest"]
    values = latest["amountsKrwBillion"]
    print(
        f"KB 증시주변자금 {latest['date']} · 고객예탁금 {values['customerDeposits']:,.0f}십억원 · "
        f"신용잔고 {values['creditBalance']:,.0f}십억원 · 관찰점수 {latest['score']:.1f}"
    )
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
