import * as THREE from "./vendor/three.module.min.js";

const segmentProfiles = {
  high: { width: 220, depth: 160 },
  balanced: { width: 160, depth: 116 },
  eco: { width: 96, depth: 72 }
};

const oceanVertexShader = `
  precision highp float;

  uniform float uTime;
  uniform vec2 uPointer;
  uniform float uPointerStrength;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vPointerInfluence;

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

    vec2 pointerDelta = point - uPointer;
    float pointerDistance = length(pointerDelta);
    float pointerFalloff = exp(-pointerDistance * pointerDistance * 0.90);
    float pointerRing = smoothstep(0.18, 0.68, pointerDistance);
    float pointerPhase = pointerDistance * 5.4 - uTime * 7.2;
    float raisedRing = (0.5 + 0.5 * sin(pointerPhase)) * pointerRing;
    float pointerWave = raisedRing * 0.18 * uPointerStrength * pointerFalloff;
    pointerWave += exp(-pointerDistance * pointerDistance * 3.2) * 0.06 * uPointerStrength;
    height += pointerWave;

    if (pointerDistance > 0.001) {
      float waveDerivative = 0.09 * 5.4 * cos(pointerPhase) * pointerRing;
      float falloffDerivative = -1.80 * pointerDistance * raisedRing * 0.18;
      gradient += normalize(pointerDelta) * (waveDerivative + falloffDerivative) * uPointerStrength * pointerFalloff;
    }

    displaced.xz += horizontal;
    displaced.y += height;

    vec3 localNormal = normalize(vec3(-gradient.x, 1.0, -gradient.y));
    vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
    vWaveHeight = height;
    vSlope = length(gradient);
    vPointerInfluence = pointerFalloff * pointerRing * uPointerStrength;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const oceanFragmentShader = `
  precision highp float;

  uniform vec3 uDeepColor;
  uniform vec3 uSurfaceColor;
  uniform vec3 uHorizonColor;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vPointerInfluence;

  void main() {
    vec3 normal = normalize(vWorldNormal);
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
    float foam = crest * smoothstep(0.32, 0.82, vSlope) * 0.42;
    color = mix(color, vec3(0.72, 0.91, 0.92), foam);

    float localReflection = smoothstep(0.10, 0.76, vPointerInfluence) * smoothstep(0.16, 0.68, vSlope);
    color = mix(color, vec3(0.19, 0.62, 0.66), localReflection * 0.24);

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

export function createOceanLab({ canvas, stage, profileName, maxDpr }) {
  const segments = segmentProfiles[profileName] || segmentProfiles.balanced;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: profileName !== "eco",
    alpha: false,
    powerPreference: "high-performance"
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  renderer.setClearColor(0x06141a, 1);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(43, 1, 0.1, 120);
  const lookTarget = new THREE.Vector3(0, 0.05, -7);

  const uniforms = {
    uTime: { value: 0 },
    uPointer: { value: new THREE.Vector2(1000, 1000) },
    uPointerStrength: { value: 0 },
    uDeepColor: { value: new THREE.Color(0x063644) },
    uSurfaceColor: { value: new THREE.Color(0x178b96) },
    uHorizonColor: { value: new THREE.Color(0x28515a) }
  };

  // 넓은 메시의 꼭짓점 높이를 셰이더에서 바꿔 파도 진행과 국소 변형을 함께 계산합니다.
  const oceanGeometry = new THREE.PlaneGeometry(54, 54, segments.width, segments.depth);
  oceanGeometry.rotateX(-Math.PI * 0.5);
  const oceanMaterial = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: oceanVertexShader,
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
    const targetStrength = pointerInside ? 0.34 + pointerImpulse : 0;
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
    detail: `3D 수면 ${segments.width}×${segments.depth}`
  };
}
