"""개인 승인 권한, 후보 격리, 실제 관측일 기반 상태 전환 검증."""

import copy
from datetime import datetime, timedelta
import json

import pytest

from scripts.issue_radar_llm import KST
from scripts.issue_review_store import ReviewStore
from scripts import issue_discovery as discovery
from scripts import run_issue_review_bot as bot_module
from scripts import update_issue_radar as radar_module

NOW = datetime(2026, 10, 23, 12, tzinfo=KST)
THEME = {"id": "europe", "approved": True, "title": "유럽 재정", "query": "유럽 국채", "route": "국채 → 은행",
         "checkpoints": ["금리차 확대", "은행 약세"], "scoreIds": ["existing_score"], "metricIds": ["spread", "bank"]}


def news(now=NOW):
    titles = ["검증은행 채무 상환 중단, 당국 긴급 유동성 지원 검토",
              "예금 유출 급증한 검증은행, 해외 거래상대방 담보 요구 늘어",
              "검증은행 회사채 급락, 기업 대출 회수로 실물경제 경색 우려"]
    return [{"id": f"n{i}", "title": title, "source": f"테스트 매체{i}",
             "url": f"https://example.com/news{i}", "publishedAt": now.isoformat(),
             "sourceCheck": {"version": 1, "status": "inspected", "checkedAt": now.isoformat(),
                             "publisher": f"paper{i}.example", "origins": [], "contentHash": f"body-{i}"}}
            for i, title in enumerate(titles)]


def radar(now=NOW, **reading):
    value = {"level": "보통", "levelScore": 50, "direction": "완화", "confirmed": 0, "improving": 2, "available": 2, "partial": False}
    value.update(reading)
    return {"generatedAt": now.isoformat(), "issues": [{"id": "europe", "title": "유럽 재정",
        "reading": value, "metrics": [{"id": key, "label": key, "riskSign": 1, "change1w": -10,
             "status": "ok", "asOf": now.date().isoformat()} for key in ("spread", "bank")],
        "news": {"status": "direct", "articles": news(now)}}]}


@pytest.fixture
def store(tmp_path):
    instance = ReviewStore(tmp_path / "private")
    instance.seed([THEME], None, NOW - timedelta(days=7))
    instance.observe(radar(), NOW)
    return instance


def new_payload(store, now=NOW):
    return discovery.validate_proposal({"title": "검증용 은행 유동성", "rationale": "공개 기사에 따른 유동성 점검 후보",
        "themeId": "europe", "articleIds": ["n0", "n1", "n2"], "metricIds": ["spread", "bank"], "keywords": ["검증은행", "유동성"]},
        "new", themes=[THEME], articles=news(now), metrics=[{"id": "spread"}, {"id": "bank"}], registry=store.registry(), eligible={}, now=now)


def enroll(store):
    token = store.pairing_token(NOW)
    assert store.pair(token, 123, {"type": "private", "id": 123}, NOW)
    return token


def callback(store, payload, now=NOW, action="a"):
    identifier = store.add_proposal(payload, now)
    store.bind_message(identifier, 456)
    row = next(p for p in store.pending(now) if p["id"] == identifier)
    return {"id": "query", "from": {"id": 123, "is_bot": False}, "message": {"chat": {"id": 123, "type": "private"}, "message_id": 456},
            "data": f"ir:{identifier}:{action}:{row['nonce']}"}


def transition(store, target="watch", now=NOW):
    return {"action": "transition", "issueId": "europe", "expectedVersion": store.registry()[0]["lifecycle"]["version"],
            "title": "유럽 재정", "targetState": target, "rationale": "지표 완화 지속과 기사 근거 검토", "evidence": news(now)}


def test_pairing_is_private_expiring_one_time_and_does_not_store_plain_token(store):
    token = store.pairing_token(NOW)
    assert token not in json.dumps(store.get("pairing"))
    assert not store.pair(token, 123, {"type": "supergroup", "id": -123}, NOW)
    assert not store.pair(token, 123, {"type": "private", "id": 321}, NOW)
    assert not store.pair("wrong", 123, {"type": "private", "id": 123}, NOW)
    assert not store.pair(token, 123, {"type": "private", "id": 123}, NOW + timedelta(minutes=21))
    token = enroll(store)
    assert not store.pair(token, 124, {"type": "private", "id": 124}, NOW)
    with pytest.raises(ValueError):
        store.pairing_token(NOW)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("change", ["sender", "group", "nonce", "message", "bot", "missing"])
