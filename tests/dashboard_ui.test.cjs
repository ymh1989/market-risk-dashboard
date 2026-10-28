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
    URL,
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

function fundingFixture() {
  const dates = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"];
  const series = dates.map((date, index) => ({
    date, score: 40, rawScore: 42,
    amountsKrwMillion: {
      customerDeposits: (100 + index * 2) * 1e6,
      creditBalance: (40 - index) * 1e6, receivables: 1e6
    },
    derived: {creditToDepositsPct: 40}
  }));
  return {series, latest: series.at(-1)};
}

test("잔액 증감률은 직전·5거래 관측일 기준이며 휴일과 표시 기간에 독립적", () => {
  const {context} = harness();
  const payload = fundingFixture();
  const original = JSON.stringify(payload);
  const points = context.marketFundingChartPoints(payload);
  const change = points.at(-1).changes.customerDepositsTrillion;
  assert.equal(change.day.baseDate, "2026-10-23");
  assert.equal(change.week.baseDate, "2026-10-19");
  assert.ok(Math.abs(change.day.pct - (112 / 110 - 1) * 100) < 1e-10);
  assert.ok(Math.abs(change.week.pct - (112 / 102 - 1) * 100) < 1e-10);
  assert.ok(points.at(-1).changes.creditBalanceTrillion.week.pct < 0);
  assert.equal(points[0].changes.customerDepositsTrillion.day.pct, null);
  assert.equal(points[4].changes.customerDepositsTrillion.week.pct, null);
  assert.equal(points.at(-1).changes.receivablesTrillion.week.pct, 0);
  for (const range of ["1m", "3m", "ytd", "1y", "3y"]) {
    vm.runInContext(`activeChartRange = "${range}"`, context);
    context.renderFundingBalanceChart(payload);
    const model = vm.runInContext('[...interactiveChartRegistry.values()].findLast(model => model.series[0].valueKey === "customerDepositsTrillion")', context);
    assert.equal(model.series[0].points.at(-1).changes.customerDepositsTrillion.week.pct, change.week.pct);
    assert.match(model.series[0].detail(model.series[0].points.at(-1)), /전일 \+1\.82%.*10\.23.*1주 \+9\.80%.*10\.19/);
  }
  assert.equal(JSON.stringify(payload), original);
});

test("잔액 결측·0 기준값·이력 부족은 0%로 만들지 않고 비교 불가 처리", () => {
  const {context} = harness();
  for (const value of [null, undefined, "", NaN, Infinity, 0, -1]) {
    const payload = fundingFixture();
    payload.series[1].amountsKrwMillion.customerDeposits = value;
    const points = context.marketFundingChartPoints(payload);
    const change = points.at(-1).changes.customerDepositsTrillion.week;
    assert.equal(change.pct, null);
    assert.equal(change.baseDate, "2026-10-19");
    assert.match(context.fundingChangeDetail(points.at(-1), "customerDepositsTrillion"), /1주 비교 불가/);
  }
  const payload = fundingFixture();
  payload.latest.amountsKrwMillion.customerDeposits = null;
  payload.latest.score = null;
  payload.latest.derived.creditToDepositsPct = null;
  const point = context.marketFundingChartPoints(payload).at(-1);
  assert.equal(point.customerDepositsTrillion, null);
  assert.equal(point.score, null);
  assert.equal(point.creditToDepositsPct, null);
  assert.equal(point.changes.customerDepositsTrillion.day.pct, null);
  assert.doesNotMatch(context.renderDomesticFundingPanel(payload), /NaN|Infinity|undefined/);
});

