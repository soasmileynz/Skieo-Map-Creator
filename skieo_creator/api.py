"""The window's JavaScript calls these methods (pywebview js_api). Long work runs as one background job at a time; the
page gets progress through window.app.onEvent(...) and can also poll job()."""
import base64
import datetime
import io
import json
import logging
import math
import os
import platform
import re
import secrets
import shutil
import threading
import time
import traceback
import webbrowser

import numpy as np
from PIL import Image

from . import APP_NAME, VERSION, biomes, builder, dem, gamedata, geo, install, net, paths, server, trees

log = logging.getLogger(__name__)
GAME_URL = 'steam://rungameid/5012760'
LINKS = {
    'osm': 'https://www.openstreetmap.org/copyright',
    'otm': 'https://opentopomap.org/about',
    'esri': 'https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9',
    'nominatim': 'https://nominatim.org/',
    'photon': 'https://photon.komoot.io/',
    'usgs': 'https://www.usgs.gov/3d-elevation-program',
    'terrain': 'https://registry.opendata.aws/terrain-tiles/',
    'leaflet': 'https://leafletjs.com/',
}
PHOTON = 'https://photon.komoot.io/api/?limit=12&q='
NOMINATIM = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=8&q='
MIN_KM, MAX_KM = 3.0, 12.0
PREFIX = 'mc_'
ORDER_BASE = 1000
SETTING_KEYS = ('units', 'layer', 'view', 'max_trees')
# resort names: plain letters / digits / space and a little punctuation (the page turns accents into plain letters)
NAME_CHARS = re.compile(r"[A-Za-z0-9 '.&()\-]+")
NAME_RULE = "Names can use letters A-Z, numbers, spaces and - ' . & ( )"


class UserError(Exception):
    """a problem the player can fix; shown as is"""


def _friendly(e):
    if isinstance(e, (UserError, install.InstallError, gamedata.GameDataError, dem.DemError, net.NetError,
                      builder.BuildError)):
        return str(e)
    if isinstance(e, MemoryError):
        return 'Your PC ran out of memory while building the map. Close other programs and try again.'
    if isinstance(e, OSError) and getattr(e, 'winerror', None) in (32, 33):
        return 'A game file is in use. Close Ski-E-O (and the Map Installer) and try again.'
    if isinstance(e, OSError) and e.errno == 28:
        return 'Your disk is full. Free up some space and try again.'
    return 'Something went wrong (%s). Details are in the log -- use "Copy diagnostics" to share it.' % type(e).__name__


def _data_url(img, fmt='JPEG', quality=85):
    b = io.BytesIO()
    img.save(b, fmt, quality=quality) if fmt == 'JPEG' else img.save(b, fmt, optimize=True)
    return 'data:image/%s;base64,%s' % (fmt.lower(), base64.b64encode(b.getvalue()).decode('ascii'))


class Job:
    def __init__(self, kind, api):
        self.id = secrets.token_hex(4)
        self.kind = kind
        self.api = api
        self.cancel = net.CancelToken()
        self.state = 'running'
        self.fraction = 0.0
        self.text = ''
        self.result = None
        self.error = None
        self._last_emit = 0.0

    def progress(self, fraction, text):
        self.fraction = max(self.fraction, min(1.0, float(fraction)))
        self.text = text
        now = time.monotonic()
        if now - self._last_emit > 0.12:
            self._last_emit = now
            self.api._emit('job', self.as_dict())

    def as_dict(self):
        return dict(id=self.id, kind=self.kind, state=self.state, fraction=round(self.fraction, 3), text=self.text,
                    result=self.result, error=self.error)


