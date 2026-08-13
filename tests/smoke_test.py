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
BREADTH_FILE = ROOT / "data" / "kospi-breadth.json"
STYLES_FILE = ROOT / "src" / "styles.css"
APP_FILE = ROOT / "src" / "app.js"
TARGETS_FILE = ROOT / "src" / "kospi_risk" / "targets.py"
PIPELINE_STATUS_FILE = ROOT / "data" / "pipeline-status.json"
DATA_QUALITY_FILE = ROOT / "data" / "data-quality.json"
SNOW_LAB_FILE = ROOT / "snow-lab.html"
SNOW_LAB_STYLE_FILE = ROOT / "src" / "snow-lab.css"
SNOW_LAB_SCRIPT_FILE = ROOT / "src" / "snow-lab.js"
OCEAN_LAB_SCRIPT_FILE = ROOT / "src" / "ocean-lab.js"
OBSTACLE_WAVE_SCRIPT_FILE = ROOT / "src" / "obstacle-wave-lab.js"
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
    assert any(
        reference["url"].startswith("https://www.bok.or.kr/")
        and "FSI/FVI" in reference["label"]
        for reference in market["model"]["references"]
    )
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
        "volatility_term_structure_watch",
        "us_market_breadth_watch",
        "broad_reinflation_watch",
        "m7_credit_stress_proxy",
    }
    assert all(float(indicator["weight"]) == 0 for indicator in observations)
    assert len(market["observationJournal"]) == 5
    assert {item["id"] for item in market["observationJournal"]} == {
        "ai-roi",
        "geopolitics-reinflation",
        "macro-rates",
        "flow-deleveraging",
        "china-memory-capex",
    }
    assert next(
        item for item in market["observationJournal"] if item["id"] == "china-memory-capex"
    )["score"] is None
    timeseries_ids = set(timeseries["series"])
    scored_journal_items = [
        item for item in market["observationJournal"] if item["score"] is not None
    ]
    for item in scored_journal_items:
        assert item["components"]
        assert abs(sum(float(component["weight"]) for component in item["components"]) - 1.0) < 0.001
        assert abs(
            sum(float(component["contribution"]) for component in item["components"])
            - float(item["score"])
        ) < 0.06
        assert all(component["id"] in timeseries_ids for component in item["components"])
        assert all(
            len(timeseries["series"][component["id"]]) >= 60
            for component in item["components"]
        )
    china_capex = next(
        item for item in market["observationJournal"] if item["id"] == "china-memory-capex"
    )
    assert china_capex["components"] == []
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
    assert len(issuance_map["singleStocks"]) == 2
    assert {item["id"] for item in issuance_map["singleStocks"]} == {"samsung", "hynix"}
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
    for item in issuance_map["singleStocks"]:
        assert item["assetType"] == "single-stock"
        assert item["includedInBasket"] is False
        assert item["includedInStressEpisodes"] is False
        assert 22 <= len(item["trajectory"]) <= 66

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
    assert "styles.css?v=20260813-5" in html
    assert "app.js?v=20260813-5" in html
    assert 'role="tablist"' in app_source
    assert 'role="tab"' in app_source
    assert 'role="tabpanel"' in app_source
    assert 'const visibleBaseTabs = data.tabs.filter(' in app_source
    assert '!["credit", "liquidity"].includes(tab.id)' in app_source
    assert "const visibleSections = data.sections.filter" in app_source
    assert "${visibleSections" in app_source
    assert 'tab.setAttribute("aria-selected"' in app_source
    assert 'history.replaceState(null, "", `#${target}`)' in app_source
    assert 'id: "model-monitoring"' in app_source
    assert 'id: "replay"' in app_source
    assert "function renderModelMonitoringPage" in app_source
    assert "function renderReplayPage" in app_source
    assert "data-source-detail-toggle" in app_source
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
    assert 'class="decision-cockpit"' in app_source
    assert 'class="attribution-list attribution-list--${direction}"' in app_source
    assert "formatAttributionDelta(totalChange)" in app_source
    assert 'const periodLabel = id === "1w" ? "1W 종합점수 변화" : "1D 종합점수 변화";' in app_source
    assert ".attribution-period[hidden]" in styles
    assert 'narrative-list--compact indicator-detail-list' in app_source
    assert "현재 시장리스크는 ${market.level.label} 단계입니다." not in app_source
    assert ".narrative-list {" in styles
    assert '.replace(/입니다$/, "")' in app_source
    assert '.replace(/입니다$/, "임")' not in app_source
    assert "const labeledItem = item.match" in app_source
    assert "상승형 고변동성과 위험회피 분리" in app_source
    assert "${basket.worstIndex} 주도 · ${basket.bucket}" in app_source
    assert "group-card__description" in app_source
    assert "관측창: 최대 2년" in app_source
    assert "핵심: 주가지수 · 환율 · 변동성 · 금리 · 크레딧 · 수급" in app_source
    assert ".attribution-panel {" in styles
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
    assert "function findSuccessfulRunForSchedule" in app_source
    assert "candidate.completedTimestamp >= scheduleItem.timestamp" in app_source
    assert 'scheduleItem.mode === "full"' in app_source
    assert "function medianRunDuration" in app_source
    assert "formatCountdownSeconds((item.timestamp - now) / 1000)" in app_source
    assert "formatCountdownSeconds(item.timestamp - now)" not in app_source
    assert "오늘의 예약 실행" in app_source
    assert "다음 예약" in app_source
    assert "schedule.saturdayTimes ?? []" in app_source
    assert "schedule.mondayTimes ?? schedule.times" in app_source
    assert "토 ${saturdayScheduleText}" in app_source
    assert 'statusLabel: matched.replacement ? "보완 완료" : "완료"' in app_source
    assert 'statusLabel: delayed ? "지연" : "진행 중"' in app_source
    assert "최근 성공 중앙 소요시간" in app_source
    assert "${renderScheduleOverview(pipelineStatus)}" in app_source
    assert "function renderResearchOperationsLog" not in app_source
    assert "관찰지표 운영일지" not in app_source
    assert ".research-operations-log__item" not in styles
    assert ".operations-schedule-list" in styles
    assert ".operations-schedule-item--caution" in styles
    assert 'SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"' in run_script
    assert 'MONDAY_TIMES="${LOCAL_MARKET_UPDATE_MONDAY_TIMES:-12:30,15:35}"' in run_script
    assert 'SCHEDULED_DAY_TYPE="saturday"' in run_script
    assert 'if [[ "$SCHEDULED_DAY_TYPE" == "saturday" ]]' in run_script
    assert '--saturday-times "$SATURDAY_TIMES"' in run_script
    assert 'SATURDAY_TIMES="${LOCAL_MARKET_UPDATE_SATURDAY_TIMES:-07:30}"' in installer
    assert 'append_calendar_intervals "$MONDAY_TIMES" 1' in installer
    assert 'append_calendar_intervals "$TIMES" 2 3 4 5' in installer


