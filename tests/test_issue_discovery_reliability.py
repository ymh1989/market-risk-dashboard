"""같은 사건 보강, 근거 없는 주장 차단, 수신 장애 격리와 비공개 알림 검증."""

import copy
from datetime import timedelta
import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from scripts import issue_discovery as discovery
from scripts import issue_candidate_review as review
from scripts import run_issue_review_bot as bot_module
from scripts.check_issue_discovery import check
from scripts import install_issue_review_launchd as installer
from test_issue_review import NOW, THEME, FakeBot, enroll, news, radar, new_payload, store


def draft():
    return {"title": "검증은행 유동성 위기", "rationale": "검증은행 위험 점검",
            "themeId": "europe", "articleIds": ["n0"], "metricIds": ["spread", "bank"],
            "keywords": ["검증은행", "유동성"]}


class Selector:
    def __init__(self, verdict="supported", fail=False):
        self.packets, self.verdict, self.fail = [], verdict, fail

    def chat_json(self, system, packet, schema, max_tokens):
        self.packets.append(copy.deepcopy(packet))
        if packet.get("task") == "event-evidence-review":
            if self.fail:
                raise RuntimeError("TOKEN_SECRET")
            return {"verdict": self.verdict, "evidence": [{"id": a["id"], "quote": a["title"]} for a in packet["articles"][:3]]}
        row = draft()
        row["articleIds"] = [packet["articles"][0]["id"]]
        return {"new": [row], "transitions": []}


def inspect(articles, now):
    return [{**a, "sourceCheck": {**news(now)[i % 3]["sourceCheck"], "contentHash": a["id"],
                                 "publisher": f"{a['id']}.example"}} for i, a in enumerate(articles)]


def test_single_article_seed_is_enriched_without_relaxing_three_sources(store):
    current = radar()
    current["issues"][0]["news"]["articles"] = news()[:1]
    store.observe(current, NOW)
    # 폭넓은 검색의 다른 두 기사는 모델 초안의 근거가 아닙니다.
    other = [{**a, "url": a["url"] + "-other", "title": f"다른나라{i} 부채 위험"} for i, a in enumerate(news()[1:])]
    def fetch(definition, now):
        return news()[:1] + other if definition["query"] in discovery.QUERIES else news()
    result = discovery.discover(store, [THEME], NOW, fetcher=fetch, selector=Selector(), inspector=inspect)
    assert result["proposed"] == 1 and result["targetedQueries"] == 1
    assert result["batches"] == 1 and result["reviewedArticles"] == 3
    proposal = store.pending(NOW)[0]["payload"]
    assert proposal["corroboration"]["sourceGroups"] == 3
    assert all("검증은행" in a["title"] for a in proposal["evidence"])
    assert len(store.registry()) == 1 and not store.get("needsPublication")


@pytest.mark.parametrize("verdict", ["mixed", "unsupported", "insufficient"])
def test_semantic_rejection_never_inspects_or_proposes(store, verdict):
    result = discovery.discover(store, [THEME], NOW, fetcher=lambda *a: news(), selector=Selector(verdict),
                               inspector=lambda *a: pytest.fail("문맥 검증 실패 후 원문 조회 금지"))
    assert result["proposed"] == 0 and result["rejected"] == 1
    assert result["noCandidateReason"] == "제안 후보 전부 검증 제외"
    assert not store.pending(NOW)


def test_semantic_failure_and_fetch_failure_are_partial_not_no_news(store):
    def fetch(definition, now):
        if definition["query"] not in discovery.QUERIES:
            raise RuntimeError("TOKEN_SECRET")
        return news()
    result = discovery.discover(store, [THEME], NOW, fetcher=fetch, selector=Selector(fail=True))
    assert result["status"] == "partial"
    assert result["eventReviewFailures"] == result["targetedFetchFailures"] == 1
    assert "TOKEN_SECRET" not in json.dumps(store.discovery_history())


def test_expanded_risk_words_still_require_concrete_shared_event(store):
    articles = news()
    for a, title in zip(articles, ["검증은행 주가 하락 전환", "검증은행 투자 보류", "검증은행 사업 차질"]):
        a["title"] = title
    row = {**draft(), "articleIds": [a["id"] for a in articles]}
    assert discovery.validate_proposal(row, "new", themes=[THEME], articles=articles,
        metrics=[{"id": "spread"}, {"id": "bank"}], registry=store.registry(), eligible={}, now=NOW)
    articles[1]["title"] = "다른나라 국채시장 급락"
    with pytest.raises(ValueError, match="같은 사건"):
        discovery.validate_proposal(row, "new", themes=[THEME], articles=articles,
            metrics=[{"id": "spread"}, {"id": "bank"}], registry=store.registry(), eligible={}, now=NOW)


