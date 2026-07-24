import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "risk-dashboard.json"
INDEX_FILE = ROOT / "index.html"
TIMESERIES_FILE = ROOT / "data" / "market-risk-timeseries.json"
MARKET_INDEX_CACHE_FILE = ROOT / "data" / "naver-marketindex-history.json"
BACKTEST_FILE = ROOT / "data" / "market-risk-backtest.json"
STRESS_FILE = ROOT / "data" / "market-stress-episodes.json"
ELS_FILE = ROOT / "data" / "els-index-risk.json"
STYLES_FILE = ROOT / "src" / "styles.css"
APP_FILE = ROOT / "src" / "app.js"
PIPELINE_STATUS_FILE = ROOT / "data" / "pipeline-status.json"
DATA_QUALITY_FILE = ROOT / "data" / "data-quality.json"
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
        "japan_us_rate_spread_watch",
    }
    assert all(float(indicator["weight"]) == 0 for indicator in observations)
    assert market_index_cache["schemaVersion"] == 1
    assert set(market_index_cache.get("liveSnapshots") or {}).issubset(
        set(market_index_cache["series"])
    )
    assert set(market_index_cache.get("liveSnapshotStatuses") or {}) == set(
        market_index_cache["series"]
    )
    assert all(
        isinstance(snapshot.get("isProvisional"), bool)
        and snapshot.get("observedAt")
        and isinstance(snapshot.get("previousClose"), (int, float))
        for snapshot in market_index_cache["liveSnapshots"].values()
    )
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
        "usdkrw_naver",
        "us2y_naver",
        "us10y_naver",
        "jp10y_naver",
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
    status_rule = styles.split(".status-pill {", 1)[1].split("}", 1)[0]
    watch_rule = styles.split(".status-pill--watch", 1)[1].split("}", 1)[0]
    assert styles.count("--status-ink:") == 2
    assert "color: var(--status-ink);" in status_rule
    assert "background: var(--blue);" in watch_rule


def test_ui_hierarchy_and_accessibility_contract():
    html = INDEX_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")
    app_source = APP_FILE.read_text(encoding="utf-8")
    eyebrow_rule = styles.split(".eyebrow {", 1)[1].split("}", 1)[0]
    source_chip_rule = styles.split(".source-chips span {", 1)[1].split("}", 1)[0]
    sparkline_rule = styles.split(".sparkline {", 1)[1].split("}", 1)[0]

    assert '<a class="skip-link" href="#app">대시보드 본문으로 이동</a>' in html
    assert "styles.css?v=20260724-13" in html
    assert "app.js?v=20260724-13" in html
    assert 'aria-pressed="${tab.id === "summary" ? "true" : "false"}"' in app_source
    assert 'tab.setAttribute("aria-pressed"' in app_source
    assert "font-weight: 800;" not in styles
    assert "font-weight: 900;" not in styles
    assert "text-transform: uppercase;" not in styles
    assert "font-size: clamp(" not in styles
    assert "letter-spacing: 0;" in eyebrow_rule
    assert "border: 0;" in source_chip_rule
    assert "border: 0;" in sparkline_rule
    assert "@media (prefers-reduced-motion: reduce)" in styles


def test_korean_copy_uses_structured_lists_and_contextual_wrapping():
    styles = STYLES_FILE.read_text(encoding="utf-8")
    app_source = APP_FILE.read_text(encoding="utf-8")

    assert "function renderNarrativeList" in app_source
    assert '.replace(/입니다$/, "임")' in app_source
    assert '.replace(/입니다$/, "")' not in app_source
    assert '<dl class="summary-facts">' in app_source
    assert 'narrative-list--compact indicator-detail-list' in app_source
    assert "현재 시장리스크는 ${market.level.label} 단계입니다." not in app_source
    assert ".narrative-list {" in styles
    assert ".summary-facts {" in styles
    assert "word-break: keep-all;" in styles
    assert "text-wrap: pretty;" in styles
    assert "text-wrap: balance;" in styles


def test_operation_mode_distinguishes_active_and_completed_runs():
    app_source = APP_FILE.read_text(encoding="utf-8")

    assert 'if (mode === "full") return "전체 갱신";' in app_source
    assert 'if (mode === "fast") return "빠른 갱신";' in app_source
    assert "activeRun: null" in app_source
    assert "elapsedSeconds: Math.floor(elapsedMinutes * 60)" in app_source
    assert "state.activeRun.mode" in app_source
    assert "최근 완료 · ${pipelineModeLabel(current.mode)}" in app_source
    assert "<span>${current.mode}" not in app_source


