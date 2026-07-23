const root = document.querySelector("[data-snow-lab]");
const stage = root.querySelector("[data-lab-stage]");
const fluidCanvas = root.querySelector("[data-fluid-canvas]");
const fieldCanvas = root.querySelector("[data-field-canvas]");
const title = root.querySelector("[data-lab-title]");
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
    waveSpacing: 3.2,
    maxDpr: 1.8
  },
  balanced: {
    simResolution: 128,
    dyeResolution: 512,
    pressureIterations: 22,
    bloom: true,
    sunrays: false,
    particles: 850,
    waveSpacing: 4.8,
    maxDpr: 1.5
  },
  eco: {
    simResolution: 64,
    dyeResolution: 256,
    pressureIterations: 14,
    bloom: false,
    sunrays: false,
    particles: 420,
    waveSpacing: 7.5,
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
  fallback: false,
  frameId: 0,
  lastFrame: performance.now(),
  width: 1,
  height: 1,
  dpr: 1,
  particles: [],
  wave: {
    heights: new Float32Array(0),
    velocities: new Float32Array(0),
    columns: 0,
    ripples: [],
    lastImpulseAt: 0,
    lastRippleAt: 0
  },
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
title.textContent = isWaveMode ? "Wave Lab" : "Snow Lab";
document.title = isWaveMode ? "Wave Lab" : "Snow Lab";
stage.setAttribute("aria-label", isWaveMode ? "GPU 유체장과 반응형 수면 시뮬레이션" : "GPU 유체장과 눈 입자 시뮬레이션");

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

function runningStatus() {
  if (state.fallback) return isWaveMode ? "수면 파형 모드" : "눈 입자 모드";
  if (state.fluidReady) return isWaveMode ? "GPU 유체·수면장 실행중" : "GPU 유체장 실행중";
  return isWaveMode ? "GPU 수면장 준비중" : "GPU 유체장 준비중";
}

function updatePauseButton() {
  pauseButton.textContent = state.paused ? "▶" : "Ⅱ";
  pauseButton.setAttribute("aria-label", state.paused ? "시뮬레이션 재생" : "시뮬레이션 일시정지");
  pauseButton.title = state.paused ? "재생" : "일시정지";
  root.classList.toggle("is-paused", state.paused);
  statusText.textContent = state.paused ? `${isWaveMode ? "파도" : "유체장"} 일시정지` : runningStatus();
}

function updateProfileText() {
  const qualityLabel = requestedQuality === "auto" ? `자동(${profileName === "high" ? "고화질" : profileName === "eco" ? "절전" : "균형"})` : requestedQuality === "high" ? "고화질" : "절전";
  const sceneLabel = isWaveMode ? `수면 ${state.wave.columns}` : `입자 ${state.particles.length.toLocaleString("ko-KR")}`;
  profileText.textContent = `${qualityLabel} · 유체 ${profile.simResolution} · ${sceneLabel}`;
}

function setFluidPaused(paused) {
  if (!state.fluidReady || state.fluidPaused === paused) return;
  window.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP" }));
  state.fluidPaused = paused;
}

function fluidConfiguration() {
  const sceneOptions = isWaveMode
    ? {
        DENSITY_DISSIPATION: 0.985,
        VELOCITY_DISSIPATION: 0.34,
        CURL: 18,
        SPLAT_RADIUS: 0.28,
        SPLAT_FORCE: 3200,
        SPLAT_COUNT: 0,
        COLOR_UPDATE_SPEED: 0.55,
        BACK_COLOR: { r: 2, g: 16, b: 22 },
        BLOOM_INTENSITY: 0.24,
        SUNRAYS_WEIGHT: 0.22
      }
    : {
        DENSITY_DISSIPATION: 0.97,
        VELOCITY_DISSIPATION: 0.28,
        CURL: 24,
        SPLAT_RADIUS: 0.18,
        SPLAT_FORCE: 4200,
        SPLAT_COUNT: profileName === "eco" ? 4 : 7,
        COLOR_UPDATE_SPEED: 2.2,
        BACK_COLOR: { r: 5, g: 11, b: 13 },
        BLOOM_INTENSITY: 0.34,
        SUNRAYS_WEIGHT: 0.32
      };

  return {
    TRIGGER: "hover",
    IMMEDIATE: true,
    AUTO: false,
    SIM_RESOLUTION: profile.simResolution,
    DYE_RESOLUTION: profile.dyeResolution,
    PRESSURE: 0.8,
    PRESSURE_ITERATIONS: profile.pressureIterations,
    SHADING: true,
    COLORFUL: true,
    PAUSED: false,
    TRANSPARENT: false,
    BLOOM: profile.bloom,
    BLOOM_ITERATIONS: profileName === "high" ? 6 : 4,
    BLOOM_RESOLUTION: profileName === "high" ? 256 : 128,
    BLOOM_THRESHOLD: 0.72,
    BLOOM_SOFT_KNEE: 0.6,
    SUNRAYS: profile.sunrays,
    SUNRAYS_RESOLUTION: 128,
    ...sceneOptions
  };
}

async function initializeFluid() {
  if (state.fluidReady || state.fallback) return;
  try {
    const { default: WebGLFluid } = await import("./vendor/webgl-fluid.mjs");
    WebGLFluid(fluidCanvas, fluidConfiguration());
    state.fluidReady = true;
    statusText.textContent = runningStatus();
  } catch (error) {
    state.fallback = true;
    root.classList.add("is-fallback");
    statusText.textContent = runningStatus();
    console.warn("Field Lab WebGL fallback:", error);
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

function seedWave() {
  const columns = clamp(Math.round(state.width / profile.waveSpacing), 120, 420);
  state.wave.columns = columns;
  state.wave.heights = new Float32Array(columns);
  state.wave.velocities = new Float32Array(columns);
  state.wave.ripples = [];
  state.wave.lastImpulseAt = 0;
  state.wave.lastRippleAt = 0;

  for (let index = 0; index < columns; index += 1) {
    state.wave.heights[index] = Math.sin(index * 0.085) * 2.2 + Math.sin(index * 0.031 + 1.4) * 1.3;
  }
}

function seedScene() {
  if (isWaveMode) seedWave();
  else seedParticles();
  updateProfileText();
}

function resizeFieldCanvas() {
  const rect = stage.getBoundingClientRect();
  state.width = Math.max(1, rect.width);
  state.height = Math.max(1, rect.height);
  state.dpr = Math.min(profile.maxDpr, window.devicePixelRatio || 1);
  fieldCanvas.width = Math.round(state.width * state.dpr);
  fieldCanvas.height = Math.round(state.height * state.dpr);
  fieldCanvas.style.width = `${state.width}px`;
  fieldCanvas.style.height = `${state.height}px`;
  seedScene();
}

function injectWave(x, strength) {
  const { columns, velocities } = state.wave;
  if (!columns) return;
  const center = clamp(Math.round((x / state.width) * (columns - 1)), 0, columns - 1);
  const radius = Math.max(5, Math.round(columns * 0.038));
  for (let offset = -radius; offset <= radius; offset += 1) {
    const index = center + offset;
    if (index < 0 || index >= columns) continue;
    const falloff = Math.cos((Math.abs(offset) / radius) * Math.PI * 0.5) ** 2;
    velocities[index] += strength * falloff;
  }
}

function addRipple(x, y, strength, now) {
  if (state.wave.ripples.length >= 18) state.wave.ripples.shift();
  state.wave.ripples.push({
    x,
    y,
    strength: clamp(strength / 180, 0.25, 1),
    age: 0,
    life: 1.35 + Math.random() * 0.45,
    startedAt: now
  });
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

  if (isWaveMode && hasPreviousPoint && !state.paused && now - state.wave.lastImpulseAt > 28) {
    const impulse = clamp(vy * 0.1 + state.pointer.speed * 0.055, -180, 220);
    injectWave(x, impulse);
    state.wave.lastImpulseAt = now;
    if (now - state.wave.lastRippleAt > 72) {
      addRipple(x, y, Math.abs(impulse), now);
      state.wave.lastRippleAt = now;
    }
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

function updateWave(delta) {
  const { heights, velocities, columns } = state.wave;
  if (columns < 3) return;
  const steps = Math.max(1, Math.ceil(delta / 0.018));
  const step = delta / steps;

  for (let substep = 0; substep < steps; substep += 1) {
    for (let index = 1; index < columns - 1; index += 1) {
      const neighborAverage = (heights[index - 1] + heights[index + 1]) * 0.5;
      const acceleration = (neighborAverage - heights[index]) * 46 - velocities[index] * 2.9;
      velocities[index] += acceleration * step;
    }
    for (let index = 1; index < columns - 1; index += 1) {
      heights[index] += velocities[index] * step;
      heights[index] = clamp(heights[index], -54, 54);
    }
    heights[0] = heights[1];
    heights[columns - 1] = heights[columns - 2];
    velocities[0] = velocities[1] * 0.92;
    velocities[columns - 1] = velocities[columns - 2] * 0.92;
  }

  state.wave.ripples.forEach((ripple) => {
    ripple.age += delta;
  });
  state.wave.ripples = state.wave.ripples.filter((ripple) => ripple.age < ripple.life);
}

function wavePoint(index, baseline, response, now, phase) {
  const slowSwell = Math.sin(index * 0.038 - now * 0.00042 + phase) * 5.2;
  const surfaceTexture = Math.sin(index * 0.102 + now * 0.00072 + phase * 0.7) * 2.8;
  return baseline + state.wave.heights[index] * response + (slowSwell + surfaceTexture) * response;
}

function traceWavePath(baseline, response, now, phase) {
  const columns = state.wave.columns;
  const spacing = state.width / Math.max(1, columns - 1);
  let previousX = 0;
  let previousY = wavePoint(0, baseline, response, now, phase);
  context.beginPath();
  context.moveTo(previousX, previousY);

  for (let index = 1; index < columns; index += 1) {
    const x = index * spacing;
    const y = wavePoint(index, baseline, response, now, phase);
    const middleX = (previousX + x) * 0.5;
    const middleY = (previousY + y) * 0.5;
    context.quadraticCurveTo(previousX, previousY, middleX, middleY);
    previousX = x;
    previousY = y;
  }
  context.quadraticCurveTo(previousX, previousY, state.width, previousY);
}

function drawWaveLayer(baseline, response, now, phase, fillStyle, strokeStyle, lineWidth) {
  traceWavePath(baseline, response, now, phase);
  context.lineTo(state.width, state.height);
  context.lineTo(0, state.height);
  context.closePath();
  context.fillStyle = fillStyle;
  context.fill();

  traceWavePath(baseline, response, now, phase);
  context.strokeStyle = strokeStyle;
  context.lineWidth = lineWidth;
  context.stroke();
}

function drawWaveGlints(baseline, now) {
  const columns = state.wave.columns;
  const spacing = state.width / Math.max(1, columns - 1);
  context.save();
  context.globalCompositeOperation = "screen";
  context.lineCap = "round";

  for (let index = 7; index < columns - 8; index += 13) {
    const shimmer = (Math.sin(index * 1.73 + now * 0.0013) + 1) * 0.5;
    if (shimmer < 0.58) continue;
    const x = index * spacing;
    const y = wavePoint(index, baseline, 1, now, 0);
    const width = 8 + shimmer * 18;
    context.strokeStyle = `rgba(182, 248, 246, ${(0.08 + shimmer * 0.22).toFixed(3)})`;
    context.lineWidth = 0.7 + shimmer;
    context.beginPath();
    context.moveTo(x - width * 0.5, y + 2);
    context.lineTo(x + width * 0.5, y - 1);
    context.stroke();
  }
  context.restore();
}

function drawRipples() {
  context.save();
  context.globalCompositeOperation = "screen";
  state.wave.ripples.forEach((ripple) => {
    const progress = ripple.age / ripple.life;
    const radius = 12 + progress * (95 + ripple.strength * 80);
    const alpha = (1 - progress) ** 1.8 * (0.16 + ripple.strength * 0.34);
    for (let ring = 0; ring < 2; ring += 1) {
      context.strokeStyle = `rgba(151, 239, 241, ${(alpha / (ring + 1)).toFixed(3)})`;
      context.lineWidth = 1.2 - ring * 0.3;
      context.beginPath();
      context.ellipse(ripple.x, ripple.y, radius + ring * 13, (radius + ring * 13) * 0.27, 0, 0, Math.PI * 2);
      context.stroke();
    }
  });
  context.restore();
}

function drawWave(now) {
  const atmosphere = context.createLinearGradient(0, 0, 0, state.height);
  atmosphere.addColorStop(0, "rgba(1, 15, 20, 0.08)");
  atmosphere.addColorStop(0.46, "rgba(2, 34, 43, 0.2)");
  atmosphere.addColorStop(1, "rgba(0, 17, 27, 0.74)");
  context.fillStyle = atmosphere;
  context.fillRect(0, 0, state.width, state.height);

  drawWaveLayer(
    state.height * 0.44,
    0.38,
    now,
    1.8,
    "rgba(13, 78, 91, 0.28)",
    "rgba(116, 214, 219, 0.2)",
    1
  );
  drawWaveLayer(
    state.height * 0.51,
    0.62,
    now,
    0.9,
    "rgba(7, 64, 79, 0.48)",
    "rgba(126, 231, 232, 0.28)",
    1.2
  );

  const water = context.createLinearGradient(0, state.height * 0.56, 0, state.height);
  water.addColorStop(0, "rgba(10, 102, 116, 0.68)");
  water.addColorStop(0.45, "rgba(3, 54, 70, 0.88)");
  water.addColorStop(1, "rgba(1, 20, 33, 0.98)");
  drawWaveLayer(
    state.height * 0.59,
    1,
    now,
    0,
    water,
    "rgba(174, 246, 244, 0.72)",
    1.45
  );
  drawWaveGlints(state.height * 0.59, now);
  drawRipples();
}

function renderFrame(now) {
  const delta = Math.min(0.033, Math.max(0, (now - state.lastFrame) / 1000));
  state.lastFrame = now;
  context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  context.clearRect(0, 0, state.width, state.height);

  if (isWaveMode) {
    if (!state.paused && !document.hidden) updateWave(delta);
    drawWave(now);
  } else {
    if (!state.paused && !document.hidden) {
      state.particles.forEach((particle) => updateParticle(particle, delta, now));
    }
    state.particles.forEach(drawParticle);
  }
  state.frameId = requestAnimationFrame(renderFrame);
}

pauseButton.addEventListener("click", async () => {
  state.paused = !state.paused;
  if (!state.paused) await initializeFluid();
  setFluidPaused(state.paused);
  updatePauseButton();
});

resetButton.addEventListener("click", () => {
  if (isWaveMode) {
    window.location.reload();
    return;
  }
  seedScene();
  if (state.fluidReady) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: " ", code: "Space" }));
  }
});

stage.addEventListener("pointermove", updatePointer, { passive: true });
stage.addEventListener("pointerdown", (event) => {
  if (!isWaveMode || state.paused) return;
  const rect = stage.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  injectWave(x, 190);
  addRipple(x, y, 190, performance.now());
});
stage.addEventListener("pointerleave", () => {
  state.pointer.active = false;
  state.pointer.updatedAt = 0;
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

window.addEventListener("resize", resizeFieldCanvas, { passive: true });
window.addEventListener("pagehide", () => cancelAnimationFrame(state.frameId), { once: true });

resizeFieldCanvas();
updatePauseButton();
renderFrame(performance.now());
if (!state.paused) initializeFluid();
