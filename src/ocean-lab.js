import * as THREE from "./vendor/three.module.min.js";

const GRAVITY = 9.81;

const segmentProfiles = {
  high: { width: 220, depth: 160, spectrumWaves: 32 },
  balanced: { width: 160, depth: 116, spectrumWaves: 24 },
  eco: { width: 96, depth: 72, spectrumWaves: 16 }
};

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(random) {
  const first = Math.max(1e-7, random());
  const second = random();
  return Math.sqrt(-2 * Math.log(first)) * Math.cos(2 * Math.PI * second);
}

function jonswapSpectrum(omega, peakOmega, alpha, gamma, shortWaveFade) {
  const sigma = omega <= peakOmega ? 0.07 : 0.09;
  const peakDistance = (omega - peakOmega) / (sigma * peakOmega);
  const peakShape = Math.exp(-0.5 * peakDistance * peakDistance);
  const ratio = peakOmega / Math.max(omega, 1e-5);
  const base = alpha * GRAVITY * GRAVITY * Math.pow(omega, -5) * Math.exp(-1.25 * Math.pow(ratio, 4));
  return base * Math.pow(gamma, peakShape) * shortWaveFade;
}

function buildJonswapComponents(count) {
  const random = seededRandom(20260723);
  const depth = 18;
  const fetch = 12000;
  const windSpeed = 15;
  const gamma = 3.3;
  const alpha = 0.076 * Math.pow((GRAVITY * fetch) / (windSpeed * windSpeed), -0.22);
  const peakOmega = 22 * Math.pow((windSpeed * fetch) / (GRAVITY * GRAVITY), -0.33);
  const minimumK = 0.16;
  const maximumK = 3.8;
  const deltaK = (maximumK - minimumK) / Math.max(1, count - 1);
  const samples = [];

  // JONSWAP 에너지를 장주기·풍파·단주기 대역에 걸쳐 고정 시드로 희소 표본화합니다.
  for (let index = 0; index < count; index += 1) {
    const progress = count <= 1 ? 0 : index / (count - 1);
    const kMagnitude = minimumK * Math.pow(maximumK / minimumK, progress);
    const limitedDepth = Math.min(kMagnitude * depth, 20);
    const tanhDepth = Math.tanh(limitedDepth);
    const omega = Math.sqrt(GRAVITY * kMagnitude * tanhDepth);
    const coshDepth = Math.cosh(limitedDepth);
    const dispersionDerivative =
      (GRAVITY * (depth * kMagnitude / (coshDepth * coshDepth) + tanhDepth)) / Math.max(2 * omega, 1e-5);
    const shortWaveFade = Math.exp(-0.018 * kMagnitude * kMagnitude);
    const spectralEnergy = jonswapSpectrum(omega, peakOmega, alpha, gamma, shortWaveFade);
    const relativeFrequency = omega / peakOmega;
    const directionalSpread = 0.07 + Math.min(0.34, Math.abs(Math.log(relativeFrequency)) * 0.16);
    const angle = gaussian(random) * directionalSpread;
    const randomEnergy = 0.72 + Math.min(0.72, Math.abs(gaussian(random)) * 0.24);
    const amplitude = Math.sqrt(Math.max(0, 2 * spectralEnergy * dispersionDerivative * deltaK)) * randomEnergy;
    const band = kMagnitude < 0.46 ? 0 : kMagnitude < 1.35 ? 1 : 2;

    samples.push({
      directionX: Math.cos(angle),
      directionZ: Math.sin(angle),
      kMagnitude,
      amplitude,
      omega,
      phase: random() * Math.PI * 2,
      choppiness: 0.48 + band * 0.12,
      band
    });
  }

  const energy = samples.reduce((sum, sample) => sum + sample.amplitude * sample.amplitude * 0.5, 0);
  const normalization = energy > 0 ? 0.56 / Math.sqrt(energy) : 1;
  const waves = Array.from({ length: count }, () => new THREE.Vector4());
  const motion = Array.from({ length: count }, () => new THREE.Vector4());

  samples.forEach((sample, index) => {
    waves[index].set(
      sample.directionX * sample.kMagnitude,
      sample.directionZ * sample.kMagnitude,
      sample.amplitude * normalization,
      sample.phase
    );
    motion[index].set(sample.omega, sample.choppiness, sample.band, 0);
  });

  return { waves, motion, count, depth };
}

