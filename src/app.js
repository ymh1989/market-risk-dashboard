import { clampScore, evaluateDashboard, isScoredIndicator } from "./risk-model.js";

const app = document.querySelector("#app");
const THEME_STORAGE_KEY = "risk-dashboard-theme";
const ASSET_VERSION = "20260819-2";
const DATA_REQUEST_VERSION = Date.now().toString(36);
const IS_OFFLINE_SNAPSHOT =
  document.querySelector('meta[name="offline-snapshot"]')?.content === "true";
const OFFLINE_ADMIN_TAB_IDS = new Set(["operations", "model-monitoring"]);
document.documentElement.classList.toggle("is-offline-snapshot", IS_OFFLINE_SNAPSHOT);
const chartRangeOptions = [
  { id: "1m", label: "1M", calendarDays: 31 },
  { id: "3m", label: "3M", calendarDays: 93 },
  { id: "ytd", label: "YTD" },
  { id: "1y", label: "1Y", calendarDays: 366 },
  { id: "3y", label: "3Y", calendarDays: 1096 }
];
let activeChartRange = "ytd";
let interactiveChartSequence = 0;
const interactiveChartRegistry = new Map();

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

const riskGroupDefinitions = {
  crash: {
    label: "급락 스트레스",
    legendLabel: "급락",
    shortLabel: "Crash",
    englishLabel: "Crash Stress",
    description: "KOSPI와 KOSDAQ의 가격 하락 충격"
  },
  macro: {
    label: "거시환경 부담",
    legendLabel: "거시",
    shortLabel: "Macro",
    englishLabel: "Macro",
    description: "환율·변동성·금리·신용·원자재·운임의 거시 부담"
  },
  ai_semi: {
    label: "AI·반도체 부담",
    legendLabel: "AI·반도체",
    shortLabel: "AI Semi",
    englishLabel: "AI Semi",
    description: "글로벌 AI 수요와 국내외 반도체 집중 위험"
  },
  overheating: {
    label: "과열·쏠림",
    legendLabel: "과열",
    shortLabel: "Overheating",
    englishLabel: "Overheating",
    description: "레버리지와 신흥국 위험선호로 본 시장 과열"
  },
  flow: {
    label: "수급 압력",
    legendLabel: "수급",
    shortLabel: "Flow",
    englishLabel: "Flow",
    description: "외국인 보유비중 변화로 본 수급 이탈 압력"
  },
  liquidity: {
    label: "거래 유동성",
    legendLabel: "유동성",
    shortLabel: "Liquidity",
    englishLabel: "Liquidity",
    description: "거래량과 거래대금의 과열 또는 위축"
  }
};

const marketTrendGroups = [
  {
    id: "rates",
    label: "국채금리",
    items: [
      { id: "us2y_naver", label: "미국 2년", type: "yield", upLabel: "금리 상승", downLabel: "금리 하락" },
      { id: "us10y_naver", label: "미국 10년", type: "yield", upLabel: "금리 상승", downLabel: "금리 하락" },
      { id: "jp10y_naver", label: "일본 10년", type: "yield", upLabel: "금리 상승", downLabel: "금리 하락" },
      { id: "kr3y", label: "한국 3년", type: "yield", upLabel: "금리 상승", downLabel: "금리 하락" },
      { id: "kr10y", label: "한국 10년", type: "yield", upLabel: "금리 상승", downLabel: "금리 하락" }
    ]
  },
  {
    id: "fx",
    label: "환율",
    items: [
      { id: "usdkrw_naver", label: "원/달러", type: "fx", upLabel: "원화 약세", downLabel: "원화 강세" },
      { id: "usdjpy", label: "달러/엔", type: "fx", upLabel: "엔화 약세", downLabel: "엔화 강세" },
      { id: "usdcny", label: "달러/위안", type: "fx", upLabel: "위안화 약세", downLabel: "위안화 강세" }
    ]
  },
  {
    id: "commodities",
    label: "에너지·금속",
    items: [
      { id: "brent", label: "브렌트유", type: "price", upLabel: "유가 상승", downLabel: "유가 하락" },
      { id: "copper", label: "구리", type: "price", upLabel: "가격 상승", downLabel: "가격 하락" },
      { id: "iron_ore", label: "철광석", type: "price", upLabel: "가격 상승", downLabel: "가격 하락" },
      { id: "gold", label: "국제 금", type: "price", upLabel: "금값 상승", downLabel: "금값 하락" }
    ]
  },
  {
    id: "transport",
    label: "운임",
    items: [
      { id: "scfi", label: "SCFI", type: "index", upLabel: "운임 상승", downLabel: "운임 하락" },
      { id: "bdti", label: "BDTI", type: "index", upLabel: "운임 상승", downLabel: "운임 하락" },
      { id: "bdi", label: "BDI", type: "index", upLabel: "운임 상승", downLabel: "운임 하락" }
    ]
  }
];

const formatScore = (value) => `${clampScore(value).toFixed(1)} / 100`;
const formatNumber = (value, digits = 0) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return new Intl.NumberFormat("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  }).format(number);
};
const formatPct = (value) => `${Number(value).toFixed(2)}%`;
const formatSignedPct = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
};
const formatSignedThousands = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value) / 1000;
  return `${number > 0 ? "+" : ""}${formatNumber(number, 1)}천 종목`;
};
const formatSignedEok = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${formatNumber(number, Math.abs(number) < 100 ? 1 : 0)}억원`;
};
const formatPointDelta = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}p`;
};
const formatPctPointDelta = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}%p`;
};
const formatAttributionDelta = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}p`;
};
const formatShortDate = (value) => {
  if (!value) return "-";
  const date = new Date(`${value}T00:00:00Z`);
  return `${date.getUTCMonth() + 1}.${String(date.getUTCDate()).padStart(2, "0")}`;
};
const inverseScore = (value) => clampScore(100 - clampScore(value));
function compactNarrativeItem(value) {
  return String(value ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .replace(/[.!?。]\s*$/, "")
    .replace(/해야 합니다$/, " 필요")
    .replace(/해야 됩니다$/, " 필요")
    .replace(/할 수 있습니다$/, " 가능")
    .replace(/될 수 있습니다$/, " 가능")
    .replace(/되어 있습니다$/, " 상태")
    .replace(/돼 있습니다$/, " 상태")
    .replace(/있습니다$/, " 있음")
    .replace(/없습니다$/, " 없음")
    .replace(/확인됐습니다$/, "확인")
    .replace(/확인되었습니다$/, "확인")
    .replace(/됐습니다$/, "")
    .replace(/되었습니다$/, "")
    .replace(/됩니다$/, "")
    .replace(/입니다$/, "")
    .replace(/합니다$/, "")
    .trim();
}

function toNarrativeItems(value) {
  const values = Array.isArray(value) ? value : [value];
  return values
    .flatMap((item) => String(item ?? "").split(/(?:[.!?。]\s+|\n+)/))
    .map(compactNarrativeItem)
    .filter(Boolean);
}

function renderNarrativeList(value, extraClass = "") {
  const items = toNarrativeItems(value);
  if (!items.length) return "";
  const className = ["narrative-list", extraClass].filter(Boolean).join(" ");
  return `<ul class="${className}">${items
    .map((item) => {
      const labeledItem = item.match(/^([^:：]{1,24})[:：]\s+(.+)$/);
      return labeledItem
        ? `<li><strong>${labeledItem[1]}</strong><span>${labeledItem[2]}</span></li>`
        : `<li>${item}</li>`;
    })
    .join("")}</ul>`;
}

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

function offlineSnapshotFilename(data) {
  const generatedAt = String(data?.metadata?.generatedAt ?? "");
  const match = generatedAt.match(
    /(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/
  );
  if (!match) return "market-risk-dashboard-offline.html";
  const [, year, month, day, hour = "00", minute = "00"] = match;
  return `market-risk-dashboard-${year}${month}${day}-${hour}${minute}-KST.html`;
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
    const currentDate = section.asOf ?? latest.date;
    if (latest.date === currentDate) {
      latest.value = currentScore;
    } else if (latest.date < currentDate) {
      composite.push({ date: currentDate, value: currentScore });
    }
  }
  return composite;
}

function buildGroupCompositeSeries(section, groupId, timeseries) {
  const indicators = (section?.indicators ?? []).filter(
    (indicator) =>
      indicator.group === groupId &&
      isScoredIndicator(indicator) &&
      Number(indicator.weight) > 0
  );
  const totalWeight = indicators.reduce(
    (sum, indicator) => sum + Number(indicator.weight || 0),
    0
  );
  if (!totalWeight) return [];

  const dateScores = {};
  const dateWeights = {};
  indicators.forEach((indicator) => {
    const weight = Number(indicator.weight);
    (timeseries?.series?.[indicator.id] ?? []).forEach((point) => {
      if (!Number.isFinite(Number(point.value))) return;
      dateScores[point.date] = (dateScores[point.date] ?? 0) + clampScore(point.value) * weight;
      dateWeights[point.date] = (dateWeights[point.date] ?? 0) + weight;
    });
  });

  const composite = Object.keys(dateScores)
    .sort()
    .filter((date) => dateWeights[date] >= totalWeight * 0.7)
    .map((date) => ({
      date,
      value: dateScores[date] / dateWeights[date]
    }));
  if (!composite.length) return composite;

  const currentGroup = (section.groupScores ?? []).find((group) => group.id === groupId);
  const currentScore = Number(currentGroup?.score);
  const currentDate = section.asOf;
  if (Number.isFinite(currentScore) && currentDate) {
    const latest = composite.at(-1);
    if (latest.date === currentDate) {
      latest.value = currentScore;
    } else if (latest.date < currentDate) {
      composite.push({ date: currentDate, value: currentScore });
    }
  }
  return composite;
}

function buildObservationJournalSeries(section, item, timeseries) {
  const components = (item?.components ?? []).filter(
    (component) => Number(component.weight) > 0
  );
  const totalWeight = components.reduce(
    (sum, component) => sum + Number(component.weight),
    0
  );
  if (!totalWeight) return [];

  const dateScores = {};
  const dateWeights = {};
  components.forEach((component) => {
    const weight = Number(component.weight);
    (timeseries?.series?.[component.id] ?? []).forEach((point) => {
      if (!Number.isFinite(Number(point.value))) return;
      dateScores[point.date] =
        (dateScores[point.date] ?? 0) + clampScore(point.value) * weight;
      dateWeights[point.date] = (dateWeights[point.date] ?? 0) + weight;
    });
  });

  const composite = Object.keys(dateScores)
    .sort()
    .filter((date) => dateWeights[date] >= totalWeight * 0.7)
    .map((date) => ({
      date,
      value: dateScores[date] / dateWeights[date]
    }));
  if (!composite.length) return composite;

  const currentScore = Number(item.score);
  const currentDate = section.asOf;
  if (Number.isFinite(currentScore) && currentDate) {
    const latest = composite.at(-1);
    if (latest.date === currentDate) {
      latest.value = currentScore;
    } else if (latest.date < currentDate) {
      composite.push({ date: currentDate, value: currentScore });
    }
  }
  return composite;
}

function buildScoreAttribution(section, timeseries, offset) {
  const indicators = (section?.indicators ?? []).filter(isScoredIndicator);
  const totalWeight = indicators.reduce((sum, indicator) => sum + Number(indicator.weight || 0), 0);
  if (!totalWeight) return [];

  return indicators
    .map((indicator) => {
      const points = timeseries?.series?.[indicator.id] ?? [];
      const scoreChange = valueChange(indicator.value, points, offset);
      if (!Number.isFinite(Number(scoreChange))) return null;
      return {
        ...indicator,
        scoreChange,
        weightedChange: (scoreChange * Number(indicator.weight || 0)) / totalWeight
      };
    })
    .filter(Boolean);
}

function dashboardTabsWithSentiment(tabs) {
  if (tabs.some((tab) => tab.id === "sentiment")) return tabs;
  const summaryIndex = tabs.findIndex((tab) => tab.id === "summary");
  const insertAt = summaryIndex >= 0 ? summaryIndex + 1 : 0;
  const sentimentTab = { id: "sentiment", label: "시장 센티멘트", enabled: true };
  return [...tabs.slice(0, insertAt), sentimentTab, ...tabs.slice(insertAt)];
}

function dashboardTabsWithBreadth(tabs) {
  const withSentiment = dashboardTabsWithSentiment(tabs);
  if (withSentiment.some((tab) => tab.id === "market-breadth")) return withSentiment;
  const sentimentIndex = withSentiment.findIndex((tab) => tab.id === "sentiment");
  const insertAt = sentimentIndex >= 0 ? sentimentIndex + 1 : 1;
  const breadthTab = { id: "market-breadth", label: "시장 내부강도", enabled: true };
  return [...withSentiment.slice(0, insertAt), breadthTab, ...withSentiment.slice(insertAt)];
}

function dashboardTabsWithOperations(tabs) {
  const withSentiment = dashboardTabsWithBreadth(tabs);
  if (withSentiment.some((tab) => tab.id === "operations")) return withSentiment;
  const breadthIndex = withSentiment.findIndex((tab) => tab.id === "market-breadth");
  const insertAt = breadthIndex >= 0 ? breadthIndex + 1 : 2;
  const operationsTab = { id: "operations", label: "운영현황", enabled: true };
  return [...withSentiment.slice(0, insertAt), operationsTab, ...withSentiment.slice(insertAt)];
}

function dashboardTabsWithElsTool(tabs) {
  const withOperations = dashboardTabsWithOperations(tabs);
  const operationsIndex = withOperations.findIndex((tab) => tab.id === "operations");
  const researchTabs = [
    { id: "model-monitoring", label: "모델 검증", enabled: true },
    { id: "replay", label: "시점 비교", enabled: true }
  ];
  const withResearch = researchTabs.reduce((current, tab, index) => {
    if (current.some((item) => item.id === tab.id)) return current;
    const insertAt = operationsIndex >= 0 ? operationsIndex + 1 + index : 2 + index;
    return [...current.slice(0, insertAt), tab, ...current.slice(insertAt)];
  }, withOperations);
  if (withResearch.some((tab) => tab.id === "els-issuance")) return withResearch;
  const replayIndex = withResearch.findIndex((tab) => tab.id === "replay");
  const insertAt = replayIndex >= 0 ? replayIndex + 1 : 2;
  const elsToolTab = { id: "els-issuance", label: "ELS 발행·헤지", enabled: true };
  return [...withResearch.slice(0, insertAt), elsToolTab, ...withResearch.slice(insertAt)];
}

function formatDurationSeconds(value) {
  if (value === null || value === undefined || value === "") return "-";
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "-";
  if (seconds < 60) return `${Math.round(seconds)}초`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}분 ${remainder}초` : `${minutes}분`;
}

function pipelineModeLabel(mode) {
  if (mode === "full") return "전체 갱신";
  if (mode === "fast") return "빠른 갱신";
  return mode || "-";
}

function formatCountdownSeconds(value) {
  const seconds = Math.max(0, Number(value) || 0);
  if (seconds < 60) return "1분 이내";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}시간 ${remainder}분` : `${hours}시간`;
}

function formatKstClock(value) {
  const clock = String(value ?? "").split(" ")[1];
  return clock ? clock.slice(0, 5) : "-";
}

function parseKstTimestamp(value) {
  const match = String(value ?? "").match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/
  );
  if (!match) return Number.NaN;
  const [, year, month, day, hour, minute, second = "0"] = match;
  return Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour) - 9,
    Number(minute),
    Number(second)
  );
}

function findSuccessfulRunForSchedule(history, scheduleItem) {
  const modeMatches = (run) =>
    scheduleItem.mode === "full"
      ? run.mode === "full"
      : run.mode === "fast" || run.mode === "full";
  const successful = (history ?? []).filter(
    (run) => run.status === "success" && modeMatches(run)
  );
  const exact = successful.find(
    (run) =>
      run.scheduledTime === scheduleItem.time &&
      String(run.startedAt ?? "").startsWith(scheduleItem.dateKey)
  );
  if (exact) return { run: exact, replacement: false };

  const replacement = successful
    .map((run) => ({ run, completedTimestamp: parseKstTimestamp(run.completedAt) }))
    .filter(
      (candidate) =>
        Number.isFinite(candidate.completedTimestamp) &&
        candidate.completedTimestamp >= scheduleItem.timestamp
    )
    .sort((left, right) => left.completedTimestamp - right.completedTimestamp)[0];
  return replacement ? { run: replacement.run, replacement: true } : null;
}

