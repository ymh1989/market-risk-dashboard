"""기존 가중점수와 분리한 조달·실질금리 관찰과 유럽 은행주 보조 판독."""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


FRED_SOURCES = {
    "real10y": ("DFII10", "미국 10년 실질금리"),
    "breakeven10y": ("T10YIE", "미국 10년 기대인플레이션"),
    "nominal10y": ("DGS10", "미국 10년 명목금리"),
    "sofr": ("SOFR", "미국 국채담보 익일 조달금리"),
    "iorb": ("IORB", "연준 준비금 이자율"),
    "sofr99": ("SOFR99", "미국 국채담보 조달금리 99분위"),
}
BANK_SOURCES = {
    "bnp": ("BNP.PA", "BNP파리바"),
    "socgen": ("GLE.PA", "소시에테제네랄"),
    "creditagricole": ("ACA.PA", "크레디아그리콜"),
    "euro50": ("EXW1.DE", "EURO STOXX 50 추종 ETF"),
}
WATCH_IDS = {"domestic_corporate_funding_watch", "us_real_yield_watch", "usd_short_funding_watch"}
MIN_HISTORY = 120
NORMALIZATION = "과거방향 · 최소 120개 관측 · 수준 55%(최대 504개) + 20일 양의 확대폭 45%(최대 252개) · 분위수 40%·z 30%·robust z 30%"


def clean_series(rows: list[dict], end: str | None = None) -> list[dict]:
    """유효 관측만 날짜순으로 중복 제거하며 결측을 전일값으로 채우지 않습니다."""
    valid = {}
    for row in rows:
        day, value = row.get("date"), row.get("close")
        try:
            datetime.strptime(day, "%Y-%m-%d")
            if isinstance(value, bool) or not math.isfinite(float(value)) or (end and day > end):
                continue
        except (TypeError, ValueError):
            continue
        valid[day] = {"date": day, "close": float(value)}
    return [valid[day] for day in sorted(valid)]


def aligned(*series: list[dict]) -> list[tuple]:
    """모든 구성 원천의 공통 관측일만 결합합니다."""
    maps = [{row["date"]: row["close"] for row in clean_series(rows)} for rows in series]
    if not maps:
        return []
    return [(day, *[mapping[day] for mapping in maps]) for day in sorted(set.intersection(*map(set, maps)))]


def difference(first: list[dict], second: list[dict], multiplier: float = 100) -> list[dict]:
    """같은 날짜 금리차를 bp로 변환합니다. 음수도 유효합니다."""
    return [{"date": day, "close": (a - b) * multiplier} for day, a, b in aligned(first, second)]


def rolling_mean(rows: list[dict], window: int = 5) -> list[dict]:
    """당일까지의 공통 관측만 사용한 이동평균입니다."""
    return [{"date": row["date"], "close": sum(p["close"] for p in rows[i-window+1:i+1]) / window}
            for i, row in enumerate(rows) if i >= window - 1]


def chart(label: str, unit: str, rows: list[dict]) -> dict:
    """원값과 점수의 단위를 구분하는 차트 정의를 만듭니다."""
    return {"label": label, "unit": unit, "points": clean_series(rows)}


def joint_scores(risk: Any, *series: list[dict]) -> list[dict]:
    """120개 관측 이전에는 점수를 내지 않고 모든 구성점수를 같은 날짜로 결합합니다."""
    if not series or min(map(len, series)) < MIN_HISTORY:
        return []
    components = [risk.level_and_point_change_component_points(rows) for rows in series]
    maps = [{p["date"]: p["value"] for p in rows} for rows in components]
    eligible_start = max(rows[MIN_HISTORY - 1]["date"] for rows in series)
    return [{"date": day, "value": round(sum(m[day] for m in maps) / len(maps), 1)}
            for day in sorted(set.intersection(*map(set, maps))) if day >= eligible_start]


def card(identifier: str, name: str, category: str, points: list[dict], charts: list[dict],
         notes: list[str], sources: list[dict], normalization: str = NORMALIZATION) -> dict:
    """원자료 관찰카드는 점수 없음(null)을 그대로 보존합니다."""
    latest = points[-1] if points else None
    previous = points[max(0, len(points) - 21)] if points else None
    delta = latest["value"] - previous["value"] if latest else 0
    dates = [c["points"][-1]["date"] for c in charts if c["points"]]
    return {
        "id": identifier, "name": name, "category": category, "group": "macro",
        "role": "observation", "weight": 0.0, "value": latest["value"] if latest else None,
        "unit": "score", "asOf": latest["date"] if latest else min(dates, default=None),
        "trend": "up" if delta > 3 else "down" if delta < -3 else "flat",
        "detail": notes, "source": " · ".join(dict.fromkeys(s["provider"] for s in sources)),
        "observationCharts": charts, "sourceRecords": sources,
        "normalizationNote": normalization, "scoreSeries": points,
    }


