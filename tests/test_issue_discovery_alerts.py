"""수집 제한 대기, 장애 단위 알림, 비공개 상태 보존을 검증합니다."""

from datetime import timedelta
from email.utils import format_datetime
import json
import urllib.error

import pytest

from scripts import issue_discovery as discovery
from scripts import run_issue_review_bot as bot_module
from scripts.issue_review_store import ReviewStore
from test_issue_review import NOW, THEME, EmptyDiscovery, FakeBot, enroll, news, radar, store


def failure(now=NOW, **changes):
    return {"attemptedAt": now.isoformat(), "slot": discovery.scheduled_slot(now),
            "status": "partial", "reviewedArticles": 83, "fetchFailures": 1,
            "fetchErrors": [{"kind": "http", "code": 429, "scope": "broad"}],
            "nextRetryAt": (now + timedelta(hours=2)).isoformat(), **changes}


def test_same_incident_survives_changed_attempts_counts_and_restart(store):
    enroll(store)
    before = store.public_snapshot(), store.pending(NOW)
    bot = FakeBot()
    for i in range(6):
        now = NOW + timedelta(hours=i)
        store.put("discovery", failure(now, fetchFailures=1 + i % 3))
        bot_module.notify_discovery_health(ReviewStore(store.path.parent), bot, now)
    assert len(bot.calls) == 1
    text = bot.calls[0][1]["text"]
    assert "HTTP 429" in text and "모델 응답 정상" in text and "14:00" in text
    assert (store.public_snapshot(), store.pending(NOW)) == before


def test_migration_does_not_repeat_an_already_sent_alert(store):
    enroll(store)
    store.put("discovery", failure())
    store.put("lastFailureNotice", NOW.isoformat())
    bot = FakeBot()
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(minutes=5))
    store.put("discovery", failure(NOW + timedelta(hours=1)))
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=1))
    assert not bot.calls and store.get("discoveryIncident")["active"]


def test_escalation_once_and_daily_reminder_only_after_a_new_attempt(store):
    enroll(store)
    bot = FakeBot()
    store.put("discovery", failure())
    bot_module.notify_discovery_health(store, bot, NOW)
    later = NOW + timedelta(hours=1)
    store.put("discovery", failure(later, status="failed", reviewedArticles=0, batchFailures=4))
    bot_module.notify_discovery_health(store, bot, later)
    assert len(bot.calls) == 2 and "모델 응답 실패" in bot.calls[-1][1]["text"]
    store.put("discovery", failure(NOW + timedelta(hours=2)))
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=2))
    store.put("discovery", failure(NOW + timedelta(hours=3), status="failed"))
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=3))
    assert len(bot.calls) == 2
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=24))
    assert len(bot.calls) == 2
    store.put("discovery", failure(NOW + timedelta(hours=25), status="failed"))
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=25))
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=25, minutes=1))
    assert len(bot.calls) == 3
    bot_module.notify_discovery_health(store, bot, NOW + timedelta(hours=50))
    assert len(bot.calls) == 3


def test_recovery_is_announced_once_then_new_incident_can_notify(store):
    enroll(store)
    bot = FakeBot()
    for i, status in enumerate(["partial", "running", "waiting-data", "ok", "ok", "partial"]):
        now = NOW + timedelta(minutes=i)
        store.put("discovery", failure(now, status=status))
        bot_module.notify_discovery_health(store, bot, now)
    assert len(bot.calls) == 3 and "복구" in bot.calls[1][1]["text"]


def test_send_failure_does_not_mark_incident_notified(store):
    enroll(store)
    store.put("discovery", failure())
    class OfflineBot:
        def call(self, *args, **kwargs):
            raise bot_module.TelegramError("sendMessage", "연결 실패")
    with pytest.raises(bot_module.TelegramError):
        bot_module.notify_discovery_health(store, OfflineBot(), NOW)
    assert not store.get("discoveryIncident")
    bot = FakeBot()
    bot_module.notify_discovery_health(store, bot, NOW)
    assert len(bot.calls) == 1


def test_unregistered_owner_receives_no_failure_notice(store):
    store.put("discovery", failure())
    bot = FakeBot()
    bot_module.notify_discovery_health(store, bot, NOW)
    assert not bot.calls and not store.get("discoveryIncident")


