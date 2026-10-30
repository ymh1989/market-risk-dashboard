"""로컬 AI의 선택 범위·장애 대체·시점·점수 보존 검증."""

import copy
from datetime import datetime, timedelta
import json
import urllib.request

import pytest

from scripts import issue_radar_llm as llm

NOW = datetime(2026, 10, 22, 17, tzinfo=llm.KST)


def issue(key="europe"):
    return {"id": key, "title": "프랑스 재정", "route": "국채 → 은행", "weight": 0,
            "checkpoints": ["국채차 확대 여부", "은행 상대약세 여부"],
            "reading": {"level": "경계", "direction": "악화", "levelScore": 92.9},
            "news": {"articles": [
                {"title": "미국 국채 금리 부담" if key == "rates" else "프랑스 국채 부담", "url": "https://example.com/a", "source": "공개 언론사", "publishedAt": "2026-10-22T12:00:00+09:00"},
                {"title": "프랑스 축구 경기", "url": "https://example.com/b", "source": "공개 언론사", "publishedAt": "2026-10-22T15:00:00+09:00"}]},
            "metrics": [{"id": "fr_spread", "label": "국채차", "status": "ok", "riskSign": 1, "threshold": 5,
                         "change1w": 16.3, "value": 157.8, "asOf": "2026-10-21"}]}


class Selector:
    def __init__(self):
        self.calls = []

    def generate(self, packets):
        self.calls.append(copy.deepcopy(packets))
        return {key: {"articleIds": [packet["articles"][0]["id"]],
                      "metricIds": [row["id"] for row in packet["metrics"]][:1], "checkpointIndex": 0}
                for key, packet in packets.items()}


def test_one_request_for_all_issues_and_no_free_text_or_score_change():
    radar = {"issues": [issue(), issue("rates")]}
    before = copy.deepcopy(radar)
    client = Selector()
    llm.enrich_radar(radar, None, NOW, selector=client, environ={})
    assert len(client.calls) == 1 and set(client.calls[0]) == {"europe", "rates"}
    assert radar["llm"]["status"] == "generated"
    for current, original in zip(radar["issues"], before["issues"]):
        assert current["reading"] == original["reading"]
        assert current["metrics"] == original["metrics"] and current["weight"] == original["weight"]
        assert set(current["analysis"]["selection"]) == {"articleIds", "metricIds", "checkpointIndex"}
    sent = json.dumps(client.calls, ensure_ascii=False)
    assert "https://" not in sent and "157.8" not in sent


@pytest.mark.parametrize("changes", [
    {"articleIds": ["invented"]}, {"metricIds": ["other_issue_metric"]},
    {"checkpointIndex": True}, {"checkpointIndex": -1}, {"checkpointIndex": 99},
    {"summary": "모델이 만들어낸 문장"}, {"riskScore": 0},
    {"articleIds": []}, {"metricIds": []}, {"articleIds": [None]},
])
def test_invalid_model_choices_are_rejected(changes):
    packet = llm.packet_for(issue(), NOW)
    valid = Selector().generate({"europe": packet})["europe"]
    with pytest.raises(ValueError):
        llm.validate_selection({**valid, **changes}, packet)


def test_empty_selection_is_valid_and_duplicates_are_not():
    packet = llm.packet_for(issue(), NOW)
    empty = {"articleIds": [], "metricIds": [], "checkpointIndex": None}
    assert llm.validate_selection(empty, packet) == empty
    valid = Selector().generate({"europe": packet})["europe"]
    with pytest.raises(ValueError):
        llm.validate_selection({**valid, "articleIds": valid["articleIds"] * 2}, packet)


def test_cache_matches_prompt_model_context_and_keeps_original_generated_time():
    first, client = {"issues": [issue()]}, Selector()
    llm.enrich_radar(first, None, NOW, selector=client, environ={})
    second = {"issues": [issue()]}
    llm.enrich_radar(second, first, NOW + timedelta(minutes=10), selector=client, environ={})
    assert len(client.calls) == 1
    assert second["issues"][0]["analysis"]["status"] == "cached"
    assert second["issues"][0]["analysis"]["generatedAt"] == NOW.isoformat()
    changed = {"issues": [issue()]}
    changed["issues"][0]["reading"]["direction"] = "완화"
    llm.enrich_radar(changed, second, NOW + timedelta(minutes=20), selector=client, environ={})
    assert len(client.calls) == 2
    llm.enrich_radar({"issues": [issue()]}, first, NOW, selector=client, environ={"ISSUE_RADAR_LLM_MODEL": "different"})
    assert len(client.calls) == 3


