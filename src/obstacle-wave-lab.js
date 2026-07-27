import * as THREE from "./vendor/three.module.min.js";

const TANK_LENGTH = 36;
const TANK_WIDTH = 14;
const WATER_LEVEL = 0;
const BED_LEVEL = -0.9;
const OBSTACLE_HALF_X = 1.05;
const OBSTACLE_HALF_Z = 1.45;

const simulationProfiles = {
  high: { segmentsX: 240, segmentsZ: 120, spray: 420, pointSize: 4.8 },
  balanced: { segmentsX: 176, segmentsZ: 88, spray: 280, pointSize: 4.4 },
  eco: { segmentsX: 112, segmentsZ: 56, spray: 150, pointSize: 4.0 }
};

const waterVertexShader = `
  precision highp float;

  uniform float uTime;
  uniform vec2 uPointer;
  uniform float uPointerStrength;
  uniform float uImpactZ;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vImpact;
  varying float vPointer;

  float square(float value) {
    return value * value;
  }

  float waveHeight(vec2 point) {
    float channelEdge = 1.0 - smoothstep(6.45, 7.0, abs(point.y));
    float phase = point.x * 0.82 - uTime * 2.08;
    float incoming =
      sin(phase) * 0.43 +
      sin(phase * 1.93 + point.y * 0.16 + 0.8) * 0.11 +
      sin(phase * 3.45 - point.y * 0.34) * 0.035;

    float downstream = smoothstep(0.25, 3.8, point.x);
    float centerShadow = exp(-point.y * point.y * 0.12) * downstream;
    incoming *= 1.0 - centerShadow * 0.48;

    float impactCycle = pow(max(0.0, sin(uTime * 2.08 + 0.18)), 5.0);
    float impactX = exp(-square((point.x + 0.9) * 1.25));
    float impactZ = exp(-square((point.y - uImpactZ) * 0.72));
    float collisionJet = impactX * impactZ * impactCycle *
      (1.5 + uPointerStrength * 0.7);

    float upstreamPile = exp(-square((point.x + 1.65) * 0.72)) *
      exp(-square((point.y - uImpactZ) * 0.42)) *
      max(0.0, sin(phase + 0.75)) * 0.66;

    vec2 upperEdge = vec2(point.x - 0.25, point.y - 1.65);
    vec2 lowerEdge = vec2(point.x - 0.25, point.y + 1.65);
    float upperRadius = length(upperEdge);
    float lowerRadius = length(lowerEdge);
    float diffractionGate = smoothstep(-0.2, 1.5, point.x);
    float diffraction =
      sin(upperRadius * 1.42 - uTime * 2.18) * exp(-upperRadius * 0.085) +
      sin(lowerRadius * 1.42 - uTime * 2.18 + 0.5) * exp(-lowerRadius * 0.085);
    diffraction *= diffractionGate * 0.13;

    float wake = sin(point.x * 1.18 - uTime * 2.24 + abs(point.y) * 0.62) *
      exp(-point.y * point.y * 0.08) * downstream * 0.12;

    vec2 pointerDelta = point - uPointer;
    float pointerDistance = length(pointerDelta);
    float pointerEnvelope = exp(-pointerDistance * 0.72);
    float pointerRing = sin(pointerDistance * 4.7 - uTime * 6.4) *
      pointerEnvelope * uPointerStrength * 0.18;
    float pointerLift = exp(-pointerDistance * pointerDistance * 1.25) *
      uPointerStrength * 0.26;

    float obstacleInterior =
      (1.0 - smoothstep(${OBSTACLE_HALF_X.toFixed(2)}, ${(OBSTACLE_HALF_X + 0.18).toFixed(2)}, abs(point.x))) *
      (1.0 - smoothstep(${OBSTACLE_HALF_Z.toFixed(2)}, ${(OBSTACLE_HALF_Z + 0.18).toFixed(2)}, abs(point.y)));

    float height = (incoming + collisionJet + upstreamPile + diffraction + wake +
      pointerRing + pointerLift) * channelEdge;
    return mix(height, -0.42, obstacleInterior);
  }

  void main() {
    vec3 displaced = position;
    vec2 point = position.xz;
    float height = waveHeight(point);
    float epsilon = 0.08;
    float slopeX = (waveHeight(point + vec2(epsilon, 0.0)) -
      waveHeight(point - vec2(epsilon, 0.0))) / (epsilon * 2.0);
    float slopeZ = (waveHeight(point + vec2(0.0, epsilon)) -
      waveHeight(point - vec2(0.0, epsilon))) / (epsilon * 2.0);
    vec3 localNormal = normalize(vec3(-slopeX, 1.0, -slopeZ));

    float impactCycle = pow(max(0.0, sin(uTime * 2.08 + 0.18)), 4.0);
    float impactMask = exp(-square((point.x + 0.9) * 1.08)) *
      exp(-square((point.y - uImpactZ) * 0.64));
    float pointerDistance = length(point - uPointer);

    displaced.y += height;
    vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
    vWaveHeight = height;
    vSlope = length(vec2(slopeX, slopeZ));
    vImpact = impactMask * impactCycle;
    vPointer = exp(-pointerDistance * pointerDistance * 0.48) * uPointerStrength;
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const waterFragmentShader = `
  precision highp float;

  uniform float uTime;

  varying vec3 vWorldPosition;
  varying vec3 vWorldNormal;
  varying float vWaveHeight;
  varying float vSlope;
  varying float vImpact;
  varying float vPointer;

  void main() {
    vec3 normal = normalize(vWorldNormal);
    vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
    vec3 lightDirection = normalize(vec3(-0.42, 0.82, 0.36));
    float diffuse = max(dot(normal, lightDirection), 0.0);
    float fresnel = pow(1.0 - max(dot(normal, viewDirection), 0.0), 3.2);
    float depthTint = smoothstep(-0.45, 0.72, vWaveHeight);

    vec3 deepColor = vec3(0.018, 0.20, 0.29);
    vec3 surfaceColor = vec3(0.035, 0.49, 0.61);
    vec3 horizonColor = vec3(0.32, 0.72, 0.77);
    vec3 color = mix(deepColor, surfaceColor, depthTint);
    color = mix(color, horizonColor, fresnel * 0.58);
    color += diffuse * vec3(0.055, 0.11, 0.12);

    float foamTexture =
      0.5 + 0.5 * sin(vWorldPosition.x * 5.2 + vWorldPosition.z * 3.7 - uTime * 4.8);
    float slopeFoam = smoothstep(0.72, 1.55, vSlope);
    float foam = clamp(
      slopeFoam * (0.48 + foamTexture * 0.32) +
      vImpact * 0.92 +
      vPointer * 0.28,
      0.0,
      1.0
    );
    color = mix(color, vec3(0.78, 0.94, 0.95), foam);
    gl_FragColor = vec4(color, 0.98);
  }
