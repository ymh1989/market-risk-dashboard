import { clampScore, evaluateDashboard, isScoredIndicator } from "./risk-model.js";

const app = document.querySelector("#app");
const THEME_STORAGE_KEY = "risk-dashboard-theme";
const ASSET_VERSION = "20260722-3";
const DATA_REQUEST_VERSION = Date.now().toString(36);

const indicatorSortOptions = [
  { key: "score", label: "점수순", description: "현재 점수가 높은 지표부터 봅니다." },
  {
    key: "change1d",
    label: "1D",
    description: "전일 대비 점수 상승폭이 큰 지표부터 봅니다.",
    reverseDescription: "전일 대비 점수 하락폭이 큰 지표부터 봅니다.",
    offset: 1
  },
  {
    key: "change1w",
    label: "1W",
    description: "최근 5거래일 점수 상승폭이 큰 지표부터 봅니다.",
    reverseDescription: "최근 5거래일 점수 하락폭이 큰 지표부터 봅니다.",
    offset: 5
  },
  {
    key: "change1m",
    label: "1M",
    description: "최근 20거래일 점수 상승폭이 큰 지표부터 봅니다.",
    reverseDescription: "최근 20거래일 점수 하락폭이 큰 지표부터 봅니다.",
    offset: 20
  }
];

const trendLabel = {
  up: "상승",
  down: "하락",
  flat: "보합"
};

const sentimentGroupDefinitions = [
  { id: "crash", label: "가격 안정감", detail: "가격·변동성 스트레스의 반대 점수" },
  { id: "macro", label: "매크로 안정감", detail: "환율·금리·원자재 부담의 반대 점수" },
  { id: "ai_semi", label: "AI·반도체 심리", detail: "AI 수요와 반도체 집중 부담의 반대 점수" },
  { id: "flow", label: "수급 신뢰", detail: "외국인·시장 수급 압력의 반대 점수" },
  { id: "liquidity", label: "거래 안정감", detail: "거래량 과열·위축 부담의 반대 점수" },
  { id: "overheating", label: "과열 부담 완화", detail: "밸류에이션·쏠림 부담의 반대 점수" }
];

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
const inverseScore = (value) => clampScore(100 - clampScore(value));
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
  return `${path}?v=${ASSET_VERSION}&request=${DATA_REQUEST_VERSION}`;
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

function indicatorSortValue(indicator, timeseries, sortKey) {
  if (sortKey === "score") return clampScore(indicator.value);
  const option = indicatorSortOptions.find((item) => item.key === sortKey);
  if (!option?.offset) return clampScore(indicator.value);
  return valueChange(indicator.value, timeseries?.series?.[indicator.id] ?? [], option.offset);
}

function sortOptionLabel(option, active = false, direction = "desc") {
  if (!option.offset) return option.label;
  return `${option.label} ${active && direction === "asc" ? "하락" : "상승"}`;
}

function sortOptionDescription(option, active = false, direction = "desc") {
  if (option.offset && active && direction === "asc") return option.reverseDescription;
  return option.description;
}

