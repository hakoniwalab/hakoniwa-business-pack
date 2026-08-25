import { CityWorldPduClient } from './city-world-client.js?v=20260825-dem-fill-1';

const elements = Object.fromEntries([
  'latitude', 'longitude', 'northSouth', 'eastWest', 'physics-level', 'terrain-uncovered-policy', 'coplanar-union', 'convex-decompose', 'tolerant-planar', 'inspect', 'generate', 'cancel', 'connect',
  'connection', 'connection-text', 'selection-summary', 'mesh-summary', 'overall',
  'municipality', 'capabilities', 'generation', 'artifact-select', 'artifact-path', 'artifact-detail', 'cache-info',
  'download', 'view3d', 'delete-artifact', 'viewer-visual', 'viewer-collider',
  'viewer-panel', 'viewer-status', 'viewer-canvas', 'log',
].map((id) => [id, document.getElementById(id)]));

const capabilityLabels = {
  building: 'Building', terrain: 'Terrain', road: 'Road',
  road_markings: 'Road markings', bridge: 'Bridge',
};
const progressPhaseLabels = {
  source_download: 'PLATEAUソース',
  geometry_extract: '建物形状抽出',
  geometry_extract_files: '建物形状抽出',
  building_collision: '建物Collider',
  terrain: '地形',
  terrain_extract: 'DEM抽出',
  terrain_gap_fill: 'DEM欠損補間',
  building_mjcf: '建物Physics',
  building_physics_surfaces: '建物Physics面変換',
  building_physics_exact_reduction: '建物Collider厳密統合',
  building_physics_exact_groups: '建物Collider厳密統合',
  building_physics_tolerant_reduction: '建物Collider 5cm許容統合',
  building_physics_tolerant_groups: '建物Collider 5cm許容統合',
  building_physics_assemble: '建物Physics構築',
  building_physics_write: '建物Physics書出し',
  building_visual: '建物Visual',
  texture_download: '建物テクスチャ',
  building_glb: '建物GLB',
  building_glb_textures: '建物GLBテクスチャ処理',
  building_glb_batches: '建物GLB構築',
  building_glb_export: '建物GLB書出し',
  roads: '道路Visual',
  road_markings: 'LOD3路面標示',
  bridges_visual: '橋梁Visual',
  bridges_physics: '橋梁Physics',
  compose: 'City World統合',
  dataset_validation: 'Capability検証',
  world_generated: '生成完了確認',
  collider_visualization: 'Collider表示生成',
  packaging: '検証・ZIP作成',
};
const prefectureSlugs = {
  '01': 'hokkaido', '02': 'aomori', '03': 'iwate', '04': 'miyagi', '05': 'akita',
  '06': 'yamagata', '07': 'fukushima', '08': 'ibaraki', '09': 'tochigi', '10': 'gunma',
  '11': 'saitama', '12': 'chiba', '13': 'tokyo', '14': 'kanagawa', '15': 'niigata',
  '16': 'toyama', '17': 'ishikawa', '18': 'fukui', '19': 'yamanashi', '20': 'nagano',
  '21': 'gifu', '22': 'shizuoka', '23': 'aichi', '24': 'mie', '25': 'shiga',
  '26': 'kyoto', '27': 'osaka', '28': 'hyogo', '29': 'nara', '30': 'wakayama',
  '31': 'tottori', '32': 'shimane', '33': 'okayama', '34': 'hiroshima', '35': 'yamaguchi',
  '36': 'tokushima', '37': 'kagawa', '38': 'ehime', '39': 'kochi', '40': 'fukuoka',
  '41': 'saga', '42': 'nagasaki', '43': 'kumamoto', '44': 'oita', '45': 'miyazaki',
  '46': 'kagoshima', '47': 'okinawa',
};

function generatedJobId(request, inspected) {
  const codes = [...new Set(inspected.municipalities.map((item) => item.city_code))].sort();
  const cityCode = codes[0] ?? '00000';
  const prefecture = prefectureSlugs[cityCode.slice(0, 2)] ?? `pref${cityCode.slice(0, 2)}`;
  const center = request.selection.center;
  const multiple = codes.length > 1 ? '-multi' : '';
  return `${prefecture}-${cityCode}${multiple}-lat${center.latitude.toFixed(3)}-lon${center.longitude.toFixed(3)}`;
}

function progressText(progress) {
  const phase = progressPhaseLabels[progress.phase] ?? progress.phase;
  const heading = phase ? `${phase} — ` : '';
  return `${progress.percent}% — ${heading}${progress.message}`;
}

