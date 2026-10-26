"""비공개 이슈 검토함. 승인과 상태 이력을 하나의 SQLite 트랜잭션으로 저장합니다."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3

if __package__:
    from .issue_radar_llm import KST, parsed_time, fingerprint
    from .issue_evidence import duplicate_event, require_corroboration
else:
    from issue_radar_llm import KST, parsed_time, fingerprint
    from issue_evidence import duplicate_event, require_corroboration

STATES = {"active", "watch", "archived"}


def utc_now() -> datetime:
    """운영시각은 한국시간으로 통일합니다."""
    return datetime.now(KST)


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class ReviewStore:
    """개인 계정·미승인 후보는 공개 산출물과 물리적으로 분리합니다."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / "review.sqlite3"
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY, definition TEXT NOT NULL, state TEXT NOT NULL,
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    history TEXT NOT NULL, archived_snapshot TEXT);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, dedup_key TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    nonce TEXT NOT NULL, message_id INTEGER);
                CREATE TABLE IF NOT EXISTS observations (
                    issue_id TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(issue_id, day));
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    action TEXT NOT NULL, actor TEXT, payload TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, key, default=None):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _put(db, key, value):
        db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    def get(self, key, default=None):
        with self.transaction() as db:
            return self._get(db, key, default)

    def put(self, key, value):
        with self.transaction() as db:
            self._put(db, key, value)

    def seed(self, definitions: list[dict], previous: dict | None, now: datetime):
        """기존 승인 주제와 이미 게시된 등록부만 초기 이관합니다."""
        public = {row["definition"]["id"]: row for row in (previous or {}).get("registry", [])}
        for definition in definitions:
            if definition.get("approved") is True:
                public.setdefault(definition["id"], {"definition": definition})
        with self.transaction() as db:
            for key, row in public.items():
                lifecycle = row.get("lifecycle", {})
                state = lifecycle.get("state", "active")
                if state not in STATES or row["definition"].get("approved") is not True:
                    continue
                db.execute("INSERT OR IGNORE INTO issues VALUES (?,?,?,?,?,?,?,?)", (
                    key, encode(row["definition"]), state, lifecycle.get("version", 1),
                    lifecycle.get("createdAt", now.isoformat()), lifecycle.get("updatedAt", now.isoformat()),
                    encode(lifecycle.get("history", [{"at": now.isoformat(), "state": state, "reason": "기존 승인 이슈 이관"}])),
                    encode(row["archivedSnapshot"]) if row.get("archivedSnapshot") else None))
            if self._get(db, "revision") is None:
                self._put(db, "revision", (previous or {}).get("registryRevision", 0))

    def registry(self) -> list[dict]:
        """승인된 공개 필드만 반환합니다. 계정·후보·반려 사유는 포함하지 않습니다."""
        return self.public_snapshot()[0]

    def public_snapshot(self) -> tuple[list[dict], int]:
        """등록부와 버전을 같은 트랜잭션에서 읽어 게시 중 승인의 혼입을 막습니다."""
        with self.transaction() as db:
            rows = [{"definition": json.loads(row["definition"]), "lifecycle": {
                "state": row["state"], "version": row["version"], "createdAt": row["created_at"],
                "updatedAt": row["updated_at"], "history": json.loads(row["history"])},
                **({"archivedSnapshot": json.loads(row["archived_snapshot"])} if row["archived_snapshot"] else {})}
                for row in db.execute("SELECT * FROM issues ORDER BY created_at,id")
                if not json.loads(row["definition"]).get("supersededBy")]
            return rows, self._get(db, "revision", 0)

    def merged_events(self) -> list[dict]:
        """통합 사건은 비공개 재등록 방지용으로만 읽습니다."""
        with self.transaction() as db:
            return [definition for row in db.execute("SELECT definition FROM issues")
                    if (definition := json.loads(row[0])).get("supersededBy")]

    def merge_duplicate(self, issue_id: str, target_id: str, reason: str, now: datetime) -> bool:
        """사용자가 명시적으로 요청한 중복만 통합하며 원본·감사 이력은 보존합니다."""
        with self.transaction() as db:
            row = db.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
            target = db.execute("SELECT definition FROM issues WHERE id=?", (target_id,)).fetchone()
            if not row or not target or issue_id == target_id or not reason.strip():
                raise ValueError("통합 대상·사유 확인 필요")
            definition = json.loads(row["definition"])
            if definition.get("supersededBy") == target_id:
                return False
            if definition.get("themeId") != target_id or json.loads(target[0]).get("supersededBy"):
                raise ValueError("같은 상위 주제로만 중복 통합 가능")
            definition["supersededBy"] = target_id
            history = json.loads(row["history"]) + [{"at": now.isoformat(), "state": row["state"],
                                                    "reason": reason, "supersededBy": target_id}]
            db.execute("UPDATE issues SET definition=?,version=version+1,updated_at=?,history=? WHERE id=?",
                       (encode(definition), now.isoformat(), encode(history), issue_id))
            for proposal in db.execute("SELECT id,payload FROM proposals WHERE status='pending'").fetchall():
                payload = json.loads(proposal["payload"])
                if payload.get("issueId") == issue_id or duplicate_event(payload.get("definition", {}), definition):
                    db.execute("UPDATE proposals SET status='superseded' WHERE id=?", (proposal["id"],))
            db.execute("INSERT INTO audit(at,action,actor,payload) VALUES (?,?,?,?)",
                       (now.isoformat(), "merged", "local-user-request", encode({"issueId": issue_id, "targetId": target_id, "reason": reason})))
            self._put(db, "revision", self._get(db, "revision", 0) + 1)
            self._put(db, "needsPublication", True)
            return True

    def observe(self, radar: dict, now: datetime):
        """반복 배치를 영업일 수로 세지 않고 실제 원자료 관측일별로 한 번만 기록합니다."""
        with self.transaction() as db:
            self._put(db, "latestRadar", radar)
            for issue in radar["issues"]:
                relevant = [m for m in issue["metrics"] if m["riskSign"]]
                days = [m["asOf"] for m in relevant if m.get("asOf")]
                if not days:
                    continue
                valid = (len(relevant) >= 2 and all(m["status"] == "ok" and m.get("change1w") is not None for m in relevant)
                         and not issue["reading"]["partial"] and issue["news"].get("status") not in {"failed", "unavailable"})
                value = {"valid": valid, "reading": issue["reading"], "seenAt": now.isoformat()}
                db.execute("INSERT INTO observations VALUES (?,?,?) ON CONFLICT(issue_id,day) DO UPDATE SET payload=excluded.payload",
                           (issue["id"], min(days), encode(value)))
            db.execute("DELETE FROM observations WHERE day < ?", ((now - timedelta(days=90)).date().isoformat(),))

    def _eligible(self, db, issue_id: str, target: str, now: datetime) -> bool:
        row = db.execute("SELECT * FROM issues WHERE id=?", (issue_id,)).fetchone()
        radar = self._get(db, "latestRadar", {})
        generated = parsed_time(radar.get("generatedAt"))
        if not row or json.loads(row["definition"]).get("supersededBy") or not generated or not timedelta(0) <= now - generated <= timedelta(hours=26):
            return False
        latest = next((i for i in radar.get("issues", []) if i["id"] == issue_id), None)
        if not latest or latest["news"].get("status") in {"failed", "unavailable"} or latest["reading"].get("partial"):
            return False
        transitions = {("active", "watch"): 3, ("watch", "archived"): 5, ("watch", "active"): 1, ("archived", "active"): 1}
        required = transitions.get((row["state"], target))
        if required is None:
            return False
        samples = [(r["day"], json.loads(r["payload"])) for r in db.execute(
            "SELECT day,payload FROM observations WHERE issue_id=? ORDER BY day DESC LIMIT ?", (issue_id, required))]
        if len(samples) < required or not all(s["valid"] for _, s in samples):
            return False
        ages = [(now.date() - datetime.fromisoformat(day).date()).days for day, _ in samples]
        if not 0 <= ages[0] <= 5 or ages[-1] > 21:
            return False
        if target == "active":
            return samples[0][1]["reading"]["confirmed"] >= 2
        if any(s["reading"]["confirmed"] or s["reading"]["levelScore"] is None or s["reading"]["levelScore"] >= 75 for _, s in samples):
            return False
        if target == "watch":
            return all(s["reading"]["direction"] == "완화" for _, s in samples)
        changed = parsed_time(row["updated_at"]).date().isoformat()
        return all(day > changed and s["reading"]["direction"] in {"완화", "뚜렷한 변화 없음"} for day, s in samples)

    def eligible_transitions(self, now: datetime) -> dict[str, list[str]]:
        with self.transaction() as db:
            return {row[0]: [state for state in ("active", "watch", "archived") if self._eligible(db, row[0], state, now)]
                    for row in db.execute("SELECT id FROM issues")}

    def add_proposal(self, payload: dict, now: datetime) -> str | None:
        """동일 후보의 반복 알림과 승인 전 내용 변경을 막습니다."""
        require_corroboration(payload["evidence"], now)
        key = fingerprint({k: payload.get(k) for k in ("action", "issueId", "expectedVersion", "title", "targetState")})
        with self.transaction() as db:
            old = db.execute("SELECT status,created_at,expires_at,payload FROM proposals WHERE dedup_key=? ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
            if old and (old["status"] == "approved" or (now - parsed_time(old["created_at"]) < timedelta(days=7) and old["status"] == "rejected")
                        or (old["status"] == "pending" and parsed_time(old["expires_at"]) > now)):
                return None
            if db.execute("SELECT count(*) FROM proposals WHERE status='pending' AND expires_at>?", (now.isoformat(),)).fetchone()[0] >= 10:
                return None
            if payload["action"] == "new":
                for existing in db.execute("SELECT definition FROM issues"):
                    if duplicate_event(payload["definition"], json.loads(existing[0])):
                        return None
                urls = {a["url"] for a in payload["evidence"]}
                for row in db.execute("SELECT payload,status,created_at,expires_at FROM proposals WHERE status IN ('pending','approved','rejected')"):
                    if row["status"] == "rejected" and now - parsed_time(row["created_at"]) >= timedelta(days=7):
                        continue
                    if row["status"] == "pending" and parsed_time(row["expires_at"]) <= now:
                        continue
                    prior = json.loads(row["payload"])
                    if prior["action"] == "new" and (urls & {a["url"] for a in prior["evidence"]}
                                                     or duplicate_event(payload["definition"], prior["definition"])):
                        return None
            identifier = secrets.token_hex(8)
            # 기사 신선도보다 긴 승인 기한을 표시하지 않습니다.
            expiry = min(now + timedelta(hours=48),
                         max(parsed_time(a["publishedAt"]) for a in payload["evidence"]) + timedelta(hours=24),
                         min(parsed_time(a["publishedAt"]) for a in payload["evidence"]) + timedelta(hours=48))
            db.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,NULL)", (
                identifier, encode(payload), key, "pending", now.isoformat(), expiry.isoformat(), secrets.token_hex(8)))
            return identifier

    def pending(self, now: datetime) -> list[dict]:
        with self.transaction() as db:
            db.execute("UPDATE proposals SET status='expired' WHERE status='pending' AND expires_at<=?", (now.isoformat(),))
            return [dict(row) | {"payload": json.loads(row["payload"])} for row in db.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY created_at")]

    def bind_message(self, proposal_id: str, message_id: int):
        with self.transaction() as db:
            db.execute("UPDATE proposals SET message_id=? WHERE id=? AND status='pending'", (message_id, proposal_id))

    def pairing_token(self, now: datetime) -> str:
        """코드 원문은 DB에 저장하지 않으며 기존 관리자를 재등록으로 덮어쓰지 않습니다."""
        token = secrets.token_urlsafe(24)
        with self.transaction() as db:
            if self._get(db, "ownerId"):
                raise ValueError("이미 개인 승인 계정이 등록되어 있습니다")
            self._put(db, "pairing", {"hash": hashlib.sha256(token.encode()).hexdigest(), "expiresAt": (now + timedelta(minutes=20)).isoformat()})
        return token

    def pair(self, token: str, sender_id: int, chat: dict, now: datetime) -> bool:
        if type(sender_id) is not int or sender_id <= 0 or chat.get("type") != "private" or chat.get("id") != sender_id:
            return False
        with self.transaction() as db:
            pair = self._get(db, "pairing")
            if self._get(db, "ownerId") or not pair or parsed_time(pair["expiresAt"]) <= now:
                return False
            if not hmac.compare_digest(pair["hash"], hashlib.sha256(token.encode()).hexdigest()):
                return False
            self._put(db, "ownerId", sender_id)
            db.execute("DELETE FROM meta WHERE key='pairing'")
            db.execute("INSERT INTO audit(at,action,actor,payload) VALUES (?,?,?,?)", (now.isoformat(), "paired", str(sender_id), "{}"))
            return True

    def decide(self, callback: dict, now: datetime) -> str:
        """본인·개인방·발송 메시지·일회용 값·기한·이슈 버전을 모두 검증합니다."""
        parts = str(callback.get("data", "")).split(":")
        if len(parts) != 4 or parts[0] != "ir" or parts[2] not in {"a", "r"}:
            return "invalid"
        message = callback.get("message", {})
        sender = callback.get("from", {}).get("id")
        with self.transaction() as db:
            owner = self._get(db, "ownerId")
            if not owner or type(sender) is not int or sender != owner or callback.get("from", {}).get("is_bot") is True or message.get("chat", {}).get("id") != owner or message.get("chat", {}).get("type") != "private":
                return "unauthorized"
            row = db.execute("SELECT * FROM proposals WHERE id=?", (parts[1],)).fetchone()
            if not row or row["message_id"] is None or row["message_id"] != message.get("message_id") or not hmac.compare_digest(row["nonce"], parts[3]):
                return "invalid"
            if row["status"] != "pending":
                return "already-decided"
            if parsed_time(row["expires_at"]) <= now:
                db.execute("UPDATE proposals SET status='expired' WHERE id=?", (row["id"],))
                return "expired"
            proposal = json.loads(row["payload"])
            decision = "approved" if parts[2] == "a" else "rejected"
            if decision == "approved":
                evidence = proposal.get("evidence", [])
                if not evidence or any(not parsed_time(a.get("publishedAt")) or not timedelta(0) <= now - parsed_time(a["publishedAt"]) <= timedelta(days=7) for a in evidence):
                    return "stale-evidence"
                try:
                    require_corroboration(evidence, now)
                except ValueError:
                    return "insufficient-evidence"
                radar = self._get(db, "latestRadar", {})
                generated = parsed_time(radar.get("generatedAt"))
                if not generated or not timedelta(0) <= now - generated <= timedelta(hours=26):
                    return "stale-data"
                if proposal["action"] == "new":
                    definition = proposal["definition"]
                    if any(duplicate_event(definition, json.loads(r[0])) for r in db.execute("SELECT definition FROM issues")):
                        return "conflict"
                    current_metrics = {m["id"]: m for i in radar.get("issues", []) for m in i["metrics"]}
                    if any(key not in current_metrics or current_metrics[key]["status"] != "ok" for key in definition["metricIds"]):
                        return "stale-data"
                    if db.execute("SELECT 1 FROM issues WHERE id=?", (definition["id"],)).fetchone():
                        return "conflict"
                    history = [{"at": now.isoformat(), "state": "active", "reason": proposal["rationale"]}]
                    db.execute("INSERT INTO issues VALUES (?,?,?,1,?,?,?,NULL)", (definition["id"], encode(definition), "active", now.isoformat(), now.isoformat(), encode(history)))
                else:
                    current = db.execute("SELECT * FROM issues WHERE id=?", (proposal["issueId"],)).fetchone()
                    if not current or current["version"] != proposal["expectedVersion"]:
                        return "conflict"
                    if not self._eligible(db, proposal["issueId"], proposal["targetState"], now):
                        return "condition-changed"
                    history = json.loads(current["history"]) + [{"at": now.isoformat(), "state": proposal["targetState"], "reason": proposal["rationale"]}]
                    snapshot = next(i for i in radar["issues"] if i["id"] == current["id"]) if proposal["targetState"] == "archived" else None
                    db.execute("UPDATE issues SET state=?,version=version+1,updated_at=?,history=?,archived_snapshot=? WHERE id=?", (
                        proposal["targetState"], now.isoformat(), encode(history), encode(snapshot) if snapshot else None, current["id"]))
                self._put(db, "revision", self._get(db, "revision", 0) + 1)
                self._put(db, "needsPublication", True)
            db.execute("UPDATE proposals SET status=? WHERE id=?", (decision, row["id"]))
            db.execute("INSERT INTO audit(at,action,actor,payload) VALUES (?,?,?,?)", (now.isoformat(), decision, str(sender), encode({"proposalId": row["id"], "proposal": proposal})))
            return decision