def test_one_neutral_same_event_headline_does_not_discard_risk_evidence(store):
    articles = news()
    articles[1]["title"] = "검증은행 당국 점검회의 개최"
    row = {**draft(), "articleIds": [a["id"] for a in articles]}
    discovery.validate_proposal(row, "new", themes=[THEME], articles=articles,
        metrics=[{"id": "spread"}, {"id": "bank"}], registry=store.registry(), eligible={}, now=NOW)
    articles[2]["title"] = "검증은행 정기 행사 개최"
    with pytest.raises(ValueError, match="위험 근거"):
        discovery.validate_proposal(row, "new", themes=[THEME], articles=articles,
            metrics=[{"id": "spread"}, {"id": "bank"}], registry=store.registry(), eligible={}, now=NOW)


@pytest.mark.parametrize("change", ["invented-id", "invented-quote", "duplicate-id", "outlook"])
def test_audit_requires_literal_headline_evidence(change):
    articles, row = news(), draft()
    if change == "outlook":
        row["title"] = "검증은행 신용등급 강등"
        for a in articles:
            a["title"] = f"검증은행 신용등급 전망 부정적 전환 {a['id']}"
    result = {"verdict": "supported", "evidence": [{"id": a["id"], "quote": a["title"]} for a in articles]}
    if change == "invented-id": result["evidence"][0]["id"] = "unknown"
    if change == "invented-quote": result["evidence"][0]["quote"] = "존재하지 않는 사실"
    if change == "duplicate-id": result["evidence"][1] = result["evidence"][0]
    class Audit:
        def chat_json(self, *a, **kw): return result
    with pytest.raises(ValueError):
        review.review_candidate(Audit(), row, "new", articles, [])


def test_wire_duplicates_remain_blocked_after_context_approval(store):
    def same_wire(articles, now):
        return [{**a, "sourceCheck": {**a["sourceCheck"], "origins": ["Reuters"]}} for a in inspect(articles, now)]
    result = discovery.discover(store, [THEME], NOW, fetcher=lambda *a: news(), selector=Selector(), inspector=same_wire)
    assert result["proposed"] == 0
    assert any("출처군 부족" in k for k in result["rejectionReasons"])


def test_receive_failure_does_not_block_private_delivery_or_publisher(store, capsys):
    enroll(store)
    store.add_proposal(new_payload(store), NOW)
    class Bot(FakeBot):
        def call(self, method, **params):
            if method == "getUpdates": raise bot_module.TelegramError(method, "HTTP 거부", 409)
            return super().call(method, **params)
    class Publisher:
        called = False
        def tick(self, bot): self.called = True
    bot, publisher, retry = Bot(), Publisher(), {}
    bot_module.service_tick(store, bot, publisher, NOW, retry)
    assert publisher.called and len(bot.calls) == 1
    assert store.pending(NOW)[0]["message_id"] is not None
    assert store.get("botHealth")["receive"]["status"] == "failed"
    assert "409" in capsys.readouterr().out


def test_health_alert_only_after_three_distinct_slots_and_once_per_incident(store):
    enroll(store)
    bot = FakeBot()
    for i, (h, m) in enumerate(discovery.SCHEDULE):
        now = NOW.replace(hour=h, minute=m)
        summary = {"attemptedAt": now.isoformat(), "slot": discovery.scheduled_slot(now), "status": "ok",
                   "modelCandidates": 4, "rejected": 4, "proposed": 0}
        store.record_discovery(summary, [], [], now)
        bot_module.notify_discovery_health(store, bot, now)
        assert len(bot.calls) == (1 if i == 2 else 0)
    bot_module.notify_discovery_health(store, bot, now + timedelta(minutes=1))
    assert len(bot.calls) == 1 and bot.calls[0][1]["chat_id"] == 123
    assert "위험 부재" in bot.calls[0][1]["text"]


@pytest.mark.parametrize("code", [401, 409, 429, 502])
def test_telegram_error_redacts_secrets_but_retains_status(monkeypatch, code):
    def fail(*a, **kw):
        raise urllib.error.HTTPError("https://api.telegram.org/botTOKEN_SECRET/getUpdates", code,
                                     "TOKEN_SECRET", {}, io.BytesIO(b'{"parameters":{"retry_after":60}}'))
    monkeypatch.setattr(bot_module.urllib.request, "urlopen", fail)
    with pytest.raises(bot_module.TelegramError) as exc:
        bot_module.Telegram("TOKEN_SECRET").call("getUpdates")
    assert str(code) in str(exc.value) and "TOKEN_SECRET" not in str(exc.value)
    assert exc.value.retry_after == (60 if code == 429 else 10)


def test_isolated_check_preserves_source_owner_history_and_pending(store):
    enroll(store)
    before = store.public_snapshot(), store.get("ownerId"), store.discovery_history(), store.pending(NOW)
    result = check(store.path, [THEME], NOW, fetcher=lambda *a: news(), selector=Selector(), inspector=inspect)
    assert result["productionUnchanged"] and result["summary"]["proposed"] == 1
    assert (store.public_snapshot(), store.get("ownerId"), store.discovery_history(), store.pending(NOW)) == before
    assert "proposalId" not in json.dumps(result)
    assert "ownerId" not in json.dumps(result)