def test_changed_values_are_read_live_while_unchanged_selection_context_reuses_cache():
    first, client = {"issues": [issue()]}, Selector()
    llm.enrich_radar(first, None, NOW, selector=client, environ={})
    second = {"issues": [issue()]}
    second["issues"][0]["metrics"][0]["value"] = 160
    second["issues"][0]["metrics"][0]["change1w"] = 18
    llm.enrich_radar(second, first, NOW, selector=client, environ={})
    assert len(client.calls) == 1
    assert second["issues"][0]["metrics"][0]["value"] == 160


def test_unavailable_model_does_not_fail_dashboard_and_cools_down():
    class Failed:
        calls = 0

        def generate(self, packets):
            self.calls += 1
            raise TimeoutError("비공개 예외·경로·비밀번호")

    first, client = {"issues": [issue()]}, Failed()
    llm.enrich_radar(first, None, NOW, selector=client, environ={})
    assert first["llm"]["status"] == "unavailable"
    assert "비밀번호" not in json.dumps(first, ensure_ascii=False)
    second = {"issues": [issue()]}
    llm.enrich_radar(second, first, NOW + timedelta(minutes=5), selector=client, environ={})
    assert client.calls == 1 and second["llm"]["status"] == "cooldown"
    llm.enrich_radar({"issues": [issue()]}, first, NOW + timedelta(minutes=31), selector=client, environ={})
    assert client.calls == 2


@pytest.mark.parametrize("environ,offline", [({"CI": "true"}, False), ({"ISSUE_RADAR_LLM_ENABLED": "0"}, False), ({}, True)])
def test_disabled_and_offline_never_call_model(environ, offline):
    client, radar = Selector(), {"issues": [issue()]}
    llm.enrich_radar(radar, None, NOW, selector=client, environ=environ, offline=offline)
    assert client.calls == [] and radar["llm"]["status"] == "disabled"


def test_no_recent_news_and_stale_metrics_do_not_get_commentary():
    current = issue()
    current["metrics"][0]["status"] = "stale"
    assert llm.packet_for(current, NOW)["metrics"] == []
    for article in current["news"]["articles"]:
        article["publishedAt"] = "2026-10-01T12:00:00+09:00"
    client, radar = Selector(), {"issues": [current]}
    llm.enrich_radar(radar, None, NOW, selector=client, environ={})
    assert client.calls == [] and radar["issues"][0]["analysis"]["reason"] == "no-news"


@pytest.mark.parametrize("url", ["https://example.com", "http://192.168.1.2:11434", "http://localhost.evil.com", "http://user:pass@localhost", "http://localhost/proxy", "http://localhost?target=remote"])
def test_only_loopback_endpoints_are_allowed(url):
    with pytest.raises(ValueError):
        llm.OllamaIssueSelector(base_url=url)


def test_structured_local_request_has_no_tools_and_rejects_incomplete_response():
    client = llm.OllamaIssueSelector(timeout=999)
    assert client.timeout == 60
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b'{"done":true,"done_reason":"length","message":{"content":"{}"}}'

    class Opener:
        def open(self, request, timeout):
            captured.append(json.loads(request.data))
            return Response()

    client.opener = Opener()
    with pytest.raises(ValueError):
        client.generate({"europe": llm.packet_for(issue(), NOW)})
    payload = captured[0]
    assert payload["think"] is False and payload["stream"] is False
    assert payload["format"]["additionalProperties"] is False
    assert payload["options"]["seed"] == 42 and "tools" not in payload
    with pytest.raises(ValueError):
        llm.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_invalid_choice_falls_back_without_discarding_other_valid_issues():
    class Partial(Selector):
        def generate(self, packets):
            result = super().generate(packets)
            result["europe"]["metricIds"] = ["not-provided"]
            return result

    radar = {"issues": [issue(), issue("rates")]}
    llm.enrich_radar(radar, None, NOW, selector=Partial(), environ={})
    assert radar["llm"]["status"] == "partial"
    assert radar["issues"][0]["analysis"]["reason"] == "invalid-response"
    assert radar["issues"][1]["analysis"]["status"] == "generated"


def test_compact_request_ids_round_trip_and_unknown_ids_fail_independently():
    client = llm.OllamaIssueSelector()
    packets = {key: llm.packet_for(issue(key), NOW) for key in ("europe", "rates")}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            content = {"europe": {"articleIds": ["a0"], "metricIds": ["m0"], "checkpointIndex": 0},
                       "rates": {"articleIds": ["a999"], "metricIds": ["m0"], "checkpointIndex": 0}}
            return json.dumps({"done": True, "done_reason": "stop", "message": {"content": json.dumps(content)}}).encode()

    class Opener:
        def open(self, request, timeout):
            payload = json.loads(request.data)
            packet = json.loads(payload["messages"][1]["content"])["issues"]["europe"]
            assert packet["articles"][0] == {"id": "a0", "title": "프랑스 국채 부담", "source": "공개 언론사", "publishedAt": "2026-10-22T12:00:00+09:00"}
            assert packets["europe"]["articles"][0]["id"] not in str(payload)
            return Response()

    client.opener = Opener()
    result = client.generate(packets)
    assert result["europe"]["articleIds"] == [packets["europe"]["articles"][0]["id"]]
    assert result["europe"]["metricIds"] == ["fr_spread"]
    assert result["rates"] is None


