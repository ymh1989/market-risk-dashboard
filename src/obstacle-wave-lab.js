import * as THREE from "./vendor/three.module.min.js";

const TANK_LENGTH = 36;
const TANK_WIDTH = 14;
const WATER_LEVEL = 0;
const BED_LEVEL = -0.9;
const OBSTACLE_HALF_X = 1.05;
const OBSTACLE_HALF_Z = 1.45;
const IMPACT_FACE_X = -OBSTACLE_HALF_X - 0.08;
const WAVE_NUMBER = 0.82;
const WAVE_FREQUENCY = 2.08;

const simulationProfiles = {
  high: { segmentsX: 240, segmentsZ: 120, spray: 420, pointSize: 3.8, impactProbes: 13 },
  balanced: { segmentsX: 176, segmentsZ: 88, spray: 280, pointSize: 3.4, impactProbes: 11 },
  eco: { segmentsX: 112, segmentsZ: 56, spray: 150, pointSize: 3.0, impactProbes: 7 }
};

function clamp01(value) {
  return THREE.MathUtils.clamp(value, 0, 1);
}

function incidentWaveHeight(x, z, time) {
  const phase = x * WAVE_NUMBER - time * WAVE_FREQUENCY;
  return (
    Math.sin(phase) * 0.43 +
    Math.sin(phase * 1.93 + z * 0.16 + 0.8) * 0.11 +
    Math.sin(phase * 3.45 - z * 0.34) * 0.035
  );
}

function pointerSurfaceHeight(x, z, time, pointer, pointerStrength) {
  if (pointerStrength <= 0) return 0;
  const dx = x - pointer.x;
  const dz = z - pointer.y;
  const distance = Math.hypot(dx, dz);
  const envelope = Math.exp(-distance * 0.72);
  const ring = Math.sin(distance * 4.7 - time * 6.4) *
    envelope * pointerStrength * 0.18;
  const lift = Math.exp(-(dx * dx + dz * dz) * 1.25) *
    pointerStrength * 0.26;
  return ring + lift;
}

function sampleImpactState(time, z, pointer, pointerStrength) {
  const step = 1 / 90;
  const height = incidentWaveHeight(IMPACT_FACE_X, z, time);
  const previousHeight = incidentWaveHeight(IMPACT_FACE_X, z, time - step);
  const verticalVelocity = (height - previousHeight) / step;
  const xStep = 0.08;
  const zStep = 0.08;
  const slopeX = (
    incidentWaveHeight(IMPACT_FACE_X + xStep, z, time) -
    incidentWaveHeight(IMPACT_FACE_X - xStep, z, time)
  ) / (xStep * 2);
  const slopeZ = (
    incidentWaveHeight(IMPACT_FACE_X, z + zStep, time) -
    incidentWaveHeight(IMPACT_FACE_X, z - zStep, time)
  ) / (zStep * 2);
  const crest = clamp01((height - 0.08) / 0.46);
  const rising = clamp01((verticalVelocity - 0.04) / 0.9);
  const steepness = clamp01(Math.hypot(slopeX, slopeZ) / 0.72);
  const pointerDistance = Math.hypot(IMPACT_FACE_X - pointer.x, z - pointer.y);
  const pointerCoupling = Math.exp(-pointerDistance * pointerDistance * 0.34) *
    pointerStrength;
  const energy = clamp01(
    crest * rising * (0.72 + steepness * 0.28) * (1 + pointerCoupling * 0.42)
  );

  return {
    z,
    height,
    verticalVelocity,
    slopeZ,
    pointerCoupling,
    energy
  };
}

