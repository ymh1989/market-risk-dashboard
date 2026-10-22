"""이슈 레이더의 뉴스 위생·시점·단위·점수 보존 회귀검증."""

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import update_issue_radar as radar

NOW = datetime(2026, 10, 22, 16, tzinfo=radar.KST)
ROOT = Path(__file__).resolve().parents[1]


def article(**changes):
    return {"title": "프랑스 국채 동향 - 언론사", "url": "https://example.com/news",
            "source": "언론사", "publishedAt": "2026-10-22T12:00:00+09:00", **changes}


def metric(**changes):
    return {"id": "test", "label": "검증 지표", "unit": "%", "riskSign": 1,
            "threshold": 5, "changeMode": "yield", "points": [
                {"date": f"2026-10-{day:02}", "close": value}
                for day, value in [(15, 0), (16, .01), (19, .02), (20, .03), (21, .04), (22, .1)]],
            **changes}


def empty_data():
    return {"dashboard": {"sections": [{"id": "market", "indicators": []}]}, "naver": {},
            "history": {}, "breadth": {}, "funds": {}, "dram": {}, "timeseries": {}}


def test_news_deduplication_dates_and_links():
    rows = [article(), article(url="https://example.com/copy"),
            article(title="<b>다른 기사</b>", url="https://example.com/second"),
            article(title="미래", publishedAt="2026-10-23T00:00:00+09:00"),
            article(title="날짜 없음", publishedAt=None),
            article(title="오래된 기사", publishedAt="2026-01-01"),
            article(title="스크립트", url="javascript:alert(1)"),
            article(title="비정상 URL", url="https://[broken"),
            article(title="인증정보", url="https://user:secret@example.com/")]
    result = radar.clean_articles(rows, NOW)
    assert len(result) == 2
    assert result[1]["title"] == "다른 기사"
    assert all(radar.timestamp(row["publishedAt"]) <= NOW for row in result)