function statusMatchesCommand(status, command) {
  return status.job_id === command.job_id
    && status.request_sha256 === command.request_sha256;
}
const client = new CityWorldPduClient();
let connected = false;
let inspecting = false;
let generating = false;
let canceling = false;
let activeGenerationCommand = null;
let lastAvailable = null;
let generatedJobs = [];
let viewerRuntime = null;
let viewerModels = { visual: null, collider: null };
let viewerJobId = null;
let viewerLoadSequence = 0;

const map = L.map('map', { preferCanvas: true, maxZoom: 20 }).setView([
  Number(elements.latitude.value), Number(elements.longitude.value),
], 16);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors', maxZoom: 20, maxNativeZoom: 19,
}).addTo(map);
L.control.scale().addTo(map);

const marker = L.marker(map.getCenter(), { draggable: true }).addTo(map)
  .bindTooltip('City World center', { direction: 'top' });
const selectionRectangle = L.rectangle([[0, 0], [0, 0]], {
  color: '#1367a8', weight: 2, fillColor: '#2d8dca', fillOpacity: .18,
  className: 'selection-rectangle',
}).addTo(map).bindTooltip('Drag to move / corner handles to resize');
const generatedRectangle = L.rectangle([[0, 0], [0, 0]], {
  color: '#e06b23', weight: 5, opacity: 0, fill: false, dashArray: '10 7',
  interactive: false,
}).addTo(map);
const diagnosticMeshLayer = L.layerGroup().addTo(map);
const resizeHandleDirections = ['nw', 'ne', 'se', 'sw'];
const resizeHandles = resizeHandleDirections.map((direction) => L.marker([0, 0], {
  draggable: true,
  icon: L.divIcon({
    className: `selection-resize-handle selection-resize-handle-${direction}`,
    html: '<span aria-hidden="true"></span>',
    iconSize: [28, 28], iconAnchor: [14, 14],
  }),
  keyboard: false, zIndexOffset: 1000,
}).addTo(map));
const oppositeCorner = [2, 3, 0, 1];
let suppressMapClickUntil = 0;
let selectionInteraction = null;

function suppressMapClick() {
  // Leaflet may emit a click immediately after a marker/shape drag. Use a
  // deadline rather than a sticky flag so a later intentional click is kept.
  suppressMapClickUntil = Date.now() + 250;
}

function numeric(id) { return Number(elements[id].value); }

function invalidateInspection() {
  lastAvailable = null;
  diagnosticMeshLayer.clearLayers();
  elements['mesh-summary'].textContent = '';
  elements.generate.disabled = true;
  if (!generating) {
    elements.generation.className = 'generation';
    elements.generation.textContent = '';
  }
}

function selectionBounds() {
  const latitude = numeric('latitude');
  const longitude = numeric('longitude');
  const latDelta = numeric('northSouth') / 111320;
  const lonDelta = numeric('eastWest') / (111320 * Math.cos(latitude * Math.PI / 180));
  return L.latLngBounds(
    [latitude - latDelta, longitude - lonDelta],
    [latitude + latDelta, longitude + lonDelta],
  );
}

function boundsCorners(bounds) {
  return [bounds.getNorthWest(), bounds.getNorthEast(), bounds.getSouthEast(), bounds.getSouthWest()];
}

function refreshResizeHandles(bounds, skipIndex = -1) {
  boundsCorners(bounds).forEach((corner, index) => {
    if (index !== skipIndex) resizeHandles[index].setLatLng(corner);
  });
}

function setInputsFromBounds(bounds) {
  const center = bounds.getCenter();
  const northSouth = (bounds.getNorth() - bounds.getSouth()) * 111320 / 2;
  const eastWest = (bounds.getEast() - bounds.getWest())
    * 111320 * Math.cos(center.lat * Math.PI / 180) / 2;
  elements.latitude.value = center.lat.toFixed(6);
  elements.longitude.value = center.lng.toFixed(6);
  elements.northSouth.value = northSouth.toFixed(1);
  elements.eastWest.value = eastWest.toFixed(1);
}

function selectionIsValid() {
  return Number.isFinite(numeric('latitude')) && Number.isFinite(numeric('longitude'))
    && numeric('latitude') >= -90 && numeric('latitude') <= 90
    && numeric('longitude') >= -180 && numeric('longitude') <= 180
    && numeric('northSouth') >= 10 && numeric('northSouth') <= 1000
    && numeric('eastWest') >= 10 && numeric('eastWest') <= 1000;
}