def test_selection_cache_expires_and_future_generated_time_is_not_reused():
    first, client = {"issues": [issue()]}, Selector()
    llm.enrich_radar(first, None, NOW, selector=client, environ={})
    llm.enrich_radar({"issues": [issue()]}, first, NOW + timedelta(hours=24), selector=client, environ={})
    assert len(client.calls) == 2
    first["issues"][0]["analysis"]["generatedAt"] = (NOW + timedelta(hours=1)).isoformat()
    llm.enrich_radar({"issues": [issue()]}, first, NOW, selector=client, environ={})
    assert len(client.calls) == 3


@pytest.mark.parametrize("theme,title,excluded", [
    ("dollar", "마이크로소프트 미국 채권 발행으로 250억 달러 조달", True),
    ("dollar", "홍콩법인 달러 디지털 채권 발행 성공", True),
    ("dollar", "마이크로소프트, 미국 채권 발행: AAA 신용등급으로 AI 자금 조달 우려 완화", True),
    ("dollar", "미국 기업 신용등급 하향 조정에 채권 가격 하락", False),
    ("dollar", "미국 기업 차환 부담 확대, 하이일드 스프레드 급등", False),
    ("dollar", "미국 단기 자금시장 안정, SOFR 금리차 축소", False),
    ("energy", "미국과 이란 협상 기대에 유가 하락", False),
    ("semiconductors", "AI 데이터센터 건설 지연, 메모리 수요 우려", False),
    ("rates", "프랑스 국채 금리 하락에 유로 강세", True),
    ("rates", "프랑스 국채 불안에 미국 국채 금리 하락", False),
    ("rates", "미국 국채 금리가 6% 돌파하면", True),
])
def test_topic_guards_keep_easing_evidence_and_exclude_routine_funding(theme, title, excluded):
    from scripts.issue_relevance import exclusion_reason
    assert bool(exclusion_reason(theme, title)) is excluded


def test_candidates_expand_to_twelve_and_keep_topic_specific_titles():
    current = issue("semiconductors")
    current["news"]["articles"] = [{**current["news"]["articles"][0], "title": f"반도체 {n} 실적 수요 점검", "url": f"https://example.com/{n}"} for n in range(20)]
    assert len(llm.candidates(current, NOW)) == 12


def test_batched_selector_rechecks_empty_topics_once_without_forcing_selection(monkeypatch):
    client = llm.OllamaIssueSelector()
    packets = {f"topic{i}": llm.packet_for(issue(), NOW) for i in range(6)}
    calls = []
    def chat(system, content, schema, max_tokens=700):
        calls.append(content)
        return {key: {"articleIds": [], "metricIds": [], "checkpointIndex": None} for key in content["issues"]}
    monkeypatch.setattr(client, "chat_json", chat)
    result = client.generate(packets)
    assert [len(c["issues"]) for c in calls] == [3, 3, 2]
    assert all(not row["articleIds"] for row in result.values())


def test_empty_topic_recheck_can_recover_related_article(monkeypatch):
    client = llm.OllamaIssueSelector()
    calls = []
    def chat(system, content, schema, max_tokens=700):
        calls.append(system)
        return {"europe": {"articleIds": ["a0"] if len(calls) == 2 else [],
                           "metricIds": ["m0"] if len(calls) == 2 else [],
                           "checkpointIndex": 0 if len(calls) == 2 else None}}
    monkeypatch.setattr(client, "chat_json", chat)
    packet = llm.packet_for(issue(), NOW)
    result = client.generate({"europe": packet})
    assert len(calls) == 2
    assert result["europe"]["articleIds"] == [packet["articles"][0]["id"]]


def test_batch_failure_keeps_other_successful_topics(monkeypatch):
    client = llm.OllamaIssueSelector()
    packets = {f"topic{i}": llm.packet_for(issue(), NOW) for i in range(4)}
    def chat(system, content, schema, max_tokens=700):
        if "topic3" in content["issues"]:
            raise TimeoutError()
        return {key: {"articleIds": ["a0"], "metricIds": ["m0"], "checkpointIndex": 0} for key in content["issues"]}
    monkeypatch.setattr(client, "chat_json", chat)
    result = client.generate(packets)
    assert result["topic3"] is None
    assert all(result[f"topic{i}"]["articleIds"] for i in range(3))
