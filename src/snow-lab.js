const root = document.querySelector("[data-snow-lab]");
const stage = root.querySelector(".snow-lab__stage");
const fluidCanvas = root.querySelector("[data-fluid-canvas]");
const snowCanvas = root.querySelector("[data-snow-canvas]");
const pauseButton = root.querySelector("[data-snow-pause]");
const resetButton = root.querySelector("[data-snow-reset]");
const statusText = root.querySelector("[data-snow-status]");
const profileText = root.querySelector("[data-snow-profile]");
const qualityButtons = [...root.querySelectorAll("[data-quality]")];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const qualityProfiles = {
  high: {
    label: "고화질 · 유체 192 · 입자 1,300",
    simResolution: 192,
    dyeResolution: 1024,
    pressureIterations: 28,
    bloom: true,
    sunrays: true,
    particles: 1300,
    maxDpr: 1.8
  },
  balanced: {
    label: "자동 · 유체 128 · 입자 850",
    simResolution: 128,
    dyeResolution: 512,
    pressureIterations: 22,
    bloom: true,
    sunrays: false,
    particles: 850,
    maxDpr: 1.5
  },
  eco: {
    label: "절전 · 유체 64 · 입자 420",
    simResolution: 64,
    dyeResolution: 256,
    pressureIterations: 14,
    bloom: false,
    sunrays: false,
    particles: 420,
    maxDpr: 1.25
  }
};

function autoProfileName() {
  const cores = Number(navigator.hardwareConcurrency || 4);
  const memory = Number(navigator.deviceMemory || 4);
  const compact = window.matchMedia("(max-width: 760px)").matches;
  if (compact || cores <= 4 || memory <= 4) return "eco";
  if (cores >= 10 && memory >= 8 && window.innerWidth >= 1200) return "high";
  return "balanced";
}

const requestedQuality = new URLSearchParams(window.location.search).get("quality") || "auto";
const profileName = requestedQuality === "auto" ? autoProfileName() : requestedQuality;
const profile = qualityProfiles[profileName] || qualityProfiles.balanced;
const state = {
  paused: reducedMotion.matches,
  hiddenSuspension: false,
  fluidReady: false,
  fluidPaused: false,
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

function updatePauseButton() {
  pauseButton.textContent = state.paused ? "▶" : "Ⅱ";
  pauseButton.setAttribute("aria-label", state.paused ? "시뮬레이션 재생" : "시뮬레이션 일시정지");
  pauseButton.title = state.paused ? "재생" : "일시정지";
  root.classList.toggle("is-paused", state.paused);
  if (state.paused) statusText.textContent = "유체장 일시정지";
  else if (state.fallback) statusText.textContent = "눈 입자 모드";
  else if (state.fluidReady) statusText.textContent = "GPU 유체장 실행중";
}

function setFluidPaused(paused) {
  if (!state.fluidReady || state.fluidPaused === paused) return;
  window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP" }));
  state.fluidPaused = paused;
}

async function initializeFluid() {
  if (state.fluidReady || state.fallback) return;
  try {
    const { default: WebGLFluid } = await import("./vendor/webgl-fluid.mjs");
    WebGLFluid(fluidCanvas, {
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
    });
    state.fluidReady = true;
    statusText.textContent = "GPU 유체장 실행중";
  } catch (error) {
    state.fallback = true;
    root.classList.add("is-fallback");
    statusText.textContent = "눈 입자 모드";
    console.warn("Snow Lab WebGL fallback:", error);
  }
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

function resizeSnowCanvas() {
  const rect = stage.getBoundingClientRect();
  state.width = Math.max(1, rect.width);
  state.height = Math.max(1, rect.height);
  state.dpr = Math.min(profile.maxDpr, window.devicePixelRatio || 1);
  snowCanvas.width = Math.round(state.width * state.dpr);
  snowCanvas.height = Math.round(state.height * state.dpr);
  snowCanvas.style.width = `${state.width}px`;
  snowCanvas.style.height = `${state.height}px`;
  seedParticles();
}

const context = snowCanvas.getContext("2d", { alpha: true });

function updatePointer(event) {
  const rect = stage.getBoundingClientRect();
  const now = performance.now();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const elapsed = Math.max(12, now - state.pointer.updatedAt) / 1000;
  const vx = (x - state.pointer.x) / elapsed;
  const vy = (y - state.pointer.y) / elapsed;
  state.pointer.active = true;
  state.pointer.vx = Math.max(-1600, Math.min(1600, vx));
  state.pointer.vy = Math.max(-1600, Math.min(1600, vy));
  state.pointer.speed = Math.min(1600, Math.hypot(vx, vy));
  state.pointer.x = x;
  state.pointer.y = y;
  state.pointer.updatedAt = now;
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

function renderFrame(now) {
  const delta = Math.min(0.033, Math.max(0, (now - state.lastFrame) / 1000));
  state.lastFrame = now;
  context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  context.clearRect(0, 0, state.width, state.height);

  if (!state.paused && !document.hidden) {
    state.particles.forEach((particle) => updateParticle(particle, delta, now));
  }
  state.particles.forEach(drawParticle);
  state.frameId = requestAnimationFrame(renderFrame);
}

pauseButton.addEventListener("click", async () => {
  state.paused = !state.paused;
  if (!state.paused) await initializeFluid();
  setFluidPaused(state.paused);
  updatePauseButton();
});

resetButton.addEventListener("click", () => {
  seedParticles();
  if (state.fluidReady) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space" }));
  }
});

stage.addEventListener("pointermove", updatePointer, { passive: true });
stage.addEventListener("pointerleave", () => {
  state.pointer.active = false;
});

document.addEventListener("visibilitychange", () => {
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

window.addEventListener("resize", resizeSnowCanvas, { passive: true });
window.addEventListener("pagehide", () => cancelAnimationFrame(state.frameId), { once: true });

profileText.textContent = profile.label;
resizeSnowCanvas();
updatePauseButton();
state.frameId = requestAnimationFrame(renderFrame);
if (!state.paused) initializeFluid();
