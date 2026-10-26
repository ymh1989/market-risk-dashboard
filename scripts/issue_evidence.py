"""같은 외신의 전재를 다중 보도로 세지 않는 비공개 후보 검증입니다."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

if __package__:
    from .issue_radar_llm import parsed_time
else:
    from issue_radar_llm import parsed_time

MIN_REPORTS = 3
MAX_AGE_HOURS = 48
MAX_SPAN_HOURS = 24
POLICY_VERSION = 1
ORIGINS = {
    "Reuters": r"로이터|\breuters\b",
    "Bloomberg": r"블룸버그|\bbloomberg\b",
    "AP": r"AP\s*통신|\bassociated press\b",
    "AFP": r"AFP\s*통신|\bagence france.presse\b|\bAFP\b",
    "Financial Times": r"파이낸셜\s*타임[스즈]|\bfinancial times\b|\bFT\b",
    "Wall Street Journal": r"월스트리트\s*저널|\bwall street journal\b|\bWSJ\b",
    "연합뉴스": r"연합뉴스|\byonhap\b",
    "뉴시스": r"뉴시스|\bnewsis\b",
    "뉴스1": r"뉴스\s*1|\bnews1\b",
}
GENERIC = set("금융 시장 위험 우려 경제 충격 글로벌 경고 국가 부도 위기 유동성 국채금리 금리 부채 신용 증시 정책 여파 확산 은행 투자 하락 상승 급등 급락 전이 거품 ai 주식 가능성 경색 부담".split())


def normalize(text: str) -> str:
    """구두점·매체 접미사 차이는 기사 독립성의 근거로 보지 않습니다."""
    return re.sub(r"[\W_]", "", text.casefold())


def headline(article: dict) -> str:
    return normalize(article["title"].split(" - ")[0])


def similar_headline(left: dict, right: dict) -> bool:
    a, b = headline(left), headline(right)
    return bool(a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.78))


def text_signature(body: str) -> list[str]:
    """본문을 저장하지 않고 긴 공통 문구의 해시 표본만 남깁니다."""
    words = re.findall(r"[가-힣a-z0-9]+", body.casefold())
    return sorted({hashlib.sha256(" ".join(words[i:i + 5]).encode()).hexdigest()[:16]
                   for i in range(max(0, len(words) - 4))})[:80]


def similar_body(left: dict, right: dict) -> bool:
    a, b = set(left.get("textSignature", [])), set(right.get("textSignature", []))
    return min(len(a), len(b)) >= 12 and len(a & b) / len(a | b) >= 0.7


def publisher_family(host: str) -> str:
    parts = host.casefold().split(".")
    size = 3 if ".".join(parts[-2:]) in {"co.kr", "co.jp", "co.uk", "com.au", "com.cn", "com.hk"} else 2
    return ".".join(parts[-size:])


def event_anchors(keywords: list[str]) -> set[str]:
    """위험·금융 같은 일반어만으로 다른 국가의 기사를 연결하지 않습니다."""
    return {normalize(t) for word in keywords for t in re.findall(r"[가-힣A-Za-z]{2,}", word)
            if t.casefold() not in GENERIC}


def require_same_event(articles: list[dict], keywords: list[str]) -> None:
    anchors = event_anchors(keywords)
    if not anchors or any(not any(key in headline(a) for key in anchors) for a in articles):
        raise ValueError("같은 사건의 구체적 대상이 모든 근거 제목에 확인되지 않음")


def duplicate_event(candidate: dict, existing: dict) -> bool:
    if not candidate.get("title") or not existing.get("themeId") or candidate.get("themeId") != existing.get("themeId"):
        return False
    if similar_headline(candidate, existing):
        return True
    terms = re.findall(r'"([^"]+)"', existing.get("query", ""))
    text = headline(candidate).replace("cds", "부도보험료")
    return any(len(event_anchors([term])) >= 2 and normalize(term).replace("cds", "부도보험료") in text for term in terms)


class ArticleHTML(HTMLParser):
    """구조화된 기사 본문 또는 기사 영역만 읽고 메뉴·관련 기사 영역은 제외합니다."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.parts, self.scripts, self.current_script = [], [], []
        self.article_depth = None
        self.json_depth = None

    def handle_starttag(self, tag, attrs):
        if tag in {"meta", "link", "img", "br", "hr", "input", "source", "wbr"}:
            return
        self.stack.append(tag)
        attrs = dict(attrs)
        identity = " ".join((attrs.get("id", ""), attrs.get("class", ""))).casefold()
        if tag == "article" or re.search(r"article[_-]?(body|view|content)|news[_-]?(body|content)|newsct_article", identity):
            if self.article_depth is None:
                self.article_depth = len(self.stack)
        if tag == "script" and attrs.get("type", "").casefold() == "application/ld+json":
            self.json_depth, self.current_script = len(self.stack), []

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.json_depth and index < self.json_depth:
            self.scripts.append("".join(self.current_script))
            self.json_depth = None
        if self.article_depth and index < self.article_depth:
            self.article_depth = None
        del self.stack[index:]

    def handle_data(self, data):
        if self.json_depth:
            self.current_script.append(data)
        elif self.article_depth and not any(t in self.stack for t in ("script", "style", "nav", "aside", "footer")):
            self.parts.append(data)

    def body(self) -> str:
        def bodies(value):
            if isinstance(value, dict):
                if isinstance(value.get("articleBody"), str):
                    yield value["articleBody"]
                for child in value.values():
                    yield from bodies(child)
            elif isinstance(value, list):
                for child in value:
                    yield from bodies(child)
        structured = []
        for script in self.scripts:
            try:
                structured.extend(bodies(json.loads(script)))
            except (ValueError, TypeError):
                continue
        return " ".join((max(structured, key=len) if structured else " ".join(self.parts)).split())[:30000]