function sortedIndicators(section, timeseries, sortKey = "score", direction = "desc") {
  return [...(section.indicators ?? [])].sort((a, b) => {
    const left = indicatorSortValue(a, timeseries, sortKey);
    const right = indicatorSortValue(b, timeseries, sortKey);
    const leftValid = Number.isFinite(Number(left));
    const rightValid = Number.isFinite(Number(right));
    if (leftValid !== rightValid) return leftValid ? -1 : 1;
    const leftRank = Number.isFinite(Number(left)) ? Number(left) : Number.NEGATIVE_INFINITY;
    const rightRank = Number.isFinite(Number(right)) ? Number(right) : Number.NEGATIVE_INFINITY;
    if (rightRank !== leftRank) return direction === "asc" ? leftRank - rightRank : rightRank - leftRank;
    return clampScore(b.value) - clampScore(a.value);
  });
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

function dashboardTabsWithSentiment(tabs) {
  if (tabs.some((tab) => tab.id === "sentiment")) return tabs;
  const summaryIndex = tabs.findIndex((tab) => tab.id === "summary");
  const insertAt = summaryIndex >= 0 ? summaryIndex + 1 : 0;
  const sentimentTab = { id: "sentiment", label: "시장 센티멘트", enabled: true };
  return [...tabs.slice(0, insertAt), sentimentTab, ...tabs.slice(insertAt)];
}

function dashboardTabsWithOperations(tabs) {
  const withSentiment = dashboardTabsWithSentiment(tabs);
  if (withSentiment.some((tab) => tab.id === "operations")) return withSentiment;
  const sentimentIndex = withSentiment.findIndex((tab) => tab.id === "sentiment");
  const insertAt = sentimentIndex >= 0 ? sentimentIndex + 1 : 1;
  const operationsTab = { id: "operations", label: "운영현황", enabled: true };
  return [...withSentiment.slice(0, insertAt), operationsTab, ...withSentiment.slice(insertAt)];
}

function dashboardTabsWithElsTool(tabs) {
  const withOperations = dashboardTabsWithOperations(tabs);
  if (withOperations.some((tab) => tab.id === "els-issuance")) return withOperations;
  const operationsIndex = withOperations.findIndex((tab) => tab.id === "operations");
  const insertAt = operationsIndex >= 0 ? operationsIndex + 1 : 2;
  const elsToolTab = { id: "els-issuance", label: "ELS 발행·헤지", enabled: true };
  return [...withOperations.slice(0, insertAt), elsToolTab, ...withOperations.slice(insertAt)];
}

function formatDurationSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "-";
  if (seconds < 60) return `${Math.round(seconds)}초`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}분 ${remainder}초` : `${minutes}분`;
}

function buildScheduleInstances(pipelineStatus) {
  const schedule = pipelineStatus?.schedule;
  if (!schedule?.times?.length) return [];
  const dayMs = 24 * 60 * 60 * 1000;
  const kstOffsetMs = 9 * 60 * 60 * 1000;
  const nowKst = new Date(Date.now() + kstOffsetMs);
  const baseDate = Date.UTC(nowKst.getUTCFullYear(), nowKst.getUTCMonth(), nowKst.getUTCDate());
  const instances = [];

  for (let offset = -8; offset <= 8; offset += 1) {
    const day = new Date(baseDate + offset * dayMs);
    const weekday = day.getUTCDay();
    if (schedule.weekdaysOnly && (weekday === 0 || weekday === 6)) continue;
    const year = day.getUTCFullYear();
    const month = day.getUTCMonth();
    const date = day.getUTCDate();
    const dateKey = `${year}-${String(month + 1).padStart(2, "0")}-${String(date).padStart(2, "0")}`;

    schedule.times.forEach((item) => {
      const [hour, minute] = String(item.time).split(":").map(Number);
      if (!Number.isFinite(hour) || !Number.isFinite(minute)) return;
      instances.push({
        ...item,
        dateKey,
        timestamp: Date.UTC(year, month, date, hour - 9, minute),
        label: `${String(month + 1).padStart(2, "0")}.${String(date).padStart(2, "0")} ${item.time}`
      });
    });
  }

  return instances.sort((left, right) => left.timestamp - right.timestamp);
}

function pipelineRuntimeState(pipelineStatus) {
  if (!pipelineStatus?.current) {
    return {
      label: "확인 필요",
      tone: "muted",
      detail: "운영 상태 파일을 불러오지 못했습니다.",
      latestSuccess: null,
      nextRun: null
    };
  }

  const now = Date.now();
  const history = pipelineStatus.history ?? [];
  const instances = buildScheduleInstances(pipelineStatus);
  const latestDue = [...instances].reverse().find((item) => item.timestamp <= now);
  const nextRun = instances.find((item) => item.timestamp > now) ?? null;
  const matchingRun = latestDue
    ? history.find(
        (item) =>
          item.status === "success" &&
          item.scheduledTime === latestDue.time &&
          String(item.startedAt ?? "").startsWith(latestDue.dateKey)
      )
    : null;
  const latestSuccess = history.find((item) => item.status === "success") ?? pipelineStatus.current;
  const sourceProblem = (pipelineStatus.sources ?? []).some((source) => source.status === "error");

  if (latestDue && !matchingRun) {
    const elapsedMinutes = Math.max(0, (now - latestDue.timestamp) / 60000);
    const expectedMinutes = Number(pipelineStatus.schedule?.expectedDurationMinutes?.[latestDue.mode] ?? 10);
    const graceMinutes = Number(pipelineStatus.schedule?.delayGraceMinutes ?? 5);
    if (elapsedMinutes <= expectedMinutes + graceMinutes) {
      return {
        label: "갱신 중",
        tone: "watch",
        detail: `${latestDue.time} ${latestDue.mode} 예약 작업의 완료 기록을 기다리고 있습니다.`,
        latestSuccess,
        nextRun
      };
    }
    return {
      label: "지연",
      tone: "caution",
      detail: `${latestDue.label} 예약 작업이 예상 완료시간을 지났습니다. 로컬 로그 확인이 필요합니다.`,
      latestSuccess,
      nextRun
    };
  }

  return {
    label: sourceProblem ? "일부 확인" : "정상",
    tone: sourceProblem ? "caution" : "good",
    detail: sourceProblem ? "일부 데이터 소스에 오류가 기록되어 있습니다." : pipelineStatus.current.message,
    latestSuccess,
    nextRun
  };
}

function renderOperationStatusStrip(pipelineStatus) {
  const state = pipelineRuntimeState(pipelineStatus);
  const latestSuccess = state.latestSuccess;
  return `
    <section class="operation-status-strip operation-status-strip--${state.tone}" aria-label="대시보드 운영 상태">
      <div class="operation-status-strip__state">
        <span class="operation-status-dot" aria-hidden="true"></span>
        <div>
          <small>운영 상태</small>
          <strong>${state.label}</strong>
        </div>
      </div>
      <div>
        <small>마지막 성공</small>
        <strong>${latestSuccess?.completedAt ?? "-"}</strong>
      </div>
      <div>
        <small>다음 예약</small>
        <strong>${state.nextRun ? `${state.nextRun.label} · ${state.nextRun.mode}` : "-"}</strong>
      </div>
      <div>
        <small>데이터 기준일</small>
        <strong>${latestSuccess?.dataAsOf ?? "-"}</strong>
      </div>
    </section>
  `;
}

function operationStatusLabel(status) {
  return {
    success: "성공",
    ok: "정상",
    warning: "확인",
    error: "오류"
  }[status] ?? "확인";
}

function renderOperationsPage(pipelineStatus) {
  const state = pipelineRuntimeState(pipelineStatus);
  if (!pipelineStatus?.current) {
    return `
      <section class="operations-page">
        <div class="empty-state">
          <h2>운영 상태를 불러오지 못했습니다.</h2>
          <p>pipeline-status.json 생성 여부를 확인하세요.</p>
        </div>
      </section>
    `;
  }

  const current = pipelineStatus.current;
  const scheduleText = (pipelineStatus.schedule?.times ?? [])
    .map((item) => `${item.time} ${item.mode}`)
    .join(" · ");

  return `
    <section class="operations-page">
      <header class="operations-heading">
        <div>
          <span class="eyebrow">Pipeline Operations</span>
          <h2>데이터·업데이트 운영현황</h2>
          <p>${state.detail}</p>
        </div>
        <div class="operations-current operations-current--${state.tone}">
          <small>현재 판정</small>
          <strong>${state.label}</strong>
          <span>${current.mode} · ${formatDurationSeconds(current.durationSeconds)}</span>
        </div>
      </header>

      <section class="operations-facts" aria-label="운영 요약">
        <div><small>마지막 성공</small><strong>${state.latestSuccess?.completedAt ?? "-"}</strong></div>
        <div><small>다음 예약</small><strong>${state.nextRun ? `${state.nextRun.label} · ${state.nextRun.mode}` : "-"}</strong></div>
        <div><small>평일 스케줄</small><strong>${scheduleText || "-"}</strong></div>
        <div><small>데이터 기준일</small><strong>${current.dataAsOf ?? "-"}</strong></div>
      </section>

      <section class="operations-section">
        <div class="operations-section__heading">
          <div><span class="eyebrow">Latest Run</span><h3>최근 실행 단계</h3></div>
          <span>${current.startedAt} → ${current.completedAt}</span>
        </div>
        <div class="pipeline-stage-list">
          ${(pipelineStatus.stages ?? [])
            .map(
              (stage, index) => `
                <article class="pipeline-stage pipeline-stage--${stage.status}">
                  <span class="pipeline-stage__index">${index + 1}</span>
                  <div><strong>${stage.label}</strong><p>${stage.detail}</p></div>
                  <div class="pipeline-stage__result">
                    <span>${operationStatusLabel(stage.status)}</span>
                    <strong>${formatDurationSeconds(stage.durationSeconds)}</strong>
                  </div>
                </article>
              `
            )
            .join("")}
        </div>
      </section>

      <section class="operations-section">
        <div class="operations-section__heading">
          <div><span class="eyebrow">Data Freshness</span><h3>데이터 소스</h3></div>
          <span>마지막 관측일 기준</span>
        </div>
        <div class="operations-table-wrap">
          <table class="operations-table">
            <thead><tr><th>소스</th><th>상태</th><th>최신일</th><th>시계열</th><th>범위</th></tr></thead>
            <tbody>
              ${(pipelineStatus.sources ?? [])
                .map(
                  (source) => `
                    <tr>
                      <td><strong>${source.label}</strong></td>
                      <td><span class="operation-table-status operation-table-status--${source.status}">${operationStatusLabel(source.status)}</span></td>
                      <td>${source.lastDate ?? "-"}</td>
                      <td>${source.seriesCount ?? "-"}개</td>
                      <td>${source.detail}</td>
                    </tr>
                  `
                )
                .join("")}
            </tbody>
          </table>
        </div>
      </section>

      <section class="operations-section operations-section--split">
        <div>
          <div class="operations-section__heading">
            <div><span class="eyebrow">Artifacts</span><h3>산출물</h3></div>
          </div>
          <div class="artifact-list">
            ${(pipelineStatus.artifacts ?? [])
              .map(
                (artifact) => `
                  <div><span>${artifact.label}</span><strong>${artifact.generatedAt ?? "-"}</strong></div>
                `
              )
              .join("")}
          </div>
        </div>
        <div>
          <div class="operations-section__heading">
            <div><span class="eyebrow">History</span><h3>최근 성공 이력</h3></div>
          </div>
          <div class="run-history-list">
            ${(pipelineStatus.history ?? [])
              .map(
                (run) => `
                  <div>
                    <span>${run.scheduledTime ?? "수동"} · ${run.mode}</span>
                    <strong>${run.completedAt}</strong>
                    <small>${formatDurationSeconds(run.durationSeconds)}</small>
                  </div>
                `
              )
              .join("")}
          </div>
        </div>
      </section>
    </section>
  `;
}

function buildSentimentSeries(section, timeseries) {
  return buildCompositeSeries(section, timeseries).map((point) => ({
    date: point.date,
    value: inverseScore(point.value)
  }));
}

function sentimentLevel(score) {
  if (score >= 65) {
    return {
      label: "Risk-on",
      tone: "good",
      reading: "가격과 수급이 위험선호에 우호적입니다. 과열 여부는 별도로 확인해야 합니다."
    };
  }
  if (score >= 50) {
    return {
      label: "중립 우위",
      tone: "watch",
      reading: "위험선호가 근소하게 우세하지만 방향성이 뚜렷하지 않은 구간입니다."
    };
  }
  if (score >= 35) {
    return {
      label: "Risk-off 경계",
      tone: "caution",
      reading: "시장 부담이 우세합니다. 반등 국면에서도 변동성과 수급 악화를 함께 확인해야 합니다."
    };
  }
  return {
    label: "Risk-off",
    tone: "danger",
    reading: "안전자산 선호와 방어적 포지셔닝이 우세한 구간입니다."
  };
}

function sentimentTone(score) {
  return sentimentLevel(score).tone;
}

function indicatorWeeklyChange(indicator, timeseries) {
  return valueChange(indicator.value, timeseries?.series?.[indicator.id] ?? [], 5);
}

function sentimentChangeTone(value) {
  if (value === null || value === undefined || Math.abs(value) < 0.05) return "flat";
  return value > 0 ? "up" : "down";
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

function smoothTrajectoryPoints(points) {
  if (points.length < 3) return points;

  return points.map((point, index) => {
    if (index === 0 || index === points.length - 1) return point;
    const start = Math.max(0, index - 2);
    const end = Math.min(points.length - 1, index + 2);
    let weightTotal = 0;
    let xTotal = 0;
    let yTotal = 0;

    for (let neighborIndex = start; neighborIndex <= end; neighborIndex += 1) {
      const weight = 3 - Math.abs(neighborIndex - index);
      weightTotal += weight;
      xTotal += points[neighborIndex].x * weight;
      yTotal += points[neighborIndex].y * weight;
    }

    return { ...point, x: xTotal / weightTotal, y: yTotal / weightTotal };
  });
}

function curvedTrajectoryPath(points) {
  const smoothed = smoothTrajectoryPoints(points);
  if (smoothed.length < 2) return "";
  if (smoothed.length === 2) {
    return `M ${smoothed[0].x.toFixed(1)} ${smoothed[0].y.toFixed(1)} L ${smoothed[1].x.toFixed(1)} ${smoothed[1].y.toFixed(1)}`;
  }

  const tension = 0.65;
  return smoothed
    .map((point, index) => {
      if (index === 0) return `M ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
      const previous = smoothed[index - 1];
      const beforePrevious = smoothed[index - 2] ?? previous;
      const next = smoothed[index + 1] ?? point;
      const control1 = {
        x: previous.x + ((point.x - beforePrevious.x) * tension) / 6,
        y: previous.y + ((point.y - beforePrevious.y) * tension) / 6
      };
      const control2 = {
        x: point.x - ((next.x - previous.x) * tension) / 6,
        y: point.y - ((next.y - previous.y) * tension) / 6
      };
      return `C ${control1.x.toFixed(1)} ${control1.y.toFixed(1)} ${control2.x.toFixed(1)} ${control2.y.toFixed(1)} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    })
    .join(" ");
}

function keyTrajectoryPath(points) {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
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

function scorePathByDatePlot(points, valueKey = "score", width = 260, plotTop = 26, plotBottom = 80, domain = null) {
  const valid = points.filter((point) => Number.isFinite(Number(point[valueKey])) && Number.isFinite(dateMs(point.date)));
  const safeDomain = domain ?? timelineDomain([valid]);
  if (valid.length < 2 || !safeDomain) return "";

  return valid
    .map((point, index) => {
      const x = xFromDate(point.date, safeDomain, width);
      const y = plotBottom - (clampScore(point[valueKey]) / 100) * (plotBottom - plotTop);
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

function renderElsStressEpisodeReview(stressEpisodes, plot) {
  const episodes = [...(stressEpisodes?.items ?? [])].sort(
    (a, b) => Number(b.marketPeakScore ?? 0) - Number(a.marketPeakScore ?? 0)
  );
  if (!episodes.length) return "";

  const configuredDefault = stressEpisodes.defaultEpisodeId;
  const defaultEpisodeId = episodes.some((episode) => episode.id === configuredDefault)
    ? configuredDefault
    : episodes[0].id;
  const ticks = [0, 25, 50, 75, 100];
  const gridLines = ticks
    .map((tick) => {
      const x = plot.left + (tick / 100) * plot.width;
      const y = plot.top + ((100 - tick) / 100) * plot.height;
      return `
        <path d="M ${x.toFixed(1)} ${plot.top} V ${plot.top + plot.height}" class="els-map-grid"></path>
        <path d="M ${plot.left} ${y.toFixed(1)} H ${plot.left + plot.width}" class="els-map-grid"></path>
        <text x="${x.toFixed(1)}" y="356" text-anchor="middle" class="els-map-tick">${tick}</text>
        <text x="52" y="${(y + 4).toFixed(1)}" text-anchor="end" class="els-map-tick">${tick}</text>
      `;
    })
    .join("");
  const markerOffsets = {
    spx: { dx: 10, dy: -10, anchor: "start" },
    sx5e: { dx: 10, dy: 18, anchor: "start" },
    nky: { dx: -10, dy: -10, anchor: "end" },
    hscei: { dx: -10, dy: 18, anchor: "end" },
    kospi200: { dx: 10, dy: -10, anchor: "start" }
  };
  const coordinate = (point) => ({
    ...point,
    x: plot.left + (clampScore(point.opportunityScore) / 100) * plot.width,
    y: plot.top + ((100 - clampScore(point.hedgeBurdenScore)) / 100) * plot.height
  });

  const panels = episodes
    .map((episode, episodeIndex) => {
      const active = episode.id === defaultEpisodeId;
      const markers = episode.items
        .map(
          (item) => `
            <marker id="els-episode-arrow-${episodeIndex}-${item.id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" class="els-map-arrowhead els-map-arrowhead--${item.id}"></path>
            </marker>
          `
        )
        .join("");
      const tracks = episode.items
        .map((item) => {
          const start = coordinate(item.start);
          const peak = coordinate(item.peak);
          const end = coordinate(item.end);
          const keyCoordinates = [start, peak, end].filter(
            (point, index, points) => index === 0 || point.date !== points[index - 1].date
          );
          if (keyCoordinates.length < 2) return "";
          const path = keyTrajectoryPath(keyCoordinates);
          const offset = markerOffsets[item.id] ?? { dx: 10, dy: -10, anchor: "start" };
          const endMarker = end.date === peak.date
            ? ""
            : `
              <circle cx="${end.x.toFixed(1)}" cy="${end.y.toFixed(1)}" r="5" class="els-episode-marker els-episode-marker--end">
                <title>${item.label} 종료 ${end.date}</title>
              </circle>
            `;
          return `
            <g class="els-episode-track els-episode-track--key-path els-map-trajectory-series els-map-trajectory-series--${item.id}">
              <path d="${path}" marker-end="url(#els-episode-arrow-${episodeIndex}-${item.id})">
                <title>${item.label} 핵심 경로 ${start.date}→${peak.date}→${end.date}: 최대 발행기회 ${Number(item.maxOpportunityScore).toFixed(1)}, 최대 헤지부담 ${Number(item.maxHedgeBurdenScore).toFixed(1)}</title>
              </path>
              <circle cx="${start.x.toFixed(1)}" cy="${start.y.toFixed(1)}" r="4" class="els-episode-marker els-episode-marker--start">
                <title>${item.label} 시작 ${start.date}</title>
              </circle>
              <rect x="${(peak.x - 4).toFixed(1)}" y="${(peak.y - 4).toFixed(1)}" width="8" height="8" transform="rotate(45 ${peak.x.toFixed(1)} ${peak.y.toFixed(1)})" class="els-episode-marker els-episode-marker--peak">
                <title>${item.label} 시장 정점 ${peak.date}: 기회 ${Number(peak.opportunityScore).toFixed(1)}, 부담 ${Number(peak.hedgeBurdenScore).toFixed(1)}</title>
              </rect>
              ${endMarker}
              <text x="${(end.x + offset.dx).toFixed(1)}" y="${(end.y + offset.dy).toFixed(1)}" text-anchor="${offset.anchor}">${item.label}</text>
            </g>
          `;
        })
        .join("");
      const peakScore = Number.isFinite(Number(episode.marketPeakScore))
        ? Number(episode.marketPeakScore).toFixed(1)
        : "-";

      return `
        <article class="els-episode-panel ${active ? "is-active" : ""}" data-els-episode-panel="${episode.id}">
          <div class="els-episode-summary">
            <div><span>구간</span><strong>${episode.startDate}~${episode.endDate}</strong></div>
            <div><span>시장 정점</span><strong>${episode.peakDate}</strong><small>스트레스 ${peakScore}</small></div>
            <div><span>정점 헤지부담</span><strong>${episode.peakBurdenIndex} ${Number(episode.peakBurdenScore).toFixed(1)}</strong></div>
            <div><span>정점 발행기회</span><strong>${episode.peakOpportunityIndex} ${Number(episode.peakOpportunityScore).toFixed(1)}</strong></div>
          </div>
          <p class="els-episode-interpretation">${episode.interpretation}</p>
          <div class="els-episode-map-scroll">
            <svg viewBox="0 0 760 410" role="img" aria-label="${episode.label} 기간의 ELS 발행기회와 헤지부담 이동">
              <defs>${markers}</defs>
              <rect x="${plot.left}" y="${plot.top}" width="${plot.width}" height="${plot.height}" class="els-map-zone els-map-zone--selective"></rect>
              <rect x="${plot.left + plot.width * 0.65}" y="${plot.top + plot.height * 0.55}" width="${plot.width * 0.35}" height="${plot.height * 0.45}" class="els-map-zone els-map-zone--opportunity"></rect>
              <rect x="${plot.left + plot.width * 0.65}" y="${plot.top + plot.height * 0.2}" width="${plot.width * 0.35}" height="${plot.height * 0.35}" class="els-map-zone els-map-zone--caution"></rect>
              <rect x="${plot.left}" y="${plot.top}" width="${plot.width}" height="${plot.height * 0.2}" class="els-map-zone els-map-zone--burden"></rect>
              ${gridLines}
              <path d="M ${plot.left + plot.width * 0.65} ${plot.top} V ${plot.top + plot.height}" class="els-map-threshold"></path>
              <path d="M ${plot.left} ${plot.top + plot.height * 0.55} H ${plot.left + plot.width}" class="els-map-threshold"></path>
              <path d="M ${plot.left} ${plot.top + plot.height * 0.2} H ${plot.left + plot.width}" class="els-map-threshold els-map-threshold--danger"></path>
              ${tracks}
              <text x="${plot.left + plot.width - 12}" y="${plot.top + 18}" text-anchor="end" class="els-map-zone-label">발행부담</text>
              <text x="${plot.left + plot.width - 12}" y="${plot.top + plot.height * 0.2 + 20}" text-anchor="end" class="els-map-zone-label">헤지주의</text>
              <text x="${plot.left + plot.width - 12}" y="${plot.top + plot.height - 12}" text-anchor="end" class="els-map-zone-label">발행기회</text>
              <text x="${plot.left + 12}" y="${plot.top + plot.height - 12}" class="els-map-zone-label">선별발행</text>
              <text x="${plot.left + plot.width / 2}" y="380" text-anchor="middle" class="els-map-axis-label">상대 발행기회 →</text>
              <text x="${plot.left + plot.width / 2}" y="398" text-anchor="middle" class="els-map-axis-note">변동성↑ 쿠폰↑</text>
              <text x="16" y="${plot.top + plot.height / 2}" text-anchor="middle" transform="rotate(-90 16 ${plot.top + plot.height / 2})" class="els-map-axis-label">헤지부담 →</text>
              <text x="34" y="${plot.top + plot.height / 2}" text-anchor="middle" transform="rotate(-90 34 ${plot.top + plot.height / 2})" class="els-map-axis-note">하락위험↑ 부담↑</text>
            </svg>
          </div>
        </article>
      `;
    })
    .join("");

  const indexLegend = episodes[0].items
    .map(
      (item) => `<span class="els-map-trajectory-series els-map-trajectory-series--${item.id}"><i></i>${item.label}</span>`
    )
    .join("");

  return `
    <section class="els-episode-review" data-els-episode-review>
      <header class="els-episode-review__header">
        <div>
          <span class="eyebrow">Historical Stress Replay</span>
          <h3>스트레스 에피소드 리플레이</h3>
        </div>
        <p>${stressEpisodes.methodology}</p>
      </header>
      <div class="els-episode-switcher" role="group" aria-label="스트레스 에피소드 선택">
        ${episodes
          .map(
            (episode) => `<button type="button" class="${episode.id === defaultEpisodeId ? "is-active" : ""}" data-els-episode="${episode.id}" aria-pressed="${episode.id === defaultEpisodeId ? "true" : "false"}">${episode.label}</button>`
          )
          .join("")}
      </div>
      ${panels}
      <footer class="els-episode-legend">
        <div class="els-episode-stage-legend">
          <span><i class="els-episode-legend-start"></i>시작</span>
          <span><i class="els-episode-legend-peak"></i>시장 정점</span>
          <span><i class="els-episode-legend-end"></i>종료</span>
        </div>
        <div class="els-episode-index-legend">${indexLegend}</div>
      </footer>
    </section>
  `;
}

function renderElsIssuanceHedgePage(elsRisk) {
  const map = elsRisk?.issuanceHedgeMap;
  if (!map?.items?.length || !map?.basket) {
    return `
      <section class="els-issuance-page">
        <div class="empty-state">
          <h3>ELS 발행·헤지 데이터 준비중</h3>
          <p>다음 데이터 갱신에서 기초지수별 상대 발행기회와 헤지부담을 계산합니다.</p>
        </div>
      </section>
    `;
  }

  const plot = { left: 66, top: 24, width: 654, height: 310 };
  const markerOffsets = {
    spx: { dx: 10, dy: -10, anchor: "start" },
    sx5e: { dx: 10, dy: -10, anchor: "start" },
    nky: { dx: 10, dy: 20, anchor: "start" },
    hscei: { dx: 10, dy: 20, anchor: "start" },
    kospi200: { dx: -10, dy: 20, anchor: "end" }
  };
  const points = map.items
    .map((item) => {
      const opportunity = clampScore(item.opportunityScore);
      const burden = clampScore(item.hedgeBurdenScore);
      const x = plot.left + (opportunity / 100) * plot.width;
      const y = plot.top + ((100 - burden) / 100) * plot.height;
      const offset = markerOffsets[item.id] ?? { dx: 10, dy: -10, anchor: "start" };
      return `
        <g class="els-map-point els-map-point--${item.id}">
          <title>${item.label}: 발행기회 ${opportunity.toFixed(1)}, 헤지부담 ${burden.toFixed(1)}</title>
          <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7"></circle>
          <text x="${(x + offset.dx).toFixed(1)}" y="${(y + offset.dy).toFixed(1)}" text-anchor="${offset.anchor}">${item.label}</text>
        </g>
      `;
    })
    .join("");
  const trajectoryWindows = [
    {
      id: "1w",
      label: "1주",
      points: Number(map.trajectoryWindows?.oneWeekPoints ?? 5),
      momentum: true
    },
    { id: "1m", label: "1개월", points: Number(map.trajectoryWindows?.oneMonthPoints ?? 22), momentum: false },
    { id: "3m", label: "3개월", points: Number(map.trajectoryWindows?.threeMonthPoints ?? 66), momentum: false }
  ];
  const trajectoryLayers = trajectoryWindows
    .map((window) => {
      const tracks = map.items
        .map((item) => {
          const history = (item.trajectory ?? [])
            .filter(
              (point) =>
                Number.isFinite(Number(point.opportunityScore)) && Number.isFinite(Number(point.hedgeBurdenScore))
            )
            .slice(-window.points);
          if (history.length < 2) return "";

          const coordinates = history.map((point) => ({
            ...point,
            x: plot.left + (clampScore(point.opportunityScore) / 100) * plot.width,
            y: plot.top + ((100 - clampScore(point.hedgeBurdenScore)) / 100) * plot.height
          }));
          const path = curvedTrajectoryPath(coordinates);
          const start = coordinates[0];
          const end = coordinates[coordinates.length - 1];

          return `
            <g class="els-map-trajectory-series els-map-trajectory-series--${item.id}">
              <path d="${path}" class="els-map-trajectory"${window.momentum ? ` marker-end="url(#els-map-arrow-${item.id})"` : ""}>
                <title>${item.label} ${start.date}~${end.date}: 발행기회 ${Number(start.opportunityScore).toFixed(1)}→${Number(end.opportunityScore).toFixed(1)} (${formatPointDelta(Number(end.opportunityScore) - Number(start.opportunityScore))}), 헤지부담 ${Number(start.hedgeBurdenScore).toFixed(1)}→${Number(end.hedgeBurdenScore).toFixed(1)} (${formatPointDelta(Number(end.hedgeBurdenScore) - Number(start.hedgeBurdenScore))})</title>
              </path>
              <circle cx="${start.x.toFixed(1)}" cy="${start.y.toFixed(1)}" r="4" class="els-map-trajectory-start">
                <title>${item.label} 시작 ${start.date}: 기회 ${Number(start.opportunityScore).toFixed(1)}, 부담 ${Number(start.hedgeBurdenScore).toFixed(1)}</title>
              </circle>
            </g>
          `;
        })
        .join("");
      return `<g class="els-map-trajectories ${window.momentum ? "els-map-trajectories--momentum is-visible" : ""}" data-els-trajectory="${window.id}">${tracks}</g>`;
    })
    .join("");
  const trajectoryArrowMarkers = map.items
    .map(
      (item) => `
        <marker id="els-map-arrow-${item.id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" class="els-map-arrowhead els-map-arrowhead--${item.id}"></path>
        </marker>
      `
    )
    .join("");
  const ticks = [0, 25, 50, 75, 100];
  const gridLines = ticks
    .map((tick) => {
      const x = plot.left + (tick / 100) * plot.width;
      const y = plot.top + ((100 - tick) / 100) * plot.height;
      return `
        <path d="M ${x.toFixed(1)} ${plot.top} V ${plot.top + plot.height}" class="els-map-grid"></path>
        <path d="M ${plot.left} ${y.toFixed(1)} H ${plot.left + plot.width}" class="els-map-grid"></path>
        <text x="${x.toFixed(1)}" y="356" text-anchor="middle" class="els-map-tick">${tick}</text>
        <text x="52" y="${(y + 4).toFixed(1)}" text-anchor="end" class="els-map-tick">${tick}</text>
      `;
    })
    .join("");

  return `
    <section class="els-issuance-page">
      <header class="els-issuance-heading">
        <div>
          <span class="eyebrow">ELS Issuance Opportunity &amp; Hedge Burden</span>
          <h2>ELS 발행기회·헤지부담 맵</h2>
          <p>높아진 변동성이 제공하는 상대 발행기회와 기존 북의 순연·헤지비용 부담을 분리해 봅니다.</p>
        </div>
        <div class="els-basket-state els-basket-state--${map.basket.tone}">
          <span>Basket 판단</span>
          <strong>${map.basket.stance}</strong>
          <small>기회 ${Number(map.basket.opportunityScore).toFixed(1)} · 부담 ${Number(map.basket.hedgeBurdenScore).toFixed(1)}</small>
        </div>
      </header>

      <div class="els-issuance-facts">
        <div><span>상대 발행기회</span><strong>${Number(map.basket.opportunityScore).toFixed(1)}</strong><small>실제 쿠폰 추정값 아님</small></div>
        <div><span>헤지부담</span><strong>${Number(map.basket.hedgeBurdenScore).toFixed(1)}</strong><small>낙폭·변동성·동조화 합성</small></div>
        <div><span>기회 상위</span><strong>${map.basket.topOpportunityIndex}</strong><small>변동성 상대가치 기준</small></div>
        <div><span>부담 상위</span><strong>${map.basket.topBurdenIndex}</strong><small>기존 북 관리 우선</small></div>
      </div>

      <section class="els-opportunity-map els-opportunity-map--current" data-els-map>
        <div class="els-opportunity-map__header">
          <div>
            <span class="eyebrow">Current Positioning</span>
            <h3>현재 기초지수 포지셔닝</h3>
          </div>
          <div class="els-opportunity-map__aside">
            <p>${map.basket.interpretation}</p>
            <div class="els-opportunity-map__tools">
              <div class="els-trajectory-toggle" role="group" aria-label="궤적 조회 기간">
                ${trajectoryWindows
                  .map(
                    (window) => `
                      <button type="button" class="${window.momentum ? "is-active" : ""}" data-els-window="${window.id}" aria-pressed="${window.momentum ? "true" : "false"}">${window.label}</button>
                    `
                  )
                  .join("")}
              </div>
              <div class="els-trajectory-legend" aria-label="궤적 범례">
                <span><i class="els-trajectory-legend__start"></i>시작</span>
                <span><i class="els-trajectory-legend__current"></i>현재</span>
                <span data-els-momentum-legend><i class="els-trajectory-legend__momentum"></i>1주 방향</span>
              </div>
            </div>
          </div>
        </div>
        <div class="els-opportunity-map__scroll">
          <svg viewBox="0 0 760 410" role="img" aria-label="기초지수별 상대 발행기회와 헤지부담 분포">
            <defs>${trajectoryArrowMarkers}</defs>
            <rect x="${plot.left}" y="${plot.top}" width="${plot.width}" height="${plot.height}" class="els-map-zone els-map-zone--selective"></rect>
            <rect x="${plot.left + plot.width * 0.65}" y="${plot.top + plot.height * 0.55}" width="${plot.width * 0.35}" height="${plot.height * 0.45}" class="els-map-zone els-map-zone--opportunity"></rect>
            <rect x="${plot.left + plot.width * 0.65}" y="${plot.top + plot.height * 0.2}" width="${plot.width * 0.35}" height="${plot.height * 0.35}" class="els-map-zone els-map-zone--caution"></rect>
            <rect x="${plot.left}" y="${plot.top}" width="${plot.width}" height="${plot.height * 0.2}" class="els-map-zone els-map-zone--burden"></rect>
            ${gridLines}
            <path d="M ${plot.left + plot.width * 0.65} ${plot.top} V ${plot.top + plot.height}" class="els-map-threshold"></path>
            <path d="M ${plot.left} ${plot.top + plot.height * 0.55} H ${plot.left + plot.width}" class="els-map-threshold"></path>
            <path d="M ${plot.left} ${plot.top + plot.height * 0.2} H ${plot.left + plot.width}" class="els-map-threshold els-map-threshold--danger"></path>
            ${trajectoryLayers}
            <text x="${plot.left + plot.width - 12}" y="${plot.top + 18}" text-anchor="end" class="els-map-zone-label">발행부담</text>
            <text x="${plot.left + plot.width - 12}" y="${plot.top + plot.height * 0.2 + 20}" text-anchor="end" class="els-map-zone-label">헤지주의</text>
            <text x="${plot.left + plot.width - 12}" y="${plot.top + plot.height - 12}" text-anchor="end" class="els-map-zone-label">발행기회</text>
            <text x="${plot.left + 12}" y="${plot.top + plot.height - 12}" class="els-map-zone-label">선별발행</text>
            ${points}
            <text x="${plot.left + plot.width / 2}" y="380" text-anchor="middle" class="els-map-axis-label">상대 발행기회 →</text>
            <text x="${plot.left + plot.width / 2}" y="398" text-anchor="middle" class="els-map-axis-note">변동성↑ 쿠폰↑</text>
            <text x="16" y="${plot.top + plot.height / 2}" text-anchor="middle" transform="rotate(-90 16 ${plot.top + plot.height / 2})" class="els-map-axis-label">헤지부담 →</text>
            <text x="34" y="${plot.top + plot.height / 2}" text-anchor="middle" transform="rotate(-90 34 ${plot.top + plot.height / 2})" class="els-map-axis-note">하락위험↑ 부담↑</text>
          </svg>
        </div>
      </section>

      <section class="els-comparison">
        <div class="els-comparison__header">
          <div>
            <span class="eyebrow">Underlying Review</span>
            <h3>지수별 발행·헤지 판독</h3>
          </div>
          <small>기회 대비 부담 균형점수 순</small>
        </div>
        <div class="els-comparison-list">
          ${map.items
            .map(
              (item) => `
                <article class="els-comparison-row els-comparison-row--${item.tone}">
                  <div class="els-comparison-row__identity">
                    <span>${item.region} · ${item.lastDate}</span>
                    <strong>${item.label}</strong>
                    <em>${item.stance}</em>
                  </div>
                  <div class="els-comparison-row__scores">
                    <div>
                      <span>발행기회 <strong>${Number(item.opportunityScore).toFixed(1)}</strong></span>
                      <i><b class="els-score-bar--opportunity" style="width:${clampScore(item.opportunityScore)}%"></b></i>
                    </div>
                    <div>
                      <span>헤지부담 <strong>${Number(item.hedgeBurdenScore).toFixed(1)}</strong></span>
                      <i><b class="els-score-bar--burden" style="width:${clampScore(item.hedgeBurdenScore)}%"></b></i>
                    </div>
                  </div>
                  <dl>
                    <div><dt>20D 수익률</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                    <div><dt>20D 변동성</dt><dd>${Number(item.metrics.realizedVol20dPct).toFixed(1)}%</dd></div>
                    <div><dt>252D 낙폭</dt><dd>${formatSignedPct(item.metrics.drawdown252dPct)}</dd></div>
                  </dl>
                  <p>${item.interpretation}</p>
                </article>
              `
            )
            .join("")}
        </div>
      </section>

      <section class="els-methodology">
        <article><span>발행기회 산식</span><p>${map.methodology.opportunity}</p></article>
        <article><span>헤지부담 산식</span><p>${map.methodology.hedgeBurden}</p></article>
        <article><span>판단 기준</span><p>${map.methodology.classification}</p></article>
      </section>
      <aside class="els-limitations"><strong>운영 적용 전 확인</strong><p>${map.limitations}</p></aside>
      ${renderElsStressEpisodeReview(map.stressEpisodes, plot)}
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

