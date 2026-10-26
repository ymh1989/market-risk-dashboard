"""전재 외신, 동시 보도, 원문 실패, 잘못된 사건 연결의 회귀검증."""

from datetime import datetime, timedelta
import json

import pytest

from scripts import issue_evidence as evidence
from scripts.issue_radar_llm import KST

NOW = datetime(2026, 10, 26, 11, tzinfo=KST)


@pytest.fixture
def reports():
    titles = ["프랑스 예산안 부결, 채무 부담 확산",
              "시위로 멈춘 프랑스 재정개혁, 은행권 우려 확대",
              "프랑스 신용등급 하향 경고에 투자자 자금 이탈"]
    return [{"title": title, "source": f"언론{i}", "url": f"https://paper{i}.example/news",
             "publishedAt": (NOW - timedelta(hours=i)).isoformat(),
             "sourceCheck": {"version": 1, "status": "inspected", "checkedAt": NOW.isoformat(),
                             "publisher": f"paper{i}.example", "origins": [], "contentHash": f"unique-{i}"}}
            for i, title in enumerate(titles)]


def test_concurrent_three_sources_pass(reports):
    assert evidence.require_corroboration(reports, NOW)["sourceGroups"] == 3
    evidence.require_same_event(reports, ["프랑스", "재정 위기"])


@pytest.mark.parametrize("change", ["wire", "same-publisher", "same-title", "near-title", "same-body",
                                   "unknown", "stale", "spread", "future", "two", "same-url", "old-check"])
def test_echo_and_stale_news_never_qualify(reports, change):
    if change == "wire":
        for row in reports:
            row["sourceCheck"]["origins"] = ["Bloomberg"]
    if change == "same-publisher": reports[1]["sourceCheck"]["publisher"] = reports[0]["sourceCheck"]["publisher"]
    if change == "same-title": reports[1]["title"] = reports[0]["title"] + " - 다른 매체"
    if change == "near-title": reports[1]["title"] = reports[0]["title"].replace("부결", "무산")
    if change == "same-body": reports[1]["sourceCheck"]["contentHash"] = reports[0]["sourceCheck"]["contentHash"]
    if change == "unknown": reports[1]["sourceCheck"]["status"] = "unverified"
    if change == "stale": reports[1]["publishedAt"] = (NOW - timedelta(hours=49)).isoformat()
    if change == "spread": reports[1]["publishedAt"] = (NOW - timedelta(hours=25)).isoformat()
    if change == "future": reports[1]["publishedAt"] = (NOW + timedelta(hours=1)).isoformat()
    if change == "old-check": reports[1]["sourceCheck"]["checkedAt"] = (NOW - timedelta(days=3)).isoformat()
    if change == "two": reports.pop()
    if change == "same-url": reports[1]["url"] = reports[0]["url"]
    with pytest.raises(ValueError):
        evidence.require_corroboration(reports, NOW)


def test_unrelated_country_story_not_counted(reports):
    reports[1]["title"] = "국가 부도 속 피어난 낭만 탱고"
    with pytest.raises(ValueError, match="같은 사건"):
        evidence.require_same_event(reports, ["프랑스 부도보험료", "국채금리"])
    with pytest.raises(ValueError):
        evidence.require_same_event(reports, ["금융 위험", "글로벌 위기"])


def test_indirect_wire_chain_is_one_group(reports):
    reports[0]["sourceCheck"]["origins"] = ["Bloomberg"]
    reports[1]["sourceCheck"]["origins"] = ["Bloomberg", "연합뉴스"]
    reports[2]["sourceCheck"]["origins"] = ["연합뉴스"]
    with pytest.raises(ValueError, match="1개"):
        evidence.require_corroboration(reports, NOW)


def test_slightly_edited_wire_body_is_still_one_source(reports):
    body = " ".join(f"공통보도내용{i}" for i in range(200))
    reports[0]["sourceCheck"]["textSignature"] = evidence.text_signature(body)
    reports[1]["sourceCheck"]["textSignature"] = evidence.text_signature("다른 기자 이름 " + body)
    with pytest.raises(ValueError, match="출처군 부족"):
        evidence.require_corroboration(reports, NOW)


def test_mobile_and_desktop_domains_count_as_same_publisher(reports):
    reports[0]["sourceCheck"]["publisher"] = "www.paper.co.kr"
    reports[1]["sourceCheck"]["publisher"] = "m.paper.co.kr"
    with pytest.raises(ValueError, match="출처군 부족"):
        evidence.require_corroboration(reports, NOW)


def test_rss_source_is_not_proof_of_original_reporting(reports):
    article = reports[0]
    body = ("프랑스 예산안 부결과 채무 확산. 블룸버그 통신에 따르면 재정 부담이 커졌다. " * 10)
    result = evidence.inspect_article(article, NOW, resolver=lambda url: url, reader=lambda url: (url, body))
    assert result["sourceCheck"]["origins"] == ["Bloomberg"]
    assert "body" not in result["sourceCheck"]


@pytest.mark.parametrize("body", ["", "짧은 본문", "다른 나라의 전혀 무관한 기사 본문입니다." * 40,
                                 "프랑스 예산안 부결과 채무 확산. 외신 보도에 따르면 위험 증가. " * 10])
def test_inaccessible_or_unknown_origin_is_held(reports, body):
    result = evidence.inspect_article(reports[0], NOW, resolver=lambda url: url, reader=lambda url: (url, body))
    assert result["sourceCheck"]["status"] == "unverified"


def test_structured_article_parser_ignores_site_navigation():
    parser = evidence.ArticleHTML()
    body = "프랑스 예산안 부결과 금융 위험. " * 30
    parser.feed('<nav>Reuters Bloomberg</nav><script type="application/ld+json">' +
                json.dumps({"@type": "NewsArticle", "articleBody": body}) + '</script>')
    assert parser.body() == body.strip()
    assert "Reuters" not in parser.body()


def test_html_article_parser_skips_scripts_and_related_content():
    parser = evidence.ArticleHTML()
    parser.feed('<div id="newsct_article">실제 기사<p>기사 문단</p><aside>다른 기사</aside>'
                '<script>잘못된 출처</script></div><footer>사이트 안내</footer>')
    assert parser.body() == "실제 기사 기사 문단"


def test_private_addresses_and_credential_urls_are_blocked(monkeypatch):
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))])
    for url in ("https://127.0.0.1/", "https://user:pw@example.com/", "http://example.com/", "https://example.com:22/"):
        with pytest.raises(ValueError):
            evidence.checked_host(url)


def test_merged_france_event_cannot_be_renamed_into_another_candidate():
    prior = {"themeId": "europe", "title": "프랑스 부도보험료 급등 국채금리 5% 돌파 시위 격화",
             "query": '("프랑스 부도보험료" OR "국채금리 5%")'}
    assert evidence.duplicate_event({"themeId": "europe", "title": "프랑스 CDS 다시 급등"}, prior)
    assert not evidence.duplicate_event({"themeId": "europe", "title": "독일 은행 유동성 위기"}, prior)