def test_rate_limit_stops_queued_searches_and_does_not_report_model_failure(store):
    calls = []
    before = store.public_snapshot()
    def limited(definition, now):
        calls.append(definition["query"])
        raise urllib.error.HTTPError("https://example.com/TOKEN_SECRET", 429, "TOKEN_SECRET",
                                     {"Retry-After": "7200"}, None)
    result = discovery.discover(store, [THEME], NOW, fetcher=limited, selector=EmptyDiscovery())
    assert 1 <= len(calls) <= 2
    assert result["status"] == "partial" and result["reviewedArticles"] == 3
    assert result["fetchFailures"] == 4 and result["fetchDeferred"] >= 2
    assert result["batchFailures"] == 0 and result["noCandidateReason"] == "뉴스 수집 일부 미완료"
    assert result["nextRetryAt"] == (NOW + timedelta(hours=2)).isoformat()
    assert result["rateLimitStreak"] == 1
    assert store.public_snapshot() == before
    assert "TOKEN_SECRET" not in json.dumps(store.discovery_history())


def test_targeted_search_is_deferred_after_broad_rate_limit(store):
    from test_issue_discovery_reliability import Selector, inspect
    def limited(definition, now):
        assert definition["query"] in discovery.QUERIES
        raise urllib.error.HTTPError("https://example.com/", 429, "limited", {}, None)
    result = discovery.discover(store, [THEME], NOW, fetcher=limited, selector=Selector(), inspector=inspect)
    assert result["targetedQueries"] == 1 and result["targetedFetchFailures"] == 1
    assert any(e["kind"] == "cooldown" and e["scope"] == "targeted" for e in result["fetchErrors"])
    assert result["status"] == "partial"


def test_retry_backoff_increases_is_capped_and_resets_after_success(store):
    def limited(*args):
        raise urllib.error.HTTPError("https://example.com/", 429, "limited", {}, None)
    now = NOW
    for delay in [1, 2, 4, 6, 6]:
        store.observe(radar(now), now)
        result = discovery.discover(store, [THEME], now, fetcher=limited, selector=EmptyDiscovery(), force=True)
        assert discovery.parsed_time(result["nextRetryAt"]) == now + timedelta(hours=delay)
        now += timedelta(hours=delay)
    store.observe(radar(now), now)
    result = discovery.discover(store, [THEME], now, fetcher=lambda *a: news(now), selector=EmptyDiscovery(), force=True)
    assert result["status"] == "ok" and result["rateLimitStreak"] == 0 and result["nextRetryAt"] is None


def test_cooldown_survives_new_slot_restart_and_force(store):
    last = failure(rateLimitStreak=2)
    store.put("discovery", last)
    assert not discovery.discovery_due(last, NOW + timedelta(hours=1))
    assert discovery.discovery_due(last, NOW + timedelta(hours=2))
    def forbidden(*args):
        pytest.fail("요청 제한 대기 중 외부 재조회 금지")
    result = discovery.discover(ReviewStore(store.path.parent), [THEME], NOW + timedelta(hours=1),
                                fetcher=forbidden, force=True)
    assert result == last


@pytest.mark.parametrize("header,hours", [
    ("7200", 2), (format_datetime(NOW + timedelta(hours=9)), 9),
    ("invalid", 0), (None, 0), ("99999999999999999999999999999", 0), ("-1", 0),
])
def test_retry_after_seconds_date_and_malformed_values(header, hours):
    assert discovery.rate_limit_retry_at(header, NOW) == NOW + timedelta(hours=hours)


def test_server_retry_after_longer_than_internal_cap_is_respected():
    state = {"status": "partial", "fetchErrors": [
        {"kind": "http", "code": 429, "retryAt": (NOW + timedelta(days=2)).isoformat()}]}
    discovery.set_discovery_retry(state, {"rateLimitStreak": 4}, NOW)
    assert discovery.parsed_time(state["nextRetryAt"]) == NOW + timedelta(days=2)


@pytest.mark.parametrize("command", ["/status", "/history"])
def test_commands_explain_source_failure_and_actual_retry_time(store, command):
    enroll(store)
    store.record_discovery(failure(), [], [], NOW)
    bot = FakeBot()
    bot_module.handle_update(store, bot, {"message": {"from": {"id": 123}, "chat": {"id": 123, "type": "private"},
                                                     "text": command}}, NOW)
    text = bot.calls[0][1]["text"]
    assert "429" in text and "모델 응답 정상" in text and "14:00" in text
    assert "12:00" in text
