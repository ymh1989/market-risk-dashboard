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

function monthTicks(points, width = 760) {
  const seen = new Set();
  const ticks = [];

  points.forEach((point, index) => {
    const [year, month] = point.date.split("-");
    const key = `${year}-${month}`;
    if (seen.has(key)) return;
    seen.add(key);
    ticks.push({
      key,
      label: `${year.slice(2)}.${month}`,
      x: points.length > 1 ? (index / (points.length - 1)) * width : 0
    });
  });

  return ticks;
}

function renderMlRiskSignalPanel(mlRisk) {
  if (!mlRisk?.latest || !mlRisk?.series?.length) return "";

  const latest = mlRisk.latest;
  const series = mlRisk.series;
  const riskPath = linePath(series, "riskOffProbabilityPct");
  const volPath = linePath(series, "realizedVol20dPct");
  const returnPath = linePath(series, "kospiReturn20dPct");
  const ticks = monthTicks(series);
  const first = series[0];
  const last = series[series.length - 1];
  const ml = mlRisk.metrics?.ml ?? {};
  const baseline = mlRisk.metrics?.baseline ?? {};
  const riskTone = latest.riskOffProbabilityPct >= 75 ? "danger" : latest.riskOffProbabilityPct >= 55 ? "caution" : "watch";

  return `
    <section class="ml-risk-panel">
      <div class="ml-risk-panel__header">
        <div>
          <span class="eyebrow">ML Risk-off Signal</span>
          <h2>고변동성 활황 구간 진단</h2>
        </div>
        <div class="ml-risk-state ml-risk-state--${riskTone}">
          <span>${latest.interpretationLevel}</span>
          <strong>${Number(latest.riskOffProbabilityPct).toFixed(1)}%</strong>
        </div>
      </div>

      <div class="ml-risk-grid">
        ${createMetricCard({
          label: "ML Risk-off 확률",
          value: `${Number(latest.riskOffProbabilityPct).toFixed(1)}%`,
          meta: `${latest.regime} · 기준일 ${latest.date}`,
          tone: riskTone
        })}
        ${createMetricCard({
          label: "KOSPI 20D 모멘텀",
          value: formatSignedPct(latest.kospiReturn20dPct),
          meta: `KOSPI ${Number(latest.kospi).toLocaleString("ko-KR")}`,
          tone: latest.kospiReturn20dPct >= 0 ? "good" : "danger"
        })}
        ${createMetricCard({
          label: "20D 실현변동성",
          value: `${Number(latest.realizedVol20dPct).toFixed(1)}%`,
          meta: latest.baselineRiskOffSignal ? "변동성 기준 risk-off flag" : "변동성 기준 중립",
          tone: latest.realizedVol20dPct >= 35 ? "caution" : "watch"
        })}
        ${createMetricCard({
          label: "ELS 리스크 점수",
          value: Number(latest.elsRiskScore).toFixed(1),
          meta: latest.elsRiskBucket,
          tone: latest.elsRiskScore >= 60 ? "caution" : "watch"
        })}
      </div>

      <div class="ml-risk-body">
        <div class="ml-risk-chart" aria-label="ML risk-off 확률과 KOSPI 최근 흐름">
          <svg viewBox="0 0 760 210" role="img">
            <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
            <path class="ml-risk-chart__risk" d="${riskPath}"></path>
            <path class="ml-risk-chart__vol" d="${volPath}"></path>
            <path class="ml-risk-chart__return" d="${returnPath}"></path>
            ${ticks
              .map(
                (tick, index) => `
                  <g class="trend-chart__tick" transform="translate(${tick.x.toFixed(2)} 0)">
                    <line y1="194" y2="199"></line>
                    <text y="207" text-anchor="${index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}">${tick.label}</text>
                  </g>
                `
              )
              .join("")}
          </svg>
          <div class="ml-risk-chart__legend">
            <span><i class="legend-risk"></i>Risk-off 확률</span>
            <span><i class="legend-vol"></i>20D 변동성</span>
            <span><i class="legend-return"></i>20D 수익률</span>
          </div>
          <div class="trend-chart__meta">
            <span>${first.date}</span>
            <span>${series.length} observations</span>
            <span>${last.date}</span>
          </div>
        </div>

        <div class="ml-risk-explain">
          <strong>수치 해석</strong>
          <p>
            현재 신호는 약세장이 아니라, 주가 상승과 높은 변동성이 동시에 나타나는 구간으로 해석하는 편이 맞습니다.
            KOSPI 20D 모멘텀은 ${formatSignedPct(latest.kospiReturn20dPct)}지만 20D 실현변동성이
            ${Number(latest.realizedVol20dPct).toFixed(1)}%까지 올라와 ELS 관점의 risk-off 확률이 높아졌습니다.
          </p>
          <p>
            백테스트상 risk-off 이진모델은 recall ${Number((ml.riskOffRecall ?? 0) * 100).toFixed(1)}%,
            AUC ${Number(ml.riskOffAuc ?? 0).toFixed(3)}입니다. 기준모델 recall
            ${Number((baseline.riskOffRecall ?? 0) * 100).toFixed(1)}%, AUC ${Number(baseline.riskOffAuc ?? 0).toFixed(3)}보다 높아
            “위험 구간을 놓치지 않는 능력”은 개선됐지만, 활황장에서 과잉 경보 가능성은 함께 봐야 합니다.
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
  const first = points[0];
  const path = trendChartPath(points);
  const areaPath = `${path} L 760 210 L 0 210 Z`;
  const change1d = valueChange(latest.value, points, 1);
  const change1w = valueChange(latest.value, points, 5);
  const change1m = valueChange(latest.value, points, 20);
  const minPoint = points.reduce((min, point) => (point.value < min.value ? point : min), points[0]);
  const maxPoint = points.reduce((max, point) => (point.value > max.value ? point : max), points[0]);
  const ticks = monthTicks(points);

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
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          <path class="trend-chart__area" d="${areaPath}"></path>
          <path class="trend-chart__line" d="${path}"></path>
          ${ticks
            .map(
              (tick, index) => `
                <g class="trend-chart__tick" transform="translate(${tick.x.toFixed(2)} 0)">
                  <line y1="194" y2="199"></line>
                  <text y="207" text-anchor="${index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}">${tick.label}</text>
                </g>
              `
            )
            .join("")}
        </svg>
        <div class="trend-chart__meta">
          <span>${first.date}</span>
          <span>${points.length} observations</span>
          <span>${latest.date}</span>
        </div>
      </div>
    </section>
  `;
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

function renderSummary(data, timeseries, backtest, stressEpisodes, mlRisk) {
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

    ${renderMlRiskSignalPanel(mlRisk)}
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

function renderDashboard(rawData, timeseries, backtest, stressEpisodes, mlRisk) {
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
        ${renderSummary(data, timeseries, backtest, stressEpisodes, mlRisk)}
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
    .catch(() => null),
  fetch("./data/ml-risk-signal.json")
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null)
])
  .then(([dashboard, timeseries, backtest, stressEpisodes, mlRisk]) =>
    renderDashboard(dashboard, timeseries, backtest, stressEpisodes, mlRisk)
  )
  .catch((error) => {
    app.innerHTML = `
      <div class="loading-panel loading-panel--error">
        <strong>대시보드를 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </div>
    `;
  });