def domestic_card(risk: Any, payload: dict) -> dict:
    """잔고 기준일과 별개로, 실제 금리가 있는 날에만 기업 조달금리차를 산출합니다."""
    rows = payload.get("series") or []
    def rates(key):
        return clean_series([{"date": row.get("date"), "close": (row.get("ratesPct") or {}).get(key)} for row in rows])
    cp_cd = difference(rates("cp91d"), rates("cd91d"))
    aa_ktb = difference(rates("corporateAa3y"), rates("treasury3y"))
    common = aligned(cp_cd, aa_ktb)
    cp_cd = [{"date": d, "close": a} for d, a, _ in common]
    aa_ktb = [{"date": d, "close": b} for d, _, b in common]
    sources = [{"provider": "KB Securities OpenAPI", "symbol": "IVA10370",
                "label": "CP·CD·회사채 AA급·국고채", "lastDate": common[-1][0] if common else None,
                "observations": len(common), "retrievedAt": next((r.get("retrievedAt") for r in reversed(rows)
                    if common and r.get("date") == common[-1][0]), None),
                "valueType": "eod", "fetchStatus": "stored"}]
    notes = ["기업 조달여건 · 주식 신용잔고·고객예탁금과 별개", "금리차 확대: 기업 조달부담 증가 · 축소: 상대 부담 완화",
             "CP-CD와 AA급 회사채-국고채 점수 각각 50% · 종합점수 미반영"]
    if len(common) < MIN_HISTORY:
        notes.append(f"금리 이력 {len(common)}개 · 원금리차 우선 표시 · 점수는 최소 {MIN_HISTORY}개부터")
    return card("domestic_corporate_funding_watch", "국내 기업 조달경색", "관찰/기업 자금조달",
                joint_scores(risk, cp_cd, aa_ktb), [chart("CP 91일 - CD 91일", "bp", cp_cd),
                chart("회사채 AA급 3년 - 국고채 3년", "bp", aa_ktb)], notes, sources)


def real_yield_card(risk: Any, series: dict, sources: dict) -> dict:
    """실질금리를 점수화하고 같은 날짜 명목금리·기대인플레이션은 보조 판독합니다."""
    keys = ("real10y", "breakeven10y", "nominal10y")
    common = aligned(*(series.get(key, []) for key in keys))
    rows = [[{"date": r[0], "close": r[i+1]} for r in common] for i in range(3)]
    notes = ["실질금리 상승: 성장주 할인율 부담 · 하락: 할인율 부담 완화",
             "기대인플레이션은 보조값 · 위험·유동성 프리미엄 포함", "명목금리 하락만으로 경기·신용 위험 해소를 판단하지 않음"]
    return card("us_real_yield_watch", "미국 실질금리·할인율 부담", "관찰/금리 분해",
                joint_scores(risk, rows[0]), [chart(FRED_SOURCES[k][1], "%", r) for k, r in zip(keys, rows)],
                notes, [sources[k] for k in keys if k in sources])


def dollar_funding_card(risk: Any, series: dict, sources: dict) -> dict:
    """정책금리 대비 스프레드와 거래금리 상단을 5일 평균해 월말 하루 급등을 완충합니다."""
    keys = ("sofr", "iorb", "sofr99")
    common = aligned(*(series.get(key, []) for key in keys))
    policy = [{"date": d, "close": (s - i) * 100} for d, s, i, _ in common]
    tail = [{"date": d, "close": (p - s) * 100} for d, s, _, p in common]
    smoothed = [rolling_mean(rows) for rows in (policy, tail)]
    notes = ["SOFR: 국채담보 익일 조달금리 · IORB: 연준 준비금 이자율",
             "SOFR-IORB 확대·거래금리 상단 상승이 함께 지속되면 단기조달 부담",
             "5일 평균 후 각각 50% · 월말·분기말 기술적 상승 가능 · 은행 CDS와 별개"]
    return card("usd_short_funding_watch", "미국 단기 달러조달 압력", "관찰/단기 자금시장",
                joint_scores(risk, *smoothed), [chart("SOFR - IORB", "bp", policy),
                chart("SOFR 99분위 - SOFR", "bp", tail)], notes,
                [sources[k] for k in keys if k in sources], "5일 후행 평균 후 각 구성 50% · " + NORMALIZATION)