function medianRunDuration(history, mode) {
  const durations = (history ?? [])
    .filter((run) => run.status === "success" && run.mode === mode && Number(run.durationSeconds) > 0)
    .map((run) => Number(run.durationSeconds))
    .sort((left, right) => left - right);
  if (!durations.length) return null;
  const middle = Math.floor(durations.length / 2);
  return durations.length % 2
    ? durations[middle]
    : Math.round((durations[middle - 1] + durations[middle]) / 2);
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
    const scheduleItems =
      weekday === 1
        ? schedule.mondayTimes ?? schedule.times
        : weekday >= 2 && weekday <= 5
          ? schedule.times
          : weekday === 6
            ? schedule.saturdayTimes ?? []
            : !schedule.weekdaysOnly && !schedule.saturdayTimes
              ? schedule.times
              : [];
    if (!scheduleItems.length) continue;
    const year = day.getUTCFullYear();
    const month = day.getUTCMonth();
    const date = day.getUTCDate();
    const dateKey = `${year}-${String(month + 1).padStart(2, "0")}-${String(date).padStart(2, "0")}`;

    scheduleItems.forEach((item) => {
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

function buildScheduleOverview(pipelineStatus) {
  const now = Date.now();
  const instances = buildScheduleInstances(pipelineStatus);
  if (!instances.length) return null;
  const kstOffsetMs = 9 * 60 * 60 * 1000;
  const nowKst = new Date(now + kstOffsetMs);
  const todayKey = `${nowKst.getUTCFullYear()}-${String(nowKst.getUTCMonth() + 1).padStart(2, "0")}-${String(
    nowKst.getUTCDate()
  ).padStart(2, "0")}`;
  let scheduleDate = todayKey;
  let scheduledItems = instances.filter((item) => item.dateKey === scheduleDate);

  if (!scheduledItems.length) {
    const nextInstance = instances.find((item) => item.timestamp > now);
    if (!nextInstance) return null;
    scheduleDate = nextInstance.dateKey;
    scheduledItems = instances.filter((item) => item.dateKey === scheduleDate);
  }

  const history = pipelineStatus?.history ?? [];
  const graceMinutes = Number(pipelineStatus?.schedule?.delayGraceMinutes ?? 5);
  const items = scheduledItems.map((item) => {
    const matched = findSuccessfulRunForSchedule(history, item);
    if (matched) {
      return {
        ...item,
        status: "success",
        tone: "good",
        statusLabel: matched.replacement ? "보완 완료" : "완료",
        detail: `${formatKstClock(matched.run.completedAt)} ${matched.replacement ? "보완 실행 완료" : "완료"} · ${formatDurationSeconds(matched.run.durationSeconds)}`
      };
    }
    if (item.timestamp > now) {
      return {
        ...item,
        status: "upcoming",
        tone: "muted",
        statusLabel: "예정",
        detail: `${formatCountdownSeconds((item.timestamp - now) / 1000)} 후 시작`
      };
    }
    const elapsedSeconds = Math.max(0, Math.floor((now - item.timestamp) / 1000));
    const expectedMinutes = Number(pipelineStatus?.schedule?.expectedDurationMinutes?.[item.mode] ?? 10);
    const delayed = elapsedSeconds > (expectedMinutes + graceMinutes) * 60;
    return {
      ...item,
      status: delayed ? "delayed" : "running",
      tone: delayed ? "caution" : "watch",
      statusLabel: delayed ? "지연" : "진행 중",
      detail: delayed
        ? `예상 완료시간을 ${formatDurationSeconds(elapsedSeconds - expectedMinutes * 60)} 초과`
        : `${formatDurationSeconds(elapsedSeconds)} 경과 · 통상 ${expectedMinutes}분`
    };
  });

  return {
    scheduleDate,
    isToday: scheduleDate === todayKey,
    items,
    completedCount: items.filter((item) => item.status === "success").length,
    fullMedian: medianRunDuration(history, "full"),
    fastMedian: medianRunDuration(history, "fast")
  };
}

function pipelineRuntimeState(pipelineStatus) {
  if (!pipelineStatus?.current) {
    return {
      label: "확인 필요",
      tone: "muted",
      detail: "운영 상태 파일을 불러오지 못했습니다.",
      latestSuccess: null,
      nextRun: null,
      activeRun: null
    };
  }

  const now = Date.now();
  const history = pipelineStatus.history ?? [];
  const instances = buildScheduleInstances(pipelineStatus);
  const latestDue = [...instances].reverse().find((item) => item.timestamp <= now);
  const nextRun = instances.find((item) => item.timestamp > now) ?? null;
  const matchingRun = latestDue
    ? findSuccessfulRunForSchedule(history, latestDue)
    : null;
  const latestSuccess = history.find((item) => item.status === "success") ?? pipelineStatus.current;
  const sourceProblem = (pipelineStatus.sources ?? []).some((source) => source.status !== "ok");
  const qualityProblem = pipelineStatus.quality?.status && pipelineStatus.quality.status !== "ok";

  if (latestDue && !matchingRun) {
    const elapsedMinutes = Math.max(0, (now - latestDue.timestamp) / 60000);
    const activeRun = {
      mode: latestDue.mode,
      scheduledTime: latestDue.time,
      elapsedSeconds: Math.floor(elapsedMinutes * 60)
    };
    const expectedMinutes = Number(pipelineStatus.schedule?.expectedDurationMinutes?.[latestDue.mode] ?? 10);
    const graceMinutes = Number(pipelineStatus.schedule?.delayGraceMinutes ?? 5);
    if (elapsedMinutes <= expectedMinutes + graceMinutes) {
      return {
        label: "갱신 중",
        tone: "watch",
        detail: `${latestDue.time} ${pipelineModeLabel(latestDue.mode)} 예약 작업의 완료 기록을 기다리고 있습니다.`,
        latestSuccess,
        nextRun,
        activeRun
      };
    }
    return {
      label: "지연",
      tone: "caution",
      detail: `${latestDue.label} 예약 작업이 예상 완료시간을 지났습니다. 로컬 로그 확인이 필요합니다.`,
      latestSuccess,
      nextRun,
      activeRun
    };
  }

  return {
    label: sourceProblem || qualityProblem ? "일부 확인" : "정상",
    tone: sourceProblem || qualityProblem ? "caution" : "good",
    detail:
      sourceProblem || qualityProblem
        ? "일부 데이터 원천 또는 산출물의 완비성을 확인해야 합니다."
        : pipelineStatus.current.message,
    latestSuccess,
    nextRun,
    activeRun: null
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
        <strong>${state.nextRun ? `${state.nextRun.label} · ${pipelineModeLabel(state.nextRun.mode)}` : "-"}</strong>
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

function renderScheduleOverview(pipelineStatus) {
  const overview = buildScheduleOverview(pipelineStatus);
  if (!overview) return "";
  return `
    <section class="operations-section operations-schedule">
      <div class="operations-section__heading">
        <div>
          <span class="eyebrow">Schedule Timeline</span>
          <h3>${overview.isToday ? "오늘의 예약 실행" : "다음 예약"}</h3>
        </div>
        <span>${overview.scheduleDate} · ${overview.completedCount}/${overview.items.length} 완료</span>
      </div>
      <div class="operations-schedule-list" aria-label="${overview.scheduleDate} 예약 실행 현황">
        ${overview.items
          .map(
            (item) => `
              <article class="operations-schedule-item operations-schedule-item--${item.tone}">
                <div class="operations-schedule-item__time">
                  <time datetime="${item.dateKey}T${item.time}:00+09:00">${item.time}</time>
                  <span>${pipelineModeLabel(item.mode)}</span>
                </div>
                <div>
                  <strong>${item.statusLabel}</strong>
                  ${renderNarrativeList(item.detail, "narrative-list--compact")}
                </div>
              </article>
            `
          )
          .join("")}
      </div>
      <footer class="operations-schedule-baseline">
        <span>최근 성공 중앙 소요시간</span>
        <strong>전체 갱신 ${formatDurationSeconds(overview.fullMedian)}</strong>
        <strong>빠른 갱신 ${formatDurationSeconds(overview.fastMedian)}</strong>
      </footer>
    </section>
  `;
}

function renderOperationsPage(pipelineStatus) {
  const state = pipelineRuntimeState(pipelineStatus);
  if (!pipelineStatus?.current) {
    return `
      <section class="operations-page">
        <div class="empty-state">
          <h2>운영 상태 확인 필요</h2>
          ${renderNarrativeList("pipeline-status.json 생성 여부 확인", "narrative-list--compact")}
        </div>
      </section>
    `;
  }

  const current = pipelineStatus.current;
  const runSummary = state.activeRun
    ? `${pipelineModeLabel(state.activeRun.mode)} · ${formatDurationSeconds(state.activeRun.elapsedSeconds)} 경과`
    : `최근 완료 · ${pipelineModeLabel(current.mode)} · ${formatDurationSeconds(current.durationSeconds)}`;
  const quality = pipelineStatus.quality ?? {};
  const qualitySummary = quality.summary ?? {};
  const qualityIssues = quality.issues ?? [];
  const scheduleText = (pipelineStatus.schedule?.times ?? [])
    .map((item) => `${item.time} ${pipelineModeLabel(item.mode)}`)
    .join(" · ");
  const saturdayScheduleText = (pipelineStatus.schedule?.saturdayTimes ?? [])
    .map((item) => `${item.time} ${pipelineModeLabel(item.mode)}`)
    .join(" · ");
  const mondayScheduleText = (pipelineStatus.schedule?.mondayTimes ?? pipelineStatus.schedule?.times ?? [])
    .map((item) => `${item.time} ${pipelineModeLabel(item.mode)}`)
    .join(" · ");

  return `
    <section class="operations-page">
      <header class="operations-heading">
        <div>
          <span class="eyebrow">Pipeline Operations</span>
          <h2>데이터·업데이트 운영현황</h2>
          ${renderNarrativeList(state.detail, "narrative-list--compact")}
        </div>
        <div class="operations-current operations-current--${state.tone}">
          <small>현재 판정</small>
          <strong>${state.label}</strong>
          <span>${runSummary}</span>
        </div>
      </header>

      <section class="operations-facts" aria-label="운영 요약">
        <div><small>마지막 성공</small><strong>${state.latestSuccess?.completedAt ?? "-"}</strong></div>
        <div><small>다음 예약</small><strong>${state.nextRun ? `${state.nextRun.label} · ${pipelineModeLabel(state.nextRun.mode)}` : "-"}</strong></div>
        <div><small>예약 스케줄</small><strong>월 ${mondayScheduleText || "-"} · 화~금 ${scheduleText || "-"}${saturdayScheduleText ? ` · 토 ${saturdayScheduleText}` : ""}</strong></div>
        <div><small>데이터 기준일</small><strong>${current.dataAsOf ?? "-"}</strong></div>
      </section>

      ${renderScheduleOverview(pipelineStatus)}

      <section class="operations-section">
        <div class="operations-section__heading">
          <div><span class="eyebrow">Data Completeness</span><h3>데이터 완비성</h3></div>
          <span>기준일 ${quality.referenceDate ?? current.dataAsOf ?? "-"}</span>
        </div>
        <div class="data-quality-summary">
          <div><small>완비성 점수</small><strong>${quality.score != null ? `${formatNumber(quality.score, 1)} / 100` : "-"}</strong></div>
          <div><small>원천 수집</small><strong>${qualitySummary.sourceSeriesPresent ?? "-"} / ${qualitySummary.sourceSeriesExpected ?? "-"}</strong></div>
          <div><small>허용시차 내</small><strong>${qualitySummary.freshSeries ?? "-"}개</strong></div>
          <div><small>보강·대체</small><strong>${qualitySummary.fallbackSeries ?? "-"}개</strong></div>
          <div><small>확인·오류</small><strong>${(qualitySummary.warning ?? 0) + (qualitySummary.error ?? 0)}건</strong></div>
        </div>
        ${
          qualityIssues.length
            ? `<div class="data-quality-issues">${qualityIssues
                .map(
                  (issue) => `
                    <div class="data-quality-issue data-quality-issue--${issue.status}">
                      <span>${operationStatusLabel(issue.status)}</span>
                      <strong>${issue.label}</strong>
                      ${renderNarrativeList(issue.detail, "narrative-list--compact")}
                    </div>
                  `
                )
                .join("")}</div>`
            : `<p class="data-quality-clear">필수 원천 · 최신성 · 시계열 정렬 · 산출물 기준일 검사 통과</p>`
        }
      </section>

      <section class="operations-section">
        <div class="operations-section__heading">
          <div><span class="eyebrow">Latest Run</span><h3>최근 완료 실행 단계</h3></div>
          <span>${current.startedAt} → ${current.completedAt}</span>
        </div>
        <div class="pipeline-stage-list">
          ${(pipelineStatus.stages ?? [])
            .map(
              (stage, index) => `
                <article class="pipeline-stage pipeline-stage--${stage.status}">
                  <span class="pipeline-stage__index">${index + 1}</span>
                  <div><strong>${stage.label}</strong>${renderNarrativeList(stage.detail, "narrative-list--compact")}</div>
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
          <span>원천별 관측일 범위</span>
        </div>
        <div class="operations-table-wrap">
          <table class="operations-table">
            <thead><tr><th>소스</th><th>상태</th><th>관측일 범위</th><th>시계열</th><th>완비성</th></tr></thead>
            <tbody>
              ${(pipelineStatus.sources ?? [])
                .map(
                  (source) => `
                    <tr>
                      <td><strong>${source.label}</strong></td>
                      <td><span class="operation-table-status operation-table-status--${source.status}">${operationStatusLabel(source.status)}</span></td>
                      <td>${
                        source.oldestLastDate && source.oldestLastDate !== source.lastDate
                          ? `${source.oldestLastDate} ~ ${source.lastDate}`
                          : source.lastDate ?? "-"
                      }</td>
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
                    <span>${run.scheduledTime ?? "수동"} · ${pipelineModeLabel(run.mode)}</span>
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
      reading: ["가격·수급은 위험선호에 우호적", "과열 여부 별도 확인"]
    };
  }
  if (score >= 50) {
    return {
      label: "중립 우위",
      tone: "watch",
      reading: ["위험선호 근소 우위", "방향성은 혼조"]
    };
  }
  if (score >= 35) {
    return {
      label: "Risk-off 경계",
      tone: "caution",
      reading: ["시장 부담 우세", "반등 시 변동성·수급 악화 동시 확인"]
    };
  }
  return {
    label: "Risk-off",
    tone: "danger",
    reading: ["안전자산 선호", "방어적 포지셔닝 우세"]
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
  const modelPrices = (mlRisk?.series ?? [])
    .filter((point) => Number.isFinite(Number(point.kospi)))
    .map((point) => ({ date: point.date, close: Number(point.kospi) }));
  const referencePrices = (kospi200?.ytdPriceSeries ?? [])
    .filter((point) => Number.isFinite(Number(point.close)))
    .map((point) => ({ date: point.date, close: Number(point.close) }));
  const oosSignals = (mlRisk?.walkForwardSeries ?? []).filter((point) => Number.isFinite(Number(point.crash5d5pctProbabilityPct)));
  if (modelPrices.length < horizon + 2 || oosSignals.length < 3) return null;

  const modelBase = modelPrices[0].close;
  const indexedModelPrices = modelPrices.map((point) => ({
    date: point.date,
    indexValue: (point.close / modelBase) * 100
  }));
  const referenceBase = referencePrices[0]?.close;
  const indexedReferencePrices = Number.isFinite(referenceBase)
    ? referencePrices.map((point) => ({
        date: point.date,
        indexValue: (point.close / referenceBase) * 100
      }))
    : [];
  const signalByDate = new Map(oosSignals.map((point) => [point.date, Number(point.crash5d5pctProbabilityPct)]));
  const pairs = [];
  modelPrices.forEach((point, index) => {
    const probability = signalByDate.get(point.date);
    if (probability === undefined || index + horizon >= modelPrices.length) return;
    const forwardReturn = modelPrices[index + horizon].close / point.close - 1;
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

  return {
    signalSeries: oosSignals,
    pendingSignalSeries: pendingSignals,
    modelPriceSeries: indexedModelPrices,
    referencePriceSeries: indexedReferencePrices,
    startDate: indexedModelPrices[0].date,
    endDate: indexedModelPrices[indexedModelPrices.length - 1].date,
    currentSignalDate: liveSignals.length ? liveSignals[liveSignals.length - 1].date : signalEndDate,
    correlation: pearsonCorrelation(pairs),
    observations: pairs.length,
    horizon,
    signalEndDate,
    resultKnownThroughDate,
    signalDomain
  };
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
      year,
      month,
      startX,
      endX,
      centerX: (startX + endX) / 2,
      label: segments.length === 0 || month === "01" ? `${year}.${month}` : `${Number(month)}월`
    });
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }

  return segments.filter((segment) => segment.endX > segment.startX);
}

function chartRangeDomain(seriesList, rangeId = activeChartRange) {
  const fullDomain = timelineDomain(seriesList);
  if (!fullDomain) return null;
  const option = chartRangeOptions.find((candidate) => candidate.id === rangeId) ?? chartRangeOptions.at(-1);
  const endDate = new Date(fullDomain.end);
  const requestedStart =
    option.id === "ytd"
      ? Date.UTC(endDate.getUTCFullYear(), 0, 1)
      : fullDomain.end - option.calendarDays * 24 * 60 * 60 * 1000;
  const start = Math.max(fullDomain.start, requestedStart);
  return { start, end: fullDomain.end, span: Math.max(fullDomain.end - start, 1) };
}

function pointsWithinDomain(points, domain, valueKey = null) {
  if (!domain) return [];
  return (points ?? [])
    .filter((point) => {
      const time = dateMs(point.date);
      const validValue = valueKey
        ? point[valueKey] !== null &&
          point[valueKey] !== undefined &&
          Number.isFinite(Number(point[valueKey]))
        : true;
      return validValue && Number.isFinite(time) && time >= domain.start && time <= domain.end;
    })
    .sort((left, right) => dateMs(left.date) - dateMs(right.date));
}

function numericChartDomain(points, valueKey, padding = 0, fixedDomain = null) {
  if (fixedDomain) return fixedDomain;
  const values = points.map((point) => Number(point[valueKey])).filter(Number.isFinite);
  if (!values.length) return { min: 0, max: 1 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || Math.max(Math.abs(max), 1) * 0.02;
  return { min: min - span * padding, max: max + span * padding };
}

function datedValuePath(
  points,
  valueKey,
  timeline,
  valueDomain,
  width = 760,
  plotTop = 18,
  plotBottom = 190
) {
  const valid = pointsWithinDomain(points, timeline, valueKey);
  if (valid.length < 2 || !timeline) return "";
  const span = valueDomain.max - valueDomain.min || 1;
  return valid
    .map((point, index) => {
      const x = xFromDate(point.date, timeline, width);
      const y =
        plotBottom -
        ((Number(point[valueKey]) - valueDomain.min) / span) * (plotBottom - plotTop);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function renderMonthAxisFromDomain(
  domain,
  width = 760,
  plotTop = 18,
  plotBottom = 190,
  labelY = 207
) {
  const segments = monthSegmentsFromDomain(domain, width);
  const maxLabels = Math.max(2, Math.floor(width / (width <= 320 ? 54 : 72)));
  const labelStride = Math.max(1, Math.ceil(segments.length / maxLabels));
  const labelSegments = segments.filter(
    (segment, index) =>
      index === 0 ||
      index === segments.length - 1 ||
      index % labelStride === 0
  );
  const dividerSegments =
    segments.length > 18
      ? labelSegments
      : segments.slice(1);
  return {
    grid: `
      ${
        segments.length <= 18
          ? segments
              .map(
                (segment, index) =>
                  index % 2 === 1
                    ? `<rect class="chart-month-band" x="${segment.startX.toFixed(2)}" y="${plotTop}" width="${(segment.endX - segment.startX).toFixed(2)}" height="${plotBottom - plotTop}"></rect>`
                    : ""
              )
              .join("")
          : ""
      }
      ${dividerSegments
        .map(
          (segment) =>
            `<line class="chart-month-divider" x1="${segment.startX.toFixed(2)}" x2="${segment.startX.toFixed(2)}" y1="${plotTop}" y2="${plotBottom}"></line>`
        )
        .join("")}
    `,
    labels: labelSegments
      .map(
        (segment) => {
          const edgePadding = width <= 320 ? 3 : 5;
          const estimatedHalfWidth = Math.max(8, segment.label.length * 3.5);
          const labelX = Math.max(
            edgePadding + estimatedHalfWidth,
            Math.min(width - edgePadding - estimatedHalfWidth, segment.centerX)
          );
          return `<text class="chart-month-label" x="${labelX.toFixed(2)}" y="${labelY}" text-anchor="middle">${segment.label}</text>`;
        }
      )
      .join("")
  };
}

function registerInteractiveChart({
  series,
  width = 760,
  tooltipMode = "all",
  plotLeft = 0,
  plotRight = width
}) {
  interactiveChartSequence += 1;
  const id = `timeline-chart-${interactiveChartSequence}`;
  interactiveChartRegistry.set(id, {
    width,
    tooltipMode,
    plotLeft,
    plotRight,
    series: series.map((item) => ({
      ...item,
      points: [...(item.points ?? [])].sort((left, right) => dateMs(left.date) - dateMs(right.date))
    }))
  });
  return id;
}

function chartRangeLayerClass(rangeId) {
  return `chart-range-layer${rangeId === activeChartRange ? " is-active" : ""}`;
}

function renderChartRangeButtons(chartId, ariaLabel = "시계열 조회 기간") {
  return `
    <div class="chart-range-control" role="group" aria-label="${ariaLabel}">
      ${chartRangeOptions
        .map(
          (option) => `
            <button
              type="button"
              class="${option.id === activeChartRange ? "is-active" : ""}"
              data-chart-range="${option.id}"
              data-chart-id="${chartId}"
              aria-pressed="${option.id === activeChartRange ? "true" : "false"}"
            >${option.label}</button>
          `
        )
        .join("")}
    </div>
  `;
}

function renderChartRangeControls(chartId, { hasProvisional = false } = {}) {
  return `
    <div class="chart-range-toolbar">
      ${renderChartRangeButtons(chartId)}
      <span class="chart-value-status">
        <i class="chart-value-status__eod"></i>EOD
        ${hasProvisional ? `<i class="chart-value-status__provisional"></i>잠정` : ""}
      </span>
    </div>
  `;
}

function renderMarketChartRangeDock() {
  const activeLabel =
    chartRangeOptions.find((option) => option.id === activeChartRange)?.label ?? "YTD";
  return `
    <div class="market-chart-range-dock" role="region" aria-label="시장리스크 공통 그래프 기간">
      <div class="market-chart-range-dock__label">
        <span>전체 시계열</span>
        <strong data-chart-active-range-label>${activeLabel}</strong>
      </div>
      ${renderChartRangeButtons("market-global", "시장리스크 전체 시계열 조회 기간")}
    </div>
  `;
}

function renderChartCursorLine(y1, y2) {
  return `<line class="chart-cursor-line" data-chart-cursor-line x1="0" x2="0" y1="${y1}" y2="${y2}"></line>`;
}

function renderChartTooltip() {
  return `<div class="chart-cursor-tooltip" data-chart-tooltip role="status" aria-live="polite"></div>`;
}

function renderElsIndexRiskPanel(elsRisk) {
  if (!elsRisk?.indices?.length || !elsRisk?.basket) return "";

  const sorted = [...elsRisk.indices].sort((a, b) => Number(b.score) - Number(a.score));
  const basket = elsRisk.basket;
  const colorClass = {
    spx: "els-line--spx",
    sx5e: "els-line--sx5e",
    nky: "els-line--nky",
    hscei: "els-line--hscei",
    kospi200: "els-line--kospi200"
  };
  const cursorColor = {
    spx: "var(--blue)",
    sx5e: "var(--teal)",
    nky: "var(--amber)",
    hscei: "var(--red)",
    kospi200: "var(--green)"
  };
  const chartId = registerInteractiveChart({
    series: elsRisk.indices.map((item) => ({
      label: item.label,
      points: item.series,
      valueKey: "score",
      color: cursorColor[item.id],
      format: (value) => `${Number(value).toFixed(1)}점`,
      detail: (point) => `20D ${formatSignedPct(point.return20dPct)}`
    }))
  });
  const rangeLayers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain(
        elsRisk.indices.map((item) => item.series ?? []),
        range.id
      );
      const monthAxis = renderMonthAxisFromDomain(domain);
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img">
          ${monthAxis.grid}
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          ${elsRisk.indices
            .map(
              (item) =>
                `<path class="els-index-line ${colorClass[item.id] ?? ""}" d="${datedValuePath(item.series, "score", domain, { min: 0, max: 100 })}"></path>`
            )
            .join("")}
          ${renderChartCursorLine(18, 190)}
          ${monthAxis.labels}
        </svg>
      `;
    })
    .join("");

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
          <h3>${basket.worstIndex} 주도 · ${basket.bucket}</h3>
          ${renderNarrativeList([
            `${basket.worstIndex}: Basket 리스크 1순위`,
            `${basket.secondWorstIndex}: 취약도 2순위`,
            "평균이 아닌 worst-of 구조",
            "최고 위험지수 50% · 차순위 취약지수 20%",
            "평균 점수 15% · 동조화 점수 15%"
          ], "narrative-list--compact")}
        </article>
        <article>
          <span class="eyebrow">동조화 점수</span>
          <h3>${Number(basket.correlationScore).toFixed(1)} / 100</h3>
          ${renderNarrativeList([
            "높은 지수 간 상관 = 동시 순연·헤지비용 부담 확대",
            `평균 개별 점수 ${Number(basket.averageIndexScore).toFixed(1)}`
          ], "narrative-list--compact")}
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
                    <small class="els-index-card__asof">EOD ${item.lastDate ?? "-"}</small>
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
                ${renderNarrativeList(item.reading, "narrative-list--compact")}
              </article>
            `
          )
          .join("")}
      </div>

      <div class="els-index-chart" data-timeseries-chart="${chartId}" aria-label="기초지수별 ELS 리스크 점수 흐름">
        ${renderChartRangeControls(chartId)}
        ${rangeLayers}
        <div class="els-index-legend">
          ${elsRisk.indices
            .map((item) => `<span><i class="${colorClass[item.id] ?? ""}"></i>${item.label}<small>${item.lastDate ?? "-"}</small></span>`)
            .join("")}
        </div>
        ${renderChartTooltip()}
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
          ${renderNarrativeList(episode.interpretation, "narrative-list--compact els-episode-interpretation")}
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
        ${renderNarrativeList(stressEpisodes.methodology, "narrative-list--compact")}
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

function renderElsSingleStockSection(items, methodology) {
  if (!items?.length) return "";

  return `
    <section class="els-single-stock-section">
      <header class="els-single-stock-section__header">
        <div>
          <span class="eyebrow">Single Stock Reference</span>
          <h3>개별종목 참고</h3>
        </div>
        <div class="els-single-stock-section__scope" aria-label="개별종목 적용 범위">
          <span>지도 동시 표시</span>
          <span>Basket 제외</span>
          <span>에피소드 제외</span>
        </div>
      </header>
      ${renderNarrativeList(methodology, "narrative-list--compact els-single-stock-section__note")}
      <div class="els-single-stock-grid">
        ${items
          .map(
            (item) => `
              <article class="els-single-stock-card els-single-stock-card--${item.id}">
                <header>
                  <div>
                    <span>${item.region} · ${item.lastDate}</span>
                    <strong>${item.name}</strong>
                  </div>
                  <em class="els-single-stock-card__stance els-single-stock-card__stance--${item.tone}">${item.stance}</em>
                </header>
                <div class="els-single-stock-card__scores">
                  <div>
                    <span>발행기회</span>
                    <strong>${Number(item.opportunityScore).toFixed(1)}</strong>
                    <i><b class="els-score-bar--opportunity" style="width:${clampScore(item.opportunityScore)}%"></b></i>
                  </div>
                  <div>
                    <span>헤지부담</span>
                    <strong>${Number(item.hedgeBurdenScore).toFixed(1)}</strong>
                    <i><b class="els-score-bar--burden" style="width:${clampScore(item.hedgeBurdenScore)}%"></b></i>
                  </div>
                </div>
                <dl>
                  <div><dt>20D 수익률</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                  <div><dt>20D 변동성</dt><dd>${Number(item.metrics.realizedVol20dPct).toFixed(1)}%</dd></div>
                  <div><dt>252D 낙폭</dt><dd>${formatSignedPct(item.metrics.drawdown252dPct)}</dd></div>
                </dl>
                ${renderNarrativeList(item.interpretation, "narrative-list--compact")}
              </article>
            `
          )
          .join("")}
      </div>
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
          ${renderNarrativeList("다음 데이터 갱신 후 기초지수별 상대 발행기회·헤지부담 산출", "narrative-list--compact")}
        </div>
      </section>
    `;
  }

  const singleStockItems = map.singleStocks ?? [];
  const mapItems = [...singleStockItems, ...map.items];
  const plot = { left: 66, top: 24, width: 654, height: 310 };
  const markerOffsets = {
    spx: { dx: 10, dy: -10, anchor: "start" },
    sx5e: { dx: 10, dy: -10, anchor: "start" },
    nky: { dx: 10, dy: 20, anchor: "start" },
    hscei: { dx: 10, dy: 20, anchor: "start" },
    kospi200: { dx: -10, dy: 20, anchor: "end" },
    samsung: { dx: -11, dy: 22, anchor: "end" },
    hynix: { dx: 11, dy: 22, anchor: "start" }
  };
  const markerNudges = {
    hynix: { dx: 12, dy: -10 }
  };
  const points = mapItems
    .map((item) => {
      const opportunity = clampScore(item.opportunityScore);
      const burden = clampScore(item.hedgeBurdenScore);
      const x = plot.left + (opportunity / 100) * plot.width;
      const y = plot.top + ((100 - burden) / 100) * plot.height;
      const offset = markerOffsets[item.id] ?? { dx: 10, dy: -10, anchor: "start" };
      const nudge = markerNudges[item.id] ?? { dx: 0, dy: 0 };
      const markerX = x + nudge.dx;
      const markerY = y + nudge.dy;
      const isSingleStock = item.assetType === "single-stock";
      const marker = isSingleStock
        ? `<path d="M ${markerX.toFixed(1)} ${(markerY - 8).toFixed(1)} L ${(markerX + 8).toFixed(1)} ${markerY.toFixed(1)} L ${markerX.toFixed(1)} ${(markerY + 8).toFixed(1)} L ${(markerX - 8).toFixed(1)} ${markerY.toFixed(1)} Z" class="els-map-stock-marker"></path>`
        : `<circle cx="${markerX.toFixed(1)}" cy="${markerY.toFixed(1)}" r="7"></circle>`;
      const leader =
        nudge.dx || nudge.dy
          ? `<path d="M ${x.toFixed(1)} ${y.toFixed(1)} L ${markerX.toFixed(1)} ${markerY.toFixed(1)}" class="els-map-point-leader"></path>`
          : "";
      return `
        <g class="els-map-point els-map-point--${item.id}${isSingleStock ? " els-map-point--single-stock" : ""}">
          <title>${item.label}: 발행기회 ${opportunity.toFixed(1)}, 헤지부담 ${burden.toFixed(1)}</title>
          ${leader}
          ${marker}
          <text x="${(markerX + offset.dx).toFixed(1)}" y="${(markerY + offset.dy).toFixed(1)}" text-anchor="${offset.anchor}">${item.label}</text>
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
      const tracks = mapItems
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
            <g class="els-map-trajectory-series els-map-trajectory-series--${item.id}${item.assetType === "single-stock" ? " els-map-trajectory-series--single-stock" : ""}">
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
  const trajectoryArrowMarkers = mapItems
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
          ${renderNarrativeList([
            "변동성 기반 상대 발행기회",
            "기존 북의 순연·헤지비용 부담"
          ], "narrative-list--compact")}
        </div>
        <div class="els-basket-state els-basket-state--${map.basket.tone}">
          <span>5개 지수 Basket 판단</span>
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
            <h3>현재 기초자산 포지셔닝</h3>
          </div>
          <div class="els-opportunity-map__aside">
            ${renderNarrativeList([
              "5개 지수만 Basket 점수에 반영",
              "개별종목은 토글로 선택 표시 · Basket 미반영"
            ], "narrative-list--compact")}
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
              <label class="els-stock-visibility-toggle" title="삼성전자·SK하이닉스 현재점과 궤적 표시">
                <input type="checkbox" data-els-stock-toggle />
                <span>개별종목</span>
                <em data-els-stock-state>OFF</em>
              </label>
              <div class="els-trajectory-legend" aria-label="궤적 범례">
                <span><i class="els-trajectory-legend__start"></i>시작</span>
                <span><i class="els-trajectory-legend__current"></i>현재</span>
                <span data-els-momentum-legend><i class="els-trajectory-legend__momentum"></i>1주 방향</span>
              </div>
              <div class="els-asset-type-legend" aria-label="기초자산 유형 범례">
                <span><i class="els-asset-type-legend__index"></i>지수</span>
                <span data-els-stock-dependent hidden><i class="els-asset-type-legend__stock"></i>개별종목</span>
              </div>
            </div>
          </div>
        </div>
        <div class="els-opportunity-map__scroll">
          <svg viewBox="0 0 760 410" role="img" aria-label="기초자산별 상대 발행기회와 헤지부담 분포">
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
                  ${renderNarrativeList(item.interpretation, "narrative-list--compact")}
                </article>
              `
            )
            .join("")}
        </div>
      </section>

      <section class="els-methodology">
        <article><span>발행기회 산식</span>${renderNarrativeList(map.methodology.opportunity, "narrative-list--compact")}</article>
        <article><span>헤지부담 산식</span>${renderNarrativeList(map.methodology.hedgeBurden, "narrative-list--compact")}</article>
        <article><span>판단 기준</span>${renderNarrativeList(map.methodology.classification, "narrative-list--compact")}</article>
      </section>
      <aside class="els-limitations"><strong>운영 적용 전 확인</strong>${renderNarrativeList(map.limitations, "narrative-list--compact")}</aside>
      ${renderElsSingleStockSection(singleStockItems, map.singleStockMethodology)}
      ${renderElsStressEpisodeReview(map.stressEpisodes, plot)}
    </section>
  `;
}

function renderHmmMonthRail(domain, rangeId) {
  const segments = monthSegmentsFromDomain(domain, 100);
  if (!segments.length) return "";

  return `
    <div class="hmm-regime-month-rail ${chartRangeLayerClass(rangeId)}" data-chart-range-layer="${rangeId}" aria-hidden="true">
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
  const timelineSeries = hmmRegime.indices.map((item) => item.series ?? []);
  const timelineDomains = Object.fromEntries(
    chartRangeOptions.map((range) => [range.id, chartRangeDomain(timelineSeries, range.id)])
  );
  const timelineAxis = chartRangeOptions
    .map((range) => renderHmmMonthRail(timelineDomains[range.id], range.id))
    .join("");
  const colorClass = {
    spx: "hmm-line--spx",
    sx5e: "hmm-line--sx5e",
    nky: "hmm-line--nky",
    hscei: "hmm-line--hscei",
    kospi200: "hmm-line--kospi200"
  };
  const cursorColor = {
    spx: "var(--blue)",
    sx5e: "var(--teal)",
    nky: "var(--amber)",
    hscei: "var(--red)",
    kospi200: "var(--green)"
  };
  const chartId = registerInteractiveChart({
    series: hmmRegime.indices.map((item) => ({
      label: item.label,
      points: item.series,
      valueKey: "issuerScore",
      color: cursorColor[item.id],
      format: (value) => `부담 ${Number(value).toFixed(1)}`,
      detail: (point) => point.regime
    }))
  });
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
          <h3>상승형 고변동성과 위험회피 분리</h3>
          ${renderNarrativeList(hmmRegime.designNote, "narrative-list--compact")}
        </article>
        <article>
          <span class="eyebrow">Basket 해석</span>
          <h3>${basket.regime}</h3>
          ${renderNarrativeList([
            `${basket.highestRiskOffIndex}: 위험회피 확률 최고`,
            `${basket.highestIssuerScoreIndex}: 발행·헤지 부담 최고`,
            `평균 발행·헤지 부담 ${Number(basket.averageIssuerScore).toFixed(1)}`,
            `상태 구성 위험회피 ${basket.riskOffCount} · 활황 ${basket.highVolBullCount} · 안정 ${basket.stableCount}`
          ], "narrative-list--compact")}
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
                ${renderNarrativeList(item.reading, "narrative-list--compact")}
                <small>${regimeText(item)} · 신뢰도 ${Number(item.confidencePct).toFixed(1)}%</small>
              </article>
            `
          )
          .join("")}
      </div>

      <div class="hmm-regime-timeline" data-timeseries-chart="${chartId}" aria-label="국가별 HMM 레짐 흐름">
        <div class="hmm-regime-timeline__header">
          <div>
            <span class="eyebrow">Regime Timeline</span>
            <h3>지수별 레짐 흐름</h3>
          </div>
          <div class="chart-panel-tools">
            ${renderChartRangeControls(chartId)}
            <div class="hmm-regime-key" aria-label="레짐 색상">
              <span><i class="hmm-regime-strip__cell--good"></i>안정</span>
              <span><i class="hmm-regime-strip__cell--caution"></i>고변동성 활황</span>
              <span><i class="hmm-regime-strip__cell--danger"></i>위험회피</span>
            </div>
          </div>
        </div>

        ${timelineAxis}

        <div class="hmm-regime-rows">
          ${hmmRegime.indices
          .map((item, seriesIndex) => {
            const rangeLayers = chartRangeOptions
              .map((range) => {
                const domain = timelineDomains[range.id];
                const visible = pointsWithinDomain(item.series, domain, "issuerScore");
                return `
                  <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg data-chart-series-index="${seriesIndex}" viewBox="0 0 260 86" preserveAspectRatio="none" role="img" aria-label="${item.label} HMM 레짐과 부담 점수">
                    ${renderHmmMonthGuides(domain)}
                    <rect class="hmm-regime-band-bg" x="0" y="4" width="260" height="12"></rect>
                    ${renderHmmRegimeBands(visible, domain)}
                    <path class="hmm-regime-spark-grid" d="M 0 30 L 260 30 M 0 48 L 260 48 M 0 66 L 260 66"></path>
                    <path class="hmm-regime-spark ${colorClass[item.id] ?? ""}" d="${scorePathByDatePlot(visible, "issuerScore", 260, 26, 80, domain)}"></path>
                    ${renderChartCursorLine(4, 80)}
                  </svg>
                `;
              })
              .join("");
            return `
              <article class="hmm-regime-row hmm-regime-row--${item.tone}">
                <div class="hmm-regime-row__meta">
                  <strong>${item.label}</strong>
                  <span>${item.region} · ${item.volSource}</span>
                  <small>${item.regime} · 부담 ${Number(item.issuerScore).toFixed(1)}</small>
                </div>
                <div class="hmm-regime-row__track">
                  ${rangeLayers}
                </div>
                <dl class="hmm-regime-row__stats">
                  <div><dt>위험회피</dt><dd>${Number(item.probabilities["위험회피"]).toFixed(1)}%</dd></div>
                  <div><dt>활황</dt><dd>${Number(item.probabilities["고변동성 활황"]).toFixed(1)}%</dd></div>
                  <div><dt>20D</dt><dd>${formatSignedPct(item.metrics.return20dPct)}</dd></div>
                </dl>
              </article>
            `;
          })
          .join("")}
        </div>
        ${renderChartTooltip()}
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
  const latestSignalPoints = comparison
    ? [...comparison.signalSeries, ...comparison.pendingSignalSeries.slice(1)]
    : series;
  const chartId = registerInteractiveChart({
    series: comparison
      ? [
          {
            label: "ML 5D -5%",
            points: latestSignalPoints,
            valueKey: "crash5d5pctProbabilityPct",
            color: "var(--red)",
            format: (value) => `${Number(value).toFixed(1)}%`,
            status: (point) => (point.date > comparison.signalEndDate ? "잠정" : "EOD")
          },
          {
            label: "KOSPI · 모델 대상",
            points: comparison.modelPriceSeries,
            valueKey: "indexValue",
            color: "var(--green)",
            format: (value) => `${Number(value).toFixed(1)}`
          },
          {
            label: "KOSPI200 · ELS 참고",
            points: comparison.referencePriceSeries,
            valueKey: "indexValue",
            color: "var(--blue)",
            format: (value) => `${Number(value).toFixed(1)}`
          }
        ]
      : [
          {
            label: "20D Risk-off",
            points: series,
            valueKey: "riskOffProbabilityPct",
            color: "var(--red)",
            format: (value) => `${Number(value).toFixed(1)}%`
          }
        ]
  });
  const mlRangeLayers = chartRangeOptions
    .map((range) => {
      const sourceSeries = comparison
        ? [
            comparison.signalSeries,
            comparison.pendingSignalSeries,
            comparison.modelPriceSeries,
            comparison.referencePriceSeries
          ]
        : [series];
      const domain = chartRangeDomain(sourceSeries, range.id);
      const monthAxis = renderMonthAxisFromDomain(domain);
      const visibleSignals = comparison
        ? pointsWithinDomain(comparison.signalSeries, domain, "crash5d5pctProbabilityPct")
        : pointsWithinDomain(series, domain, "riskOffProbabilityPct");
      const visiblePending = comparison
        ? pointsWithinDomain(
            comparison.pendingSignalSeries,
            domain,
            "crash5d5pctProbabilityPct"
          )
        : [];
      const visibleModelPrices = comparison
        ? pointsWithinDomain(comparison.modelPriceSeries, domain, "indexValue")
        : [];
      const visibleReferencePrices = comparison
        ? pointsWithinDomain(comparison.referencePriceSeries, domain, "indexValue")
        : [];
      const signalValueKey = comparison
        ? "crash5d5pctProbabilityPct"
        : "riskOffProbabilityPct";
      const signalValueDomain = numericChartDomain(
        [...visibleSignals, ...visiblePending],
        signalValueKey,
        0.06
      );
      const priceValueDomain = numericChartDomain(
        [...visibleModelPrices, ...visibleReferencePrices],
        "indexValue",
        0.06
      );
      const riskPath = datedValuePath(
        visibleSignals,
        signalValueKey,
        domain,
        signalValueDomain
      );
      const pendingRiskPath = comparison
        ? datedValuePath(
            visiblePending,
            "crash5d5pctProbabilityPct",
            domain,
            signalValueDomain
          )
        : "";
      const kospiPath = comparison
        ? datedValuePath(
            visibleModelPrices,
            "indexValue",
            domain,
            priceValueDomain
          )
        : "";
      const kospi200Path = comparison
        ? datedValuePath(
            visibleReferencePrices,
            "indexValue",
            domain,
            priceValueDomain
          )
        : "";
      const cutoffX = comparison
        ? Math.max(0, Math.min(760, xFromDate(comparison.signalEndDate, domain, 760)))
        : 760;
      const pendingArea =
        comparison && visiblePending.length > 1 && cutoffX < 760
          ? `<rect class="ml-risk-chart__pending" x="${cutoffX.toFixed(2)}" y="18" width="${(760 - cutoffX).toFixed(2)}" height="172"></rect><line class="ml-risk-chart__cutoff" x1="${cutoffX.toFixed(2)}" y1="18" x2="${cutoffX.toFixed(2)}" y2="190"></line>`
          : "";
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img">
          ${pendingArea}
          ${monthAxis.grid}
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          <path class="ml-risk-chart__risk" d="${riskPath}"></path>
          <path class="ml-risk-chart__risk-pending" d="${pendingRiskPath}"></path>
          <path class="ml-risk-chart__kospi" d="${kospiPath}"></path>
          <path class="ml-risk-chart__kospi200" d="${kospi200Path}"></path>
          ${renderChartCursorLine(18, 190)}
          ${monthAxis.labels}
        </svg>
      `;
    })
    .join("");
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
    ? "워크포워드 관측치 누적 후 선행성 산출"
    : leadCorrelation <= -0.2
      ? "확률 상승 뒤 KOSPI 수익률 하락 패턴 관찰"
      : leadCorrelation >= 0.2
        ? "YTD 표본에서 기대한 역방향 선행 패턴 미확인"
        : "YTD 표본의 선행 관계 약함";
  const explanationItems = [
    `현재 스트레스: ${marketLevel.label} · ${Number.isFinite(marketScore) ? marketScore.toFixed(1) : "-"}`,
    `5D 급락 전망: -5% ${crash5pct.toFixed(1)}% · -10% ${crash10pct.toFixed(1)}%`,
    `20D 레짐 Risk-off: ${Number(latest.riskOffProbabilityPct).toFixed(1)}% · 5D 급락확률과 별도 해석`,
    `추가 악화 탐지 ML: recall ${Number((ml.riskOffRecall ?? 0) * 100).toFixed(1)}% · AUC ${Number(ml.riskOffAuc ?? 0).toFixed(3)}`,
    `기준모델: recall ${Number((baseline.riskOffRecall ?? 0) * 100).toFixed(1)}% · AUC ${Number(baseline.riskOffAuc ?? 0).toFixed(3)}`,
    `급락 OOS PR-AUC: -5% ${Number(crash5pctMetrics.averagePrecision ?? 0).toFixed(3)} · -10% ${Number(crash10pctMetrics.averagePrecision ?? 0).toFixed(3)}`,
    `실제 급락 표본: -5% ${Number(crash5pctMetrics.eventCount ?? 0).toFixed(0)}건 · -10% ${Number(crash10pctMetrics.eventCount ?? 0).toFixed(0)}건`,
    `확률 상위 10% 적중률: -5% ${Number((crash5pctMetrics.topDecileHitRate ?? 0) * 100).toFixed(1)}% · -10% ${Number((crash10pctMetrics.topDecileHitRate ?? 0) * 100).toFixed(1)}%`,
    crash10pctValidated ? "-10% 급락확률 OOS 선별력 확인" : "-10% 급락확률은 희소사건 보조 경보",
    crash5pctCalibrated && crash10pctCalibrated
      ? "두 급락확률의 Brier score가 기준모델보다 양호"
      : "Brier 열위 확률은 발생빈도보다 위험 순위 중심으로 해석",
    ...(mlRisk.interpretation ?? [])
  ];

  return `
    <section class="ml-risk-panel">
      <div class="ml-risk-panel__header">
        <div>
          <span class="eyebrow">Current Stress vs Forward Risk</span>
          <h2>현재 스트레스와 KOSPI 향후 5일 급락 전망</h2>
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
          <p>가격·변동성·환율·수급에서 관측된 현재 부담</p>
        </article>
        <div class="ml-risk-horizons__divider" aria-hidden="true"></div>
        <article>
          <span class="eyebrow">미래 · KOSPI 5D -5% 도달 전망</span>
          <strong>${crash5pct.toFixed(1)}%</strong>
          ${renderNarrativeList([
            "KOSPI가 향후 5거래일 중 현재가 대비 -5% 이하 도달할 확률",
            crash5pctValidated ? "OOS 선별력 확인" : "OOS 선별력 미확인 · 연구 참고값"
          ], "narrative-list--compact")}
        </article>
      </div>

      <div class="ml-risk-grid">
        ${createMetricCard({
          label: "KOSPI 5D -5% 도달확률",
          value: `${crash5pct.toFixed(1)}%`,
          meta: `5일 내 최저수익률 -5% 이하 · ${crash5pctValidated ? "선별력 통과" : "연구 참고값"} · ${crash5pctCalibrated ? "확률 보정 양호" : "확률 보정 주의"}`,
          tone: crashTone(crash5pct, crash5pctMetrics)
        })}
        ${createMetricCard({
          label: "KOSPI 5D -10% 도달확률",
          value: `${crash10pct.toFixed(1)}%`,
          meta: `5일 내 최저수익률 -10% 이하 · ${crash10pctValidated ? "선별력 통과" : "희소사건 참고값"} · ${crash10pctCalibrated ? "확률 보정 양호" : "확률 보정 주의"}`,
          tone: crashTone(crash10pct, crash10pctMetrics)
        })}
        ${createMetricCard({
          label: "KOSPI 20D 레짐 Risk-off",
          value: `${Number(latest.riskOffProbabilityPct).toFixed(1)}%`,
          meta: `${decisionText} · 변동성·낙폭 포함`,
          tone: riskTone
        })}
        ${createMetricCard({
          label: "KOSPI 20D 변동성",
          value: `${Number(latest.realizedVol20dPct).toFixed(1)}%`,
          meta: latest.baselineRiskOffSignal ? "현재 기준모델 risk-off" : "현재 기준모델 중립",
          tone: latest.realizedVol20dPct >= 35 ? "caution" : "watch"
        })}
      </div>

      <div class="ml-risk-body">
        <div class="ml-risk-chart" data-timeseries-chart="${chartId}" aria-label="KOSPI 5일 -5% 급락확률과 KOSPI·KOSPI200 흐름 비교">
          <div class="ml-risk-chart__header">
            <div class="ml-risk-chart__heading">
              <strong>KOSPI 5일 급락신호와 지수 흐름</strong>
              <span>모델 평가 KOSPI · ELS 참고 KOSPI200 · 현재 신호 ${formatShortDate(comparison?.currentSignalDate)} · OOS 결과 ${formatShortDate(comparison?.resultKnownThroughDate)}까지</span>
            </div>
            ${renderChartRangeControls(chartId, {
              hasProvisional: Boolean(comparison?.pendingSignalSeries?.length > 1)
            })}
          </div>
          ${mlRangeLayers}
          <div class="ml-risk-chart__legend">
            <span><i class="legend-risk"></i>ML KOSPI 5D -5% 도달확률 · OOS</span>
            <span><i class="legend-risk-pending"></i>현재까지의 최신 예측</span>
            <span><i class="legend-kospi"></i>KOSPI · 모델 평가대상 · 연초=100</span>
            <span><i class="legend-kospi200"></i>KOSPI200 · ELS 참고선 · 연초=100</span>
            <span><i class="legend-pending"></i>향후 5거래일 결과 대기</span>
          </div>
          ${renderNarrativeList([
            `KOSPI 5D 선행상관 ${leadCorrelationText} · ${comparison?.observations ?? 0}개 표본`,
            leadReading,
            "KOSPI200은 ELS 기초자산 참고선 · 모델 적중률·상관 산정 제외",
            "점선 구간: 최신 예측 · 향후 5거래일 결과 대기 · OOS 평가 제외"
          ], "narrative-list--compact ml-risk-chart__note")}
          ${renderChartTooltip()}
        </div>

        <div class="ml-risk-explain">
          <strong>시간축을 나눠 읽으세요</strong>
          ${renderNarrativeList(explanationItems, "ml-risk-explain__list")}
        </div>
      </div>
    </section>
  `;
}

function renderCompositeTrend(section, timeseries) {
  const points = buildCompositeSeries(section, timeseries);
  if (points.length < 2) return "";

  const latest = points[points.length - 1];
  const change1d = valueChange(latest.value, points, 1);
  const change1w = valueChange(latest.value, points, 5);
  const change1m = valueChange(latest.value, points, 20);
  const minPoint = points.reduce((min, point) => (point.value < min.value ? point : min), points[0]);
  const maxPoint = points.reduce((max, point) => (point.value > max.value ? point : max), points[0]);
  const chartId = registerInteractiveChart({
    series: [
      {
        label: "종합점수",
        points,
        valueKey: "value",
        color: "var(--teal)",
        format: (value) => `${Number(value).toFixed(1)}점`
      }
    ]
  });
  const rangeLayers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([points], range.id);
      const visible = pointsWithinDomain(points, domain, "value");
      const values = visible.map((point) => clampScore(point.value));
      const valueDomain = values.length
        ? {
            min: Math.max(0, Math.min(...values) - 5),
            max: Math.min(100, Math.max(...values) + 5)
          }
        : { min: 0, max: 100 };
      const path = datedValuePath(visible, "value", domain, valueDomain);
      const firstX = visible.length ? xFromDate(visible[0].date, domain, 760) : 0;
      const lastX = visible.length ? xFromDate(visible.at(-1).date, domain, 760) : 760;
      const areaPath = path
        ? `${path} L ${lastX.toFixed(2)} 190 L ${firstX.toFixed(2)} 190 Z`
        : "";
      const monthAxis = renderMonthAxisFromDomain(domain);
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img">
          ${monthAxis.grid}
          <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
          <path class="trend-chart__area" d="${areaPath}"></path>
          <path class="trend-chart__line" d="${path}"></path>
          ${renderChartCursorLine(18, 190)}
          ${monthAxis.labels}
        </svg>
      `;
    })
    .join("");

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
      <div class="trend-chart" data-timeseries-chart="${chartId}" aria-label="시장리스크 종합점수 시계열">
        ${renderChartRangeControls(chartId)}
        ${rangeLayers}
        ${renderChartTooltip()}
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

function marketTrendCoordinates(points, domain = null, width = 180, height = 52, padding = 4) {
  if (points.length < 2) return [];
  const values = points.map((point) => Number(point.close)).filter(Number.isFinite);
  if (values.length < 2) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || Math.max(Math.abs(max), 1) * 0.01;
  return points
    .map((point, index) => {
      const x = domain ? xFromDate(point.date, domain, width) : (index / (points.length - 1)) * width;
      const y = height - padding - ((Number(point.close) - min) / range) * (height - padding * 2);
      return { x, y };
    });
}

function marketTrendPath(coordinates) {
  return coordinates
    .map(
      (point, index) =>
        `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    )
    .join(" ");
}

function marketTrendChange(rows, offset, type) {
  if (!rows?.length || rows.length <= offset) return null;
  const latest = Number(rows[rows.length - 1].close);
  const base = Number(rows[rows.length - 1 - offset].close);
  if (!Number.isFinite(latest) || !Number.isFinite(base) || base === 0) return null;
  return type === "yield" ? (latest - base) * 100 : (latest / base - 1) * 100;
}

function marketTrendRangeMetric(rows, domain, type, frequency) {
  const visible = pointsWithinDomain(rows, domain, "close");
  if (visible.length < 2) {
    return { change: null, isComplete: false, coverageLabel: "구간 미충족", firstDate: null };
  }

  const firstDate = visible[0].date;
  const lastDate = visible.at(-1).date;
  const dayMs = 24 * 60 * 60 * 1000;
  const coverageDays = Math.max(0, (dateMs(lastDate) - dateMs(firstDate)) / dayMs);
  const toleranceDays = frequency === "weekly" ? 10 : 7;
  const isComplete = dateMs(firstDate) <= domain.start + toleranceDays * dayMs;
  const coverageLabel =
    coverageDays >= 335
      ? `${(coverageDays / 365.25).toFixed(1)}Y`
      : coverageDays >= 45
        ? `${Math.max(1, Math.round(coverageDays / 30.44))}M`
        : `${Math.max(1, Math.round(coverageDays / 7))}W`;
  return {
    change: marketTrendChange(visible, visible.length - 1, type),
    isComplete,
    coverageLabel,
    firstDate
  };
}

function formatMarketTrendChange(value, type) {
  if (!Number.isFinite(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return type === "yield" ? `${sign}${value.toFixed(1)}bp` : `${sign}${value.toFixed(2)}%`;
}

function formatMarketTrendCurrentComparison(pointValue, currentValue, type) {
  const point = Number(pointValue);
  const current = Number(currentValue);
  if (!Number.isFinite(point) || !Number.isFinite(current) || point === 0) return "";

  const rawChange = current - point;
  if (Math.abs(rawChange) < 1e-10) return "현재와 동일";
  if (type === "yield") {
    return `현재까지 ${formatMarketTrendChange(rawChange * 100, type)}`;
  }

  const scale = Math.max(Math.abs(point), Math.abs(current));
  const digits =
    type === "fx"
      ? scale >= 100
        ? 2
        : 4
      : type === "index"
        ? scale >= 1000
          ? 1
          : 2
        : scale >= 1000
          ? 1
          : scale >= 10
            ? 2
            : 4;
  const rawText = `${rawChange > 0 ? "+" : ""}${formatNumber(rawChange, digits)}`;
  const percentChange = (current / point - 1) * 100;
  return `현재까지 ${rawText} · ${formatMarketTrendChange(percentChange, type)}`;
}

function formatMarketTrendValue(value, type) {
  if (!Number.isFinite(Number(value))) return "-";
  const number = Number(value);
  if (type === "yield") return `${number.toFixed(3)}%`;
  if (type === "fx") return number >= 100 ? number.toFixed(2) : number.toFixed(4);
  if (number >= 1000) return formatNumber(number, 1);
  if (number >= 100) return number.toFixed(2);
  return number.toFixed(4);
}

function formatMarketSnapshotTime(observedAt) {
  const parsed = new Date(observedAt);
  if (Number.isNaN(parsed.getTime())) return String(observedAt).slice(11, 16);
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).format(parsed);
}

function usableMarketLiveSnapshot(definition, marketIndexes, confirmedLatest) {
  const snapshot = marketIndexes?.liveSnapshots?.[definition.id];
  if (
    !snapshot?.isProvisional ||
    !Number.isFinite(Number(snapshot.close)) ||
    !snapshot.date ||
    typeof snapshot.observedAt !== "string" ||
    snapshot.observedAt.length < 16 ||
    snapshot.date < confirmedLatest.date
  ) {
    return null;
  }
  return {
    date: snapshot.date,
    close: Number(snapshot.close),
    observedAt: snapshot.observedAt,
    isLive: true
  };
}

function analyzeMarketTrend(definition, marketIndexes) {
  const metadata = marketIndexes?.metadata?.[definition.id];
  const confirmedRows = (marketIndexes?.series?.[definition.id] ?? [])
    .filter((point) => Number.isFinite(Number(point.close)))
    .sort((left, right) => String(left.date).localeCompare(String(right.date)));
  if (!metadata || confirmedRows.length < 2) return null;

  const livePoint = usableMarketLiveSnapshot(
    definition,
    marketIndexes,
    confirmedRows[confirmedRows.length - 1]
  );
  const rows = livePoint
    ? livePoint.date === confirmedRows[confirmedRows.length - 1].date
      ? [...confirmedRows.slice(0, -1), livePoint]
      : [...confirmedRows, livePoint]
    : confirmedRows;

  const weekly = metadata.frequency === "weekly";
  const oneWeekOffset = weekly ? 1 : 5;
  const oneMonthOffset = weekly ? 4 : 20;
  const recentWindow = rows.slice(-(weekly ? 7 : 11));
  const changes = recentWindow
    .slice(1)
    .map((point, index) => Number(point.close) - Number(recentWindow[index].close))
    .filter((value) => value !== 0);
  const upCount = changes.filter((value) => value > 0).length;
  const downCount = changes.filter((value) => value < 0).length;
  const upShare = changes.length ? upCount / changes.length : 0.5;
  const monthChange = marketTrendChange(rows, oneMonthOffset, definition.type);
  const meaningfulThreshold = definition.type === "yield" ? 3 : 0.5;
  const meaningful = Number.isFinite(monthChange) && Math.abs(monthChange) >= meaningfulThreshold;
  let direction = "flat";
  let persistent = false;
  if (meaningful && monthChange > 0) {
    direction = "up";
    persistent = upShare >= 0.68;
  } else if (meaningful && monthChange < 0) {
    direction = "down";
    persistent = upShare <= 0.32;
  }

  const directionLabel =
    direction === "up"
      ? `${definition.upLabel}${persistent ? " 지속" : ""}`
      : direction === "down"
        ? `${definition.downLabel}${persistent ? " 지속" : ""}`
        : "방향 혼조";
  const directionalCount =
    direction === "up"
      ? upCount
      : direction === "down"
        ? downCount
        : Math.max(upCount, downCount);

  return {
    ...definition,
    metadata,
    rows,
    chartRows: rows.slice(-(weekly ? 14 : 65)),
    latest: rows[rows.length - 1],
    confirmedLatest: confirmedRows[confirmedRows.length - 1],
    liveSnapshot: livePoint ? marketIndexes.liveSnapshots[definition.id] : null,
    hasLive: Boolean(livePoint),
    oneDayChange: marketTrendChange(rows, 1, definition.type),
    oneWeekChange: marketTrendChange(rows, oneWeekOffset, definition.type),
    oneMonthChange: monthChange,
    direction,
    persistent,
    directionLabel,
    directionalCount,
    directionSamples: changes.length,
    upCount,
    downCount
  };
}

function renderMarketTrendRow(item, seriesIndex, timelineDomains) {
  const rangeLayers = chartRangeOptions
    .map((range) => {
      const domain = timelineDomains[range.id];
      const visible = pointsWithinDomain(item.rows, domain, "close");
      const coordinates = marketTrendCoordinates(visible, domain);
      const hasVisibleLive = Boolean(visible.at(-1)?.isLive);
      const confirmedCoordinates = hasVisibleLive ? coordinates.slice(0, -1) : coordinates;
      const liveCoordinates = hasVisibleLive ? coordinates.slice(-2) : [];
      const path = marketTrendPath(confirmedCoordinates);
      const livePath = marketTrendPath(liveCoordinates);
      const lastPoint = coordinates.at(-1);
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg data-chart-series-index="${seriesIndex}" viewBox="0 0 180 52" preserveAspectRatio="none" role="img" aria-label="${item.label} 선택 기간 흐름">
          <path class="market-trend-row__baseline" d="M 0 26 H 180"></path>
          <path class="market-trend-row__line" d="${path}"></path>
          ${hasVisibleLive ? `<path class="market-trend-row__live-line" d="${livePath}"></path>` : ""}
          ${lastPoint ? `<circle class="${hasVisibleLive ? "market-trend-row__live-point" : ""}" cx="${lastPoint.x.toFixed(2)}" cy="${lastPoint.y.toFixed(2)}" r="3"></circle>` : ""}
          ${renderChartCursorLine(3, 49)}
        </svg>
      `;
    })
    .join("");
  const rangeChangeLayers = chartRangeOptions
    .map((range) => {
      const domain = timelineDomains[range.id];
      const metric = marketTrendRangeMetric(
        item.rows,
        domain,
        item.type,
        item.metadata.frequency
      );
      const label = metric.isComplete ? range.label : `가용 ${metric.coverageLabel}`;
      const coverageTitle = metric.isComplete
        ? ""
        : ` title="${range.label} 요청 · ${metric.firstDate ?? "첫 관측 없음"}부터"`;
      return `
        <div class="market-trend-row__range-change ${metric.isComplete ? "" : "is-partial"} ${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}"${coverageTitle}>
          <dt>${label} 변동</dt>
          <dd>${formatMarketTrendChange(metric.change, item.type)}</dd>
        </div>
      `;
    })
    .join("");
  const persistenceText =
    item.direction === "flat"
      ? `상승 ${item.upCount} · 하락 ${item.downCount}`
      : `최근 ${item.directionSamples}회 중 ${item.directionalCount}회 ${item.direction === "up" ? "상승" : "하락"}`;

  return `
    <article class="market-trend-row market-trend-row--${item.direction}">
      <div class="market-trend-row__identity">
        <strong>${item.label}</strong>
        ${
          item.hasLive
            ? `<span class="market-trend-row__asof market-trend-row__asof--live">${item.liveSnapshot.displayStatus || "최신"} ${formatMarketSnapshotTime(item.liveSnapshot.observedAt)} KST · 잠정</span>`
            : `<span class="market-trend-row__asof">${item.latest.date}</span>`
        }
      </div>
      <div class="market-trend-row__chart">
        ${rangeLayers}
      </div>
      <dl class="market-trend-row__numbers">
        <div class="market-trend-row__current">
          <dt>현재값</dt>
          <dd>${formatMarketTrendValue(item.latest.close, item.type)}</dd>
        </div>
        <div><dt>${item.metadata.frequency === "weekly" ? "직전" : "전일"}</dt><dd>${formatMarketTrendChange(item.oneDayChange, item.type)}</dd></div>
        <div><dt>1주</dt><dd>${formatMarketTrendChange(item.oneWeekChange, item.type)}</dd></div>
        ${rangeChangeLayers}
      </dl>
      <div class="market-trend-row__state">
        <strong>${item.directionLabel}</strong>
        <span>${persistenceText}</span>
      </div>
    </article>
  `;
}

function renderMarketIndexTrendPanel(marketIndexes) {
  if (!marketIndexes?.series || !marketIndexes?.metadata) return "";
  const groups = marketTrendGroups
    .map((group) => ({
      ...group,
      items: group.items.map((item) => analyzeMarketTrend(item, marketIndexes)).filter(Boolean)
    }))
    .filter((group) => group.items.length);
  const items = groups.flatMap((group) => group.items);
  if (!items.length) return "";

  const persistent = items.filter((item) => item.persistent);
  const liveItems = items.filter((item) => item.hasLive);
  const latestDate = items.map((item) => item.latest.date).sort().at(-1);
  const timelineSeries = items.map((item) => item.rows);
  const timelineDomains = Object.fromEntries(
    chartRangeOptions.map((range) => [range.id, chartRangeDomain(timelineSeries, range.id)])
  );
  const chartId = registerInteractiveChart({
    tooltipMode: "hovered",
    width: 180,
    series: items.map((item) => ({
      label: item.label,
      points: item.rows,
      valueKey: "close",
      color: item.direction === "up" ? "var(--red)" : item.direction === "down" ? "var(--green)" : "var(--blue)",
      format: (value) => formatMarketTrendValue(value, item.type),
      detail: (point) =>
        formatMarketTrendCurrentComparison(point.close, item.latest.close, item.type),
      status: (point) => (point.isLive ? "잠정" : "EOD")
    }))
  });
  const seriesIndexById = new Map(items.map((item, index) => [item.id, index]));
  const narrativeItems = persistent
    .sort((left, right) => Math.abs(right.oneMonthChange) - Math.abs(left.oneMonthChange))
    .slice(0, 4)
    .map((item) => `${item.label} ${item.directionLabel}`);
  const narrative = narrativeItems.length
    ? `${narrativeItems.join(" · ")} 흐름 지속`
    : "뚜렷한 단일 방향 없이 자산별 혼조";

  return `
    <section class="market-trend-panel" data-timeseries-chart="${chartId}">
      <header class="market-trend-panel__header">
        <div>
          <span class="eyebrow">Naver Market Direction</span>
          <h2>금리·환율·원자재·운임 방향성</h2>
          ${renderNarrativeList(narrative, "narrative-list--compact")}
        </div>
        <div class="market-trend-panel__summary">
          <strong>${persistent.length}</strong>
          <span>지속 방향</span>
          <small>${liveItems.length ? `장중 최신값 ${liveItems.length}개` : `최신 ${latestDate}`}</small>
        </div>
      </header>
      <div class="market-trend-panel__toolbar">
        ${renderChartRangeControls(chartId, { hasProvisional: liveItems.length > 0 })}
      </div>
      <div class="market-trend-groups">
        ${groups
          .map(
            (group) => `
              <section class="market-trend-group market-trend-group--${group.id}">
                <header><h3>${group.label}</h3><span>${group.items.length}개</span></header>
                <div>${group.items
                  .map((item) =>
                    renderMarketTrendRow(item, seriesIndexById.get(item.id), timelineDomains)
                  )
                  .join("")}</div>
              </section>
            `
          )
          .join("")}
      </div>
      ${renderChartTooltip()}
      <footer class="market-trend-panel__footer">
        <span>Naver Pay 증권 시장지표</span>
        <span>현재값은 실시간·지연 잠정치 · 과거 시계열과 ML은 확정 EOD</span>
        <span>일간 최근 10회 · 주간 최근 6회 방향 판독</span>
        <span>전일·1주는 고정 · 기간 변동은 선택 구간 첫 관측 대비 · 부족 시 가용기간 표기</span>
      </footer>
    </section>
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
        <strong>백테스트 읽는 법</strong>
        ${renderNarrativeList([
          `대상: 최근 ${backtest.sampleCount}개 거래일 점수를 정상·관심·주의·경고로 구분`,
          "관찰: 각 날짜 이후 20거래일 KOSPI 최대낙폭",
          "Hit-rate: 최대낙폭 -5% 이하 발생 비율",
          "평균·최악 낙폭: 실제 후행 하락 강도",
          "성격: 예측 확률이 아닌 과거 조건부 진단",
          "소표본 구간: 방향성 중심 · 스트레스 사례와 병행"
        ], "narrative-list--compact")}
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
        ${renderNarrativeList([
          "카드: 2020년 이후 스트레스 신호 집중 기간",
          "피크 점수: 구간 내 최고 모델 점수 · 75점 이상 경고권",
          "피크 날짜: 최고 점수 관측일",
          "거래일: 실제 시장 개장일 기준 구간 길이",
          "고점대비 최대낙폭: 직전 252거래일 고점 대비 최대 하락",
          "구간 저점: 구간 시작일 대비 최저 KOSPI 수준",
          "20D 선행 최대낙폭: 피크 이후 20거래일 추가 하락",
          "0.00%: 피크 당일보다 낮은 후행 저점 없음"
        ], "narrative-list--compact")}
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

function sourceCatalog(snapshot) {
  if (!snapshot) return [];
  const catalogs = [
    ["Yahoo", snapshot.yahooSymbols],
    ["Naver Finance", snapshot.naverSymbols],
    ["FRED", snapshot.fredSeries],
    ["Naver Market Index", snapshot.naverMarketIndexes]
  ];
  return catalogs.flatMap(([provider, records]) =>
    Object.entries(records ?? {}).map(([id, record]) => ({ id, provider, ...record }))
  );
}

function indicatorSourceRecords(indicator, snapshot) {
  const source = String(indicator.source ?? "").toLowerCase();
  const matches = sourceCatalog(snapshot).filter((record) => {
    const tokens = [record.symbol, record.seriesId, record.label, record.id]
      .filter(Boolean)
      .map((token) => String(token).toLowerCase());
    return tokens.some((token) => token.length >= 3 && source.includes(token));
  });
  if (indicator.id === "m7_credit_stress_proxy" && snapshot?.m7CreditProxy?.latest) {
    matches.push({
      id: "m7-credit-proxy",
      provider: "M7 공개시장 프록시",
      label: "M7 Credit Stress Proxy",
      lastDate: snapshot.m7CreditProxy.latest.date,
      coveragePct: snapshot.m7CreditProxy.latest.coveragePct,
      fetchStatus: snapshot.m7CreditProxy.latest.qualityStatus
    });
  }
  return matches.filter(
    (record, index, all) =>
      all.findIndex((candidate) => `${candidate.provider}:${candidate.symbol ?? candidate.seriesId ?? candidate.id}` === `${record.provider}:${record.symbol ?? record.seriesId ?? record.id}`) === index
  );
}

function sourceValueState(record) {
  const status = String(record.fetchStatus ?? "").toLowerCase();
  if (status.includes("live")) return "잠정 · 실시간";
  if (status.includes("fallback") || status.includes("cached") || status.includes("대체")) return "대체값";
  if (status.includes("naver") && status.includes("yahoo")) return "EOD · 이중 확인";
  return "EOD";
}

function sourceEnhancementNote(indicator) {
  if (["kospi_price_stress", "kosdaq_growth_stress"].includes(indicator.id)) {
    return "Yahoo·Naver 종가 이중 확인";
  }
  if (["rates_pressure", "us_financial_conditions_stress", "korea_us_rate_fx_watch"].includes(indicator.id)) {
    return "라이브 시세와 공공 EOD 시계열 병행";
  }
  if (indicator.id === "m7_credit_stress_proxy") {
    return "OFR FSI·미 국채금리·회사채 ETF 보강";
  }
  return "소스 변경 이력 없음";
}

const indicatorDirectionMeanings = {
  kospi_price_stress: {
    up: "가격 하락·실현변동성·고점 대비 낙폭 부담 확대",
    down: "가격 회복·변동성 진정·고점 대비 낙폭 축소"
  },
  kosdaq_growth_stress: {
    up: "성장주 가격 하락과 변동성 부담 확대",
    down: "성장주 가격 회복과 변동성 부담 완화"
  },
  usdkrw_fx_pressure: {
    up: "원화 약세와 환율 변동 부담 확대",
    down: "원화 안정과 환율 부담 완화"
  },
  global_volatility_pressure: {
    up: "VIX 상승·공포 심리 확대·주식 위험회피 강화",
    down: "VIX 하락·헤지 수요 감소·위험선호 회복"
  },
  rates_pressure: {
    up: "미국 장기금리 상승과 주식 할인율 부담 확대",
    down: "장기금리 안정과 밸류에이션 부담 완화"
  },
  us_credit_spread_stress: {
    up: "하이일드 스프레드 확대와 신용·조달 위험 상승",
    down: "스프레드 축소와 신용시장 위험 완화"
  },
  us_financial_conditions_stress: {
    up: "미국 금융여건 긴축과 자금조달 부담 확대",
    down: "금융여건 완화와 유동성 부담 감소"
  },
  shipping_cost_pressure: {
    up: "수요 대비 운임 비용 부담과 공급망 압력 확대",
    down: "운임 비용·수요 괴리 축소와 마진 부담 완화"
  },
  china_demand_fx_stress: {
    up: "위안화 약세와 중국 수요 민감 원자재 부진 심화",
    down: "위안화·원자재 신호 개선과 중국 수요 우려 완화"
  },
  energy_import_cost_pressure: {
    up: "원화 환산 에너지 비용과 물가·기업 마진 부담 확대",
    down: "원화 환산 에너지 비용과 물가 압력 완화"
  },
  yen_carry_unwind_watch: {
    up: "엔화 강세·VIX 상승·SPX 하락이 겹친 캐리 청산 위험 확대",
    down: "엔 캐리 청산 신호와 글로벌 수급 충격 완화"
  },
  korea_us_rate_fx_watch: {
    up: "한미 금리차 축소와 원화 약세가 겹친 자금이탈 압력 확대",
    down: "금리차·환율 조합이 안정되며 자금이탈 우려 완화"
  },
  japan_us_rate_spread_watch: {
    up: "미·일 금리차 축소와 엔화 강세·자금 환류 가능성 확대",
    down: "금리차 축소 압력이 줄며 캐리 수급 부담 완화"
  },
  volatility_term_structure_watch: {
    up: "단기 변동성과 VVIX 상승으로 옵션 헤지·감마 부담 확대",
    down: "변동성 기간구조 정상화와 옵션 헤지 부담 완화"
  },
  us_market_breadth_watch: {
    up: "동일가중 상대약세로 미국 증시 상승 폭이 좁아짐",
    down: "상승 종목 참여가 넓어지며 시장 내부 강도 개선"
  },
  broad_reinflation_watch: {
    up: "원자재·농산물 가격 상승 확산과 재인플레이션 우려 확대",
    down: "광의 물가 압력과 추가 금리 부담 완화"
  },
  global_ai_semiconductor_stress: {
    up: "글로벌 AI 반도체 가격 약세·변동성·낙폭 부담 확대",
    down: "AI 반도체 가격과 변동성 스트레스 완화"
  },
  bigtech_ai_demand_pressure: {
    up: "빅테크 주가 스트레스와 AI 투자비용·ROI 우려 확대",
    down: "빅테크 수요 기대와 AI 투자비용 부담 개선"
  },
  korea_ai_semiconductor_concentration: {
    up: "국내 반도체 가격 스트레스와 KOSPI 대비 쏠림 위험 확대",
    down: "반도체 가격 스트레스와 지수 쏠림 부담 완화"
  },
  foreign_ownership_pressure: {
    up: "주요 반도체 종목의 외국인 보유비중 이탈 확대",
    down: "외국인 보유비중 이탈 진정 또는 수급 회복"
  },
  trading_activity_heat: {
    up: "거래량이 60일 평균에서 크게 벗어난 과열·위축 이상 신호 확대",
    down: "거래량이 평시 범위로 돌아오며 유동성 이상 신호 완화"
  },
  single_name_semiconductor_leverage: {
    up: "삼성전자·SK하이닉스 낙폭과 KOSPI 대비 변동성 배율 확대",
    down: "개별종목 변동성·낙폭 부담이 KOSPI 대비 완화"
  },
  global_credit_proxy_stress: {
    up: "HYG가 LQD 대비 약해지며 글로벌 신용 위험 확대",
    down: "하이일드 상대가격 회복과 신용 위험 완화"
  },
  emerging_market_stress: {
    up: "신흥국 주가 약세·변동성·낙폭 부담 확대",
    down: "신흥국 위험선호와 가격 흐름 회복"
  },
  m7_credit_stress_proxy: {
    up: "M7 고유 주가손실과 공개 신용시장 스트레스 동반 확대",
    down: "M7 고유손실과 공개 신용시장 부담 완화"
  }
};

function indicatorDirectionMeaning(indicator) {
  return indicatorDirectionMeanings[indicator.id] ?? {
    up: `${indicator.name} 위험 신호 강화`,
    down: `${indicator.name} 위험 신호 완화`
  };
}

function renderIndicatorDirectionMeaning(indicator) {
  const meaning = indicatorDirectionMeaning(indicator);
  return `
    <section class="indicator-direction" aria-label="${indicator.name} 점수 방향 해석">
      <div class="indicator-direction__heading">
        <strong>점수 방향</strong>
        <small>0~100 위험점수 기준</small>
      </div>
      <dl class="indicator-direction__list">
        <div class="indicator-direction__item indicator-direction__item--up">
          <dt>상승 시</dt>
          <dd>${meaning.up}</dd>
        </div>
        <div class="indicator-direction__item indicator-direction__item--down">
          <dt>하락 시</dt>
          <dd>${meaning.down}</dd>
        </div>
      </dl>
    </section>
  `;
}

function indicatorQualityNotes(indicator, quality) {
  const source = String(indicator.source ?? "").toLowerCase();
  return (quality?.issues ?? [])
    .filter((issue) => {
      if (indicator.id === "m7_credit_stress_proxy" && issue.groupId === "m7-credit") return true;
      const label = String(issue.label ?? "").toLowerCase();
      return label.length >= 3 && source.includes(label.replace(/^price:/, ""));
    })
    .slice(0, 3);
}

function renderIndicatorSourceDetail(indicator, provenance) {
  const records = indicatorSourceRecords(indicator, provenance?.snapshot);
  const qualityNotes = indicatorQualityNotes(indicator, provenance?.quality);
  const normalization = provenance?.model?.normalization;
  const normalizationText = normalization
    ? `최대 2년 · 분위수 ${Number(normalization.percentileWeight) * 100}% · z ${Number(normalization.zScoreWeight) * 100}% · robust z ${Number(normalization.robustZScoreWeight) * 100}%`
    : "최대 2년 · 가용 관측치 기준";
  const detailId = `source-detail-${indicator.id}`;
  return `
    <button
      type="button"
      class="source-detail-toggle"
      data-source-detail-toggle
      aria-expanded="false"
      aria-controls="${detailId}"
    >원천·산식</button>
    <div class="source-detail" id="${detailId}" hidden>
      <dl class="source-detail__summary">
        <div><dt>조회시각</dt><dd>${provenance?.snapshot?.generatedAt ?? "확인 대기"}</dd></div>
        <div><dt>정규화</dt><dd>${normalizationText}</dd></div>
        <div><dt>현재 보강</dt><dd>${sourceEnhancementNote(indicator)}</dd></div>
      </dl>
      <div class="source-detail__records">
        <strong>원천 관측 상태</strong>
        ${
          records.length
            ? records
                .map(
                  (record) => `
                    <div>
                      <strong>${record.provider} · ${record.symbol ?? record.seriesId ?? record.label ?? record.id}</strong>
                      <span>${record.lastDate ?? "관측일 확인 대기"} · ${sourceValueState(record)} · ${Number.isFinite(Number(record.observations)) ? `${Number(record.observations).toLocaleString()}개 관측` : Number.isFinite(Number(record.coveragePct)) ? `커버리지 ${Number(record.coveragePct).toFixed(0)}%` : "관측수 확인 대기"}</span>
                    </div>
                  `
                )
                .join("")
            : `<div><strong>${indicator.source}</strong><span>카드 산출 결과에 기록된 원천</span></div>`
        }
      </div>
      ${
        qualityNotes.length
          ? `<div class="source-detail__components"><strong>완비성 확인</strong>${renderNarrativeList(qualityNotes.map((issue) => issue.detail), "narrative-list--compact")}</div>`
          : ""
      }
    </div>
  `;
}

function renderIndicator(indicator, thresholds, timeseries, provenance = null) {
  const level = thresholds.find((threshold) => indicator.value >= threshold.min && indicator.value < threshold.max);
  const tone = level?.tone ?? "muted";
  const indicatorPoints = timeseries?.series?.[indicator.id] ?? [];
  const isObservation = indicator.role === "observation";
  const group = riskGroupDefinitions[indicator.group] ?? {
    label: indicator.group ?? "리스크",
    shortLabel: indicator.group ?? "Risk",
    englishLabel: indicator.group ?? "Risk",
    description: "소속 리스크 그룹"
  };

  return `
    <article class="indicator-card indicator-card--group-${indicator.group ?? "risk"} ${isObservation ? "indicator-card--observation" : ""}">
      <div>
        <span class="eyebrow">${indicator.category}</span>
        <h3>${indicator.name}</h3>
      </div>
      <div class="indicator-card__score">
        <strong>${formatScore(indicator.value)}</strong>
        <span class="status-pill status-pill--${isObservation ? "watch" : tone}">${isObservation ? `관찰 · ${level?.label ?? "N/A"}` : level?.label ?? "N/A"}</span>
      </div>
      <div class="contribution-line">
        <span
          class="indicator-group-tag indicator-group-tag--${indicator.group ?? "risk"}"
          title="${group.englishLabel} · ${group.description}"
        >
          <i aria-hidden="true"></i>
          ${group.label} · ${group.shortLabel}
        </span>
        <strong>${isObservation ? "종합점수 미반영" : `기여도 +${Number(indicator.contribution ?? 0).toFixed(2)}점`}</strong>
      </div>
      ${renderChangePills(indicator.value, indicatorPoints)}
      <div class="mini-bar" aria-hidden="true">
        <span style="width:${clampScore(indicator.value)}%"></span>
      </div>
      ${renderSparkline(indicator, timeseries)}
      ${renderNarrativeList(indicator.detail, "narrative-list--compact indicator-detail-list")}
      ${renderIndicatorDirectionMeaning(indicator)}
      <footer>
        <span>${indicator.source}</span>
        <span>추세 ${trendLabel[indicator.trend] ?? "-"}</span>
      </footer>
      ${renderIndicatorSourceDetail(indicator, provenance)}
    </article>
  `;
}

function renderIndicatorSortControls(sectionId, indicatorCount) {
  return `
    <div class="indicator-toolbar">
      <div class="indicator-toolbar__heading">
        <span class="eyebrow">Indicator Sort</span>
        <h3>시장리스크 카드 정렬</h3>
        <span data-indicator-filter-status="${sectionId}">전체 ${indicatorCount}개</span>
      </div>
      <div class="indicator-toolbar__controls">
        <button
          type="button"
          class="indicator-filter-reset"
          data-indicator-filter-reset="${sectionId}"
          hidden
        >전체 그룹</button>
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
  const sentimentChartId =
    points.length >= 2
      ? registerInteractiveChart({
          series: [
            {
              label: "센티멘트",
              points,
              valueKey: "value",
              color: "var(--teal)",
              format: (value) => `${Number(value).toFixed(1)}점`
            }
          ]
        })
      : null;
  const sentimentRangeLayers = sentimentChartId
    ? chartRangeOptions
        .map((range) => {
          const domain = chartRangeDomain([points], range.id);
          const visible = pointsWithinDomain(points, domain, "value");
          const values = visible.map((point) => clampScore(point.value));
          const valueDomain = values.length
            ? {
                min: Math.max(0, Math.min(...values) - 5),
                max: Math.min(100, Math.max(...values) + 5)
              }
            : { min: 0, max: 100 };
          const path = datedValuePath(visible, "value", domain, valueDomain);
          const firstX = visible.length ? xFromDate(visible[0].date, domain, 760) : 0;
          const lastX = visible.length ? xFromDate(visible.at(-1).date, domain, 760) : 760;
          const areaPath = path
            ? `${path} L ${lastX.toFixed(2)} 190 L ${firstX.toFixed(2)} 190 Z`
            : "";
          const monthAxis = renderMonthAxisFromDomain(domain);
          return `
            <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img">
              ${monthAxis.grid}
              <path class="trend-chart__grid" d="M 0 42 L 760 42 M 0 84 L 760 84 M 0 126 L 760 126 M 0 168 L 760 168"></path>
              <path class="trend-chart__area" d="${areaPath}"></path>
              <path class="trend-chart__line" d="${path}"></path>
              ${renderChartCursorLine(18, 190)}
              ${monthAxis.labels}
            </svg>
          `;
        })
        .join("")
    : "";
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
          ${renderNarrativeList(level.reading, "narrative-list--compact")}
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
              <div class="trend-chart" data-timeseries-chart="${sentimentChartId}" aria-label="시장 센티멘트 시계열">
                ${renderChartRangeControls(sentimentChartId)}
                ${sentimentRangeLayers}
                ${renderChartTooltip()}
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
          <p>높은 점수 = 낮은 시장 부담</p>
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

function summaryCrashTone(probability, metrics) {
  const baseRatePct = Number(metrics?.eventRate) * 100;
  const relativeRisk = baseRatePct > 0 ? Number(probability) / baseRatePct : 0;
  if (relativeRisk >= 3) return "danger";
  if (relativeRisk >= 1.5) return "caution";
  return "watch";
}

function renderDecisionCockpit(data, timeseries, mlRisk, hmmRegime) {
  const market = data.sections.find((section) => section.id === "market");
  if (!market) return "";

  const composite = buildCompositeSeries(market, timeseries);
  const latestComposite = composite[composite.length - 1];
  const change1d = latestComposite ? valueChange(latestComposite.value, composite, 1) : null;
  const change1w = latestComposite ? valueChange(latestComposite.value, composite, 5) : null;
  const topContributor = [...(market.indicators ?? [])]
    .filter(isScoredIndicator)
    .sort((left, right) => Number(right.contribution ?? 0) - Number(left.contribution ?? 0))[0];
  const crashProbability = Number(mlRisk?.latest?.crash5d5pctProbabilityPct);
  const crashMetrics = mlRisk?.metrics?.crash5d5pct ?? {};
  const hmmBasket = hmmRegime?.basket;

  return `
    <section class="decision-cockpit" aria-labelledby="decision-cockpit-title">
      <header class="decision-cockpit__header">
        <div>
          <span class="eyebrow">Decision Cockpit</span>
          <h2 id="decision-cockpit-title">오늘 먼저 볼 네 가지</h2>
        </div>
        <span>${data.metadata.asOf} 확정 EOD 기준</span>
      </header>
      <div class="decision-grid">
        ${createMetricCard({
          label: "시장 스트레스",
          value: `${market.level.label} · ${Number(market.score).toFixed(1)}`,
          meta: `1D ${formatPointDelta(change1d)} · 1W ${formatPointDelta(change1w)}`,
          tone: market.level.tone
        })}
        ${createMetricCard({
          label: "최대 점수 기여",
          value: topContributor ? `+${Number(topContributor.contribution ?? 0).toFixed(2)}점` : "-",
          meta: topContributor
            ? `${topContributor.name} · 현재 ${clampScore(topContributor.value).toFixed(1)}`
            : "가중 지표 확인 필요",
          tone: topContributor && clampScore(topContributor.value) >= 75 ? "danger" : "caution"
        })}
        ${createMetricCard({
          label: "5D -5% 급락확률",
          value: Number.isFinite(crashProbability) ? `${crashProbability.toFixed(1)}%` : "-",
          meta: Number.isFinite(crashProbability)
            ? `향후 5거래일 도달 · OOS AP ${Number(crashMetrics.averagePrecision ?? 0).toFixed(3)}`
            : "ML 산출물 확인 필요",
          tone: Number.isFinite(crashProbability) ? summaryCrashTone(crashProbability, crashMetrics) : "neutral"
        })}
        ${createMetricCard({
          label: "Cross-market HMM",
          value: hmmBasket?.regime ?? "-",
          meta: hmmBasket
            ? `위험회피 ${hmmBasket.riskOffCount} · 활황 ${hmmBasket.highVolBullCount} · 안정 ${hmmBasket.stableCount}`
            : "HMM 산출물 확인 필요",
          tone: hmmBasket?.tone ?? "neutral"
        })}
      </div>
    </section>
  `;
}

function renderAttributionList(items, direction, maxMagnitude) {
  const selected = items
    .filter((item) => (direction === "up" ? item.weightedChange > 0.005 : item.weightedChange < -0.005))
    .sort((left, right) =>
      direction === "up"
        ? right.weightedChange - left.weightedChange
        : left.weightedChange - right.weightedChange
    )
    .slice(0, 5);

  if (!selected.length) {
    return `<p class="attribution-empty">유의한 ${direction === "up" ? "상승" : "완화"} 요인 없음</p>`;
  }

  return `
    <ol class="attribution-list attribution-list--${direction}">
      ${selected
        .map((item) => {
          const definition = riskGroupDefinitions[item.group] ?? { label: item.group ?? "기타" };
          const width = maxMagnitude > 0 ? (Math.abs(item.weightedChange) / maxMagnitude) * 100 : 0;
          return `
            <li>
              <div class="attribution-list__identity">
                <span>${item.name}</span>
                <small>${definition.label} · 원점수 ${formatPointDelta(item.scoreChange)}</small>
              </div>
              <div class="attribution-list__bar" aria-hidden="true"><span style="width:${width.toFixed(1)}%"></span></div>
              <strong>${formatAttributionDelta(item.weightedChange)}</strong>
            </li>
          `;
        })
        .join("")}
    </ol>
  `;
}

function renderAttributionPeriod(market, timeseries, offset, id, active = false) {
  const items = buildScoreAttribution(market, timeseries, offset);
  const totalChange = items.reduce((sum, item) => sum + item.weightedChange, 0);
  const maxMagnitude = Math.max(...items.map((item) => Math.abs(item.weightedChange)), 0);
  const periodLabel = id === "1w" ? "1W 종합점수 변화" : "1D 종합점수 변화";
  const comparisonLabel = id === "1w" ? "5거래일 전 대비" : "전 거래일 대비";

  return `
    <div
      class="attribution-period ${active ? "is-active" : ""}"
      data-attribution-panel="${id}"
      aria-hidden="${active ? "false" : "true"}"
      ${active ? "" : "hidden"}
    >
      <div class="attribution-period__summary">
        <span>${periodLabel}</span>
        <strong class="change-pill change-pill--${changeTone(totalChange)}">${formatAttributionDelta(totalChange)}</strong>
        <small>${comparisonLabel} · 개별 점수 변화 × 종합모델 가중치</small>
      </div>
      <div class="attribution-columns">
        <section>
          <header><span class="attribution-dot attribution-dot--up"></span><h3>부담 상승</h3></header>
          ${renderAttributionList(items, "up", maxMagnitude)}
        </section>
        <section>
          <header><span class="attribution-dot attribution-dot--down"></span><h3>부담 완화</h3></header>
          ${renderAttributionList(items, "down", maxMagnitude)}
        </section>
      </div>
    </div>
  `;
}

function renderScoreAttribution(market, timeseries) {
  return `
    <section class="attribution-panel">
      <header class="attribution-panel__header">
        <div>
          <span class="eyebrow">Score Attribution</span>
          <h2>점수가 왜 움직였나</h2>
          <p>종합점수 변화에 실제로 기여한 지표를 분해</p>
        </div>
        <div class="attribution-toggle" role="group" aria-label="점수 변화 기간">
          <button type="button" class="is-active" data-attribution-window="1d" aria-pressed="true">1D</button>
          <button type="button" data-attribution-window="1w" aria-pressed="false">1W</button>
        </div>
      </header>
      ${renderAttributionPeriod(market, timeseries, 1, "1d", true)}
      ${renderAttributionPeriod(market, timeseries, 5, "1w")}
      <footer>
        <span>상승 = 시장 부담 확대</span>
        <button type="button" data-open-tab="market">전체 지표에서 확인</button>
      </footer>
    </section>
  `;
}

function groupScore(market, groupId) {
  const score = Number(
    (market?.groupScores ?? []).find((group) => group.id === groupId)?.score
  );
  return Number.isFinite(score) ? clampScore(score) : null;
}

function weightedAvailableScore(parts) {
  const available = parts.filter(
    ({ value, weight }) =>
      value !== null &&
      value !== undefined &&
      Number.isFinite(Number(value)) &&
      Number(weight) > 0
  );
  const totalWeight = available.reduce((sum, part) => sum + Number(part.weight), 0);
  if (!totalWeight) return null;
  return clampScore(
    available.reduce(
      (sum, part) => sum + clampScore(part.value) * Number(part.weight),
      0
    ) / totalWeight
  );
}

function breadthCollapseScore(latest) {
  if (!latest) return null;
  return weightedAvailableScore([
    { value: 50 - Number(latest.breadthPct), weight: 0.35 },
    { value: 50 - Number(latest.breadthMa5Pct), weight: 0.45 },
    { value: 50 - Number(latest.breadthMa20Pct), weight: 0.2 }
  ]);
}

function diagnosticLevel(score) {
  if (score === null || score === undefined || !Number.isFinite(Number(score))) {
    return { label: "확인 필요", tone: "muted" };
  }
  if (score >= 75) return { label: "매우 높음", tone: "danger" };
  if (score >= 55) return { label: "높음", tone: "caution" };
  if (score >= 35) return { label: "관찰", tone: "watch" };
  return { label: "낮음", tone: "good" };
}

function latestBreadthChange(breadthData, offset = 5) {
  const points = breadthData?.series ?? [];
  if (points.length <= offset) return null;
  const latest = Number(points.at(-1)?.breadthMa5Pct);
  const previous = Number(points.at(-1 - offset)?.breadthMa5Pct);
  return Number.isFinite(latest) && Number.isFinite(previous) ? latest - previous : null;
}

function stressAcceleration(market, timeseries, breadthData) {
  const composite = buildCompositeSeries(market, timeseries);
  const crashSeries = buildGroupCompositeSeries(market, "crash", timeseries);
  const latestComposite = composite.at(-1);
  const latestCrash = crashSeries.at(-1);
  const compositeChange5d = latestComposite
    ? valueChange(latestComposite.value, composite, 5)
    : null;
  const crashChange5d = latestCrash ? valueChange(latestCrash.value, crashSeries, 5) : null;
  const breadthChange5d = latestBreadthChange(breadthData, 5);
  const sharplyWorse =
    Number(compositeChange5d) >= 4 ||
    Number(crashChange5d) >= 6 ||
    Number(breadthChange5d) <= -20;
  const worse =
    Number(compositeChange5d) >= 1.5 ||
    Number(crashChange5d) >= 3 ||
    Number(breadthChange5d) <= -10;
  const improving =
    Number(compositeChange5d) <= -1.5 &&
    (!Number.isFinite(Number(breadthChange5d)) || Number(breadthChange5d) >= 5);

  return {
    label: sharplyWorse ? "급격 악화" : worse ? "악화" : improving ? "완화" : "보합",
    tone: sharplyWorse ? "danger" : worse ? "caution" : improving ? "good" : "watch",
    compositeChange5d,
    crashChange5d,
    breadthChange5d
  };
}

function classifyMarketShock({
  priceShock,
  flowLiquidityShock,
  breadthStress,
  macroConfirmation,
  acceleration
}) {
  const priceHigh = Number(priceShock) >= 75;
  const flowHigh = Number(flowLiquidityShock) >= 65 || Number(breadthStress) >= 75;
  const macroHigh = Number(macroConfirmation) >= 65;

  if (priceHigh && flowHigh && macroHigh) {
    return {
      label: "시스템 스트레스 주의",
      tone: "danger",
      note: "가격·수급·거시 부담 동반"
    };
  }
  if (priceHigh && flowHigh) {
    return {
      label: "수급성 오버슈팅 가능성",
      tone: "caution",
      note: "시장 내부 매도 확산 · 충격 지속성 확인"
    };
  }
  if (priceHigh && acceleration?.tone === "danger") {
    return {
      label: "가격 충격 재가속",
      tone: "danger",
      note: "높은 가격 부담 위에 악화 속도 상승"
    };
  }
  if (priceHigh && acceleration?.tone === "caution") {
    return {
      label: "가격 충격 잔존",
      tone: "caution",
      note: "5일 악화 속도 재상승 관찰"
    };
  }
  if (priceHigh && acceleration?.tone === "good") {
    return {
      label: "가격 충격 잔존",
      tone: "watch",
      note: "시장 내부 정상화 여부 관찰"
    };
  }
  if (priceHigh) {
    return {
      label: "가격 충격 잔존",
      tone: "watch",
      note: "추가 확산·거시 확인 신호 관찰"
    };
  }
  if (macroHigh && Number(priceShock) >= 55) {
    return {
      label: "거시 부담 주도",
      tone: "caution",
      note: "금리·환율·신용 확인 신호 우세"
    };
  }
  if (flowHigh) {
    return {
      label: "수급·유동성 경계",
      tone: "caution",
      note: "가격 충격 전이 가능성 관찰"
    };
  }
  return {
    label: Number(priceShock) >= 55 ? "복합 부담 관찰" : "충격 제한적",
    tone: Number(priceShock) >= 55 ? "watch" : "good",
    note: "단일 원인 확정 신호 없음"
  };
}

function shockMetric({ label, value, level, meta, note }) {
  const hasValue = value !== null && value !== undefined && Number.isFinite(Number(value));
  if (!hasValue) return "";

  const tone = level?.tone ?? "muted";
  return `
    <article class="shock-metric shock-metric--${tone}">
      <span>${label}</span>
      <strong>${Number(value).toFixed(1)}</strong>
      <small>${level?.label ?? ""}</small>
      <p>${meta}</p>
      ${note ? `<em>${note}</em>` : ""}
    </article>
  `;
}

function renderShockDecomposition(market, timeseries, breadthData) {
  const priceShock = groupScore(market, "crash");
  const macroConfirmation = groupScore(market, "macro");
  const fallbackFlowScore = groupScore(market, "flow");
  const directFlowValue = breadthData?.latest?.directFlowPressure;
  const directFlowScore = directFlowValue == null ? Number.NaN : Number(directFlowValue);
  const flowScore = Number.isFinite(directFlowScore) ? directFlowScore : fallbackFlowScore;
  const liquidityScore = groupScore(market, "liquidity");
  const breadthStress = breadthCollapseScore(breadthData?.latest);
  const flowLiquidityShock = weightedAvailableScore([
    { value: breadthStress, weight: 0.45 },
    { value: flowScore, weight: 0.3 },
    { value: liquidityScore, weight: 0.25 }
  ]);
  const acceleration = stressAcceleration(market, timeseries, breadthData);
  const classification = classifyMarketShock({
    priceShock,
    flowLiquidityShock,
    breadthStress,
    macroConfirmation,
    acceleration
  });
  const breadthDate = breadthData?.latest?.date ?? "-";

  return `
    <section class="shock-decomposition" aria-labelledby="shock-decomposition-title">
      <header class="shock-decomposition__header">
        <div>
          <span class="eyebrow">Shock Decomposition · Research Overlay</span>
          <h2 id="shock-decomposition-title">시장 충격 분해</h2>
          <p>종합점수 ${Number(market.score).toFixed(1)} 유지 · 원인 해석을 위한 가중치 0 보조 진단</p>
        </div>
        <div class="shock-diagnosis shock-diagnosis--${classification.tone}">
          <span>잠정 충격 유형</span>
          <strong>${classification.label}</strong>
          <small>${classification.note}</small>
        </div>
      </header>

      <div class="shock-grid">
        ${shockMetric({
          label: "가격 충격",
          value: priceShock,
          level: diagnosticLevel(priceShock),
          meta: "기존 Crash Stress",
          note: "KOSPI·KOSDAQ 가격 부담"
        })}
        ${shockMetric({
          label: "수급·유동성 충격",
          value: flowLiquidityShock,
          level: diagnosticLevel(flowLiquidityShock),
          meta: Number.isFinite(directFlowScore)
            ? `KRX 직접 순매수 · ${breadthDate} EOD`
            : `기존 Flow proxy · ${breadthDate}`,
          note: "시장 확산 45% · 직접 수급 30% · 거래 25%"
        })}
        ${shockMetric({
          label: "거시·신용 확인",
          value: macroConfirmation,
          level: diagnosticLevel(macroConfirmation),
          meta: "기존 Macro 그룹",
          note: "금리·환율·변동성·신용·원자재"
        })}
      </div>

      <div class="shock-decomposition__footer">
        <div class="shock-acceleration shock-acceleration--${acceleration.tone}">
          <span>5D 스트레스 가속도</span>
          <strong>${acceleration.label}</strong>
          <small>종합 ${formatPointDelta(acceleration.compositeChange5d)} · Crash ${formatPointDelta(acceleration.crashChange5d)} · 확산 5D 평균 ${formatPctPointDelta(acceleration.breadthChange5d)}</small>
        </div>
        ${renderNarrativeList([
          "수급성 판정: 가격 충격과 시장 내부 매도 확산의 동시 확인",
          "운영 원칙: 기존 종합점수·가중치·경보단계 변경 없음"
        ], "narrative-list--compact shock-decomposition__notes")}
        <details class="shock-methodology">
          <summary>산식</summary>
          ${renderNarrativeList([
            "시장확산 부담: 일간 35% · 5일 평균 45% · 20일 평균 20%",
            "수급·유동성: 시장확산 45% · KRX 직접 수급 30% · 기존 Liquidity 25%",
            "직접 수급: 외국인 45% · 기관 35% · 프로그램 20%의 5일 매도압력 분위수",
            "악화 속도: 종합·Crash 5일 변화와 Breadth 5일 평균 변화를 규칙 기반 분류",
            Number.isFinite(directFlowScore)
              ? "각 날짜까지의 직전 최대 252거래일만 비교 · 미래값 미사용"
              : "직접 순매수 누락 · 기존 외국인 보유비중 Flow proxy로 대체"
          ], "narrative-list--compact")}
        </details>
      </div>
    </section>
  `;
}

function renderBreadthSummaryPanel(breadthData) {
  const latest = breadthData?.latest;
  if (!latest) return "";

  const state = latest.state ?? { label: "확인 필요", tone: "watch" };
  return `
    <section class="breadth-summary breadth-summary--${state.tone}">
      <div>
        <span class="eyebrow">Market Breadth · KRX EOD ${latest.date}</span>
        <h2>시장 내부강도 <strong>${state.label}</strong></h2>
        <p>상승 ${formatNumber(latest.up)} · 하락 ${formatNumber(latest.down)} · 보합 ${formatNumber(latest.flat)}</p>
      </div>
      <dl>
        <div><dt>일간 확산도</dt><dd>${formatSignedPct(latest.breadthPct)}</dd><small>-100~+100%</small></div>
        <div><dt>5일 평균</dt><dd>${formatSignedPct(latest.breadthMa5Pct)}</dd><small>20일 ${formatSignedPct(latest.breadthMa20Pct)}</small></div>
        <div><dt>AD-20일선</dt><dd>${formatSignedThousands(latest.adDistance20)}</dd><small>누적 순확산</small></div>
        <div><dt>직접 수급 압력</dt><dd>${latest.directFlowPressure == null ? "-" : `${formatNumber(latest.directFlowPressure, 1)} / 100`}</dd><small>외국인·기관·프로그램</small></div>
      </dl>
      <button type="button" data-open-tab="market-breadth">내부 흐름 보기</button>
    </section>
  `;
}

function renderBreadthPriceChart(breadthData) {
  const points = breadthData?.series ?? [];
  if (points.length < 2) return "";
  const chartWidth = 760;
  const plotLeft = 64;
  const plotRight = 696;
  const plotWidth = plotRight - plotLeft;
  const plotTop = 20;
  const plotBottom = 184;
  const chartId = registerInteractiveChart({
    width: chartWidth,
    plotLeft,
    plotRight,
    series: [
      {
        label: "KOSPI",
        points,
        valueKey: "kospiClose",
        color: "var(--blue)",
        format: (value) => formatNumber(value, 2)
      },
      {
        label: "일간 확산도",
        points,
        valueKey: "breadthPct",
        color: "var(--teal)",
        format: (value) => formatSignedPct(value)
      },
      {
        label: "확산도 5일 평균",
        points,
        valueKey: "breadthMa5Pct",
        color: "var(--green)",
        format: (value) => formatSignedPct(value)
      },
      {
        label: "확산도 20일 평균",
        points,
        valueKey: "breadthMa20Pct",
        color: "var(--amber)",
        format: (value) => formatSignedPct(value)
      }
    ]
  });
  const layers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([points], range.id);
      const visible = pointsWithinDomain(points, domain, "kospiClose");
      const kospiDomain = numericChartDomain(visible, "kospiClose", 0.1);
      const breadthDomain = { min: -100, max: 100 };
      const axis = renderMonthAxisFromDomain(domain, plotWidth, plotTop, plotBottom, 207);
      const kospiTicks = [
        { value: kospiDomain.max, y: plotTop },
        { value: (kospiDomain.max + kospiDomain.min) / 2, y: (plotTop + plotBottom) / 2 },
        { value: kospiDomain.min, y: plotBottom }
      ];
      const breadthTicks = [100, 50, 0, -50, -100].map((value) => ({
        value,
        y: plotBottom - ((value - breadthDomain.min) / (breadthDomain.max - breadthDomain.min)) * (plotBottom - plotTop)
      }));
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 ${chartWidth} 210" role="img" aria-label="왼쪽 KOSPI 축과 오른쪽 일간 확산도 축 비교">
          <g transform="translate(${plotLeft} 0)">
            ${axis.grid}
            <path class="breadth-chart__grid" d="M 0 ${plotTop} L ${plotWidth} ${plotTop} M 0 ${(plotTop + plotBottom) / 2} L ${plotWidth} ${(plotTop + plotBottom) / 2} M 0 ${plotBottom} L ${plotWidth} ${plotBottom}"></path>
            <path class="breadth-chart__zero-line" d="M 0 ${(plotTop + plotBottom) / 2} L ${plotWidth} ${(plotTop + plotBottom) / 2}"></path>
            <path class="breadth-chart__daily" d="${datedValuePath(points, "breadthPct", domain, breadthDomain, plotWidth, plotTop, plotBottom)}"></path>
            <path class="breadth-chart__ma5" d="${datedValuePath(points, "breadthMa5Pct", domain, breadthDomain, plotWidth, plotTop, plotBottom)}"></path>
            <path class="breadth-chart__ma" d="${datedValuePath(points, "breadthMa20Pct", domain, breadthDomain, plotWidth, plotTop, plotBottom)}"></path>
            <path class="breadth-chart__kospi-halo" d="${datedValuePath(points, "kospiClose", domain, kospiDomain, plotWidth, plotTop, plotBottom)}"></path>
            <path class="breadth-chart__kospi" d="${datedValuePath(points, "kospiClose", domain, kospiDomain, plotWidth, plotTop, plotBottom)}"></path>
            ${axis.labels}
          </g>
          <text class="breadth-chart__axis-title is-kospi" x="4" y="13">KOSPI</text>
          ${kospiTicks.map((tick) => `<text class="breadth-chart__axis-value is-kospi" x="4" y="${tick.y + 4}">${formatNumber(tick.value)}</text>`).join("")}
          <text class="breadth-chart__axis-title is-breadth" x="756" y="13" text-anchor="end">확산도</text>
          ${breadthTicks.map((tick) => `<text class="breadth-chart__axis-value is-breadth" x="756" y="${tick.y + 4}" text-anchor="end">${tick.value > 0 ? "+" : ""}${tick.value}%</text>`).join("")}
          ${renderChartCursorLine(plotTop, plotBottom)}
        </svg>
      `;
    })
    .join("");

  return `
    <article class="breadth-chart-card breadth-chart-card--dual-axis">
      <header>
        <div><span class="eyebrow">Price & Participation</span><h3>KOSPI와 일간 확산도</h3></div>
        <div class="breadth-chart-legend"><span><i class="is-kospi"></i>KOSPI · 왼쪽</span><span><i class="is-breadth"></i>일간 확산 · 오른쪽 · 옅은 선</span><span><i class="is-ma5"></i>5일 평균</span><span><i class="is-ma"></i>20일 평균</span></div>
      </header>
      <div class="breadth-chart" data-timeseries-chart="${chartId}">
        ${renderChartRangeControls(chartId)}
        ${layers}
        ${renderChartTooltip()}
      </div>
      <p class="breadth-chart-card__note">일간은 옅은 원자료 · 5일은 단기 방향 · 20일은 중기 추세 · 오른쪽 확산도 축은 항상 -100~+100%</p>
    </article>
  `;
}

function renderBreadthAdChart(breadthData) {
  const points = breadthData?.series ?? [];
  if (points.length < 2) return "";
  const chartPoints = points.map((point) => ({
    ...point,
    adLineThousands: point.adLine == null ? null : Number(point.adLine) / 1000,
    adMa20Thousands: point.adMa20 == null ? null : Number(point.adMa20) / 1000
  }));
  const chartId = registerInteractiveChart({
    series: [
      {
        label: "AD 누적선",
        points: chartPoints,
        valueKey: "adLineThousands",
        color: "var(--green)",
        format: (value) => `${value > 0 ? "+" : ""}${formatNumber(value, 1)}천 종목`
      },
      {
        label: "AD 20일선",
        points: chartPoints,
        valueKey: "adMa20Thousands",
        color: "var(--amber)",
        format: (value) => `${value > 0 ? "+" : ""}${formatNumber(value, 1)}천 종목`
      }
    ]
  });
  const layers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([chartPoints], range.id);
      const visible = pointsWithinDomain(chartPoints, domain, "adLineThousands");
      const combined = [
        ...visible.filter((point) => point.adLineThousands != null).map((point) => ({ value: point.adLineThousands })),
        ...visible.filter((point) => point.adMa20Thousands != null).map((point) => ({ value: point.adMa20Thousands }))
      ];
      const valueDomain = numericChartDomain(combined, "value", 0.12);
      const axis = renderMonthAxisFromDomain(domain, 760, 18, 190, 207);
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img" aria-label="천 종목 단위 Advance Decline 누적선과 20일 이동평균">
          ${axis.grid}
          <path class="breadth-chart__grid" d="M 0 61 L 760 61 M 0 104 L 760 104 M 0 147 L 760 147 M 0 190 L 760 190"></path>
          <path class="breadth-chart__ad-ma" d="${datedValuePath(chartPoints, "adMa20Thousands", domain, valueDomain, 760, 18, 190)}"></path>
          <path class="breadth-chart__ad" d="${datedValuePath(chartPoints, "adLineThousands", domain, valueDomain, 760, 18, 190)}"></path>
          ${renderChartCursorLine(18, 190)}
          ${axis.labels}
        </svg>
      `;
    })
    .join("");

  return `
    <article class="breadth-chart-card">
      <header>
        <div><span class="eyebrow">Cumulative Breadth</span><h3>AD 누적선 (천 종목)</h3></div>
        <div class="breadth-chart-legend"><span><i class="is-ad"></i>AD 누적선</span><span><i class="is-ma"></i>20일선</span></div>
      </header>
      <div class="breadth-chart" data-timeseries-chart="${chartId}">
        ${renderChartRangeControls(chartId)}
        ${layers}
        ${renderChartTooltip()}
      </div>
      <p class="breadth-chart-card__note">${breadthData.period.adLineBaseDate}부터 일별 상승-하락 종목 수 누계 · 화면은 1,000종목 단위 · 방향과 20일선 비교 중심</p>
    </article>
  `;
}

function renderBreadthFlowChart(breadthData) {
  const points = breadthData?.series ?? [];
  const hasDirectFlow = points.some((point) =>
    [point.foreignNetBuy5dEok, point.institutionNetBuy5dEok, point.programNetBuy5dEok]
      .some((value) => value !== null && value !== undefined && Number.isFinite(Number(value)))
  );
  if (points.length < 2 || !hasDirectFlow) return "";

  const chartId = registerInteractiveChart({
    series: [
      {
        label: "외국인 5D",
        points,
        valueKey: "foreignNetBuy5dEok",
        color: "var(--blue)",
        format: formatSignedEok
      },
      {
        label: "기관 5D",
        points,
        valueKey: "institutionNetBuy5dEok",
        color: "var(--green)",
        format: formatSignedEok
      },
      {
        label: "프로그램 5D",
        points,
        valueKey: "programNetBuy5dEok",
        color: "var(--amber)",
        format: formatSignedEok
      }
    ]
  });
  const layers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([points], range.id);
      const visible = pointsWithinDomain(points, domain, "foreignNetBuy5dEok");
      const combined = [
        { value: 0 },
        ...visible.flatMap((point) =>
          ["foreignNetBuy5dEok", "institutionNetBuy5dEok", "programNetBuy5dEok"]
            .filter((key) => point[key] !== null && point[key] !== undefined)
            .map((key) => ({ value: Number(point[key]) }))
        )
      ];
      const valueDomain = numericChartDomain(combined, "value", 0.12);
      const zeroY = 190 - ((0 - valueDomain.min) / (valueDomain.max - valueDomain.min)) * 172;
      const axis = renderMonthAxisFromDomain(domain, 760, 18, 190, 207);
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 210" role="img" aria-label="외국인 기관 프로그램 5거래일 순매수 누계">
          ${axis.grid}
          <path class="breadth-chart__grid" d="M 0 18 L 760 18 M 0 104 L 760 104 M 0 190 L 760 190"></path>
          <path class="breadth-chart__zero-line" d="M 0 ${zeroY} L 760 ${zeroY}"></path>
          <path class="breadth-chart__flow-foreign" d="${datedValuePath(points, "foreignNetBuy5dEok", domain, valueDomain, 760, 18, 190)}"></path>
          <path class="breadth-chart__flow-institution" d="${datedValuePath(points, "institutionNetBuy5dEok", domain, valueDomain, 760, 18, 190)}"></path>
          <path class="breadth-chart__flow-program" d="${datedValuePath(points, "programNetBuy5dEok", domain, valueDomain, 760, 18, 190)}"></path>
          ${renderChartCursorLine(18, 190)}
          ${axis.labels}
        </svg>
      `;
    })
    .join("");

  return `
    <article class="breadth-chart-card breadth-chart-card--flow">
      <header>
        <div><span class="eyebrow">Direct Order Flow · KRX</span><h3>투자자별 5일 순매수 누계</h3></div>
        <div class="breadth-chart-legend"><span><i class="is-flow-foreign"></i>외국인</span><span><i class="is-flow-institution"></i>기관</span><span><i class="is-flow-program"></i>프로그램</span></div>
      </header>
      <div class="breadth-chart" data-timeseries-chart="${chartId}">
        ${renderChartRangeControls(chartId)}
        ${layers}
        ${renderChartTooltip()}
      </div>
      <p class="breadth-chart-card__note">0 위는 순매수 · 0 아래는 순매도 · 거래대금 억원 단위 · 5거래일 누계로 일간 잡음 완화</p>
    </article>
  `;
}

function renderMarketBreadthPage(breadthData) {
  if (!breadthData?.latest || !breadthData?.series?.length) {
    return `
      <section class="breadth-page">
        <div class="empty-state">
          <h3>시장 내부강도 데이터 준비중</h3>
          ${renderNarrativeList(["KRX 로그인 기반 EOD 집계", "다음 운영 갱신에서 데이터 생성"], "narrative-list--compact")}
        </div>
      </section>
    `;
  }

  const latest = breadthData.latest;
  const quality = breadthData.quality ?? {};
  const state = latest.state ?? { label: "확인 필요", tone: "watch" };
  const directFlowLevel = diagnosticLevel(latest.directFlowPressure);
  const qualityLabel = quality.status === "ok" ? "정상" : quality.status === "warning" ? "주의" : "확인 필요";
  const investorFlowLabel = breadthData.source?.investorFlowStatus === "available" ? "연결" : "미연결";
  const programFlowLabel = breadthData.source?.programFlowStatus === "available" ? "연결" : "미연결";
  return `
    <section class="breadth-page">
      <div class="section-heading breadth-page__heading">
        <div>
          <span class="eyebrow">KOSPI Market Breadth</span>
          <h2>시장 내부강도</h2>
          ${renderNarrativeList(["지수 방향과 상승·하락 종목 확산을 함께 확인", "장 마감 EOD 기준 · 지수만으로 보이지 않는 내부 체력 관찰"], "narrative-list--compact")}
        </div>
        <div class="breadth-state breadth-state--${state.tone}">
          <span>현재 판독</span>
          <strong>${state.label}</strong>
          <small>${latest.date} · KRX EOD</small>
        </div>
      </div>

      <div class="breadth-metrics">
        <article><span>일간 확산도 (%)</span><strong>${formatSignedPct(latest.breadthPct)}</strong><small>5일 ${formatSignedPct(latest.breadthMa5Pct)} · 20일 ${formatSignedPct(latest.breadthMa20Pct)}</small></article>
        <article><span>상승 / 하락</span><strong>${formatNumber(latest.up)} / ${formatNumber(latest.down)}</strong><small>보합 ${formatNumber(latest.flat)} · 전체 ${formatNumber(latest.total)}</small></article>
        <article><span>당일 순확산 (Net)</span><strong>${latest.netBreadth > 0 ? "+" : ""}${formatNumber(latest.netBreadth)}종목</strong><small>상승-하락 · 비율 ${formatNumber(latest.adRatio, 2)}</small></article>
        <article><span>AD 누적선-20일선</span><strong>${formatSignedThousands(latest.adDistance20)}</strong><small>AD ${formatSignedThousands(latest.adLine)} · 20D ${formatSignedThousands(latest.adMa20)}</small></article>
      </div>

      <section class="breadth-flow-panel" aria-label="KRX 직접 수급 판독">
        <header>
          <div><span class="eyebrow">Direct Order Flow</span><h3>외국인·기관·프로그램 수급</h3></div>
          <div class="status-pill status-pill--${directFlowLevel.tone}">
            ${latest.directFlowPressure == null ? "자료 준비중" : `매도압력 ${formatNumber(latest.directFlowPressure, 1)}`}
          </div>
        </header>
        <div class="breadth-flow-metrics">
          <article><span>외국인</span><strong>${formatSignedEok(latest.foreignNetBuy5dEok)}</strong><small>당일 ${formatSignedEok(latest.foreignNetBuyEok)} · 5D 누계</small></article>
          <article><span>기관</span><strong>${formatSignedEok(latest.institutionNetBuy5dEok)}</strong><small>당일 ${formatSignedEok(latest.institutionNetBuyEok)} · 5D 누계</small></article>
          <article><span>프로그램</span><strong>${formatSignedEok(latest.programNetBuy5dEok)}</strong><small>당일 ${formatSignedEok(latest.programNetBuyEok)} · 차익+비차익</small></article>
          <article><span>기관 내부</span><strong>금투 ${formatSignedEok(latest.financialInvestmentNetBuyEok)}</strong><small>연기금 ${formatSignedEok(latest.pensionNetBuyEok)} · 당일</small></article>
        </div>
        ${renderNarrativeList([
          "양수 = 순매수 · 음수 = 순매도",
          "압력점수 = 5일 순매도 강도를 직전 최대 252거래일과 비교",
          "프로그램 = 주문 방식 · 외국인·기관 거래와 일부 중첩 가능",
          "수급 진단 전용 · 기존 종합점수와 6개 가중치에는 미반영"
        ], "narrative-list--compact")}
      </section>

      <section class="breadth-unit-guide" aria-label="시장 내부강도 단위 읽는 법">
        <strong>단위 읽는 법</strong>
        <div><span>일간 확산도</span><p>범위 -100~+100 · 0=균형</p></div>
        <div><span>당일 순확산</span><p>상승 종목 수 - 하락 종목 수</p></div>
        <div><span>AD 누적선</span><p>순확산 누계 · 화면은 천 종목 단위</p></div>
      </section>

      <section class="breadth-interpretation-guide" aria-label="시장 내부강도 해석 기준">
        <header><span class="eyebrow">Reading Guide</span><h3>어떻게 읽나요</h3></header>
        <div>
          <strong>일간 확산도</strong>
          <ul>
            <li><b>0 위</b><span>상승 종목 우위 · 높을수록 상승 참여 확대</span></li>
            <li><b>0 아래</b><span>하락 종목 우위 · 낮을수록 매도 확산</span></li>
            <li><b>지수와 반대</b><span>대형주 편중 또는 업종 순환 가능성</span></li>
          </ul>
        </div>
        <div>
          <strong>AD 누적선</strong>
          <ul>
            <li><b>상승·20일선 위</b><span>시장 내부 체력 개선 누적</span></li>
            <li><b>하락·20일선 아래</b><span>시장 내부 약화 누적</span></li>
            <li><b>절대값보다</b><span>기울기·20일선·지수와의 괴리 중심</span></li>
          </ul>
        </div>
      </section>

      <div class="breadth-reading">
        ${renderNarrativeList(latest.interpretation, "narrative-list--compact")}
      </div>

      <div class="breadth-chart-grid">
        ${renderBreadthPriceChart(breadthData)}
        ${renderBreadthAdChart(breadthData)}
        ${renderBreadthFlowChart(breadthData)}
      </div>

      <section class="breadth-trust">
        <div>
          <span class="eyebrow">Data Quality</span>
          <h3>운영 신뢰도 <em class="status-pill status-pill--${quality.status === "ok" ? "good" : "watch"}">${qualityLabel}</em></h3>
          <dl>
            <div><dt>관측기간</dt><dd>${breadthData.period.startDate} ~ ${breadthData.period.endDate}</dd></div>
            <div><dt>거래일</dt><dd>${formatNumber(breadthData.period.observations)}개</dd></div>
            <div><dt>최근 종목 수</dt><dd>${formatNumber(quality.minRecentTotal)}~${formatNumber(quality.maxRecentTotal)}개</dd></div>
            <div><dt>수집 실패</dt><dd>${formatNumber(quality.failedDates?.length ?? 0)}일</dd></div>
            <div><dt>외국인·기관</dt><dd>${investorFlowLabel}</dd></div>
            <div><dt>프로그램</dt><dd>${programFlowLabel}</dd></div>
          </dl>
        </div>
        <div>
          <span class="eyebrow">Source & Limits</span>
          <h3>KRX 원천 · pykrx</h3>
          ${renderNarrativeList([
            "stock.get_market_ohlcv(date, market=KOSPI)",
            "투자자 순매수: get_market_trading_value_by_date · 거래대금",
            "프로그램 순매수: KRX MDCSTAT02601 · 차익+비차익 전체",
            "우선주·SPAC·REIT 포함 가능 · ETF·ETN 제외",
            "화면 집계와 종목 분류·기준시각에 따라 차이 가능",
            "VKOSPI 미결합 · risk-on·panic 확정 판정 보류"
          ], "narrative-list--compact")}
        </div>
      </section>
    </section>
  `;
}


function renderSummary(data, timeseries, mlRisk, elsRisk, hmmRegime, breadthData) {
  const market = data.sections.find((section) => section.id === "market");
  market.asOf = data.metadata.asOf;

  return `
    ${renderDecisionCockpit(data, timeseries, mlRisk, hmmRegime)}
    ${renderShockDecomposition(market, timeseries, breadthData)}
    ${renderScoreAttribution(market, timeseries)}
    ${renderBreadthSummaryPanel(breadthData)}
    ${renderMlRiskSignalPanel(mlRisk, market, elsRisk)}
    ${renderElsIndexRiskPanel(elsRisk)}
    ${renderHmmRegimePanel(hmmRegime)}
  `;
}

function renderModelPanel(section) {
  const model = section.model ?? {};
  const normalization = model.normalization;
  const sources = model.dataSources ?? [];
  const methodologyItems =
    section.id === "market"
      ? [
          "관측창: 최대 2년",
          "입력: 레벨 · 20개 관측치 변화율 · 20일 실현변동성 · 252일 낙폭",
          "주간 SCFI: 4개 관측치 변화",
          "시계열 정렬: 직전 가용값 결합 · 미래값 미사용",
          "관찰카드: 가중치 0 · 종합점수 미반영"
        ]
      : model.methodology ?? "지표별 점수의 가중평균 합성";
  const methodologyReference = (model.references ?? []).find(
    (reference) =>
      reference?.url?.includes("bok.or.kr") ||
      reference?.label?.includes("FSI/FVI")
  );

  return `
    <section class="model-panel">
      <article>
        <span class="eyebrow">Model</span>
        <h3>${model.version ?? "risk-model"}</h3>
        ${renderNarrativeList(methodologyItems, "narrative-list--compact")}
      </article>
      <article class="model-panel__normalization">
        <span class="eyebrow">Normalization</span>
        <h3>${normalization ? `${normalization.percentileWeight * 100}% 분위수 · ${normalization.zScoreWeight * 100}% z · ${normalization.robustZScoreWeight * 100}% robust z` : "Weighted score"}</h3>
        ${renderNarrativeList(
          normalization
            ? [`z-score ${normalization.zScoreMapping}`, `robust z-score ${normalization.robustZScore}`, `${normalization.scoreRange} 점수 매핑`]
            : "섹션 모델 설정 사용",
          "narrative-list--compact"
        )}
        ${
          methodologyReference
            ? `
              <footer class="model-reference">
                <span>복수 지표를 표준화해 종합지수로 합성하는 접근 참고</span>
                <a href="${methodologyReference.url}" target="_blank" rel="noopener noreferrer">
                  한국은행 FSI·FVI 설명
                  <span aria-hidden="true">↗</span>
                </a>
              </footer>
            `
            : ""
        }
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

function renderGroupScoreTrend(section, group, timeseries) {
  const points = buildGroupCompositeSeries(section, group.id, timeseries);
  if (points.length < 2) {
    return `
      <div class="group-card__trend group-card__trend--empty">
        <span>가중 점수 흐름</span>
        <small>시계열 준비중</small>
      </div>
    `;
  }

  const chartId = registerInteractiveChart({
    width: 280,
    tooltipMode: "all",
    series: [
      {
        label: riskGroupDefinitions[group.id]?.label ?? group.label,
        points,
        valueKey: "value",
        color: "var(--group-accent)",
        format: (value) => `${Number(value).toFixed(1)}점`
      }
    ]
  });
  const layers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([points], range.id);
      const visible = pointsWithinDomain(points, domain, "value");
      const rawValueDomain = numericChartDomain(visible, "value", 0.18);
      const center = (rawValueDomain.min + rawValueDomain.max) / 2;
      const halfSpan = Math.max((rawValueDomain.max - rawValueDomain.min) / 2, 10);
      const valueDomain = {
        min: Math.max(0, center - halfSpan),
        max: Math.min(100, center + halfSpan)
      };
      const axis = renderMonthAxisFromDomain(domain, 280, 8, 62, 81);
      const latest = visible.at(-1);
      const latestX = latest ? xFromDate(latest.date, domain, 280) : 0;
      const latestY = latest
        ? 62 -
          ((Number(latest.value) - valueDomain.min) /
            Math.max(valueDomain.max - valueDomain.min, 1)) *
            54
        : 0;
      return `
        <svg
          class="${chartRangeLayerClass(range.id)}"
          data-chart-range-layer="${range.id}"
          data-chart-svg
          viewBox="0 0 280 88"
          role="img"
          aria-label="${riskGroupDefinitions[group.id]?.label ?? group.label} ${range.label} 가중 점수 흐름"
        >
          ${axis.grid}
          <path class="group-card__trend-grid" d="M 0 35 L 280 35 M 0 62 L 280 62"></path>
          <path
            class="group-card__trend-line"
            d="${datedValuePath(points, "value", domain, valueDomain, 280, 8, 62)}"
          ></path>
          ${
            latest
              ? `<circle class="group-card__trend-end" cx="${latestX.toFixed(2)}" cy="${latestY.toFixed(2)}" r="3"></circle>`
              : ""
          }
          ${renderChartCursorLine(8, 62)}
          ${axis.labels}
        </svg>
      `;
    })
    .join("");

  return `
    <div class="group-card__trend" data-timeseries-chart="${chartId}">
      <div class="group-card__trend-heading">
        <span>가중 점수 흐름</span>
        <small data-chart-active-range-label>${chartRangeOptions.find((option) => option.id === activeChartRange)?.label ?? "YTD"}</small>
      </div>
      ${layers}
      ${renderChartTooltip()}
    </div>
  `;
}

function renderGroupScores(section, timeseries) {
  const groups = section.groupScores ?? [];
  if (!groups.length) return "";

  return `
    <section class="group-panel">
      <div class="risk-color-legend" aria-label="시장리스크 그래프 색상 기준">
        <div class="risk-color-legend__set">
          <strong>그룹색</strong>
          ${groups
            .map((group) => {
              const definition = riskGroupDefinitions[group.id] ?? {
                legendLabel: group.label
              };
              return `
                <span class="risk-color-key risk-color-key--${group.id}">
                  <i aria-hidden="true"></i>${definition.legendLabel ?? definition.label}
                </span>
              `;
            })
            .join("")}
        </div>
        <div class="risk-color-legend__set">
          <strong>카드 추세</strong>
          <span class="risk-color-key risk-color-key--trend-up"><i aria-hidden="true"></i>부담 상승</span>
          <span class="risk-color-key risk-color-key--trend-down"><i aria-hidden="true"></i>부담 하락</span>
          <span class="risk-color-key risk-color-key--trend-flat"><i aria-hidden="true"></i>보합</span>
        </div>
      </div>
      ${groups
        .map((group) => {
          const definition = riskGroupDefinitions[group.id] ?? {
            label: group.label,
            shortLabel: group.label,
            englishLabel: group.label,
            description: "리스크 구성 지표"
          };
          const weightedIndicators = (section.indicators ?? []).filter(
            (indicator) => indicator.group === group.id && indicator.role !== "observation"
          ).sort(
            (left, right) =>
              Number(right.contribution ?? 0) - Number(left.contribution ?? 0) ||
              Number(right.value ?? 0) - Number(left.value ?? 0)
          );
          const observationIndicators = (section.indicators ?? []).filter(
            (indicator) => indicator.group === group.id && indicator.role === "observation"
          ).sort((left, right) => Number(right.value ?? 0) - Number(left.value ?? 0));
          const tooltipId = `group-tooltip-${section.id}-${group.id}`;
          return `
            <article class="group-card group-card--${group.id}" data-risk-group-card="${group.id}">
              <div class="group-card__heading">
                <div>
                  <span class="eyebrow">가중 ${group.indicatorCount}${group.observationCount ? ` · 관찰 ${group.observationCount}` : ""}</span>
                  <h3>${definition.label}<small>${definition.englishLabel}</small></h3>
                </div>
                <button
                  type="button"
                  class="group-card__info"
                  aria-label="${definition.label} 구성 지표 보기"
                  aria-describedby="${tooltipId}"
                  title="${definition.label} 구성 지표 보기"
                >i</button>
              </div>
              <strong>${formatScore(group.score)}</strong>
              <div class="mini-bar" aria-hidden="true">
                <span style="width:${clampScore(group.score)}%"></span>
              </div>
              <footer>
                <span>비중 ${(group.weight * 100).toFixed(1)}%</span>
                <span>기여도 +${Number(group.contribution).toFixed(2)}점</span>
              </footer>
              ${renderGroupScoreTrend(section, group, timeseries)}
              <button
                type="button"
                class="group-card__filter"
                data-group-filter="${group.id}"
                data-section-id="${section.id}"
                aria-pressed="false"
              >이 그룹만 보기</button>
              <div class="group-card__tooltip" id="${tooltipId}" role="tooltip">
                <strong>${definition.label} 구성</strong>
                ${renderNarrativeList(definition.description, "narrative-list--compact group-card__description")}
                <span>가중 반영 · 기여도 높은 순</span>
                <ul>
                  ${weightedIndicators
                    .map(
                      (indicator) =>
                        `<li>
                           <span>${indicator.name}</span>
                           <small>${clampScore(indicator.value).toFixed(1)} × ${Number(indicator.contributionPct ?? 0).toFixed(1)}%</small>
                           <strong>+${Number(indicator.contribution ?? 0).toFixed(2)}점</strong>
                         </li>`
                    )
                    .join("")}
                </ul>
                ${
                  observationIndicators.length
                    ? `<span>관찰 전용 · 가중치 미반영</span>
                       <ul>
                         ${observationIndicators
                           .map(
                             (indicator) =>
                               `<li>
                                  <span>${indicator.name}</span>
                                  <small>${clampScore(indicator.value).toFixed(1)} × 0.0%</small>
                                  <strong>+0.00점</strong>
                                </li>`
                           )
                           .join("")}
                       </ul>`
                    : ""
                }
              </div>
            </article>
          `;
        })
        .join("")}
    </section>
  `;
}

function renderObservationJournalTrend(section, item, timeseries) {
  if (item.score == null) return "";

  const points = buildObservationJournalSeries(section, item, timeseries);
  if (points.length < 2) {
    return `
      <div class="observation-journal__trend observation-journal__trend--empty">
        <span>검증 점수 흐름</span>
        <small>시계열 준비중</small>
      </div>
    `;
  }

  const width = 420;
  const chartId = registerInteractiveChart({
    width,
    tooltipMode: "all",
    series: [
      {
        label: item.title,
        points,
        valueKey: "value",
        color: "var(--journal-accent)",
        format: (value) => `${Number(value).toFixed(1)}점`
      }
    ]
  });
  const layers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([points], range.id);
      const visible = pointsWithinDomain(points, domain, "value");
      const rawValueDomain = numericChartDomain(visible, "value", 0.18);
      const center = (rawValueDomain.min + rawValueDomain.max) / 2;
      const halfSpan = Math.max((rawValueDomain.max - rawValueDomain.min) / 2, 10);
      const valueDomain = {
        min: Math.max(0, center - halfSpan),
        max: Math.min(100, center + halfSpan)
      };
      const axis = renderMonthAxisFromDomain(domain, width, 8, 62, 82);
      const latest = visible.at(-1);
      const latestX = latest ? xFromDate(latest.date, domain, width) : 0;
      const latestY = latest
        ? 62 -
          ((Number(latest.value) - valueDomain.min) /
            Math.max(valueDomain.max - valueDomain.min, 1)) *
            54
        : 0;
      return `
        <svg
          class="${chartRangeLayerClass(range.id)}"
          data-chart-range-layer="${range.id}"
          data-chart-svg
          viewBox="0 0 ${width} 90"
          role="img"
          aria-label="${item.title} ${range.label} 검증 점수 흐름"
        >
          ${axis.grid}
          <path class="observation-journal__trend-grid" d="M 0 35 L ${width} 35 M 0 62 L ${width} 62"></path>
          <path
            class="observation-journal__trend-line"
            d="${datedValuePath(points, "value", domain, valueDomain, width, 8, 62)}"
          ></path>
          ${
            latest
              ? `<circle class="observation-journal__trend-end" cx="${latestX.toFixed(2)}" cy="${latestY.toFixed(2)}" r="3"></circle>`
              : ""
          }
          ${renderChartCursorLine(8, 62)}
          ${axis.labels}
        </svg>
      `;
    })
    .join("");

  return `
    <div class="observation-journal__trend" data-timeseries-chart="${chartId}">
      <div class="observation-journal__trend-heading">
        <span>검증 점수 흐름</span>
        <small data-chart-active-range-label>${chartRangeOptions.find((option) => option.id === activeChartRange)?.label ?? "YTD"}</small>
      </div>
      ${layers}
      ${renderChartTooltip()}
    </div>
  `;
}

