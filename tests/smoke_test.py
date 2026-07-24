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
SNOW_LAB_FILE = ROOT / "snow-lab.html"
SNOW_LAB_STYLE_FILE = ROOT / "src" / "snow-lab.css"
SNOW_LAB_SCRIPT_FILE = ROOT / "src" / "snow-lab.js"
OCEAN_LAB_SCRIPT_FILE = ROOT / "src" / "ocean-lab.js"
FOREST_LAB_SCRIPT_FILE = ROOT / "src" / "forest-lab.js"
WEBGL_FLUID_FILE = ROOT / "src" / "vendor" / "webgl-fluid.mjs"
WEBGL_FLUID_LICENSE_FILE = ROOT / "src" / "vendor" / "webgl-fluid.LICENSE"
WEBGL_FLUID_ORIGIN_LICENSE_FILE = ROOT / "src" / "vendor" / "webgl-fluid-origin.LICENSE"
THREE_MODULE_FILE = ROOT / "src" / "vendor" / "three.module.min.js"
THREE_CORE_FILE = ROOT / "src" / "vendor" / "three.core.min.js"
THREE_LICENSE_FILE = ROOT / "src" / "vendor" / "three.LICENSE"


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

    stress_replay = issuance_map["stressEpisodes"]
    assert stress_replay["defaultEpisodeId"]
    assert len(stress_replay["items"]) >= 4
    for episode in stress_replay["items"]:
        assert episode["startDate"] <= episode["peakDate"] <= episode["endDate"]
        assert len(episode["items"]) == 5
        for item in episode["items"]:
            assert len(item["trajectory"]) >= 2
            assert item["trajectory"][0]["date"] >= episode["startDate"]
            assert item["trajectory"][-1]["date"] <= episode["endDate"]
            assert all(
                0 <= point["opportunityScore"] <= 100 and 0 <= point["hedgeBurdenScore"] <= 100
                for point in item["trajectory"]
            )

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
    assert "renderElsStressEpisodeReview" in app_source
    assert 'data-els-episode="${episode.id}"' in app_source
    assert "스트레스 에피소드 리플레이" in app_source
    assert "keyTrajectoryPath(keyCoordinates)" in app_source
    assert "현재 기초지수 포지셔닝" in app_source
    assert app_source.index('<aside class="els-limitations">') < app_source.index(
        "${renderElsStressEpisodeReview(map.stressEpisodes, plot)}"
    )
    assert "변동성↑ 쿠폰↑" in app_source
    assert "하락위험↑ 부담↑" in app_source
    assert 'loadJson("./data/pipeline-status.json")' in app_source