function surfaceHeightAt(x, z, time, pointer, pointerStrength) {
  const downstream = THREE.MathUtils.smoothstep(x, 0.25, 3.8);
  const centerShadow = Math.exp(-z * z * 0.12) * downstream;
  const incoming = incidentWaveHeight(x, z, time) * (1 - centerShadow * 0.48);
  return incoming + pointerSurfaceHeight(x, z, time, pointer, pointerStrength);
}

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

  float incidentWave(vec2 point, float time) {
    float phase = point.x * ${WAVE_NUMBER.toFixed(2)} - time * ${WAVE_FREQUENCY.toFixed(2)};
    return
      sin(phase) * 0.43 +
      sin(phase * 1.93 + point.y * 0.16 + 0.8) * 0.11 +
      sin(phase * 3.45 - point.y * 0.34) * 0.035;
  }

  float faceImpactEnergy(float z) {
    vec2 facePoint = vec2(${IMPACT_FACE_X.toFixed(2)}, z);
    float height = incidentWave(facePoint, uTime);
    float previousHeight = incidentWave(facePoint, uTime - 0.0111);
    float verticalVelocity = (height - previousHeight) / 0.0111;
    float crest = smoothstep(0.08, 0.54, height);
    float rising = smoothstep(0.04, 0.94, verticalVelocity);
    float pointerDistance = distance(facePoint, uPointer);
    float pointerCoupling = exp(-pointerDistance * pointerDistance * 0.34) *
      uPointerStrength;
    return clamp(crest * rising * (1.0 + pointerCoupling * 0.42), 0.0, 1.0);
  }

  float waveHeight(vec2 point) {
    float channelEdge = 1.0 - smoothstep(6.45, 7.0, abs(point.y));
    float incoming = incidentWave(point, uTime);

    float downstream = smoothstep(0.25, 3.8, point.x);
    float centerShadow = exp(-point.y * point.y * 0.12) * downstream;
    incoming *= 1.0 - centerShadow * 0.48;

    float collisionEnergy = faceImpactEnergy(point.y);
    float impactX = exp(-square((point.x - ${IMPACT_FACE_X.toFixed(2)}) * 1.28));
    float obstacleFace = 1.0 - smoothstep(1.32, 1.62, abs(point.y));
    float impactFocus = 0.72 + 0.28 * exp(-square((point.y - uImpactZ) * 0.72));
    float collisionJet = impactX * obstacleFace * impactFocus *
      collisionEnergy * 1.34;

    float upstreamPile = exp(-square((point.x + 1.65) * 0.72)) *
      obstacleFace * collisionEnergy * 0.62;

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

    float impactMask = exp(-square((point.x - ${IMPACT_FACE_X.toFixed(2)}) * 1.08)) *
      (1.0 - smoothstep(1.32, 1.62, abs(point.y)));
    float pointerDistance = length(point - uPointer);

    displaced.y += height;
    vec4 worldPosition = modelMatrix * vec4(displaced, 1.0);
    vWorldPosition = worldPosition.xyz;
    vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
    vWaveHeight = height;
    vSlope = length(vec2(slopeX, slopeZ));
    vImpact = impactMask * faceImpactEnergy(point.y);
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
    age: 0,
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
    blending: THREE.NormalBlending
  });
  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;

  let cursor = 0;
  let randomState = 0x7f4a7c15;

  function random() {
    randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0;
    return randomState / 4294967296;
  }

  function spawn(impact) {
    const particle = particles[cursor];
    cursor = (cursor + 1) % particles.length;
    const energy = clamp01(impact.energy);
    const edgeDirection = THREE.MathUtils.clamp(
      impact.z / OBSTACLE_HALF_Z,
      -1,
      1
    );
    const jitter = random() - 0.5;
    particle.active = true;
    particle.position.set(
      IMPACT_FACE_X - 0.03 - random() * 0.1,
      WATER_LEVEL + Math.max(0.08, impact.height + energy * 0.34) + random() * 0.05,
      THREE.MathUtils.clamp(
        impact.z + jitter * (0.08 + energy * 0.2),
        -OBSTACLE_HALF_Z,
        OBSTACLE_HALF_Z
      )
    );
    particle.velocity.set(
      -(0.34 + energy * 1.65 + random() * 0.24),
      0.55 + energy * 3.05 + Math.max(0, impact.verticalVelocity) * 0.35 +
        random() * 0.38,
      edgeDirection * (0.18 + energy * 0.82) -
        impact.slopeZ * 0.28 + jitter * (0.25 + energy * 0.48)
    );
    particle.age = 0;
    particle.duration = 0.42 + energy * 0.46 + random() * 0.2;
    particle.life = particle.duration;
  }

  function reset() {
    randomState = 0x7f4a7c15;
    particles.forEach((particle, index) => {
      particle.active = false;
      particle.age = 0;
      particle.life = 0;
      positionAttribute.setXYZ(index, 0, -100, 0);
      lifeAttribute.setX(index, 0);
    });
    positionAttribute.needsUpdate = true;
    lifeAttribute.needsUpdate = true;
  }

  function update(delta, time, pointer, pointerStrength) {
    particles.forEach((particle, index) => {
      if (!particle.active) {
        positionAttribute.setXYZ(index, 0, -100, 0);
        lifeAttribute.setX(index, 0);
        return;
      }
      particle.age += delta;
      particle.life -= delta;
      const surfaceHeight = WATER_LEVEL + surfaceHeightAt(
        particle.position.x,
        particle.position.z,
        time,
        pointer,
        pointerStrength
      );
      const reenteredWater = particle.age > 0.08 &&
        particle.velocity.y < 0 &&
        particle.position.y <= surfaceHeight + 0.025;
      if (particle.life <= 0 || reenteredWater || particle.position.y < BED_LEVEL) {
        particle.active = false;
        positionAttribute.setXYZ(index, 0, -100, 0);
        lifeAttribute.setX(index, 0);
        return;
      }
      particle.velocity.y -= 9.1 * delta;
      particle.velocity.x *= Math.exp(-delta * 0.72);
      particle.velocity.z *= Math.exp(-delta * 0.92);
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
    opacity: 0.28
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
  let impactSequence = 0;
  let disposed = false;
  const impactProbeZ = Array.from(
    { length: profile.impactProbes },
    (_, index) => THREE.MathUtils.lerp(
      -OBSTACLE_HALF_Z * 0.92,
      OBSTACLE_HALF_Z * 0.92,
      index / Math.max(1, profile.impactProbes - 1)
    )
  );

  function resize() {
    const rect = stage.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    const compact = width <= 760;
    renderer.setPixelRatio(Math.min(maxDpr, window.devicePixelRatio || 1));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = compact ? 50 : 42;
    camera.position.set(
      compact ? -15.8 : -12.6,
      compact ? 10.4 : 6.8,
      compact ? 21.8 : 17.0
    );
    lookTarget.set(-0.6, compact ? 0.18 : 0.12, compact ? -0.15 : 0);
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
    return true;
  }

  function setPointerActive(active) {
    pointerInside = active;
    if (!active) pointerNearObstacle = false;
  }

  function pulsePointer() {
    pointerImpulse = 1.35;
  }

  function setPaused(nextPaused) {
    paused = nextPaused;
  }

  function reset() {
    simulationTime = 0;
    pointerImpulse = 0;
    impactTargetZ = 0;
    spawnBudget = 0;
    impactSequence = 0;
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

    const impactStates = impactProbeZ.map((probeZ) => sampleImpactState(
      simulationTime,
      probeZ,
      uniforms.uPointer.value,
      uniforms.uPointerStrength.value
    ));
    const totalImpactEnergy = impactStates.reduce(
      (sum, impact) => sum + impact.energy,
      0
    );
    const meanImpactEnergy = totalImpactEnergy / impactStates.length;
    const emissionRate = profileName === "eco" ? 108 : 168;
    spawnBudget += delta * emissionRate * Math.pow(meanImpactEnergy, 1.18);
    while (spawnBudget >= 1) {
      const selection = totalImpactEnergy > 0
        ? ((impactSequence * 0.61803398875) % 1) * totalImpactEnergy
        : 0;
      let cumulativeEnergy = 0;
      let selectedImpact = impactStates[Math.floor(impactStates.length * 0.5)];
      for (const impact of impactStates) {
        cumulativeEnergy += impact.energy;
        if (cumulativeEnergy >= selection) {
          selectedImpact = impact;
          break;
        }
      }
      spray.spawn(selectedImpact);
      impactSequence += 1;
      spawnBudget -= 1;
    }
    spray.update(
      delta,
      simulationTime,
      uniforms.uPointer.value,
      uniforms.uPointerStrength.value
    );
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
    detail: `진행파·고정 장애물 · 충돌면 ${profile.impactProbes}점 연동 · 수면 ${profile.segmentsX}×${profile.segmentsZ}`
  };
}
