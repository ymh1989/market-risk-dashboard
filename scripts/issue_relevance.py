"""기사 제목의 주제 적합성을 보수적으로 점검합니다. 위험 수준은 판정하지 않습니다."""

from __future__ import annotations

import re


def matches(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def exclusion_reason(theme: str, title: str, source: str = "") -> str | None:
    """제목만으로 연결하기 어려운 기사에 고정된 제외 사유를 부여합니다."""
    if matches(r"목표주가|추천주|매수 추천|축구|운세|골프대회", title):
        return "홍보·종목추천·비시장 기사"
    if matches(r"브런치|티스토리|개인 블로그", source) or matches(r"돌파하면|상승한다면|하락한다면|가정하면", title):
        return "개인 의견·가정 시나리오"
    if theme == "dollar":
        if not matches(r"미국|美|달러|연준|Fed|SOFR|IORB|미 국채|US\b|U\.S\.", title):
            return "달러시장 연결 근거 부족"
        if not matches(r"자금시장|단기금융|레포|SOFR|IORB|하이일드|신용.?스프레드|유동성|차환|디폴트|부도|파산|신용위험|신용 위험|신용경색|신용 경색|신용등급.{0,10}(강등|하향|상향|개선|악화)|상환|담보|조달.{0,10}(비용|금리|어려|곤란|부담)|발행.{0,6}(실패|취소|연기)|CDS|funding stress|credit spread|default|refinanc", title):
            return "일반 자금조달·신용위험 근거 부족"
    elif theme == "energy":
        if not matches(r"유가|원유|석유|에너지|가스|유조선|운임|해운|호르무즈|Brent|WTI|oil|LNG", title):
            return "에너지·운송 연결 근거 부족"
    elif theme == "semiconductors":
        if not matches(r"반도체|메모리|DRAM|HBM|삼성전자|하이닉스|엔비디아|Nvidia|TSMC|데이터센터|semiconductor", title):
            return "반도체 연결 근거 부족"
    elif theme == "rates":
        if not matches(r"금리|국채|인플레|물가|연준|Fed|수익률|yield|inflation", title):
            return "금리 연결 근거 부족"
        foreign = matches(r"프랑스|독일|일본|유럽|유로|ECB|佛|日|英|영국", title)
        domestic = matches(r"미국|美|연준|Fed|한국|국내|국고|한은|금통위|미 국채", title)
        if foreign and not domestic:
            return "현재 미국·한국 점검지표와 국가 불일치"
    elif theme == "europe":
        if not matches(r"프랑스|France|佛|유럽|유로|ECB|독일", title):
            return "유럽 연결 근거 부족"
    return None