function refreshSelection({ pan = false, skipHandle = -1 } = {}) {
  if (!selectionIsValid()) {
    elements['selection-summary'].textContent = '範囲の値を確認してください。';
    elements.inspect.disabled = true;
    elements.generate.disabled = true;
    return;
  }
  const center = [numeric('latitude'), numeric('longitude')];
  marker.setLatLng(center);
  const bounds = selectionBounds();
  selectionRectangle.setBounds(bounds);
  refreshResizeHandles(bounds, skipHandle);
  elements['selection-summary'].textContent = (
    `${(numeric('eastWest') * 2).toFixed(0)}m × ${(numeric('northSouth') * 2).toFixed(0)}m`
    + ` / center=${center[0].toFixed(6)}, ${center[1].toFixed(6)}`
  );
  elements.inspect.disabled = !connected || inspecting || generating;
  elements.generate.disabled = !connected || inspecting || generating || lastAvailable === null;
  if (pan) map.panTo(center);
}

function setCenter(latlng) {
  invalidateInspection();
  elements.latitude.value = latlng.lat.toFixed(6);
  elements.longitude.value = latlng.lng.toFixed(6);
  refreshSelection();
}

map.on('click', (event) => {
  if (Date.now() < suppressMapClickUntil) return;
  setCenter(event.latlng);
});
marker.on('drag', (event) => setCenter(event.target.getLatLng()));
for (const id of ['latitude', 'longitude', 'northSouth', 'eastWest', 'physics-level', 'terrain-uncovered-policy']) {
  elements[id].addEventListener('input', () => {
    invalidateInspection();
    refreshSelection();
  });
  elements[id].addEventListener('change', () => {
    if (!selectionIsValid()) return;
    map.fitBounds(selectionBounds().pad(0.35), { maxZoom: 19 });
  });
}
elements['coplanar-union'].addEventListener('change', () => {
  if (!elements['coplanar-union'].checked) {
    elements['convex-decompose'].checked = false;
    elements['tolerant-planar'].checked = false;
  }
  invalidateInspection();
  refreshSelection();
});
elements['convex-decompose'].addEventListener('change', () => {
  if (elements['convex-decompose'].checked) {
    elements['coplanar-union'].checked = true;
  } else {
    elements['tolerant-planar'].checked = false;
  }
  invalidateInspection();
  refreshSelection();
});
elements['tolerant-planar'].addEventListener('change', () => {
  if (elements['tolerant-planar'].checked) {
    elements['convex-decompose'].checked = true;
    elements['coplanar-union'].checked = true;
  }
  invalidateInspection();
  refreshSelection();
});

resizeHandles.forEach((handle, index) => {
  let fixedCorner = null;
  handle.on('mousedown', (event) => {
    // The resize marker sits over the movable rectangle. Claim pointer-down
    // before the SVG layer can interpret the same gesture as rectangle move.
    selectionInteraction = 'resize';
    L.DomEvent.stopPropagation(event.originalEvent);
    suppressMapClick();
  });
  handle.on('dragstart', () => {
    selectionInteraction = 'resize';
    fixedCorner = boundsCorners(selectionRectangle.getBounds())[oppositeCorner[index]];
    suppressMapClick();
  });
  handle.on('drag', (event) => {
    invalidateInspection();
    const bounds = L.latLngBounds(fixedCorner, event.target.getLatLng());
    setInputsFromBounds(bounds);
    refreshSelection({ skipHandle: index });
  });
  handle.on('dragend', () => {
    suppressMapClick();
    fixedCorner = null;
    selectionInteraction = null;
    refreshSelection();
  });
});

selectionRectangle.on('mousedown', (event) => {
  const target = event.originalEvent?.target;
  if (selectionInteraction === 'resize'
      || (target instanceof Element && target.closest('.selection-resize-handle'))) return;
  selectionInteraction = 'move';
  L.DomEvent.stopPropagation(event.originalEvent);
  suppressMapClick();
  const start = event.latlng;
  const original = selectionRectangle.getBounds();
  map.dragging.disable();

  const move = (moveEvent) => {
    invalidateInspection();
    const latDelta = moveEvent.latlng.lat - start.lat;
    const lonDelta = moveEvent.latlng.lng - start.lng;
    const moved = L.latLngBounds(
      [original.getSouth() + latDelta, original.getWest() + lonDelta],
      [original.getNorth() + latDelta, original.getEast() + lonDelta],
    );
    setInputsFromBounds(moved);
    refreshSelection();
  };
  const finish = () => {
    suppressMapClick();
    map.off('mousemove', move);
    map.off('mouseup', finish);
    document.removeEventListener('mouseup', finish);
    map.dragging.enable();
    selectionInteraction = null;
  };
  map.on('mousemove', move);
  map.on('mouseup', finish);
  document.addEventListener('mouseup', finish, { once: true });
});

function writeLog(value) {
  elements.log.textContent += `${JSON.stringify(value, null, 2)}\n`;
  elements.log.scrollTop = elements.log.scrollHeight;
}

