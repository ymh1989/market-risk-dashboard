"""개인 텔레그램 승인함과 로컬 LLM 후보 탐색. 공개 웹에 관리 API를 열지 않습니다."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.issue_review_store import ReviewStore, utc_now
from scripts.issue_discovery import discover, discovery_due
from scripts.issue_evidence import require_corroboration

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_URL = "https://ymh1989.github.io/market-risk-dashboard/data/risk-dashboard.json"
STATUS_TEXT = {"approved": "승인 완료 · 홈페이지 반영 대기", "rejected": "반려 완료",
               "already-decided": "이미 처리한 후보입니다", "expired": "검토 기한이 지난 후보입니다",
               "condition-changed": "시장 조건이 바뀌어 다시 검토해야 합니다", "stale-data": "최신 시장자료를 확인한 뒤 다시 승인해 주세요",
               "stale-evidence": "기사 근거가 오래되어 재검토가 필요합니다", "conflict": "이슈 버전이 바뀌었습니다",
               "insufficient-evidence": "동시 보도·원출처 검증 기준 미충족으로 승인할 수 없습니다",
               "unauthorized": "등록된 개인 승인 계정에서만 처리할 수 있습니다", "invalid": "유효하지 않은 승인 버튼입니다"}
STATE_LABEL = {"active": "활성", "watch": "관찰", "archived": "보관"}
DISCOVERY_LABEL = {"ok": "완료", "partial": "일부 미완료", "failed": "실패", "running": "탐색 중", "waiting-data": "시장자료 대기"}


def best_effort(bot, method, **params):
    """이미 저장된 승인 결과는 Telegram 확인 응답 실패 때문에 반복 처리하지 않습니다."""
    try:
        return bot.call(method, **params)
    except RuntimeError:
        return None


def load_env(path: Path):
    """로컬 설정만 읽으며 값은 로그에 출력하지 않습니다."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        tokens = shlex.split(value, comments=True)
        os.environ.setdefault(key.strip(), " ".join(tokens))


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, **params):
        request = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                         data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                result = json.loads(response.read(1_000_000))
            if not result.get("ok"):
                raise ValueError("API 응답 실패")
            return result["result"]
        except Exception:
            # URL에 들어 있는 봇 토큰과 응답 원문은 예외·로그에 노출하지 않습니다.
            raise RuntimeError(f"Telegram {method} 요청 실패") from None


def proposal_message(proposal: dict, metric_labels: dict | None = None) -> tuple[str, dict]:
    payload = proposal["payload"]
    kind = "신규 등록" if payload["action"] == "new" else STATE_LABEL[payload["targetState"]] + " 전환"
    lines = [f"[시장 이슈 검토 · {kind}]", payload["title"], "", "AI 제안 · 미승인 · 기사 제목 기준", payload["rationale"]]
    if payload["action"] == "new":
        definition = payload["definition"]
        labels = metric_labels or {}
        lines += ["", "점검 경로: " + definition["route"], "지표: " + ", ".join(labels.get(key, key) for key in definition["metricIds"]), "수준은 연결 주제의 기존 카드 판독 · 종합점수 변경 없음"]
    check = payload.get("corroboration", {})
    lines += [f"동시 보도: {check.get('articleCount', 0)}건 · 중복 제거 {check.get('sourceGroups', 0)}개 출처군 · {check.get('spanHours', 0):g}시간 내"]
    for n, article in enumerate(payload["evidence"]):
        origins = article.get("sourceCheck", {}).get("origins", [])
        lines += ["", f"{n+1}. {article['title']}",
                  f"{article['source']} · {article['publishedAt'][:16].replace('T', ' ')} KST"]
        if origins:
            lines.append("인용 원출처: " + ", ".join(origins))
    lines += ["", "전재·유사 보도 자동 점검 · 독립 취재 여부와 사실관계는 승인 전 최종 확인",
              f"검토 기한: {proposal['expires_at'][:16].replace('T', ' ')} KST"]
    links = [[{"text": f"근거 {n+1} · {a['source'][:25]}", "url": a["url"]}] for n, a in enumerate(payload["evidence"])]
    buttons = [{"text": text, "callback_data": f"ir:{proposal['id']}:{action}:{proposal['nonce']}"} for action, text in (("a", "승인"), ("r", "반려"))]
    return "\n".join(lines), {"inline_keyboard": links + [buttons]}


def send_pending(store: ReviewStore, bot, now, resend=False):
    owner = store.get("ownerId")
    if not owner:
        return 0
    sent = 0
    labels = {m["id"]: m["label"] for issue in store.get("latestRadar", {}).get("issues", []) for m in issue["metrics"]}
    for proposal in store.pending(now):
        try:
            require_corroboration(proposal["payload"]["evidence"], now)
        except ValueError:
            continue
        if proposal["message_id"] is not None and not resend:
            continue
        text, markup = proposal_message(proposal, labels)
        result = bot.call("sendMessage", chat_id=owner, text=text, reply_markup=markup, disable_web_page_preview=True)
        store.bind_message(proposal["id"], result["message_id"])
        sent += 1
        if sent >= 5:
            break
    return sent