def checked_host(url: str) -> str:
    """기사 URL과 모든 리디렉션에서 로컬·사설 주소 접근을 차단합니다."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in {None, 443}:
        raise ValueError("공개 HTTPS 기사만 허용")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("사설 기사 주소 차단")
    return parts.hostname.removeprefix("www.")


def fetch_body(url: str) -> tuple[str, str]:
    """원문은 메모리에서 출처 판독에만 사용하며 공개 데이터에 저장하지 않습니다."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    for _ in range(4):
        checked_host(url)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (market-risk-evidence)"})
        try:
            with opener.open(request, timeout=8) as response:
                if "text/html" not in response.headers.get("Content-Type", ""):
                    raise ValueError("기사 HTML 응답 아님")
                encoding = response.headers.get_content_charset() or "utf-8"
                parser = ArticleHTML()
                parser.feed(response.read(1_500_000).decode(encoding, errors="replace"))
                return url, parser.body()
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308} or not error.headers.get("Location"):
                raise
            url = urllib.parse.urljoin(url, error.headers["Location"])
    raise ValueError("기사 리디렉션 제한 초과")


def resolve_article(url: str) -> str:
    if urllib.parse.urlsplit(url).hostname == "news.google.com":
        # 비공개 승인 서비스만 선택 의존성을 사용합니다. 공개 갱신에는 필요하지 않습니다.
        from googlenewsdecoder import gnewsdecoder
        result = gnewsdecoder(url, timeout=5)
        if not result.get("success"):
            raise ValueError("원문 링크 확인 실패")
        return result["decoded_url"]
    return url


def inspect_article(article: dict, now: datetime, *, resolver=resolve_article, reader=fetch_body) -> dict:
    """접근 불가·본문 부족은 독립 보도로 간주하지 않습니다."""
    check = {"version": POLICY_VERSION, "status": "unverified", "checkedAt": now.isoformat()}
    try:
        url, body = reader(resolver(article["url"]))
        if len(body) < 250:
            raise ValueError("원문 본문 부족")
        terms = event_anchors([article["title"].split(" - ")[0]])
        if not terms or sum(term in normalize(body) for term in terms) < min(2, len(terms)):
            raise ValueError("원문과 제목 연결 미확인")
        publisher = urllib.parse.urlsplit(url).hostname.removeprefix("www.")
        origins = [name for name, pattern in ORIGINS.items() if re.search(pattern, body, re.I)
                   or re.fullmatch(pattern, article["source"], re.I)]
        # 출처 불명의 외신 인용도 매체별 독립 보도로 세지 않습니다.
        if not origins and re.search(r"외신.{0,30}(따르면|인용|보도)|foreign media|wire service", body, re.I):
            raise ValueError("외신 원출처 미확인")
        check.update(status="inspected", publisher=publisher, origins=origins,
                     resolvedUrl=url, contentHash=hashlib.sha256(normalize(body).encode()).hexdigest(),
                     textSignature=text_signature(body))
    except Exception:
        pass
    return {**article, "sourceCheck": check}


def inspect_articles(articles: list[dict], now: datetime) -> list[dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(lambda a: inspect_article(a, now), articles))


def require_corroboration(articles: list[dict], now: datetime) -> dict:
    """48시간 이내·24시간 집중 보도·중복 제거 후 3개 출처군을 모두 요구합니다."""
    if len(articles) < MIN_REPORTS or len({a["url"] for a in articles}) != len(articles):
        raise ValueError("서로 다른 기사 3건 이상 필요")
    times = [parsed_time(a.get("publishedAt")) for a in articles]
    if any(t is None or not timedelta(0) <= now - t <= timedelta(hours=MAX_AGE_HOURS) for t in times):
        raise ValueError("최근 48시간 기사 부족")
    span = (max(times) - min(times)).total_seconds() / 3600
    if span > MAX_SPAN_HOURS or now - max(times) > timedelta(hours=24):
        raise ValueError("24시간 내 동시 보도 근거 부족")
    checks = [a.get("sourceCheck", {}) for a in articles]
    if any(c.get("version") != POLICY_VERSION or c.get("status") != "inspected"
           or not c.get("publisher") or not c.get("contentHash")
           or not parsed_time(c.get("checkedAt"))
           or not timedelta(0) <= now - parsed_time(c["checkedAt"]) <= timedelta(hours=48) for c in checks):
        raise ValueError("원출처 확인 미완료")
    groups = list(range(len(articles)))
    def root(index):
        while groups[index] != index:
            index = groups[index]
        return index
    for i, left in enumerate(articles):
        for j in range(i):
            right, a, b = articles[j], checks[i], checks[j]
            if (normalize(left["source"]) == normalize(right["source"])
                or publisher_family(a["publisher"]) == publisher_family(b["publisher"])
                or set(a.get("origins", [])) & set(b.get("origins", []))
                or a["contentHash"] == b["contentHash"] or similar_body(a, b) or similar_headline(left, right)):
                groups[root(i)] = root(j)
    count = len({root(i) for i in range(len(articles))})
    if count < MIN_REPORTS:
        raise ValueError(f"전재·유사기사 통합 후 출처군 부족: {count}개")
    return {"policyVersion": POLICY_VERSION, "articleCount": len(articles),
            "sourceGroups": count, "spanHours": round(span, 1), "checkedAt": now.isoformat()}