function renderHmmRegimeBands(points, domain, width = 260) {
  if (!points?.length || !domain) return "";
  const valid = points
    .filter((point) => Number.isFinite(Number(point.issuerScore)) && Number.isFinite(dateMs(point.date)))
    .sort((a, b) => dateMs(a.date) - dateMs(b.date));
  if (!valid.length) return "";

  return valid
    .map((point, index) => {
      const previous = valid[index - 1];
      const next = valid[index + 1];
      const currentX = xFromDate(point.date, domain, width);
      const previousX = previous ? xFromDate(previous.date, domain, width) : 0;
      const nextX = next ? xFromDate(next.date, domain, width) : width;
      const left = index === 0 ? Math.max(0, currentX) : (previousX + currentX) / 2;
      const right = index === valid.length - 1 ? Math.min(width, currentX) : (currentX + nextX) / 2;
      const safeLeft = Math.max(0, Math.min(width, left));
      const safeRight = Math.max(safeLeft + 0.8, Math.min(width, right));
      return `
        <rect
          class="hmm-regime-band hmm-regime-band--${point.tone}"
          x="${safeLeft.toFixed(2)}"
          y="4"
          width="${(safeRight - safeLeft).toFixed(2)}"
          height="12"
        >
          <title>${point.date} · ${point.regime} · 부담 ${Number(point.issuerScore).toFixed(1)}</title>
        </rect>
      `;
    })
    .join("");
}