def test_callbacks_fail_closed(store, change):
    enroll(store)
    cb = callback(store, new_payload(store))
    if change == "sender": cb["from"]["id"] = 124
    if change == "group": cb["message"]["chat"] = {"type": "group", "id": 123}
    if change == "nonce": cb["data"] += "x"
    if change == "message": cb["message"]["message_id"] += 1
    if change == "bot": cb["from"]["is_bot"] = True
    if change == "missing": cb.pop("message")
    assert store.decide(cb, NOW) in {"unauthorized", "invalid"}
    assert len(store.registry()) == 1 and store.get("revision") == 0


def test_unpaired_callback_never_grants_permission(store):
    cb = callback(store, new_payload(store))
    assert store.decide(cb, NOW) == "unauthorized"


def test_proposal_is_private_until_approved_and_double_click_is_idempotent(store):
    enroll(store)
    cb = callback(store, new_payload(store))
    assert len(store.registry()) == 1
    assert store.decide(cb, NOW) == "approved"
    assert store.decide(cb, NOW) == "already-decided"
    public, revision = store.public_snapshot()
    assert len(public) == 2 and revision == 1 and store.get("needsPublication") is True
    published = json.dumps(public, ensure_ascii=False)
    for secret in ("ownerId", "nonce", "message_id", "telegramOffset", "pending"):
        assert secret not in published
    assert public[-1]["definition"]["scoreIds"] == THEME["scoreIds"]
    with store.transaction() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='approved'").fetchone()[0] == 1


def test_rejection_and_expiry_do_not_change_public_registry(store):
    enroll(store)
    cb = callback(store, new_payload(store), action="r")
    assert store.decide(cb, NOW) == "rejected"
    assert store.add_proposal(new_payload(store), NOW + timedelta(days=1)) is None
    proposal = new_payload(store)
    proposal["title"] = "다른 검증 사건"
    proposal["definition"]["title"] = proposal["title"]
    proposal["definition"]["id"] = "event-other"
    proposal["evidence"] = [{**a, "url": a["url"] + "-other"} for a in proposal["evidence"]]
    cb = callback(store, proposal)
    assert store.decide(cb, NOW + timedelta(hours=49)) == "expired"
    assert len(store.registry()) == 1 and store.get("revision") == 0


def test_new_approval_rechecks_latest_data(store):
    enroll(store)
    cb = callback(store, new_payload(store))
    current = radar()
    current["issues"][0]["metrics"][0]["status"] = "stale"
    store.observe(current, NOW)
    assert store.decide(cb, NOW) == "stale-data"


def test_repeated_intraday_refreshes_do_not_make_three_observations(store):
    for hour in range(5):
        store.observe(radar(), NOW + timedelta(hours=hour))
    assert store.eligible_transitions(NOW)["europe"] == []
    for offset in (-2, -1):
        day = NOW + timedelta(days=offset)
        store.observe(radar(day), day)
    store.observe(radar(), NOW)
    assert store.eligible_transitions(NOW)["europe"] == ["watch"]


@pytest.mark.parametrize("change", ["partial", "high", "news-failed", "stale", "worsening", "old-snapshot"])
def test_bad_data_and_risk_expansion_block_quiet_news_exit(store, change):
    for offset in (-2, -1, 0):
        day = NOW + timedelta(days=offset)
        current = radar(day)
        if change == "partial": current["issues"][0]["reading"]["partial"] = True
        if change == "high": current["issues"][0]["reading"]["levelScore"] = 80
        if change == "news-failed": current["issues"][0]["news"]["status"] = "failed"
        if change == "stale": current["issues"][0]["metrics"][0]["status"] = "stale"
        if change == "worsening": current["issues"][0]["reading"]["confirmed"] = 1
        store.observe(current, day)
    now = NOW + timedelta(hours=27) if change == "old-snapshot" else NOW
    assert "watch" not in store.eligible_transitions(now)["europe"]


