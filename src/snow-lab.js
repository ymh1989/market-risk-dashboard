const root = document.querySelector("[data-snow-lab]");
const stage = root.querySelector("[data-lab-stage]");
const fluidCanvas = root.querySelector("[data-fluid-canvas]");
const fieldCanvas = root.querySelector("[data-field-canvas]");
const title = root.querySelector("[data-lab-title]");
const eyebrow = root.querySelector("[data-lab-eyebrow]");
const pauseButton = root.querySelector("[data-snow-pause]");
const resetButton = root.querySelector("[data-snow-reset]");
const statusText = root.querySelector("[data-snow-status]");
const profileText = root.querySelector("[data-snow-profile]");
const modeButtons = [...root.querySelectorAll("[data-mode-select]")];
const qualityButtons = [...root.querySelectorAll("[data-quality]")];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const parameters = new URLSearchParams(window.location.search);

const requestedMode = parameters.get("mode") === "wave" ? "wave" : "snow";
const isWaveMode = requestedMode === "wave";

const qualityProfiles = {
  high: {
    simResolution: 192,
    dyeResolution: 1024,
    pressureIterations: 28,
    bloom: true,
    sunrays: true,
    particles: 1300,
    maxDpr: 1.8
  },
  balanced: {
    simResolution: 128,
    dyeResolution: 512,
    pressureIterations: 22,
    bloom: true,
    sunrays: false,
    particles: 850,
    maxDpr: 1.5
  },
  eco: {
    simResolution: 64,
    dyeResolution: 256,
    pressureIterations: 14,
    bloom: false,
    sunrays: false,
    particles: 420,
    maxDpr: 1.25
  }
};

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function autoProfileName() {
  const cores = Number(navigator.hardwareConcurrency || 4);
  const memory = Number(navigator.deviceMemory || 0);
  const compact = window.matchMedia("(max-width: 760px)").matches;
  const lowMemory = memory > 0 && memory <= 4;
  const highMemory = memory === 0 || memory >= 8;
  if (compact || cores <= 4 || lowMemory) return "eco";
  if (cores >= 10 && highMemory && window.innerWidth >= 1200) return "high";
  return "balanced";
}

const qualityParameter = parameters.get("quality") || "auto";
const requestedQuality = ["auto", "high", "eco"].includes(qualityParameter) ? qualityParameter : "auto";
const profileName = requestedQuality === "auto" ? autoProfileName() : requestedQuality;
const profile = qualityProfiles[profileName] || qualityProfiles.balanced;
const context = fieldCanvas.getContext("2d", { alpha: true });

const state = {
  paused: reducedMotion.matches,
  hiddenSuspension: false,
  fluidReady: false,
  fluidPaused: false,
  ocean: null,
  oceanLoading: false,
  fallback: false,
  frameId: 0,
  lastFrame: performance.now(),
  width: 1,
  height: 1,
  dpr: 1,
  particles: [],
  pointer: {
    active: false,
    x: 0,
    y: 0,
    vx: 0,
    vy: 0,
    speed: 0,
    updatedAt: 0
  }
};

root.dataset.mode = requestedMode;
title.textContent = isWaveMode ? "Ocean Lab" : "Snow Lab";
eyebrow.textContent = isWaveMode ? "Gerstner Ocean Field" : "Navier–Stokes Field";
document.title = isWaveMode ? "Ocean Lab" : "Snow Lab";
stage.setAttribute(
  "aria-label",
  isWaveMode ? "왼쪽에서 오른쪽으로 진행하는 GPU 3D 바다 파도 시뮬레이션" : "GPU 유체장과 눈 입자 시뮬레이션"
);

modeButtons.forEach((button) => {
  const active = button.dataset.modeSelect === requestedMode;
  button.classList.toggle("is-active", active);
  button.setAttribute("aria-pressed", active ? "true" : "false");
  button.addEventListener("click", () => {
    const url = new URL(window.location.href);
    const mode = button.dataset.modeSelect;
    if (mode === "snow") url.searchParams.delete("mode");
    else url.searchParams.set("mode", mode);
    window.location.replace(url);
  });
});

qualityButtons.forEach((button) => {
  const active = button.dataset.quality === requestedQuality;
  button.classList.toggle("is-active", active);
  button.setAttribute("aria-pressed", active ? "true" : "false");
  button.addEventListener("click", () => {
    const url = new URL(window.location.href);
    const quality = button.dataset.quality;
    if (quality === "auto") url.searchParams.delete("quality");
    else url.searchParams.set("quality", quality);
    window.location.replace(url);
  });
});

