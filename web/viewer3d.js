/* Full-window 3D view of the loaded terrain: rotate (left drag), move (right drag), zoom (wheel).
   Heights (uint16, every 2nd landscape vertex) and the colour texture come from the app (api.view3d, served as
   /blob/...); the topo-map and satellite textures are stitched here from the same map tiles the main map uses (their own local port).
   Close / Esc work at any time, also while loading: every load checks `gen` and is dropped once the view was closed. */
import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const FT = 3.28084;
let R = null;          // { renderer, scene, camera, controls, mesh, skirt, pin, data, heights, tex, raf }
let renderer = null;   // one WebGL context for the app's lifetime (browsers cap how many a page may create)
let gen = 0;           // bumped by every open / close
const LOAD_MS = 90000;
const TOPO_MS = 180000;
const MAP_NAMES = { topo: 'topo map', sat: 'satellite imagery' };   // surfaces stitched from map tiles

function q(s) { return encodeURIComponent(s); }

function withTimeout(p, ms, what) {
  let t;
  const late = new Promise((_, reject) => { t = setTimeout(() => reject(new Error(what + ' took too long')), ms); });
  return Promise.race([p, late]).finally(() => clearTimeout(t));
}

function getRenderer() {
  if (!renderer) {
    renderer = new THREE.WebGLRenderer({ canvas: $('v3dCanvas'), antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  }
  return renderer;
}

async function loadHeights(token, ver) {
  const r = await fetch(`/blob/heights?t=${q(token)}&v=${ver}`);
  if (!r.ok) throw new Error('heights ' + r.status);
  return new Uint16Array(await r.arrayBuffer());
}

function loadTexture(url) {
  return new Promise((resolve, reject) => new THREE.TextureLoader().load(url, resolve, undefined, reject));
}

function buildTerrain(data, heights, exag) {
  const n = data.n, size = data.size_m, half = size / 2, step = size / (n - 1);
  const range = data.hmax - data.hmin;
  const pos = new Float32Array(n * n * 3);
  const uv = new Float32Array(n * n * 2);
  for (let r = 0, k = 0; r < n; r++) {
    for (let c = 0; c < n; c++, k++) {
      pos[k * 3] = c * step - half;
      pos[k * 3 + 1] = heights[k] / 65535 * range * exag;
      pos[k * 3 + 2] = r * step - half;              // row 0 = north = -Z
      uv[k * 2] = c / (n - 1);
      uv[k * 2 + 1] = 1 - r / (n - 1);
    }
  }
  const idx = new Uint32Array((n - 1) * (n - 1) * 6);
  for (let r = 0, k = 0; r < n - 1; r++) {
    for (let c = 0; c < n - 1; c++) {
      const a = r * n + c, b = a + 1, d = a + n, e = d + 1;
      idx[k++] = a; idx[k++] = d; idx[k++] = b;
      idx[k++] = b; idx[k++] = d; idx[k++] = e;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  return g;
}

function buildSkirt(data, heights, exag) {
  /* diorama-style walls from the terrain edge down to a base below the lowest point */
  const n = data.n, size = data.size_m, half = size / 2, step = size / (n - 1);
  const range = data.hmax - data.hmin, bottom = -Math.max(range * 0.08, size * 0.012) * exag;
  const edges = [];
  for (let c = 0; c < n; c++) edges.push([c, 0]);
  for (let r = 1; r < n; r++) edges.push([n - 1, r]);
  for (let c = n - 2; c >= 0; c--) edges.push([c, n - 1]);
  for (let r = n - 2; r > 0; r--) edges.push([0, r]);
  edges.push(edges[0]);
  const pos = new Float32Array(edges.length * 6);
  edges.forEach(([c, r], i) => {
    const x = c * step - half, z = r * step - half, y = heights[r * n + c] / 65535 * range * exag;
    pos.set([x, y, z, x, bottom, z], i * 6);
  });
  const idx = [];
  for (let i = 0; i < edges.length - 1; i++) {
    const a = i * 2, b = a + 1, c = a + 2, d = a + 3;
    idx.push(a, c, b, b, c, d);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

function heightAt(x, z) {
  const d = R.data, n = d.n, half = d.size_m / 2, step = d.size_m / (n - 1);
  const c = Math.max(0, Math.min(n - 1.001, (x + half) / step)), r = Math.max(0, Math.min(n - 1.001, (z + half) / step));
  const c0 = Math.floor(c), r0 = Math.floor(r), fc = c - c0, fr = r - r0, H = R.heights;
  const h = H[r0 * n + c0] * (1 - fc) * (1 - fr) + H[r0 * n + c0 + 1] * fc * (1 - fr)
    + H[(r0 + 1) * n + c0] * (1 - fc) * fr + H[(r0 + 1) * n + c0 + 1] * fc * fr;
  return h / 65535 * (d.hmax - d.hmin);
}

function makePin(size) {
  const grp = new THREE.Group();
  const s = size / 130;
  const red = new THREE.MeshStandardMaterial({ color: 0xb3263d, roughness: 0.5 });
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(s * 0.12, s * 0.12, s * 3.2, 12), new THREE.MeshStandardMaterial({ color: 0x3a2a1c }));
  pole.position.y = s * 1.6;
  const head = new THREE.Mesh(new THREE.SphereGeometry(s * 0.7, 20, 14), red);
  head.position.y = s * 3.4;
  grp.add(pole, head);
  return grp;
}

function setExaggeration(exag) {
  const g = R.mesh.geometry, pos = g.attributes.position.array, H = R.heights, range = R.data.hmax - R.data.hmin;
  for (let k = 0; k < H.length; k++) pos[k * 3 + 1] = H[k] / 65535 * range * exag;
  g.attributes.position.needsUpdate = true;
  g.computeVertexNormals();
  g.computeBoundingSphere();
  R.scene.remove(R.skirt);
  R.skirt.geometry.dispose();
  R.skirt.geometry = buildSkirt(R.data, H, exag);
  R.scene.add(R.skirt);
  if (R.pin && R.data.base) R.pin.position.y = heightAt(R.data.base[0], -R.data.base[1]) * exag;
  R.exag = exag;
}

async function mapTexture(layer, bbox, opts, onProgress) {
  /* stitch map tiles (topo or satellite) over the square (lat/lon box; mercator is ~linear over a few km).
     -> { tex, missing, total }; tex is null when no tile could be loaded (offline) */
  const lon2x = (lon, z) => (lon + 180) / 360 * 256 * 2 ** z;
  const lat2y = (lat, z) => { const s = Math.sin(lat * Math.PI / 180); return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z; };
  let z = 17;
  while (z > 9 && (lon2x(bbox.e, z) - lon2x(bbox.w, z)) > 1700) z--;
  const x0 = lon2x(bbox.w, z), x1 = lon2x(bbox.e, z), y0 = lat2y(bbox.n, z), y1 = lat2y(bbox.s, z);
  const tx0 = Math.floor(x0 / 256), tx1 = Math.floor(x1 / 256), ty0 = Math.floor(y0 / 256), ty1 = Math.floor(y1 / 256);
  const src = document.createElement('canvas');
  src.width = (tx1 - tx0 + 1) * 256; src.height = (ty1 - ty0 + 1) * 256;
  const ctx = src.getContext('2d');
  ctx.fillStyle = '#d8d2c4'; ctx.fillRect(0, 0, src.width, src.height);
  const jobs = [];
  let done = 0, drawn = 0;
  const total = (tx1 - tx0 + 1) * (ty1 - ty0 + 1);
  for (let ty = ty0; ty <= ty1; ty++) {
    for (let tx = tx0; tx <= tx1; tx++) {
      jobs.push(new Promise((res) => {
        const im = new Image();
        im.crossOrigin = 'anonymous';                    // other port: without CORS the canvas can't become a texture
        im.onload = () => {
          if (im.naturalWidth >= 256) { ctx.drawImage(im, (tx - tx0) * 256, (ty - ty0) * 256); drawn++; }  // 1 px = no tile
          onProgress(++done, total); res();
        };
        im.onerror = () => { onProgress(++done, total); res(); };
        im.src = `${opts.tiles || ''}/tile/${layer}/${z}/${tx}/${ty}.png?t=${q(opts.token)}`;
      }));
    }
  }
  await Promise.race([Promise.all(jobs), new Promise((res) => setTimeout(res, TOPO_MS))]);
  if (!drawn) return { tex: null, missing: total, total };
  const out = document.createElement('canvas');
  out.width = out.height = 2048;
  out.getContext('2d').drawImage(src, x0 - tx0 * 256, y0 - ty0 * 256, x1 - x0, y1 - y0, 0, 0, 2048, 2048);
  const t = new THREE.CanvasTexture(out);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = getRenderer().capabilities.getMaxAnisotropy();
  return { tex: t, missing: total - drawn, total };
}

function resetView() {
  const s = R.data.size_m, range = (R.data.hmax - R.data.hmin) * R.exag;
  R.controls.target.set(0, range * 0.35, 0);
  R.camera.position.set(-s * 0.62, range * 0.35 + s * 0.62, s * 0.78);
  R.controls.update();
}

function placeLabel(el, x, y, z) {
  const v = new THREE.Vector3(x, y, z).project(R.camera);
  const on = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05;
  el.classList.toggle('hidden', !on);
  if (on) {
    el.style.left = ((v.x + 1) / 2 * window.innerWidth) + 'px';
    el.style.top = ((1 - v.y) / 2 * window.innerHeight) + 'px';
  }
}

function loop() {
  R.raf = requestAnimationFrame(loop);
  R.controls.update();
  const s = R.data.size_m;
  if (R.data.base) placeLabel($('v3dBaseLbl'), R.data.base[0], heightAt(R.data.base[0], -R.data.base[1]) * R.exag + s / 130 * 4.4, -R.data.base[1]);
  placeLabel($('v3dPeakLbl'), R.data.peak[0], heightAt(R.data.peak[0], -R.data.peak[1]) * R.exag + s / 250, -R.data.peak[1]);
  const v = new THREE.Vector3().subVectors(R.camera.position, R.controls.target);
  $('v3dCompass').style.transform = `rotate(${Math.atan2(v.x, v.z) * 180 / Math.PI}deg)`;
  R.renderer.render(R.scene, R.camera);
}

function onResize() {
  if (!R) return;
  const w = window.innerWidth, h = window.innerHeight;
  R.renderer.setSize(w, h);
  R.camera.aspect = w / h;
  R.camera.updateProjectionMatrix();
}

function onKey(e) { if (e.key === 'Escape') close(); }

let noteTimer = 0;
function setStatus(text, kind = 'busy') {
  /* kind: 'busy' (spinner; the scene is tinted while nothing is drawn yet) or 'note' (a message that goes away) */
  clearTimeout(noteTimer);
  const el = $('v3dLoading');
  el.classList.toggle('hidden', !text);
  el.classList.toggle('dim', !!text && kind === 'busy' && !R);
  el.classList.toggle('note', !!text && kind === 'note');
  $('v3d').classList.toggle('busy', !!text && kind === 'busy' && !R);
  $('v3dLoadingText').textContent = text || '';
  if (text && kind === 'note') noteTimer = setTimeout(() => setStatus(''), 5000);
}

export async function open(opts) {
  close();
  const my = gen;
  const fail = (msg) => { if (my !== gen) return; close(); if (opts.onError) opts.onError(msg); };
  $('v3d').classList.remove('hidden');
  $('v3dCanvas').style.visibility = 'hidden';       // no last frame of a previous terrain while loading
  $('v3dTitle').textContent = opts.name || 'Terrain';
  $('v3dInfo').textContent = '';
  $('v3dBaseLbl').classList.add('hidden');
  $('v3dPeakLbl').classList.add('hidden');
  window.addEventListener('resize', onResize);
  window.addEventListener('keydown', onKey);
  setStatus('Building the 3D view...');
  let res;
  try {
    res = await withTimeout(window.pywebview.api.view3d(opts.treeline), LOAD_MS, 'Building the 3D view');
  } catch (e) {
    return fail('The 3D view could not be built (' + (e && e.message || e) + '). Please try again.');
  }
  if (my !== gen) return;
  if (!res || !res.ok) return fail(res && res.error || 'No answer from the app.');
  try {
    setStatus('Loading the terrain...');
    const [heights, tex] = await withTimeout(Promise.all([loadHeights(opts.token, res.version),
      loadTexture(`/blob/albedo?t=${q(opts.token)}&v=${res.version}`)]), LOAD_MS, 'Loading the terrain');
    if (my !== gen) { tex.dispose(); return; }
    const renderer = getRenderer();
    const s = res.size_m, range = res.hmax - res.hmin;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xb6cfe2);
    scene.fog = new THREE.Fog(0xb6cfe2, s * 1.6, s * 4.5);
    const camera = new THREE.PerspectiveCamera(42, 1, s / 2000, s * 8);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    R = { renderer, scene, camera, data: res, heights, tex: { terrain: tex, topo: null, sat: null }, exag: opts.exag || 1.5 };
    const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95, metalness: 0 });
    R.mesh = new THREE.Mesh(buildTerrain(res, heights, R.exag), mat);
    R.mesh.castShadow = R.mesh.receiveShadow = true;
    R.skirt = new THREE.Mesh(buildSkirt(res, heights, R.exag),
      new THREE.MeshStandardMaterial({ color: 0x6b4a34, roughness: 1, side: THREE.DoubleSide }));
    scene.add(R.mesh, R.skirt);
    scene.add(new THREE.HemisphereLight(0xe3eef7, 0x6a5a4c, 1.05));
    const sun = new THREE.DirectionalLight(0xfff3e0, 2.1);
    sun.position.set(-s * 0.8, range * 2 + s * 0.7, -s * 0.35);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    const sc = sun.shadow.camera;
    sc.left = sc.bottom = -s * 0.75; sc.right = sc.top = s * 0.75; sc.near = s * 0.05; sc.far = s * 4;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = s / 3000;
    scene.add(sun, sun.target);
    if (res.base) {
      R.pin = makePin(s);
      R.pin.position.set(res.base[0], heightAt(res.base[0], -res.base[1]) * R.exag, -res.base[1]);
      R.pin.traverse((o) => { o.castShadow = true; });
      scene.add(R.pin);
    }
    R.controls = new OrbitControls(camera, renderer.domElement);
    R.controls.enableDamping = true;
    R.controls.dampingFactor = 0.08;
    R.controls.screenSpacePanning = false;
    R.controls.maxPolarAngle = Math.PI * 0.48;
    R.controls.minDistance = s * 0.01;
    R.controls.maxDistance = s * 3;
    R.controls.zoomSpeed = 1.2;
    onResize();
    resetView();
    $('v3dExag').value = R.exag;
    $('v3dExagOut').textContent = R.exag.toFixed(1) + '×';
    document.querySelectorAll('[data-tex]').forEach((b) => b.classList.toggle('on', b.dataset.tex === 'terrain'));
    $('v3dPeakLbl').textContent = 'Peak ' + (opts.units === 'm' ? Math.round(res.hmax) + ' m' : Math.round(res.hmax * FT).toLocaleString('en-US') + ' ft');
    $('v3dBaseLbl').classList.toggle('hidden', !res.base);
    $('v3dInfo').textContent = `${(s / 1000).toFixed(1)} km × ${(s / 1000).toFixed(1)} km · ` +
      (opts.units === 'm' ? `${Math.round(res.hmin)}–${Math.round(res.hmax)} m` :
        `${Math.round(res.hmin * FT).toLocaleString('en-US')}–${Math.round(res.hmax * FT).toLocaleString('en-US')} ft`);
    R.opts = opts;
    setStatus('');
    $('v3dPeakLbl').classList.remove('hidden');
    $('v3dCanvas').style.visibility = '';
    loop();
  } catch (e) {
    fail('The 3D view could not be shown (' + (e && e.message || e) + '). Please try again.');
  }
}

export function close() {
  gen++;                                            // drops any load still under way
  setStatus('');
  $('v3d').classList.add('hidden');
  window.removeEventListener('resize', onResize);
  window.removeEventListener('keydown', onKey);
  if (!R) return;
  cancelAnimationFrame(R.raf);
  R.controls && R.controls.dispose();
  R.scene.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  });
  Object.values(R.tex).forEach((t) => t && t.dispose());
  R.renderer.renderLists.dispose();
  R = null;
}