def test_watch_archive_reactivate_preserve_history_and_archive_snapshot(store):
    enroll(store)
    for offset in (-2, -1, 0):
        day = NOW + timedelta(days=offset)
        store.observe(radar(day), day)
    assert store.decide(callback(store, transition(store)), NOW) == "approved"
    assert store.eligible_transitions(NOW)["europe"] == []
    for offset in (3, 4, 5, 6, 7):
        day = NOW + timedelta(days=offset)
        store.observe(radar(day, direction="뚜렷한 변화 없음"), day)
    later = NOW + timedelta(days=7)
    assert store.eligible_transitions(later)["europe"] == ["archived"]
    assert store.decide(callback(store, transition(store, "archived", later), later), later) == "approved"
    frozen = copy.deepcopy(store.registry()[0]["archivedSnapshot"])
    day = later + timedelta(days=1)
    store.observe(radar(day, direction="악화", confirmed=2, levelScore=90), day)
    assert store.registry()[0]["archivedSnapshot"] == frozen
    assert store.eligible_transitions(day)["europe"] == ["active"]
    assert store.decide(callback(store, transition(store, "active", day), day), day) == "approved"
    assert [r["state"] for r in store.registry()[0]["lifecycle"]["history"]] == ["active", "watch", "archived", "active"]


def test_changed_conditions_and_revision_invalidate_pending_transition(store):
    enroll(store)
    for offset in (-2, -1, 0):
        day = NOW + timedelta(days=offset)
        store.observe(radar(day), day)
    cb = callback(store, transition(store))
    store.observe(radar(confirmed=2), NOW)
    assert store.decide(cb, NOW) == "condition-changed"
    with store.transaction() as db:
        db.execute("UPDATE issues SET version=version+1")
    assert store.decide(cb, NOW) == "conflict"


@pytest.mark.parametrize("change", ["one-source", "missing-article", "missing-metric", "extra-field", "search-command", "html", "generic-cooperation", "wrong-theme"])
def test_model_output_requires_evidence_catalog_and_bounded_fields(store, change):
    row = {"title": "검증용 사건", "rationale": "제공 기사 검토", "themeId": "europe", "articleIds": ["n0", "n1", "n2"], "metricIds": ["spread", "bank"], "keywords": ["검증은행", "유동성"]}
    articles = news()
    if change == "one-source": articles[1]["source"] = articles[0]["source"]
    if change == "missing-article": row["articleIds"] = ["n0", "invented"]
    if change == "missing-metric": row["metricIds"] = ["spread", "invented"]
    if change == "extra-field": row["riskScore"] = 0
    if change == "search-command": row["keywords"] = ["site:private", "$(rm)"]
    if change == "html": row["title"] = "<script>bad</script>"
    if change == "generic-cooperation":
        articles[0]["title"] = "한일 CPTPP 협력 회담 개최"
        articles[1]["title"] = "한일 공급망 협력 확대 기대"
    if change == "wrong-theme": row["metricIds"] = ["spread", "kr_yield"]
    with pytest.raises(ValueError):
        discovery.validate_proposal(row, "new", themes=[THEME], articles=articles, metrics=[{"id": "spread"}, {"id": "bank"}, {"id": "kr_yield"}], registry=store.registry(), eligible={}, now=NOW)


def test_discovery_runs_once_daily_and_never_changes_public_issues(store):
    class Selector:
        calls = 0
        def chat_json(self, system, packet, schema, max_tokens):
            self.calls += 1
            assert len(packet["articles"]) == 3
            assert all("publishedAt" in article for article in packet["articles"])
            return {"new": [{"title": "새 검증 사건", "rationale": "공개 기사 검토", "themeId": "europe", "articleIds": ["n0", "n1", "n2"], "metricIds": ["spread", "bank"], "keywords": ["검증은행", "유동성"]}], "transitions": []}
    client = Selector()
    fetch = lambda *args: news()
    assert discovery.discover(store, [THEME], NOW, fetcher=fetch, selector=client,
                              inspector=lambda articles, now: news(now))["proposed"] == 1
    discovery.discover(store, [THEME], NOW + timedelta(minutes=30), fetcher=fetch, selector=client)
    assert client.calls == 1 and len(store.registry()) == 1
    assert len(store.pending(NOW)) == 1