function renderHmmMonthGuides(domain, width = 260) {
  const segments = monthSegmentsFromDomain(domain, width);
  if (!segments.length) return "";

  return `
    ${segments
      .map((segment, index) =>
        index % 2 === 1
          ? `<rect class="hmm-regime-month-guide-band" x="${segment.startX.toFixed(2)}" y="4" width="${(segment.endX - segment.startX).toFixed(2)}" height="76"></rect>`
          : ""
      )
      .join("")}
    ${segments
      .slice(1)
      .map(
        (segment) =>
          `<line class="hmm-regime-month-guide-line" x1="${segment.startX.toFixed(2)}" x2="${segment.startX.toFixed(2)}" y1="4" y2="80"></line>`
      )
      .join("")}
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
                  <svg viewBox="0 0 260 86" preserveAspectRatio="none" role="img" aria-label="${item.label} HMM 레짐과 부담 점수">
                    ${renderHmmMonthGuides(timelineDomainValue)}
                    <rect class="hmm-regime-band-bg" x="0" y="4" width="260" height="12"></rect>
                    ${renderHmmRegimeBands(item.series, timelineDomainValue)}
                    <path class="hmm-regime-spark-grid" d="M 0 30 L 260 30 M 0 48 L 260 48 M 0 66 L 260 66"></path>
                    <path class="hmm-regime-spark ${colorClass[item.id] ?? ""}" d="${scorePathByDatePlot(item.series, "issuerScore", 260, 26, 80, timelineDomainValue)}"></path>
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
  const isObservation = indicator.role === "observation";

  return `
    <article class="indicator-card ${isObservation ? "indicator-card--observation" : ""}">
      <div>
        <span class="eyebrow">${indicator.category}</span>
        <h3>${indicator.name}</h3>
      </div>
      <div class="indicator-card__score">
        <strong>${formatScore(indicator.value)}</strong>
        <span class="status-pill status-pill--${isObservation ? "watch" : tone}">${isObservation ? `관찰 · ${level?.label ?? "N/A"}` : level?.label ?? "N/A"}</span>
      </div>
      <div class="contribution-line">
        <span>${indicator.group ?? "risk"}</span>
        <strong>${isObservation ? "종합점수 미반영" : `기여도 +${Number(indicator.contribution ?? 0).toFixed(2)}점`}</strong>
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

function renderIndicatorSortControls(sectionId) {
  return `
    <div class="indicator-toolbar">
      <div>
        <span class="eyebrow">Indicator Sort</span>
        <h3>시장리스크 카드 정렬</h3>
      </div>
      <div class="indicator-sort" role="group" aria-label="시장리스크 카드 정렬 기준">
        ${indicatorSortOptions
          .map(
            (option, index) => `
              <button
                type="button"
                class="indicator-sort__button ${index === 0 ? "is-active" : ""}"
                data-indicator-sort="${option.key}"
                data-section-id="${sectionId}"
                data-sort-direction="desc"
                title="${sortOptionDescription(option, index === 0, "desc")}"
                aria-pressed="${index === 0 ? "true" : "false"}"
                aria-label="${sortOptionLabel(option, index === 0, "desc")} 정렬"
              >
                ${sortOptionLabel(option, index === 0, "desc")}
              </button>
            `
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderSentimentMoverList(title, eyebrow, items, mode = "change") {
  return `
    <section class="sentiment-list">
      <div>
        <span class="eyebrow">${eyebrow}</span>
        <h3>${title}</h3>
      </div>
      <ol>
        ${
          items.length
            ? items
                .map((item) => {
                  const sentimentChange = -Number(item.change1w);
                  const value = mode === "score" ? formatScore(item.value) : formatPointDelta(sentimentChange);
                  const tone =
                    mode === "score" ? sentimentTone(inverseScore(item.value)) : sentimentChangeTone(sentimentChange);
                  return `
                    <li>
                      <span>${item.name}</span>
                      <strong class="sentiment-value sentiment-value--${tone}">${value}</strong>
                    </li>
                  `;
                })
                .join("")
            : `<li class="sentiment-list__empty">유의한 변화 없음</li>`
        }
      </ol>
    </section>
  `;
}

function renderSentimentPage(data, timeseries, mlRisk, elsRisk, hmmRegime) {
  const market = data.sections.find((section) => section.id === "market");
  if (!market) return "";

  market.asOf = data.metadata.asOf;
  const score = inverseScore(market.score);
  const level = sentimentLevel(score);
  const points = buildSentimentSeries(market, timeseries);
  const latest = points[points.length - 1] ?? { date: data.metadata.asOf, value: score };
  const path = trendChartPath(points);
  const areaPath = path ? `${path} L 760 190 L 0 190 Z` : "";
  const monthAxis = renderMonthAxis(points);
  const changes = [
    ["1D", valueChange(latest.value, points, 1)],
    ["1W", valueChange(latest.value, points, 5)],
    ["1M", valueChange(latest.value, points, 20)]
  ];
  const groupById = Object.fromEntries((market.groupScores ?? []).map((group) => [group.id, group]));
  const components = sentimentGroupDefinitions
    .map((definition) => ({ ...definition, group: groupById[definition.id] }))
    .filter((item) => item.group);
  const scoredIndicators = (market.indicators ?? []).filter(isScoredIndicator);
  const indicatorMoves = scoredIndicators
    .map((indicator) => ({ ...indicator, change1w: indicatorWeeklyChange(indicator, timeseries) }))
    .filter((indicator) => Number.isFinite(Number(indicator.change1w)));
  const worsening = indicatorMoves
    .filter((indicator) => indicator.change1w > 0.05)
    .sort((a, b) => b.change1w - a.change1w)
    .slice(0, 4);
  const improving = indicatorMoves
    .filter((indicator) => indicator.change1w < -0.05)
    .sort((a, b) => a.change1w - b.change1w)
    .slice(0, 4);
  const pressure = [...scoredIndicators].sort((a, b) => clampScore(b.value) - clampScore(a.value)).slice(0, 4);
  const mlRiskOff = Number(mlRisk?.latest?.riskOffProbabilityPct);
  const mlSentiment = Number.isFinite(mlRiskOff) ? inverseScore(mlRiskOff) : null;
  const elsScore = Number(elsRisk?.basket?.score);
  const elsSentiment = Number.isFinite(elsScore) ? inverseScore(elsScore) : null;

  return `
    <section class="sentiment-page">
      <header class="sentiment-hero">
        <div>
          <span class="eyebrow">Market Sentiment</span>
          <h2>시장 센티멘트</h2>
          <p>${level.reading}</p>
        </div>
        <div class="sentiment-state sentiment-state--${level.tone}">
          <span>현재 심리</span>
          <strong>${level.label}</strong>
          <small>${formatScore(score)}</small>
        </div>
      </header>

      <div class="sentiment-signal-grid">
        ${createMetricCard({
          label: "ML 향후심리",
          value: mlSentiment === null ? "-" : formatScore(mlSentiment),
          meta: mlSentiment === null ? "데이터 준비중" : `5일 risk-off 확률 ${mlRiskOff.toFixed(1)}%의 반대 점수`,
          tone: mlSentiment === null ? "neutral" : sentimentTone(mlSentiment)
        })}
        ${createMetricCard({
          label: "ELS 바스켓 심리",
          value: elsSentiment === null ? "-" : formatScore(elsSentiment),
          meta: elsSentiment === null ? "데이터 준비중" : `${elsRisk.basket.bucket} · 바스켓 리스크의 반대 점수`,
          tone: elsSentiment === null ? "neutral" : sentimentTone(elsSentiment)
        })}
        ${createMetricCard({
          label: "HMM 시장 레짐",
          value: hmmRegime?.basket?.regime ?? "-",
          meta: hmmRegime?.basket
            ? `위험회피 ${hmmRegime.basket.riskOffCount} · 고변동성 활황 ${hmmRegime.basket.highVolBullCount} · 안정 ${hmmRegime.basket.stableCount}`
            : "데이터 준비중",
          tone: hmmRegime?.basket?.tone ?? "neutral"
        })}
      </div>

      ${
        points.length >= 2
          ? `<section class="trend-panel sentiment-trend">
              <div class="trend-panel__header">
                <div>
                  <span class="eyebrow">Sentiment Trend</span>
                  <h2>시장 센티멘트 흐름</h2>
                </div>
                <div class="trend-score">
                  <strong>${formatScore(latest.value)}</strong>
                  <span>${latest.date}</span>
                </div>
              </div>
              <div class="trend-kpis">
                ${changes
                  .map(
                    ([label, value]) => `<span class="change-pill change-pill--${sentimentChangeTone(value)}"><small>${label}</small><strong>${formatPointDelta(value)}</strong></span>`
                  )
                  .join("")}
                <span><small>기준</small><strong>50.0</strong></span>
              </div>
              <div class="trend-chart" aria-label="시장 센티멘트 시계열">
                <svg viewBox="0 0 760 210" role="img">
                  ${monthAxis.grid}
                  <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
                  <path class="trend-chart__area" d="${areaPath}"></path>
                  <path class="trend-chart__line" d="${path}"></path>
                  ${monthAxis.labels}
                </svg>
              </div>
            </section>`
          : ""
      }

      <section class="sentiment-components">
        <div class="sentiment-section-heading">
          <div>
            <span class="eyebrow">Drivers</span>
            <h2>심리 구성요소</h2>
          </div>
          <p>높을수록 해당 영역의 시장 부담이 낮다는 뜻입니다.</p>
        </div>
        <div class="sentiment-component-grid">
          ${components
            .map(({ label, detail, group }) => {
              const componentScore = inverseScore(group.score);
              return `
                <article class="sentiment-component sentiment-component--${sentimentTone(componentScore)}">
                  <div>
                    <h3>${label}</h3>
                    <p>${detail}</p>
                  </div>
                  <strong>${componentScore.toFixed(1)}</strong>
                  <div class="sentiment-meter" aria-hidden="true"><span style="width:${componentScore}%"></span></div>
                </article>
              `;
            })
            .join("")}
        </div>
      </section>

      <section class="sentiment-movers">
        ${renderSentimentMoverList("심리를 끌어내린 지표", "1W Deterioration", worsening)}
        ${renderSentimentMoverList("심리를 회복시킨 지표", "1W Improvement", improving)}
        ${renderSentimentMoverList("현재 부담 상위 지표", "Current Pressure", pressure, "score")}
      </section>
    </section>
  `;
}

function renderSummary(data, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime) {
  const market = data.sections.find((section) => section.id === "market");
  const scoredIndicatorCount = (market.indicators ?? []).filter(isScoredIndicator).length;
  const observationCount = (market.indicators ?? []).filter((indicator) => indicator.role === "observation").length;
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
        meta: `가중 ${scoredIndicatorCount}개 · 관찰 ${observationCount}개`,
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
        시장리스크 종합점수는 ${formatScore(market.score)}입니다. 현재 가중지표 ${scoredIndicatorCount}개와
        종합점수 미반영 관찰지표 ${observationCount}개를
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
                <span class="eyebrow">가중 ${group.indicatorCount}${group.observationCount ? ` · 관찰 ${group.observationCount}` : ""}</span>
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
  const initiallySortedIndicators = sortedIndicators(section, timeseries);

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
            ${section.id === "market" ? renderIndicatorSortControls(section.id) : ""}
            <div class="indicator-grid" data-indicator-grid="${section.id}">
              ${initiallySortedIndicators
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

function renderDashboard(rawData, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime, pipelineStatus) {
  const data = evaluateDashboard(rawData);
  const dashboardTabs = dashboardTabsWithElsTool(data.tabs);
  const enabledTabs = dashboardTabs.filter((tab) => tab.enabled);
  const indicatorSortStates = Object.fromEntries(
    data.sections.map((section) => [section.id, { key: "score", direction: "desc" }])
  );

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

    ${renderOperationStatusStrip(pipelineStatus)}

    <nav class="tabs" aria-label="리스크 대시보드 탭">
      <div class="tabs__items">
        ${dashboardTabs
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
      <section class="tab-panel" data-panel="sentiment">
        ${renderSentimentPage(data, timeseries, mlRisk, elsRisk, hmmRegime)}
      </section>
      <section class="tab-panel" data-panel="operations">
        ${renderOperationsPage(pipelineStatus)}
      </section>
      <section class="tab-panel" data-panel="els-issuance">
        ${renderElsIssuanceHedgePage(elsRisk)}
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

  app.querySelectorAll("[data-indicator-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const sectionId = button.dataset.sectionId;
      const sortKey = button.dataset.indicatorSort;
      const section = data.sections.find((item) => item.id === sectionId);
      const grid = app.querySelector(`[data-indicator-grid="${sectionId}"]`);
      if (!section || !grid) return;
      const current = indicatorSortStates[sectionId] ?? { key: "score", direction: "desc" };
      const nextDirection = sortKey !== "score" && current.key === sortKey && current.direction === "desc" ? "asc" : "desc";
      indicatorSortStates[sectionId] = { key: sortKey, direction: nextDirection };

      app
        .querySelectorAll(`[data-section-id="${sectionId}"]`)
        .forEach((item) => {
          const option = indicatorSortOptions.find((candidate) => candidate.key === item.dataset.indicatorSort);
          const active = item === button;
          item.classList.toggle("is-active", active);
          item.setAttribute("aria-pressed", active ? "true" : "false");
          item.dataset.sortDirection = active ? nextDirection : "desc";
          item.textContent = sortOptionLabel(option, active, nextDirection);
          item.title = sortOptionDescription(option, active, nextDirection);
          item.setAttribute("aria-label", `${sortOptionLabel(option, active, nextDirection)} 정렬`);
        });

      grid.innerHTML = sortedIndicators(section, timeseries, sortKey, nextDirection)
        .map((indicator) => renderIndicator(indicator, section.model.thresholds, timeseries))
        .join("");
    });
  });

  app.querySelectorAll("[data-els-map]").forEach((mapElement) => {
    mapElement.querySelectorAll("[data-els-window]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.elsWindow;
        mapElement.querySelectorAll("[data-els-window]").forEach((option) => {
          const active = option === button;
          option.classList.toggle("is-active", active);
          option.setAttribute("aria-pressed", active ? "true" : "false");
        });
        mapElement.querySelectorAll("[data-els-trajectory]").forEach((layer) => {
          layer.classList.toggle("is-visible", layer.dataset.elsTrajectory === target);
        });
        mapElement.querySelectorAll("[data-els-momentum-legend]").forEach((legend) => {
          legend.hidden = target !== "1w";
        });
      });
    });
  });

  app.querySelectorAll("[data-els-episode-review]").forEach((review) => {
    review.querySelectorAll("[data-els-episode]").forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.dataset.elsEpisode;
        review.querySelectorAll("[data-els-episode]").forEach((option) => {
          const active = option === button;
          option.classList.toggle("is-active", active);
          option.setAttribute("aria-pressed", active ? "true" : "false");
        });
        review.querySelectorAll("[data-els-episode-panel]").forEach((panel) => {
          panel.classList.toggle("is-active", panel.dataset.elsEpisodePanel === target);
        });
      });
    });
  });

  const themeButton = app.querySelector("[data-theme-toggle]");
  themeButton.addEventListener("click", toggleTheme);
  updateThemeButton();
}

async function loadJson(path, required = false) {
  try {
    const response = await fetch(versioned(path), { cache: "no-store" });
    if (!response.ok) throw new Error(`${path} 응답 오류: ${response.status}`);
    return await response.json();
  } catch (error) {
    if (required) throw error;
    console.warn(`선택 데이터 로드 실패: ${path}`, error);
    return null;
  }
}

Promise.all([
  loadJson("./data/risk-dashboard.json", true),
  loadJson("./data/market-risk-timeseries.json"),
  loadJson("./data/market-risk-backtest.json"),
  loadJson("./data/market-stress-episodes.json"),
  loadJson("./data/ml-risk-signal.json"),
  loadJson("./data/els-index-risk.json"),
  loadJson("./data/hmm-regime.json"),
  loadJson("./data/pipeline-status.json")
])
  .then(([dashboard, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime, pipelineStatus]) =>
    renderDashboard(dashboard, timeseries, backtest, stressEpisodes, mlRisk, elsRisk, hmmRegime, pipelineStatus)
  )
  .catch((error) => {
    app.innerHTML = `
      <div class="loading-panel loading-panel--error">
        <strong>대시보드를 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </div>
    `;
  });
