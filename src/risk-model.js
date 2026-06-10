export function clampScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(100, Math.max(0, number));
}

export function pickLevel(score, thresholds) {
  const safeScore = clampScore(score);
  return (
    thresholds.find((threshold) => safeScore >= threshold.min && safeScore < threshold.max) ??
    thresholds[thresholds.length - 1]
  );
}

export function weightedScore(indicators) {
  const activeIndicators = indicators.filter((indicator) => Number(indicator.weight) > 0);
  const weightTotal = activeIndicators.reduce((sum, indicator) => sum + Number(indicator.weight), 0);

  if (!activeIndicators.length || weightTotal <= 0) {
    return 0;
  }

  const weightedTotal = activeIndicators.reduce((sum, indicator) => {
    return sum + clampScore(indicator.value) * Number(indicator.weight);
  }, 0);

  return Math.round((weightedTotal / weightTotal) * 10) / 10;
}

export function evaluateSection(section) {
  const score = weightedScore(section.indicators ?? []);
  const level = pickLevel(score, section.model.thresholds);
  const highRiskIndicators = (section.indicators ?? []).filter((indicator) => clampScore(indicator.value) >= 75);

  return {
    ...section,
    score,
    level,
    highRiskCount: highRiskIndicators.length,
    topIndicators: [...(section.indicators ?? [])]
      .sort((a, b) => clampScore(b.value) - clampScore(a.value))
      .slice(0, 3)
  };
}

export function evaluateDashboard(data) {
  const evaluatedSections = data.sections.map(evaluateSection);
  const activeSections = evaluatedSections.filter((section) => section.status === "active");
  const dashboardScore = weightedScore(
    activeSections.map((section) => ({
      value: section.score,
      weight: 1
    }))
  );
  const defaultThresholds = activeSections[0]?.model.thresholds ?? [];
  const dashboardLevel = defaultThresholds.length
    ? pickLevel(dashboardScore, defaultThresholds)
    : { label: "준비중", tone: "muted" };

  return {
    ...data,
    sections: evaluatedSections,
    summary: {
      score: dashboardScore,
      level: dashboardLevel,
      activeCount: activeSections.length,
      plannedCount: evaluatedSections.filter((section) => section.status !== "active").length,
      topIndicators: activeSections.flatMap((section) => section.topIndicators).slice(0, 4)
    }
  };
}