def test_dashboard_data_requests_bypass_stale_cache():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")
    assert "DATA_REQUEST_VERSION = Date.now()" in app_source
    assert "request=${DATA_REQUEST_VERSION}" in app_source
    assert 'cache: "no-store"' in app_source
    assert 'id: "operations", label: "운영현황"' in app_source
    assert 'id: "els-issuance", label: "ELS 발행·헤지"' in app_source
    assert "renderElsIssuanceHedgePage" in app_source
    assert 'class="els-index-card__asof"' in app_source
    assert '${item.label}<small>${item.lastDate ?? "-"}</small>' in app_source
    assert 'data-els-window="${window.id}"' in app_source
    assert 'data-els-trajectory="${window.id}"' in app_source
    assert "curvedTrajectoryPath(coordinates)" in app_source
    assert 'id: "1w"' in app_source
    assert 'marker-end="url(#els-map-arrow-${item.id})"' in app_source
    assert "1주 방향" in app_source
    assert "renderElsStressEpisodeReview" in app_source
    assert "renderElsSingleStockSection" in app_source
    assert "map.singleStocks" in app_source
    assert "개별종목 참고" in app_source
    assert "els-map-point--single-stock" in app_source
    assert "els-map-trajectory-series--single-stock" in app_source
    assert 'data-els-episode="${episode.id}"' in app_source
    assert "스트레스 에피소드 리플레이" in app_source
    assert "keyTrajectoryPath(keyCoordinates)" in app_source
    assert "현재 기초자산 포지셔닝" in app_source
    assert app_source.index('<aside class="els-limitations">') < app_source.index(
        "${renderElsSingleStockSection(singleStockItems, map.singleStockMethodology)}"
    )
    assert app_source.index(
        "${renderElsSingleStockSection(singleStockItems, map.singleStockMethodology)}"
    ) < app_source.index(
        "${renderElsStressEpisodeReview(map.stressEpisodes, plot)}"
    )
    assert "변동성↑ 쿠폰↑" in app_source
    assert "하락위험↑ 부담↑" in app_source
    assert "methodologyReference" in app_source
    assert "한국은행 FSI·FVI 설명" in app_source
    assert 'target="_blank" rel="noopener noreferrer"' in app_source
    assert ".model-reference" in styles
    assert 'loadJson("./data/pipeline-status.json")' in app_source
    assert 'loadJson("./data/naver-marketindex-history.json")' in app_source
    assert "renderMarketIndexTrendPanel" in app_source
    assert "금리·환율·원자재·운임 방향성" in app_source
    assert "현재값은 실시간·지연 잠정치" in app_source
    assert "market-trend-row__live-line" in app_source
    assert 'class="market-trend-row__current"' in app_source
    assert ".market-trend-row__current dd" in styles
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
    assert 'data-group-filter="${group.id}"' in app_source
    assert "function updateIndicatorGrid" not in app_source
    assert "const updateIndicatorGrid" in app_source
    assert 'data-indicator-filter-reset="${sectionId}"' in app_source
    assert ".group-card:has(.group-card__info:hover) .group-card__tooltip" in styles
    assert ".group-card:has(.group-card__info:focus-visible) .group-card__tooltip" in styles
    assert "엔화 약세" in app_source
    assert '{ id: "jp10y_naver", label: "일본 10년"' in app_source
    assert '{ id: "usdkrw_naver", label: "원/달러"' in app_source
    assert 'upLabel: "원화 약세", downLabel: "원화 강세"' in app_source

    summary_source = app_source.split("function renderSummary", 1)[1].split("function renderModelPanel", 1)[0]
    assert "renderDecisionCockpit" in summary_source
    assert "renderScoreAttribution" in summary_source
    assert "renderMarketIndexTrendPanel" not in summary_source
    assert "renderBacktestPanel" not in summary_source
    assert "renderStressEpisodesPanel" not in summary_source

    section_source = app_source.split("function renderSection", 1)[1].split("function renderDashboard", 1)[0]
    assert section_source.index("renderIndicatorSortControls") < section_source.index("renderBacktestPanel")
    assert section_source.index("renderBacktestPanel") < section_source.index("renderStressEpisodesPanel")
    assert 'data-market-direction-slot' in section_source
    assert 'data-market-history-slot' in section_source
    assert "renderObservationJournal(section, timeseries)" in section_source
    assert "시장 의견 검증 일지" in app_source
    assert ".observation-journal__item" in styles