def test_repeated_same_slot_retries_do_not_trigger_health_alert(store):
    enroll(store)
    for i in range(3):
        now = NOW + timedelta(minutes=i)
        store.record_discovery({"attemptedAt": now.isoformat(), "slot": discovery.scheduled_slot(NOW),
            "status": "ok", "modelCandidates": 4, "rejected": 4, "proposed": 0}, [], [], now)
    bot = FakeBot()
    bot_module.notify_discovery_health(store, bot, now)
    assert not bot.calls


def test_no_candidate_does_not_send_health_alert(store):
    enroll(store)
    for h, m in discovery.SCHEDULE:
        now = NOW.replace(hour=h, minute=m)
        store.record_discovery({"attemptedAt": now.isoformat(), "slot": discovery.scheduled_slot(now),
            "status": "ok", "modelCandidates": 0, "rejected": 0, "proposed": 0}, [], [], now)
    bot = FakeBot()
    bot_module.notify_discovery_health(store, bot, now)
    assert not bot.calls


def test_targeted_results_can_enter_full_related_pool():
    row = draft()
    broad = [{**news()[0], "id": f"b{i}", "url": f"https://example.com/b{i}",
              "title": f"검증은행 주가 하락 사건{i}"} for i in range(20)]
    targeted = {**news()[1], "id": "target", "url": "https://example.com/target", "title": "검증은행 유동성 위기 점검"}
    selected = review.related_articles(row, "new", [], broad + [targeted], broad[:1])
    assert len(selected) == 18 and "target" in {a["id"] for a in selected}


def test_event_review_budget_is_bounded_and_logs_deferral(store):
    class Many(Selector):
        def chat_json(self, system, packet, schema, max_tokens):
            if packet.get("task"):
                return {"verdict": "mixed", "evidence": []}
            return {"new": [{**draft(), "articleIds": [packet["articles"][0]["id"]],
                             "keywords": ["검증은행", f"사건{i}{packet['articles'][0]['id']}"]} for i in range(2)], "transitions": []}
    pool = [{**news()[0], "url": f"https://example.com/{i}", "title": f"검증은행 채무 경고 {i}"} for i in range(24)]
    current = radar()
    current["issues"][0]["news"]["articles"] = []
    store.observe(current, NOW)
    def fetch(definition, now):
        if definition["query"] in discovery.QUERIES:
            group = discovery.QUERIES.index(definition["query"])
            return [{**a, "url": a["url"] + f"/{group}", "title": a["title"] + f" 분야{group}"} for a in pool]
        return []
    result = discovery.discover(store, [THEME], NOW, fetcher=fetch, selector=Many())
    assert result["modelCandidates"] == 8 and result["targetedQueries"] == 4
    assert result["eventReviews"] == 4 and result["proposed"] == 0
    assert result["rejectionReasons"]["회차별 사건 재검토 한도 도달"] == 4


def test_different_topics_receive_review_budget_first():
    results = [("new", {"themeId": t, "title": str(i)}, []) for i, t in enumerate(["korea", "korea", "energy", "korea", "europe", "rates"])]
    ordered = discovery.candidate_order(results)
    assert [r[1]["themeId"] for r in ordered[:4]] == ["korea", "energy", "europe", "rates"]
    assert len(ordered) == len(results)
    assert discovery.candidate_order([("new", None, [])])[0][1] is None


def test_targeted_query_requires_all_event_words_without_phrase_gap():
    row = {**draft(), "keywords": ["코스피", "외국인 매도"]}
    assert review.candidate_query(row, "new", []) == '"코스피" "외국인" "매도"'


def test_transient_news_network_error_is_retried_once(store, monkeypatch):
    from test_issue_review import EmptyDiscovery
    calls = {}
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)
    def fetch(definition, now):
        q = definition["query"]
        calls[q] = calls.get(q, 0) + 1
        if calls[q] == 1:
            raise urllib.error.URLError("TOKEN_SECRET")
        return news()
    result = discovery.discover(store, [THEME], NOW, fetcher=fetch, selector=EmptyDiscovery())
    assert result["status"] == "ok" and result["fetchFailures"] == 0
    assert set(calls.values()) == {2}


def test_service_registration_retries_only_transient_shutdown_race(monkeypatch):
    commands, waits = [], []
    codes = iter([0, 5, 0])
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=next(codes))
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(installer.time, "sleep", waits.append)
    installer.reload_agent("gui/501", Path("/private/test.plist"))
    assert [c[1] for c in commands] == ["bootout", "bootstrap", "bootstrap"]
    assert waits == [2]


def test_service_registration_failure_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: calls.append(a) or SimpleNamespace(returncode=5))
    monkeypatch.setattr(installer.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="종료코드 5"):
        installer.reload_agent("gui/501", Path("/private/test.plist"))
    assert len(calls) == 6


def test_telegram_json_retry_limit_is_respected(monkeypatch):
    body = b'{"ok":false,"error_code":429,"parameters":{"retry_after":7200}}'
    monkeypatch.setattr(bot_module.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(body))
    with pytest.raises(bot_module.TelegramError) as exc:
        bot_module.Telegram("TOKEN_SECRET").call("sendMessage")
    assert exc.value.retry_after == 7200 and "TOKEN_SECRET" not in str(exc.value)
