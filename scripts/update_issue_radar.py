"""공개 뉴스와 기존 시장지표를 연결한 관찰 전용 이슈 레이더."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import json
import math
from pathlib import Path
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def timestamp(value: str | None) -> datetime | None:
    """날짜가 없는 기사를 현재 뉴스로 둔갑시키지 않습니다."""
    try:
        parsed = datetime.fromisoformat(str(value).replace(" KST", "+09:00"))
        return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)
    except ValueError:
        return None


def public_url(value: str) -> bool:
    """표시 링크는 공개 HTTPS만 허용합니다."""
    try:
        parsed = urllib.parse.urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username
                    and not parsed.password and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    except ValueError:
        return False


def plain_text(value: str) -> str:
    """RSS 제목의 마크업과 제어문자를 제거합니다."""
    return " ".join(re.sub(r"<[^>]*>", "", html.unescape(value)).split())[:280]


def clean_articles(articles: list[dict], now: datetime) -> list[dict]:
    """최근 공개 기사만 남기고 동일 제목·URL을 중복 제거합니다."""
    seen_titles, seen_urls, output = set(), set(), []
    for item in sorted(articles, key=lambda row: timestamp(row.get("publishedAt")) or datetime.min.replace(tzinfo=KST), reverse=True):
        published = timestamp(item.get("publishedAt"))
        title, url = plain_text(str(item.get("title") or "")), str(item.get("url") or "")
        if not published or not now - timedelta(days=30) <= published <= now or not title or not public_url(url):
            continue
        key = re.sub(r"[\W_]", "", title.rsplit(" - ", 1)[0].casefold())
        if key in seen_titles or url in seen_urls:
            continue
        seen_titles.add(key)
        seen_urls.add(url)
        output.append({"title": title, "url": url, "source": plain_text(str(item.get("source") or "뉴스")),
                       "publishedAt": published.isoformat()})
    return output[:60]


def fetch_articles(definition: dict, now: datetime) -> list[dict]:
    """공개 RSS의 제목·링크·발행시각만 수집하며 사내 브리핑은 읽지 않습니다."""
    query = urllib.parse.urlencode({"q": f'{definition["query"]} when:7d', "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    request = urllib.request.Request("https://news.google.com/rss/search?" + query,
                                     headers={"User-Agent": "Mozilla/5.0 (market-risk-dashboard)"})
    with urllib.request.urlopen(request, timeout=10) as response:
        root = ET.fromstring(response.read(2_000_000))
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("뉴스 응답이 RSS 형식이 아닙니다")
    articles = []
    for item in root.findall("./channel/item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", ""))
            if published.tzinfo is None:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        articles.append({"title": item.findtext("title", ""), "url": item.findtext("link", ""),
                         "source": item.findtext("source", "뉴스"), "publishedAt": published.isoformat()})
    return clean_articles(articles, now)


def news_for_issue(definition: dict, previous: dict, now: datetime, fetcher=fetch_articles, offline=False) -> dict:
    """한 시간 캐시와 조회 실패를 구분하고 마지막 성공 시각을 보존합니다."""
    old = previous.get("news") or {}
    retrieved = timestamp(old.get("retrievedAt"))
    cached = clean_articles(old.get("articles") or [], now)
    state, articles = "unavailable", cached
    if offline:
        state = "stored" if retrieved else "unavailable"
    elif (retrieved and timedelta(0) <= now - retrieved < timedelta(hours=1)
          and old.get("status") != "failed" and old.get("query") == definition["query"]):
        state = "cached"
    else:
        try:
            articles = clean_articles([*fetcher(definition, now), *cached], now)
            retrieved, state = now, "direct"
        except Exception:
            # 실패 예외에 URL·인증정보가 섞일 수 있어 공개 데이터에는 고정 상태만 저장합니다.
            state = "failed"
    recent = [item for item in articles if timestamp(item["publishedAt"]) >= now - timedelta(days=7)]
    count_day = sum(timestamp(item["publishedAt"]) >= now - timedelta(days=1) for item in recent)
    old_urls = {item["url"] for item in cached}
    fresh_count = sum(item["url"] not in old_urls for item in recent)
    label = ("조회 실패" if state == "failed" else "미수집" if not retrieved else
             "최근 기사 없음" if not recent else "새 기사" if fresh_count else "후속 관찰")
    return {"status": state, "label": label, "retrievedAt": retrieved.isoformat() if retrieved else None,
            "attemptedAt": now.isoformat(), "articles": articles, "count7d": len(recent), "count24h": count_day,
            "newCount": fresh_count, "provider": "Google News 공개 RSS", "query": definition["query"]}


def clean_points(rows: list[dict], now: datetime, key="close", scale=1.0) -> list[dict]:
    """미래·결측·무한값을 제외하고 날짜를 정렬합니다. 결측을 보간하지 않습니다."""
    output = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("date")))
            value = row.get(key)
            if day > now.date() or value is None or isinstance(value, bool) or not math.isfinite(float(value)):
                continue
            output[day.isoformat()] = {"date": day.isoformat(), "close": round(float(value) * scale, 6)}
        except (ValueError, TypeError):
            continue
    return [output[key] for key in sorted(output)]


def ratio_points(numerator: list[dict], denominator: list[dict], now: datetime) -> list[dict]:
    """동일 관측일 가격의 비율을 최초 공통일 100으로 표시합니다."""
    second = {row["date"]: row["close"] for row in clean_points(denominator, now)}
    ratios = [{"date": row["date"], "close": row["close"] / second[row["date"]]}
              for row in clean_points(numerator, now) if row["date"] in second and second[row["date"]] > 0 and row["close"] > 0]
    if not ratios:
        return []
    base = ratios[0]["close"]
    return [{"date": row["date"], "close": round(row["close"] / base * 100, 6)} for row in ratios]


def business_lag(day: str, now: datetime) -> int:
    """글로벌 공통 평일 기준으로 지연 여부를 보수적으로 표시합니다."""
    start = date.fromisoformat(day)
    return sum((start + timedelta(days=i)).weekday() < 5 for i in range(1, (now.date() - start).days + 1))


def metric_catalog(data: dict, market: dict, now: datetime) -> dict:
    """기존 공개 산출물에서 원자료만 가져오며 운영 점수에는 쓰지 않습니다."""
    catalog = {}
    naver = data["naver"].get("series") or {}
    history = data["history"]
    cards = {item["id"]: item for item in market.get("indicators", [])}

    def add(key, label, unit, rows, source, sign, threshold, change="absolute"):
        catalog[key] = {"id": key, "label": label, "unit": unit, "points": clean_points(rows, now),
                        "source": source, "riskSign": sign, "threshold": threshold, "changeMode": change}

    for key, field, label, unit, sign, threshold, change, scale in [
        ("fr_spread", "fr_de_10y_spread", "프랑스−독일 10년", "bp", 1, 5, "absolute", 100),
        ("fr_yield", "fr10y_naver", "프랑스 10년 금리", "%", 1, 5, "yield", 1),
        ("kr_yield", "kr10y", "한국 10년 금리", "%", 1, 5, "yield", 1),
        ("brent", "brent", "브렌트유", "달러", 1, 2, "percent", 1),
        ("usdkrw", "usdkrw_naver", "원/달러", "원", 1, 1, "percent", 1),
        ("bdti", "bdti", "유조선 운임", "index", 1, 5, "percent", 1),
    ]:
        add(key, label, unit, clean_points(naver.get(field, []), now, scale=scale), "네이버 시장지표 · EOD", sign, threshold, change)
    for key, card_id, index, sign, threshold, mode in [
        ("fr_banks", "france_germany_spread_watch", 0, -1, 2, "percent"),
        ("us_real", "us_real_yield_watch", 0, 1, 5, "yield"),
        ("us_breakeven", "us_real_yield_watch", 1, 1, 5, "yield"),
        ("sofr_iorb", "usd_short_funding_watch", 0, 1, 3, "absolute"),
        ("sofr_tail", "usd_short_funding_watch", 1, 1, 3, "absolute"),
    ]:
        charts = cards.get(card_id, {}).get("observationCharts") or []
        item = charts[index] if len(charts) > index else {}
        add(key, item.get("label", key), item.get("unit", "%"), item.get("points", []),
            "Yahoo 조정가격 · 공통일" if key == "fr_banks" else "FRED · EOD", sign, threshold, mode)
    add("hy_oas", "미국 하이일드 OAS", "bp", clean_points((history.get("fred") or {}).get("us_high_yield_oas", []), now, scale=100), "FRED BAMLH0A0HYM2 · EOD", 1, 10)
    breadth = data["breadth"].get("series") or []
    add("foreign_flow", "외국인 5일 순매수", "억원", clean_points(breadth, now, "foreignNetBuy5dEok"), "KRX · 시장 내부강도", -1, 10000)
    add("breadth", "시장 확산도 5일 평균", "%", clean_points(breadth, now, "breadthMa5Pct"), "KRX · 시장 내부강도", -1, 5)
    funds = [{"date": row.get("date"), "close": (row.get("derived") or {}).get("creditToDepositsPct")}
             for row in data["funds"].get("series", [])]
    add("credit_deposit", "신용잔고/고객예탁금", "%", funds, "금융투자협회 FreeSIS · 확정일", 1, 1)
    yahoo = history.get("yahoo") or {}
    add("sox_relative", "SOX / S&P 500", "index", ratio_points(yahoo.get("sox", []), yahoo.get("spx", []), now), "Yahoo · 최초 공통일=100", -1, 2, "percent")
    add("korea_semi_relative", "삼성전자 / KOSPI", "index", ratio_points(yahoo.get("samsung", []), yahoo.get("kospi", []), now), "Yahoo · 최초 공통일=100", -1, 2, "percent")
    dram = data["dram"]
    prices = {row["date"]: row for row in [*dram.get("priceHistory", []), *dram.get("history", [])]}
    add("dram_ddr5", "DDR5 16Gb 현물", "달러", [{"date": key, "close": (row.get("pricesUsd") or {}).get("DDR5_16Gb")} for key, row in prices.items()],
        "TrendForce 공식 최신값 · 보관 가격 이력", 0, 0, "percent")
    return catalog


def difference(metric: dict, current: float, previous: float) -> float | None:
    """금리 bp·비율 %p·가격 수익률을 구분합니다."""
    mode = metric["changeMode"]
    value = (current - previous) * 100 if mode == "yield" else ((current / previous - 1) * 100 if previous else None) if mode == "percent" else current - previous
    return round(value, 3) if value is not None else None


def summarize_metric(metric: dict, now: datetime, anchor: str | None) -> dict:
    item = dict(metric)
    points = item["points"]
    item.update(value=points[-1]["close"] if points else None, asOf=points[-1]["date"] if points else None,
                status="missing" if not points else "stale" if business_lag(points[-1]["date"], now) > 5 else "ok",
                deltaUnit="bp" if item["changeMode"] == "yield" else "%" if item["changeMode"] == "percent" else "%p" if item["unit"] == "%" else item["unit"])
    for name, offset in (("change1d", 1), ("change1w", 5)):
        previous = points[-1-offset] if len(points) > offset else None
        item[name] = difference(item, points[-1]["close"], previous["close"]) if previous else None
        item[name + "Date"] = previous["date"] if previous else None
    before = [row for row in points if anchor and row["date"] < anchor]
    base = before[-1] if before and points[-1]["date"] >= anchor and (date.fromisoformat(anchor) - date.fromisoformat(before[-1]["date"])).days <= 10 else None
    item["anchorBaseDate"] = base["date"] if base else None
    item["changeSinceAnchor"] = difference(item, points[-1]["close"], base["close"]) if base else None
    # 표시 이력은 최대 3년, 변화 계산은 보유 원자료 전체에서 수행합니다.
    cutoff = (now.date() - timedelta(days=1100)).isoformat()
    item["points"] = [row for row in points if row["date"] >= cutoff]
    return item


def market_reading(definition: dict, metrics: list[dict], score_series: dict, now: datetime) -> dict:
    """수준은 기존 연결카드 최대값, 방향은 원자료의 5개 관측 변화로 분리합니다."""
    components = []
    for item_id in definition["scoreIds"]:
        rows = clean_points(score_series.get(item_id, []), now, "value")
        if rows and business_lag(rows[-1]["date"], now) <= 5:
            components.append({"id": item_id, "date": rows[-1]["date"], "value": rows[-1]["close"]})
    high = max((item["value"] for item in components), default=None)
    severity = "미확인" if high is None else "경계" if high >= 75 else "주의" if high >= 55 else "보통"
    usable = [item for item in metrics if item["riskSign"] and item["status"] == "ok" and item["change1w"] is not None]
    up = sum(item["change1w"] * item["riskSign"] >= item["threshold"] for item in usable)
    down = sum(item["change1w"] * item["riskSign"] <= -item["threshold"] for item in usable)
    direction = "판독 대기" if len(usable) < 2 else "악화" if up >= 2 and down == 0 else "완화" if down >= 2 and up == 0 else "혼조" if up or down else "뚜렷한 변화 없음"
    return {"level": severity, "levelScore": high, "scoreComponents": components, "direction": direction,
            "confirmed": up, "improving": down, "available": len(usable),
            "asOf": min((item["date"] for item in components), default=None),
            "partial": len(usable) < sum(bool(item["riskSign"]) for item in metrics)}


def build_radar(data: dict, definitions: list[dict], now: datetime, previous=None, fetcher=fetch_articles, offline=False) -> dict:
    """승인된 이슈만 노출하며 점수 산출물은 읽기만 합니다."""
    market = next(section for section in data["dashboard"]["sections"] if section["id"] == "market")
    old = {item["id"]: item for item in (previous or {}).get("issues", [])}
    definitions = [item for item in definitions if item.get("approved") is True]
    with ThreadPoolExecutor(max_workers=3) as executor:
        news = list(executor.map(lambda item: news_for_issue(item, old.get(item["id"], {}), now, fetcher, offline), definitions))
    catalog = metric_catalog(data, market, now)
    identifiers = [item["id"] for item in definitions]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("이슈 설정에 중복 ID가 있습니다")
    card_names = {item["id"]: item.get("name", item["id"]) for item in market.get("indicators", [])}
    issues = []
    for definition, coverage in zip(definitions, news):
        articles = coverage["articles"]
        anchor = min((timestamp(item["publishedAt"]).date().isoformat() for item in articles), default=None)
        metrics = [summarize_metric(catalog[key], now, anchor) for key in definition["metricIds"]]
        reading = market_reading(definition, metrics, data["timeseries"].get("series", {}), now)
        for component in reading["scoreComponents"]:
            component["label"] = card_names.get(component["id"], component["id"])
        issues.append({"id": definition["id"], "title": definition["title"], "route": definition["route"],
                       "checkpoints": definition["checkpoints"], "news": coverage, "metrics": metrics,
                       "reading": reading, "anchorDate": anchor, "anchorLabel": "보관 기사 첫 보도",
                       "weight": 0, "events": definition.get("events", [])})
    issues.sort(key=lambda item: (item["reading"]["direction"] == "악화", item["reading"]["levelScore"] or -1), reverse=True)
    return {"schemaVersion": 1, "generatedAt": now.isoformat(), "issues": issues,
            "methodology": ["위험 수준: 최신 연결카드 중 최고 점수(55 이상 주의·75 이상 경계), 종합점수 미반영",
                            "방향: 5개 관측 변화가 기준폭을 넘는 지표 2개 이상·반대 신호 없음. 2개 미만 수집 시 판독 대기",
                            "기준폭: 금리 5bp·국채차 5bp·단기조달차 3bp·HY 10bp·유가/상대주가 2%·환율 1%·운임 5%·확산도 5%p·신용/예탁금 1%p·수급 1조원",
                            "방향 기준은 관찰용 초기 규칙이며 예측성능 검증 전. DRAM 가격은 맥락 지표로 방향 판정에서 제외",
                            "뉴스 건수는 수집된 표본의 중복 제거 제목 수. 첫 보도는 보관 기사 기준이며 사건 발생일·인과관계를 뜻하지 않음",
                            "뉴스 제목 중 국채·금리·수급 등 시장 관련어를 포함한 보도를 우선 표시. 기사 내용의 사실 여부를 자동 검증한 것은 아님",
                            "5영업일(공통 평일 기준) 초과 원자료는 방향 판정 제외. 다음 일정은 승인된 일정만 표시"]}


def refresh(root: Path = ROOT, offline=False) -> dict:
    """기존 대시보드 안에 저장하여 원자적 게시·오프라인 내보내기를 그대로 따릅니다."""
    files = {"dashboard": "risk-dashboard.json", "naver": "naver-marketindex-history.json", "history": "market-history-cache.json",
             "breadth": "kospi-breadth.json", "funds": "kb-market-funds.json", "dram": "dram-spot-prices.json", "timeseries": "market-risk-timeseries.json"}
    data = {key: json.loads((root / "data" / path).read_text(encoding="utf-8")) for key, path in files.items()}
    definitions = json.loads((root / "config/market_issues.json").read_text(encoding="utf-8"))["issues"]
    market = next(section for section in data["dashboard"]["sections"] if section["id"] == "market")
    radar = build_radar(data, definitions, datetime.now(KST), previous=market.get("issueRadar"), offline=offline)
    market["issueRadar"] = radar
    path = root / "data/risk-dashboard.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data["dashboard"], ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return radar


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="공개 뉴스·시장 이슈 레이더 갱신")
    parser.add_argument("--offline", action="store_true", help="네트워크 조회 없이 보관 기사로 갱신")
    args = parser.parse_args()
    result = refresh(offline=args.offline)
    print("이슈 레이더: " + " · ".join(f'{item["title"]} {item["reading"]["level"]}/{item["reading"]["direction"]}' for item in result["issues"]))