function simulationReady() {
  return isWaveMode ? Boolean(state.ocean) : state.fluidReady;
}

function runningStatus() {
  if (state.fallback) return isWaveMode ? "3D 미지원 · 단순 파도" : "눈 입자 모드";
  if (state.oceanLoading) return "GPU 3D 해양 준비중";
  if (simulationReady()) return isWaveMode ? "GPU 3D 해양 실행중" : "GPU 유체장 실행중";
  return isWaveMode ? "GPU 3D 해양 준비중" : "GPU 유체장 준비중";
}

function qualityLabel() {
  if (requestedQuality === "high") return "고화질";
  if (requestedQuality === "eco") return "절전";
  if (profileName === "high") return "자동(고화질)";
  if (profileName === "eco") return "자동(절전)";
  return "자동(균형)";
}

function updateProfileText() {
  const detail = isWaveMode
    ? state.ocean?.detail || "3D 수면 준비중"
    : `유체 ${profile.simResolution} · 입자 ${state.particles.length.toLocaleString("ko-KR")}`;
  profileText.textContent = `${qualityLabel()} · ${detail}`;
}

function updatePauseButton() {
  pauseButton.textContent = state.paused ? "▶" : "Ⅱ";
  pauseButton.setAttribute("aria-label", state.paused ? "시뮬레이션 재생" : "시뮬레이션 일시정지");
  pauseButton.title = state.paused ? "재생" : "일시정지";
  root.classList.toggle("is-paused", state.paused);
  statusText.textContent = state.paused ? `${isWaveMode ? "3D 해양" : "유체장"} 일시정지` : runningStatus();
}

function setFluidPaused(paused) {
  if (isWaveMode || !state.fluidReady || state.fluidPaused === paused) return;
  window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP" }));
  state.fluidPaused = paused;
}

function snowFluidConfiguration() {
  return {
    TRIGGER: "hover",
    IMMEDIATE: true,
    AUTO: false,
    SIM_RESOLUTION: profile.simResolution,
    DYE_RESOLUTION: profile.dyeResolution,
    DENSITY_DISSIPATION: 0.97,
    VELOCITY_DISSIPATION: 0.28,
    PRESSURE: 0.8,
    PRESSURE_ITERATIONS: profile.pressureIterations,
    CURL: 24,
    SPLAT_RADIUS: 0.18,
    SPLAT_FORCE: 4200,
    SPLAT_COUNT: profileName === "eco" ? 4 : 7,
    SHADING: true,
    COLORFUL: true,
    COLOR_UPDATE_SPEED: 2.2,
    PAUSED: false,
    BACK_COLOR: { r: 5, g: 11, b: 13 },
    TRANSPARENT: false,
    BLOOM: profile.bloom,
    BLOOM_ITERATIONS: profileName === "high" ? 6 : 4,
    BLOOM_RESOLUTION: profileName === "high" ? 256 : 128,
    BLOOM_INTENSITY: 0.34,
    BLOOM_THRESHOLD: 0.72,
    BLOOM_SOFT_KNEE: 0.6,
    SUNRAYS: profile.sunrays,
    SUNRAYS_RESOLUTION: 128,
    SUNRAYS_WEIGHT: 0.32
  };
}

async function initializeSnowFluid() {
  if (state.fluidReady || state.fallback) return;
  try {
    const { default: WebGLFluid } = await import("./vendor/webgl-fluid.mjs");
    WebGLFluid(fluidCanvas, snowFluidConfiguration());
    state.fluidReady = true;
    statusText.textContent = runningStatus();
  } catch (error) {
    state.fallback = true;
    root.classList.add("is-fallback");
    statusText.textContent = runningStatus();
    console.warn("Snow Lab WebGL fallback:", error);
  }
}

async function initializeOcean() {
  if (state.ocean || state.oceanLoading || state.fallback) return;
  state.oceanLoading = true;
  statusText.textContent = runningStatus();
  try {
    const { createOceanLab } = await import("./ocean-lab.js");
    state.ocean = createOceanLab({
      canvas: fluidCanvas,
      stage,
      profileName,
      maxDpr: profile.maxDpr
    });
    state.ocean.setPaused(state.paused);
  } catch (error) {
    state.fallback = true;
    root.classList.add("is-fallback");
    root.dataset.renderError = error instanceof Error ? error.message : String(error);
    console.warn("Ocean Lab Three.js fallback:", error);
  } finally {
    state.oceanLoading = false;
    updateProfileText();
    statusText.textContent = runningStatus();
  }
}

