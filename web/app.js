'use strict';
/* Ski-E-O Map Creator -- window logic. Talks to Python through window.pywebview.api (see skieo_creator/api.py);
   progress of background jobs arrives through window.app.onEvent(kind, payload). */

const $ = (id) => document.getElementById(id);
const FT = 3.28084;
const Q = new URLSearchParams(location.search);
const S = {
  token: Q.get('t') || '',
  // map tiles come from their own local port, so slow / failing tile downloads never hold up the app's requests
  tiles: /^\d+$/.test(Q.get('tp') || '') ? `http://127.0.0.1:${Q.get('tp')}` : '',
  units: 'ft', layer: 'topo', game: null, biomes: [], limits: { min_km: 3, max_km: 12 },
  sq: { lat: 45.92, lon: 6.88, km: 8 }, preview: null, job: null, maps: [], overlayOn: true,
  touched: { biome: false, treeline: false, snow: false, name: false }, suggestName: '',
  dragging: false, busyPoll: null,
};

async function call(name, ...args) {
  try {
    const r = await window.pywebview.api[name](...args);
    return r || { ok: false, error: 'No answer from the app.' };
  } catch (e) {
    return { ok: false, error: String(e && e.message || e) };
  }
}

/* ------------------------------------------------------------ formatting */

function fmtElev(m) {
  if (m == null || isNaN(m)) return '-';
  return S.units === 'ft' ? Math.round(m * FT).toLocaleString('en-US') + ' ft' : Math.round(m).toLocaleString('en-US') + ' m';
}
function fmtSize(km) {
  const mi = km * 0.621371;
  return S.units === 'ft' ? `${km.toFixed(1)} km (${mi.toFixed(1)} mi)` : `${km.toFixed(1)} km`;
}
function toUnits(m) { return S.units === 'ft' ? m * FT : m; }
function fromUnits(v) { return S.units === 'ft' ? v / FT : v; }
function unitName() { return S.units === 'ft' ? 'ft' : 'm'; }
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ------------------------------------------------------------ toast / modal */

let toastTimer = null;
function toast(msg, bad = false, ms = 4200) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), ms);
}
function confirmBox(title, text, yes = 'OK', danger = true) {
  return new Promise((resolve) => {
    $('modalTitle').textContent = title;
    $('modalText').textContent = text;
    $('modalYes').textContent = yes;
    $('modalYes').className = 'gbtn ' + (danger ? 'crimson' : 'green');
    $('modalNo').classList.toggle('hidden', yes === null);
    $('modal').classList.remove('hidden');
    const done = (v) => { $('modal').classList.add('hidden'); $('modalYes').onclick = $('modalNo').onclick = null; resolve(v); };
    $('modalYes').onclick = () => done(true);
    $('modalNo').onclick = () => done(false);
  });
}
function errorBox(text) {
  $('modalNo').textContent = 'Copy diagnostics';
  return confirmBox('Something went wrong', text, 'OK', false).then((ok) => {
    $('modalNo').textContent = 'Cancel';
    if (!ok) copyDiagnostics();
  });
}

/* ------------------------------------------------------------ map + selection square */

const map = L.map('map', { zoomControl: true, worldCopyJump: true, minZoom: 3, maxZoom: 17, zoomSnap: 0.5 });
map.zoomControl.setPosition('topleft');
L.control.scale({ position: 'bottomleft', imperial: true, metric: true, maxWidth: 160 }).addTo(map);
const layers = {};
function tileLayer(id, maxNative) {
  return L.tileLayer(`${S.tiles}/tile/${id}/{z}/{x}/{y}.png?t=${encodeURIComponent(S.token)}`,
    { maxZoom: 18, maxNativeZoom: maxNative, keepBuffer: 3, updateWhenIdle: false });
}
function setLayer(id) {
  S.layer = layers[id] ? id : 'topo';
  for (const [k, l] of Object.entries(layers)) { if (k !== S.layer && map.hasLayer(l)) map.removeLayer(l); }
  if (!map.hasLayer(layers[S.layer])) layers[S.layer].addTo(map);
  document.querySelectorAll('[data-layer]').forEach((b) => b.classList.toggle('on', b.dataset.layer === S.layer));
}

