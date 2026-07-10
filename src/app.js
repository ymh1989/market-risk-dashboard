import { clampScore, evaluateDashboard } from "./risk-model.js";

const app = document.querySelector("#app");
const THEME_STORAGE_KEY = "risk-dashboard-theme";
const ASSET_VERSION = "20260710-3";

const trendLabel = {
  up: "상승",
  down: "하락",
  flat: "보합"
};

const formatScore = (value) => `${clampScore(value).toFixed(1)} / 100`;
const formatPct = (value) => `${Number(value).toFixed(2)}%`;
const formatSignedPct = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
};
const formatPointDelta = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}p`;
};
const formatShortDate = (value) => {
  if (!value) return "-";
  const date = new Date(`${value}T00:00:00Z`);
  return `${date.getUTCMonth() + 1}.${String(date.getUTCDate()).padStart(2, "0")}`;
};
const categoryCountText = (indicators) => {
  const counts = indicators.reduce((acc, indicator) => {
    acc[indicator.category] = (acc[indicator.category] ?? 0) + 1;
    return acc;
  }, {});

  return Object.entries(counts)
    .map(([category, count]) => `${category} ${count}`)
    .join(" · ");
};

function getStoredTheme() {
  return localStorage.getItem(THEME_STORAGE_KEY);
}

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  const resolvedTheme = theme === "dark" || theme === "light" ? theme : getSystemTheme();
  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.style.colorScheme = resolvedTheme;
}

function toggleTheme() {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  applyTheme(nextTheme);
  updateThemeButton(nextTheme);
}

function updateThemeButton(theme = document.documentElement.dataset.theme) {
  const button = document.querySelector("[data-theme-toggle]");
  if (!button) return;
  const isDark = theme === "dark";
  button.textContent = isDark ? "☀" : "◐";
  button.setAttribute("aria-label", isDark ? "라이트 모드로 전환" : "다크 모드로 전환");
  button.setAttribute("title", isDark ? "라이트 모드" : "다크 모드");
}

function versioned(path) {
  return `${path}?v=${ASSET_VERSION}`;
}

applyTheme(getStoredTheme());

function sparklinePath(points, width = 260, height = 62, padding = 5) {
  if (points.length < 2) return "";

  const values = points.map((point) => clampScore(point.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (points.length - 1);

  return points
    .map((point, index) => {
      const x = index * step;
      const y = height - padding - ((clampScore(point.value) - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function valueChange(currentValue, points, offset) {
  if (!points?.length || points.length <= offset) return null;
  const base = points[points.length - 1 - offset];
  if (!base) return null;
  return clampScore(currentValue) - clampScore(base.value);
}

function changeTone(value) {
  if (value === null || value === undefined || Math.abs(value) < 0.05) return "flat";
  return value > 0 ? "up" : "down";
}

function renderChangePills(currentValue, points) {
  const changes = [
    ["1D", valueChange(currentValue, points, 1)],
    ["1W", valueChange(currentValue, points, 5)],
    ["1M", valueChange(currentValue, points, 20)]
  ];

  return `
    <div class="change-pills" aria-label="점수 변화">
      ${changes
        .map(
          ([label, value]) => `
            <span class="change-pill change-pill--${changeTone(value)}">
              <small>${label}</small>
              <strong>${formatPointDelta(value)}</strong>
            </span>
          `
        )
        .join("")}
    </div>
  `;
}

function buildCompositeSeries(section, timeseries) {
  const indicators = section?.indicators ?? [];
  const weights = Object.fromEntries(indicators.map((indicator) => [indicator.id, Number(indicator.weight) || 0]));
  const dateScores = {};
  const dateWeights = {};

  Object.entries(timeseries?.series ?? {}).forEach(([indicatorId, points]) => {
    const weight = weights[indicatorId];
    if (!weight) return;
    points.forEach((point) => {
      dateScores[point.date] = (dateScores[point.date] ?? 0) + clampScore(point.value) * weight;
      dateWeights[point.date] = (dateWeights[point.date] ?? 0) + weight;
    });
  });

  const composite = Object.keys(dateScores)
    .sort()
    .filter((date) => dateWeights[date] > 0.7)
    .map((date) => ({ date, value: dateScores[date] / dateWeights[date] }));

  if (!composite.length) return composite;

  const latest = composite[composite.length - 1];
  const currentScore = Number(section.score);
  if (Number.isFinite(currentScore) && Math.abs(latest.value - currentScore) > 0.05) {
    composite.push({ date: section.asOf ?? latest.date, value: currentScore });
  }
  return composite;
}

function trendChartPath(points, width = 760, height = 210, padding = 18) {
  if (points.length < 2) return "";
  const values = points.map((point) => clampScore(point.value));
  const min = Math.max(0, Math.min(...values) - 5);
  const max = Math.min(100, Math.max(...values) + 5);
  const range = max - min || 1;
  const step = width / (points.length - 1);

  return points
    .map((point, index) => {
      const x = index * step;
      const y = height - padding - ((clampScore(point.value) - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function linePath(points, valueKey, width = 760, height = 210, padding = 18) {
  const valid = points.filter((point) => Number.isFinite(Number(point[valueKey])));
  if (valid.length < 2) return "";
  const values = valid.map((point) => Number(point[valueKey]));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (valid.length - 1);

  return valid
    .map((point, index) => {
      const x = index * step;
      const y = height - padding - ((Number(point[valueKey]) - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function datedLinePath(points, valueKey, startDate, endDate, width = 760, height = 210, padding = 18, domain = null) {
  const valid = points.filter((point) => Number.isFinite(Number(point[valueKey])));
  if (valid.length < 2) return "";
  const values = valid.map((point) => Number(point[valueKey]));
  const min = domain?.min ?? Math.min(...values);
  const max = domain?.max ?? Math.max(...values);
  const range = max - min || 1;
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  const dateRange = end - start || 1;

  return valid
    .map((point, index) => {
      const x = ((Date.parse(`${point.date}T00:00:00Z`) - start) / dateRange) * width;
      const y = height - padding - ((Number(point[valueKey]) - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function pearsonCorrelation(pairs) {
  if (pairs.length < 3) return null;
  const meanX = pairs.reduce((sum, pair) => sum + pair[0], 0) / pairs.length;
  const meanY = pairs.reduce((sum, pair) => sum + pair[1], 0) / pairs.length;
  const covariance = pairs.reduce((sum, pair) => sum + (pair[0] - meanX) * (pair[1] - meanY), 0);
  const varianceX = pairs.reduce((sum, pair) => sum + (pair[0] - meanX) ** 2, 0);
  const varianceY = pairs.reduce((sum, pair) => sum + (pair[1] - meanY) ** 2, 0);
  const denominator = Math.sqrt(varianceX * varianceY);
  return denominator > 0 ? covariance / denominator : null;
}

function buildLeadLagComparison(mlRisk, elsRisk, horizon = 5) {
  const kospi200 = elsRisk?.indices?.find((item) => item.id === "kospi200");
  const prices = (kospi200?.ytdPriceSeries ?? []).filter((point) => Number.isFinite(Number(point.close)));
  const oosSignals = (mlRisk?.walkForwardSeries ?? []).filter((point) => Number.isFinite(Number(point.crash5d5pctProbabilityPct)));
  if (prices.length < horizon + 2 || oosSignals.length < 3) return null;

  const base = Number(prices[0].close);
  const indexedPrices = prices.map((point) => ({
    date: point.date,
    kospi200YtdIndex: (Number(point.close) / base) * 100
  }));
  const signalByDate = new Map(oosSignals.map((point) => [point.date, Number(point.crash5d5pctProbabilityPct)]));
  const pairs = [];
  prices.forEach((point, index) => {
    const probability = signalByDate.get(point.date);
    if (probability === undefined || index + horizon >= prices.length) return;
    const forwardReturn = Number(prices[index + horizon].close) / Number(point.close) - 1;
    pairs.push([probability, forwardReturn]);
  });
  const signalEndDate = oosSignals[oosSignals.length - 1].date;
  const resultKnownThroughDate = oosSignals[oosSignals.length - 1].resultKnownThroughDate;
  const liveSignals = (mlRisk?.series ?? [])
    .filter((point) => Number.isFinite(Number(point.crash5d5pctProbabilityPct)) && point.date > signalEndDate)
    .map((point) => ({
      date: point.date,
      crash5d5pctProbabilityPct: Number(point.crash5d5pctProbabilityPct),
      crash5d10pctProbabilityPct: Number(point.crash5d10pctProbabilityPct)
    }));
  const pendingSignals = liveSignals.length ? [oosSignals[oosSignals.length - 1], ...liveSignals] : [];
  const signalValues = [...oosSignals, ...liveSignals].map((point) => Number(point.crash5d5pctProbabilityPct));
  const signalDomain = {
    min: Math.max(0, Math.min(...signalValues) - 3),
    max: Math.min(100, Math.max(...signalValues) + 3)
  };
  const chartStart = Date.parse(`${indexedPrices[0].date}T00:00:00Z`);
  const chartEnd = Date.parse(`${indexedPrices[indexedPrices.length - 1].date}T00:00:00Z`);
  const signalEndX = ((Date.parse(`${signalEndDate}T00:00:00Z`) - chartStart) / (chartEnd - chartStart || 1)) * 760;

  return {
    signalSeries: oosSignals,
    pendingSignalSeries: pendingSignals,
    priceSeries: indexedPrices,
    startDate: indexedPrices[0].date,
    endDate: indexedPrices[indexedPrices.length - 1].date,
    currentSignalDate: liveSignals.length ? liveSignals[liveSignals.length - 1].date : signalEndDate,
    correlation: pearsonCorrelation(pairs),
    observations: pairs.length,
    horizon,
    signalEndDate,
    resultKnownThroughDate,
    signalDomain,
    signalEndX: Math.max(0, Math.min(760, signalEndX))
  };
}

function scorePath(points, valueKey = "score", width = 760, height = 210, padding = 18) {
  const valid = points.filter((point) => Number.isFinite(Number(point[valueKey])));
  if (valid.length < 2) return "";
  const step = width / (valid.length - 1);

  return valid
    .map((point, index) => {
      const x = index * step;
      const y = height - padding - (clampScore(point[valueKey]) / 100) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function dateMs(date) {
  return Date.parse(`${date}T00:00:00Z`);
}

function timelineDomain(seriesList) {
  const dates = seriesList
    .flatMap((series) => series ?? [])
    .map((point) => dateMs(point.date))
    .filter((value) => Number.isFinite(value));
  if (!dates.length) return null;
  const start = Math.min(...dates);
  const end = Math.max(...dates);
  return { start, end, span: Math.max(end - start, 1) };
}

function xFromDate(date, domain, width = 100) {
  if (!domain) return 0;
  return ((dateMs(date) - domain.start) / domain.span) * width;
}

function scorePathByDate(points, valueKey = "score", width = 260, height = 62, padding = 7, domain = null) {
  const valid = points.filter((point) => Number.isFinite(Number(point[valueKey])) && Number.isFinite(dateMs(point.date)));
  const safeDomain = domain ?? timelineDomain([valid]);
  if (valid.length < 2 || !safeDomain) return "";

  return valid
    .map((point, index) => {
      const x = xFromDate(point.date, safeDomain, width);
      const y = height - padding - (clampScore(point[valueKey]) / 100) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function monthSegments(points, width = 760) {
  if (!points.length) return [];

  const groups = [];
  points.forEach((point, index) => {
    const [year, month] = point.date.split("-");
    const key = `${year}-${month}`;
    const current = groups[groups.length - 1];
    if (current?.key === key) {
      current.endIndex = index;
      return;
    }
    groups.push({ key, year, month, startIndex: index, endIndex: index });
  });

  const denominator = Math.max(points.length - 1, 1);
  return groups.map((group, index) => {
    const startX = group.startIndex === 0 ? 0 : ((group.startIndex - 0.5) / denominator) * width;
    const endX = group.endIndex === points.length - 1 ? width : ((group.endIndex + 0.5) / denominator) * width;
    return {
      ...group,
      startX,
      endX,
      centerX: (startX + endX) / 2,
      label:
        width <= 300
          ? group.month === "01"
            ? `${group.year.slice(2)}.01`
            : `${Number(group.month)}월`
          : index === 0 || group.month === "01"
            ? `${group.year}.${group.month}`
            : `${Number(group.month)}월`
    };
  });
}

function renderMonthAxis(points, width = 760, plotTop = 18, plotBottom = 190, labelY = 207) {
  const segments = monthSegments(points, width);
  return {
    grid: `
      ${segments
      .map(
        (segment, index) =>
          index % 2 === 1
            ? `<rect class="chart-month-band" x="${segment.startX.toFixed(2)}" y="${plotTop}" width="${(segment.endX - segment.startX).toFixed(2)}" height="${plotBottom - plotTop}"></rect>`
            : ""
      )
      .join("")}
    ${segments
      .slice(1)
      .map(
        (segment) =>
          `<line class="chart-month-divider" x1="${segment.startX.toFixed(2)}" x2="${segment.startX.toFixed(2)}" y1="${plotTop}" y2="${plotBottom}"></line>`
      )
      .join("")}
    `,
    labels: segments
      .map(
        (segment) =>
          `<text class="chart-month-label" x="${segment.centerX.toFixed(2)}" y="${labelY}" text-anchor="middle">${segment.label}</text>`
      )
      .join("")
  };
}

function monthSegmentsFromDomain(domain, width = 100) {
  if (!domain) return [];
  const startDate = new Date(domain.start);
  const endDate = new Date(domain.end);
  const cursor = new Date(Date.UTC(startDate.getUTCFullYear(), startDate.getUTCMonth(), 1));
  const segments = [];

  while (cursor.getTime() <= domain.end) {
    const next = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
    const startX = ((Math.max(cursor.getTime(), domain.start) - domain.start) / domain.span) * width;
    const endX = ((Math.min(next.getTime(), domain.end) - domain.start) / domain.span) * width;
    const month = String(cursor.getUTCMonth() + 1).padStart(2, "0");
    const year = String(cursor.getUTCFullYear());
    segments.push({
      key: `${year}-${month}`,
      startX,
      endX,
      centerX: (startX + endX) / 2,
      label: segments.length === 0 || month === "01" ? `${year}.${month}` : `${Number(month)}월`
    });
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }

  return segments.filter((segment) => segment.endX > segment.startX);
}

function renderElsIndexRiskPanel(elsRisk) {
  if (!elsRisk?.indices?.length || !elsRisk?.basket) return "";

  const sorted = [...elsRisk.indices].sort((a, b) => Number(b.score) - Number(a.score));
  const basket = elsRisk.basket;
  const monthAxis = renderMonthAxis(sorted[0].series ?? []);
  const colorClass = {
    spx: "els-line--spx",
    sx5e: "els-line--sx5e",
    nky: "els-line--nky",
    hscei: "els-line--hscei",
    kospi200: "els-line--kospi200"
  };

  return `
    <section class="els-index-panel">
      <div class="els-index-panel__header">
        <div>
          <span class="eyebrow">ELS Underlying Indices</span>
          <h2>기초지수별 ELS 리스크 판독</h2>
        </div>
        <div class="els-basket-state els-basket-state--${basket.tone}">
          <span>Worst-of Basket</span>
          <strong>${Number(basket.score).toFixed(1)}</strong>
          <small>${basket.bucket} · ${basket.worstIndex} 주도</small>
        </div>
      </div>

      <div class="els-index-summary">
        <article>
          <span class="eyebrow">Basket 해석</span>
          <h3>${basket.interpretation}</h3>
          <p>
            Basket 점수는 평균이 아니라 worst-of 구조를 반영합니다. 가장 높은 개별 리스크 점수에 50%,
            두 번째 취약 지수에 20%, 평균 점수와 동조화 점수에 각각 15%를 반영합니다.
          </p>
        </article>
        <article>
          <span class="eyebrow">동조화 점수</span>
          <h3>${Number(basket.correlationScore).toFixed(1)} / 100</h3>
          <p>
            최근 지수 간 수익률 상관이 높을수록 동시 순연 가능성과 헤지 비용 부담이 커집니다.
            현재 평균 개별 점수는 ${Number(basket.averageIndexScore).toFixed(1)}점입니다.
          </p>
        </article>
      </div>

      <div class="els-index-cards">
        ${sorted
          .map(
            (item) => `
              <article class="els-index-card els-index-card--${item.tone}">
                <header>
                  <div>
                    <span class="eyebrow">${item.region}</span>
                    <h3>${item.label}</h3>
                  </div>
                  <strong>${Number(item.score).toFixed(1)}</strong>
                </header>
                <div class="mini-bar" aria-hidden="true">
                  <span style="width:${clampScore(item.score)}%"></span>
                </div>
                <dl>
                  <div><dt>20D 수익률</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                  <div><dt>20D 변동성</dt><dd>${Number(item.metrics.realizedVol20dPct).toFixed(1)}%</dd></div>
                  <div><dt>252D 낙폭</dt><dd>${formatSignedPct(item.metrics.drawdown252dPct)}</dd></div>
                </dl>
                <p>${item.reading}</p>
              </article>
            `
          )
          .join("")}
      </div>

      <div class="els-index-chart" aria-label="기초지수별 ELS 리스크 점수 흐름">
        <svg viewBox="0 0 760 210" role="img">
          ${monthAxis.grid}
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          ${elsRisk.indices
            .map(
              (item) => `<path class="els-index-line ${colorClass[item.id] ?? ""}" d="${scorePath(item.series)}"></path>`
            )
            .join("")}
          ${monthAxis.labels}
        </svg>
        <div class="els-index-legend">
          ${elsRisk.indices
            .map((item) => `<span><i class="${colorClass[item.id] ?? ""}"></i>${item.label}</span>`)
            .join("")}
        </div>
      </div>
    </section>
  `;
}

function renderHmmMonthRail(domain) {
  const segments = monthSegmentsFromDomain(domain, 100);
  if (!segments.length) return "";

  return `
    <div class="hmm-regime-month-rail" aria-hidden="true">
      ${segments
      .map(
        (segment) => `
          <span style="left:${segment.startX.toFixed(2)}%;width:${(segment.endX - segment.startX).toFixed(2)}%">
            ${segment.label}
          </span>
        `
      )
      .join("")}
    </div>
  `;
}

function renderHmmRegimeStrip(points, domain) {
  if (!points?.length) return "";
  const valid = points
    .filter((point) => Number.isFinite(Number(point.issuerScore)) && Number.isFinite(dateMs(point.date)))
    .sort((a, b) => dateMs(a.date) - dateMs(b.date));
  if (!valid.length || !domain) return "";

  return `
    <div class="hmm-regime-strip">
      ${valid
      .map((point, index) => {
        const previous = valid[index - 1];
        const next = valid[index + 1];
        const currentX = xFromDate(point.date, domain, 100);
        const previousX = previous ? xFromDate(previous.date, domain, 100) : 0;
        const nextX = next ? xFromDate(next.date, domain, 100) : 100;
        const left = index === 0 ? Math.max(0, currentX) : (previousX + currentX) / 2;
        const right = index === valid.length - 1 ? Math.min(100, currentX) : (currentX + nextX) / 2;
        const safeLeft = Math.max(0, Math.min(100, left));
        const safeRight = Math.max(safeLeft + 0.35, Math.min(100, right));
        return `
          <span
            class="hmm-regime-strip__cell hmm-regime-strip__cell--${point.tone}"
            style="left:${safeLeft.toFixed(2)}%;width:${(safeRight - safeLeft).toFixed(2)}%"
            title="${point.date} · ${point.regime} · 부담 ${Number(point.issuerScore).toFixed(1)}"
          ></span>
        `;
      })
      .join("")}
    </div>
  `;
}

function renderHmmRegimePanel(hmmRegime) {
  if (!hmmRegime?.indices?.length || !hmmRegime?.basket) return "";

  const sorted = [...hmmRegime.indices].sort((a, b) => Number(b.issuerScore) - Number(a.issuerScore));
  const basket = hmmRegime.basket;
  const timelineDomainValue = timelineDomain(hmmRegime.indices.map((item) => item.series ?? []));
  const timelineAxis = renderHmmMonthRail(timelineDomainValue);
  const colorClass = {
    spx: "hmm-line--spx",
    sx5e: "hmm-line--sx5e",
    nky: "hmm-line--nky",
    hscei: "hmm-line--hscei",
    kospi200: "hmm-line--kospi200"
  };
  const regimeText = (item) =>
    `${item.regime} · 위험회피 ${Number(item.probabilities["위험회피"]).toFixed(1)}% · 활황 ${Number(item.probabilities["고변동성 활황"]).toFixed(1)}%`;

  return `
    <section class="hmm-regime-panel">
      <div class="hmm-regime-panel__header">
        <div>
          <span class="eyebrow">HMM Market Regime</span>
          <h2>국가별 3상태 HMM 레짐 판독</h2>
        </div>
        <div class="hmm-basket-state hmm-basket-state--${basket.tone}">
          <span>Cross-market HMM</span>
          <strong>${basket.regime}</strong>
          <small>위험회피 ${basket.riskOffCount} · 활황 ${basket.highVolBullCount} · 안정 ${basket.stableCount}</small>
        </div>
      </div>

      <div class="hmm-regime-summary">
        <article>
          <span class="eyebrow">개선 포인트</span>
          <h3>고변동성을 모두 주의로 보지 않습니다</h3>
          <p>${hmmRegime.designNote}</p>
        </article>
        <article>
          <span class="eyebrow">Basket 해석</span>
          <h3>${basket.interpretation}</h3>
          <p>
            최근 상태는 ${basket.regime}입니다. 평균 발행/헤지 부담 점수는 ${Number(basket.averageIssuerScore).toFixed(1)}점이고,
            위험회피 확률이 가장 높은 지수는 ${basket.highestRiskOffIndex}입니다.
          </p>
        </article>
      </div>

      <div class="hmm-regime-cards">
        ${sorted
          .map(
            (item) => `
              <article class="hmm-regime-card hmm-regime-card--${item.tone}">
                <header>
                  <div>
                    <span class="eyebrow">${item.region} · ${item.volSource}</span>
                    <h3>${item.label}</h3>
                  </div>
                  <strong>${item.regime}</strong>
                </header>
                <div class="mini-bar" aria-hidden="true">
                  <span style="width:${clampScore(item.issuerScore)}%"></span>
                </div>
                <dl>
                  <div><dt>부담점수</dt><dd>${Number(item.issuerScore).toFixed(1)}</dd></div>
                  <div><dt>위험회피</dt><dd>${Number(item.probabilities["위험회피"]).toFixed(1)}%</dd></div>
                  <div><dt>20D 수익률</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                  <div><dt>20D 변동성</dt><dd>${Number(item.metrics.realizedVol20dPct).toFixed(1)}%</dd></div>
                </dl>
                <p>${item.reading}</p>
                <small>${regimeText(item)} · 신뢰도 ${Number(item.confidencePct).toFixed(1)}%</small>
              </article>
            `
          )
          .join("")}
      </div>

      <div class="hmm-regime-timeline" aria-label="국가별 HMM 레짐 흐름">
        <div class="hmm-regime-timeline__header">
          <div>
            <span class="eyebrow">Regime Timeline</span>
            <h3>지수별 레짐 흐름</h3>
          </div>
          <div class="hmm-regime-key" aria-label="레짐 색상">
            <span><i class="hmm-regime-strip__cell--good"></i>안정</span>
            <span><i class="hmm-regime-strip__cell--caution"></i>고변동성 활황</span>
            <span><i class="hmm-regime-strip__cell--danger"></i>위험회피</span>
          </div>
        </div>

        ${timelineAxis}

        <div class="hmm-regime-rows">
          ${hmmRegime.indices
          .map(
            (item) => `
              <article class="hmm-regime-row hmm-regime-row--${item.tone}">
                <div class="hmm-regime-row__meta">
                  <strong>${item.label}</strong>
                  <span>${item.region} · ${item.volSource}</span>
                  <small>${item.regime} · 부담 ${Number(item.issuerScore).toFixed(1)}</small>
                </div>
                <div class="hmm-regime-row__track">
                  ${renderHmmRegimeStrip(item.series, timelineDomainValue)}
                  <svg viewBox="0 0 260 62" role="img" aria-label="${item.label} HMM 부담 점수">
                    <path class="hmm-regime-spark-grid" d="M 0 16 L 260 16 M 0 31 L 260 31 M 0 46 L 260 46"></path>
                    <path class="hmm-regime-spark ${colorClass[item.id] ?? ""}" d="${scorePathByDate(item.series, "issuerScore", 260, 62, 7, timelineDomainValue)}"></path>
                  </svg>
                </div>
                <dl class="hmm-regime-row__stats">
                  <div><dt>위험회피</dt><dd>${Number(item.probabilities["위험회피"]).toFixed(1)}%</dd></div>
                  <div><dt>활황</dt><dd>${Number(item.probabilities["고변동성 활황"]).toFixed(1)}%</dd></div>
                  <div><dt>20D</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                </dl>
              </article>
            `
          )
          .join("")}
        </div>
      </div>
    </section>
  `;
}

function renderMlRiskSignalPanel(mlRisk, market, elsRisk) {
  if (!mlRisk?.latest || !mlRisk?.series?.length) return "";

  const latest = mlRisk.latest;
  const series = mlRisk.series;
  const crash5pct = Number(latest.crash5d5pctProbabilityPct);
  const crash10pct = Number(latest.crash5d10pctProbabilityPct);
  const comparison = buildLeadLagComparison(mlRisk, elsRisk);
  const chartSeries = comparison?.priceSeries ?? series;
  const monthAxis = renderMonthAxis(chartSeries);
  const riskPath = comparison
    ? datedLinePath(comparison.signalSeries, "crash5d5pctProbabilityPct", comparison.startDate, comparison.endDate, 760, 210, 18, comparison.signalDomain)
    : linePath(series, "riskOffProbabilityPct");
  const pendingRiskPath = comparison
    ? datedLinePath(comparison.pendingSignalSeries, "crash5d5pctProbabilityPct", comparison.startDate, comparison.endDate, 760, 210, 18, comparison.signalDomain)
    : "";
  const kospi200Path = comparison
    ? datedLinePath(comparison.priceSeries, "kospi200YtdIndex", comparison.startDate, comparison.endDate)
    : "";
  const ml = mlRisk.metrics?.ml ?? {};
  const baseline = mlRisk.metrics?.baseline ?? {};
  const crash5pctMetrics = mlRisk.metrics?.crash5d5pct ?? {};
  const crash10pctMetrics = mlRisk.metrics?.crash5d10pct ?? {};
  const crash5pctValidated = Number(crash5pctMetrics.auc) >= 0.55 && Number(crash5pctMetrics.topDecileLift) > 1;
  const crash10pctValidated = Number(crash10pctMetrics.eventCount) >= 20 && Number(crash10pctMetrics.auc) >= 0.55 && Number(crash10pctMetrics.topDecileLift) > 1;
  const crash5pctCalibrated = Number(crash5pctMetrics.brier) <= Number(crash5pctMetrics.baselineBrier);
  const crash10pctCalibrated = Number(crash10pctMetrics.brier) <= Number(crash10pctMetrics.baselineBrier);
  const marketScore = Number(market?.score);
  const marketLevel = market?.level ?? { label: "확인 필요", tone: "watch" };
  const decisionThreshold = Number(mlRisk.thresholds?.riskOffDecisionThresholdPct);
  const riskTone =
    latest.riskOffProbabilityPct >= 75
      ? "danger"
      : latest.riskOffProbabilityPct >= 55 ||
          (Number.isFinite(decisionThreshold) && latest.riskOffProbabilityPct >= decisionThreshold)
        ? "caution"
        : "watch";
  const crashTone = (probability, metrics) => {
    const baseRatePct = Number(metrics.eventRate) * 100;
    const ratio = baseRatePct > 0 ? probability / baseRatePct : 0;
    return ratio >= 3 ? "danger" : ratio >= 1.5 ? "caution" : "watch";
  };
  const decisionText = Number.isFinite(decisionThreshold)
    ? `${latest.regime} 판정 · 임계치 ${decisionThreshold.toFixed(1)}%`
    : `모델 판정 ${latest.regime}`;
  const leadCorrelation = comparison?.correlation;
  const leadCorrelationText = Number.isFinite(leadCorrelation)
    ? `${leadCorrelation > 0 ? "+" : ""}${leadCorrelation.toFixed(2)}`
    : "산출 대기";
  const leadReading = !Number.isFinite(leadCorrelation)
    ? "워크포워드 관측치가 더 쌓이면 선행성을 계산합니다."
    : leadCorrelation <= -0.2
      ? "확률 상승 뒤 KOSPI200 수익률이 낮아지는 선행 패턴이 관찰됩니다."
      : leadCorrelation >= 0.2
        ? "현재 YTD 표본에서는 기대한 역방향 선행 패턴이 확인되지 않습니다."
        : "현재 YTD 표본의 선행 관계는 약합니다.";
  const divergenceText =
    `현재 스트레스는 ${marketLevel.label} 단계입니다. 향후 5거래일 내 현재가 대비 -5% 도달확률은 ${crash5pct.toFixed(1)}%, -10% 도달확률은 ${crash10pct.toFixed(1)}%이며, 기존 Risk-off ${Number(latest.riskOffProbabilityPct).toFixed(1)}%와 구분해서 봐야 합니다.`;

  return `
    <section class="ml-risk-panel">
      <div class="ml-risk-panel__header">
        <div>
          <span class="eyebrow">Current Stress vs Forward Risk</span>
          <h2>현재 스트레스와 향후 5일 급락 전망</h2>
        </div>
        <div class="ml-risk-state ml-risk-state--${marketLevel.tone}">
          <span>현재 시장 스트레스</span>
          <strong>${marketLevel.label} · ${Number.isFinite(marketScore) ? marketScore.toFixed(1) : "-"}</strong>
          <small>관측 지표 종합점수</small>
        </div>
      </div>

      <div class="ml-risk-horizons" aria-label="현재 스트레스와 미래 추가 악화 가능성 비교">
        <article>
          <span class="eyebrow">현재 · 관측값</span>
          <strong>${Number.isFinite(marketScore) ? `${marketScore.toFixed(1)} / 100` : "-"}</strong>
          <p>가격·변동성·환율·수급 등 지금 확인되는 시장 부담입니다.</p>
        </article>
        <div class="ml-risk-horizons__divider" aria-hidden="true"></div>
        <article>
          <span class="eyebrow">미래 · 5D -5% 도달 전망</span>
          <strong>${crash5pct.toFixed(1)}%</strong>
          <p>현재 수준에서 향후 5거래일 중 최저점이 -5% 이하에 도달할 확률입니다. ${crash5pctValidated ? "OOS 선별력이 확인됐습니다." : "현재 OOS 선별력이 확인되지 않아 연구 참고값으로만 봐야 합니다."}</p>
        </article>
      </div>

      <div class="ml-risk-grid">
        ${createMetricCard({
          label: "5D -5% 도달확률",
          value: `${crash5pct.toFixed(1)}%`,
          meta: `5일 내 최저수익률 -5% 이하 · ${crash5pctValidated ? "선별력 통과" : "연구 참고값"} · ${crash5pctCalibrated ? "확률 보정 양호" : "확률 보정 주의"}`,
          tone: crashTone(crash5pct, crash5pctMetrics)
        })}
        ${createMetricCard({
          label: "5D -10% 도달확률",
          value: `${crash10pct.toFixed(1)}%`,
          meta: `5일 내 최저수익률 -10% 이하 · ${crash10pctValidated ? "선별력 통과" : "희소사건 참고값"} · ${crash10pctCalibrated ? "확률 보정 양호" : "확률 보정 주의"}`,
          tone: crashTone(crash10pct, crash10pctMetrics)
        })}
        ${createMetricCard({
          label: "20D 레짐 Risk-off",
          value: `${Number(latest.riskOffProbabilityPct).toFixed(1)}%`,
          meta: `${decisionText} · 변동성·낙폭 포함`,
          tone: riskTone
        })}
        ${createMetricCard({
          label: "현재 20D 변동성",
          value: `${Number(latest.realizedVol20dPct).toFixed(1)}%`,
          meta: latest.baselineRiskOffSignal ? "현재 기준모델 risk-off" : "현재 기준모델 중립",
          tone: latest.realizedVol20dPct >= 35 ? "caution" : "watch"
        })}
      </div>

      <div class="ml-risk-body">
        <div class="ml-risk-chart" aria-label="워크포워드 5일 -5% 급락확률과 KOSPI200 YTD 선행성 비교">
          <div class="ml-risk-chart__header">
            <strong>5일 급락신호 → KOSPI200</strong>
            <span>현재 신호 ${formatShortDate(comparison?.currentSignalDate)} · OOS 결과 ${formatShortDate(comparison?.resultKnownThroughDate)}까지 확인</span>
          </div>
          <svg viewBox="0 0 760 210" role="img">
            ${comparison && comparison.signalEndX < 760 ? `<rect class="ml-risk-chart__pending" x="${comparison.signalEndX.toFixed(2)}" y="0" width="${(760 - comparison.signalEndX).toFixed(2)}" height="210"></rect><line class="ml-risk-chart__cutoff" x1="${comparison.signalEndX.toFixed(2)}" y1="0" x2="${comparison.signalEndX.toFixed(2)}" y2="210"></line>` : ""}
            ${monthAxis.grid}
            <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
            <path class="ml-risk-chart__risk" d="${riskPath}"></path>
            <path class="ml-risk-chart__risk-pending" d="${pendingRiskPath}"></path>
            <path class="ml-risk-chart__kospi200" d="${kospi200Path}"></path>
            ${monthAxis.labels}
          </svg>
          <div class="ml-risk-chart__legend">
            <span><i class="legend-risk"></i>ML 5D -5% 도달확률 · OOS 확인</span>
            <span><i class="legend-risk-pending"></i>현재까지의 최신 예측</span>
            <span><i class="legend-kospi200"></i>KOSPI200 · 연초=100</span>
            <span><i class="legend-pending"></i>향후 5거래일 결과 대기</span>
          </div>
          <p class="ml-risk-chart__note">5D 선행상관 ${leadCorrelationText} · ${comparison?.observations ?? 0}개 표본. ${leadReading} 점선 구간은 현재까지 관측된 신호지만, 향후 5거래일 결과가 아직 확정되지 않아 OOS 평가에서는 제외됩니다.</p>
        </div>

        <div class="ml-risk-explain">
          <strong>시간축을 나눠 읽으세요</strong>
          <p>${divergenceText}</p>
          <p>
            백테스트상 추가 악화 탐지모델은 recall ${Number((ml.riskOffRecall ?? 0) * 100).toFixed(1)}%,
            AUC ${Number(ml.riskOffAuc ?? 0).toFixed(3)}입니다. 기준모델 recall
            ${Number((baseline.riskOffRecall ?? 0) * 100).toFixed(1)}%, AUC ${Number(baseline.riskOffAuc ?? 0).toFixed(3)}보다 높아
            위험 구간을 놓치지 않는 능력은 개선됐지만, 확률 자체는 현재 스트레스 점수와 함께 판단해야 합니다.
          </p>
          <p>
            급락모델 OOS PR-AUC는 -5% ${Number(crash5pctMetrics.averagePrecision ?? 0).toFixed(3)},
            -10% ${Number(crash10pctMetrics.averagePrecision ?? 0).toFixed(3)}이며, 실제 급락 표본은 각각
            ${Number(crash5pctMetrics.eventCount ?? 0).toFixed(0)}건, ${Number(crash10pctMetrics.eventCount ?? 0).toFixed(0)}건입니다. 확률 상위 10% 구간의 급락 적중률은
            각각 ${Number((crash5pctMetrics.topDecileHitRate ?? 0) * 100).toFixed(1)}%,
            ${Number((crash10pctMetrics.topDecileHitRate ?? 0) * 100).toFixed(1)}%입니다.
            ${crash10pctValidated ? "-10% 급락확률도 OOS 선별력이 확인됐습니다." : "-10% 급락확률은 희소사건 표본이 적어 보조 경보로만 사용해야 합니다."}
            ${crash5pctCalibrated && crash10pctCalibrated ? "두 확률의 Brier score도 기준모델보다 양호합니다." : "Brier score가 기준모델보다 나쁜 확률은 정확한 발생빈도보다 위험 순위를 비교하는 용도로 해석해야 합니다."}
          </p>
          <ul>
            ${(mlRisk.interpretation ?? []).map((item) => `<li>${item}</li>`).join("")}
          </ul>
        </div>
      </div>
    </section>
  `;
}

function renderCompositeTrend(section, timeseries) {
  const points = buildCompositeSeries(section, timeseries);
  if (points.length < 2) return "";

  const latest = points[points.length - 1];
  const path = trendChartPath(points);
  const areaPath = `${path} L 760 190 L 0 190 Z`;
  const change1d = valueChange(latest.value, points, 1);
  const change1w = valueChange(latest.value, points, 5);
  const change1m = valueChange(latest.value, points, 20);
  const minPoint = points.reduce((min, point) => (point.value < min.value ? point : min), points[0]);
  const maxPoint = points.reduce((max, point) => (point.value > max.value ? point : max), points[0]);
  const monthAxis = renderMonthAxis(points);

  return `
    <section class="trend-panel">
      <div class="trend-panel__header">
        <div>
          <span class="eyebrow">Composite Trend</span>
          <h2>시장리스크 종합점수 흐름</h2>
        </div>
        <div class="trend-score">
          <strong>${formatScore(latest.value)}</strong>
          <span>${latest.date}</span>
        </div>
      </div>
      <div class="trend-kpis">
        <span class="change-pill change-pill--${changeTone(change1d)}"><small>1D</small><strong>${formatPointDelta(change1d)}</strong></span>
        <span class="change-pill change-pill--${changeTone(change1w)}"><small>1W</small><strong>${formatPointDelta(change1w)}</strong></span>
        <span class="change-pill change-pill--${changeTone(change1m)}"><small>1M</small><strong>${formatPointDelta(change1m)}</strong></span>
        <span><small>High</small><strong>${maxPoint.value.toFixed(1)}</strong></span>
        <span><small>Low</small><strong>${minPoint.value.toFixed(1)}</strong></span>
      </div>
      <div class="trend-chart" aria-label="시장리스크 종합점수 시계열">
        <svg viewBox="0 0 760 210" role="img">
          ${monthAxis.grid}
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          <path class="trend-chart__area" d="${areaPath}"></path>
          <path class="trend-chart__line" d="${path}"></path>
          ${monthAxis.labels}
        </svg>
      </div>
    </section>
  `;
}

function renderSparkline(indicator, timeseries) {
  const points = timeseries?.series?.[indicator.id] ?? [];
  if (points.length < 2) {
    return `<div class="sparkline sparkline--empty">시계열 준비중</div>`;
  }

  const last = points[points.length - 1];
  const change = clampScore(last.value) - clampScore(points[0].value);
  const trend = change > 3 ? "up" : change < -3 ? "down" : "flat";
  const path = sparklinePath(points, 260, 58, 5);
  const monthAxis = renderMonthAxis(points, 260, 5, 56, 72);

  return `
    <div class="sparkline sparkline--${trend}" aria-label="${indicator.name} 최근 점수 추세">
      <svg viewBox="0 0 260 76" role="img">
        ${monthAxis.grid}
        <path class="sparkline__baseline" d="M 0 55 L 260 55"></path>
        <path class="sparkline__line" d="${path}"></path>
        ${monthAxis.labels}
      </svg>
    </div>
  `;
}

function createMetricCard({ label, value, meta, tone = "neutral" }) {
  return `
    <article class="metric-card metric-card--${tone}">
      <span class="metric-card__label">${label}</span>
      <strong>${value}</strong>
      <span class="metric-card__meta">${meta}</span>
    </article>
  `;
}

function renderBacktestPanel(backtest) {
  if (!backtest?.byBucket) return "";

  const bucketOrder = ["정상", "관심", "주의", "경고"];
  const buckets = bucketOrder
    .filter((bucket) => backtest.byBucket[bucket])
    .map((bucket) => ({ name: bucket, ...backtest.byBucket[bucket] }));
  const samples = backtest.recentSamples ?? [];

  return `
    <section class="backtest-panel">
      <div class="backtest-panel__header">
        <div>
          <span class="eyebrow">Backtest</span>
          <h2>향후 20거래일 KOSPI 최대낙폭 진단</h2>
        </div>
        <div class="backtest-panel__meta">
          <strong>${backtest.sampleCount}</strong>
          <span>samples</span>
        </div>
      </div>

      <div class="backtest-grid">
        ${buckets
          .map(
            (bucket) => `
              <article class="backtest-card backtest-card--${bucket.name}">
                <div>
                  <span class="eyebrow">${bucket.count} samples</span>
                  <h3>${bucket.name}</h3>
                </div>
                <strong>${Number(bucket.hitRateDrawdownOver5Pct).toFixed(1)}%</strong>
                <div class="mini-bar" aria-hidden="true">
                  <span style="width:${clampScore(bucket.hitRateDrawdownOver5Pct)}%"></span>
                </div>
                <footer>
                  <span>평균 ${Number(bucket.avgForwardMaxDrawdownPct).toFixed(2)}%</span>
                  <span>최악 ${Number(bucket.worstForwardMaxDrawdownPct).toFixed(2)}%</span>
                </footer>
              </article>
            `
          )
          .join("")}
      </div>

      <div class="backtest-strip" aria-label="최근 백테스트 샘플">
        ${samples
          .map((sample) => {
            const drawdown = Math.abs(Math.min(0, Number(sample.forwardMaxDrawdownPct)));
            return `
              <span
                class="backtest-dot backtest-dot--${sample.bucket}"
                style="height:${Math.max(8, Math.min(52, drawdown * 3.2))}px"
                title="${sample.date} · ${sample.bucket} · ${Number(sample.forwardMaxDrawdownPct).toFixed(2)}%"
              ></span>
            `;
          })
          .join("")}
      </div>

      <div class="backtest-help">
        <strong>이렇게 읽으면 됩니다</strong>
        <p>
          최근 ${backtest.sampleCount}개 샘플은 각 거래일의 모델 점수를 정상·관심·주의·경고로 나눈 뒤,
          그 날 이후 20거래일 안에 KOSPI가 얼마나 크게 밀렸는지 되돌아본 조건부 진단입니다.
          hit-rate는 해당 구간에서 향후 20거래일 최대낙폭이 -5% 이하였던 비율이고,
          평균·최악 낙폭은 실제로 뒤따른 하락 강도를 보여줍니다.
        </p>
        <p>
          따라서 이 값은 “앞으로 반드시 하락한다”는 예측 확률이 아니라, 현재 점수 구간이 과거에는
          어느 정도의 후행 스트레스와 함께 나타났는지 보는 참고 지표입니다. 샘플 수가 적은 구간은
          방향성 위주로 보고, 2020년 이후 스트레스 구간 테스트와 함께 해석하는 편이 좋습니다.
        </p>
      </div>
    </section>
  `;
}

function renderStressEpisodesPanel(stressEpisodes) {
  const episodes = stressEpisodes?.episodes ?? [];
  if (!episodes.length) return "";

  return `
    <section class="stress-panel">
      <div class="backtest-panel__header">
        <div>
          <span class="eyebrow">Historical Stress</span>
          <h2>2020년 이후 주요 스트레스 구간</h2>
        </div>
        <div class="backtest-panel__meta">
          <strong>${stressEpisodes.episodeCount}</strong>
          <span>episodes</span>
        </div>
      </div>

      <div class="stress-grid">
        ${episodes
          .map(
            (episode) => `
              <article class="stress-card">
                <header>
                  <div>
                    <span class="eyebrow">${episode.startDate} - ${episode.endDate}</span>
                    <h3>${episode.label}</h3>
                  </div>
                  <strong>${formatScore(episode.peakScore)}</strong>
                </header>
                <div class="stress-card__metrics">
                  <span>고점대비 최대낙폭 <strong>-${formatPct(episode.kospiMaxDrawdownFromHighPct)}</strong></span>
                  <span>구간 저점 <strong>${formatPct(episode.kospiLowFromStartPct)}</strong></span>
                  <span>20D 선행 최대낙폭 <strong>${formatPct(episode.forward20dMaxDrawdownFromPeakPct)}</strong></span>
                </div>
                <div class="stress-card__bar" aria-hidden="true">
                  <span style="width:${clampScore(episode.peakScore)}%"></span>
                </div>
                <footer>
                  <span>피크 ${episode.peakDate}</span>
                  <span>${episode.tradingDays}거래일</span>
                </footer>
                <div class="stress-contributors">
                  ${(episode.topContributors ?? [])
                    .slice(0, 3)
                    .map(
                      (item) => `
                        <span title="${item.name}">
                          ${item.name}
                          <strong>+${Number(item.contribution).toFixed(2)}</strong>
                        </span>
                      `
                    )
                    .join("")}
                </div>
              </article>
            `
          )
          .join("")}
      </div>

      <div class="backtest-help">
        <strong>스트레스 구간 숫자 읽는 법</strong>
        <p>
          각 카드는 2020년 이후 같은 모델을 과거 데이터에 적용했을 때 스트레스 신호가 모였던 기간입니다.
          제목 옆 점수는 해당 구간에서 모델 점수가 가장 높았던 날의 피크 점수이고, 75점 이상이면 경고권으로 봅니다.
          “피크”는 그 점수가 가장 높았던 날짜, “14거래일”은 달력일이 아니라 실제 시장이 열린 날 기준으로 잡힌 구간 길이입니다.
        </p>
        <p>
          “고점대비 최대낙폭”은 그 구간 안에서 KOSPI가 직전 252거래일 고점 대비 얼마나 내려왔는지의 최대값이고,
          “구간 저점”은 구간 시작일 대비 가장 낮았던 KOSPI 수준입니다. “20D 선행 최대낙폭”은 피크 날짜 이후
          20거래일 동안 추가로 확인된 최대 하락폭입니다. 0.00%라면 피크 이후 20거래일 안에는 피크 당일보다
          더 낮은 KOSPI 저점이 나오지 않았다는 뜻입니다.
        </p>
      </div>
    </section>
  `;
}

function renderGauge(score, level, thresholds) {
  const safeScore = clampScore(score);
  return `
    <div class="gauge" aria-label="${level.label} ${formatScore(safeScore)}">
      <div class="gauge__track">
        ${thresholds
          .map(
            (threshold) =>
              `<span class="gauge__segment gauge__segment--${threshold.tone}" style="flex:${threshold.max - threshold.min}"></span>`
          )
          .join("")}
        <span class="gauge__marker" style="left:${safeScore}%"></span>
      </div>
      <div class="gauge__labels">
        ${thresholds.map((threshold) => `<span>${threshold.label}</span>`).join("")}
      </div>
    </div>
  `;
}

function renderIndicator(indicator, thresholds, timeseries) {
  const level = thresholds.find((threshold) => indicator.value >= threshold.min && indicator.value < threshold.max);
  const tone = level?.tone ?? "muted";
  const indicatorPoints = timeseries?.series?.[indicator.id] ?? [];

  return `
    <article class="indicator-card">
      <div>
        <span class="eyebrow">${indicator.category}</span>
        <h3>${indicator.name}</h3>
      </div>
      <div class="indicator-card__score">
        <strong>${formatScore(indicator.value)}</strong>
        <span class="status-pill status-pill--${tone}">${level?.label ?? "N/A"}</span>
      </div>
      <div class="contribution-line">
        <span>${indicator.group ?? "risk"}</span>
        <strong>기여도 +${Number(indicator.contribution ?? 0).toFixed(2)}점</strong>
      </div>
      ${renderChangePills(indicator.value, indicatorPoints)}
      <div class="mini-bar" aria-hidden="true">
        <span style="width:${clampScore(indicator.value)}%"></span>
      </div>
      ${renderSparkline(indicator, timeseries)}
      <p>${indicator.detail}</p>
      <footer>
        <span>${indicator.source}</span>
        <span>추세 ${trendLabel[indicator.trend] ?? "-"}</span>
      </footer>
    </article>
  `;
}

function renderSummary(data, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime) {
  const market = data.sections.find((section) => section.id === "market");
  market.asOf = data.metadata.asOf;
  const plannedLabels = data.sections
    .filter((section) => section.status !== "active")
    .map((section) => section.label)
    .join(", ");

  return `
    <section class="summary-grid">
      ${createMetricCard({
        label: "통합 경보등급",
        value: data.summary.level.label,
        meta: `활성 모듈 ${data.summary.activeCount}개 · 준비중 ${data.summary.plannedCount}개`,
        tone: data.summary.level.tone
      })}
      ${createMetricCard({
        label: "시장리스크 종합점수",
        value: formatScore(market.score),
        meta: `${market.level.label} · 기준일 ${data.metadata.asOf}`,
        tone: market.level.tone
      })}
      ${createMetricCard({
        label: "고위험 시장지표",
        value: `${market.highRiskCount}개`,
        meta: `총 ${market.indicators.length}개 지표 모니터링`,
        tone: market.highRiskCount > 0 ? "danger" : "good"
      })}
      ${createMetricCard({
        label: "확장 예정 모듈",
        value: plannedLabels || "없음",
        meta: "동일 데이터 스키마로 추가 가능",
        tone: "neutral"
      })}
    </section>

    <section class="narrative-band">
      <div>
        <span class="eyebrow">오늘의 Summary</span>
        <h2>${data.metadata.asOf} 현재 시장리스크는 ${market.level.label} 단계입니다.</h2>
      </div>
      <p>
        시장리스크 종합점수는 ${formatScore(market.score)}입니다. 현재 ${market.indicators.length}개 지표를
        ${categoryCountText(market.indicators)} 범주로 모니터링하며, 상위 리스크 지표는
        ${market.topIndicators.map((indicator) => indicator.name).join(", ")}입니다.
      </p>
    </section>

    ${renderMlRiskSignalPanel(mlRisk, market, elsRisk)}
    ${renderElsIndexRiskPanel(elsRisk)}
    ${renderHmmRegimePanel(hmmRegime)}
    ${renderCompositeTrend(market, timeseries)}
    ${renderBacktestPanel(backtest)}
    ${renderStressEpisodesPanel(stressEpisodes)}
  `;
}

function renderModelPanel(section) {
  const model = section.model ?? {};
  const normalization = model.normalization;
  const sources = model.dataSources ?? [];

  return `
    <section class="model-panel">
      <article>
        <span class="eyebrow">Model</span>
        <h3>${model.version ?? "risk-model"}</h3>
        <p>${model.methodology ?? "지표별 점수를 가중평균으로 합성합니다."}</p>
      </article>
      <article>
        <span class="eyebrow">Normalization</span>
        <h3>${normalization ? `${normalization.percentileWeight * 100}% 분위수 · ${normalization.zScoreWeight * 100}% z · ${normalization.robustZScoreWeight * 100}% robust z` : "Weighted score"}</h3>
        <p>${normalization ? `z-score는 ${normalization.zScoreMapping}, robust z-score는 ${normalization.robustZScore} 방식으로 ${normalization.scoreRange} 점수에 매핑합니다.` : "섹션 모델 설정을 사용합니다."}</p>
      </article>
      <article>
        <span class="eyebrow">Data</span>
        <div class="source-chips">
          ${sources.slice(0, 6).map((source) => `<span>${source}</span>`).join("")}
        </div>
      </article>
    </section>
  `;
}

function renderGroupScores(section) {
  const groups = section.groupScores ?? [];
  if (!groups.length) return "";

  return `
    <section class="group-panel">
      ${groups
        .map(
          (group) => `
            <article class="group-card">
              <div>
                <span class="eyebrow">${group.indicatorCount} indicators</span>
                <h3>${group.label}</h3>
              </div>
              <strong>${formatScore(group.score)}</strong>
              <div class="mini-bar" aria-hidden="true">
                <span style="width:${clampScore(group.score)}%"></span>
              </div>
              <footer>
                <span>비중 ${(group.weight * 100).toFixed(1)}%</span>
                <span>기여도 +${Number(group.contribution).toFixed(2)}점</span>
              </footer>
            </article>
          `
        )
        .join("")}
    </section>
  `;
}

function renderSection(section, timeseries, backtest, stressEpisodes) {
  const isPlanned = section.status !== "active";
  const sortedIndicators = [...(section.indicators ?? [])].sort((a, b) => clampScore(b.value) - clampScore(a.value));

  return `
    <section class="risk-section" data-panel="${section.id}">
      <div class="section-heading">
        <div>
          <span class="eyebrow">${section.owner}</span>
          <h2>${section.label}</h2>
          <p>${section.description}</p>
        </div>
        <div class="section-score">
          <span class="status-pill status-pill--${section.level.tone}">${section.level.label}</span>
          <strong>${formatScore(section.score)}</strong>
        </div>
      </div>

      ${
        isPlanned
          ? `<div class="empty-state">
              <h3>${section.label} 모듈 준비중</h3>
              <p>지표를 ${section.id} 섹션의 indicators 배열에 추가하고 탭 enabled 값을 true로 바꾸면 화면에 즉시 노출됩니다.</p>
            </div>`
          : `
            ${renderModelPanel(section)}
            ${renderGroupScores(section)}
            ${renderCompositeTrend(section.id === "market" ? section : null, timeseries)}
            ${renderBacktestPanel(section.id === "market" ? backtest : null)}
            ${renderStressEpisodesPanel(section.id === "market" ? stressEpisodes : null)}
            ${renderGauge(section.score, section.level, section.model.thresholds)}
            <div class="indicator-grid">
              ${sortedIndicators
                .map((indicator) => renderIndicator(indicator, section.model.thresholds, timeseries))
                .join("")}
            </div>
          `
      }

      <div class="action-panel">
        <h3>운영 기준</h3>
        <ul>
          ${section.actions.map((action) => `<li>${action}</li>`).join("")}
        </ul>
      </div>
    </section>
  `;
}

function renderDashboard(rawData, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime) {
  const data = evaluateDashboard(rawData);
  const enabledTabs = data.tabs.filter((tab) => tab.enabled);

  app.innerHTML = `
    <header class="hero">
      <div class="hero__content">
        <span class="eyebrow">Risk Monitoring</span>
        <h1>${data.metadata.title}</h1>
        <p>${data.metadata.subtitle}</p>
      </div>
      <div class="hero__aside">
        <span>기준일</span>
        <strong>${data.metadata.asOf}</strong>
        <small>${data.metadata.generatedAt}</small>
      </div>
    </header>

    <nav class="tabs" aria-label="리스크 대시보드 탭">
      <div class="tabs__items">
        ${data.tabs
          .map(
            (tab) => `
              <button class="tab-button ${tab.id === "summary" ? "is-active" : ""}" data-tab="${tab.id}" ${
                tab.enabled ? "" : "disabled"
              }>
                ${tab.label}
              </button>
            `
          )
          .join("")}
      </div>
      <button class="theme-toggle" type="button" data-theme-toggle aria-label="다크 모드로 전환" title="다크 모드">
        ◐
      </button>
    </nav>

    <div class="panel-stack">
      <section class="tab-panel is-active" data-panel="summary">
        ${renderSummary(data, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime)}
      </section>
      ${data.sections.map((section) => renderSection(section, timeseries, backtest, stressEpisodes)).join("")}
    </div>
  `;

  app.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.tab;
      if (!enabledTabs.some((tab) => tab.id === target)) return;

      app.querySelectorAll(".tab-button").forEach((tab) => tab.classList.toggle("is-active", tab === button));
      app
        .querySelectorAll("[data-panel]")
        .forEach((panel) => panel.classList.toggle("is-active", panel.dataset.panel === target));
    });
  });

  const themeButton = app.querySelector("[data-theme-toggle]");
  themeButton.addEventListener("click", toggleTheme);
  updateThemeButton();
}

Promise.all([
  fetch(versioned("./data/risk-dashboard.json")).then((response) => {
    if (!response.ok) throw new Error(`Dashboard data load failed: ${response.status}`);
    return response.json();
  }),
  fetch(versioned("./data/market-risk-timeseries.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch(versioned("./data/market-risk-backtest.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch(versioned("./data/market-stress-episodes.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch(versioned("./data/ml-risk-signal.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch(versioned("./data/els-index-risk.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch(versioned("./data/hmm-regime.json"))
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null)
])
  .then(([dashboard, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime]) =>
    renderDashboard(dashboard, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime)
  )
  .catch((error) => {
    app.innerHTML = `
      <div class="loading-panel loading-panel--error">
        <strong>대시보드를 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </div>
    `;
  });