def test_discovery_failures_do_not_expose_exception_or_modify_issues(store):
    class Failed:
        def chat_json(self, *args, **kwargs):
            raise RuntimeError("TOKEN_SECRET")
    result = discovery.discover(store, [THEME], NOW, fetcher=lambda *args: news(), selector=Failed())
    assert result["status"] == "failed"
    assert "TOKEN_SECRET" not in json.dumps(store.get("discovery")) and len(store.registry()) == 1


class FakeBot:
    def __init__(self): self.calls = []
    def call(self, method, **params):
        self.calls.append((method, params))
        return {"message_id": 456}


def test_no_group_delivery_and_buttons_bind_to_private_message(store):
    store.add_proposal(new_payload(store), NOW)
    bot = FakeBot()
    assert bot_module.send_pending(store, bot, NOW) == 0 and not bot.calls
    enroll(store)
    assert bot_module.send_pending(store, bot, NOW) == 1
    params = bot.calls[0][1]
    assert params["chat_id"] == 123
    buttons = params["reply_markup"]["inline_keyboard"][-1]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)
    assert "미승인" in params["text"]
    assert bot_module.send_pending(store, bot, NOW) == 0


def test_saved_decision_survives_telegram_acknowledgement_failure(store):
    class Failed:
        def call(self, *args, **kwargs): raise RuntimeError("network")
    enroll(store)
    cb = callback(store, new_payload(store))
    bot_module.handle_update(store, Failed(), {"callback_query": cb}, NOW)
    assert len(store.registry()) == 2 and store.get("revision") == 1


def test_seed_restores_only_public_approved_registry_without_reenrolling_owner(store, tmp_path):
    enroll(store)
    assert store.decide(callback(store, new_payload(store)), NOW) == "approved"
    registry, revision = store.public_snapshot()
    restored = ReviewStore(tmp_path / "restored")
    restored.seed([THEME], {"registry": registry, "registryRevision": revision}, NOW)
    assert len(restored.registry()) == 2 and restored.get("ownerId") is None
    assert restored.get("revision") == 1 and restored.pending(NOW) == []


@pytest.mark.parametrize("code,remote,newer,expected", [(0, 1, False, False), (0, 0, False, True), (1, 1, False, True), (0, 1, True, True)])
def test_publication_requires_remote_revision_and_keeps_concurrent_approval(store, tmp_path, monkeypatch, code, remote, newer, expected):
    class Process:
        returncode = code
        def poll(self): return code
    store.put("revision", 2 if newer else 1)
    store.put("needsPublication", True)
    publisher = bot_module.Publisher(tmp_path, store)
    publisher.process, publisher.revision = Process(), 1
    publisher.log = (tmp_path / "publication.log").open("a")
    monkeypatch.setattr(bot_module, "remote_revision", lambda: remote)
    publisher.tick(FakeBot())
    assert store.get("needsPublication") is expected


def test_widely_separated_old_observations_do_not_allow_exit(store):
    for offset in (-50, -25, 0):
        day = NOW + timedelta(days=offset)
        store.observe(radar(day), day)
    assert store.eligible_transitions(NOW)["europe"] == []