function mPerDeg(lat) {
  const p = lat * Math.PI / 180;
  return { lat: 111132.954 - 559.822 * Math.cos(2 * p) + 1.175 * Math.cos(4 * p),
           lon: 111412.84 * Math.cos(p) - 93.5 * Math.cos(3 * p) };
}
function corners(lat, lon, km) {
  const m = mPerDeg(lat), h = km * 500;
  const dl = h / m.lat, dn = h / m.lon;
  return [[lat + dl, lon - dn], [lat + dl, lon + dn], [lat - dl, lon + dn], [lat - dl, lon - dn]];
}
function toLocal(lat, lon) {            // metres east / north of the square's centre
  const m = mPerDeg(S.sq.lat);
  return { x: (lon - S.sq.lon) * m.lon, y: (lat - S.sq.lat) * m.lat };
}
function fromLocal(x, y) {
  const m = mPerDeg(S.sq.lat);
  return L.latLng(S.sq.lat + y / m.lat, S.sq.lon + x / m.lon);
}

const sqStyle = { color: '#b3263d', weight: 3, fillColor: '#ffffff', fillOpacity: 0.12, className: 'sq' };
const square = L.polygon(corners(S.sq.lat, S.sq.lon, S.sq.km), sqStyle).addTo(map);
const sqLabel = L.tooltip({ permanent: true, direction: 'top', className: 'sq-label', offset: [0, -2] });
const handle = L.marker([S.sq.lat, S.sq.lon], {
  draggable: true, zIndexOffset: 500, keyboard: false, title: 'Drag to move the area',
  icon: L.divIcon({ className: '', iconSize: [30, 30], iconAnchor: [15, 15],
    html: '<div class="move-handle"><svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/></svg></div>' }),
}).addTo(map);
let overlay = null;

function drawSquare() {
  const c = corners(S.sq.lat, S.sq.lon, S.sq.km);
  square.setLatLngs(c);
  handle.setLatLng([S.sq.lat, S.sq.lon]);
  sqLabel.setLatLng([c[0][0], S.sq.lon]).setContent(`${S.sq.km.toFixed(1)} × ${S.sq.km.toFixed(1)} km`);
  if (!map.hasLayer(sqLabel)) sqLabel.addTo(map);
  showNum('size');
  $('areaMeta').textContent = `${S.sq.lat.toFixed(5)}, ${S.sq.lon.toFixed(5)}`;
}

function moveSquare(lat, lon) {
  S.sq.lat = Math.max(-84, Math.min(84, lat));
  S.sq.lon = ((lon + 540) % 360) - 180;
  drawSquare();
  areaChanged();
}

let viewTimer = null;
function areaChanged() {
  const p = S.preview;
  const same = p && Math.abs(p.lat - S.sq.lat) < 1e-7 && Math.abs(p.lon - S.sq.lon) < 1e-7 && Math.abs(p.size_km - S.sq.km) < 1e-9;
  if (p && !same) {
    S.preview = null;
    if (overlay) { map.removeLayer(overlay); overlay = null; }
    $('overlayBtn').disabled = true;
    lockTerrain(true);
    toast('The area changed. Press "Load terrain" to load it.');
  }
  updateButtons();
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => call('set_setting', 'view', { lat: S.sq.lat, lon: S.sq.lon, km: S.sq.km, zoom: map.getZoom() }), 800);
}

handle.on('drag', () => { const ll = handle.getLatLng(); S.sq.lat = ll.lat; S.sq.lon = ll.lng; drawSquare(); });
handle.on('dragend', () => moveSquare(S.sq.lat, S.sq.lon));
let dragFrom = null;
square.on('mousedown', (e) => {
  if (busy()) return;
  S.dragging = true;
  dragFrom = { ll: e.latlng, lat: S.sq.lat, lon: S.sq.lon };
  map.dragging.disable();
  L.DomEvent.stop(e);
});
map.on('mousemove', (e) => {
  if (!S.dragging || !dragFrom) return;
  S.sq.lat = dragFrom.lat + (e.latlng.lat - dragFrom.ll.lat);
  S.sq.lon = dragFrom.lon + (e.latlng.lng - dragFrom.ll.lng);
  drawSquare();
});
function endDrag() {
  if (!S.dragging) return;
  S.dragging = false;
  map.dragging.enable();
  const moved = dragFrom && (Math.abs(dragFrom.lat - S.sq.lat) > 1e-9 || Math.abs(dragFrom.lon - S.sq.lon) > 1e-9);
  dragFrom = null;
  if (moved) moveSquare(S.sq.lat, S.sq.lon);
  setTimeout(() => { S.justDragged = false; }, 0);
  S.justDragged = moved;
}
map.on('mouseup', endDrag);
document.addEventListener('mouseup', endDrag);
map.on('click', (e) => {
  if (S.justDragged || busy()) return;
  const p = toLocal(e.latlng.lat, e.latlng.lng), h = S.sq.km * 500;
  if (Math.abs(p.x) <= h && Math.abs(p.y) <= h) return;       // clicks inside the square don't move it
  moveSquare(e.latlng.lat, e.latlng.lng);
  hideHint();
});