function formatBytes(value) {
  if (value < 1000) return `${value} B`;
  if (value < 1_000_000) return `${(value / 1000).toFixed(1)} KB`;
  return `${(value / 1_000_000).toFixed(1)} MB`;
}

function selectedGeneratedJob() {
  return generatedJobs.find((item) => item.job_id === elements['artifact-select'].value) ?? null;
}

function applyGeneratedSelection(job) {
  const selection = job?.selection;
  if (selection === null || selection === undefined) {
    generatedRectangle.setStyle({ opacity: 0 });
    return;
  }
  const center = selection.center;
  const extent = selection.half_extent_m;
  elements.latitude.value = Number(center.latitude).toFixed(6);
  elements.longitude.value = Number(center.longitude).toFixed(6);
  elements.northSouth.value = Number(extent.north_south).toFixed(1);
  elements.eastWest.value = Number(extent.east_west).toFixed(1);
  if (Number.isInteger(job.building_physics_level)) {
    elements['physics-level'].value = String(job.building_physics_level);
  }
  elements['coplanar-union'].checked = [
    'coplanar-union', 'convex-decompose', 'tolerant-planar',
  ].includes(
    job.building_collider_reduction,
  );
  elements['convex-decompose'].checked = (
    ['convex-decompose', 'tolerant-planar'].includes(job.building_collider_reduction)
  );
  elements['tolerant-planar'].checked = (
    job.building_collider_reduction === 'tolerant-planar'
  );
  elements['terrain-uncovered-policy'].value = (
    job.terrain_uncovered_policy === 'constant' ? 'constant' : 'error'
  );
  invalidateInspection();
  refreshSelection();
  const bounds = selectionBounds();
  generatedRectangle.setBounds(bounds);
  generatedRectangle.setStyle({ opacity: 1 });
  generatedRectangle.unbindTooltip().bindTooltip(`生成結果: ${job.job_id}`);
  map.fitBounds(bounds.pad(0.35), { maxZoom: 19 });
}

function updateArtifactSelection({ restoreSelection = false } = {}) {
  const job = selectedGeneratedJob();
  elements.download.disabled = job === null;
  elements.view3d.disabled = job === null;
  elements['delete-artifact'].disabled = job === null;
  elements['viewer-visual'].disabled = job === null;
  elements['viewer-collider'].disabled = job === null || !job?.collider_available;
  if (job !== null && !job.collider_available) {
    elements['viewer-visual'].checked = true;
    elements['viewer-collider'].checked = false;
  }
  elements['artifact-path'].textContent = job === null
    ? '—'
    : `${job.server_relative_path}/`;
  const componentCounts = job?.colliders?.by_component ?? {};
  const componentText = Object.entries(componentCounts)
    .map(([name, count]) => `${name}=${count}`)
    .join(', ');
  const classCounts = job?.colliders?.by_physics_class ?? {};
  const classText = ['P0', 'P1', 'P2', 'P3']
    .map((name) => `${name}=${classCounts[name] ?? 0}`)
    .join(', ');
  const geomTypes = job?.colliders?.building_by_geom_type;
  elements['artifact-detail'].textContent = job === null
    ? 'Physics Level: — / Collider: —'
    : [
      `Physics Level: ${job.building_physics_level ?? '旧形式'}`,
      `Collider reduction: ${job.building_collider_reduction ?? 'safe'}`,
      `Collider total: ${job.colliders?.total ?? '不明'} geoms`,
      componentText ? `Components: ${componentText}` : null,
      job.colliders?.by_physics_class ? `Building allocation: ${classText}` : null,
      geomTypes ? `Building geom types: box=${geomTypes.box}, mesh=${geomTypes.mesh}` : null,
    ].filter(Boolean).join('\n');
  if (restoreSelection) applyGeneratedSelection(job);
}

function applyViewerMode() {
  if (viewerModels.visual === null) return;
  viewerModels.visual.visible = elements['viewer-visual'].checked;
  if (viewerModels.collider !== null) {
    viewerModels.collider.visible = elements['viewer-collider'].checked;
  }
  viewerRuntime?.renderer.render(viewerRuntime.scene, viewerRuntime.camera);
}

function viewerLayerLabel() {
  if (elements['viewer-visual'].checked && elements['viewer-collider'].checked) {
    return 'Visual + Collider';
  }
  return elements['viewer-visual'].checked ? 'Visual' : 'Collider';
}

function changeViewerLayer(changedElement) {
  if (!elements['viewer-visual'].checked && !elements['viewer-collider'].checked) {
    changedElement.checked = true;
  }
  applyViewerMode();
  if (viewerJobId !== null) {
    elements['viewer-status'].textContent = `${viewerJobId} — ${viewerLayerLabel()}`;
  }
}