def handle_update(store: ReviewStore, bot, update: dict, now):
    if "callback_query" in update:
        callback = update["callback_query"]
        result = store.decide(callback, now)
        best_effort(bot, "answerCallbackQuery", callback_query_id=callback["id"], text=STATUS_TEXT[result], show_alert=result not in {"approved", "rejected"})
        if result in {"approved", "rejected", "already-decided", "expired"}:
            best_effort(bot, "editMessageReplyMarkup", chat_id=callback["message"]["chat"]["id"], message_id=callback["message"]["message_id"], reply_markup={"inline_keyboard": []})
        return
    message = update.get("message", {})
    chat, sender = message.get("chat", {}), message.get("from", {})
    if chat.get("type") != "private" or chat.get("id") != sender.get("id") or sender.get("is_bot"):
        return
    text = message.get("text", "")
    if text.startswith("/start review_"):
        paired = store.pair(text.removeprefix("/start review_").strip(), sender["id"], chat, now)
        bot.call("sendMessage", chat_id=chat["id"], text="개인 승인 계정 연결 완료. 신규·전환 후보가 생기면 이 대화에 전달합니다. /issues: 검토함, /status: 운영 상태" if paired else "연결 링크가 만료되었거나 이미 등록된 계정이 있습니다.")
        if paired:
            send_pending(store, bot, now)
        return
    if sender.get("id") != store.get("ownerId"):
        if text in {"/start", "/help"}:
            bot.call("sendMessage", chat_id=chat["id"], text="시장 이슈 개인 승인함입니다. 관리자가 발급한 일회용 연결 링크로 등록해 주세요.")
        return
    if text.split(" ")[0] == "/issues":
        count = send_pending(store, bot, now, resend=True)
        if not count:
            bot.call("sendMessage", chat_id=chat["id"], text="현재 검토 대기 후보 없음")
    elif text == "/history":
        rows = store.discovery_history(6)
        lines = ["최근 이슈 탐색 기록 · 한국시간"]
        for row in rows:
            lines.append(f"{row['slot'].replace('T', ' ')} · {DISCOVERY_LABEL.get(row['status'], row['status'])} · 기사 {row.get('reviewedArticles', 0)}건 · 제안 {row.get('modelCandidates', 0)}건 · 상정 {row.get('proposed', 0)}건")
            if row.get("noCandidateReason"):
                lines.append(row["noCandidateReason"])
            for reason, count in row.get("rejectionReasons", {}).items():
                lines.append(f"제외: {reason} {count}건")
        bot.call("sendMessage", chat_id=chat["id"], text="\n".join(lines) if rows else "아직 저장된 탐색 기록 없음")
    elif text in {"/status", "/start", "/help"}:
        last = store.get("discovery", {})
        text = (f"시장 이슈 개인 승인함\n탐색: 매일 07:45 · 12:45 · 16:20 KST\n검토 대기 {len(store.pending(now))}건\n"
                f"후보 탐색: {DISCOVERY_LABEL.get(last.get('status'), '대기')} · {str(last.get('attemptedAt', '-'))[:16]}\n"
                f"기사 {last.get('reviewedArticles', 0)}건 · 모델 제안 {last.get('modelCandidates', 0)}건 · 상정 {last.get('proposed', 0)}건\n"
                f"홈페이지: {'반영 대기' if store.get('needsPublication') else '승인 변경 대기 없음'}\n/issues 검토함 · /history 탐색 기록 · /status 상태")
        bot.call("sendMessage", chat_id=chat["id"], text=text)


def remote_revision() -> int:
    request = urllib.request.Request(PUBLIC_URL + f"?review={time.time_ns()}", headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read(8_000_000))
    radar = next(s for s in data["sections"] if s["id"] == "market")["issueRadar"]
    return int(radar.get("registryRevision", 0))