let hintHidden = false;
function hideHint() { if (!hintHidden) { hintHidden = true; $('mapHint').style.opacity = 0; } }
map.on('dragstart', hideHint);                 // user drags only (setView / fitBounds also fire zoomstart)

/* ------------------------------------------------------------ panel state */

function busy() { return S.job && (S.job.state === 'running' || S.job.state === 'waiting'); }
function lockTerrain(lock) {
  $('cardTerrain').classList.toggle('locked', lock);
  $('cardSettings').classList.toggle('locked', lock);
  if (lock) {
    $('diorama').removeAttribute('src');
    $('dioEmpty').classList.remove('hidden');
    $('photo').classList.remove('ready');
    $('photoBadge').classList.add('hidden');
    $('stats').innerHTML = '';
    $('warnings').innerHTML = '';
    $('srcTag').textContent = '';
  }
}
function updateButtons() {
  const g = S.game;
  const ready = g && g.found;
  $('loadBtn').disabled = busy();
  $('createBtn').disabled = busy() || !S.preview || !ready || !$('name').value.trim() || S.nameBad;
  $('size').disabled = $('sizeOut').disabled = busy();
  document.querySelectorAll('.mact .link').forEach((b) => { b.disabled = busy(); });
}

function renderGame(g) {
  S.game = g;
  const banner = $('banner');
  banner.className = 'banner hidden';
  if (!g || !g.found) {
    setStatus('bad', 'Game not found', 'Click to show the creator where Ski-E-O is installed');
    banner.className = 'banner';
    banner.innerHTML = 'Ski-E-O! was not found on this PC. If it is installed, show the creator where.' +
      '<br><button class="gbtn green" id="locateBtn">Locate game folder</button>';
    $('locateBtn').onclick = locateGame;
  } else if (!g.ready) {
    setStatus('warn', 'Reading game files...', '');
  } else if (g.running) {
    setStatus('warn', 'Ski-E-O is running', 'Maps are built while the game runs and installed when you close it.');
  } else {
    setStatus('ok', 'Ski-E-O ready', `${g.folder}\nbuild ${g.build || '?'}`);
  }
  updateButtons();
}

function setStatus(kind, text, title) {
  const p = $('gamePill');
  p.className = 'status ' + kind;
  p.querySelector('span').textContent = text;
  p.title = title || '';
}

async function locateGame() {
  const r = await call('pick_game_folder');
  if (!r.ok) return errorBox(r.error);
  renderGame(r.game);
  if (r.game.found && !r.game.ready) prepareGame();
}
async function refreshGame() {
  if (busy()) return;
  const r = await call('game_status');
  if (r.ok) renderGame(r);
}
async function prepareGame() {
  const r = await call('prepare_game');
  if (!r.ok) return errorBox(r.error);
  startJob(r);
}

/* ------------------------------------------------------------ jobs */

const JOB_TITLES = { prepare: 'Preparing', preview: 'Loading terrain', create: 'Creating map', install: 'Installing',
  uninstall: 'Removing', delete: 'Deleting', rebuild: 'Rebuilding', repair: 'Repairing the map list' };

function startJob(j) {
  S.job = j;
  renderJob();
  clearInterval(S.busyPoll);
  S.busyPoll = setInterval(async () => {
    const r = await call('job');
    if (r.ok && r.id) window.app.onEvent('job', r);
  }, 700);
}

function renderJob() {
  const j = S.job;
  const box = $('jobBox');
  const active = j && (j.state === 'running' || j.state === 'waiting');
  const anchor = j && j.kind === 'preview' ? $('loadBtn') : $('createBtn');
  if (box.previousElementSibling !== anchor) anchor.after(box);
  const inPanel = j && ['preview', 'create', 'install', 'rebuild', 'uninstall', 'delete', 'repair', 'prepare'].includes(j.kind);
  box.classList.toggle('hidden', !(active && inPanel));
  box.classList.toggle('waiting', !!(j && j.state === 'waiting'));
  if (active) {
    $('jobText').textContent = j.text || (JOB_TITLES[j.kind] || 'Working') + '...';
    $('jobBar').style.width = Math.round((j.fraction || 0) * 100) + '%';
    if (j.kind === 'prepare') setStatus('warn', 'Reading game files...', '');
  }
  updateButtons();
}

window.app = {
  onEvent(kind, p) {
    if (kind !== 'job' || !p) return;
    const prev = S.job;
    if (prev && prev.id === p.id && !(prev.state === 'running' || prev.state === 'waiting')) return;   // already handled
    S.job = p;
    renderJob();
    if (p.state === 'running' || p.state === 'waiting') return;
    clearInterval(S.busyPoll);
    jobFinished(p);
  },
};