async function refreshGeneratedJobs(preferredJobId = null) {
  try {
    const response = await fetch('/generated/index.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`generated index: HTTP ${response.status}`);
    const payload = await response.json();
    generatedJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    const cache = payload.shared_cache;
    elements['cache-info'].textContent = cache
      ? `共有キャッシュ: ${cache.server_relative_path}/ (${cache.object_count} files / ${formatBytes(cache.size_bytes)})`
      : '共有キャッシュ: —';
    elements['artifact-select'].replaceChildren();
    if (generatedJobs.length === 0) {
      elements['artifact-select'].append(new Option('生成結果はありません', ''));
      elements['artifact-select'].disabled = true;
    } else {
      for (const job of generatedJobs) {
        elements['artifact-select'].append(new Option(
          job.selection
            ? `${job.job_id} — ${job.selection.half_extent_m.east_west * 2}m × ${job.selection.half_extent_m.north_south * 2}m — ${formatBytes(job.size_bytes)}`
            : `${job.job_id} — ${formatBytes(job.size_bytes)}`,
          job.job_id,
        ));
      }
      elements['artifact-select'].disabled = false;
      if (preferredJobId && generatedJobs.some((job) => job.job_id === preferredJobId)) {
        elements['artifact-select'].value = preferredJobId;
      }
    }
    updateArtifactSelection({ restoreSelection: true });
  } catch (error) {
    generatedJobs = [];
    elements['cache-info'].textContent = '共有キャッシュ: 取得できません';
    elements['artifact-select'].replaceChildren(new Option('生成履歴を取得できません', ''));
    elements['artifact-select'].disabled = true;
    updateArtifactSelection();
    writeLog({ type: 'GENERATED_INDEX_FAILED', error: String(error) });
  }
}

function disposeObject(root) {
  root.traverse((object) => {
    if (object.geometry) object.geometry.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials.filter(Boolean)) {
      for (const value of Object.values(material)) {
        if (value?.isTexture) value.dispose();
      }
      material.dispose();
    }
  });
}

function closeViewerForJob(jobId) {
  if (viewerJobId !== jobId) return;
  viewerLoadSequence += 1;
  if (viewerRuntime !== null) {
    for (const model of Object.values(viewerModels).filter(Boolean)) {
      viewerRuntime.scene.remove(model);
      disposeObject(model);
    }
    viewerRuntime.renderer.render(viewerRuntime.scene, viewerRuntime.camera);
  }
  viewerModels = { visual: null, collider: null };
  viewerJobId = null;
  document.getElementById('app').classList.remove('viewer-open');
  requestAnimationFrame(() => map.invalidateSize());
}

async function deleteSelectedArtifact() {
  const job = selectedGeneratedJob();
  if (job === null) return;
  if (!window.confirm(
    `生成結果 ${job.job_id} を削除しますか？\n` +
    'サーバー上のjobディレクトリ（ZIP・GLB・MJCF・中間生成物）を削除します。\n' +
    '共有CityGMLキャッシュは削除しません。',
  )) return;
  elements['delete-artifact'].disabled = true;
  try {
    const response = await fetch(`/generated/${encodeURIComponent(job.job_id)}`, {
      method: 'DELETE', cache: 'no-store',
    });
    if (!response.ok) throw new Error(`delete generated job: HTTP ${response.status}`);
    closeViewerForJob(job.job_id);
    writeLog(await response.json());
    elements.generation.className = 'generation ready';
    elements.generation.textContent =
      `サーバー上のjobを削除しました — ${job.job_id}（共有CityGMLキャッシュは保持）`;
    await refreshGeneratedJobs();
  } catch (error) {
    elements.generation.className = 'generation failed';
    elements.generation.textContent = '生成結果の削除に失敗しました。';
    writeLog({ type: 'DELETE_GENERATED_FAILED', job_id: job.job_id, error: String(error) });
    updateArtifactSelection();
  }
}