function initializeSimulation() {
  return isWaveMode ? initializeOcean() : initializeSnowFluid();
}

function resetParticle(particle, initial = false) {
  particle.x = Math.random() * state.width;
  particle.y = initial ? Math.random() * state.height : -20 - Math.random() * state.height * 0.18;
  particle.depth = 0.25 + Math.random() * 0.75;
  particle.size = 0.7 + particle.depth * 2.3 + Math.random() * 0.8;
  particle.opacity = 0.2 + particle.depth * 0.64;
  particle.vx = (Math.random() - 0.5) * (7 + particle.depth * 9);
  particle.vy = 11 + particle.depth * 30 + Math.random() * 12;
  particle.phase = Math.random() * Math.PI * 2;
  particle.spin = (Math.random() - 0.5) * 2.2;
}

function seedParticles() {
  const areaFactor = Math.min(1, (state.width * state.height) / (1280 * 760));
  const targetCount = Math.max(220, Math.round(profile.particles * Math.max(0.62, areaFactor)));
  state.particles = Array.from({ length: targetCount }, () => {
    const particle = {};
    resetParticle(particle, true);
    return particle;
  });
}

function resizeField() {
  const rect = stage.getBoundingClientRect();
  state.width = Math.max(1, rect.width);
  state.height = Math.max(1, rect.height);
  state.dpr = Math.min(profile.maxDpr, window.devicePixelRatio || 1);
  fieldCanvas.width = Math.round(state.width * state.dpr);
  fieldCanvas.height = Math.round(state.height * state.dpr);
  fieldCanvas.style.width = `${state.width}px`;
  fieldCanvas.style.height = `${state.height}px`;
  if (!isWaveMode) seedParticles();
  state.ocean?.resize();
  updateProfileText();
}

function updatePointer(event) {
  const rect = stage.getBoundingClientRect();
  const now = performance.now();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const hasPreviousPoint = state.pointer.updatedAt > 0;
  const elapsed = hasPreviousPoint ? Math.max(12, now - state.pointer.updatedAt) / 1000 : 1;
  const vx = hasPreviousPoint ? (x - state.pointer.x) / elapsed : 0;
  const vy = hasPreviousPoint ? (y - state.pointer.y) / elapsed : 0;
  state.pointer.active = true;
  state.pointer.vx = clamp(vx, -1600, 1600);
  state.pointer.vy = clamp(vy, -1600, 1600);
  state.pointer.speed = Math.min(1600, Math.hypot(vx, vy));
  state.pointer.x = x;
  state.pointer.y = y;
  state.pointer.updatedAt = now;
  if (isWaveMode && state.ocean && !state.paused) {
    state.ocean.setPointer(event.clientX, event.clientY, state.pointer.speed);
  }
}

function updateParticle(particle, delta, elapsed) {
  const pointerAge = elapsed - state.pointer.updatedAt;
  const pointerActive = state.pointer.active && pointerAge < 260;
  const wind = Math.sin(elapsed * 0.00022 + particle.phase) * (5 + particle.depth * 12);
  particle.vx += wind * delta;

  if (pointerActive) {
    const dx = particle.x - state.pointer.x;
    const dy = particle.y - state.pointer.y;
    const distance = Math.hypot(dx, dy);
    const radius = 115 + Math.min(110, state.pointer.speed * 0.07);
    if (distance > 0.1 && distance < radius) {
      const influence = (1 - distance / radius) ** 2;
      const nx = dx / distance;
      const ny = dy / distance;
      const impulse = 90 + state.pointer.speed * 0.24;
      particle.vx += (nx * impulse + state.pointer.vx * 0.17 - ny * 55) * influence * delta;
      particle.vy += (ny * impulse + state.pointer.vy * 0.12 + nx * 55) * influence * delta;
    }
  }

  const damping = Math.pow(0.986, delta * 60);
  particle.vx *= damping;
  particle.vy = particle.vy * damping + (8 + particle.depth * 15) * delta;
  particle.x += particle.vx * delta;
  particle.y += particle.vy * delta;
  particle.phase += particle.spin * delta;

  if (particle.y > state.height + 24) resetParticle(particle);
  if (particle.x < -30) particle.x = state.width + 24;
  if (particle.x > state.width + 30) particle.x = -24;
}