class Publisher:
    """승인 변경만 고정된 운영 명령으로 게시합니다. 모델 출력은 명령에 넣지 않습니다."""

    def __init__(self, runtime: Path, store: ReviewStore):
        self.runtime, self.store = runtime, store
        self.process = None
        self.log = None
        self.revision = None
        self.retry_after = 0

    def tick(self, bot):
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.log.close()
            self.process = None
            verified = False
            if code == 0:
                try:
                    verified = remote_revision() >= self.revision
                except Exception:
                    pass
            with self.store.transaction() as db:
                pending = self.store._get(db, "revision", 0) > self.revision or not verified
                self.store._put(db, "needsPublication", pending)
            owner = self.store.get("ownerId")
            self.retry_after = time.monotonic() + 300
            if owner:
                best_effort(bot, "sendMessage", chat_id=owner, text="승인한 이슈 변경의 홈페이지 반영을 확인했습니다.\nhttps://ymh1989.github.io/market-risk-dashboard/#market-issues" if verified else "이슈 승인은 저장됐지만 홈페이지 반영은 확인되지 않았습니다. 5분 뒤 재시도합니다.\n로그: issue-review/publication.log")
        if self.process or not self.store.get("needsPublication") or time.monotonic() < self.retry_after:
            return
        script = self.runtime / "scripts/run_local_market_update.sh"
        if not script.is_file():
            raise RuntimeError("운영 게시 실행기 없음")
        self.revision = self.store.get("revision", 0)
        self.log = (self.store.path.parent / "publication.log").open("a")
        env = {**os.environ, "LOCAL_MARKET_UPDATE_RUNTIME_SELF_UPDATE": "1", "ISSUE_REVIEW_STATE_DIR": str(self.store.path.parent)}
        self.process = subprocess.Popen(["/bin/bash", str(script), "--live"], cwd=self.runtime, env=env, stdout=self.log, stderr=subprocess.STDOUT)


def main():
    parser = argparse.ArgumentParser(description="시장 이슈 개인 승인함")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--pair", action="store_true", help="20분 유효한 일회용 개인 연결 링크 발급")
    parser.add_argument("--once", action="store_true", help="검토함 발송만 한 번 실행")
    args = parser.parse_args()
    load_env(args.env_file)
    token = os.getenv("ISSUE_REVIEW_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("개인 승인용 봇 토큰 설정 필요")
    store, bot = ReviewStore(args.state_dir), Telegram(token)
    if args.pair:
        username = bot.call("getMe")["username"]
        print(f"https://t.me/{username}?start=review_{store.pairing_token(utc_now())}")
        return
    lock = (args.state_dir / "bot.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("이 승인함의 봇이 이미 실행 중입니다")
    if bot.call("getWebhookInfo").get("url"):
        raise SystemExit("기존 webhook이 있어 수신 방식을 변경하지 않습니다")
    bot_id = bot.call("getMe")["id"]
    if store.get("botId") not in {None, bot_id}:
        raise SystemExit("등록된 봇과 다른 토큰입니다")
    store.put("botId", bot_id)
    if args.once:
        send_pending(store, bot, utc_now())
        return
    if not args.runtime:
        raise SystemExit("운영 게시 경로 --runtime 필요")
    publisher = Publisher(args.runtime, store)
    themes = json.loads((ROOT / "config/market_issues.json").read_text())["issues"]
    pool, future = ThreadPoolExecutor(max_workers=1), None
    print("시장 이슈 승인함 시작 · 개인 계정 인증 필수", flush=True)
    while True:
        # Telegram 조회 장애가 뉴스 탐색까지 중단시키지 않도록 먼저 회차를 점검합니다.
        now = utc_now()
        if future is not None and future.done():
            try:
                last = future.result()
                print(f"{now.isoformat()} 이슈 탐색 완료: {last.get('status')} · 기사 {last.get('reviewedArticles', 0)} · 상정 {last.get('proposed', 0)}", flush=True)
                if last.get("status") in {"failed", "partial"} and last.get("attemptedAt") != store.get("lastFailureNotice") and store.get("ownerId"):
                    result = best_effort(bot, "sendMessage", chat_id=store.get("ownerId"), text="시장 이슈 후보 탐색 일부 또는 전체 미완료. 뉴스 수집·로컬 모델 응답 확인 필요. 기존 승인 이슈는 유지하며 1시간 뒤 재시도. /history에서 탐색 기록 확인")
                    if result is not None:
                        store.put("lastFailureNotice", last.get("attemptedAt"))
            except Exception as error:
                print(f"{now.isoformat()} 이슈 탐색 작업 오류: {type(error).__name__}", flush=True)
            future = None
        if future is None and discovery_due(store.get("discovery", {}), now):
            future = pool.submit(discover, store, themes, now)
        try:
            updates = bot.call("getUpdates", offset=store.get("telegramOffset"), timeout=20, allowed_updates=["message", "callback_query"])
            for update in updates:
                handle_update(store, bot, update, utc_now())
                store.put("telegramOffset", update["update_id"] + 1)
            now = utc_now()
            send_pending(store, bot, now)
            publisher.tick(bot)
        except Exception as error:
            print(f"{utc_now().isoformat()} 승인함 수신·발송·게시 일시 오류: {type(error).__name__} · 10초 뒤 재시도", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