`;

const skyVertexShader = `
  varying vec3 vDirection;

  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vDirection = normalize(worldPosition.xyz - cameraPosition);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const skyFragmentShader = `
  precision highp float;

  varying vec3 vDirection;

  void main() {
    float elevation = clamp(vDirection.y * 0.5 + 0.5, 0.0, 1.0);
    float horizon = pow(1.0 - abs(vDirection.y), 4.5);
    vec3 lowSky = vec3(0.10, 0.25, 0.30);
    vec3 highSky = vec3(0.018, 0.055, 0.075);
    vec3 color = mix(lowSky, highSky, smoothstep(0.2, 0.92, elevation));
    color += horizon * vec3(0.035, 0.085, 0.09);
    gl_FragColor = vec4(color, 1.0);
  }
`;

const sprayVertexShader = `
  precision highp float;

  uniform float uPointSize;
  attribute float aLife;
  varying float vAlpha;

  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    float lifeShape = sin(clamp(aLife, 0.0, 1.0) * 3.14159265);
    vAlpha = smoothstep(0.0, 0.18, lifeShape);
    gl_PointSize = uPointSize * (0.62 + lifeShape * 0.82) *
      clamp(170.0 / max(1.0, -viewPosition.z), 0.7, 2.7);
    gl_Position = projectionMatrix * viewPosition;
  }
`;

const sprayFragmentShader = `
  precision highp float;

  varying float vAlpha;

  void main() {
    vec2 centered = gl_PointCoord - 0.5;
    float distanceFromCenter = length(centered);
    float alpha = smoothstep(0.5, 0.08, distanceFromCenter) * vAlpha * 0.82;
    if (alpha < 0.015) discard;
    vec3 color = mix(vec3(0.38, 0.78, 0.84), vec3(0.92, 0.99, 1.0), vAlpha);
    gl_FragColor = vec4(color, alpha);
  }
`;

