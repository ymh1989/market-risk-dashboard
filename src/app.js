import { clampScore, evaluateDashboard } from "./risk-model.js";

const app = document.querySelector("#app");
const THEME_STORAGE_KEY = "risk-dashboard-theme";

const trendLabel = {
  up: "상승",
  down: "하락",
  flat: "보합"
};

const formatScore = (value) => `${clampScore(value).toFixed(1)} / 100`;
const formatPct = (value) => `${Number(value).toFixed(2)}%`;
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

function renderSparkline(indicator, timeseries) {
  const points = timeseries?.series?.[indicator.id] ?? [];
  if (points.length < 2) {
    return `<div class="sparkline sparkline--empty">시계열 준비중</div>`;
  }

  const first = points[0];
  const last = points[points.length - 1];
  const change = clampScore(last.value) - clampScore(first.value);
  const trend = change > 3 ? "up" : change < -3 ? "down" : "flat";
  const path = sparklinePath(points);

  return `
    <div class="sparkline sparkline--${trend}" aria-label="${indicator.name} 최근 점수 추세">
      <svg viewBox="0 0 260 62" role="img">
        <path class="sparkline__baseline" d="M 0 55 L 260 55"></path>
        <path class="sparkline__line" d="${path}"></path>
      </svg>
      <div class="sparkline__meta">
        <span>${first.date}</span>
        <strong>${clampScore(last.value).toFixed(1)}</strong>
        <span>${last.date}</span>
      </div>
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

function renderSummary(data, backtest, stressEpisodes) {
  const market = data.sections.find((section) => section.id === "market");
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

function renderDashboard(rawData, timeseries, backtest, stressEpisodes) {
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
        ${renderSummary(data, backtest, stressEpisodes)}
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
  fetch("./data/risk-dashboard.json").then((response) => {
    if (!response.ok) throw new Error(`Dashboard data load failed: ${response.status}`);
    return response.json();
  }),
  fetch("./data/market-risk-timeseries.json")
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch("./data/market-risk-backtest.json")
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null),
  fetch("./data/market-stress-episodes.json")
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null)
])
  .then(([dashboard, timeseries, backtest, stressEpisodes]) =>
    renderDashboard(dashboard, timeseries, backtest, stressEpisodes)
  )
  .catch((error) => {
    app.innerHTML = `
      <div class="loading-panel loading-panel--error">
        <strong>대시보드를 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </div>
    `;
  });