def european_bank_context(series: dict, sources: dict) -> dict:
    """은행 3개를 매일 동일가중한 조정가격 수익률과 EUR ETF를 공통 거래일로 비교합니다."""
    keys = tuple(BANK_SOURCES)
    common = aligned(*(series.get(key, []) for key in keys))
    bank_index = benchmark_index = 100.0
    bank_rows, benchmark_rows, relative_rows = [], [], []
    for i, row in enumerate(common):
        if i:
            previous = common[i-1]
            bank_index *= sum(row[j] / previous[j] for j in (1, 2, 3)) / 3
            benchmark_index *= row[4] / previous[4]
        bank_rows.append({"date": row[0], "close": bank_index})
        benchmark_rows.append({"date": row[0], "close": benchmark_index})
        relative_rows.append({"date": row[0], "close": bank_index / benchmark_index * 100})
    changes = {}
    for offset in (5, 20):
        if len(bank_rows) > offset:
            bank_return = (bank_rows[-1]["close"] / bank_rows[-1-offset]["close"] - 1) * 100
            benchmark_return = (benchmark_rows[-1]["close"] / benchmark_rows[-1-offset]["close"] - 1) * 100
            changes[f"relativeReturn{offset}dPp"] = round(bank_return - benchmark_return, 2)
    return {"observationCharts": [chart("프랑스 은행 / 유럽 대표주 상대지수", "index", relative_rows)],
            "sourceRecords": [sources[k] for k in keys if k in sources],
            "metrics": changes, "notes": ["은행 3개 매일 동일가중 / EURO STOXX 50 추종 ETF(EXW1.DE)",
                "유로화 조정가격 · 상대지수 하락: 은행주 상대약세 · 은행 CDS·인과관계와 별개",
                "기존 국채금리차 점수는 그대로 · 은행주 흐름은 점수 미반영"]}


def fetch_bank_series(risk: Any, symbol: str, now: datetime) -> list[dict]:
    """배당·분할 조정 종가만 사용하며 유럽 장 마감 전 당일 봉은 제외합니다."""
    errors = []
    for template in risk.YAHOO_CHART_URLS:
        url = template.format(symbol=urllib.parse.quote(symbol, safe=""), range_value="5y")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": risk.USER_AGENT})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            result = payload["chart"]["result"][0]
            if result["meta"].get("currency") != "EUR":
                raise ValueError("유로화 원천이 아닙니다")
            values = result["indicators"]["adjclose"][0]["adjclose"]
            local = now.astimezone(ZoneInfo("Europe/Paris"))
            end = local.date() if (local.hour, local.minute) >= (18, 0) else local.date() - timedelta(days=1)
            rows = clean_series([{"date": datetime.fromtimestamp(t, timezone.utc).date().isoformat(), "close": v}
                                 for t, v in zip(result["timestamp"], values)], end.isoformat())
            if len(rows) < 120 or any(row["close"] <= 0 for row in rows):
                raise ValueError("유효 조정종가 이력이 부족합니다")
            return rows
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            errors.append(type(error).__name__)
    raise RuntimeError(f"{symbol} 조정가격 조회 실패: {', '.join(errors)}")


def fetch_sources(risk: Any, previous: dict, now: datetime) -> dict:
    """관찰 원천 실패는 원천별 상태로 남겨 기존 핵심 배치를 중단하지 않습니다."""
    configs = {**{k: ("FRED", *v) for k, v in FRED_SOURCES.items()},
               **{k: ("Yahoo Finance", *v) for k, v in BANK_SOURCES.items()}}
    stamp = now.isoformat()
    def fetch(config):
        key, (provider, symbol, label) = config
        old = previous.get(key) or {}
        try:
            rows = (risk.fetch_fred_series(symbol, lookback_days=1600) if provider == "FRED"
                    else fetch_bank_series(risk, symbol, now))
            rows = clean_series(rows, now.date().isoformat())
            if len(rows) < MIN_HISTORY:
                raise ValueError("원천 유효 관측 부족")
            if old.get("rows") and rows[-1]["date"] < old["rows"][-1]["date"]:
                raise ValueError("새 응답이 저장 원장보다 오래됨")
            record = {"provider": provider, "symbol": symbol, "label": label,
                      "url": f"https://fred.stlouisfed.org/series/{symbol}" if provider == "FRED"
                      else f"https://finance.yahoo.com/quote/{symbol}/history/",
                      "retrievedAt": stamp, "lastAttemptAt": stamp, "fetchStatus": "direct",
                      "lastDate": rows[-1]["date"], "observations": len(rows), "valueType": "eod"}
            return {"rows": rows, "source": record}
        except Exception as error:
            record = {**(old.get("source") or {}), "provider": provider, "symbol": symbol, "label": label,
                      "lastAttemptAt": stamp, "fetchStatus": "cached" if old.get("rows") else "unavailable",
                      "warning": f"원천 조회 실패 ({type(error).__name__})"}
            print(f"관찰 원천 {symbol}: {record['fetchStatus']} · {record['warning']}", flush=True)
            return {"rows": clean_series(old.get("rows") or [], now.date().isoformat()), "source": record}
    return risk.fetch_configured_series({k: (k, v) for k, v in configs.items()}, fetch, max_workers=3)