const pointerShaderFunction = `
  void applyPointer(
    vec2 point,
    inout float height,
    inout vec2 gradient,
    out float pointerInfluence
  ) {
    vec2 pointerDelta = point - uPointer;
    float pointerDistance = length(pointerDelta);
    float pointerFalloff = exp(-pointerDistance * pointerDistance * 0.58);
    float pointerRing = smoothstep(0.14, 0.62, pointerDistance);
    float pointerPhase = pointerDistance * 5.4 - uTime * 7.2;
    float raisedRing = (0.5 + 0.5 * sin(pointerPhase)) * pointerRing;
    float pointerWave = raisedRing * 0.23 * uPointerStrength * pointerFalloff;
    pointerWave += exp(-pointerDistance * pointerDistance * 2.8) * 0.075 * uPointerStrength;
    height += pointerWave;

    if (pointerDistance > 0.001) {
      float waveDerivative = 0.09 * 5.4 * cos(pointerPhase) * pointerRing;
      float falloffDerivative = -1.16 * pointerDistance * raisedRing * 0.23;
      gradient += normalize(pointerDelta) * (waveDerivative + falloffDerivative) * uPointerStrength * pointerFalloff;
    }

    pointerInfluence = pointerFalloff * (0.35 + pointerRing * 0.65) * uPointerStrength;
  }
`;

const gerstnerVertexShader = `
  precision highp float;

  uniform float uTime;
  uniform vec2 uPointer;
  uniform float uPointerStrength;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vPointerInfluence;
  varying float vFoam;

  void addWave(
    vec2 point,
    vec2 direction,
    float amplitude,
    float frequency,
    float speed,
    float steepness,
    inout float height,
    inout vec2 gradient,
    inout vec2 horizontal
  ) {
    vec2 waveDirection = normalize(direction);
    float phase = dot(waveDirection, point) * frequency - uTime * speed;
    float sine = sin(phase);
    float cosine = cos(phase);
    height += sine * amplitude;
    gradient += waveDirection * cosine * amplitude * frequency;
    horizontal += waveDirection * cosine * amplitude * steepness;
  }

  ${pointerShaderFunction}

  void main() {
    vec3 displaced = position;
    vec2 point = position.xz;
    float height = 0.0;
    vec2 gradient = vec2(0.0);
    vec2 horizontal = vec2(0.0);

    addWave(point, vec2(1.0, 0.0), 0.48, 0.60, 1.05, 0.36, height, gradient, horizontal);
    addWave(point, vec2(0.98, 0.18), 0.22, 0.95, 1.38, 0.26, height, gradient, horizontal);
    addWave(point, vec2(0.93, -0.36), 0.10, 1.65, 2.00, 0.18, height, gradient, horizontal);
    addWave(point, vec2(0.58, 0.82), 0.042, 2.80, 2.60, 0.09, height, gradient, horizontal);

    float pointerInfluence = 0.0;
    applyPointer(point, height, gradient, pointerInfluence);

    displaced.xz += horizontal;
    displaced.y += height;

    float slope = length(gradient);
    vec3 localNormal = normalize(vec3(-gradient.x, 1.0, -gradient.y));
    vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
    vWaveHeight = height;
    vSlope = slope;
    vPointerInfluence = pointerInfluence;
    vFoam = smoothstep(0.72, 1.34, slope + max(height, 0.0) * 0.32);
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

function createSpectrumVertexShader(waveCount) {
  return `
  precision highp float;

  #define SPECTRUM_WAVE_COUNT ${waveCount}

  uniform float uTime;
  uniform vec2 uPointer;
  uniform float uPointerStrength;
  uniform vec4 uSpectrumWaves[SPECTRUM_WAVE_COUNT];
  uniform vec4 uSpectrumMotion[SPECTRUM_WAVE_COUNT];

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vPointerInfluence;
  varying float vFoam;

  ${pointerShaderFunction}

  void main() {
    vec3 displaced = position;
    vec2 point = position.xz;
    float height = 0.0;
    vec2 gradient = vec2(0.0);
    vec2 horizontal = vec2(0.0);
    vec4 horizontalDerivative = vec4(0.0);

    for (int index = 0; index < SPECTRUM_WAVE_COUNT; index += 1) {
      vec4 wave = uSpectrumWaves[index];
      vec4 motion = uSpectrumMotion[index];
      vec2 waveVector = wave.xy;
      float waveNumber = max(length(waveVector), 0.0001);
      vec2 direction = waveVector / waveNumber;
      float amplitude = wave.z;
      float phase = dot(waveVector, point) - motion.x * uTime + wave.w;
      float cosine = cos(phase);
      float sine = sin(phase);
      float choppiness = motion.y;

      height += amplitude * cosine;
      gradient -= waveVector * amplitude * sine;
      horizontal -= direction * amplitude * choppiness * sine;

      float derivativeScale = -amplitude * choppiness * cosine;
      horizontalDerivative.x += derivativeScale * direction.x * waveVector.x;
      horizontalDerivative.y += derivativeScale * direction.x * waveVector.y;
      horizontalDerivative.z += derivativeScale * direction.y * waveVector.x;
      horizontalDerivative.w += derivativeScale * direction.y * waveVector.y;
    }

    float pointerInfluence = 0.0;
    applyPointer(point, height, gradient, pointerInfluence);

    displaced.xz += horizontal;
    displaced.y += height;

    float jacobian =
      (1.0 + horizontalDerivative.x) * (1.0 + horizontalDerivative.w) -
      horizontalDerivative.y * horizontalDerivative.z;
    float compression = max(0.0, 1.0 - jacobian);
    float slope = length(gradient);
    float crestGate = smoothstep(-0.05, 0.58, height + slope * 0.18);

    vec3 localNormal = normalize(vec3(-gradient.x, 1.0, -gradient.y));
    vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
    vWaveHeight = height;
    vSlope = slope;
    vPointerInfluence = pointerInfluence;
    vFoam = smoothstep(0.10, 0.46, compression) * crestGate;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;
}