def test_interactive_timeline_range_and_cursor_contract():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")

    assert '{ id: "1m", label: "1M", calendarDays: 31 }' in app_source
    assert '{ id: "3m", label: "3M", calendarDays: 93 }' in app_source
    assert '{ id: "ytd", label: "YTD" }' in app_source
    assert '{ id: "1y", label: "1Y", calendarDays: 366 }' in app_source
    assert '{ id: "3y", label: "3Y", calendarDays: 1096 }' in app_source
    assert 'let activeChartRange = "ytd"' in app_source
    assert "CHART_RANGE_STORAGE_KEY" not in app_source
    assert "function chartRangeDomain" in app_source
    assert "const estimatedHalfWidth = Math.max(8, segment.label.length * 3.5)" in app_source
    assert "Math.min(width - edgePadding - estimatedHalfWidth, segment.centerX)" in app_source
    assert "function registerInteractiveChart" in app_source
    assert "function initializeInteractiveCharts" in app_source
    assert "function updateChartCursor" in app_source
    assert "function nearestChartPoint" in app_source
    assert "function renderChartRangeButtons" in app_source
    assert "function renderMarketChartRangeDock" in app_source
    assert 'class="market-chart-range-dock"' in app_source
    assert 'renderChartRangeButtons("market-global"' in app_source
    assert "시장리스크 전체 시계열 조회 기간" in app_source
    assert 'section.id === "market" ? renderMarketChartRangeDock()' in app_source
    assert 'data-chart-range="${option.id}"' in app_source
    assert 'data-chart-range-layer="${range.id}"' in app_source
    assert "data-chart-cursor-line" in app_source
    assert "data-chart-tooltip" in app_source
    assert 'chart.addEventListener("pointermove"' in app_source
    assert 'chart.addEventListener("pointerdown"' in app_source
    assert 'status === "잠정"' in app_source
    assert "renderChartRangeControls(chartId, {" in app_source
    assert 'tooltipMode: "hovered"' in app_source
    assert 'data-timeseries-chart="${chartId}"' in app_source
    assert 'data-chart-series-index="${seriesIndex}"' in app_source
    assert "renderHmmRegimeBands(visible, domain)" in app_source
    assert "market-trend-row__live-line" in app_source
    assert ".chart-range-control" in styles
    assert "position: sticky;" in styles.split(".market-chart-range-dock {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in styles
    assert ".chart-range-layer.is-active" in styles
    assert ".chart-cursor-line.is-visible" in styles
    assert ".chart-cursor-tooltip.is-visible" in styles
    assert ".chart-value-status__provisional" in styles


def test_ml_crash_chart_distinguishes_model_target_and_els_reference():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")
    targets_source = TARGETS_FILE.read_text(encoding="utf-8")

    assert 'out["fwd_min_ret_5d"] = _future_min_return(out["KOSPI"], crash_horizon)' in targets_source
    assert "KOSPI 5일 급락신호와 지수 흐름" in app_source
    assert "KOSPI · 모델 평가대상 · 연초=100" in app_source
    assert "KOSPI200 · ELS 참고선 · 연초=100" in app_source
    assert "KOSPI200은 ELS 기초자산 참고선 · 모델 적중률·상관 산정 제외" in app_source
    assert "5일 급락신호 → KOSPI200" not in app_source
    assert "modelPriceSeries: indexedModelPrices" in app_source
    assert "referencePriceSeries: indexedReferencePrices" in app_source
    assert ".ml-risk-chart__kospi" in styles
    assert ".legend-kospi200" in styles


def test_market_breadth_dashboard_contract():
    breadth = json.loads(BREADTH_FILE.read_text(encoding="utf-8"))
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")

    latest = breadth["latest"]
    quality = breadth["quality"]
    assert breadth["source"]["provider"] == "KRX"
    assert breadth["source"]["frequency"] == "EOD"
    assert breadth["source"]["vkospiStatus"] in {"merged", "not_available"}
    assert breadth["period"]["observations"] >= 500
    assert latest["up"] + latest["down"] + latest["flat"] == latest["total"]
    assert quality["status"] in {"ok", "warning"}
    assert len(breadth["series"]) == breadth["period"]["observations"]
    assert "function renderMarketBreadthPage" in app_source
    assert "function renderBreadthPriceChart" in app_source
    assert "function renderBreadthAdChart" in app_source
    assert 'id: "market-breadth", label: "시장 내부강도"' in app_source
    assert 'loadJson("./data/kospi-breadth.json")' in app_source
    assert "VKOSPI 미결합 · risk-on·panic 확정 판정 보류" in app_source
    assert "renderChartRangeControls(chartId)" in app_source
    assert "일간 확산도 (%)" in app_source
    assert "범위 -100~+100 · 0=균형" in app_source
    assert "당일 순확산 (Net)" in app_source
    assert "AD 누적선 (천 종목)" in app_source
    assert "formatSignedThousands" in app_source
    assert ".breadth-unit-guide" in styles
    assert "plotLeft = 0" in app_source
    assert "일간 확산 · 오른쪽" in app_source
    assert "오른쪽 확산도 축은 항상 -100~+100%" in app_source
    assert "어떻게 읽나요" in app_source
    assert ".breadth-chart__axis-title" in styles
    assert ".breadth-interpretation-guide" in styles
    assert "breadth-chart__kospi-halo" in app_source
    assert "일간 확산 · 오른쪽 · 옅은 선" in app_source
    assert ".breadth-chart__kospi-halo" in styles
    assert "opacity: 0.32;" in styles
    assert "const plotLeft = 64" in app_source
    assert "const plotRight = 696" in app_source
    assert "font-size: 11.5px;" in styles
    assert "font-size: 10.5px;" in styles
    assert "min-width: 620px;" in styles
    assert ".breadth-chart__daily" in styles
    assert ".breadth-chart__ad" in styles


def test_weighted_group_timeline_contract():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")

    assert "function buildGroupCompositeSeries" in app_source
    assert "indicator.group === groupId" in app_source
    assert "isScoredIndicator(indicator)" in app_source
    assert "dateWeights[date] >= totalWeight * 0.7" in app_source
    assert "const currentGroup = (section.groupScores ?? []).find" in app_source
    assert "function renderGroupScoreTrend" in app_source
    assert "가중 점수 흐름" in app_source
    assert "data-chart-active-range-label" in app_source
    assert "renderGroupScores(section, timeseries)" in app_source
    assert ".group-card__trend-line" in styles
    assert "grid-template-columns: repeat(5, 42px)" in styles
    assert 'class="risk-color-legend"' in app_source
    assert "그룹색" in app_source
    assert "카드 추세" in app_source
    assert "부담 상승" in app_source
    assert "부담 하락" in app_source
    assert "--group-liquidity: #8fb1c9;" in styles
    assert "--group-accent: var(--group-liquidity);" in styles
    assert "stroke: var(--trend-worsening);" in styles
    assert "stroke: var(--trend-improving);" in styles
    assert "stroke: var(--trend-flat);" in styles


def test_observation_journal_timeline_and_detail_contract():
    app_source = APP_FILE.read_text(encoding="utf-8")
    styles = STYLES_FILE.read_text(encoding="utf-8")

    assert "function buildObservationJournalSeries" in app_source
    assert ".filter((date) => dateWeights[date] >= totalWeight * 0.7)" in app_source
    assert "function renderObservationJournalTrend" in app_source
    assert "function renderObservationJournalDetail" in app_source
    assert "renderObservationJournal(section, timeseries)" in app_source
    assert "검증 점수 흐름" in app_source
    assert "검증 점수 구성" in app_source
    assert "일지 내부 비중 · 종합점수 가중치 0" in app_source
    assert "data-observation-detail-toggle" in app_source
    assert 'aria-expanded="false"' in app_source
    assert "detail.hidden = expanded" in app_source
    assert ".observation-journal__trend-line" in styles
    assert ".observation-journal__detail[hidden]" in styles


def test_snow_lab_easter_egg_contract():
    app_source = APP_FILE.read_text(encoding="utf-8")
    html = SNOW_LAB_FILE.read_text(encoding="utf-8")
    styles = SNOW_LAB_STYLE_FILE.read_text(encoding="utf-8")
    script = SNOW_LAB_SCRIPT_FILE.read_text(encoding="utf-8")
    ocean_script = OCEAN_LAB_SCRIPT_FILE.read_text(encoding="utf-8")
    obstacle_script = OBSTACLE_WAVE_SCRIPT_FILE.read_text(encoding="utf-8")
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
    assert 'data-mode-select="obstacle"' in html
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
    assert '["snow", "wave", "spectrum", "obstacle", "forest"]' in script
    assert 'model: isSpectrumMode ? "spectrum" : "gerstner"' in script
    assert 'import("./ocean-lab.js")' in script
    assert 'import("./obstacle-wave-lab.js?v=20260729-1")' in script
    assert "createOceanLab" in script
    assert "createObstacleWaveLab" in script
    assert "drawFallbackOcean" in script
    assert "drawFallbackObstacleWave" in script
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

    assert 'import * as THREE from "./vendor/three.module.min.js"' in obstacle_script
    assert "new THREE.WebGLRenderer" in obstacle_script
    assert "new THREE.PlaneGeometry" in obstacle_script
    assert "new THREE.BoxGeometry" in obstacle_script
    assert "new THREE.Raycaster" in obstacle_script
    assert "collisionJet" in obstacle_script
    assert "diffraction" in obstacle_script
    assert "createSpraySystem" in obstacle_script
    assert "sampleImpactState" in obstacle_script
    assert "verticalVelocity" in obstacle_script
    assert "surfaceHeightAt" in obstacle_script
    assert "THREE.NormalBlending" in obstacle_script
    assert "impactCrest" not in obstacle_script
    assert "burstPending" not in obstacle_script
    assert "pointerNearObstacle" in obstacle_script
    assert 'stage.setAttribute("data-obstacle-wave-ready", "true")' in obstacle_script
    assert "renderer.setSize(width, height, false)" in obstacle_script
    assert "https://" not in obstacle_script

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
    assert '.snow-lab[data-mode="obstacle"]' in styles
    assert '.snow-lab[data-mode="forest"]' in styles
    assert "filter: grayscale(0.94) sepia(0.28) hue-rotate(150deg)" in styles
    wave_rule = styles.split('.snow-lab[data-mode="wave"] .snow-lab__stage', 1)[1].split("}", 1)[0]
    assert "width: min(calc(100vw - 64px), 1280px);" in wave_rule
    assert "height: min(calc(100dvh - 64px), 760px);" in wave_rule
    assert "border-color: #1d4b57;" in wave_rule
    assert "grid-template-columns: repeat(5" in styles

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
    assert {item["time"] for item in status["schedule"]["mondayTimes"]} == {"12:30", "15:35"}
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
            "m7-credit",
            "krx-breadth",
        }
    assert len(status["artifacts"]) >= 9
    assert any(item["id"] == "breadth" and item["status"] == "ok" for item in status["artifacts"])
    assert status["quality"]["score"] >= 0
    assert len(status["researchLog"]) == 5
    assert all(item["decision"] and item["operation"] for item in status["researchLog"])
    assert status["history"]
    assert quality["schemaVersion"] == 1
    assert quality["summary"]["sourceSeriesExpected"] == 94
    assert quality["summary"]["sourceSeriesPresent"] == 94
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
