import * as THREE from "./vendor/three.module.min.js";

const forestProfiles = {
  high: { trees: 1900, terrainX: 112, terrainZ: 88, crownSegments: 9 },
  balanced: { trees: 1250, terrainX: 88, terrainZ: 68, crownSegments: 8 },
  eco: { trees: 720, terrainX: 64, terrainZ: 48, crownSegments: 7 }
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

function terrainHeight(x, z) {
  const depth = THREE.MathUtils.clamp((-z - 4) / 78, 0, 1);
  const broadRidge = Math.sin(x * 0.074 + z * 0.018) * (0.8 + depth * 1.7);
  const brokenSlope = Math.sin(x * 0.19 - z * 0.052) * 0.48;
  const sideMountain = Math.exp(-Math.pow((x + 19) / 22, 2)) * (2.8 + depth * 2.4);
  const rightShoulder = Math.exp(-Math.pow((x - 28) / 18, 2)) * (1.8 + depth * 2.1);
  return -2.1 + depth * 7.6 + broadRidge + brokenSlope + sideMountain + rightShoulder;
}

function createTerrain(profile) {
  const geometry = new THREE.PlaneGeometry(112, 90, profile.terrainX, profile.terrainZ);
  geometry.rotateX(-Math.PI * 0.5);
  const position = geometry.attributes.position;
  for (let index = 0; index < position.count; index += 1) {
    const x = position.getX(index);
    const localZ = position.getZ(index);
    position.setY(index, terrainHeight(x, localZ - 38));
  }
  geometry.translate(0, 0, -38);
  geometry.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0x234a32,
    roughness: 1,
    metalness: 0
  });
  const terrain = new THREE.Mesh(geometry, material);
  terrain.receiveShadow = false;
  return { terrain, geometry, material };
}

function createRidgeGeometry({ z, baseline, amplitude, phase, detail }) {
  const positions = [];
  const width = 150;
  const bottom = -14;
  for (let index = 0; index < detail; index += 1) {
    const progress = index / (detail - 1);
    const x = -width * 0.5 + progress * width;
    const ridge =
      baseline +
      Math.sin(progress * Math.PI * 3.2 + phase) * amplitude +
      Math.sin(progress * Math.PI * 8.4 - phase * 0.7) * amplitude * 0.28 +
      Math.sin(progress * Math.PI * 17.2 + phase * 1.7) * amplitude * 0.08;
    positions.push(x, bottom, z, x, ridge, z);
  }

  const indices = [];
  for (let index = 0; index < detail - 1; index += 1) {
    const lower = index * 2;
    indices.push(lower, lower + 1, lower + 3, lower, lower + 3, lower + 2);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function installWindShader(material, windUniforms) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uForestTime = windUniforms.time;
    shader.uniforms.uForestPointer = windUniforms.pointer;
    shader.uniforms.uForestPointerStrength = windUniforms.pointerStrength;
    shader.vertexShader = shader.vertexShader
      .replace(
        "#include <common>",
        `#include <common>
        attribute float aWindPhase;
        attribute float aWindStrength;
        uniform float uForestTime;
        uniform vec2 uForestPointer;
        uniform float uForestPointerStrength;
        varying float vForestSway;`
      )
      .replace(
        "#include <begin_vertex>",
        `vec3 transformed = vec3(position);
        vec3 treeAnchor = vec3(instanceMatrix[3]);
        float crownHeight = smoothstep(0.35, 5.2, position.y);
        float slowWind = sin(
          uForestTime * 0.72 +
          treeAnchor.x * 0.125 +
          treeAnchor.z * 0.052 +
          aWindPhase
        );
        float leafFlutter = sin(
          uForestTime * 1.63 -
          treeAnchor.x * 0.071 +
          treeAnchor.z * 0.095 +
          aWindPhase * 1.91
        );
        float longGust = 0.5 + 0.5 * sin(
          uForestTime * 0.19 +
          treeAnchor.x * 0.028 -
          treeAnchor.z * 0.037
        );
        float pointerDistance = distance(treeAnchor.xz, uForestPointer);
        float localGust = exp(-pointerDistance * pointerDistance * 0.018) *
          uForestPointerStrength;
        float bend = (
          slowWind * 0.21 +
          leafFlutter * 0.055 +
          longGust * 0.045 +
          localGust * sin(uForestTime * 3.2 + pointerDistance * 0.55) * 0.58
        ) * aWindStrength;
        float bendMask = crownHeight * crownHeight;
        transformed.x += bend * bendMask;
        transformed.z += (
          slowWind * 0.055 +
          localGust * cos(uForestTime * 2.7 + aWindPhase) * 0.19
        ) * aWindStrength * bendMask;
        vForestSway = abs(bend) * crownHeight + localGust * 0.22;`
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
        varying float vForestSway;`
      )
      .replace(
        "#include <dithering_fragment>",
        `gl_FragColor.rgb += vec3(0.022, 0.052, 0.026) *
          smoothstep(0.08, 0.7, vForestSway);
        #include <dithering_fragment>`
      );
  };
  material.customProgramCacheKey = () => "forest-wind-v1";
}