const oceanFragmentShader = `
  precision highp float;

  uniform float uTime;
  uniform vec3 uDeepColor;
  uniform vec3 uSurfaceColor;
  uniform vec3 uHorizonColor;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vPointerInfluence;
  varying float vFoam;

  void main() {
    float microX = sin(vWorldPosition.x * 1.8 - uTime * 1.7);
    float microZ = sin(vWorldPosition.z * 2.2 - uTime * 1.25);
    vec3 normal = normalize(vWorldNormal + vec3(microX * 0.012, 0.0, microZ * 0.012));
    vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
    vec3 lightDirection = normalize(vec3(-0.42, 0.78, 0.46));

    float facing = clamp(dot(normal, viewDirection), 0.0, 1.0);
    float fresnel = pow(1.0 - facing, 3.2);
    float diffuse = max(dot(normal, lightDirection), 0.0);
    float specular = pow(max(dot(reflect(-lightDirection, normal), viewDirection), 0.0), 92.0);
    float shallowMix = smoothstep(-0.58, 0.62, vWaveHeight) * 0.56 + diffuse * 0.18;

    vec3 water = mix(uDeepColor, uSurfaceColor, clamp(shallowMix, 0.0, 1.0));
    vec3 reflectedSky = mix(uHorizonColor, vec3(0.24, 0.47, 0.56), clamp(normal.y * 0.7, 0.0, 1.0));
    vec3 color = mix(water, reflectedSky, fresnel * 0.72);
    color += specular * vec3(0.82, 0.98, 1.0) * 0.72;

    float crest = smoothstep(0.43, 0.74, vWaveHeight + vSlope * 0.13);
    float slopeFoam = crest * smoothstep(0.32, 0.82, vSlope) * 0.42;
    float foam = max(slopeFoam, clamp(vFoam, 0.0, 1.0) * 0.68);
    color = mix(color, vec3(0.72, 0.91, 0.92), foam);

    float localSlope = 0.22 + smoothstep(0.08, 0.62, vSlope) * 0.78;
    float localReflection = smoothstep(0.06, 0.58, vPointerInfluence) * localSlope;
    color = mix(color, vec3(0.25, 0.70, 0.70), localReflection * 0.32);

    float distanceFromCamera = length(cameraPosition - vWorldPosition);
    float horizonFog = smoothstep(25.0, 48.0, distanceFromCamera);
    color = mix(color, uHorizonColor, horizonFog * 0.72);
    gl_FragColor = vec4(color, 1.0);
  }
`;

const skyVertexShader = `
  varying vec3 vDirection;

  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vDirection = normalize(worldPosition.xyz - cameraPosition);
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const skyFragmentShader = `
  precision highp float;

  varying vec3 vDirection;

  void main() {
    float elevation = clamp(vDirection.y * 0.5 + 0.5, 0.0, 1.0);
    float horizon = pow(1.0 - abs(vDirection.y), 5.0);
    vec3 zenith = vec3(0.015, 0.055, 0.075);
    vec3 lowSky = vec3(0.10, 0.25, 0.30);
    vec3 color = mix(lowSky, zenith, smoothstep(0.28, 0.92, elevation));
    color += horizon * vec3(0.035, 0.085, 0.095);
    gl_FragColor = vec4(color, 1.0);
  }