function jobFinished(j) {
  if (j.state === 'cancelled') { toast('Cancelled'); return refreshGame(); }
  if (j.state === 'error') { refreshGame(); return errorBox(j.error); }
  const r = j.result || {};
  if (j.kind === 'prepare') { renderGame(r); toast('Game files ready'); }
  if (j.kind === 'preview') showPreview(r);
  if (j.kind === 'create') { showDone(r); loadMaps(); }
  if (['install', 'uninstall', 'rebuild'].includes(j.kind)) {
    toast({ install: 'Installed in Ski-E-O', uninstall: 'Removed from Ski-E-O', rebuild: 'Rebuilt and installed' }[j.kind]);
    loadMaps();
  }
  if (j.kind === 'delete') { toast('Map deleted'); loadMaps(); }
  if (j.kind === 'repair') toast('Map list rebuilt');
  refreshGame();
}

$('cancelBtn').onclick = () => call('cancel');

/* ------------------------------------------------------------ preview */

$('loadBtn').onclick = async () => {
  hideHint();
  $('doneBox').classList.add('hidden');
  const r = await call('preview', S.sq.lat, S.sq.lon, S.sq.km);
  if (!r.ok) return errorBox(r.error);
  startJob(r);
};

function showPreview(p) {
  S.preview = p;
  lockTerrain(false);
  $('diorama').src = p.diorama;
  $('dioEmpty').classList.add('hidden');
  $('photo').classList.add('ready');
  $('photoBadge').classList.remove('hidden');
  $('srcTag').textContent = p.source;
  $('srcTag').title = p.credit;
  $('stats').innerHTML = [
    ['Peak', fmtElev(p.peak_m)], ['Base', fmtElev(p.base_m)], ['Vertical', fmtElev(p.relief_m)],
    ['Avg slope', Math.round(p.mean_slope) + '°'],
  ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
  $('warnings').innerHTML = (p.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join('');
  if (overlay) map.removeLayer(overlay);
  const c = corners(p.lat, p.lon, p.size_km);
  overlay = L.imageOverlay(p.shade, [[c[2][0], c[0][1]], [c[0][0], c[1][1]]], { opacity: 0.55, interactive: false });
  if (S.overlayOn) overlay.addTo(map);
  $('overlayBtn').disabled = false;
  $('overlayBtn').classList.toggle('on', S.overlayOn);
  applyDefaults(p.defaults);
  if (!S.touched.name && S.suggestName) $('name').value = S.suggestName;
  checkName();
  updateButtons();
  $('cardSettings').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

$('photo').onclick = () => {
  if (!S.preview || !window.terrain3d) return;
  window.terrain3d.open({
    token: S.token, tiles: S.tiles, name: $('name').value.trim() || S.suggestName || 'Terrain', units: S.units,
    treeline: +$('treeline').value, onError: (msg) => errorBox(msg),
  });
};

$('overlayBtn').onclick = () => {
  S.overlayOn = !S.overlayOn;
  $('overlayBtn').classList.toggle('on', S.overlayOn);
  if (overlay) { if (S.overlayOn) overlay.addTo(map); else map.removeLayer(overlay); }
};

/* ------------------------------------------------------------ settings */

function fillBiomes(list) {
  $('biome').innerHTML = list.map((b) => `<option value="${esc(b.id)}">${esc(b.label)}</option>`).join('');
}
function applyDefaults(d) {
  if (!d) return;
  S.defaults = d;
  if (!S.touched.biome) $('biome').value = d.biome;
  $('biomeHint').textContent = `Automatic: nearest real resort in the game is ${d.near} (${d.near_km.toLocaleString('en-US')} km)`;
  if (!S.touched.treeline) $('treeline').value = autoTreeline();
  if (!S.touched.snow) $('snow').value = (d.snows || {})[$('biome').value] ?? d.snow;
  renderSettings();
}
function renderSettings() {
  $('tlAuto').classList.toggle('hidden', !S.touched.treeline);
  ['treeline', 'snow', 'valley', 'slope', 'trees'].forEach(showNum);
  renderSnowHint();
}
function renderSnowHint() {
  /* the biome's reference snowfall (the game's AverageAnnualSnowfallInches) times the multiplier: a rough guide, the
     game also varies snowfall with elevation, temperature and each season's weather */
  const b = (S.biomes || []).find((x) => x.id === $('biome').value);
  if (!b || !b.inches) { $('snowHint').textContent = ''; return; }
  const inch = b.inches * +$('snow').value, cm = inch * 2.54;
  const fmt = (v, u) => Math.round(v).toLocaleString('en-US') + ' ' + u;
  const est = S.units === 'ft' ? `${fmt(inch, 'in')} (${fmt(cm, 'cm')})` : `${fmt(cm, 'cm')} (${fmt(inch, 'in')})`;
  const avg = S.units === 'ft' ? fmt(b.inches, 'in') : fmt(b.inches * 2.54, 'cm');
  $('snowHint').textContent = `About ${est} a season (estimate from the biome's ${avg} average)`;
  $('snowHint').title = 'Rough guide: the game also varies snowfall with elevation, temperature and each season\'s weather.';
}

/* The value next to each slider is a box you can type into: Enter (or clicking away) applies it, Esc undoes.
   Values are typed in the shown units and kept inside the slider's range. */
const clampTo = (el, v) => Math.min(+el.max, Math.max(+el.min, v));
const NUMS = {
  size: {
    box: 'sizeOut', unit: 'sizeUnit', decimal: true,
    show: () => [S.sq.km.toFixed(1), S.units === 'ft' ? `km (${(S.sq.km * 0.621371).toFixed(1)} mi)` : 'km'],
    apply: (v) => {
      S.sq.km = Math.round(clampTo($('size'), v) * 10) / 10;
      $('size').value = S.sq.km;
      moveSquare(S.sq.lat, S.sq.lon); fitSquare();
    },
  },
  treeline: {
    box: 'tlOut', unit: 'tlUnit', zero: 'none',
    show: () => { const m = +$('treeline').value; return m <= 0 ? ['none', '(trees everywhere)'] : [Math.round(toUnits(m)).toLocaleString('en-US'), unitName()]; },
    apply: (v) => { $('treeline').value = clampTo($('treeline'), fromUnits(v)); S.touched.treeline = true; renderSettings(); },
  },
  snow: {
    box: 'snowOut', unit: 'snowUnit', decimal: true,
    show: () => [(+$('snow').value).toFixed(2), '×'],
    apply: (v) => { $('snow').value = clampTo($('snow'), Math.round(v * 100) / 100); S.touched.snow = true; renderSettings(); },
  },
  valley: {
    box: 'valOut', unit: 'valUnit', zero: 'off',
    show: () => { const m = +$('valley').value; return m <= 0 ? ['off', ''] : [Math.round(toUnits(m)).toLocaleString('en-US'), unitName()]; },
    apply: (v) => { $('valley').value = clampTo($('valley'), fromUnits(v)); renderSettings(); },
  },
  slope: {
    box: 'slopeOut', unit: 'slopeUnit',
    show: () => [String(Math.round(+$('slope').value)), '°'],
    apply: (v) => { $('slope').value = clampTo($('slope'), Math.round(v)); renderSettings(); },
  },
  trees: {
    box: 'treesOut', unit: 'treesUnit',
    show: () => [(+$('trees').value).toLocaleString('en-US'), ''],
    apply: (v) => { $('trees').value = clampTo($('trees'), Math.round(v / 1000) * 1000); saveTrees(); renderSettings(); },
  },
};
function saveTrees() { call('set_setting', 'max_trees', +$('trees').value); }   // a slow PC wants the same next time
function showNum(key, force) {
  const n = NUMS[key], box = $(n.box);
  if (!force && document.activeElement === box) return;      // don't overwrite what is being typed
  const [text, unit] = n.show();
  box.value = box.dataset.shown = text;
  $(n.unit).textContent = unit;
}
function parseNum(t, decimal) {
  /* "0,93" and "0.93" both work (most of Europe types a decimal comma). In whole-number boxes a , or . before exactly
     three digits groups thousands: 11,300 ft / 3.500 m. */
  let s = t.replace(/[^0-9.,\-]/g, '');
  if (!decimal) s = s.replace(/[.,](?=\d{3}(?!\d))/g, '');
  return parseFloat(s.replace(',', '.'));
}
function commitNum(key) {
  const n = NUMS[key], box = $(n.box), t = box.value.trim().toLowerCase();
  if (t !== (box.dataset.shown || '').toLowerCase()) {      // only when something was typed: tabbing through changes nothing
    const v = n.zero && (t === '' || t === n.zero || t === 'no' || t === 'none') ? 0 : parseNum(t, n.decimal);
    if (isFinite(v)) n.apply(v);
  }
  showNum(key, true);
}
Object.entries(NUMS).forEach(([key, n]) => {
  const box = $(n.box);
  box.onfocus = () => box.select();
  box.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); commitNum(key); box.blur(); }
    if (e.key === 'Escape') { showNum(key, true); box.blur(); }
  };
  box.onblur = () => commitNum(key);
});
function autoTreeline() {
  const d = S.defaults;
  return d ? (d.treelines || {})[$('biome').value] ?? d.treeline : 0;
}
$('biome').onchange = () => {
  S.touched.biome = true;
  const d = S.defaults;
  if (d) {                     // the chosen biome's own treeline / snowfall at this latitude
    if (!S.touched.treeline) $('treeline').value = autoTreeline();
    if (!S.touched.snow && d.snows) $('snow').value = d.snows[$('biome').value];
  }
  renderSettings();
};
$('treeline').oninput = () => { S.touched.treeline = true; renderSettings(); };
$('tlAuto').onclick = () => { S.touched.treeline = false; $('treeline').value = autoTreeline(); renderSettings(); };
$('snow').oninput = () => { S.touched.snow = true; renderSettings(); };
$('valley').oninput = renderSettings;
$('slope').oninput = renderSettings;
$('trees').oninput = renderSettings;
$('trees').onchange = saveTrees;
let nameTimer = null;
function checkName() {
  clearTimeout(nameTimer);
  nameTimer = setTimeout(async () => {
    const r = await call('name_ok', $('name').value);
    S.nameBad = !!(r.ok && !r.valid && r.message);
    $('nameErr').textContent = S.nameBad ? r.message : '';
    $('nameErr').classList.toggle('hidden', !S.nameBad);
    $('name').classList.toggle('bad', S.nameBad);
    updateButtons();
  }, 220);
}
/* Names keep to letters A-Z, numbers, spaces and - ' . & ( ): accents become plain letters, anything else is dropped
   (the same rule as api.NAME_CHARS) */
