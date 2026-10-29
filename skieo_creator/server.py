"""Local web servers for the window: one for the UI files and the app's data (/blob/...), one for map tiles.

Map tiles are fetched by Python, not by the page, so the tile servers' usage policies can be followed: an identifying
User-Agent, at most two connections per server, and a 14-day disk cache. Tiles get their own port because the browser
opens at most 6 connections per host:port: slow or failing tile downloads (offline, a slow tile server) must never
queue the app's own requests behind them. Both listen on 127.0.0.1 only; tiles and blobs need the per-run random token
(no other program or web page can use them as a proxy).
"""
import logging
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import net, paths

log = logging.getLogger(__name__)
LAYERS = {
    'osm': dict(url='https://tile.openstreetmap.org/{z}/{x}/{y}.png', max_zoom=19),
    'topo': dict(url='https://tile.opentopomap.org/{z}/{x}/{y}.png', max_zoom=17),
    'sat': dict(url='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                max_zoom=18, ext='jpg'),
}
TILE_TYPES = {'png': (b'\x89PNG\r\n\x1a\n', 'image/png'), 'jpg': (b'\xff\xd8\xff', 'image/jpeg')}   # ext -> (magic, type)
TILE_DAYS = 14
TILE_CACHE_MB = 400
TOKEN = secrets.token_urlsafe(24)
BLOBS = {}                    # name -> (bytes, content type): data the app hands the page (3D view heights / texture)
# fixed types: Windows' registry can map .js to text/plain, and the browser refuses module scripts served like that
TYPES = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json',
         '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ttf': 'font/ttf', '.txt': 'text/plain'}
OFFLINE_S = 15.0              # after a server could not be reached, its tiles fail at once for this long
_offline = {}                 # layer -> time.monotonic() when it was found unreachable
_fails = {}                   # layer -> failed downloads in a row


def _is_offline(layer):
    return time.monotonic() - _offline.get(layer, -1e9) < OFFLINE_S


class _OfflineGate(net.CancelToken):
    """lets tile downloads still waiting for a connection give up at once when another one found the server unreachable"""

    def __init__(self, layer):
        super().__init__()
        self.layer = layer

    def check(self):
        if _is_offline(self.layer):
            raise net.Cancelled()

    def sleep(self, seconds):
        time.sleep(seconds)
        self.check()


EMPTY_PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                          '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')


def tile(layer, z, x, y):
    spec = LAYERS[layer]
    if not (0 <= z <= spec['max_zoom'] and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return None
    ext = spec.get('ext', 'png')
    f = paths.TILES / layer / str(z) / str(x) / ('%d.%s' % (y, ext))
    if f.is_file() and time.time() - f.stat().st_mtime < TILE_DAYS * 86400:
        return f.read_bytes()
    stale = f.read_bytes() if f.is_file() else None      # stale beats nothing when offline
    try:
        _OfflineGate(layer).check()
        data = net.get(spec['url'].format(z=z, x=x, y=y), timeout=20, retries=1, per_host=2, accept=TILE_TYPES[ext][1],
                       cancel=_OfflineGate(layer))
    except net.Cancelled:
        return stale
    except net.NetError as e:
        if 'the server answered' in str(e):              # it is up, just not for this tile
            log.warning('tile %s/%d/%d/%d: %s', layer, z, x, y, e)
        else:
            _fails[layer] = _fails.get(layer, 0) + 1
            if 'getaddrinfo' in str(e) or _fails[layer] >= 3:   # no internet (DNS), or the server is down
                if not _is_offline(layer):
                    log.warning('tiles %s: server not reachable, trying again in %.0f s (%s)', layer, OFFLINE_S, e)
                _offline[layer] = time.monotonic()
        return stale
    _fails[layer] = 0
    if data.startswith(TILE_TYPES[ext][0]):
        paths.atomic_write(f, data)
    return data


def prune_tiles():
    files = []
    total = 0
    for f in paths.TILES.rglob('*'):
        if f.suffix[1:] not in TILE_TYPES:
            continue
        st = f.stat()
        files.append((st.st_mtime, st.st_size, f))
        total += st.st_size
    files.sort()
    while files and total > TILE_CACHE_MB * 1 << 20:
        _, size, f = files.pop(0)
        try:
            f.unlink()
            total -= size
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'SkiEOCreator'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b'', ctype='text/plain', cache=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        if self.server.cors:                      # the 3D view draws tiles into a canvas (needs CORS from the UI origin)
            self.send_header('Access-Control-Allow-Origin', self.server.cors)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if cache:
            self.send_header('Cache-Control', cache)
        try:
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass                                  # the map dropped a tile it no longer needs (zoom / pan)

    def do_GET(self):
        host = (self.headers.get('Host') or '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):                   # DNS rebinding guard
            return self._send(403, b'forbidden')
        u = urlsplit(self.path)
        parts = u.path.strip('/').split('/')
        if self.server.role == 'tiles' and parts[0] != 'tile':
            return self._send(404, b'not found')
        if parts[0] == 'blob':
            blob = BLOBS.get(parts[1]) if len(parts) == 2 else None
            if parse_qs(u.query).get('t', [''])[0] != TOKEN or blob is None:
                return self._send(404, b'not found')
            return self._send(200, blob[0], blob[1], 'no-store')
        if parts[0] == 'tile':
            if parse_qs(u.query).get('t', [''])[0] != TOKEN or len(parts) != 5 or parts[1] not in LAYERS:
                return self._send(403, b'forbidden')
            try:
                z, x, y = int(parts[2]), int(parts[3]), int(parts[4].split('.')[0])
            except ValueError:
                return self._send(400, b'bad tile')
            data = tile(parts[1], z, x, y)
            if data is None:
                return self._send(200, EMPTY_PNG, 'image/png', 'no-store')
            return self._send(200, data, TILE_TYPES[LAYERS[parts[1]].get('ext', 'png')][1], 'max-age=86400')
        rel = '/'.join(p for p in parts if p not in ('', '.', '..')) or 'index.html'
        f = paths.resource('web', *rel.split('/'))
        web = paths.resource('web').resolve()
        try:
            f = f.resolve()
            f.relative_to(web)
        except (OSError, ValueError):
            return self._send(404, b'not found')
        if not f.is_file():
            return self._send(404, b'not found')
        ctype = TYPES.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
        return self._send(200, f.read_bytes(), ctype, 'no-cache')

    do_HEAD = do_GET


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, role, cors=None):
        super().__init__(('127.0.0.1', 0), Handler)
        self.role, self.cors = role, cors

    def handle_error(self, request, client_address):
        log.debug('%s server request error', self.role, exc_info=True)


def start():
    ui = Server('ui')
    port = ui.server_address[1]
    tiles = Server('tiles', cors='http://127.0.0.1:%d' % port)
    tport = tiles.server_address[1]
    for srv, name in ((ui, 'http-ui'), (tiles, 'http-tiles')):
        threading.Thread(target=srv.serve_forever, daemon=True, name=name).start()
    threading.Thread(target=prune_tiles, daemon=True, name='prune').start()
    log.info('ui server on 127.0.0.1:%d, tiles on :%d', port, tport)
    return 'http://127.0.0.1:%d/index.html?t=%s&tp=%d' % (port, TOKEN, tport), port