def test_operations_page_exposes_daily_schedule_overview():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")
    run_script = (ROOT / "scripts" / "run_local_market_update.sh").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_local_market_update_launchd.sh").read_text(
        encoding="utf-8"
    )

    assert "function buildScheduleOverview" in app_source
    assert "function medianRunDuration" in app_source
    assert "오늘의 예약 실행" in app_source
    assert "다음 예약" in app_source
    assert "schedule.saturdayTimes ?? []" in app_source
    assert "토 ${saturdayScheduleText}" in app_source
    assert 'statusLabel: "완료"' in app_source
    assert 'statusLabel: delayed ? "지연" : "진행 중"' in app_source
    assert "최근 성공 중앙 소요시간" in app_source
    assert "${renderScheduleOverview(pipelineStatus)}" in app_source
    assert ".operations-schedule-list" in styles
    assert ".operations-schedule-item--caution" in styles
    assert 'SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"' in run_script
    assert 'SCHEDULED_DAY_TYPE="saturday"' in run_script
    assert 'if [[ "$SCHEDULED_DAY_TYPE" == "saturday" ]]' in run_script
    assert '--saturday-times "$SATURDAY_TIMES"' in run_script
    assert 'SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"' in installer


def test_dashboard_data_requests_bypass_stale_cache():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")
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
    assert 'loadJson("./data/naver-marketindex-history.json")' in app_source
    assert "renderMarketIndexTrendPanel" in app_source
    assert "금리·환율·원자재·운임 방향성" in app_source
    assert "현재값은 실시간·지연 잠정치" in app_source
    assert "market-trend-row__live-line" in app_source
    assert '"weekly" ? "직전" : "전일"' in app_source
    assert "const riskGroupDefinitions" in app_source
    assert 'class="group-card__info"' in app_source
    assert 'class="group-card__tooltip"' in app_source
    assert 'role="tooltip"' in app_source
    assert 'aria-describedby="${tooltipId}"' in app_source
    assert "관찰 전용 · 가중치 미반영" in app_source
    assert "가중 반영 · 기여도 높은 순" in app_source
    assert "indicator.contributionPct" in app_source
    assert "Number(right.contribution ?? 0) - Number(left.contribution ?? 0)" in app_source
    assert "indicator-group-tag" in app_source
    assert ".group-card:hover .group-card__tooltip" in styles
    assert ".group-card:focus-within .group-card__tooltip" in styles
    assert "엔화 약세" in app_source
    assert '{ id: "jp10y_naver", label: "일본 10년"' in app_source
    assert '{ id: "usdkrw_naver", label: "원/달러"' in app_source
    assert 'upLabel: "원화 약세", downLabel: "원화 강세"' in app_source

    summary_source = app_source.split("function renderSummary", 1)[1].split("function renderModelPanel", 1)[0]
    assert summary_source.index("renderMarketIndexTrendPanel") < summary_source.index("renderBacktestPanel")
    assert summary_source.index("renderBacktestPanel") < summary_source.index("renderStressEpisodesPanel")

    section_source = app_source.split("function renderSection", 1)[1].split("function renderDashboard", 1)[0]
    assert section_source.index("renderIndicatorSortControls") < section_source.index("renderBacktestPanel")
    assert section_source.index("renderBacktestPanel") < section_source.index("renderStressEpisodesPanel")


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
    assert "COLOR_UPDATE_SPEED: 0.7" in script
    assert "SPLAT_FORCE: 3200" in script
    assert "BLOOM_INTENSITY: 0.18" in script
    assert "SUNRAYS: false" in script
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
    assert "filter: grayscale(0.94) sepia(0.28) hue-rotate(150deg)" in styles
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
    quality = json.loads(DATA_QUALITY_FILE.read_text(encoding="utf-8"))
    assert status["schemaVersion"] == 1
    assert status["current"]["status"] == "success"
    assert status["current"]["dataAsOf"]
    assert {item["time"] for item in status["schedule"]["times"]} == {"07:30", "12:30", "15:35"}
    assert status["schedule"]["saturdayTimes"] == [{"time": "07:30", "mode": "full"}]
    assert status["schedule"]["weekdaysOnly"] is False
    assert {stage["id"] for stage in status["stages"]} == {"market", "ml", "validation", "deployment"}
    assert all(stage["status"] == "success" for stage in status["stages"])
    assert {source["id"] for source in status["sources"]} == {
        "yahoo",
        "naver-equity",
        "naver-market-index",
        "fred",
        "ml-input",
    }
    assert len(status["artifacts"]) >= 6
    assert status["quality"]["score"] >= 0
    assert status["history"]
    assert quality["schemaVersion"] == 1
    assert quality["summary"]["sourceSeriesExpected"] == 72
    assert quality["summary"]["sourceSeriesPresent"] == 72
    assert quality["summary"]["error"] == 0


if __name__ == "__main__":
    test_dashboard_contract()
    test_watch_badge_keeps_readable_contrast()
    test_ui_hierarchy_and_accessibility_contract()
    test_korean_copy_uses_structured_lists_and_contextual_wrapping()
    test_operation_mode_distinguishes_active_and_completed_runs()
    test_operations_page_exposes_daily_schedule_overview()
    test_dashboard_data_requests_bypass_stale_cache()
    test_snow_lab_easter_egg_contract()
    test_pipeline_status_contract()