`;

export function createOceanLab({ canvas, stage, profileName, maxDpr, model = "gerstner" }) {
  const segments = segmentProfiles[profileName] || segmentProfiles.balanced;
  const spectrum = buildJonswapComponents(segments.spectrumWaves);
  const useSpectrum = model === "spectrum";
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: profileName !== "eco",
    alpha: false,
    powerPreference: "high-performance"
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = useSpectrum ? 1.24 : 1.18;
  renderer.setClearColor(0x06141a, 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(43, 1, 0.1, 120);
  const lookTarget = new THREE.Vector3(0, 0.05, -7);

  const uniforms = {
    uTime: { value: 0 },
    uPointer: { value: new THREE.Vector2(1000, 1000) },
    uPointerStrength: { value: 0 },
    uDeepColor: { value: new THREE.Color(useSpectrum ? 0x052f42 : 0x063644) },
    uSurfaceColor: { value: new THREE.Color(useSpectrum ? 0x16828c : 0x178b96) },
    uHorizonColor: { value: new THREE.Color(useSpectrum ? 0x345b64 : 0x28515a) },
    uSpectrumWaves: { value: spectrum.waves },
    uSpectrumMotion: { value: spectrum.motion }
  };

  // 넓은 메시에서 스펙트럼 역합성 또는 Gerstner 변위를 계산하고 같은 조명 단계로 넘깁니다.
  const oceanGeometry = new THREE.PlaneGeometry(54, 54, segments.width, segments.depth);
  oceanGeometry.rotateX(-Math.PI * 0.5);
  const oceanMaterial = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: useSpectrum ? createSpectrumVertexShader(spectrum.count) : gerstnerVertexShader,
    fragmentShader: oceanFragmentShader,
    side: THREE.FrontSide
  });
  const ocean = new THREE.Mesh(oceanGeometry, oceanMaterial);
  scene.add(ocean);

  const skyGeometry = new THREE.SphereGeometry(72, 48, 24);
  const skyMaterial = new THREE.ShaderMaterial({
    vertexShader: skyVertexShader,
    fragmentShader: skyFragmentShader,
    side: THREE.BackSide,
    depthWrite: false
  });
  scene.add(new THREE.Mesh(skyGeometry, skyMaterial));

  const raycaster = new THREE.Raycaster();
  const pointerNdc = new THREE.Vector2();
  const pointerPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  const intersection = new THREE.Vector3();
  const localIntersection = new THREE.Vector3();
  const pointerTarget = new THREE.Vector2(1000, 1000);

  let simulationTime = 0;
  let paused = false;
  let pointerInside = false;
  let pointerImpulse = 0;
  let disposed = false;

  function resize() {
    const rect = stage.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    const compact = width <= 760;
    renderer.setPixelRatio(Math.min(maxDpr, window.devicePixelRatio || 1));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = compact ? 52 : 43;
    camera.position.set(0, compact ? 6.8 : 5.5, compact ? 11.6 : 11.2);
    lookTarget.set(0, compact ? 0.15 : 0.05, compact ? -5.2 : -7.0);
    camera.lookAt(lookTarget);
    camera.updateProjectionMatrix();
  }

  function setPointer(clientX, clientY, speed = 0) {
    const rect = stage.getBoundingClientRect();
    pointerNdc.set(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1
    );
    raycaster.setFromCamera(pointerNdc, camera);
    if (!raycaster.ray.intersectPlane(pointerPlane, intersection)) return false;
    localIntersection.copy(intersection);
    ocean.worldToLocal(localIntersection);
    pointerTarget.set(localIntersection.x, localIntersection.z);
    pointerInside = true;
    pointerImpulse = Math.max(pointerImpulse, THREE.MathUtils.clamp(speed / 850, 0, 1) * 0.9);
    return true;
  }

  function setPointerActive(active) {
    pointerInside = active;
  }

  function pulsePointer() {
    pointerImpulse = 1.15;
  }

  function setPaused(nextPaused) {
    paused = nextPaused;
  }

  function reset() {
    simulationTime = 0;
    pointerImpulse = 0;
    uniforms.uPointerStrength.value = 0;
  }

  function update(delta) {
    if (!paused) simulationTime += delta;
    uniforms.uTime.value = simulationTime;
    const pointerBlend = 1 - Math.exp(-delta * 10);
    uniforms.uPointer.value.lerp(pointerTarget, pointerBlend);
    pointerImpulse *= Math.exp(-delta * 3.8);
    const targetStrength = pointerInside ? 0.52 + pointerImpulse : 0;
    uniforms.uPointerStrength.value = THREE.MathUtils.lerp(
      uniforms.uPointerStrength.value,
      targetStrength,
      1 - Math.exp(-delta * 7)
    );
  }

  function render() {
    if (!disposed) renderer.render(scene, camera);
  }

  function dispose() {
    disposed = true;
    oceanGeometry.dispose();
    oceanMaterial.dispose();
    skyGeometry.dispose();
    skyMaterial.dispose();
    renderer.dispose();
  }

  resize();
  render();

  return {
    resize,
    setPointer,
    setPointerActive,
    pulsePointer,
    setPaused,
    reset,
    update,
    render,
    dispose,
    detail: useSpectrum
      ? `JONSWAP ${spectrum.count}파 · 3대역`
      : `Gerstner 수면 ${segments.width}×${segments.depth}`
  };
}