const NAME_SWAPS = { 'ß': 'ss', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE', 'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D', 'þ': 'th' };
function cleanName(s) {
  return s.replace(/[ßæÆœŒøØłŁđĐþ]/g, (c) => NAME_SWAPS[c]).normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Za-z0-9 '.&()\-]/g, '');
}
let nameHintTimer = 0;
$('name').oninput = () => {
  const box = $('name'), v = box.value, c = cleanName(v);
  if (c !== v) {
    const caret = Math.max(0, (box.selectionStart || 0) - (v.length - c.length));
    box.value = c;
    box.setSelectionRange(caret, caret);
    $('nameHint').classList.remove('hidden');
    clearTimeout(nameHintTimer);
    nameHintTimer = setTimeout(() => $('nameHint').classList.add('hidden'), 5000);
  }
  S.touched.name = true; updateButtons(); checkName();
};
$('size').oninput = () => { S.sq.km = +$('size').value; drawSquare(); };
$('size').onchange = () => { S.sq.km = +$('size').value; moveSquare(S.sq.lat, S.sq.lon); fitSquare(); };

function fitSquare() {
  const c = corners(S.sq.lat, S.sq.lon, S.sq.km);
  map.fitBounds([[c[2][0], c[0][1]], [c[0][0], c[1][1]]], { padding: [60, 60], maxZoom: 14 });
}

/* ------------------------------------------------------------ create */

$('createBtn').onclick = async () => {
  const name = $('name').value.trim();
  if (!name) { $('name').focus(); return toast('Give the map a name first', true); }
  const r = await call('create', {
    name, lat: S.preview.lat, lon: S.preview.lon, size_km: S.preview.size_km, biome: $('biome').value,
    treeline_m: +$('treeline').value, snow: +$('snow').value, valley_m: +$('valley').value, max_trees: +$('trees').value,
    tree_slope: +$('slope').value,
    base: S.preview.base,             // flattest low ground, picked when the terrain loaded
  });
  if (!r.ok) return errorBox(r.error);
  $('doneBox').classList.add('hidden');
  startJob(r);
};

function showDone(m) {
  $('doneImg').src = m.thumb || '';
  $('doneTitle').textContent = m.name;
  $('doneText').textContent = `Start Ski-E-O, choose New Resort and pick ${m.name} on the map list ` +
    `(with the ${m.biome_label} maps).` + (m.stats && m.stats.trees != null ? ` ${m.stats.trees.toLocaleString('en-US')} trees planted.` : '');
  $('doneBox').classList.remove('hidden');
  $('doneBox').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
$('playBtn').onclick = () => call('play');
$('againBtn').onclick = () => {
  $('doneBox').classList.add('hidden');
  $('name').value = '';
  S.touched = { biome: false, treeline: false, snow: false, name: false };
  S.preview = null;
  if (overlay) { map.removeLayer(overlay); overlay = null; }
  $('overlayBtn').disabled = true;
  lockTerrain(true);
  updateButtons();
  $('q').focus();
};

/* ------------------------------------------------------------ search */

let results = [], resSel = -1;
async function doSearch() {
  const q = $('q').value.trim();
  if (!q) return;
  const box = $('results');
  box.innerHTML = '<div class="row none">Searching...</div>';
  box.classList.remove('hidden');
  const r = await call('search', q);
  if (!r.ok) { box.innerHTML = `<div class="row none">${esc(r.error)}</div>`; return; }
  results = r.results || [];
  resSel = -1;
  box.innerHTML = results.length ? results.map((x, i) =>
    `<button class="row" data-i="${i}"><div>${esc(x.label)}</div><div class="k">${esc(x.kind || '')}</div></button>`).join('')
    : '<div class="row none">Nothing found. Try another name, or type coordinates like 46.02, 7.75</div>';
  box.querySelectorAll('.row[data-i]').forEach((el) => { el.onclick = () => pickResult(+el.dataset.i); });
}
function pickResult(i) {
  const x = results[i];
  if (!x) return;
  $('results').classList.add('hidden');
  S.suggestName = cleanName((x.name || (x.label || '').split(',')[0]).trim()
    .replace(/\s+(ski\s+(resort|area|hill|cent(re|er))|resort|mountain\s+resort)$/i, '')).replace(/\s+/g, ' ').trim().slice(0, 40);
  if (!S.touched.name && S.preview) { $('name').value = S.suggestName; checkName(); }
  if (x.size_km) { S.sq.km = x.size_km; $('size').value = x.size_km; }
  moveSquare(x.lat, x.lon);
  fitSquare();
  hideHint();
}
$('qBtn').onclick = doSearch;
$('q').addEventListener('keydown', (e) => {
  const items = [...$('results').querySelectorAll('.row[data-i]')];
  if (e.key === 'Enter') { e.preventDefault(); if (resSel >= 0) pickResult(resSel); else doSearch(); }
  else if (e.key === 'Escape') $('results').classList.add('hidden');
  else if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && items.length) {
    e.preventDefault();
    resSel = (resSel + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
    items.forEach((el, k) => el.classList.toggle('sel', k === resSel));
  }
});
document.addEventListener('click', (e) => { if (!e.target.closest('.search')) $('results').classList.add('hidden'); });

/* ------------------------------------------------------------ my maps */

async function loadMaps() {
  const r = await call('maps');
  if (!r.ok) return;
  S.maps = r.maps || [];
  renderMaps();
}
function renderMaps() {
  $('mapCount').textContent = S.maps.length || '';
  $('mapsEmpty').classList.toggle('hidden', S.maps.length > 0);
  $('mapsList').innerHTML = S.maps.map((m) => {
    const badge = m.stale ? '<span class="badge stale">Rebuild recommended</span>'
      : m.installed ? '<span class="badge">Installed</span>' : '<span class="badge off">Not installed</span>';
    const st = m.stats || {};
    return `<div class="mrow" data-slug="${esc(m.slug)}">
      <img src="${m.thumb || ''}" alt="">
      <div class="mi"><h4 title="${esc(m.name)}">${esc(m.name)}</h4>
        <div class="sub">${esc(m.biome_label)} · ${m.size_km.toFixed(1)} km · vertical ${fmtElev((st.vert_ft || 0) / FT)}</div>
        ${badge}
        <div class="mact">
          <button class="link" data-a="show">Show</button>
          ${m.installed ? '<button class="link" data-a="uninstall">Uninstall</button>' : '<button class="link" data-a="install">Install</button>'}
          <button class="link" data-a="rebuild">Rebuild</button>
          <button class="link danger" data-a="delete">Delete</button>
        </div></div></div>`;
  }).join('');
  $('mapsList').querySelectorAll('.mact .link').forEach((b) => {
    b.onclick = () => mapAction(b.closest('.mrow').dataset.slug, b.dataset.a);
  });
  updateButtons();
}
async function mapAction(slug, a) {
  const m = S.maps.find((x) => x.slug === slug);
  if (!m || busy()) return;
  if (a === 'show') {
    S.sq.km = m.size_km; $('size').value = m.size_km;
    moveSquare(m.lat, m.lon); fitSquare();
    return;
  }
  if (a === 'delete' && !await confirmBox('Delete ' + m.name + '?',
    'The map is removed from Ski-E-O and from this app. Resorts you started on it will not load any more.', 'Delete')) return;
  if (a === 'uninstall' && !await confirmBox('Uninstall ' + m.name + '?',
    'Resorts you started on this map will not load until you install it again. The map stays in this app.', 'Uninstall')) return;
  if (a === 'rebuild' && !await confirmBox('Rebuild ' + m.name + '?',
    'Builds the map again for the current game version (same terrain and trees, so your resorts keep working) and installs it.', 'Rebuild', false)) return;
  const fn = { install: 'install_map', uninstall: 'uninstall_map', rebuild: 'rebuild_map', delete: 'delete_map' }[a];
  const r = await call(fn, slug);
  if (!r.ok) return errorBox(r.error);
  startJob(r);
}
/* ------------------------------------------------------------ tabs, footer */

document.querySelectorAll('.tag-tab').forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll('.tag-tab').forEach((x) => x.classList.toggle('active', x === t));
    $('tab-create').classList.toggle('hidden', t.dataset.tab !== 'create');
    $('tab-maps').classList.toggle('hidden', t.dataset.tab !== 'maps');
    if (t.dataset.tab === 'maps') loadMaps();
  };
});
document.querySelectorAll('[data-layer]').forEach((b) => {
  b.onclick = () => { setLayer(b.dataset.layer); call('set_setting', 'layer', S.layer); };
});
document.querySelectorAll('[data-link]').forEach((b) => { b.onclick = () => call('link', b.dataset.link); });
function renderUnits() {
  document.querySelectorAll('[data-units]').forEach((b) => b.classList.toggle('on', b.dataset.units === S.units));
  drawSquare(); renderSettings();
  if (S.preview) showStatsOnly();
  renderMaps();
}
function showStatsOnly() {
  const p = S.preview;
  $('stats').innerHTML = [['Peak', fmtElev(p.peak_m)], ['Base', fmtElev(p.base_m)], ['Vertical', fmtElev(p.relief_m)],
    ['Avg slope', Math.round(p.mean_slope) + '°']].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
}
document.querySelectorAll('[data-units]').forEach((b) => {
  b.onclick = () => { S.units = b.dataset.units; call('set_setting', 'units', S.units); renderUnits(); };
});
$('gamePill').onclick = () => {
  const g = S.game;
  if (!g || !g.found) return locateGame();
  toast(g.running ? 'Ski-E-O is running. New maps install as soon as you close it.' : `Game folder: ${g.folder}`);
};
$('repairBtn').onclick = async () => {
  if (!await confirmBox('Repair the map list?', 'Rebuilds the game\'s map list from the maps that are installed. Use it if a map is missing from the game\'s list.', 'Repair', false)) return;
  const r = await call('repair');
  if (!r.ok) return errorBox(r.error);
  startJob(r);
};
async function copyDiagnostics() {
  const r = await call('diagnostics');
  if (!r.ok) return toast(r.error, true);
  try { await navigator.clipboard.writeText(r.text); }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = r.text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  toast('Diagnostics copied. Paste them where you ask for help.');
}
$('diagBtn').onclick = copyDiagnostics;