async function initializeViewer() {
  if (viewerRuntime !== null) return viewerRuntime;
  const THREE = await import('three');
  const [{ GLTFLoader }, { OrbitControls }] = await Promise.all([
    import('three/addons/loaders/GLTFLoader.js'),
    import('three/addons/controls/OrbitControls.js'),
  ]);
  const renderer = new THREE.WebGLRenderer({
    canvas: elements['viewer-canvas'], antialias: true, alpha: false,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xdde6eb);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
  const controls = new OrbitControls(camera, renderer.domElement);
  // Render only on interaction so an idle Viewer does not consume CPU.
  controls.enableDamping = false;
  controls.addEventListener('change', () => renderer.render(scene, camera));
  scene.add(new THREE.HemisphereLight(0xffffff, 0x53606b, 2.2));
  const sunlight = new THREE.DirectionalLight(0xffffff, 2.5);
  sunlight.position.set(100, 180, 80);
  scene.add(sunlight);

  const resize = () => {
    const width = Math.max(1, elements['viewer-panel'].clientWidth);
    const height = Math.max(1, elements['viewer-panel'].clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
  };
  new ResizeObserver(resize).observe(elements['viewer-panel']);
  viewerRuntime = { THREE, GLTFLoader, renderer, scene, camera, controls, resize };
  return viewerRuntime;
}

async function openSelectedViewer() {
  const job = selectedGeneratedJob();
  if (job === null) return;
  const loadSequence = ++viewerLoadSequence;
  document.getElementById('app').classList.add('viewer-open');
  elements['viewer-status'].textContent = `${job.job_id}を読み込み中…`;
  requestAnimationFrame(() => map.invalidateSize());
  try {
    const runtime = await initializeViewer();
    runtime.resize();
    const visualGltf = await new runtime.GLTFLoader().loadAsync(
      `/generated/${encodeURIComponent(job.job_id)}/city-world.glb`,
    );
    const colliderGltf = job.collider_available
      ? await new runtime.GLTFLoader().loadAsync(
        `/generated/${encodeURIComponent(job.job_id)}/city-world-colliders.glb`,
      )
      : null;
    if (loadSequence !== viewerLoadSequence) {
      disposeObject(visualGltf.scene);
      if (colliderGltf !== null) disposeObject(colliderGltf.scene);
      return;
    }
    for (const model of Object.values(viewerModels).filter(Boolean)) {
      runtime.scene.remove(model);
      disposeObject(model);
    }
    viewerModels = { visual: visualGltf.scene, collider: colliderGltf?.scene ?? null };
    viewerJobId = job.job_id;
    runtime.scene.add(viewerModels.visual);
    if (viewerModels.collider !== null) {
      const colliderMaterial = new runtime.THREE.MeshBasicMaterial({
        color: 0x28a86b, transparent: true, opacity: 0.38,
        wireframe: true, depthTest: true, depthWrite: false,
      });
      viewerModels.collider.traverse((object) => {
        if (!object.isMesh) return;
        const oldMaterials = Array.isArray(object.material) ? object.material : [object.material];
        oldMaterials.filter(Boolean).forEach((material) => material.dispose());
        object.material = colliderMaterial;
        object.renderOrder = 10;
      });
      runtime.scene.add(viewerModels.collider);
    }
    const box = new runtime.THREE.Box3().setFromObject(viewerModels.visual);
    if (viewerModels.collider !== null) box.expandByObject(viewerModels.collider);
    if (box.isEmpty()) throw new Error('GLBに表示可能なgeometryがありません');
    const center = box.getCenter(new runtime.THREE.Vector3());
    const size = box.getSize(new runtime.THREE.Vector3());
    viewerModels.visual.position.set(-center.x, -box.min.y, -center.z);
    if (viewerModels.collider !== null) {
      viewerModels.collider.position.copy(viewerModels.visual.position);
    }
    const distance = Math.max(size.x, size.y, size.z, 10) * 1.35;
    runtime.camera.near = Math.max(0.1, distance / 10000);
    runtime.camera.far = Math.max(2000, distance * 20);
    runtime.camera.position.set(distance * 0.65, distance * 0.55, distance * 0.65);
    runtime.camera.updateProjectionMatrix();
    runtime.controls.target.set(0, Math.max(0, size.y * 0.2), 0);
    runtime.controls.update();
    applyViewerMode();
    elements['viewer-status'].textContent = viewerModels.collider === null
      ? `${job.job_id} — Visual（Collider表示なし）`
      : `${job.job_id} — ${viewerLayerLabel()}`;
  } catch (error) {
    elements['viewer-status'].textContent = '3D表示に失敗しました。通信ログを確認してください。';
    writeLog({ type: 'VIEWER_FAILED', job_id: job.job_id, error: String(error) });
  }
}

function setConnection(state, text) {
  elements.connection.dataset.state = state;
  elements['connection-text'].textContent = text;
  connected = state === 'connected';
  refreshSelection();
}

async function connect() {
  if (connected) return;
  setConnection('connecting', 'Workerへ接続中');
  elements.connect.disabled = true;
  try {
    await client.connect();
    setConnection('connected', 'Worker接続済み');
  } catch (error) {
    setConnection('error', `接続失敗: ${error.message ?? error}`);
    writeLog({ type: 'CONNECTION_FAILED', error: String(error) });
  } finally {
    elements.connect.disabled = false;
  }
}

function capabilityPresentation(capability) {
  if (capability.dataset_status !== 'available') {
    return { style: 'unavailable', symbol: '—', title: 'Not available' };
  }
  if (capability.generation_status !== 'candidate') {
    return { style: 'limited', symbol: '△', title: 'Limited' };
  }
  return { style: 'candidate', symbol: '✓', title: 'Available' };
}

function renderInspection(message, command) {
  const inspected = message.inspection;
  diagnosticMeshLayer.clearLayers();
  const meshes = Array.isArray(inspected.query_meshes) ? inspected.query_meshes : [];
  for (const mesh of meshes) {
    const bounds = mesh.bbox;
    L.rectangle([[bounds.south, bounds.west], [bounds.north, bounds.east]], {
      color: '#00897b', weight: 3, opacity: 0.9,
      fillColor: '#36b7a7', fillOpacity: 0.035, dashArray: '5 5',
    }).bindTooltip(`PLATEAU 3次メッシュ: ${mesh.code}`).addTo(diagnosticMeshLayer);
  }
  elements['mesh-summary'].textContent = meshes.length
    ? `診断対象PLATEAUメッシュ: ${meshes.map((mesh) => mesh.code).join(', ')}`
    : '';
  elements.overall.className = inspected.status;
  elements.overall.textContent = inspected.status === 'available'
    ? `生成候補あり — ${inspected.source_file_count} files / 約${(inspected.estimated_download_bytes / 1_000_000).toFixed(1)} MB / DEM未被覆: ${command.request.options?.terrain_uncovered_policy === 'constant' ? '標高0 mで補完' : '厳密停止'}`
    : `生成不可 — ${inspected.reason}`;
  elements.municipality.textContent = inspected.municipalities.length
    ? inspected.municipalities.map((item) => `${item.city} (${item.year}, spec ${item.spec})`).join(' / ')
    : '';
  elements.capabilities.replaceChildren();
  for (const [name, label] of Object.entries(capabilityLabels)) {
    const capability = inspected.capabilities[name];
    const view = capabilityPresentation(capability);
    const card = document.createElement('div');
    card.className = `capability ${view.style}`;
    const symbol = document.createElement('div');
    symbol.className = 'symbol';
    symbol.textContent = view.symbol;
    const body = document.createElement('div');
    const heading = document.createElement('strong');
    heading.textContent = `${label}: ${view.title}`;
    const detail = document.createElement('small');
    detail.textContent = capability.dataset_status === 'available'
      ? `max LOD ${capability.max_lod} / ${capability.source_file_count} files${capability.reason ? ` / ${capability.reason}` : ''}`
      : capability.reason;
    body.append(heading, detail);
    card.append(symbol, body);
    elements.capabilities.append(card);
  }
  lastAvailable = inspected.status === 'available'
    ? {
      jobId: generatedJobId(command.request, inspected),
      request: command.request,
      inspectionSha256: message.inspection_sha256,
    }
    : null;
  elements.generate.disabled = !connected || lastAvailable === null;
}

async function inspectSelection() {
  if (!connected || inspecting || !selectionIsValid()) return;
  inspecting = true;
  invalidateInspection();
  elements.inspect.disabled = true;
  elements.inspect.textContent = '診断中…';
  elements.overall.className = '';
  elements.overall.textContent = 'PLATEAU catalogを確認中';
  elements.capabilities.replaceChildren();
  try {
    const command = await client.inspect({
      // A unique job ID prevents a delayed terminal status from an older
      // inspection being mistaken for the latest map selection.
      jobId: `inspection-${Date.now().toString(36)}`,
      latitude: numeric('latitude'), longitude: numeric('longitude'),
      halfExtentNorthSouth: numeric('northSouth'), halfExtentEastWest: numeric('eastWest'),
      buildingPhysicsLevel: numeric('physics-level'),
      buildingColliderReduction: elements['tolerant-planar'].checked
        ? 'tolerant-planar'
        : elements['convex-decompose'].checked ? 'convex-decompose'
        : elements['coplanar-union'].checked ? 'coplanar-union' : 'safe',
      terrainUncoveredPolicy: elements['terrain-uncovered-policy'].value,
    });
    writeLog(command);
    while (true) {
      const status = await client.nextStatus(120000);
      writeLog(status);
      if (!statusMatchesCommand(status, command)) continue;
      if (status.type === 'INSPECTING') continue;
      if (status.type === 'SELECTION_AVAILABLE' || status.type === 'SELECTION_UNAVAILABLE') {
        renderInspection(status, command);
        break;
      }
      if (status.type === 'FAILED') {
        elements.overall.className = 'failed';
        elements.overall.textContent = status.error.code === 'INSPECTION_FAILED'
          ? '診断サービスからPLATEAU catalogを取得できませんでした。時間をおいて再試行してください。'
          : '診断処理に失敗しました。詳細は通信ログを確認してください。';
        break;
      }
    }
  } catch (error) {
    if (!client.isConnected()) setConnection('error', 'Workerとの接続が切れました');
    elements.overall.className = 'failed';
    elements.overall.textContent = '診断処理に失敗しました。詳細は通信ログを確認してください。';
    writeLog({ type: 'CLIENT_ERROR', error: String(error) });
  } finally {
    inspecting = false;
    elements.inspect.textContent = '2. Capabilityを診断';
    refreshSelection();
  }
}

async function generateWorld() {
  if (!connected || generating || lastAvailable === null) return;
  generating = true;
  elements.generate.disabled = true;
  elements.inspect.disabled = true;
  elements.generate.textContent = '生成中…';
  elements.generation.className = 'generation running';
  elements.generation.textContent = 'Generateを送信しています';
  const target = lastAvailable;
  try {
    const command = await client.generate(target);
    activeGenerationCommand = command;
    elements.cancel.disabled = false;
    writeLog(command);
    while (true) {
      const status = await client.nextStatus(30 * 60 * 1000);
      writeLog(status);
      if (!statusMatchesCommand(status, command)) continue;
      if (['ACCEPTED', 'DOWNLOADING', 'GENERATING', 'VALIDATING'].includes(status.type)) {
        elements.generation.textContent = progressText(status.progress);
        continue;
      }
      if (status.type === 'READY') {
        elements.generation.className = 'generation ready';
        elements.generation.textContent = `Generate成功 — ${status.result.artifact_name}`;
        await refreshGeneratedJobs(command.job_id);
        break;
      }
      if (status.type === 'CANCELED') {
        elements.generation.className = 'generation canceled';
        elements.generation.textContent = 'Generateをキャンセルしました。途中生成物は破棄されました。';
        break;
      }
      if (status.type === 'FAILED') {
        elements.generation.className = 'generation failed';
        elements.generation.textContent = status.error.code === 'DEM_UNCOVERED'
          ? `地形生成を停止しました — ${status.error.message}`
          : `Generate失敗 — ${status.error.message}`;
        break;
      }
    }
  } catch (error) {
    if (!client.isConnected()) setConnection('error', 'Workerとの接続が切れました');
    elements.generation.className = 'generation failed';
    elements.generation.textContent = 'Generateに失敗しました。詳細は通信ログを確認してください。';
    writeLog({ type: 'CLIENT_ERROR', phase: 'generation', error: String(error) });
  } finally {
    generating = false;
    canceling = false;
    activeGenerationCommand = null;
    elements.cancel.disabled = true;
    elements.cancel.textContent = '生成をキャンセル';
    elements.generate.textContent = '3. City Worldを生成';
    refreshSelection();
  }
}

async function cancelGeneration() {
  if (!generating || canceling || activeGenerationCommand === null) return;
  canceling = true;
  elements.cancel.disabled = true;
  elements.cancel.textContent = 'キャンセル中…';
  elements.generation.className = 'generation running';
  elements.generation.textContent = '安全な停止点で生成処理を終了しています…';
  try {
    const command = await client.cancel({
      jobId: activeGenerationCommand.job_id,
      requestSha256: activeGenerationCommand.request_sha256,
    });
    writeLog(command);
  } catch (error) {
    canceling = false;
    elements.cancel.disabled = false;
    elements.cancel.textContent = '生成をキャンセル';
    elements.generation.className = 'generation failed';
    elements.generation.textContent = 'キャンセル要求の送信に失敗しました。生成処理は継続しています。';
    writeLog({ type: 'CLIENT_ERROR', phase: 'cancel', error: String(error) });
  }
}

elements.connect.addEventListener('click', connect);
elements.inspect.addEventListener('click', inspectSelection);
elements.generate.addEventListener('click', generateWorld);
elements.cancel.addEventListener('click', cancelGeneration);
elements['artifact-select'].addEventListener('change', () => {
  updateArtifactSelection({ restoreSelection: true });
});
elements['viewer-visual'].addEventListener('change', (event) => changeViewerLayer(event.target));
elements['viewer-collider'].addEventListener('change', (event) => changeViewerLayer(event.target));
elements.download.addEventListener('click', () => {
  const job = selectedGeneratedJob();
  if (job !== null) window.location.assign(
    `/generated/${encodeURIComponent(job.job_id)}/artifact.zip`,
  );
});
elements.view3d.addEventListener('click', openSelectedViewer);
elements['delete-artifact'].addEventListener('click', deleteSelectedArtifact);
refreshSelection();
refreshGeneratedJobs();
connect();
