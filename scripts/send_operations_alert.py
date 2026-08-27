from __future__ import annotations

import argparse
import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
KST = timezone(timedelta(hours=9), "KST")
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
USER_AGENT = "market-lab-operations-alert/1.0"


def load_env_file(path: Path = ENV_FILE) -> None:
    """저장소 .env 값을 기존 환경변수를 덮지 않고 읽는다."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def operations_bot_token(environ: Mapping[str, str] = os.environ) -> str:
    """일반 뉴스 봇과 분리된 운영 알림 전용 봇 토큰을 반환한다."""

    return environ.get("MARKET_OPERATIONS_TELEGRAM_BOT_TOKEN", "").strip()


def operations_chat_ids(environ: Mapping[str, str] = os.environ) -> list[str]:
    """운영 알림 전용 채팅만 반환하며 일반 뉴스 채팅으로 대체하지 않는다."""

    configured = (
        environ.get("MARKET_OPERATIONS_TELEGRAM_CHAT_IDS")
        or environ.get("MARKET_OPERATIONS_TELEGRAM_CHAT_ID")
        or ""
    )
    return list(dict.fromkeys(value.strip() for value in configured.split(",") if value.strip()))


def _redact(text: str) -> str:
    sanitized = text
    for name in (
        "MARKET_OPERATIONS_TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "KRX_PW",
        "FRED_API_KEY",
    ):
        secret = os.environ.get(name)
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def read_log_tail(path: str | Path, lines: int = 8) -> str:
    log_path = Path(path)
    if not log_path.exists():
        return "로그 파일 없음"
    values = [line.strip() for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    compact = [line for line in values if line][-lines:]
    return _redact("\n".join(compact))[-1400:] or "로그 내용 없음"


def format_alert(
    *,
    job: str,
    stage: str,
    exit_code: int,
    command: str,
    log_file: str,
    occurred_at: datetime | None = None,
) -> str:
    """운영자가 읽고 Codex에 바로 붙여넣을 수 있는 짧은 실패 메시지를 만든다."""

    now = (occurred_at or datetime.now(KST)).astimezone(KST)
    safe_command = _redact(command).strip()[-500:] or "확인 불가"
    log_tail = read_log_tail(log_file)
    codex_prompt = (
        f"market-lab {job} 실패를 점검해줘. 단계={stage}, 종료코드={exit_code}, "
        f"실패명령={safe_command}. logs/{Path(log_file).name} 마지막 로그를 확인하고 "
        "원인을 수정한 뒤 같은 야간 준비 작업을 재실행해줘."
    )
    return "\n".join(
        [
            "<b>[운영 실패] 시장리스크 야간 준비</b>",
            f"시각: {html.escape(now.strftime('%Y-%m-%d %H:%M KST'))}",
            f"작업: {html.escape(job)}",
            f"단계: {html.escape(stage)} · 종료코드 {exit_code}",
            f"명령: <code>{html.escape(safe_command)}</code>",
            "",
            "<b>마지막 로그</b>",
            f"<pre>{html.escape(log_tail)}</pre>",
            "<b>Codex 붙여넣기</b>",
            f"<code>{html.escape(codex_prompt)}</code>",
        ]
    )


def format_schedule_alert(
    *,
    scheduled_at: datetime,
    mode: str,
    elapsed_minutes: int,
    state: str,
    log_file: str,
) -> str:
    """정기 갱신이 허용 시간을 넘긴 상황을 짧은 운영 알림으로 만든다."""

    slot = scheduled_at.astimezone(KST)
    log_tail = read_log_tail(log_file)
    codex_prompt = (
        f"market-lab {slot.strftime('%Y-%m-%d %H:%M')} {mode} 정기 갱신 지연을 점검해줘. "
        f"현재상태={state}, 경과={elapsed_minutes}분. logs/{Path(log_file).name}와 "
        "local-market-update 상태 파일을 확인하고 원인을 수정한 뒤 누락 갱신을 복구해줘."
    )
    return "\n".join(
        [
            "<b>[운영 지연] 시장리스크 정기 갱신</b>",
            f"예약: {html.escape(slot.strftime('%Y-%m-%d %H:%M KST'))} · {html.escape(mode)}",
            f"경과: {elapsed_minutes}분",
            f"상태: {html.escape(state)}",
            "",
            "<b>마지막 로그</b>",
            f"<pre>{html.escape(log_tail)}</pre>",
            "<b>Codex 붙여넣기</b>",
            f"<code>{html.escape(codex_prompt)}</code>",
        ]
    )


def send_message(message: str, token: str, chat_id: str) -> None:
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        TELEGRAM_URL.format(token=token),
        data=data,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"텔레그램 운영 알림 실패: {payload}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="시장리스크 운영 실패를 텔레그램으로 알립니다.")
    parser.add_argument("--job", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--command", default="")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    load_env_file()
    args = build_parser().parse_args()
    message = format_alert(
        job=args.job,
        stage=args.stage,
        exit_code=args.exit_code,
        command=args.command,
        log_file=args.log_file,
    )
    if args.dry_run:
        print(message)
        return
    token = operations_bot_token()
    chat_ids = operations_chat_ids()
    if not token or not chat_ids:
        raise SystemExit("운영 알림 전용 봇 토큰과 금융공학뉴스 chat id가 필요합니다.")
    for chat_id in chat_ids:
        send_message(message, token, chat_id)
    print(f"운영 실패 알림 발송 완료: {len(chat_ids)}개 채팅")


if __name__ == "__main__":
    main()
