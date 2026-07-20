import json
import importlib.util
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "risk-dashboard.json"
TIMESERIES_FILE = ROOT / "data" / "market-risk-timeseries.json"
MARKET_INDEX_CACHE_FILE = ROOT / "data" / "naver-marketindex-history.json"
NEWS_DIGEST_SCRIPT = ROOT / "scripts" / "send_risk_news_digest.py"
BACKTEST_FILE = ROOT / "data" / "market-risk-backtest.json"
STRESS_FILE = ROOT / "data" / "market-stress-episodes.json"


def clamp_score(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return min(100, max(0, number))


def weighted_score(indicators):
    active = [indicator for indicator in indicators if float(indicator.get("weight", 0)) > 0]
    weight_total = sum(float(indicator["weight"]) for indicator in active)
    if not active or weight_total <= 0:
        return 0
    weighted_total = sum(clamp_score(indicator["value"]) * float(indicator["weight"]) for indicator in active)
    return round((weighted_total / weight_total) * 10) / 10


def pick_level(score, thresholds):
    safe_score = clamp_score(score)
    for threshold in thresholds:
        if safe_score >= threshold["min"] and safe_score < threshold["max"]:
            return threshold
    return thresholds[-1]


def load_news_digest_module():
    spec = importlib.util.spec_from_file_location("send_risk_news_digest", NEWS_DIGEST_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_news_title_clustering():
    news_digest = load_news_digest_module()
    first = news_digest.normalize_title_for_dedupe("KB증권, 디지털WM 자산 15조원 돌파")
    second = news_digest.normalize_title_for_dedupe("KB증권 디지털 WM 자산 15조 돌파…ISA·연금 성장 견인")
    unrelated = news_digest.normalize_title_for_dedupe("금감원, 불완전판매 검사 강화")

    assert news_digest.is_similar_title(first, second)
    assert not news_digest.is_similar_title(first, unrelated)


def test_news_korean_web_filter_blocks_ru_sources():
    news_digest = load_news_digest_module()

    assert news_digest.article_matches_language_filters(
        "증권사 유동성 리스크 확대",
        "example.ru",
        "https://example.ru/news/123",
        "https://news.google.com/rss/articles/example",
    ) is False
    assert news_digest.article_matches_language_filters(
        "증권사 유동성 리스크 확대",
        "국내경제신문",
        "https://example.co.kr/news/123",
        "https://news.google.com/rss/articles/example",
    ) is True


def test_dashboard_contract():
    dashboard = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    timeseries = json.loads(TIMESERIES_FILE.read_text(encoding="utf-8"))
    market_index_cache = json.loads(MARKET_INDEX_CACHE_FILE.read_text(encoding="utf-8"))
    backtest = json.loads(BACKTEST_FILE.read_text(encoding="utf-8"))
    stress = json.loads(STRESS_FILE.read_text(encoding="utf-8"))
    assert dashboard["metadata"]["title"] == "통합 리스크 모니터링 대시보드"
    assert any(section["id"] == "market" and section["status"] == "active" for section in dashboard["sections"])
    assert any(section["id"] == "credit" and section["status"] == "planned" for section in dashboard["sections"])
    assert any(section["id"] == "liquidity" and section["status"] == "planned" for section in dashboard["sections"])

    for section in dashboard["sections"]:
        assert len(section["model"]["thresholds"]) >= 4, f"{section['id']} should define risk thresholds"
        assert isinstance(section["indicators"], list), f"{section['id']} should expose indicators array"

    market = next(section for section in dashboard["sections"] if section["id"] == "market")
    market_score = weighted_score(market["indicators"])
    market_level = pick_level(market_score, market["model"]["thresholds"])
    market_weights = sum(float(indicator["weight"]) for indicator in market["indicators"])

    assert len(market["indicators"]) >= 12
    assert abs(market_weights - 1.0) < 0.001
    assert market["model"]["aggregation"] == "weightedAverage"
    assert market["model"]["normalization"]["zScoreMapping"] == "normalCDF"
    assert market["model"]["normalization"]["robustZScore"] == "median/MAD"
    assert len(market.get("groupScores", [])) >= 5
    assert market_level["label"] in {"정상", "관심", "주의", "경고"}
    assert len(sorted(market["indicators"], key=lambda indicator: indicator["value"], reverse=True)[:3]) == 3
    assert any(indicator["id"] == "global_ai_semiconductor_stress" for indicator in market["indicators"])
    assert any(indicator["id"] == "korea_ai_semiconductor_concentration" for indicator in market["indicators"])
    assert any(indicator["id"] == "foreign_ownership_pressure" for indicator in market["indicators"])
    assert any(indicator["id"] == "trading_activity_heat" for indicator in market["indicators"])
    assert any(indicator["id"] == "global_credit_proxy_stress" for indicator in market["indicators"])
    assert any(indicator["id"] == "shipping_cost_pressure" for indicator in market["indicators"])
    assert any(indicator["id"] == "china_demand_fx_stress" for indicator in market["indicators"])
    assert any(indicator["id"] == "energy_import_cost_pressure" for indicator in market["indicators"])
    observations = [indicator for indicator in market["indicators"] if indicator.get("role") == "observation"]
    assert {indicator["id"] for indicator in observations} == {
        "yen_carry_unwind_watch",
        "korea_us_rate_fx_watch",
    }
    assert all(float(indicator["weight"]) == 0 for indicator in observations)
    assert market_index_cache["schemaVersion"] == 1
    assert set(market_index_cache["series"]) == {
        "scfi",
        "bdti",
        "bdi",
        "iron_ore",
        "copper",
        "gold",
        "brent",
        "usdcny",
        "usdjpy",
        "us2y_naver",
        "us10y_naver",
        "kr3y",
        "kr10y",
    }
    assert all(len(points) >= 60 for points in market_index_cache["series"].values())

    for indicator in market["indicators"]:
        assert 0 <= indicator["value"] <= 100, f"{indicator['id']} score must be 0~100"
        assert indicator["group"], f"{indicator['id']} should include risk group"
        assert indicator["contribution"] >= 0, f"{indicator['id']} should include contribution"
        assert indicator["source"], f"{indicator['id']} should include a source"
        points = timeseries["series"].get(indicator["id"], [])
        assert len(points) >= 60, f"{indicator['id']} should expose enough trend points"
        assert all(0 <= point["value"] <= 100 for point in points), f"{indicator['id']} trend scores must be 0~100"

    assert backtest["sampleCount"] >= 60
    assert "byBucket" in backtest
    assert stress["sampleStart"] >= "2020-01-01"
    assert stress["sampleCount"] >= 120
    assert stress["episodeCount"] >= 1
    for episode in stress["episodes"]:
        assert episode["startDate"] <= episode["endDate"]
        assert episode["peakScore"] >= 0
        assert "topContributors" in episode

    print("Smoke tests passed")


if __name__ == "__main__":
    test_news_title_clustering()
    test_dashboard_contract()