function renderObservationJournalDetail(item) {
  const components = item.components ?? [];
  if (!components.length) return "";

  return `
    <div class="observation-journal__detail-heading">
      <strong>검증 점수 구성</strong>
      <span>일지 내부 비중 · 종합점수 가중치 0</span>
    </div>
    <ul>
      ${components
        .map(
          (component) => `
            <li>
              <span>${component.name}</span>
              <small>${formatNumber(component.value, 1)} × ${(Number(component.weight) * 100).toFixed(0)}%</small>
              <strong>+${formatNumber(component.contribution, 2)}점</strong>
            </li>
          `
        )
        .join("")}
    </ul>
    <p>구성 기여점수 합계 ${formatNumber(item.score, 1)}점 · 높을수록 해당 시장 의견을 지지</p>
  `;
}

function renderObservationJournal(section, timeseries) {
  const items = section.observationJournal ?? [];
  if (!items.length) return "";

  return `
    <section class="observation-journal">
      <header class="observation-journal__header">
        <div>
          <span class="eyebrow">Market Observation Journal</span>
          <h3>시장 의견 검증 일지</h3>
        </div>
        <p>전망을 점수에 선반영하지 않고 관측값으로 확인</p>
      </header>
      <div class="observation-journal__list">
        ${items
          .map((item) => {
            const detailId = `observation-detail-${item.id}`;
            const hasScore = item.score !== null && item.score !== undefined;
            const hasComponents = (item.components ?? []).length > 0;
            const decision =
              !hasScore && String(item.decision ?? "").includes("점수화 보류")
                ? ""
                : item.decision;
            return `
              <article class="observation-journal__item observation-journal__item--${item.tone ?? "muted"}">
                <div class="observation-journal__summary">
                  <div>
                    ${decision ? `<span>${decision}</span>` : ""}
                    <div class="observation-journal__title">
                      <h4>${item.title}</h4>
                      ${
                        hasComponents
                          ? `<button
                               type="button"
                               class="observation-journal__info"
                               data-observation-detail-toggle="${item.id}"
                               aria-label="${item.title} 구성 지표 보기"
                               aria-controls="${detailId}"
                               aria-expanded="false"
                               title="${item.title} 구성 지표 보기"
                             >i</button>`
                          : ""
                      }
                    </div>
                  </div>
                  <div>
                    <span>${item.status}</span>
                    ${hasScore ? `<strong>${formatNumber(item.score, 1)}점</strong>` : ""}
                  </div>
                </div>
                ${renderObservationJournalTrend(section, item, timeseries)}
                <div class="observation-journal__evidence">
                  ${(item.evidence ?? []).map((evidence) => `<span>${evidence}</span>`).join("")}
                </div>
                ${renderNarrativeList(item.assessment, "narrative-list--compact")}
                <footer><span>운영</span><strong>${item.operation}</strong></footer>
                ${
                  hasComponents
                    ? `<div class="observation-journal__detail" id="${detailId}" hidden>
                         ${renderObservationJournalDetail(item)}
                       </div>`
                    : ""
                }
              </article>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderSection(section, timeseries, backtest, stressEpisodes, marketIndexes, activeTab, provenance = null) {
  const isPlanned = section.status !== "active";
  const initiallySortedIndicators = sortedIndicators(section, timeseries);
  const isActive = section.id === activeTab;
  const sectionDescription =
    section.id === "market"
      ? [
          "핵심: 주가지수 · 환율 · 변동성 · 금리 · 크레딧 · 수급",
          "확장: 운임 · 에너지 · 중국 수요 · AI 반도체",
          "관찰 전용: 엔 캐리 · 금리차 · 옵션 기간구조 · 시장 폭 · 재인플레이션"
        ]
      : section.description;

  return `
    <section
      class="risk-section tab-panel ${isActive ? "is-active" : ""}"
      id="panel-${section.id}"
      data-panel="${section.id}"
      role="tabpanel"
      aria-labelledby="tab-${section.id}"
      tabindex="0"
      ${isActive ? "" : "hidden"}
    >
      <div class="section-heading">
        <div>
          <span class="eyebrow">${section.owner}</span>
          <h2>${section.label}</h2>
          ${renderNarrativeList(sectionDescription, "narrative-list--compact")}
        </div>
        <div class="section-score">
          <span class="status-pill status-pill--${section.level.tone}">${section.level.label}</span>
          <strong>${formatScore(section.score)}</strong>
        </div>
      </div>

      ${!isPlanned && section.id === "market" ? renderMarketChartRangeDock() : ""}

      ${
        isPlanned
          ? `<div class="empty-state">
              <h3>${section.label} 모듈 준비중</h3>
              ${renderNarrativeList([
                `${section.id}.indicators 배열에 지표 추가`,
                "탭 enabled 값을 true로 변경",
                "변경 즉시 화면 노출"
              ], "narrative-list--compact")}
            </div>`
          : `
            ${renderModelPanel(section)}
            ${renderGroupScores(section, timeseries)}
            ${section.id === "market" ? renderObservationJournal(section, timeseries) : ""}
            ${
              section.id === "market"
                ? `<div data-market-direction-slot>${renderMarketIndexTrendPanel(marketIndexes)}</div>`
                : ""
            }
            ${renderCompositeTrend(section.id === "market" ? section : null, timeseries)}
            ${renderGauge(section.score, section.level, section.model.thresholds)}
            ${
              section.id === "market"
                ? renderIndicatorSortControls(section.id, initiallySortedIndicators.length)
                : ""
            }
            <div class="indicator-grid" data-indicator-grid="${section.id}">
              ${initiallySortedIndicators
                .map((indicator) => renderIndicator(indicator, section.model.thresholds, timeseries, { ...provenance, model: section.model }))
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

      ${
        !isPlanned && section.id === "market"
          ? `<div data-market-history-slot>
              ${renderBacktestPanel(backtest)}
              ${renderStressEpisodesPanel(stressEpisodes)}
            </div>`
          : ""
      }
    </section>
  `;
}

function nearestChartPoint(points, targetTime, domain, valueKey) {
  const visible = pointsWithinDomain(points, domain, valueKey);
  if (!visible.length) return null;
  return visible.reduce((nearest, point) =>
    Math.abs(dateMs(point.date) - targetTime) < Math.abs(dateMs(nearest.date) - targetTime)
      ? point
      : nearest
  );
}

function hideChartCursor(chart) {
  chart.querySelectorAll("[data-chart-cursor-line]").forEach((line) => {
    line.classList.remove("is-visible");
  });
  chart.querySelector("[data-chart-tooltip]")?.classList.remove("is-visible");
}

function effectiveChartZoom(element) {
  const currentZoom = Number(element.currentCSSZoom);
  if (Number.isFinite(currentZoom) && currentZoom > 0) return currentZoom;

  const rect = element.getBoundingClientRect();
  const layoutWidth = Number(element.offsetWidth);
  const inferredZoom = layoutWidth > 0 ? rect.width / layoutWidth : 1;
  return Number.isFinite(inferredZoom) && inferredZoom > 0 ? inferredZoom : 1;
}

function chartPointerInViewBox(svg, event, fallbackWidth) {
  const matrix = svg.getScreenCTM?.();
  if (matrix && typeof svg.createSVGPoint === "function") {
    try {
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      const transformed = point.matrixTransform(matrix.inverse());
      if (Number.isFinite(transformed.x)) return transformed.x;
    } catch (_error) {
      // 분리된 SVG나 구형 브라우저에서는 표시 사각형 기준으로 계산합니다.
    }
  }

  const rect = svg.getBoundingClientRect();
  if (!rect.width) return null;
  return ((event.clientX - rect.left) / rect.width) * fallbackWidth;
}

function updateChartCursor(chart, svg, event) {
  const model = interactiveChartRegistry.get(chart.dataset.timeseriesChart);
  if (!model) return;
  const domain = chartRangeDomain(
    model.series.map((item) => item.points),
    activeChartRange
  );
  if (!domain) return;

  const svgRect = svg.getBoundingClientRect();
  if (!svgRect.width) return;
  const viewBoxWidth = svg.viewBox?.baseVal?.width || model.width;
  const pointerX = chartPointerInViewBox(svg, event, viewBoxWidth);
  if (!Number.isFinite(pointerX)) return;
  const plotSpan = Math.max(model.plotRight - model.plotLeft, 1);
  const ratio = Math.max(0, Math.min(1, (pointerX - model.plotLeft) / plotSpan));
  const requestedTime = domain.start + ratio * domain.span;
  const hoveredIndex = Number(svg.dataset.chartSeriesIndex);
  const hasHoveredSeries = Number.isInteger(hoveredIndex) && model.series[hoveredIndex];
  const anchorSeries = hasHoveredSeries ? model.series[hoveredIndex] : model.series[0];
  const anchorPoint = nearestChartPoint(
    anchorSeries.points,
    requestedTime,
    domain,
    anchorSeries.valueKey
  );
  if (!anchorPoint) return;

  const cursorTime = dateMs(anchorPoint.date);
  const cursorRatio = Math.max(0, Math.min(1, (cursorTime - domain.start) / domain.span));
  chart
    .querySelectorAll(
      `[data-chart-range-layer="${activeChartRange}"] [data-chart-cursor-line]`
    )
    .forEach((line) => {
      const x = model.plotLeft + cursorRatio * (model.plotRight - model.plotLeft);
      line.setAttribute("x1", x.toFixed(2));
      line.setAttribute("x2", x.toFixed(2));
      line.classList.add("is-visible");
    });

  const tooltipSeries =
    model.tooltipMode === "hovered" && hasHoveredSeries
      ? [model.series[hoveredIndex]]
      : model.series;
  const rows = tooltipSeries
    .map((item) => {
      const point = nearestChartPoint(item.points, cursorTime, domain, item.valueKey);
      if (!point) return "";
      const value = item.format
        ? item.format(point[item.valueKey], point)
        : formatNumber(point[item.valueKey], 2);
      const detail = item.detail?.(point);
      const status = item.status?.(point) ?? "EOD";
      const alternateDate = point.date === anchorPoint.date ? "" : formatShortDate(point.date);
      const secondary = [detail, alternateDate].filter(Boolean).join(" · ");
      return `
        <div class="chart-cursor-tooltip__row">
          <i style="background:${item.color ?? "var(--blue)"}"></i>
          <span>${item.label}</span>
          <strong>${value}</strong>
          ${secondary ? `<small>${secondary}</small>` : ""}
          <em class="${status === "잠정" ? "is-provisional" : ""}">${status}</em>
        </div>
      `;
    })
    .filter(Boolean)
    .join("");
  const tooltip = chart.querySelector("[data-chart-tooltip]");
  if (!tooltip || !rows) return;
  tooltip.innerHTML = `<b>${anchorPoint.date}</b>${rows}`;
  const chartRect = chart.getBoundingClientRect();
  const cssZoom = effectiveChartZoom(chart);
  const layoutWidth = chartRect.width / cssZoom;
  const layoutHeight = chartRect.height / cssZoom;
  const horizontalInset = Math.min(110, Math.max(16, layoutWidth / 2));
  const tooltipPointerX = (event.clientX - chartRect.left) / cssZoom;
  const localX = Math.max(
    horizontalInset,
    Math.min(layoutWidth - horizontalInset, tooltipPointerX)
  );
  const localY = (event.clientY - chartRect.top) / cssZoom;
  const above = localY > layoutHeight * 0.52;
  tooltip.style.left = `${localX}px`;
  tooltip.style.top = `${above ? localY - 14 : localY + 14}px`;
  tooltip.dataset.placement = above ? "above" : "below";
  tooltip.classList.add("is-visible");
}

function activateChartRange(rangeId) {
  if (!chartRangeOptions.some((option) => option.id === rangeId)) return;
  activeChartRange = rangeId;
  const activeLabel =
    chartRangeOptions.find((option) => option.id === rangeId)?.label ?? "YTD";
  app.querySelectorAll("[data-chart-range]").forEach((button) => {
    const selected = button.dataset.chartRange === rangeId;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-pressed", selected ? "true" : "false");
  });
  app.querySelectorAll("[data-chart-range-layer]").forEach((layer) => {
    layer.classList.toggle("is-active", layer.dataset.chartRangeLayer === rangeId);
  });
  app.querySelectorAll("[data-chart-active-range-label]").forEach((label) => {
    label.textContent = activeLabel;
  });
  app.querySelectorAll("[data-timeseries-chart]").forEach(hideChartCursor);
}

function initializeInteractiveCharts(scope = app) {
  scope.querySelectorAll("[data-chart-range]:not([data-chart-range-bound])").forEach((button) => {
    button.dataset.chartRangeBound = "true";
    button.addEventListener("click", () => activateChartRange(button.dataset.chartRange));
  });
  scope
    .querySelectorAll("[data-timeseries-chart]:not([data-timeseries-chart-bound])")
    .forEach((chart) => {
      chart.dataset.timeseriesChartBound = "true";
      const handlePointer = (event) => {
        const target = event.target instanceof Element ? event.target : null;
        const svg = target?.closest("svg[data-chart-svg]");
        if (!svg || !chart.contains(svg)) return;
        updateChartCursor(chart, svg, event);
      };
      chart.addEventListener("pointermove", handlePointer, { passive: true });
      chart.addEventListener("pointerdown", handlePointer, { passive: true });
      chart.addEventListener("pointerleave", () => hideChartCursor(chart));
    });
  activateChartRange(activeChartRange);
}

const monitoringTaskView = {
  crash5d5pct: {
    probabilityKey: "crash5d5pctProbabilityPct",
    targetKey: "targetCrash5d5pct",
    label: "5일 중 -5% 도달"
  },
  crash5d10pct: {
    probabilityKey: "crash5d10pctProbabilityPct",
    targetKey: "targetCrash5d10pct",
    label: "5일 중 -10% 도달"
  }
};

function formatModelMetric(value, digits = 3) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "표본 부족";
}

function renderMonitoringMetricCard(label, metrics, field, { percent = false } = {}) {
  const value = Number(metrics?.[field]);
  const display = Number.isFinite(value)
    ? percent
      ? `${(value * 100).toFixed(1)}%`
      : value.toFixed(3)
    : "산출 대기";
  return `
    <div class="monitoring-metric">
      <span>${label}</span>
      <strong>${display}</strong>
      <small>${Number(metrics?.observations ?? 0)}개 · 급락 ${Number(metrics?.eventCount ?? 0)}건</small>
    </div>
  `;
}

function renderMonitoringTimeline(taskId, task, series) {
  const view = monitoringTaskView[taskId];
  const valid = (series ?? []).filter(
    (row) => Number.isFinite(Number(row[view.probabilityKey])) && row[view.targetKey] !== null && row[view.targetKey] !== undefined
  );
  if (valid.length < 2) return `<div class="empty-state"><h3>OOS 결과 누적 중</h3><span>실제 5거래일 결과가 확정되면 표시</span></div>`;
  const chartId = registerInteractiveChart({
    series: [
      {
        label: "도달확률",
        points: valid,
        valueKey: view.probabilityKey,
        color: "var(--red)",
        format: (value) => `${Number(value).toFixed(1)}%`
      },
      {
        label: "5D 최저수익률",
        points: valid,
        valueKey: "forwardMinReturn5dPct",
        color: "var(--blue)",
        format: (value) => `${Number(value).toFixed(1)}%`
      }
    ]
  });
  const threshold = Number(task?.cases?.alertThresholdPct ?? 50);
  const rangeLayers = chartRangeOptions
    .map((range) => {
      const domain = chartRangeDomain([valid], range.id);
      const visible = pointsWithinDomain(valid, domain, view.probabilityKey);
      const axis = renderMonthAxisFromDomain(domain, 760, 18, 150, 174);
      const probabilityPath = datedValuePath(visible, view.probabilityKey, domain, { min: 0, max: 100 }, 760, 18, 150);
      const eventMarks = visible
        .filter((row) => Number(row[view.targetKey]) === 1)
        .map((row) => {
          const x = xFromDate(row.date, domain, 760);
          return `<line class="monitoring-chart__event" x1="${x.toFixed(2)}" x2="${x.toFixed(2)}" y1="18" y2="150"></line>`;
        })
        .join("");
      const thresholdY = 150 - (threshold / 100) * 132;
      return `
        <svg class="${chartRangeLayerClass(range.id)}" data-chart-range-layer="${range.id}" data-chart-svg viewBox="0 0 760 180" role="img" aria-label="${view.label} 경보와 실제 결과 비교">
          ${axis.grid}
          <path class="trend-chart__grid" d="M 0 51 L 760 51 M 0 84 L 760 84 M 0 117 L 760 117 M 0 150 L 760 150"></path>
          <line class="monitoring-chart__threshold" x1="0" x2="760" y1="${thresholdY.toFixed(2)}" y2="${thresholdY.toFixed(2)}"></line>
          ${eventMarks}
          <path class="monitoring-chart__probability" d="${probabilityPath}"></path>
          ${renderChartCursorLine(18, 150)}
          ${axis.labels}
        </svg>
      `;
    })
    .join("");
  return `
    <div class="monitoring-chart" data-timeseries-chart="${chartId}">
      <div class="monitoring-chart__header">
        <div><strong>경보와 실제 결과</strong><span>붉은 세로선: 이후 5일 내 실제 도달이 확인된 예측일 · 점선: 경보 임계 ${threshold.toFixed(0)}%</span></div>
        ${renderChartRangeControls(chartId)}
      </div>
      ${rangeLayers}
      ${renderChartTooltip()}
    </div>
  `;
}

function renderMonitoringCases(title, rows, emptyText) {
  return `
    <section class="monitoring-cases">
      <h4>${title}</h4>
      <div>
        ${
          rows?.length
            ? rows
                .slice(0, 6)
                .map(
                  (row) => `<span><b>${row.date}</b><strong>${Number(row.probabilityPct).toFixed(1)}%</strong><small>5D 최저 ${Number(row.forwardMinReturn5dPct).toFixed(1)}%</small></span>`
                )
                .join("")
            : `<p>${emptyText}</p>`
        }
      </div>
    </section>
  `;
}

function renderMonitoringTask(taskId, task, series, active) {
  const metrics3m = task?.metrics?.["3m"] ?? {};
  const metrics6m = task?.metrics?.["6m"] ?? {};
  const cases = task?.cases ?? {};
  return `
    <div class="monitoring-task ${active ? "is-active" : ""}" data-monitoring-task-panel="${taskId}" ${active ? "" : "hidden"}>
      <div class="monitoring-status monitoring-status--${task?.status?.tone ?? "watch"}">
        <div><span>최근 운영판정</span><strong>${task?.status?.label ?? "산출 대기"}</strong></div>
        <p>${task?.status?.reason ?? "OOS 결과 누적 중"}</p>
      </div>
      <section class="monitoring-window-grid">
        <div class="monitoring-window">
          <header><strong>최근 3개월</strong><span>${metrics3m.startDate ?? "-"} ~ ${metrics3m.endDate ?? "-"}</span></header>
          <div class="monitoring-metrics">
            ${renderMonitoringMetricCard("AUC", metrics3m, "auc")}
            ${renderMonitoringMetricCard("AP", metrics3m, "averagePrecision")}
            ${renderMonitoringMetricCard("Brier", metrics3m, "brier")}
            ${renderMonitoringMetricCard("기준 대비 일별 승률", metrics3m, "dailyWinRate", { percent: true })}
            ${renderMonitoringMetricCard("기준 대비 fold 승률", metrics3m, "foldWinRate", { percent: true })}
          </div>
        </div>
        <div class="monitoring-window">
          <header><strong>최근 6개월</strong><span>${metrics6m.startDate ?? "-"} ~ ${metrics6m.endDate ?? "-"}</span></header>
          <div class="monitoring-metrics">
            ${renderMonitoringMetricCard("AUC", metrics6m, "auc")}
            ${renderMonitoringMetricCard("AP", metrics6m, "averagePrecision")}
            ${renderMonitoringMetricCard("Brier", metrics6m, "brier")}
            ${renderMonitoringMetricCard("기준 Brier", metrics6m, "baselineBrier")}
            ${renderMonitoringMetricCard("기준 대비 fold 승률", metrics6m, "foldWinRate", { percent: true })}
          </div>
        </div>
      </section>
      ${renderMonitoringTimeline(taskId, task, series)}
      <section class="monitoring-calibration">
        <div><span class="eyebrow">Calibration</span><h3>확률구간별 실제 급락 빈도</h3></div>
        <div class="monitoring-calibration__rows">
          ${(task?.calibrationBuckets ?? [])
            .map(
              (bucket) => `
                <div>
                  <span>${bucket.label}%</span>
                  <div class="monitoring-calibration__track"><i style="width:${Math.max(0, Math.min(100, Number(bucket.actualFrequencyPct ?? 0)))}%"></i></div>
                  <strong>${bucket.actualFrequencyPct == null ? "-" : `${Number(bucket.actualFrequencyPct).toFixed(1)}%`}</strong>
                  <small>${bucket.observations}개 · 평균예측 ${bucket.averageProbabilityPct == null ? "-" : `${Number(bucket.averageProbabilityPct).toFixed(1)}%`}</small>
                </div>
              `
            )
            .join("")}
        </div>
      </section>
      <div class="monitoring-case-grid">
        ${renderMonitoringCases("적중", cases.hitDates, "적중 사례 없음")}
        ${renderMonitoringCases("오경보", cases.falsePositiveDates, "오경보 없음")}
        ${renderMonitoringCases("미탐", cases.missedDates, "미탐 없음")}
      </div>
      <div class="monitoring-footnote">
        <strong>운영 판정 기준</strong>
        <span>최근 3개월 · 급락 3건 이상 · AUC/AP/Brier/기준모델 fold 승률 동시 확인</span>
        <span>표본 부족은 자동 강등하지 않고, 명확한 성능 저하만 연구용 전환 경고</span>
      </div>
    </div>
  `;
}

function renderModelMonitoringPage(mlRisk) {
  const monitoring = mlRisk?.monitoring;
  if (!monitoring?.tasks) {
    return `<div class="empty-state"><h3>모델 운영 모니터링 산출 대기</h3><span>다음 full 검증에서 OOS 실제 결과를 연결합니다</span></div>`;
  }
  return `
    <section class="research-page model-monitoring-page">
      <div class="section-heading research-page__heading">
        <div><span class="eyebrow">Model Operations</span><h2>ML 최근 성능 검증</h2>${renderNarrativeList(["누적 성능과 최근 성능 분리", "실제 급락·경보·오경보·미탐 확인", "기준모델 대비 성능 저하 감시"], "narrative-list--compact")}</div>
        <span class="status-pill status-pill--${monitoring.status?.tone ?? "watch"}">${monitoring.status?.label ?? "산출 대기"}</span>
      </div>
      <div class="monitoring-task-toggle" role="group" aria-label="급락 모델 선택">
        ${Object.entries(monitoringTaskView)
          .map(([taskId, view], index) => `<button type="button" class="${index === 0 ? "is-active" : ""}" data-monitoring-task="${taskId}" aria-pressed="${index === 0 ? "true" : "false"}">${view.label}</button>`)
          .join("")}
      </div>
      ${Object.entries(monitoring.tasks)
        .map(([taskId, task], index) => renderMonitoringTask(taskId, task, mlRisk.walkForwardSeries, index === 0))
        .join("")}
    </section>
  `;
}

function nearestDatedRow(rows, targetDate) {
  return [...(rows ?? [])]
    .filter((row) => row.date && row.date <= targetDate)
    .sort((left, right) => left.date.localeCompare(right.date))
    .at(-1) ?? null;
}

function replayDates(timeseries, mlRisk) {
  return [...new Set([
    ...Object.values(timeseries?.series ?? {}).flat().map((row) => row.date),
    ...(mlRisk?.series ?? []).map((row) => row.date)
  ].filter(Boolean))].sort();
}

function marketReplaySnapshot(market, timeseries, targetDate) {
  const indicators = (market?.indicators ?? [])
    .map((indicator) => {
      const row = nearestDatedRow(timeseries?.series?.[indicator.id], targetDate);
      const value = Number(row?.value);
      return Number.isFinite(value) ? { ...indicator, value, observedDate: row.date } : null;
    })
    .filter(Boolean);
  const scored = indicators.filter(isScoredIndicator);
  const totalWeight = scored.reduce((sum, indicator) => sum + Number(indicator.weight ?? 0), 0);
  const score = totalWeight
    ? scored.reduce((sum, indicator) => sum + indicator.value * Number(indicator.weight ?? 0), 0) / totalWeight
    : null;
  return { date: targetDate, score, indicators };
}

function replaySnapshot(data, timeseries, mlRisk, elsRisk, hmmRegime, targetDate) {
  const market = data.sections.find((section) => section.id === "market");
  const marketSnapshot = marketReplaySnapshot(market, timeseries, targetDate);
  const ml = nearestDatedRow(mlRisk?.series, targetDate);
  const hmm = (hmmRegime?.indices ?? []).map((item) => ({ ...item, point: nearestDatedRow(item.series, targetDate) }));
  const els = (elsRisk?.indices ?? []).map((item) => ({ ...item, point: nearestDatedRow(item.series, targetDate) }));
  const elsMap = (elsRisk?.issuanceHedgeMap?.items ?? [])
    .filter((item) => item.assetType === "index")
    .map((item) => ({ ...item, point: nearestDatedRow(item.trajectory, targetDate) }));
  return { targetDate, market: marketSnapshot, ml, hmm, els, elsMap };
}

function signedDifference(current, previous, suffix = "") {
  const value = Number(current) - Number(previous);
  if (!Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}${suffix}`;
}

function renderReplayElsMap(current, previous) {
  const items = current.elsMap.filter((item) => item.point);
  if (!items.length) {
    return `<div class="replay-els-map replay-els-map--empty"><strong>ELS 맵 복원 대기</strong><span>선택일의 발행기회·헤지부담 궤적 없음</span></div>`;
  }
  const previousById = new Map(previous.elsMap.map((item) => [item.id, item.point]));
  const colors = ["var(--blue)", "var(--teal)", "var(--amber)", "var(--red)", "var(--green)"];
  const pointMarkup = items
    .map((item, index) => {
      const currentPoint = item.point;
      const previousPoint = previousById.get(item.id);
      const currentX = 42 + clampScore(currentPoint.opportunityScore) * 4.7;
      const currentY = 232 - clampScore(currentPoint.hedgeBurdenScore) * 1.9;
      if (!previousPoint) {
        return `<circle cx="${currentX.toFixed(1)}" cy="${currentY.toFixed(1)}" r="6" fill="${colors[index % colors.length]}"></circle><text x="${(currentX + 9).toFixed(1)}" y="${(currentY + 4).toFixed(1)}">${item.label}</text>`;
      }
      const previousX = 42 + clampScore(previousPoint.opportunityScore) * 4.7;
      const previousY = 232 - clampScore(previousPoint.hedgeBurdenScore) * 1.9;
      return `
        <line class="replay-els-map__path" x1="${previousX.toFixed(1)}" y1="${previousY.toFixed(1)}" x2="${currentX.toFixed(1)}" y2="${currentY.toFixed(1)}" stroke="${colors[index % colors.length]}"></line>
        <circle class="replay-els-map__before" cx="${previousX.toFixed(1)}" cy="${previousY.toFixed(1)}" r="5" stroke="${colors[index % colors.length]}"></circle>
        <circle cx="${currentX.toFixed(1)}" cy="${currentY.toFixed(1)}" r="6" fill="${colors[index % colors.length]}"></circle>
        <text x="${(currentX + 9).toFixed(1)}" y="${(currentY + 4).toFixed(1)}">${item.label}</text>
      `;
    })
    .join("");
  return `
    <section class="replay-els-map">
      <div><span class="eyebrow">ELS Positioning</span><h3>발행기회·헤지부담 이동</h3><small>빈 원: 기준 · 채운 원: 비교</small></div>
      <svg viewBox="0 0 540 260" role="img" aria-label="두 날짜의 ELS 기초지수 발행기회와 헤지부담 비교">
        <rect class="replay-els-map__quadrant" x="42" y="42" width="235" height="95"></rect>
        <rect class="replay-els-map__quadrant replay-els-map__quadrant--alternate" x="277" y="137" width="235" height="95"></rect>
        <path class="replay-els-map__axis" d="M 42 42 L 42 232 L 512 232 M 277 42 L 277 232 M 42 137 L 512 137"></path>
        <text class="replay-els-map__axis-label" x="277" y="254" text-anchor="middle">발행기회 →</text>
        <text class="replay-els-map__axis-label" x="14" y="137" text-anchor="middle" transform="rotate(-90 14 137)">헤지부담 →</text>
        ${pointMarkup}
      </svg>
    </section>
  `;
}

function renderReplayContent(data, timeseries, mlRisk, elsRisk, hmmRegime, currentDate, previousDate) {
  const current = replaySnapshot(data, timeseries, mlRisk, elsRisk, hmmRegime, currentDate);
  const previous = replaySnapshot(data, timeseries, mlRisk, elsRisk, hmmRegime, previousDate);
  const currentWorstEls = [...current.els].filter((item) => item.point).sort((a, b) => Number(b.point.score) - Number(a.point.score))[0];
  const previousWorstEls = previous.els.find((item) => item.id === currentWorstEls?.id);
  const currentRiskOff = current.hmm.filter((item) => item.point?.regime === "위험회피").length;
  const previousRiskOff = previous.hmm.filter((item) => item.point?.regime === "위험회피").length;
  const previousByIndicator = new Map(previous.market.indicators.map((item) => [item.id, item]));
  const movers = current.market.indicators
    .map((item) => ({ ...item, change: item.value - Number(previousByIndicator.get(item.id)?.value) }))
    .filter((item) => Number.isFinite(item.change))
    .sort((left, right) => Math.abs(right.change) - Math.abs(left.change))
    .slice(0, 8);
  return `
    <div class="replay-content">
      <div class="replay-date-banner"><span>기준 ${previousDate}</span><strong>→</strong><span>비교 ${currentDate}</span></div>
      <section class="replay-summary-grid">
        <article><span>시장 종합점수</span><strong>${formatScore(current.market.score)}</strong><small>${signedDifference(current.market.score, previous.market.score, "점")}</small></article>
        <article><span>ML 5D -5%</span><strong>${Number(current.ml?.crash5d5pctProbabilityPct ?? 0).toFixed(1)}%</strong><small>${signedDifference(current.ml?.crash5d5pctProbabilityPct, previous.ml?.crash5d5pctProbabilityPct, "%p")}</small></article>
        <article><span>ELS 최고 부담</span><strong>${currentWorstEls?.label ?? "-"} ${Number(currentWorstEls?.point?.score ?? 0).toFixed(1)}</strong><small>${signedDifference(currentWorstEls?.point?.score, previousWorstEls?.point?.score, "점")}</small></article>
        <article><span>HMM 위험회피</span><strong>${currentRiskOff}/${current.hmm.length}</strong><small>${signedDifference(currentRiskOff, previousRiskOff, "개")}</small></article>
      </section>
      ${renderReplayElsMap(current, previous)}
      <section class="replay-comparison-grid">
        <div class="replay-table">
          <div><span class="eyebrow">First Movers</span><h3>먼저 움직인 시장지표</h3></div>
          ${movers.map((item) => `<p><span>${item.name}</span><strong class="${item.change > 0 ? "is-worse" : "is-better"}">${item.change > 0 ? "+" : ""}${item.change.toFixed(1)}점</strong></p>`).join("")}
        </div>
        <div class="replay-table">
          <div><span class="eyebrow">HMM</span><h3>지수별 레짐</h3></div>
          ${current.hmm.map((item) => {
            const before = previous.hmm.find((candidate) => candidate.id === item.id);
            return `<p><span>${item.label}</span><strong>${before?.point?.regime ?? "-"} → ${item.point?.regime ?? "-"}</strong></p>`;
          }).join("")}
        </div>
        <div class="replay-table">
          <div><span class="eyebrow">ELS</span><h3>기초자산 부담점수</h3></div>
          ${current.els.map((item) => {
            const before = previous.els.find((candidate) => candidate.id === item.id);
            return `<p><span>${item.label}</span><strong>${Number(before?.point?.score ?? 0).toFixed(1)} → ${Number(item.point?.score ?? 0).toFixed(1)}</strong></p>`;
          }).join("")}
        </div>
      </section>
    </div>
  `;
}

function renderReplayPage(data, timeseries, mlRisk, elsRisk, hmmRegime, stressEpisodes) {
  const dates = replayDates(timeseries, mlRisk);
  if (!dates.length) return `<div class="empty-state"><h3>재생 가능한 날짜 없음</h3></div>`;
  const currentDate = dates.at(-1);
  const previousDate = dates[Math.max(0, dates.length - 6)];
  const recentEpisodes = (stressEpisodes?.episodes ?? []).filter(
    (episode) => episode.startDate >= dates[0] && episode.peakDate <= dates.at(-1)
  );
  const alertThreshold = Number(mlRisk?.monitoring?.tasks?.crash5d5pct?.cases?.alertThresholdPct ?? 50);
  const oosRows = (mlRisk?.walkForwardSeries ?? []).filter((row) => row.date >= dates[0] && row.date <= dates.at(-1));
  const episodePresets = recentEpisodes.map((episode) => {
    const firstAlert = oosRows.find(
      (row) =>
        row.date >= episode.startDate &&
        row.date <= episode.endDate &&
        Number(row.crash5d5pctProbabilityPct) >= alertThreshold
    );
    return { ...episode, replayStartDate: firstAlert?.date ?? episode.startDate, hasFirstAlert: Boolean(firstAlert) };
  });
  const worstForwardRow = [...oosRows]
    .filter((row) => Number.isFinite(Number(row.forwardMinReturn5dPct)))
    .sort((left, right) => Number(left.forwardMinReturn5dPct) - Number(right.forwardMinReturn5dPct))[0];
  return `
    <section class="research-page replay-page">
      <div class="section-heading research-page__heading">
        <div><span class="eyebrow">Historical Replay</span><h2>날짜 재생·비교</h2>${renderNarrativeList(["같은 날짜의 시장지표 · ML · HMM · ELS 동시 복원", "저장된 시계열 범위 안에서 직전 가용값 사용", "미래값 없이 당시 관측 상태 비교"], "narrative-list--compact")}</div>
        <span class="status-pill status-pill--watch">${dates[0]} ~ ${dates.at(-1)}</span>
      </div>
      <div class="replay-controls">
        <label><span>기준 시점</span><input type="date" data-replay-previous min="${dates[0]}" max="${dates.at(-1)}" value="${previousDate}"></label>
        <label><span>비교 시점</span><input type="date" data-replay-current min="${dates[0]}" max="${dates.at(-1)}" value="${currentDate}"></label>
        <button type="button" data-replay-preset="week">현재 vs 1주 전</button>
        ${worstForwardRow ? `<button type="button" data-replay-start="${worstForwardRow.date}" data-replay-end="${worstForwardRow.resultKnownThroughDate}">급락 직전 vs 이후</button>` : ""}
      </div>
      <div class="replay-episodes">
        <span>스트레스 에피소드</span>
        ${episodePresets.map((episode) => `<button type="button" data-replay-start="${episode.replayStartDate}" data-replay-end="${episode.peakDate}">${episode.label}${episode.hasFirstAlert ? " · 첫 경보" : ""}</button>`).join("") || `<small>현재 저장구간과 겹치는 에피소드 없음</small>`}
      </div>
      <div data-replay-content>${renderReplayContent(data, timeseries, mlRisk, elsRisk, hmmRegime, currentDate, previousDate)}</div>
    </section>
  `;
}

function renderDashboard(
  rawData,
  timeseries,
  mlRisk,
  elsRisk,
  hmmRegime,
  pipelineStatus,
  sourceSnapshot,
  dataQuality,
  stressEpisodes,
  breadthData
) {
  interactiveChartRegistry.clear();
  interactiveChartSequence = 0;
  const data = evaluateDashboard(rawData);
  const visibleBaseTabs = data.tabs.filter(
    (tab) => !["credit", "liquidity"].includes(tab.id)
  );
  const allDashboardTabs = dashboardTabsWithElsTool(visibleBaseTabs);
  const dashboardTabs = IS_OFFLINE_SNAPSHOT
    ? allDashboardTabs.filter((tab) => !OFFLINE_ADMIN_TAB_IDS.has(tab.id))
    : allDashboardTabs;
  const enabledTabs = dashboardTabs.filter((tab) => tab.enabled);
  const visibleSections = data.sections.filter((section) =>
    enabledTabs.some((tab) => tab.id === section.id)
  );
  const requestedTab = decodeURIComponent(window.location.hash.replace(/^#/, ""));
  const activeTab = enabledTabs.some((tab) => tab.id === requestedTab) ? requestedTab : "summary";
  const indicatorSortStates = Object.fromEntries(
    visibleSections.map((section) => [
      section.id,
      { key: "score", direction: "desc", group: null }
    ])
  );
  const panelState = (id) => ({
    className: id === activeTab ? "is-active" : "",
    hidden: id === activeTab ? "" : "hidden"
  });
  const summaryState = panelState("summary");
  const sentimentState = panelState("sentiment");
  const breadthState = panelState("market-breadth");
  const operationsState = panelState("operations");
  const modelMonitoringState = panelState("model-monitoring");
  const replayState = panelState("replay");
  const elsIssuanceState = panelState("els-issuance");

  app.innerHTML = `
    <header class="hero">
      <div class="hero__content">
        <span class="eyebrow">Risk Monitoring</span>
        <h1>${data.metadata.title}</h1>
        ${renderNarrativeList(data.metadata.subtitle, "narrative-list--hero")}
      </div>
      <div class="hero__aside">
        ${IS_OFFLINE_SNAPSHOT ? `<span class="hero__snapshot-badge">오프라인 스냅샷</span>` : ""}
        <span>기준일</span>
        <strong>${data.metadata.asOf}</strong>
        <a
          class="hero__download"
          href="./reports/market-risk-dashboard-offline.html?v=${ASSET_VERSION}"
          download="${offlineSnapshotFilename(data)}"
          title="현재 데이터 오프라인 HTML 다운로드"
        >
          <span aria-hidden="true">↓</span>
          <span>오프라인 HTML</span>
        </a>
        <div class="hero__timestamp">
          <small>${data.metadata.generatedAt}</small>
          <a class="snow-lab-trigger" href="./snow-lab.html" aria-label="Field Lab 열기" title="Field Lab">❄</a>
        </div>
      </div>
    </header>

    ${IS_OFFLINE_SNAPSHOT ? "" : renderOperationStatusStrip(pipelineStatus)}

    <nav class="tabs" aria-label="리스크 대시보드 탭">
      <div class="tabs__items" role="tablist" aria-label="리스크 화면 선택">
        ${dashboardTabs
          .map((tab) => {
            const selected = tab.id === activeTab;
            return `
              <button
                class="tab-button ${selected ? "is-active" : ""}"
                id="tab-${tab.id}"
                data-tab="${tab.id}"
                role="tab"
                aria-controls="panel-${tab.id}"
                aria-selected="${selected ? "true" : "false"}"
                aria-disabled="${tab.enabled ? "false" : "true"}"
                tabindex="${selected ? "0" : "-1"}"
                ${tab.enabled ? "" : "disabled"}
              >
                ${tab.label}
              </button>
            `;
          })
          .join("")}
      </div>
      <button class="theme-toggle" type="button" data-theme-toggle aria-label="다크 모드로 전환" title="다크 모드">
        ◐
      </button>
    </nav>

    <div class="panel-stack">
      <section
        class="tab-panel ${summaryState.className}"
        id="panel-summary"
        data-panel="summary"
        role="tabpanel"
        aria-labelledby="tab-summary"
        tabindex="0"
        ${summaryState.hidden}
      >
        ${renderSummary(data, timeseries, mlRisk, elsRisk, hmmRegime, breadthData)}
      </section>
      <section
        class="tab-panel ${sentimentState.className}"
        id="panel-sentiment"
        data-panel="sentiment"
        role="tabpanel"
        aria-labelledby="tab-sentiment"
        tabindex="0"
        ${sentimentState.hidden}
      >
        ${renderSentimentPage(data, timeseries, mlRisk, elsRisk, hmmRegime)}
      </section>
      <section
        class="tab-panel ${breadthState.className}"
        id="panel-market-breadth"
        data-panel="market-breadth"
        role="tabpanel"
        aria-labelledby="tab-market-breadth"
        tabindex="0"
        ${breadthState.hidden}
      >
        ${renderMarketBreadthPage(breadthData)}
      </section>
      ${
        IS_OFFLINE_SNAPSHOT
          ? ""
          : `<section
               class="tab-panel ${operationsState.className}"
               id="panel-operations"
               data-panel="operations"
               role="tabpanel"
               aria-labelledby="tab-operations"
               tabindex="0"
               ${operationsState.hidden}
             >
               ${renderOperationsPage(pipelineStatus)}
             </section>
             <section
               class="tab-panel ${modelMonitoringState.className}"
               id="panel-model-monitoring"
               data-panel="model-monitoring"
               role="tabpanel"
               aria-labelledby="tab-model-monitoring"
               tabindex="0"
               ${modelMonitoringState.hidden}
             >
               ${renderModelMonitoringPage(mlRisk)}
             </section>`
      }
      <section
        class="tab-panel ${replayState.className}"
        id="panel-replay"
        data-panel="replay"
        role="tabpanel"
        aria-labelledby="tab-replay"
        tabindex="0"
        ${replayState.hidden}
      >
        ${renderReplayPage(data, timeseries, mlRisk, elsRisk, hmmRegime, stressEpisodes)}
      </section>
      <section
        class="tab-panel ${elsIssuanceState.className}"
        id="panel-els-issuance"
        data-panel="els-issuance"
        role="tabpanel"
        aria-labelledby="tab-els-issuance"
        tabindex="0"
        ${elsIssuanceState.hidden}
      >
        ${renderElsIssuanceHedgePage(elsRisk)}
      </section>
          ${visibleSections
            .map((section) => renderSection(section, timeseries, null, null, null, activeTab, { snapshot: sourceSnapshot, quality: dataQuality }))
            .join("")}
    </div>
  `;
  initializeInteractiveCharts(app);
  app.querySelectorAll("[data-observation-detail-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const detail = document.getElementById(button.getAttribute("aria-controls"));
      if (!detail) return;
      const expanded = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", expanded ? "false" : "true");
      detail.hidden = expanded;
      button.closest(".observation-journal__item")?.classList.toggle("is-expanded", !expanded);
    });
  });

  app.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-source-detail-toggle]");
    if (!button || !app.contains(button)) return;
    const detail = document.getElementById(button.getAttribute("aria-controls"));
    if (!detail) return;
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", expanded ? "false" : "true");
    detail.hidden = expanded;
    button.textContent = expanded ? "원천·산식" : "상세 닫기";
  });

  app.querySelectorAll("[data-monitoring-task]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.monitoringTask;
      app.querySelectorAll("[data-monitoring-task]").forEach((option) => {
        const selected = option === button;
        option.classList.toggle("is-active", selected);
        option.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      app.querySelectorAll("[data-monitoring-task-panel]").forEach((panel) => {
        const selected = panel.dataset.monitoringTaskPanel === target;
        panel.classList.toggle("is-active", selected);
        panel.hidden = !selected;
      });
      initializeInteractiveCharts(app.querySelector(`[data-monitoring-task-panel="${target}"]`) ?? app);
    });
  });

  const replayPrevious = app.querySelector("[data-replay-previous]");
  const replayCurrent = app.querySelector("[data-replay-current]");
  const replayContent = app.querySelector("[data-replay-content]");
  const updateReplay = () => {
    if (!replayPrevious || !replayCurrent || !replayContent) return;
    replayContent.innerHTML = renderReplayContent(
      data,
      timeseries,
      mlRisk,
      elsRisk,
      hmmRegime,
      replayCurrent.value,
      replayPrevious.value
    );
  };
  replayPrevious?.addEventListener("change", updateReplay);
  replayCurrent?.addEventListener("change", updateReplay);
  app.querySelector('[data-replay-preset="week"]')?.addEventListener("click", () => {
    const dates = replayDates(timeseries, mlRisk);
    if (!dates.length || !replayPrevious || !replayCurrent) return;
    replayCurrent.value = dates.at(-1);
    replayPrevious.value = dates[Math.max(0, dates.length - 6)];
    updateReplay();
  });
  app.querySelectorAll("[data-replay-start][data-replay-end]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!replayPrevious || !replayCurrent) return;
      replayPrevious.value = button.dataset.replayStart;
      replayCurrent.value = button.dataset.replayEnd;
      updateReplay();
    });
  });

  let marketDetailsStatus = "idle";
  const hydrateMarketDetails = async () => {
    if (marketDetailsStatus !== "idle") return;
    marketDetailsStatus = "loading";
    const directionSlot = app.querySelector("[data-market-direction-slot]");
    const historySlot = app.querySelector("[data-market-history-slot]");
    const loadingMarkup = `<div class="deferred-panel"><span class="loading-dot" aria-hidden="true"></span>상세 데이터를 불러오는 중</div>`;
    if (directionSlot) directionSlot.innerHTML = loadingMarkup;
    if (historySlot) historySlot.innerHTML = loadingMarkup;

    const [marketIndexes, backtest, stressEpisodes] = await Promise.all([
      loadJson("./data/naver-marketindex-history.json"),
      loadJson("./data/market-risk-backtest.json"),
      loadJson("./data/market-stress-episodes.json")
    ]);

    if (directionSlot) {
      directionSlot.innerHTML =
        renderMarketIndexTrendPanel(marketIndexes) ||
        `<div class="deferred-panel deferred-panel--error">시장 방향성 데이터를 확인하지 못했습니다</div>`;
      initializeInteractiveCharts(directionSlot);
    }
    if (historySlot) {
      historySlot.innerHTML =
        `${renderBacktestPanel(backtest)}${renderStressEpisodesPanel(stressEpisodes)}` ||
        `<div class="deferred-panel deferred-panel--error">백테스트 데이터를 확인하지 못했습니다</div>`;
    }
    marketDetailsStatus = "loaded";
  };

  const activateTab = (target, { focus = false, updateHash = true } = {}) => {
    if (!enabledTabs.some((tab) => tab.id === target)) return;
    let selectedTab = null;
    app.querySelectorAll('[role="tab"][data-tab]').forEach((tab) => {
      const selected = tab.dataset.tab === target;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
      if (selected) selectedTab = tab;
    });
    app.querySelectorAll(".tab-panel[data-panel]").forEach((panel) => {
      const selected = panel.dataset.panel === target;
      panel.classList.toggle("is-active", selected);
      panel.hidden = !selected;
    });
    if (selectedTab) {
      if (focus) selectedTab.focus();
      selectedTab.scrollIntoView({
        behavior: focus && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto",
        block: "nearest",
        inline: "nearest"
      });
    }
    if (target === "market") void hydrateMarketDetails();
    if (updateHash) history.replaceState(null, "", `#${target}`);
  };

  app.querySelectorAll('[role="tab"][data-tab]').forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });

  const tabList = app.querySelector('[role="tablist"]');
  tabList?.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const buttons = [...tabList.querySelectorAll('[role="tab"]:not(:disabled)')];
    const currentIndex = buttons.indexOf(document.activeElement);
    if (currentIndex < 0) return;
    event.preventDefault();
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? buttons.length - 1
          : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
    activateTab(buttons[nextIndex].dataset.tab, { focus: true });
  });

  app.querySelectorAll("[data-open-tab]").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.openTab, { focus: true }));
  });

  window.addEventListener("hashchange", () => {
    const target = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    activateTab(target, { updateHash: false });
  });

  app.querySelectorAll("[data-attribution-window]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.attributionWindow;
      app.querySelectorAll("[data-attribution-window]").forEach((option) => {
        const selected = option === button;
        option.classList.toggle("is-active", selected);
        option.setAttribute("aria-pressed", selected ? "true" : "false");
      });
      app.querySelectorAll("[data-attribution-panel]").forEach((panel) => {
        const selected = panel.dataset.attributionPanel === target;
        panel.classList.toggle("is-active", selected);
        panel.hidden = !selected;
        panel.setAttribute("aria-hidden", selected ? "false" : "true");
      });
    });
  });

  const updateIndicatorGrid = (sectionId) => {
    const section = data.sections.find((item) => item.id === sectionId);
    const grid = app.querySelector(`[data-indicator-grid="${sectionId}"]`);
    if (!section || !grid) return;
    const state = indicatorSortStates[sectionId] ?? { key: "score", direction: "desc", group: null };
    const indicators = sortedIndicators(section, timeseries, state.key, state.direction).filter(
      (indicator) => !state.group || indicator.group === state.group
    );
    grid.innerHTML = indicators
      .map((indicator) => renderIndicator(indicator, section.model.thresholds, timeseries, { snapshot: sourceSnapshot, quality: dataQuality, model: section.model }))
      .join("");

    const definition = state.group ? riskGroupDefinitions[state.group] : null;
    const status = app.querySelector(`[data-indicator-filter-status="${sectionId}"]`);
    if (status) status.textContent = definition ? `${definition.label} ${indicators.length}개` : `전체 ${indicators.length}개`;
    const reset = app.querySelector(`[data-indicator-filter-reset="${sectionId}"]`);
    if (reset) reset.hidden = !state.group;

    app.querySelectorAll(`[data-group-filter][data-section-id="${sectionId}"]`).forEach((button) => {
      const selected = button.dataset.groupFilter === state.group;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
      button.textContent = selected ? "선택 해제" : "이 그룹만 보기";
      button.closest("[data-risk-group-card]")?.classList.toggle("is-filtered", selected);
    });
  };

  app.querySelectorAll("[data-indicator-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const sectionId = button.dataset.sectionId;
      const sortKey = button.dataset.indicatorSort;
      const section = data.sections.find((item) => item.id === sectionId);
      const grid = app.querySelector(`[data-indicator-grid="${sectionId}"]`);
      if (!section || !grid) return;
      const current = indicatorSortStates[sectionId] ?? { key: "score", direction: "desc", group: null };
      const nextDirection = sortKey !== "score" && current.key === sortKey && current.direction === "desc" ? "asc" : "desc";
      indicatorSortStates[sectionId] = { ...current, key: sortKey, direction: nextDirection };

      app
        .querySelectorAll(`[data-indicator-sort][data-section-id="${sectionId}"]`)
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

      updateIndicatorGrid(sectionId);
    });
  });

  app.querySelectorAll("[data-group-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const sectionId = button.dataset.sectionId;
      const current = indicatorSortStates[sectionId];
      if (!current) return;
      current.group = current.group === button.dataset.groupFilter ? null : button.dataset.groupFilter;
      updateIndicatorGrid(sectionId);
      app.querySelector(`[data-indicator-grid="${sectionId}"]`)?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start"
      });
    });
  });

  app.querySelectorAll("[data-indicator-filter-reset]").forEach((button) => {
    button.addEventListener("click", () => {
      const sectionId = button.dataset.indicatorFilterReset;
      if (!indicatorSortStates[sectionId]) return;
      indicatorSortStates[sectionId].group = null;
      updateIndicatorGrid(sectionId);
    });
  });

  app.querySelectorAll("[data-els-map]").forEach((mapElement) => {
    const stockToggle = mapElement.querySelector("[data-els-stock-toggle]");
    const syncStockVisibility = () => {
      const showStocks = Boolean(stockToggle?.checked);
      mapElement.classList.toggle("is-showing-single-stocks", showStocks);
      stockToggle?.closest(".els-stock-visibility-toggle")?.classList.toggle("is-active", showStocks);
      mapElement.querySelectorAll("[data-els-stock-state]").forEach((state) => {
        state.textContent = showStocks ? "ON" : "OFF";
      });
      mapElement.querySelectorAll("[data-els-stock-dependent]").forEach((item) => {
        item.hidden = !showStocks;
      });
    };
    stockToggle?.addEventListener("change", syncStockVisibility);
    syncStockVisibility();

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
  activateTab(activeTab, { updateHash: false });
}

let activePublicationRunId = "";

function validatePublicationBundle(manifest, entries) {
  if (!manifest) return;
  const runId = String(manifest.runId ?? "");
  if (manifest.status !== "ready" || !runId) {
    throw new Error("게시 manifest가 준비 완료 상태가 아닙니다.");
  }
  const mismatches = entries
    .filter(({ payload }) => payload)
    .filter(({ payload }) => payload?.publication?.runId !== runId)
    .map(({ path }) => path);
  if (mismatches.length) {
    throw new Error(`게시 데이터 실행번호가 일치하지 않습니다: ${mismatches.join(", ")}`);
  }
  activePublicationRunId = runId;
}

async function loadJson(path, required = false, allowLegacyMissing = false) {
  try {
    const response = await fetch(versioned(path), { cache: "no-store" });
    if (allowLegacyMissing && response.status === 404) return null;
    if (!response.ok) throw new Error(`${path} 응답 오류: ${response.status}`);
    const payload = await response.json();
    if (
      activePublicationRunId &&
      !path.includes("publication-manifest.json") &&
      payload?.publication?.runId !== activePublicationRunId
    ) {
      throw new Error(`${path}가 현재 게시 실행번호와 일치하지 않습니다.`);
    }
    return payload;
  } catch (error) {
    if (required || allowLegacyMissing) throw error;
    console.warn(`선택 데이터 로드 실패: ${path}`, error);
    return null;
  }
}

Promise.all([
  loadJson("./data/publication-manifest.json", false, true),
  loadJson("./data/risk-dashboard.json", true),
  loadJson("./data/market-risk-timeseries.json"),
  loadJson("./data/ml-risk-signal.json"),
  loadJson("./data/els-index-risk.json"),
  loadJson("./data/hmm-regime.json"),
  loadJson("./data/pipeline-status.json"),
  loadJson("./data/market-risk-snapshot.json"),
  loadJson("./data/data-quality.json"),
  loadJson("./data/market-stress-episodes.json"),
  loadJson("./data/kospi-breadth.json")
])
  .then(([publicationManifest, dashboard, timeseries, mlRisk, elsRisk, hmmRegime, pipelineStatus, sourceSnapshot, dataQuality, stressEpisodes, breadthData]) => {
    validatePublicationBundle(publicationManifest, [
      { path: "risk-dashboard.json", payload: dashboard },
      { path: "market-risk-timeseries.json", payload: timeseries },
      { path: "ml-risk-signal.json", payload: mlRisk },
      { path: "els-index-risk.json", payload: elsRisk },
      { path: "hmm-regime.json", payload: hmmRegime },
      { path: "pipeline-status.json", payload: pipelineStatus },
      { path: "market-risk-snapshot.json", payload: sourceSnapshot },
      { path: "data-quality.json", payload: dataQuality },
      { path: "market-stress-episodes.json", payload: stressEpisodes },
      { path: "kospi-breadth.json", payload: breadthData }
    ]);
    return renderDashboard(
      dashboard,
      timeseries,
      mlRisk,
      elsRisk,
      hmmRegime,
      pipelineStatus,
      sourceSnapshot,
      dataQuality,
      stressEpisodes,
      breadthData
    );
  })
  .catch((error) => {
    app.innerHTML = `
      <div class="loading-panel loading-panel--error">
        <strong>대시보드를 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </div>
    `;
  });