def test_rss_query_does_not_add_nested_group(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b'<rss><channel><item><title>News</title><link>https://example.com/n</link><pubDate>Thu, 22 Oct 2026 03:00:00 GMT</pubDate></item></channel></rss>'

    def open_url(request, timeout):
        seen.append(parse_qs(urlsplit(request.full_url).query)["q"][0])
        assert timeout == 10
        return Response()

    monkeypatch.setattr(radar.urllib.request, "urlopen", open_url)
    query = "(France OR 프랑스) (bond OR 국채)"
    assert len(radar.fetch_articles({"query": query}, NOW)) == 1
    assert seen == [query + " when:7d"]


def test_mixed_rss_utc_and_stored_kst_dates_sort_by_actual_time():
    result = radar.clean_articles([article(), article(title="나중 기사", url="https://example.com/later",
                                                     publishedAt="2026-10-22T06:00:00+00:00")], NOW)
    assert result[0]["title"] == "나중 기사"
    assert result[0]["publishedAt"] == "2026-10-22T15:00:00+09:00"


def test_cache_ttl_failure_and_query_changes():
    definition = {"query": "프랑스 국채"}
    calls = []

    def fetch(*args):
        calls.append(True)
        return [article()]

    first = radar.news_for_issue(definition, {}, NOW, fetch)
    second = radar.news_for_issue(definition, {"news": first}, NOW + timedelta(minutes=30), fetch)
    assert second["status"] == "cached" and len(calls) == 1
    radar.news_for_issue({"query": "프랑스 재정"}, {"news": first}, NOW, fetch)
    assert len(calls) == 2

    def fail(*args):
        raise RuntimeError("인증키 등 외부 공개 금지")

    failed = radar.news_for_issue(definition, {"news": first}, NOW + timedelta(hours=2), fail)
    assert failed["status"] == "failed" and failed["articles"] == first["articles"]
    assert failed["retrievedAt"] == first["retrievedAt"]
    assert "인증키" not in json.dumps(failed, ensure_ascii=False)
    assert radar.news_for_issue(definition, {}, NOW, lambda *_: [])["label"] == "최근 기사 없음"
    assert radar.news_for_issue(definition, {}, NOW, fail)["label"] == "조회 실패"


@pytest.mark.parametrize("mode,previous,current,expected", [
    ("yield", 0, .1, 10), ("yield", -.1, 0, 10), ("absolute", 30, 31, 1),
    ("percent", 100, 102, 2), ("percent", 0, 1, None)
])
def test_units(mode, previous, current, expected):
    assert radar.difference({"changeMode": mode}, current, previous) == expected


def test_series_and_ratios_do_not_fill_missing_dates_or_use_future():
    first = [{"date": "2026-10-21", "close": 100}, {"date": "2026-10-22", "close": 120},
             {"date": "2026-10-23", "close": 900}]
    second = [{"date": "2026-10-20", "close": 20}, {"date": "2026-10-22", "close": 60}]
    assert radar.ratio_points(first, second, NOW) == [{"date": "2026-10-22", "close": 100}]
    assert len(radar.clean_points(first, NOW)) == 2
    assert radar.clean_points([{"date": "2026-10-22", "close": float("inf")}], NOW) == []


def test_anchor_and_change_use_observed_dates_only():
    result = radar.summarize_metric(metric(), NOW, "2026-10-19")
    assert result["change1w"] == 10 and result["deltaUnit"] == "bp"
    assert result["anchorBaseDate"] == "2026-10-16"
    assert result["changeSinceAnchor"] == 9
    assert radar.summarize_metric(metric(), NOW, "2026-10-15")["changeSinceAnchor"] is None
    assert radar.summarize_metric(metric(), NOW, "2026-10-23")["changeSinceAnchor"] is None
    assert radar.summarize_metric(metric(points=[]), NOW, None)["value"] is None


def test_reading_keeps_level_and_direction_separate_and_waits_on_missing():
    definition = {"scoreIds": ["score"]}
    scores = {"score": [{"date": "2026-10-21", "value": 90}]}
    item = radar.summarize_metric(metric(), NOW, None)
    up = radar.market_reading(definition, [item, item], scores, NOW)
    assert up["level"] == "경계" and up["direction"] == "악화"
    down = {**item, "change1w": -10}
    assert radar.market_reading(definition, [down, down], scores, NOW)["direction"] == "완화"
    assert radar.market_reading(definition, [item, down], scores, NOW)["direction"] == "혼조"
    stale = {**item, "status": "stale"}
    assert radar.market_reading(definition, [item, stale], scores, NOW)["direction"] == "판독 대기"
    assert radar.market_reading(definition, [item, {**item, "riskSign": 0}], scores, NOW)["direction"] == "판독 대기"
    assert radar.market_reading(definition, [item, item], scores, NOW + timedelta(days=20))["level"] == "미확인"


def test_build_is_read_only_and_offline_does_not_access_network():
    data = empty_data()
    before = copy.deepcopy(data)
    config = json.loads((ROOT / "config/market_issues.json").read_text())["issues"]
    config.append({"id": "unapproved", "approved": False})

    def forbidden(*args):
        pytest.fail("오프라인 모드에서 네트워크 호출")

    result = radar.build_radar(data, config, NOW, fetcher=forbidden, offline=True)
    assert len(result["issues"]) == 6
    assert all(i["weight"] == 0 and i["reading"]["level"] == "미확인" for i in result["issues"])
    assert data == before
    json.dumps(result, allow_nan=False)


def test_pipeline_embeds_radar_before_validation_and_offline_export():
    runner = (ROOT / "scripts/run_local_market_update.sh").read_text()
    assert runner.index('"$PYTHON_BIN" scripts/update_issue_radar.py') < runner.index('CURRENT_STAGE="품질 검증"')
    assert runner.index('"$PYTHON_BIN" scripts/update_issue_radar.py') < runner.index('scripts/export_offline_dashboard.py --stable-only')
    assert "scripts/update_issue_radar.py" in (ROOT / ".github/workflows/update-market-risk.yml").read_text()