function createSpraySystem(profile) {
  const particles = Array.from({ length: profile.spray }, () => ({
    active: false,
    position: new THREE.Vector3(0, -100, 0),
    velocity: new THREE.Vector3(),
    life: 0,
    duration: 1
  }));
  const positions = new Float32Array(profile.spray * 3);
  const lives = new Float32Array(profile.spray);
  positions.fill(-100);

  const geometry = new THREE.BufferGeometry();
  const positionAttribute = new THREE.BufferAttribute(positions, 3);
  const lifeAttribute = new THREE.BufferAttribute(lives, 1);
  positionAttribute.setUsage(THREE.DynamicDrawUsage);
  lifeAttribute.setUsage(THREE.DynamicDrawUsage);
  geometry.setAttribute("position", positionAttribute);
  geometry.setAttribute("aLife", lifeAttribute);

  const material = new THREE.ShaderMaterial({
    uniforms: {
      uPointSize: { value: profile.pointSize }
    },
    vertexShader: sprayVertexShader,
    fragmentShader: sprayFragmentShader,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;

  let cursor = 0;

  function spawn(impactZ, strength = 1) {
    const particle = particles[cursor];
    cursor = (cursor + 1) % particles.length;
    const side = Math.random() < 0.5 ? -1 : 1;
    const spread = (Math.random() - 0.5) * 1.8;
    particle.active = true;
    particle.position.set(
      -0.92 + (Math.random() - 0.5) * 0.36,
      WATER_LEVEL + 0.34 + Math.random() * 0.28,
      THREE.MathUtils.clamp(impactZ + spread, -OBSTACLE_HALF_Z, OBSTACLE_HALF_Z)
    );
    particle.velocity.set(
      0.9 + Math.random() * 2.7,
      (2.2 + Math.random() * 4.6) * strength,
      side * (0.5 + Math.random() * 2.1) + spread * 0.28
    );
    particle.duration = 0.62 + Math.random() * 0.78;
    particle.life = particle.duration;
  }

  function burst(impactZ, count, strength = 1) {
    for (let index = 0; index < count; index += 1) spawn(impactZ, strength);
  }

  function reset() {
    particles.forEach((particle, index) => {
      particle.active = false;
      particle.life = 0;
      positionAttribute.setXYZ(index, 0, -100, 0);
      lifeAttribute.setX(index, 0);
    });
    positionAttribute.needsUpdate = true;
    lifeAttribute.needsUpdate = true;
  }

  function update(delta) {
    particles.forEach((particle, index) => {
      if (!particle.active) {
        positionAttribute.setXYZ(index, 0, -100, 0);
        lifeAttribute.setX(index, 0);
        return;
      }
      particle.life -= delta;
      if (particle.life <= 0 || particle.position.y < BED_LEVEL) {
        particle.active = false;
        positionAttribute.setXYZ(index, 0, -100, 0);
        lifeAttribute.setX(index, 0);
        return;
      }
      particle.velocity.y -= 7.7 * delta;
      particle.velocity.x *= Math.exp(-delta * 0.42);
      particle.velocity.z *= Math.exp(-delta * 0.74);
      particle.position.addScaledVector(particle.velocity, delta);
      positionAttribute.setXYZ(index, particle.position.x, particle.position.y, particle.position.z);
      lifeAttribute.setX(index, particle.life / particle.duration);
    });
    positionAttribute.needsUpdate = true;
    lifeAttribute.needsUpdate = true;
  }

  return {
    points,
    geometry,
    material,
    spawn,
    burst,
    reset,
    update
  };
}

function createTank(scene) {
  const resources = { geometries: [], materials: [] };

  const bedGeometry = new THREE.PlaneGeometry(TANK_LENGTH, TANK_WIDTH);
  bedGeometry.rotateX(-Math.PI * 0.5);
  const bedMaterial = new THREE.MeshStandardMaterial({
    color: 0x10272c,
    roughness: 0.94,
    metalness: 0.03
  });
  const bed = new THREE.Mesh(bedGeometry, bedMaterial);
  bed.position.y = BED_LEVEL;
  scene.add(bed);
  resources.geometries.push(bedGeometry);
  resources.materials.push(bedMaterial);

  const grid = new THREE.GridHelper(TANK_LENGTH, 24, 0x2c6570, 0x1a3e45);
  grid.scale.z = TANK_WIDTH / TANK_LENGTH;
  grid.position.y = BED_LEVEL + 0.012;
  grid.material.transparent = true;
  grid.material.opacity = 0.26;
  scene.add(grid);
  resources.geometries.push(grid.geometry);
  resources.materials.push(grid.material);

  const tankGeometry = new THREE.BoxGeometry(TANK_LENGTH, 6.2, TANK_WIDTH);
  const tankEdgesGeometry = new THREE.EdgesGeometry(tankGeometry);
  const tankEdgesMaterial = new THREE.LineBasicMaterial({
    color: 0x86b4bb,
    transparent: true,
    opacity: 0.42
  });
  const tankEdges = new THREE.LineSegments(tankEdgesGeometry, tankEdgesMaterial);
  tankEdges.position.y = BED_LEVEL + 3.1;
  scene.add(tankEdges);
  resources.geometries.push(tankGeometry, tankEdgesGeometry);
  resources.materials.push(tankEdgesMaterial);

  const obstacleGeometry = new THREE.BoxGeometry(
    OBSTACLE_HALF_X * 2,
    3.15,
    OBSTACLE_HALF_Z * 2
  );
  const obstacleMaterial = new THREE.MeshStandardMaterial({
    color: 0x11191b,
    roughness: 0.66,
    metalness: 0.48
  });
  const obstacle = new THREE.Mesh(obstacleGeometry, obstacleMaterial);
  obstacle.position.y = BED_LEVEL + 1.575;
  scene.add(obstacle);
  resources.geometries.push(obstacleGeometry);
  resources.materials.push(obstacleMaterial);

  const obstacleEdgesGeometry = new THREE.EdgesGeometry(obstacleGeometry);
  const obstacleEdgesMaterial = new THREE.LineBasicMaterial({
    color: 0xa5c6ca,
    transparent: true,
    opacity: 0.58
  });
  const obstacleEdges = new THREE.LineSegments(obstacleEdgesGeometry, obstacleEdgesMaterial);
  obstacleEdges.position.copy(obstacle.position);
  scene.add(obstacleEdges);
  resources.geometries.push(obstacleEdgesGeometry);
  resources.materials.push(obstacleEdgesMaterial);

  return resources;
}

export function createObstacleWaveLab({ canvas, stage, profileName, maxDpr }) {
  const profile = simulationProfiles[profileName] || simulationProfiles.balanced;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: profileName !== "eco",
    alpha: false,
    powerPreference: "high-performance"
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  renderer.setClearColor(0x061319, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0a2229, 0.012);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 130);
  const lookTarget = new THREE.Vector3(0, 0.35, 0);

  const hemisphere = new THREE.HemisphereLight(0xb9e3e4, 0x071316, 1.65);
  scene.add(hemisphere);
  const keyLight = new THREE.DirectionalLight(0xd8fbf6, 2.5);
  keyLight.position.set(-12, 18, 11);
  scene.add(keyLight);

  const skyGeometry = new THREE.SphereGeometry(78, 42, 22);
  const skyMaterial = new THREE.ShaderMaterial({
    vertexShader: skyVertexShader,
    fragmentShader: skyFragmentShader,
    side: THREE.BackSide,
    depthWrite: false
  });
  scene.add(new THREE.Mesh(skyGeometry, skyMaterial));

  const tankResources = createTank(scene);
  const uniforms = {
    uTime: { value: 0 },
    uPointer: { value: new THREE.Vector2(1000, 1000) },
    uPointerStrength: { value: 0 },
    uImpactZ: { value: 0 }
  };
  const waterGeometry = new THREE.PlaneGeometry(
    TANK_LENGTH,
    TANK_WIDTH,
    profile.segmentsX,
    profile.segmentsZ
  );
  waterGeometry.rotateX(-Math.PI * 0.5);
  const waterMaterial = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: waterVertexShader,
    fragmentShader: waterFragmentShader,
    side: THREE.DoubleSide,
    transparent: true
  });
  const water = new THREE.Mesh(waterGeometry, waterMaterial);
  water.position.y = WATER_LEVEL;
  scene.add(water);

  const spray = createSpraySystem(profile);
  scene.add(spray.points);

  const raycaster = new THREE.Raycaster();
  const pointerNdc = new THREE.Vector2();
  const pointerPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -WATER_LEVEL);
  const intersection = new THREE.Vector3();
  const pointerTarget = new THREE.Vector2(1000, 1000);

  let simulationTime = 0;
  let paused = false;
  let pointerInside = false;
  let pointerNearObstacle = false;
  let pointerImpulse = 0;
  let impactTargetZ = 0;
  let spawnBudget = 0;
  let burstPending = 0;
  let disposed = false;

  function resize() {
    const rect = stage.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    const compact = width <= 760;
    renderer.setPixelRatio(Math.min(maxDpr, window.devicePixelRatio || 1));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = compact ? 49 : 40;
    camera.position.set(
      compact ? 16.8 : 18.5,
      compact ? 10.8 : 10.2,
      compact ? 22.5 : 24.5
    );
    lookTarget.set(0, compact ? 0.28 : 0.35, compact ? -0.4 : 0);
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
    pointerTarget.set(
      THREE.MathUtils.clamp(intersection.x, -TANK_LENGTH * 0.5, TANK_LENGTH * 0.5),
      THREE.MathUtils.clamp(intersection.z, -TANK_WIDTH * 0.5, TANK_WIDTH * 0.5)
    );
    pointerInside = true;
    pointerNearObstacle =
      Math.abs(pointerTarget.x) < 4.2 && Math.abs(pointerTarget.y) < 4.2;
    if (pointerNearObstacle) {
      impactTargetZ = THREE.MathUtils.clamp(
        pointerTarget.y,
        -OBSTACLE_HALF_Z,
        OBSTACLE_HALF_Z
      );
    }
    const speedImpulse = THREE.MathUtils.clamp(speed / 850, 0, 1);
    pointerImpulse = Math.max(pointerImpulse, speedImpulse * (pointerNearObstacle ? 1.15 : 0.82));
    if (pointerNearObstacle && speedImpulse > 0.46) {
      burstPending = Math.max(burstPending, Math.round(speedImpulse * 10));
    }
    return true;
  }

  function setPointerActive(active) {
    pointerInside = active;
    if (!active) pointerNearObstacle = false;
  }

  function pulsePointer() {
    pointerImpulse = 1.35;
    if (pointerNearObstacle) {
      burstPending = Math.max(burstPending, 46);
    }
  }

  function setPaused(nextPaused) {
    paused = nextPaused;
  }

  function reset() {
    simulationTime = 0;
    pointerImpulse = 0;
    impactTargetZ = 0;
    uniforms.uPointerStrength.value = 0;
    uniforms.uImpactZ.value = 0;
    spray.reset();
  }

  function update(delta) {
    if (paused || delta <= 0) return;
    simulationTime += delta;
    uniforms.uTime.value = simulationTime;

    const pointerBlend = 1 - Math.exp(-delta * 11);
    uniforms.uPointer.value.lerp(pointerTarget, pointerBlend);
    pointerImpulse *= Math.exp(-delta * 3.4);
    const targetStrength = pointerInside ? 0.42 + pointerImpulse : 0;
    uniforms.uPointerStrength.value = THREE.MathUtils.lerp(
      uniforms.uPointerStrength.value,
      targetStrength,
      1 - Math.exp(-delta * 7.5)
    );
    uniforms.uImpactZ.value = THREE.MathUtils.lerp(
      uniforms.uImpactZ.value,
      pointerNearObstacle ? impactTargetZ : 0,
      1 - Math.exp(-delta * 4.2)
    );

    const impactCrest = Math.pow(Math.max(0, Math.sin(simulationTime * 2.08 + 0.18)), 5);
    spawnBudget += delta * (2 + impactCrest * (profileName === "eco" ? 54 : 86));
    if (pointerNearObstacle) {
      spawnBudget += delta * uniforms.uPointerStrength.value * 24;
    }
    while (spawnBudget >= 1) {
      spray.spawn(uniforms.uImpactZ.value, 0.7 + impactCrest * 0.72);
      spawnBudget -= 1;
    }
    if (burstPending > 0) {
      spray.burst(
        pointerNearObstacle ? uniforms.uImpactZ.value : pointerTarget.y,
        burstPending,
        pointerNearObstacle ? 1.2 : 0.72
      );
      burstPending = 0;
    }
    spray.update(delta);
  }

  function render() {
    if (!disposed) renderer.render(scene, camera);
  }

  function dispose() {
    disposed = true;
    stage.removeAttribute("data-obstacle-wave-ready");
    waterGeometry.dispose();
    waterMaterial.dispose();
    skyGeometry.dispose();
    skyMaterial.dispose();
    spray.geometry.dispose();
    spray.material.dispose();
    tankResources.geometries.forEach((geometry) => geometry.dispose());
    tankResources.materials.forEach((material) => material.dispose());
    renderer.dispose();
  }

  stage.setAttribute("data-obstacle-wave-ready", "true");
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
    detail: `진행파·고정 장애물 · 수면 ${profile.segmentsX}×${profile.segmentsZ} · 물보라 ${profile.spray}`
  };
}