function drawParticle(particle) {
  const shimmer = 0.82 + Math.sin(particle.phase) * 0.18;
  const alpha = Math.max(0.08, particle.opacity * shimmer);
  const hue = particle.depth > 0.72 ? "244, 250, 247" : particle.depth > 0.45 ? "212, 240, 244" : "179, 219, 230";
  context.fillStyle = `rgba(${hue}, ${alpha.toFixed(3)})`;
  context.beginPath();
  context.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
  context.fill();

  if (particle.size > 2.7) {
    context.strokeStyle = `rgba(236, 250, 248, ${(alpha * 0.42).toFixed(3)})`;
    context.lineWidth = 0.65;
    context.beginPath();
    context.moveTo(particle.x - particle.size * 1.7, particle.y);
    context.lineTo(particle.x + particle.size * 1.7, particle.y);
    context.moveTo(particle.x, particle.y - particle.size * 1.7);
    context.lineTo(particle.x, particle.y + particle.size * 1.7);
    context.stroke();
  }
}

function drawFallbackOcean(now) {
  const horizon = state.height * 0.46;
  const water = context.createLinearGradient(0, horizon, 0, state.height);
  water.addColorStop(0, "rgba(15, 104, 116, 0.9)");
  water.addColorStop(1, "rgba(2, 27, 39, 1)");
  context.fillStyle = water;
  context.beginPath();
  context.moveTo(0, horizon);
  for (let x = 0; x <= state.width; x += 16) {
    const y = horizon + Math.sin(x * 0.018 - now * 0.0016) * 9 + Math.sin(x * 0.043 - now * 0.0025) * 3;
    context.lineTo(x, y);
  }
  context.lineTo(state.width, state.height);
  context.lineTo(0, state.height);
  context.closePath();
  context.fill();
}

function renderFrame(now) {
  const delta = Math.min(0.033, Math.max(0, (now - state.lastFrame) / 1000));
  state.lastFrame = now;

  if (isWaveMode) {
    if (state.ocean) {
      if (!document.hidden) state.ocean.update(state.paused ? 0 : delta);
      state.ocean.render();
    } else if (state.fallback) {
      context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
      context.clearRect(0, 0, state.width, state.height);
      drawFallbackOcean(now);
    }
  } else {
    context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
    context.clearRect(0, 0, state.width, state.height);
    if (!state.paused && !document.hidden) {
      state.particles.forEach((particle) => updateParticle(particle, delta, now));
    }
    state.particles.forEach(drawParticle);
  }
  state.frameId = requestAnimationFrame(renderFrame);
}

pauseButton.addEventListener("click", async () => {
  state.paused = !state.paused;
  if (!state.paused) await initializeSimulation();
  state.ocean?.setPaused(state.paused);
  setFluidPaused(state.paused);
  updatePauseButton();
});

resetButton.addEventListener("click", () => {
  if (isWaveMode) {
    state.ocean?.reset();
    return;
  }
  seedParticles();
  if (state.fluidReady) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space" }));
  }
});

stage.addEventListener("pointermove", updatePointer, { passive: true });
stage.addEventListener("pointerdown", (event) => {
  if (!isWaveMode || !state.ocean || state.paused) return;
  state.ocean.setPointer(event.clientX, event.clientY, 1200);
  state.ocean.pulsePointer();
});
stage.addEventListener("pointerleave", () => {
  state.pointer.active = false;
  state.pointer.updatedAt = 0;
  state.ocean?.setPointerActive(false);
});

document.addEventListener("visibilitychange", () => {
  if (isWaveMode) {
    state.lastFrame = performance.now();
    return;
  }
  if (!state.fluidReady || state.paused) return;
  if (document.hidden && !state.hiddenSuspension) {
    setFluidPaused(true);
    state.hiddenSuspension = true;
  } else if (!document.hidden && state.hiddenSuspension) {
    setFluidPaused(false);
    state.hiddenSuspension = false;
    state.lastFrame = performance.now();
  }
});

window.addEventListener("resize", resizeField, { passive: true });
window.addEventListener(
  "pagehide",
  () => {
    cancelAnimationFrame(state.frameId);
    state.ocean?.dispose();
  },
  { once: true }
);

resizeField();
updatePauseButton();
renderFrame(performance.now());
if (!state.paused) initializeSimulation();