def refresh_market_watch(risk: Any, fetch: bool = True, now: datetime | None = None) -> dict:
    """기존 가중카드를 변경하지 않고 관찰카드와 원자료 그래프만 증분 갱신합니다."""
    now = now or datetime.now(risk.KST)
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    cache = read(risk.HISTORY_CACHE_FILE)
    records = cache.get("marketWatchSources") or {}
    if fetch:
        records = fetch_sources(risk, records, now)
    series = {key: clean_series(item.get("rows") or [], now.date().isoformat()) for key, item in records.items()}
    sources = {key: item["source"] for key, item in records.items()}
    domestic = domestic_card(risk, read(risk.KB_MARKET_FUNDS_FILE))
    cards = [domestic, real_yield_card(risk, series, sources), dollar_funding_card(risk, series, sources)]
    for item in cards:
        degraded = [s["symbol"] for s in item["sourceRecords"] if s.get("fetchStatus") in ("cached", "unavailable")]
        dates = [s.get("lastDate") for s in item["sourceRecords"] if s.get("lastDate")]
        stale = dates and (now.date() - datetime.strptime(min(dates), "%Y-%m-%d").date()).days > 7
        if degraded:
            item["detail"].append("원천 조회 실패 · 보관값/미수집: " + ", ".join(degraded))
        if stale:
            item["detail"].append("관측 지연 · 원천 기준일 확인 필요")
    dashboard = read(risk.DASHBOARD_FILE)
    snapshot = read(risk.SNAPSHOT_FILE)
    timeseries = read(risk.TIMESERIES_FILE)
    market = next(section for section in dashboard["sections"] if section["id"] == "market")
    indicators = [item for item in market["indicators"] if item["id"] not in WATCH_IDS]
    for item in cards:
        if item["value"] is not None:
            indicators.append({k: v for k, v in item.items() if k != "scoreSeries"})
            timeseries["series"][item["id"]] = item["scoreSeries"]
        else:
            timeseries["series"].pop(item["id"], None)
    market["rawObservationCards"] = [{k: v for k, v in item.items() if k != "scoreSeries"}
                                     for item in cards if item["value"] is None]
    european = european_bank_context(series, sources)
    for item in indicators:
        if item["id"] == "france_germany_spread_watch":
            item["observationCharts"] = european["observationCharts"]
            item["supplementalSources"] = european["sourceRecords"]
            item["supplementalNotes"] = european["notes"]
            for offset in (5, 20):
                item["metrics"].pop(f"relativeReturn{offset}dPp", None)
            item["metrics"].update(european["metrics"])
            item["supplementalMetrics"] = [
                {"label": f"은행주 {n}일 상대수익률", "value": european["metrics"].get(f"relativeReturn{n}dPp"), "unit": "%p"}
                for n in (5, 20)]
    enriched, groups = risk.enrich_indicators(indicators)
    for group in groups:
        group["observationCount"] += sum(item["group"] == group["id"] for item in market["rawObservationCards"])
    market["indicators"], market["groupScores"] = enriched, groups
    snapshot["indicators"], snapshot["groupScores"] = enriched, groups
    snapshot["marketWatchSources"] = sources
    stamp = now.strftime("%Y-%m-%d %H:%M:%S KST")
    cache["marketWatchSources"] = records
    cache["generatedAt"] = stamp
    dashboard["metadata"]["generatedAt"] = stamp
    snapshot["generatedAt"] = timeseries["generatedAt"] = stamp
    for path, payload in ((risk.HISTORY_CACHE_FILE, cache), (risk.DASHBOARD_FILE, dashboard),
                          (risk.SNAPSHOT_FILE, snapshot), (risk.TIMESERIES_FILE, timeseries)):
        path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                   indent=None if path == risk.HISTORY_CACHE_FILE else 2) + "\n", encoding="utf-8")
    return {"cards": [(item["id"], item["value"], item["asOf"]) for item in cards],
            "europe": european["metrics"]}