/* ------------------------------------------------------------ start */

async function start() {
  const r = await call('init');
  if (!r.ok) return errorBox(r.error);
  $('ver').textContent = 'v' + r.version;
  S.biomes = r.biomes;
  S.limits = r.limits;
  fillBiomes(r.biomes);
  const st = r.settings || {};
  $('slope').min = S.limits.min_slope; $('slope').max = S.limits.max_slope; $('slope').value = S.limits.default_slope;
  $('trees').min = S.limits.min_trees; $('trees').max = S.limits.max_trees;
  $('trees').value = isFinite(st.max_trees) && st.max_trees > 0 ? st.max_trees : S.limits.default_trees;
  S.units = st.units === 'm' ? 'm' : 'ft';
  layers.topo = tileLayer('topo', 17);
  layers.osm = tileLayer('osm', 19);
  layers.sat = tileLayer('sat', 18);
  setLayer(st.layer || 'topo');
  const v = st.view;
  if (v && isFinite(v.lat) && isFinite(v.lon)) {
    S.sq = { lat: v.lat, lon: v.lon, km: Math.max(S.limits.min_km, Math.min(S.limits.max_km, v.km || 8)) };
    map.setView([v.lat, v.lon], v.zoom || 12);
  } else {
    map.setView([S.sq.lat, S.sq.lon], 11);
  }
  $('size').min = S.limits.min_km; $('size').max = S.limits.max_km; $('size').value = S.sq.km;
  drawSquare();
  renderUnits();
  renderGame(r.game);
  S.maps = r.maps || [];
  renderMaps();
  if (r.game.found && !r.game.ready) prepareGame();
  setInterval(refreshGame, 5000);
}

if (window.pywebview && window.pywebview.api) start();
else window.addEventListener('pywebviewready', start, { once: true });
