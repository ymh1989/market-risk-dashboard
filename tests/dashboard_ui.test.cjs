const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = join(__dirname, "..");
const model = readFileSync(join(root, "src/risk-model.js"), "utf8").replace(/^export\s+/gm, "");
const source = readFileSync(join(root, "src/app.js"), "utf8")
  .replace(/^import[^\n]+\n/, "")
  .replace(/\nstartDashboard\(\);\s*$/, "\n");

function harness(options = {}) {
  const errorNode = { textContent: "" };
  const button = { addEventListener() {} };
  const app = {
    innerHTML: "",
    querySelector: (selector) => selector === "[data-load-error]" ? errorNode : button
  };
  const timers = new Map();
  let sequence = 0;
  const context = vm.createContext({
    console: { warn() {} },
    AbortController,
    document: {
      querySelector: (selector) => selector === "#app" ? app : null,
      documentElement: { classList: { toggle() {} }, dataset: {}, style: {} }
    },
    window: { matchMedia: () => ({ matches: false }) },
    localStorage: { getItem: () => null },
    setTimeout(callback, delay) {
      const id = ++sequence;
      if (delay < 15000) queueMicrotask(callback);
      else timers.set(id, callback);
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    ...options
  });
  vm.runInContext(`${model}\n${source}`, context);
  return { context, app, timers, errorNode };
}

function completeFlowPoint(date = "2026-10-14", pressure = 70.9) {
  return {
    date, directFlowPressure: pressure,
    foreignNetBuyEok: -11705.1, foreignNetBuy5dEok: -124235.5, foreignSellPressure: 87.1,
    institutionNetBuyEok: 7901.7, institutionNetBuy5dEok: 3328, institutionSellPressure: 56.2,
    programNetBuyEok: -40.9, programNetBuy5dEok: -61120.7, programSellPressure: 60.1
  };
}

test("외국인·기관 결측 때 프로그램 점수를 종합압력으로 오인하지 않음", () => {
  const { context } = harness();
  const previous = completeFlowPoint();
  const latest = {
    ...completeFlowPoint("2026-10-15", 39.6),
    foreignNetBuyEok: null, institutionNetBuyEok: null,
    foreignNetBuy5dEok: null, institutionNetBuy5dEok: null,
    foreignSellPressure: null, institutionSellPressure: null,
    programNetBuyEok: 5688.5, programNetBuy5dEok: -36639, programSellPressure: 39.6
  };
  const payload = { latest, series: [previous, latest] };
  const original = JSON.stringify(payload);
  assert.equal(context.completeDirectFlowScore(latest), null);
  const html = context.renderBreadthFlowPanel(payload);
  for (const label of ["당일 판독 대기", "외국인·기관", "1/3 확인", "직전 완비 2026-10-14", "70.9 / 100", "당일값 아님", "집계 대기"]) {
    assert.ok(html.includes(label), label);
  }
  assert.doesNotMatch(html, /매도압력 39\.6|NaN|undefined/);
  assert.match(context.renderBreadthSummaryPanel(payload), /집계 대기/);
  assert.equal(JSON.stringify(payload), original);
});

test("수급 0은 유효하고, 구버전 캐시의 결측·무한대 점수는 차단", () => {
  const { context } = harness();
  const complete = completeFlowPoint("2026-10-15", 0);
  assert.equal(context.completeDirectFlowScore(complete), 0);
  assert.match(context.renderBreadthFlowPanel({ latest: complete, series: [complete] }), /매도압력 0\.0 \/ 100/);
  for (const prefix of ["foreign", "institution", "program"]) {
    for (const suffix of ["NetBuyEok", "NetBuy5dEok", "SellPressure"]) {
      for (const invalid of [null, undefined, "", Infinity, NaN]) {
        assert.equal(context.completeDirectFlowScore({ ...complete, [`${prefix}${suffix}`]: invalid }), null);
      }
    }
  }
});

test("자금 원장의 관측일과 재조회 시각·실패 상태를 구분", () => {
  const { context } = harness();
  const payload = JSON.parse(readFileSync(join(root, "data/kb-market-funds.json"), "utf8"));
  payload.generatedAt = "2026-10-16 10:00:05 KST";
  payload.sourceStatus.kofiaHistory = "stale-fallback";
  const html = context.renderDomesticFundingPanel(payload);
  assert.match(html, /최근 조회 시도 2026-10-16 10:00:05 KST/);
  assert.ok(html.includes(`기준일 ${payload.latest.date}`));
  assert.match(html, /원천 조회 실패 · 이전 관측 유지/);
});

test("잠정값은 확정값보다 최근 날짜·유효 시각·신선한 원천일 때만 채택", () => {
  const { context } = harness();
  const definition = { id: "us10y_naver", type: "yield" };
  const confirmed = { date: "2026-10-09", close: 5.244 };
  const snapshot = { date: "2026-10-12", close: 5.25, observedAt: "2026-10-12T17:00:00-04:00", isProvisional: true, fetchStatus: "live" };
  const payload = (patch = {}) => ({ generatedAt: "2026-10-13 09:00:00 KST", liveSnapshots: { us10y_naver: { ...snapshot, ...patch } } });
  assert.equal(context.usableMarketLiveSnapshot(definition, payload(), confirmed).close, 5.25);
  for (const patch of [
    { date: "2026-10-09", observedAt: "2026-10-09T17:00:00-04:00" },
    { date: "2026-10-10", observedAt: "2026-10-10T17:00:00-04:00" },
    { observedAt: "2026-10-13T17:00:00-04:00" },
    { observedAt: "2026-10-12T17:00:00" }, { observedAt: "invalid" },
    { close: null }, { close: "" }, { close: Infinity }, { fetchStatus: "cache_fallback: timeout" }
  ]) assert.equal(context.usableMarketLiveSnapshot(definition, payload(patch), confirmed), null);
  const sameDate = { ...confirmed, date: snapshot.date };
  assert.equal(context.usableMarketLiveSnapshot(definition, payload(), sameDate), null);
  assert.equal(context.usableMarketLiveSnapshot(definition, payload({ fetchStatus: "derived: live + live" }), confirmed).close, 5.25);
});

test("ELS 수익률 월말 보정·전년말 기준·명시된 원천값 우선", () => {
  const { context } = harness();
  const item = { sixMonthPriceSeries: [
    { date: "2025-11-28", close: 100 }, { date: "2025-12-31", close: 110 },
    { date: "2026-02-27", close: 120 }, { date: "2026-03-02", close: 160 },
    { date: "2026-05-31", close: 132 }
  ] };
  const result = context.elsDerivedPeriodReturns(item);
  assert.ok(Math.abs(result.return3mPct - 10) < 1e-9);
  assert.ok(Math.abs(result.return6mPct - 32) < 1e-9);
  assert.ok(Math.abs(result.returnYtdPct - 20) < 1e-9);
  assert.equal(context.elsDerivedPeriodReturns({ sixMonthPriceSeries: item.sixMonthPriceSeries.slice(2) }).returnYtdPct, null);
  assert.equal(context.elsDerivedPeriodReturns({ ...item, metrics: { return3mPct: 0 } }).return3mPct, 0);
});

test("운영 화면은 계산시간과 게시시간·안내를 구분", () => {
  const { context } = harness();
  const payload = JSON.parse(readFileSync(join(root, "data/pipeline-status.json"), "utf8"));
  payload.lastVerifiedPublication = { mode: "live", publishedAt: "2026-10-13 09:02:02 KST", endToEndDurationSeconds: 114 };
  payload.quality.notices = [{ label: "AAPL", reason: "검증된 대체값", detail: "결측·중복 없음" }];
  payload.quality.summary.information = 1;
  const html = context.renderOperationsPage(payload);
  for (const expected of ["데이터 계산 완료", "최근 홈페이지 게시 확인", "09:02:02", "정상 대체값·휴장 내역 1건", "검증된 대체값"]) assert.ok(html.includes(expected));
  assert.doesNotMatch(html, /undefined|NaN/);
});

test("변화값 누락은 상승·하락 정렬 모두 마지막, 실제 0 변화는 유효", () => {
  const { context } = harness();
  const section = { indicators: [
    { id: "missing", value: 90 }, { id: "up", value: 55 },
    { id: "flat", value: 50 }, { id: "down", value: 45 }
  ] };
  const timeseries = { series: Object.fromEntries(["up", "flat", "down"].map((id) => [id, [
    { date: "2026-10-01", value: 50 }, { date: "2026-10-02", value: 50 }
  ]])) };
  const original = JSON.stringify(section);
  for (const [direction, expected] of [
    ["asc", ["down", "flat", "up", "missing"]],
    ["desc", ["up", "flat", "down", "missing"]]
  ]) {
    assert.deepEqual(Array.from(context.sortedIndicators(section, timeseries, "change1d", direction), (item) => item.id), expected);
  }
  assert.equal(JSON.stringify(section), original);
});

test("점수 없는 메모리도 가격·범위·공식 누적 수를 표시하고 원본 점수는 유지", () => {
  const { context } = harness();
  const section = { id: "market", indicators: [{ id: "other", value: 30 }] };
  const payload = JSON.parse(readFileSync(join(root, "data/dram-spot-prices.json"), "utf8"));
  payload.latest.score = null;
  payload.qualityChecks.officialObservationCount = 9;
  payload.qualityChecks.minimumScoreObservations = 21;
  const before = JSON.stringify({ section, payload });
  const html = context.renderPendingDramCard(section, payload);
  assert.match(html, /data-pending-dram/);
  assert.match(html, /9 \/ 21개/);
  assert.match(html, /종합점수 미반영/);
  assert.equal((html.match(/data-chart-tooltip-host/g) ?? []).length, 4);
  for (const range of ["1m", "3m", "ytd", "1y", "3y"]) {
    assert.match(html, new RegExp(`data-chart-range-layer="${range}"`));
  }
  assert.doesNotMatch(html, /NaN|undefined/);
  assert.equal(context.renderPendingDramCard(section, payload, "macro"), "");
  assert.match(context.renderPendingDramCard(section, payload, "ai_semi"), /data-pending-dram/);
  assert.equal(JSON.stringify({ section, payload }), before);
  assert.equal(context.renderPendingDramCard({ ...section, id: "credit" }, payload), "");
  assert.equal(context.renderPendingDramCard({ ...section, indicators: [{ id: "dram_spot_cycle_watch" }] }, payload), "");
  assert.equal(context.renderPendingDramCard(section, { ...payload, latest: { ...payload.latest, score: 0 } }), "");
});

test("게시 실행번호 혼합은 전체 묶음 재조회 후에만 렌더링", async () => {
  let round = 0;
  const { context, timers } = harness({
    fetch: async (url) => ({
      ok: true,
      json: async () => url.includes("publication-manifest")
        ? { status: "ready", runId: ++round === 1 ? "old" : "new", artifacts: [] }
        : { publication: { runId: "new" } }
    })
  });
  vm.runInContext("globalThis.rendered = 0; renderDashboard = () => { globalThis.rendered += 1; };", context);
  await context.startDashboard();
  assert.equal(round, 2);
  assert.equal(context.rendered, 1);
  assert.equal(timers.size, 0);
});

test("계속 불일치하는 게시 묶음은 세 번 뒤 중단하고 오류·복구 버튼 표시", async () => {
  let round = 0;
  const { context, app, errorNode, timers } = harness({
    fetch: async (url) => ({
      ok: true,
      json: async () => url.includes("publication-manifest")
        ? { status: "ready", runId: `run-${++round}`, artifacts: [] }
        : { publication: { runId: "mismatch" } }
    })
  });
  vm.runInContext("globalThis.rendered = 0; renderDashboard = () => { globalThis.rendered += 1; };", context);
  await context.startDashboard();
  assert.equal(round, 3);
  assert.equal(context.rendered, 0);
  assert.match(app.innerHTML, /data-reload-dashboard/);
  assert.match(errorNode.textContent, /게시 데이터 실행번호가 일치하지 않습니다/);
  assert.equal(timers.size, 0);
});

test("응답 없는 필수 요청을 취소하고 타이머 정리", async () => {
  const { context, timers } = harness({
    fetch: (_url, { signal }) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(Object.assign(new Error("timeout"), { name: "AbortError" })));
    })
  });
  const request = context.loadJson("./data/risk-dashboard.json", true);
  const rejected = assert.rejects(request, { name: "AbortError" });
  for (const abort of timers.values()) abort();
  await rejected;
  assert.equal(timers.size, 0);
});