function createTieredCrownGeometry(segments) {
  const tiers = [
    { radius: 1.18, height: 3.0, y: 2.45 },
    { radius: 0.92, height: 2.65, y: 3.55 },
    { radius: 0.64, height: 2.25, y: 4.62 }
  ];
  const positions = [];
  const normals = [];

  tiers.forEach((tier) => {
    const indexed = new THREE.ConeGeometry(tier.radius, tier.height, segments, 2);
    indexed.translate(0, tier.y, 0);
    const geometry = indexed.toNonIndexed();
    positions.push(...geometry.attributes.position.array);
    normals.push(...geometry.attributes.normal.array);
    indexed.dispose();
    geometry.dispose();
  });

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("normal", new THREE.Float32BufferAttribute(normals, 3));
  geometry.computeBoundingSphere();
  return geometry;
}

function createForestInstances(profile, windUniforms) {
  const random = seededRandom(20260723);
  const crownGeometry = createTieredCrownGeometry(profile.crownSegments);
  const trunkGeometry = new THREE.CylinderGeometry(0.13, 0.22, 2.3, 6, 1);
  trunkGeometry.translate(0, 1.15, 0);

  const phases = new Float32Array(profile.trees);
  const strengths = new Float32Array(profile.trees);
  crownGeometry.setAttribute("aWindPhase", new THREE.InstancedBufferAttribute(phases, 1));
  crownGeometry.setAttribute("aWindStrength", new THREE.InstancedBufferAttribute(strengths, 1));

  const crownMaterial = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    emissive: 0x102d1b,
    emissiveIntensity: 0.58,
    roughness: 0.96,
    metalness: 0,
    vertexColors: true
  });
  installWindShader(crownMaterial, windUniforms);

  const trunkMaterial = new THREE.MeshStandardMaterial({
    color: 0x4d3b29,
    roughness: 1,
    metalness: 0
  });

  const crowns = new THREE.InstancedMesh(crownGeometry, crownMaterial, profile.trees);
  const trunks = new THREE.InstancedMesh(trunkGeometry, trunkMaterial, profile.trees);
  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const quaternion = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  const color = new THREE.Color();

  for (let index = 0; index < profile.trees; index += 1) {
    const depthProgress = Math.pow(random(), 0.82);
    const z = -5.5 - depthProgress * 76;
    const availableWidth = 42 + depthProgress * 54;
    const x = (random() - 0.5) * availableWidth;
    const y = terrainHeight(x, z);
    const baseScale = 0.66 + random() * 0.64;
    const depthScale = 1 - depthProgress * 0.22;
    const slenderness = 0.82 + random() * 0.28;

    position.set(x, y, z);
    quaternion.setFromAxisAngle(new THREE.Vector3(0, 1, 0), random() * Math.PI * 2);
    scale.set(baseScale * slenderness, baseScale * depthScale, baseScale * slenderness);
    matrix.compose(position, quaternion, scale);
    crowns.setMatrixAt(index, matrix);

    scale.set(baseScale * 0.82, baseScale * depthScale, baseScale * 0.82);
    matrix.compose(position, quaternion, scale);
    trunks.setMatrixAt(index, matrix);

    const colorNoise = random();
    const depthHaze = depthProgress * 0.34;
    color.setHSL(
      0.325 + colorNoise * 0.035,
      0.43 - depthHaze * 0.18,
      0.215 + colorNoise * 0.055 + depthHaze * 0.31
    );
    crowns.setColorAt(index, color);
    phases[index] = random() * Math.PI * 2;
    strengths[index] = 0.72 + random() * 0.68;
  }

  crowns.instanceMatrix.needsUpdate = true;
  crowns.instanceColor.needsUpdate = true;
  crownGeometry.attributes.aWindPhase.needsUpdate = true;
  crownGeometry.attributes.aWindStrength.needsUpdate = true;
  crowns.frustumCulled = false;
  trunks.frustumCulled = false;

  return {
    crowns,
    trunks,
    geometries: [crownGeometry, trunkGeometry],
    materials: [crownMaterial, trunkMaterial]
  };
}