test("잔액 이력은 날짜 정렬·중복 제거 후 비교하며 과거 증감률에 미래값 미사용", () => {
  const {context} = harness();
  const payload = fundingFixture();
  const expected = context.marketFundingChartPoints(payload);
  const shuffled = {series: [...payload.series].reverse().concat(payload.series[0])};
  assert.equal(JSON.stringify(context.marketFundingChartPoints(shuffled)), JSON.stringify(expected));
  const prefix = context.marketFundingChartPoints({series: payload.series.slice(0, 6)});
  assert.equal(JSON.stringify(prefix), JSON.stringify(expected.slice(0, 6)));
  payload.latest = payload.series[5];
  const html = context.renderDomesticFundingPanel(payload);
  assert.match(html, /전일 <b>\+1\.85%<\/b>/);
  assert.match(html, /1주 <b>\+10\.00%<\/b>/);
  assert.doesNotMatch(html, /전일 <b>\+1\.82%<\/b>/);
});

test("실제 잔액의 일간 증감률은 원장의 산출값과 일치하며 화면은 최신 1주 표시", () => {
  const {context} = harness();
  const payload = JSON.parse(readFileSync(join(root, "data/kb-market-funds.json"), "utf8"));
  const points = context.marketFundingChartPoints(payload);
  for (const [key, derivedKey] of [["customerDepositsTrillion", "customerDepositsChangePct"], ["creditBalanceTrillion", "creditBalanceChangePct"]]) {
    for (let i = Math.max(1, points.length - 10); i < points.length; i++) {
      const source = payload.series.find(row => row.date === points[i].date);
      assert.ok(Math.abs(points[i].changes[key].day.pct - source.derived[derivedKey]) < 1e-8);
    }
  }
  const html = context.renderDomesticFundingPanel(payload);
  assert.equal((html.match(/class="funding-balance-changes"/g) ?? []).length, 2);
  assert.match(html, /1주\(5거래 관측일\)/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("잔액 차트는 1주 증감 막대를 먼저 보여주고 전일·잔액 전환과 공통 기간 유지", () => {
  const {context} = harness();
  const html = context.renderFundingBalanceChart(fundingFixture());
  assert.match(html, /data-funding-active="week"/);
  assert.match(html, /data-funding-scale="focus"/);
  assert.match(html, /data-funding-scale-toggle checked/);
  assert.match(html, /data-funding-view="week"[^>]*aria-pressed="true"/);
  for (const view of ["day", "balance"]) assert.match(html, new RegExp(`data-funding-panel="${view}" hidden`));
  for (const range of ["1m", "3m", "ytd", "1y", "3y"]) assert.match(html, new RegExp(`data-chart-range-layer="${range}"`));
  assert.match(html, /data-funding-bars="week"/);
  assert.match(html, /data-funding-bars="day"/);
  assert.match(html, /고객예탁금 1주 대비 증감률 막대/);
  assert.match(html, /신용잔고 전일 대비 증감률 막대/);
  assert.match(html, /조회 기간 첫날을 100으로 환산/);
  assert.match(html, /두 잔액 동일 축/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("잔액 차트 이름·숫자·날짜는 SVG 축소와 분리하고 날짜에 연도 표시", () => {
  const {context} = harness();
  const html = context.renderFundingBalanceChart(fundingFixture());
  assert.match(html, /class="funding-series-name"><i[^>]*><\/i>고객예탁금/);
  assert.match(html, /class="funding-series-name"><i[^>]*><\/i>신용잔고/);
  assert.match(html, /class="chart-value-axis"/);
  assert.match(html, /class="chart-date-axis"/);
  for (const svg of html.matchAll(/<svg\b[^>]*>[\s\S]*?<\/svg>/g)) {
    assert.doesNotMatch(svg[0], /<text[^>]*>고객예탁금|<text[^>]*>신용잔고|breadth-chart__axis-value|chart-month-label/);
    assert.match(svg[0], /preserveAspectRatio="none"/);
  }
  const domain = context.chartRangeDomain([[{date: "2023-10-27"}, {date: "2026-10-27"}]], "3y");
  const axis = context.renderChartDateAxis(domain);
  assert.equal((axis.match(/<span /g) ?? []).length, 3);
  assert.match(axis, /2023\.10/);
  assert.match(axis, /2026\.10/);
  assert.doesNotMatch(context.renderChartDateAxis(null), /<span|NaN/);
  const charts = vm.runInContext("[...interactiveChartRegistry.values()]", context);
  for (const chart of charts) {
    assert.equal(chart.plotLeft, 0);
    assert.equal(chart.plotRight, chart.width);
  }
});

test("공통 날짜 축은 양 끝 날짜를 유지하고 실제 글씨 폭으로 겹침 방지", () => {
  const {context} = harness();
  for (const width of [180, 260, 500, 1000]) {
    const ticks = Array.from({length: 10}, (_, index) => ({x: width * index / 9, width: index === 0 || index === 9 ? 56 : 24}));
    const visible = context.layoutChartDateLabels(ticks, width);
    assert.equal(visible[0].index, 0);
    assert.equal(visible.at(-1).index, 9);
    visible.forEach((tick, index) => {
      assert.ok(tick.left >= 0 && tick.left + tick.width <= width);
      if (index) assert.ok(tick.left >= visible[index - 1].left + visible[index - 1].width + 12);
    });
  }
  assert.equal(context.layoutChartDateLabels([], 300).length, 0);
  assert.equal(context.layoutChartDateLabels([{x: 0, width: 50}], 300).length, 1);
});

test("내부강도 좌우 축은 공통 HTML 글씨를 쓰고 포인터 좌표는 그래프 전체 폭과 일치", () => {
  const {context} = harness();
  const breadth = JSON.parse(readFileSync(join(root, "data/kospi-breadth.json"), "utf8"));
  for (const html of [context.renderBreadthPriceChart(breadth), context.renderBreadthVkospiChart(breadth), context.renderFundingPressureChart(fundingFixture())]) {
    assert.match(html, /class="chart-axis-headings"/);
    assert.match(html, /class="chart-value-rail chart-value-rail--right"/);
    assert.match(html, /class="chart-date-axis"/);
    assert.doesNotMatch(html, /<text|NaN|Infinity|undefined/);
  }
  for (const chart of vm.runInContext("[...interactiveChartRegistry.values()]", context)) {
    assert.equal(chart.plotLeft, 0);
    assert.equal(chart.plotRight, 760);
  }
  const axis = context.renderChartValueAxis([100, 0, -100], value => `${value}%`, 20, 184, 210);
  assert.match(axis, /100%/);
  assert.match(axis, /-100%/);
  const positions = [...axis.matchAll(/top:([\d.]+)%/g)].map(match => Number(match[1]));
  assert.ok(Math.abs(positions[0] - 20 / 210 * 100) < 1e-9);
  assert.ok(Math.abs(positions[2] - 184 / 210 * 100) < 1e-9);
});

test("증감 막대는 0 기준에서 실제 비율에 비례하며 결측과 0을 구분", () => {
  const {context} = harness();
  const points = [
    {date: "2026-10-19", value: 10}, {date: "2026-10-20", value: -5},
    {date: "2026-10-21", value: 0}, {date: "2026-10-22", value: null},
    {date: "2026-10-23", value: 2}
  ];
  const domain = context.chartRangeDomain([points], "1m");
  const bars = context.fundingBarGeometry(points, "value", domain, 10, 690, 28, 128);
  assert.equal(bars.length, 4);
  assert.equal(bars[0].zeroY, 78);
  assert.equal(bars[0].y, 28);
  assert.equal(bars[0].height, 50);
  assert.equal(bars[1].y, 78);
  assert.equal(bars[1].height, 25);
  assert.equal(bars[2].height, 0);
  for (const bar of bars) assert.ok(bar.x >= 0 && bar.x + bar.width <= 690 && bar.y + bar.height <= 128);
  const empty = fundingFixture();
  empty.series.forEach(point => {point.amountsKrwMillion.customerDeposits = null;});
  const html = context.renderFundingChangeChart(context.marketFundingChartPoints(empty), "week");
  assert.match(html, /비교 가능한 잔액 부족/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("전체 보기만 공통 축을 쓰며 확대 여부와 무관하게 조회값은 원자료와 일치", () => {
  const {context} = harness();
  const payload = fundingFixture();
  const points = context.marketFundingChartPoints(payload);
  const html = context.renderFundingChangeChart(points, "week");
  const limits = [...html.matchAll(/data-funding-scale-layer="full" data-funding-limit="([^"]+)"/g)].map(match => Number(match[1]));
  for (let i = 0; i < limits.length; i += 2) assert.equal(limits[i], limits[i + 1]);
  const model = vm.runInContext("[...interactiveChartRegistry.values()].at(-1)", context);
  const point = model.series[0].points.at(-1);
  assert.equal(point[model.series[0].valueKey], points.at(-1).changes.customerDepositsTrillion.week.pct);
  assert.match(model.series[0].format(point[model.series[0].valueKey]), /\+9\.80%/);
  assert.match(model.series[0].detail(point), /112\.00조원.*1주 대비 2026-10-19/);
});

test("확대 축은 극단값에 덜 민감하고 범위 밖 값·부호·결측을 보존", () => {
  const {context} = harness();
  const values = [...Array(100).fill(1), -30, null, NaN];
  const limit = context.fundingBarLimit(values, true);
  assert.ok(limit >= 1 && limit < 2);
  assert.ok(context.fundingBarLimit(values) >= 30);
  for (const values of [[], [0], [null, NaN], [0.00001], [200]]) {
    assert.ok(Number.isFinite(context.fundingBarLimit(values, true)));
    assert.ok(context.fundingBarLimit(values, true) > 0);
  }
  const points = [
    {date: "2026-10-19", value: 30}, {date: "2026-10-20", value: -20},
    {date: "2026-10-21", value: 0.5}, {date: "2026-10-22", value: null}
  ];
  const domain = context.chartRangeDomain([points], "1m");
  const bars = context.fundingBarGeometry(points, "value", domain, 1, 690, 28, 168);
  assert.equal(bars.length, 3);
  assert.equal(bars[0].value, 30);
  assert.equal(bars[1].value, -20);
  assert.ok(bars[0].clipped && bars[1].clipped);
  assert.equal(bars[0].y, 28);
  assert.equal(bars[1].y + bars[1].height, 168);
  assert.equal(bars[2].clipped, false);
  assert.equal(bars[2].height, 35);
});

test("실제 YTD 확대 축은 두 잔액의 평소 변화를 키우고 초과일을 표시", () => {
  const {context} = harness();
  const payload = JSON.parse(readFileSync(join(root, "data/kb-market-funds.json"), "utf8"));
  const points = context.marketFundingChartPoints(payload);
  const visible = context.pointsWithinDomain(points, context.chartRangeDomain([points], "ytd"));
  for (const period of ["day", "week"]) {
    const values = visible.flatMap(point => ["customerDepositsTrillion", "creditBalanceTrillion"].map(key => point.changes[key][period].pct));
    const full = context.fundingBarLimit(values);
    for (const key of ["customerDepositsTrillion", "creditBalanceTrillion"]) {
      const focus = context.fundingBarLimit(visible.map(point => point.changes[key][period].pct), true);
      assert.ok(focus < full);
    }
    const html = context.renderFundingChangeChart(points, period);
    assert.match(html, /data-funding-overflow=/);
    assert.match(html, /지표별 축 · 절대 증감률 90% 분위 기준/);
  }
});

test("이슈 레이더는 외부 제목을 이스케이프하고 조회실패·기준일·범위를 표시", () => {
  const { context } = harness();
  const radar = { generatedAt: "2026-10-22T16:00:00+09:00", methodology: [], issues: [{
    id: "europe", title: "프랑스 <위험>", anchorDate: "2026-10-20", checkpoints: [],
    news: {status: "failed", label: "조회 실패", articles: [
      {title: '<img src=x onerror="alert(1)">', url: "javascript:alert(1)"},
      {title: "공개 뉴스", url: "https://example.com/news", publishedAt: "2026-10-22T10:00:00+09:00"}
    ]}, reading: {level: "경계", direction: "악화", partial: true},
    metrics: [{label: "국채 금리차", unit: "bp", value: 157.8, status: "ok", asOf: "2026-10-21", points: [
      {date: "2026-10-19", close: 150}, {date: "2026-10-21", close: 157.8}
    ]}]
  }]};
  const html = context.renderIssueRadar(radar);
  for (const text of ['data-chart-range-layer="3y"', 'data-chart-range-layer="ytd"', 'class="issue-anchor"',
    "157.8bp", "최신 집계 미확인", "일부 미확인", "첫 보도 전 대비", "&lt;img", 'href="https://example.com/news"']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /href="javascript:|<img|undefined|NaN/);
  assert.match(context.renderIssueRadar(radar, true), /data-issue-open="europe"/);
  assert.equal(context.radarSafeUrl("https://user:secret@example.com/"), "");
  assert.equal(context.radarSafeUrl("https://[::1]/"), "");
  assert.equal(context.renderIssueRadar(null), "");
});

test("로컬 AI 선택은 제공 기사만 표시하고 해설 숫자는 현재 원자료 사용", () => {
  const { context } = harness();
  const item = {news: {articles: [
    {id: "a", title: "정치 발언", publishedAt: "2026-10-22T12:00:00+09:00"},
    {id: "b", title: "프랑스 국채", publishedAt: "2026-10-22T11:00:00+09:00"}
  ]}, checkpoints: ["국채차 확대 여부"], metrics: [
    {id: "spread", label: "국채차", status: "ok", change1w: 16.3, riskSign: 1, threshold: 5, deltaUnit: "bp"}
  ], analysis: {status: "generated", model: "local", generatedAt: "2026-10-22T13:00:00+09:00", candidateCount: 2,
    selection: {articleIds: ["b"], metricIds: ["spread"], checkpointIndex: 0}}};
  assert.equal(context.issueArticles(item).length, 1);
  assert.equal(context.issueArticles(item)[0].id, "b");
  assert.match(context.renderIssueFocus(item), /\+16\.3bp · 부담 확대/);
  assert.match(context.renderIssueSelectionStatus(item), /로컬 AI 제목 선별 1\/2건/);
  item.metrics[0].change1w = -12;
  assert.match(context.renderIssueFocus(item), /-12\.0bp · 부담 완화/);
  item.analysis.selection = {articleIds: [], metricIds: [], checkpointIndex: null};
  assert.equal(context.issueArticles(item).length, 0);
  assert.equal(context.renderIssueFocus(item), "");
  item.analysis = {status: "fallback", reason: "unavailable"};
  assert.equal(context.issueArticles(item).length, 2);
  assert.match(context.renderIssueSelectionStatus(item), /키워드 선별/);
});

test("시장 이슈 탭은 상태를 분리하고 보관 자료·승인 이력을 표시", () => {
  const {context} = harness();
  const tabs = context.dashboardTabsWithIssues([{id: "summary"}, {id: "market"}]);
  assert.equal(tabs.at(-1).id, "market-issues");
  assert.equal(context.dashboardTabsWithIssues(tabs).length, 3);
  const item = {id: "event-ab12", title: "승인된 사건", themeId: "europe", rationale: "등록 근거",
    firstDetectedAt: "2026-10-21T10:00:00+09:00", registrationEvidence: [],
    news: {articles: []}, reading: {level: "보통", direction: "완화"}, metrics: [], checkpoints: [],
    lifecycle: {state: "archived", createdAt: "2026-10-21T10:00:00+09:00", updatedAt: "2026-10-23T10:00:00+09:00",
      history: [{at: "2026-10-23T10:00:00+09:00", state: "archived", reason: "<검토> 완료"}]}};
  const radar = {issues: [item], methodology: []};
  const html = context.renderIssueRadar(radar);
  for (const value of ['data-issue-state="active"', 'data-issue-state="watch"', 'data-issue-state="archived"',
    'data-issue-row="event-ab12"', 'data-issue-lifecycle="archived"', '보관 시점 자료', '&lt;검토&gt;', '등록 근거']) assert.ok(html.includes(value), value);
  assert.doesNotMatch(context.renderIssueRadar(radar, true), /data-issue-open/);
  assert.doesNotMatch(html, /ownerId|nonce|callback_data/);
});

test("유럽 10년 국채는 국가 간 금리차로 구분하고 bp 및 EOD 출처 표시", () => {
  const { context } = harness();
  const group = vm.runInContext('marketTrendGroups.find(group => group.id === "europe_rates")', context);
  assert.equal(group.items.length, 3);
  assert.equal(group.items[2].id, "fr_de_10y_spread");
  assert.equal(group.items[2].upLabel, "상대 부담 확대");
  assert.equal(context.formatMarketTrendValue(1.372, "spread"), "+137.2bp");
  assert.equal(context.marketTrendChange([{ close: 0 }, { close: -0.05 }], 1, "spread"), -5);
  const indicator = { id: "france_germany_spread_watch", source: "FR10YT=RR, DE10YT=RR" };
  const records = context.indicatorSourceRecords(indicator, {
    naverMarketIndexes: { fr10y_naver: { symbol: "FR10YT=RR", fetchStatus: "live" } }
  });
  assert.equal(context.sourceValueState(records[0]), "EOD");
  assert.match(records[0].url, /FR10YT%3DRR/);
  assert.match(context.sourceEnhancementNote(indicator), /TEC10·CDS/);
});

test("조달 관찰 원자료는 점수 0으로 위장하지 않고 공통 기간·단위를 표시", () => {
  const { context } = harness();
  const item = {
    id: "domestic_corporate_funding_watch", name: "국내 기업 조달경색", category: "관찰", group: "macro",
    value: null, weight: 0, asOf: "2026-10-15", source: "KB", sourceRecords: [],
    detail: ["금리 이력 24개"], normalizationNote: "최소 120개",
    observationCharts: [{label: "CP - CD", unit: "bp", points: [
      {date: "2026-10-14", close: 43}, {date: "2026-10-15", close: 44}
    ]}]
  };
  const section = {rawObservationCards: [item]};
  const html = context.renderRawObservationCards(section);
  assert.match(html, /44\.0bp/);
  assert.match(html, /원자료 관찰/);
  assert.match(html, /data-chart-range-layer="3y"/);
  assert.match(html, /data-chart-range-layer="ytd"/);
  assert.match(html, /data-chart-series-index="0"/);
  assert.doesNotMatch(html, /0\.0 \/ 100|undefined|NaN/);
  assert.equal(context.renderRawObservationCards(section, null, "flow"), "");
  assert.match(context.renderRawObservationCards(section, null, "macro"), /44\.0bp/);
});

test("신규 관찰카드 출처와 금리·은행주 보조값은 단위 및 조회실패 구분", () => {
  const { context } = harness();
  assert.equal(context.formatObservationValue(-1, "bp"), "-1.0bp");
  assert.equal(context.formatObservationValue(2.78, "%"), "2.78%");
  assert.equal(context.formatObservationValue(null, "bp"), "-");
  assert.match(context.sourceValueState({valueType: "eod", fetchStatus: "cached"}), /조회 실패/);
  const direct = {provider: "FRED", symbol: "DFII10", valueType: "eod"};
  assert.equal(context.indicatorSourceRecords({sourceRecords: [direct]}, {})[0], direct);
  const html = context.renderSupplementalObservation({
    supplementalNotes: ["은행 CDS와 별개"], supplementalSources: [{fetchStatus: "cached"}],
    supplementalMetrics: [{label: "은행주 20일 상대수익률", value: -9.26, unit: "%p"}]
  });
  assert.match(html, /-9\.3%p/);
  assert.match(html, /조회 실패/);
  assert.doesNotMatch(html, /undefined|NaN/);
});

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