$('v3dClose').onclick = close;
$('v3dReset').onclick = () => R && resetView();
$('v3dExag').oninput = () => {
  if (!R) return;
  const v = +$('v3dExag').value;
  $('v3dExagOut').textContent = v.toFixed(1) + '×';
  setExaggeration(v);
};
document.querySelectorAll('[data-tex]').forEach((b) => {
  b.onclick = async () => {
    if (!R || R.mapLoading) return;
    const want = b.dataset.tex, my = gen, what = MAP_NAMES[want];
    if (what && !R.tex[want]) {
      R.mapLoading = true;
      setStatus(`Loading the ${what}...`);
      let got;
      try {
        got = await mapTexture(want, R.data.bbox, R.opts, (k, n) => { if (my === gen) setStatus(`Loading the ${what} (${k} of ${n})...`); });
      } catch (e) {
        got = { tex: null };
      }
      if (my !== gen) { if (got.tex) got.tex.dispose(); return; }
      R.mapLoading = false;
      if (!got.tex) return setStatus(`The ${what} could not be loaded. Check the internet connection and try again.`, 'note');
      R.tex[want] = got.tex;
      setStatus(got.missing ? `Part of the ${what} could not be loaded (no connection?).` : '', 'note');
    }
    if (!R) return;
    R.mesh.material.map = R.tex[want];
    R.mesh.material.needsUpdate = true;
    document.querySelectorAll('[data-tex]').forEach((x) => x.classList.toggle('on', x.dataset.tex === want));
  };
});

window.terrain3d = { open, close };