const skyVertexShader = `
  varying vec3 vWorldDirection;

  void main() {
    vec4 worldPosition = modelMatrix * vec4(position, 1.0);
    vWorldDirection = normalize(worldPosition.xyz - cameraPosition);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const skyFragmentShader = `
  precision highp float;

  varying vec3 vWorldDirection;

  void main() {
    float elevation = clamp(vWorldDirection.y * 0.5 + 0.5, 0.0, 1.0);
    float horizon = pow(1.0 - abs(vWorldDirection.y), 4.0);
    vec3 lowSky = vec3(0.43, 0.62, 0.56);
    vec3 highSky = vec3(0.18, 0.38, 0.40);
    vec3 color = mix(lowSky, highSky, smoothstep(0.18, 0.94, elevation));
    color += horizon * vec3(0.08, 0.11, 0.075);
    gl_FragColor = vec4(color, 1.0);
  }
`;

export function createForestLab({ canvas, stage, profileName, maxDpr }) {
  const profile = forestProfiles[profileName] || forestProfiles.balanced;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: profileName !== "eco",
    alpha: false,
    powerPreference: "high-performance"
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.setClearColor(0x587d72, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x76958a, 0.0125);
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 180);
  const lookTarget = new THREE.Vector3(0, 3.2, -31);

  const skyGeometry = new THREE.SphereGeometry(150, 42, 20);
  const skyMaterial = new THREE.ShaderMaterial({
    vertexShader: skyVertexShader,
    fragmentShader: skyFragmentShader,
    side: THREE.BackSide,
    depthWrite: false
  });
  scene.add(new THREE.Mesh(skyGeometry, skyMaterial));

  const hemisphere = new THREE.HemisphereLight(0xb9d5c7, 0x132b1c, 2.05);
  scene.add(hemisphere);
  const sun = new THREE.DirectionalLight(0xffefc7, 2.35);
  sun.position.set(-24, 31, 18);
  scene.add(sun);

  const farRidgeGeometry = createRidgeGeometry({
    z: -116,
    baseline: 14,
    amplitude: 8.2,
    phase: 0.8,
    detail: 80
  });
  const farRidgeMaterial = new THREE.MeshLambertMaterial({ color: 0x55776a, side: THREE.DoubleSide });
  scene.add(new THREE.Mesh(farRidgeGeometry, farRidgeMaterial));

  const middleRidgeGeometry = createRidgeGeometry({
    z: -92,
    baseline: 10,
    amplitude: 6.8,
    phase: 2.15,
    detail: 92
  });
  const middleRidgeMaterial = new THREE.MeshLambertMaterial({ color: 0x315b47, side: THREE.DoubleSide });
  scene.add(new THREE.Mesh(middleRidgeGeometry, middleRidgeMaterial));

  const { terrain, geometry: terrainGeometry, material: terrainMaterial } = createTerrain(profile);
  scene.add(terrain);

  const windUniforms = {
    time: { value: 0 },
    pointer: { value: new THREE.Vector2(1000, 1000) },
    pointerStrength: { value: 0 }
  };
  const forest = createForestInstances(profile, windUniforms);
  scene.add(forest.trunks, forest.crowns);

  const pointerTarget = new THREE.Vector2(1000, 1000);
  let simulationTime = 0;
  let paused = false;
  let pointerInside = false;
  let pointerImpulse = 0;
  let disposed = false;
  let compactView = false;

  function resize() {
    const rect = stage.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    compactView = width <= 760;
    renderer.setPixelRatio(Math.min(maxDpr, window.devicePixelRatio || 1));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = compactView ? 51 : 42;
    camera.updateProjectionMatrix();
  }

  function setPointer(clientX, clientY, speed = 0) {
    const rect = stage.getBoundingClientRect();
    const normalizedX = THREE.MathUtils.clamp((clientX - rect.left) / rect.width, 0, 1);
    const normalizedY = THREE.MathUtils.clamp((clientY - rect.top) / rect.height, 0, 1);
    pointerTarget.set(
      THREE.MathUtils.lerp(-38, 38, normalizedX),
      THREE.MathUtils.lerp(-70, -8, normalizedY)
    );
    pointerInside = true;
    pointerImpulse = Math.max(pointerImpulse, THREE.MathUtils.clamp(speed / 900, 0, 1) * 0.95);
    return true;
  }

  function setPointerActive(active) {
    pointerInside = active;
  }

  function pulsePointer() {
    pointerImpulse = 1.2;
  }

  function setPaused(nextPaused) {
    paused = nextPaused;
  }

  function reset() {
    simulationTime = 0;
    pointerImpulse = 0;
    windUniforms.pointerStrength.value = 0;
  }

  function update(delta) {
    if (!paused) simulationTime += delta;
    windUniforms.time.value = simulationTime;
    windUniforms.pointer.value.lerp(pointerTarget, 1 - Math.exp(-delta * 4.5));
    pointerImpulse *= Math.exp(-delta * 2.5);
    const targetStrength = pointerInside ? 0.34 + pointerImpulse : 0;
    windUniforms.pointerStrength.value = THREE.MathUtils.lerp(
      windUniforms.pointerStrength.value,
      targetStrength,
      1 - Math.exp(-delta * 5.5)
    );

    const drift = Math.sin(simulationTime * 0.035) * (compactView ? 0.32 : 0.62);
    camera.position.set(drift, compactView ? 9.5 : 8.3, compactView ? 24.5 : 23.5);
    lookTarget.set(drift * 0.18, compactView ? 3.8 : 3.2, compactView ? -27 : -31);
    camera.lookAt(lookTarget);
  }

  function render() {
    if (!disposed) renderer.render(scene, camera);
  }

  function dispose() {
    disposed = true;
    terrainGeometry.dispose();
    terrainMaterial.dispose();
    farRidgeGeometry.dispose();
    farRidgeMaterial.dispose();
    middleRidgeGeometry.dispose();
    middleRidgeMaterial.dispose();
    skyGeometry.dispose();
    skyMaterial.dispose();
    forest.geometries.forEach((geometry) => geometry.dispose());
    forest.materials.forEach((material) => material.dispose());
    renderer.dispose();
  }

  resize();
  update(0);
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
    detail: `바람 수관 · 나무 ${profile.trees.toLocaleString("ko-KR")}그루`
  };
}