@pytest.mark.parametrize("offline,ci", [(False, False), (True, False), (False, True)])
def test_refresh_keeps_pending_private_and_preserves_published_registry(store, tmp_path, monkeypatch, offline, ci):
    enroll(store)
    payload = new_payload(store)
    assert store.decide(callback(store, payload), NOW) == "approved"
    registry, revision = store.public_snapshot()
    later = NOW + timedelta(days=1)
    extra = copy.deepcopy(payload)
    extra["title"] = "비공개 검토 전용 제목"
    extra["definition"]["title"] = extra["title"]
    extra["definition"]["id"] = "event-private-only"
    extra["evidence"] = [{**a, "url": a["url"] + "-other"} for a in extra["evidence"]]
    assert store.add_proposal(extra, later)
    root = tmp_path / "dashboard"
    (root / "data").mkdir(parents=True)
    (root / "config").mkdir()
    previous = {"registry": registry, "registryRevision": revision}
    dashboard = {"sections": [{"id": "market", "score": 73.5, "indicators": [], "issueRadar": previous}]}
    (root / "data/risk-dashboard.json").write_text(json.dumps(dashboard))
    for name in ("naver-marketindex-history", "market-history-cache", "kospi-breadth", "kb-market-funds", "dram-spot-prices", "market-risk-timeseries"):
        (root / f"data/{name}.json").write_text("{}")
    (root / "config/market_issues.json").write_text(json.dumps({"issues": [THEME]}))
    monkeypatch.setenv("ISSUE_REVIEW_STATE_DIR", str(store.path.parent))
    monkeypatch.setenv("CI", "true" if ci else "false")

    def build(data, definitions, now, **kwargs):
        return {**radar(now), "methodology": [], "issues": [
            {**radar(now)["issues"][0], "id": d["id"]} for d in definitions]}
    monkeypatch.setattr(radar_module, "build_radar", build)
    monkeypatch.setattr(radar_module, "enrich_radar", lambda *a, **k: None)
    result = radar_module.refresh(root, offline=offline)
    assert len(result["issues"]) == 2 and result["registryRevision"] == 1
    output = (root / "data/risk-dashboard.json").read_text()
    for forbidden in ("비공개 검토 전용 제목", "ownerId", "message_id", "nonce", "pairing", "telegramOffset"):
        assert forbidden not in output
    assert json.loads(output)["sections"][0]["score"] == 73.5


def test_merge_duplicate_hides_event_preserves_audit_and_blocks_reregistration(store):
    enroll(store)
    payload = new_payload(store)
    assert store.decide(callback(store, payload), NOW) == "approved"
    before = store.registry()[1]
    assert store.merge_duplicate(payload["definition"]["id"], "europe", "사용자 요청: 기존 주제와 중복", NOW)
    public, revision = store.public_snapshot()
    assert len(public) == 1 and revision == 2
    assert store.get("needsPublication") is True
    assert store.merged_events()[0]["supersededBy"] == "europe"
    assert store.eligible_transitions(NOW)[payload["definition"]["id"]] == []
    with store.transaction() as db:
        row = db.execute("SELECT history FROM issues WHERE id=?", (payload["definition"]["id"],)).fetchone()
        assert json.loads(row[0])[:-1] == before["lifecycle"]["history"]
        assert db.execute("SELECT count(*) FROM audit WHERE action='merged'").fetchone()[0] == 1
    repeated = copy.deepcopy(payload)
    repeated["definition"]["id"] = "event-renamed"
    repeated["evidence"] = [{**a, "url": a["url"] + "-new"} for a in payload["evidence"]]
    assert store.add_proposal(repeated, NOW) is None
    assert not store.merge_duplicate(payload["definition"]["id"], "europe", "중복 요청", NOW)
    assert store.get("revision") == 2
    store.seed([THEME], {"registry": [before]}, NOW)
    assert len(store.registry()) == 1


def test_unverified_old_proposal_cannot_be_sent_or_approved(store):
    enroll(store)
    cb = callback(store, new_payload(store))
    with store.transaction() as db:
        row = db.execute("SELECT id,payload FROM proposals").fetchone()
        payload = json.loads(row[1])
        for article in payload["evidence"]:
            article.pop("sourceCheck")
        db.execute("UPDATE proposals SET payload=?,message_id=NULL WHERE id=?", (json.dumps(payload), row[0]))
    bot = FakeBot()
    assert bot_module.send_pending(store, bot, NOW, resend=True) == 0
    assert not bot.calls
    store.bind_message(row[0], 456)
    assert store.decide(cb, NOW) == "insufficient-evidence"
    assert len(store.registry()) == 1


def test_telegram_candidate_shows_actual_sources_and_time_span(store):
    proposal = {"payload": new_payload(store), "id": "test", "nonce": "nonce", "expires_at": NOW.isoformat()}
    text, _ = bot_module.proposal_message(proposal)
    assert "중복 제거 3개 출처군" in text
    assert "2026-10-23 12:00 KST" in text


def test_review_deadline_does_not_exceed_news_freshness(store):
    identifier = store.add_proposal(new_payload(store), NOW)
    proposal = next(p for p in store.pending(NOW) if p["id"] == identifier)
    assert datetime.fromisoformat(proposal["expires_at"]) == NOW + timedelta(hours=24)
    assert store.pending(NOW + timedelta(hours=24)) == []