def test_snow_lab_easter_egg_contract():
    app_source = APP_FILE.read_text(encoding="utf-8")
    html = SNOW_LAB_FILE.read_text(encoding="utf-8")
    styles = SNOW_LAB_STYLE_FILE.read_text(encoding="utf-8")
    script = SNOW_LAB_SCRIPT_FILE.read_text(encoding="utf-8")
    ocean_script = OCEAN_LAB_SCRIPT_FILE.read_text(encoding="utf-8")
    forest_script = FOREST_LAB_SCRIPT_FILE.read_text(encoding="utf-8")
    package_license = WEBGL_FLUID_LICENSE_FILE.read_text(encoding="utf-8")
    origin_license = WEBGL_FLUID_ORIGIN_LICENSE_FILE.read_text(encoding="utf-8")
    three_module = THREE_MODULE_FILE.read_text(encoding="utf-8")
    three_license = THREE_LICENSE_FILE.read_text(encoding="utf-8")

    assert 'class="snow-lab-trigger"' in app_source
    assert 'href="./snow-lab.html"' in app_source
    assert 'data-fluid-canvas' in html
    assert 'data-field-canvas' in html
    assert 'data-mode-select="snow"' in html
    assert 'data-mode-select="wave"' in html
    assert 'data-mode-select="spectrum"' in html
    assert 'data-mode-select="forest"' in html
    assert 'Navier–Stokes Field' in html
    assert 'content="noindex"' in html
    assert './src/snow-lab.css?v=' in html
    assert './src/snow-lab.js?v=' in html

    assert 'import("./vendor/webgl-fluid.mjs")' in script
    assert "SIM_RESOLUTION" in script
    assert "DYE_RESOLUTION" in script
    assert "PRESSURE_ITERATIONS" in script
    assert "prefers-reduced-motion" in script
    assert "visibilitychange" in script
    assert "pointermove" in script
    assert "requestedMode" in script
    assert '["snow", "wave", "spectrum", "forest"]' in script
    assert 'model: isSpectrumMode ? "spectrum" : "gerstner"' in script
    assert 'import("./ocean-lab.js")' in script
    assert "createOceanLab" in script
    assert "drawFallbackOcean" in script
    assert "renderFrame(performance.now())" in script
    assert "requestAnimationFrame" in script
    assert "navigator.deviceMemory || 0" in script
    assert "https://" not in script

    assert 'import * as THREE from "./vendor/three.module.min.js"' in ocean_script
    assert "new THREE.WebGLRenderer" in ocean_script
    assert "new THREE.PlaneGeometry" in ocean_script
    assert "new THREE.Raycaster" in ocean_script
    assert "addWave(point, vec2(1.0, 0.0)" in ocean_script
    assert "function jonswapSpectrum" in ocean_script
    assert "buildJonswapComponents" in ocean_script
    assert "seededRandom(20260723)" in ocean_script
    assert "uSpectrumWaves[SPECTRUM_WAVE_COUNT]" in ocean_script
    assert "dispersionDerivative" in ocean_script
    assert "float jacobian" in ocean_script
    assert "createSpectrumVertexShader" in ocean_script
    assert "uPointerStrength" in ocean_script
    assert "pointerFalloff" in ocean_script
    assert "renderer.setSize(width, height, false)" in ocean_script
    assert "https://" not in ocean_script

    assert 'import * as THREE from "./vendor/three.module.min.js"' in forest_script
    assert "new THREE.WebGLRenderer" in forest_script
    assert "new THREE.InstancedMesh" in forest_script
    assert "function terrainHeight" in forest_script
    assert "function installWindShader" in forest_script
    assert "aWindPhase" in forest_script
    assert "aWindStrength" in forest_script
    assert "localGust" in forest_script
    assert "high: { trees: 2400" in forest_script
    assert "balanced: { trees: 1650" in forest_script
    assert "eco: { trees: 950" in forest_script
    assert "float travelingGust" in forest_script
    assert "float windEnvelope" in forest_script
    assert '"forest-wind-v2"' in forest_script
    assert "const foregroundShare = 0.12" in forest_script
    assert "const risingSlope" in forest_script
    assert "compactView ? 15.8 : 14.2" in forest_script
    assert "createRidgeGeometry" in forest_script
    assert "renderer.setSize(width, height, false)" in forest_script
    assert "https://" not in forest_script

    assert "width: min(calc(100vw - 40px), 1440px);" in styles
    assert "height: min(calc(100dvh - 40px), 900px);" in styles
    assert '.snow-lab[data-mode="wave"]' in styles
    assert '.snow-lab[data-mode="spectrum"]' in styles
    assert '.snow-lab[data-mode="forest"]' in styles
    wave_rule = styles.split('.snow-lab[data-mode="wave"] .snow-lab__stage', 1)[1].split("}", 1)[0]
    assert "width: min(calc(100vw - 64px), 1280px);" in wave_rule
    assert "height: min(calc(100dvh - 64px), 760px);" in wave_rule
    assert "border-color: #1d4b57;" in wave_rule
    assert "grid-template-columns: repeat(4" in styles

    assert WEBGL_FLUID_FILE.stat().st_size > 50_000
    assert THREE_MODULE_FILE.stat().st_size > 300_000
    assert THREE_CORE_FILE.stat().st_size > 300_000
    assert 'from"./three.core.min.js"' in three_module
    assert "MIT License" in package_license
    assert "Cloyd Lau" in package_license
    assert "MIT License" in origin_license
    assert "Pavel Dobryakov" in origin_license
    assert "MIT License" in three_license
    assert "three.js authors" in three_license


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
    test_snow_lab_easter_egg_contract()
    test_pipeline_status_contract()