test("ELS 고점 대비는 기간수익률·차트 이력과 별도로 원천 252D 낙폭 표시", () => {
  const { context } = harness();
  const cases = [[-25.82, "-25.82%"], [0, "0.00%"], [null, "–"], [undefined, "–"], ["", "–"], [NaN, "–"]];
  for (const [value, expected] of cases) {
    const asset = {
      id: "kospi200", label: "KOSPI200", lastDate: "2026-10-06",
      metrics: { drawdown252dPct: value, return1dPct: 4.2 },
      sixMonthPriceSeries: [{ date: "2026-10-05", close: 100 }, { date: "2026-10-06", close: 90 }]
    };
    const payload = { indices: [asset], singleStocks: [{ ...asset, id: "samsung", label: "삼성전자", assetType: "single-stock" }] };
    for (const range of ["1m", "3m", "6m", "ytd"]) {
      vm.runInContext(`activeElsPerformanceRange = "${range}"`, context);
      const html = context.renderElsPerformancePanel(payload);
      assert.match(html, /252D 종가/);
      for (const id of ["kospi200", "samsung"]) {
        const cell = html.match(new RegExp(`data-els-drawdown="${id}">([\\s\\S]*?)</td>`))[1];
        assert.ok(cell.includes(expected));
      }
    }
  }
});