class Api:
    def __init__(self):
        self._window = None
        self._settings = paths.Settings()
        self._lock = threading.Lock()
        self._job = None
        self._game = None
        self._gd = None
        self._preview = None
        self._search_rate = net.RateLimit(1.1)

    # ------------------------------------------------------------ plumbing

    def _attach(self, window):
        self._window = window

    def _emit(self, kind, payload):
        w = self._window
        if w is None:
            return
        try:
            w.evaluate_js('window.app && window.app.onEvent(%s, %s)' % (json.dumps(kind), json.dumps(payload)))
        except Exception:
            pass

    def _run(self, kind, work):
        with self._lock:
            if self._job and self._job.state in ('running', 'waiting'):
                raise UserError('Please wait until the current task has finished.')
            job = self._job = Job(kind, self)

        def target():
            try:
                job.result = work(job)
                job.state = 'done'
            except net.Cancelled:
                job.state = 'cancelled'
                job.text = 'Cancelled'
            except Exception as e:
                log.error('%s failed:\n%s', kind, traceback.format_exc())
                job.state = 'error'
                job.error = _friendly(e)
            self._emit('job', job.as_dict())

        threading.Thread(target=target, daemon=True, name=kind).start()
        return job.as_dict()

    def _safe(fn):
        """API methods return {'ok': True, ...} or {'ok': False, 'error': message} instead of raising into JS"""
        def wrapper(self, *a, **kw):
            try:
                r = fn(self, *a, **kw)
                return dict(r or {}, ok=True) if isinstance(r, dict) or r is None else {'ok': True, 'value': r}
            except Exception as e:
                if not isinstance(e, UserError):
                    log.error('%s failed:\n%s', fn.__name__, traceback.format_exc())
                return {'ok': False, 'error': _friendly(e)}
        wrapper.__name__ = fn.__name__
        return wrapper

    # ------------------------------------------------------------ startup / game

    @staticmethod
    def _clean_incomplete():
        """folders of builds that never finished (app closed or crashed mid-build): no map.json"""
        if not paths.MAPS.is_dir():
            return
        for d in paths.MAPS.iterdir():
            if d.is_dir() and not (d / 'map.json').is_file():
                log.info('removing incomplete build %s', d.name)
                shutil.rmtree(d, ignore_errors=True)

    @_safe
    def name_ok(self, name):
        n = re.sub(r'\s+', ' ', str(name or '')).strip()
        if not n:
            return dict(valid=False, message='')
        if len(n) < 2:
            return dict(valid=False, message='The name needs at least 2 characters.')
        if not NAME_CHARS.fullmatch(n):
            return dict(valid=False, message=NAME_RULE)
        if n.lower() in self._taken_names():
            return dict(valid=False, message='You already have a map called "%s".' % n)
        return dict(valid=True, message='')

    @_safe
    def init(self):
        if not self._busy():
            self._clean_incomplete()
        return dict(version=VERSION, app=APP_NAME, game=self._game_status(), biomes=biomes.listing(),
                    settings={k: self._settings.get(k) for k in SETTING_KEYS}, maps=self._maps(), token=server.TOKEN,
                    limits=dict(min_km=MIN_KM, max_km=MAX_KM, min_trees=trees.MIN_TREES, max_trees=trees.MAX_TREES,
                                default_trees=trees.DEFAULT_TREES, min_slope=trees.MIN_TREE_SLOPE,
                                max_slope=trees.MAX_TREE_SLOPE, default_slope=trees.TREE_SLOPE))

    def _find_game(self):
        f = self._settings.get('game_folder')
        if f and install.is_valid_game(f):
            build = None
            for line in self._appmanifest(f):
                p = [x for x in line.strip().split('"') if x.strip()]
                if len(p) == 2 and p[0] == 'buildid':
                    build = p[1]
            return f, build
        return install.find_game()

    @staticmethod
    def _appmanifest(folder):
        acf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(folder))), 'appmanifest_%s.acf' % install.APP_ID)
        try:
            with open(acf, encoding='utf-8', errors='replace') as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def _game_status(self):
        folder, build = self._find_game()
        if not folder:
            self._game = self._gd = None
            return dict(found=False)
        if not self._game or str(self._game.folder) != str(folder):
            self._game = install.Game(folder, build)
            self._gd = None
        self._game.build = build
        if self._gd is None or self._gd.fp != gamedata.game_fingerprint(folder):
            self._gd = gamedata.GameData(folder)
        return dict(found=True, folder=str(folder), build=build, running=install.game_running(), ready=self._gd.ready())

    @_safe
    def game_status(self):
        return self._game_status()

    @_safe
    def pick_game_folder(self):
        r = self._window.create_file_dialog(20, directory='')            # 20 = FileDialog.FOLDER
        if not r:
            return dict(game=self._game_status(), changed=False)
        p = r[0] if isinstance(r, (list, tuple)) else r
        for cand in (p, os.path.dirname(p), os.path.dirname(os.path.dirname(p))):
            if install.is_valid_game(cand):
                self._settings.set(game_folder=str(cand))
                return dict(game=self._game_status(), changed=True)
        raise UserError('That folder doesn\'t look like the Ski-E-O game folder (the one that contains '
                        '"SkiResortSimulator").')

    def _require_game(self):
        st = self._game_status()
        if not st['found']:
            raise UserError('Ski-E-O was not found. Use "Locate game" and pick the game folder.')
        return st

    def _prepare(self, job, base=0.0, span=1.0):
        if not self._gd.ready():
            self._gd.prepare(lambda f, t: job.progress(base + span * f, t))

    @_safe
    def prepare_game(self):
        self._require_game()
        return self._run('prepare', lambda job: (self._prepare(job), self._game_status())[1])

    # ------------------------------------------------------------ map page helpers

    @_safe
    def search(self, query):
        q = (query or '').strip()
        if not q:
            return dict(results=[])
        m = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*', q)
        if m and abs(float(m.group(1))) <= 90 and abs(float(m.group(2))) <= 180:
            return dict(results=[dict(label='%.5f, %.5f' % (float(m.group(1)), float(m.group(2))),
                                      lat=float(m.group(1)), lon=float(m.group(2)), kind='coordinates')])
        try:
            out = self._photon(q)
        except (net.NetError, ValueError, KeyError) as e:
            log.warning('photon search failed: %s', e)
            out = []
        if not out:
            out = self._nominatim(q)
        return dict(results=out[:10])

    @staticmethod
    def _rank(key, value, name):
        n = name.lower()
        if value == 'winter_sports' or ('ski' in n and key in ('leisure', 'landuse', 'tourism', 'sport', 'amenity')):
            return 3, 'Ski area'
        if value in ('peak', 'volcano', 'mountain_range', 'ridge', 'massif', 'hill'):
            return 2, value.replace('_', ' ').capitalize()
        if key == 'place' or (key == 'boundary' and value == 'administrative'):
            return 1, (value if key == 'place' else 'place').replace('_', ' ').capitalize()
        if key in ('highway', 'building', 'shop', 'office') or value in ('residential', 'house'):
            return -2, value.replace('_', ' ').capitalize()
        return 0, value.replace('_', ' ').capitalize()

    @staticmethod
    def _fit_km(w, s, e, n):
        """square size that holds a ski area's outline with some room around it"""
        lat = (s + n) / 2
        dx = abs(e - w) * 111320 * math.cos(math.radians(lat))
        dy = abs(n - s) * 110574
        return float(np.clip(round(max(dx, dy) * 1.6 / 500.0) / 2.0, MIN_KM, MAX_KM))

    def _photon(self, q):
        from urllib.parse import quote
        self._search_rate.wait()
        data = json.loads(net.get(PHOTON + quote(q[:200]), timeout=20, retries=1, per_host=1,
                                  accept='application/json').decode('utf-8'))
        out, seen = [], set()
        for i, f in enumerate(data.get('features', [])):
            p = f.get('properties', {})
            lon, lat = f['geometry']['coordinates'][:2]
            name = p.get('name') or p.get('street') or ''
            if not name:
                continue
            where = ', '.join(x for x in (p.get('state') or p.get('county'), p.get('country')) if x)
            label = name + (', ' + where if where else '')
            if label in seen:
                continue
            seen.add(label)
            score, kind = self._rank(p.get('osm_key', ''), p.get('osm_value', ''), name)
            r = dict(label=label, lat=float(lat), lon=float(lon), kind=kind, name=name, _s=(-score, i))
            ext = p.get('extent')                                  # [west, north, east, south]
            if ext and len(ext) == 4:
                w, n_, e, s = (float(v) for v in ext)
                r.update(lat=(n_ + s) / 2, lon=(w + e) / 2)
                if score == 3:
                    r['size_km'] = self._fit_km(w, s, e, n_)
            out.append(r)
        out.sort(key=lambda r: r.pop('_s'))
        return out

    def _nominatim(self, q):
        from urllib.parse import quote
        self._search_rate.wait()
        data = net.get(NOMINATIM + quote(q[:200]), timeout=20, retries=1, per_host=1, accept='application/json')
        out = []
        for r in json.loads(data.decode('utf-8')):
            try:
                name = (r.get('name') or r.get('display_name', '').split(',')[0]).strip()
                score, kind = self._rank(r.get('category', ''), r.get('type', ''), name)
                out.append(dict(label=r.get('display_name', '')[:160], lat=float(r['lat']), lon=float(r['lon']),
                                kind=kind, name=name, _s=(-score, -float(r.get('importance') or 0))))
            except (KeyError, ValueError):
                continue
        out.sort(key=lambda r: r.pop('_s'))
        return out

    @_safe
    def defaults(self, lat, lon):
        return biomes.defaults(float(lat), float(lon))

    @_safe
    def set_setting(self, key, value):
        if key in SETTING_KEYS:
            self._settings.set(**{key: value})

    @_safe
    def link(self, name):
        if name in LINKS:
            webbrowser.open(LINKS[name])

    @_safe
    def play(self):
        os.startfile(GAME_URL)

    @_safe
    def diagnostics(self):
        st = {}
        try:
            st = self._game_status()
        except Exception as e:
            st = {'error': repr(e)}
        head = ['%s %s' % (APP_NAME, VERSION), 'Windows %s' % platform.version(), 'game: %s' % json.dumps(st),
                'app folder: %s' % paths.APP_DIR, '']
        return dict(text='\n'.join(head) + paths.log_tail(250))

    # ------------------------------------------------------------ preview

    @_safe
    def preview(self, lat, lon, size_km):
        lat, lon, size_km = float(lat), float(lon), float(size_km)
        if not (MIN_KM <= size_km <= MAX_KM):
            raise UserError('The area must be between %g and %g km wide.' % (MIN_KM, MAX_KM))
        if abs(lat) > 84:
            raise UserError('Elevation data does not cover the polar regions.')
        return self._run('preview', lambda job: self._do_preview(job, lat, lon, size_km))

    def _do_preview(self, job, lat, lon, size_km):
        size_m = size_km * 1000.0
        d, src = dem.load(lat, lon, size_m, job.cancel, lambda f, t: job.progress(0.75 * f, t))
        job.progress(0.78, 'Analysing the terrain...')
        H, fr = builder.heights(d, size_m)
        slope = builder.slope_deg(H, fr)
        job.cancel.check()
        row, col = builder.suggest_base(H, fr)
        g = geo.LocalGrid(lat, lon)
        blat, blon = g.to_latlon(col * fr.sp - fr.half, fr.half - row * fr.sp)
        job.progress(0.85, 'Rendering the preview...')
        dio = builder.render_preview(H, fr, W=960, Hh=540, step=10)
        job.cancel.check()
        shade = self._shaded_relief(d, fr)
        step = 16
        grid = np.round(d[::step, ::step]).astype(int).tolist()
        relief = float(d.max() - d.min())
        warnings = []
        if relief < 150:
            warnings.append('This area is very flat (%.0f m of relief). Pick an area with a hill or mountain.' % relief)
        if float((slope > 45).mean()) > 0.25:
            warnings.append('A lot of this area is cliffs steeper than 45 degrees. Skiers need gentler slopes.')
        self._preview = dict(lat=lat, lon=lon, size_km=size_km, dem=d, src=src, base=[float(blat), float(blon)])
        return dict(lat=lat, lon=lon, size_km=size_km, source=dem.SOURCES[src]['name'], source_id=src,
                    credit=dem.SOURCES[src]['credit'], peak_m=float(d.max()), base_m=float(d.min()),
                    relief_m=relief, mean_slope=float(slope.mean()), steep=float((slope > 35).mean()),
                    base=[float(blat), float(blon)], diorama=_data_url(dio, 'JPEG', 82), shade=_data_url(shade, 'PNG'),
                    grid=dict(step_m=fr.sp * step, n=len(grid), data=grid), warnings=warnings,
                    defaults=biomes.defaults(float(blat), float(blon)))

    @_safe
    def view3d(self, treeline_m=None):
        """heights + colour texture of the loaded terrain for the 3D view (served from the local server)"""
        pv = self._preview
        if not pv:
            raise UserError('Load the terrain first.')
        from . import view3d
        tl = float(treeline_m) if treeline_m is not None else 0.0
        heights, meta, albedo = view3d.build(pv['dem'], pv['size_km'] * 1000.0, tl)
        ver = secrets.token_hex(4)
        server.BLOBS['heights'] = (heights, 'application/octet-stream')
        server.BLOBS['albedo'] = (albedo, 'image/jpeg')
        g = geo.LocalGrid(pv['lat'], pv['lon'])
        half = pv['size_km'] * 500.0
        base = pv.get('base')
        bx = by = None
        if base:
            x, y = g.to_xy(base[0], base[1])
            bx, by = float(x), float(y)
        lat_n, lon_w = g.to_latlon(-half, half)
        lat_s, lon_e = g.to_latlon(half, -half)
        r, c = np.unravel_index(int(np.argmax(pv['dem'])), pv['dem'].shape)
        sp = pv['size_km'] * 1000.0 / (pv['dem'].shape[0] - 1)
        return dict(meta, version=ver, base=[bx, by] if bx is not None else None,
                    peak=[float(c * sp - half), float(half - r * sp)],
                    bbox=dict(n=float(lat_n), s=float(lat_s), w=float(lon_w), e=float(lon_e)))

    @staticmethod
    def _shaded_relief(d, fr, n=512):
        k = (d.shape[0] - 1) // (n - 1)
        z = d[::k, ::k][:n, :n].astype(np.float64)
        gy, gx = np.gradient(z, fr.sp * k)
        nz = 1 / np.sqrt(gx * gx + gy * gy + 1)
        L = np.array([-0.5, 0.5, 0.7])
        L /= np.linalg.norm(L)
        sh = np.clip((-gx * L[0] + gy * L[1] + L[2]) * nz, 0, 1)        # row 0 = north: +gy points south
        zn = (z - z.min()) / max(z.max() - z.min(), 1.0)
        lo, hi = np.array([70, 110, 80]), np.array([240, 240, 245])
        col = lo[None, None] * (1 - zn[..., None]) + hi[None, None] * zn[..., None]
        col = col * (0.45 + 0.75 * sh[..., None])
        rgba = np.dstack([np.clip(col, 0, 255), np.full(z.shape, 255.0)]).astype(np.uint8)
        return Image.fromarray(rgba, 'RGBA')

    # ------------------------------------------------------------ create

    @_safe
    def create(self, params):
        p = self._validate(params)
        return self._run('create', lambda job: self._do_create(job, p))

    def _taken_names(self, exclude_id=None):
        names = set()
        if self._game:
            names |= {m['name'].strip().lower() for i, m in self._game.installed().items() if i != exclude_id}
        for rec in self._records():
            if PREFIX + rec['slug'] != exclude_id:
                names.add(rec['name'].strip().lower())
        return names

    def _validate(self, params):
        st = self._require_game()
        pv = self._preview
        name = re.sub(r'\s+', ' ', str(params.get('name') or '')).strip()
        if not (2 <= len(name) <= 40) or any(ord(c) < 32 for c in name):
            raise UserError('Give the map a name of 2 to 40 characters.')
        if not NAME_CHARS.fullmatch(name):
            raise UserError(NAME_RULE + '.')
        if name.lower() in self._taken_names():
            raise UserError('A map called "%s" already exists. Pick another name.' % name)
        if not pv or abs(pv['lat'] - float(params['lat'])) > 1e-7 or abs(pv['lon'] - float(params['lon'])) > 1e-7 \
                or abs(pv['size_km'] - float(params['size_km'])) > 1e-9:
            raise UserError('The area changed since the terrain preview. Press "Load terrain" again first.')
        biome = params.get('biome')
        if biome not in biomes.BIOMES:
            raise UserError('Pick a biome.')
        size_m = pv['size_km'] * 1000.0
        g = geo.LocalGrid(pv['lat'], pv['lon'])
        bx, by = g.to_xy(float(params['base'][0]), float(params['base'][1]))
        sp, half = size_m / (builder.G - 1), size_m / 2
        col, row = (float(bx) + half) / sp, (half - float(by)) / sp
        margin = 40
        if not (margin <= row <= builder.G - 1 - margin and margin <= col <= builder.G - 1 - margin):
            raise UserError('Put the base area marker inside the square (not right at its edge).')
        treeline_m = float(params.get('treeline_m', biomes.default_treeline(biome, pv['lat'])))
        return dict(name=name, lat=pv['lat'], lon=pv['lon'], size_m=size_m, size_km=pv['size_km'], biome=biome,
                    treeline=float(np.clip(treeline_m, 0, 9000)), valley=float(np.clip(float(params.get('valley_m') or 0), 0, 9000)),
                    snow=float(np.clip(float(params.get('snow', 1.0)), 0.2, 3.0)),
                    max_trees=int(np.clip(int(float(params.get('max_trees') or trees.DEFAULT_TREES)), trees.MIN_TREES, trees.MAX_TREES)),
                    tree_slope=float(np.clip(float(params.get('tree_slope') or trees.TREE_SLOPE), trees.MIN_TREE_SLOPE, trees.MAX_TREE_SLOPE)),
                    acres=int(np.clip(int(params.get('acres') or 0) or self._acres(size_m), 10, 50000)),
                    base_rc=[row, col], base_latlon=[float(params['base'][0]), float(params['base'][1])],
                    source=pv['src'], seed=secrets.randbelow(2 ** 31 - 1) + 1, tg=secrets.randbelow(2 ** 31 - 2) + 1,
                    game=st)

    @staticmethod
    def _acres(size_m):
        return int(round(size_m * size_m / 4046.86 * 0.06 / 10.0) * 10)

    def _do_create(self, job, p):
        self._prepare(job, 0.0, 0.05)
        taken = {r['slug'] for r in self._records()} | {i[len(PREFIX):] for i in self._game.installed() if i.startswith(PREFIX)}
        slug = builder.new_slug(p['name'], taken)
        spec = dict((k, p[k]) for k in ('name', 'lat', 'lon', 'size_m', 'biome', 'treeline', 'valley', 'snow', 'max_trees',
                                        'tree_slope', 'acres', 'base_rc', 'base_latlon', 'seed', 'tg', 'source'))
        spec['slug'] = slug
        d = self._preview['dem'] if self._preview else None
        if d is None:
            d, _ = dem.load(p['lat'], p['lon'], p['size_m'], job.cancel, lambda f, t: job.progress(0.05 + 0.1 * f, t), prefer=p['source'])
        rec = self._build_and_package(job, spec, d, 0.05, 0.9, order=self._next_order())
        self._install_when_closed(job, rec, 0.9)
        return self._map_view(rec)

    def _next_order(self):
        used = [m.get('order', 0) for m in (self._game.installed().values() if self._game else [])]
        used += [r.get('order', 0) for r in self._records()]
        return max([ORDER_BASE] + used) + 10

    def _build_and_package(self, job, spec, d, a, b, order):
        slug = spec['slug']
        out = paths.MAPS / slug
        out.mkdir(parents=True, exist_ok=True)
        res = builder.build(spec, d, self._gd, out, job.cancel, lambda f, t: job.progress(a + (b - a) * f, t))
        res['preview'].resize((640, 360), Image.LANCZOS).save(out / 'preview.jpg', quality=85)
        pkg_name = re.sub(r'[^A-Za-z0-9 _-]+', '', spec['name']).strip().replace(' ', '_') or slug
        man = dict(id=PREFIX + slug, name=spec['name'], version=int(spec.get('version', 1)), order=order,
                   files=res['files'], entry=res['entry'], game_build=self._game.build, format=builder.ENTRY_FORMAT,
                   creator=dict(version=VERSION, lat=spec['lat'], lon=spec['lon'], size_m=spec['size_m'], biome=spec['biome']))
        pkg = out / (pkg_name + '.skimap')
        install.make_package(pkg, man, out)
        rec = dict(slug=slug, name=spec['name'], order=order, spec=spec, stats=res['stats'], package=pkg.name,
                   files=res['files'], game_fp=self._gd.fp, game_build=self._game.build, creator=VERSION, recipe=builder.RECIPE,
                   created=datetime.datetime.now().isoformat(timespec='seconds'))
        paths.atomic_write(out / 'map.json', json.dumps(rec, indent=1).encode('utf-8'))
        return rec

    def _install_when_closed(self, job, rec, a):
        pkg = paths.MAPS / rec['slug'] / rec['package']
        if install.game_running():
            job.state = 'waiting'
            job.progress(a, 'Map built. Close Ski-E-O to finish installing it...')
            while install.game_running():
                job.cancel.sleep(2.0)
            job.state = 'running'
            job.cancel.sleep(3.0)                                  # let the game release its files
        job.progress(a + 0.02, 'Installing into Ski-E-O...')
        self._game.install(str(pkg), lambda t: job.progress(a + 0.05, t))
        job.progress(1.0, 'Installed')

    # ------------------------------------------------------------ my maps

    @staticmethod
    def _records():
        out = []
        if not paths.MAPS.is_dir():
            return out
        for d in paths.MAPS.iterdir():
            try:
                rec = json.loads((d / 'map.json').read_text(encoding='utf-8'))
                if (d / rec['package']).is_file():
                    out.append(rec)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda r: r.get('created', ''), reverse=True)

    def _record(self, slug):
        if not re.fullmatch(r'[a-z0-9_]{14}', str(slug)):
            raise UserError('Unknown map.')
        try:
            return json.loads((paths.MAPS / slug / 'map.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise UserError('That map\'s files are missing.')

    def _map_view(self, rec):
        inst = self._game.installed() if self._game else {}
        m = inst.get(PREFIX + rec['slug'])
        installed = bool(m) and all((self._game.mods / f).is_file() for f in m['files'])
        thumb = paths.MAPS / rec['slug'] / 'preview.jpg'
        stale = bool(self._gd and rec.get('game_fp') and rec['game_fp'] != self._gd.fp) \
            or rec.get('recipe', 1) < builder.RECIPE                # made by an older creator: rebuild for its fixes
        s = rec['spec']
        return dict(slug=rec['slug'], name=rec['name'], created=rec.get('created'), installed=installed, stale=stale,
                    lat=s['lat'], lon=s['lon'], size_km=s['size_m'] / 1000.0, biome=s['biome'],
                    biome_label=biomes.BIOMES.get(s['biome'], {}).get('label', s['biome']), stats=rec.get('stats', {}),
                    thumb=_data_url(Image.open(thumb), 'JPEG', 80) if thumb.is_file() else None)

    def _maps(self):
        return [self._map_view(r) for r in self._records()]

    @_safe
    def maps(self):
        self._game_status()
        return dict(maps=self._maps())

    @_safe
    def install_map(self, slug):
        rec = self._record(slug)
        self._require_game()

        def work(job):
            self._install_when_closed(job, rec, 0.0)
            return self._map_view(rec)
        return self._run('install', work)

    @_safe
    def uninstall_map(self, slug):
        rec = self._record(slug)
        self._require_game()
        if install.game_running():
            raise UserError('Close Ski-E-O first, then try again.')

        def work(job):
            job.progress(0.2, 'Removing %s from the game...' % rec['name'])
            self._game.uninstall(PREFIX + slug, lambda t: job.progress(0.5, t))
            return self._map_view(rec)
        return self._run('uninstall', work)

    @_safe
    def delete_map(self, slug):
        rec = self._record(slug)
        self._require_game()
        if install.game_running():
            raise UserError('Close Ski-E-O first, then try again.')

        def work(job):
            if PREFIX + slug in self._game.installed():
                job.progress(0.3, 'Removing %s from the game...' % rec['name'])
                self._game.uninstall(PREFIX + slug, lambda t: job.progress(0.6, t))
            shutil.rmtree(paths.MAPS / slug, ignore_errors=True)
            return dict(slug=slug, deleted=True)
        return self._run('delete', work)

    @_safe
    def rebuild_map(self, slug):
        rec = self._record(slug)
        self._require_game()

        def work(job):
            self._prepare(job, 0.0, 0.05)
            s = dict(rec['spec'], version=int(rec.get('version', 1)) + 1)
            d, _ = dem.load(s['lat'], s['lon'], s['size_m'], job.cancel, lambda f, t: job.progress(0.05 + 0.15 * f, t),
                            prefer=s['source'])
            new = self._build_and_package(job, s, d, 0.2, 0.9, order=rec.get('order', self._next_order()))
            new['version'] = s['version']
            paths.atomic_write(paths.MAPS / slug / 'map.json', json.dumps(new, indent=1).encode('utf-8'))
            self._install_when_closed(job, new, 0.9)
            return self._map_view(new)
        return self._run('rebuild', work)

    @_safe
    def export_map(self, slug):
        rec = self._record(slug)
        src = paths.MAPS / slug / rec['package']
        docs = os.path.join(os.path.expanduser('~'), 'Documents')
        r = self._window.create_file_dialog(30, directory=docs if os.path.isdir(docs) else '',     # 30 = FileDialog.SAVE
                                            save_filename=rec['package'], file_types=('Ski-E-O map (*.skimap)',))
        if not r:
            return dict(saved=False)
        dst = r[0] if isinstance(r, (list, tuple)) else r
        if not dst.lower().endswith('.skimap'):
            dst += '.skimap'
        shutil.copyfile(src, dst)
        return dict(saved=True, path=dst, size_mb=round(os.path.getsize(dst) / 1e6, 1))

    @_safe
    def repair(self):
        self._require_game()
        if install.game_running():
            raise UserError('Close Ski-E-O first, then try again.')

        def work(job):
            job.progress(0.3, 'Rebuilding the game\'s map list...')
            self._game.rebuild_registry()
            return {}
        return self._run('repair', work)

    # ------------------------------------------------------------ jobs

    @_safe
    def job(self):
        return self._job.as_dict() if self._job else {}

    @_safe
    def cancel(self):
        if self._job and self._job.state in ('running', 'waiting'):
            self._job.cancel.cancel()

    def _busy(self):
        return bool(self._job and self._job.state in ('running', 'waiting'))
