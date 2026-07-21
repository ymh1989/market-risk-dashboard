import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "risk-dashboard.json"
TIMESERIES_FILE = ROOT / "data" / "market-risk-timeseries.json"
MARKET_INDEX_CACHE_FILE = ROOT / "data" / "naver-marketindex-history.json"
BACKTEST_FILE = ROOT / "data" / "market-risk-backtest.json"
STRESS_FILE = ROOT / "data" / "market-stress-episodes.json"
ELS_FILE = ROOT / "data" / "els-index-risk.json"
STYLES_FILE = ROOT / "src" / "styles.css"
APP_FILE = ROOT / "src" / "app.js"
PIPELINE_STATUS_FILE = ROOT / "data" / "pipeline-status.json"


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


def test_dashboard_contract():
    dashboard = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    timeseries = json.loads(TIMESERIES_FILE.read_text(encoding="utf-8"))
    market_index_cache = json.loads(MARKET_INDEX_CACHE_FILE.read_text(encoding="utf-8"))
    backtest = json.loads(BACKTEST_FILE.read_text(encoding="utf-8"))
    stress = json.loads(STRESS_FILE.read_text(encoding="utf-8"))
    els_risk = json.loads(ELS_FILE.read_text(encoding="utf-8"))
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

    issuance_map = els_risk["issuanceHedgeMap"]
    assert len(issuance_map["items"]) == 5
    assert {item["id"] for item in issuance_map["items"]} == {
        "spx",
        "sx5e",
        "nky",
        "hscei",
        "kospi200",
    }
    assert issuance_map["basket"]["stance"] in {"발행기회", "선별발행", "헤지주의", "발행부담"}
    assert 0 <= issuance_map["basket"]["opportunityScore"] <= 100
    assert 0 <= issuance_map["basket"]["hedgeBurdenScore"] <= 100
    for item in issuance_map["items"]:
        assert item["stance"] in {"발행기회", "선별발행", "헤지주의", "발행부담"}
        assert 0 <= item["opportunityScore"] <= 100
        assert 0 <= item["hedgeBurdenScore"] <= 100
        assert item["interpretation"]
        assert 22 <= len(item["trajectory"]) <= 66
        assert [point["date"] for point in item["trajectory"]] == sorted(
            point["date"] for point in item["trajectory"]
        )
        assert item["trajectory"][-1]["opportunityScore"] == item["opportunityScore"]
        assert item["trajectory"][-1]["hedgeBurdenScore"] == item["hedgeBurdenScore"]

    print("Smoke tests passed")


def test_watch_badge_keeps_readable_contrast():
    styles = STYLES_FILE.read_text(encoding="utf-8")
    assert styles.count(".status-pill--watch") == 1
    watch_rule = styles.split(".status-pill--watch", 1)[1].split("}", 1)[0]
    assert "background: var(--blue);" in watch_rule
    assert "color: #fff;" in watch_rule


def test_dashboard_data_requests_bypass_stale_cache():
    app_source = APP_FILE.read_text(encoding="utf-8")
    assert "DATA_REQUEST_VERSION = Date.now()" in app_source
    assert "request=${DATA_REQUEST_VERSION}" in app_source
    assert 'cache: "no-store"' in app_source
    assert 'id: "operations", label: "운영현황"' in app_source
    assert 'id: "els-issuance", label: "ELS 발행·헤지"' in app_source
    assert "renderElsIssuanceHedgePage" in app_source
    assert 'data-els-window="${window.id}"' in app_source
    assert 'data-els-trajectory="${window.id}"' in app_source
    assert "curvedTrajectoryPath(coordinates)" in app_source
    assert 'id: "1w"' in app_source
    assert 'marker-end="url(#els-map-arrow-${item.id})"' in app_source
    assert "1주 방향" in app_source
    assert "변동성↑ 쿠폰↑" in app_source
    assert "하락위험↑ 부담↑" in app_source
    assert 'loadJson("./data/pipeline-status.json")' in app_source


def test_pipeline_status_contract():
    status = json.loads(PIPELINE_STATUS_FILE.read_text(encoding="utf-8"))
    assert status["schemaVersion"] == 1
    assert status["current"]["status"] == "success"
    assert status["current"]["dataAsOf"]
    assert {item["time"] for item in status["schedule"]["times"]} == {"07:30", "12:30", "15:35"}
    assert {stage["id"] for stage in status["stages"]} == {"market", "ml", "validation", "deployment"}
    assert all(stage["status"] == "success" for stage in status["stages"])
    assert {source["id"] for source in status["sources"]} == {
        "yahoo",
        "naver-equity",
        "naver-market-index",
        "fred",
    }
    assert len(status["artifacts"]) >= 6
    assert status["history"]


if __name__ == "__main__":
    test_dashboard_contract()
    test_watch_badge_keeps_readable_contrast()
    test_dashboard_data_requests_bypass_stale_cache()
    test_pipeline_status_contract()
