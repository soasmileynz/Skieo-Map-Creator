"""Drive the real window through a scripted session and save a screenshot after every step (dev tool).

usage: python tests/ui_drive.py <out dir> <scenario>
  scenarios: search  -- search, pick, load terrain, fill in the resort, press Create (cancelled while it waits for the
                         game to close if Ski-E-O runs), open My maps
             3d      -- load terrain, open the 3D view, topo texture, height, close
             3d-offline -- the same with the map tile servers unreachable, closing while it loads
             numbox  -- type exact values into the slider value boxes, show the "Map installed" box
  Set LOCALAPPDATA to a scratch folder to keep the test away from a creator that is running for real.
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402

from skieo_creator import APP_NAME, api, paths, server  # noqa: E402
from skieo_creator.main import release_webview_profile, style_titlebar  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else 'ui_shots')
SCEN = sys.argv[2] if len(sys.argv) > 2 else 'search'
OUT.mkdir(parents=True, exist_ok=True)
TITLE = APP_NAME + ' - UI test'          # not the real window's title, so shot() never grabs a running creator
user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDPIAware()


def shot(name):
    hwnd = user32.FindWindowW(None, TITLE)
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 3)

    class BMI(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
                    ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long), ('biYPelsPerMeter', ctypes.c_long),
                    ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]
    bi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB').save(OUT / (name + '.png'))
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    print('shot', name, flush=True)


def wait_js(win, expr, timeout=120, poll=0.4):
    t = time.time()
    while time.time() - t < timeout:
        try:
            if win.evaluate_js(expr):
                return True
        except Exception:
            pass
        time.sleep(poll)
    print('TIMEOUT waiting for', expr, flush=True)
    return False


def js(win, code):
    return win.evaluate_js(code)


def drive(win):
    wait_js(win, "document.getElementById('ver').textContent.length > 0", 60)
    js(win, "window.__errs = []; window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });")
    time.sleep(3)
    shot('01_start')
    if SCEN == 'search':
        js(win, "document.getElementById('q').value = 'Mount Hood Meadows'; doSearch();")
        wait_js(win, "document.querySelectorAll('.row[data-i]').length > 0", 30)
        time.sleep(0.5)
        shot('02_results')
        js(win, "pickResult(0)")
        time.sleep(4)
        shot('03_picked')
        js(win, "document.getElementById('loadBtn').click()")
        time.sleep(1.5)
        shot('04_loading')
        wait_js(win, "!!S.preview", 180)
        time.sleep(3)
        shot('05_preview')
        js(win, "document.getElementById('panel').querySelector('#tab-create').scrollTop = 400")
        js(win, "document.getElementById('name').value = 'Hood UI Test'; S.touched.name = true; updateButtons();")
        time.sleep(0.6)
        shot('06_settings')
        print('state', js(win, "JSON.stringify({biome: document.getElementById('biome').value, tl: document.getElementById('treeline').value, snow: document.getElementById('snow').value, create: !document.getElementById('createBtn').disabled, elev: document.getElementById('baseElev').textContent})"), flush=True)
        js(win, "document.getElementById('createBtn').click()")
        wait_js(win, "S.job && S.job.kind === 'create' && (S.job.state === 'waiting' || S.job.state === 'done' || S.job.state === 'error')", 300)
        time.sleep(1)
        js(win, "document.getElementById('panel').querySelector('#tab-create').scrollTop = 99999")
        time.sleep(0.6)
        shot('07_create')
        print('job', js(win, "JSON.stringify(S.job)")[:400], flush=True)
        if js(win, "S.job.state === 'waiting'"):
            js(win, "document.getElementById('cancelBtn').click()")
            wait_js(win, "S.job.state === 'cancelled'", 30)
            time.sleep(1)
            shot('08_cancelled')
        js(win, "document.querySelector('[data-tab=maps]').click()")
        time.sleep(1.5)
        shot('09_maps')
    if SCEN == '3d':
        js(win, "document.getElementById('q').value = 'Killington Peak'; doSearch();")
        wait_js(win, "document.querySelectorAll('.row[data-i]').length > 0", 30)
        js(win, "pickResult(0)")
        time.sleep(2)
        js(win, "document.getElementById('loadBtn').click()")
        wait_js(win, "!!S.preview", 240)
        time.sleep(2)
        shot('31_preview')
        js(win, "document.getElementById('photo').click()")
        wait_js(win, "document.getElementById('v3dLoading').classList.contains('hidden') && !document.getElementById('v3d').classList.contains('hidden')", 120)
        time.sleep(3)
        shot('32_3d')
        js(win, "document.querySelector('[data-tex=topo]').click()")
        wait_js(win, "document.querySelector('[data-tex=topo]').classList.contains('on')", 120)
        time.sleep(3)
        shot('33_topo')
        js(win, "var e = document.getElementById('v3dExag'); e.value = 2.5; e.oninput();")
        time.sleep(2)
        shot('34_exag')
        print('errors', js(win, "JSON.stringify(window.__errs || [])"), flush=True)
        js(win, "document.getElementById('v3dClose').click()")
        time.sleep(1)
        shot('35_closed')
    if SCEN == '3d-offline':
        # map tile servers unreachable (see main): the 3D view must still open, and Close / Esc must work while loading
        js(win, "document.getElementById('q').value = 'Killington Peak'; doSearch();")
        wait_js(win, "document.querySelectorAll('.row[data-i]').length > 0", 30)
        js(win, "pickResult(0)")
        time.sleep(2)
        js(win, "document.getElementById('loadBtn').click()")
        wait_js(win, "!!S.preview", 240)
        for z in (13, 14, 15, 14):                                      # plenty of tile requests in flight
            js(win, "map.setZoom(%d)" % z)
            time.sleep(0.3)
        js(win, "document.getElementById('photo').click()")
        time.sleep(0.3)
        shot('41_loading')
        js(win, "document.getElementById('v3dClose').click()")
        time.sleep(0.5)
        print('closed while loading', js(win, "document.getElementById('v3d').classList.contains('hidden')"), flush=True)
        js(win, "document.getElementById('photo').click()")
        time.sleep(0.3)
        js(win, "window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        time.sleep(0.5)
        print('esc while loading', js(win, "document.getElementById('v3d').classList.contains('hidden')"), flush=True)
        t = time.time()
        js(win, "document.getElementById('photo').click()")
        ok = wait_js(win, "document.getElementById('v3dLoading').classList.contains('hidden') && !document.getElementById('v3d').classList.contains('hidden')", 60, 0.1)
        print('opened offline', ok, round(time.time() - t, 1), 's', flush=True)
        time.sleep(2)
        shot('42_3d')
        js(win, "document.querySelector('[data-tex=topo]').click()")
        wait_js(win, "document.getElementById('v3dLoading').classList.contains('note')", 60)
        print('topo note', js(win, "document.getElementById('v3dLoadingText').textContent"), flush=True)
        shot('43_topo_offline')
        print('errors', js(win, "JSON.stringify(window.__errs || [])"), flush=True)
        js(win, "window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        time.sleep(0.5)
        print('esc closed', js(win, "document.getElementById('v3d').classList.contains('hidden')"), flush=True)
    if SCEN == 'numbox':
        js(win, "document.getElementById('q').value = 'Killington Peak'; doSearch();")
        wait_js(win, "document.querySelectorAll('.row[data-i]').length > 0", 30)
        js(win, "pickResult(0)")
        time.sleep(2)
        js(win, "document.getElementById('loadBtn').click()")
        wait_js(win, "!!S.preview", 240)
        time.sleep(1.5)
        js(win, "document.getElementById('panel').querySelector('#tab-create').scrollTop = 99999")
        time.sleep(0.6)
        shot('51_boxes')

        def typ(box, text, key='Enter'):
            js(win, "var b = document.getElementById('%s'); b.focus(); b.value = %s;" % (box, json.dumps(text)))
            if key == 'Enter':
                js(win, "document.getElementById('%s').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))" % box)
            else:
                js(win, "document.getElementById('%s').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))" % box)
            time.sleep(0.4)
            return js(win, "JSON.stringify({box: document.getElementById('%s').value, unit: document.getElementById('%s').nextElementSibling.textContent, "
                           "treeline: +document.getElementById('treeline').value, snow: +document.getElementById('snow').value, "
                           "valley: +document.getElementById('valley').value, km: S.sq.km})" % (box, box))
        print('treeline 11,300 ft ->', typ('tlOut', '11,300'), flush=True)
        print('treeline 99999 (clamped) ->', typ('tlOut', '99999'), flush=True)
        print('treeline none ->', typ('tlOut', 'none'), flush=True)
        print('treeline 8000 ft ->', typ('tlOut', '8000 ft'), flush=True)
        print('treeline abc (ignored) ->', typ('tlOut', 'abc'), flush=True)
        print('treeline 5000 + Esc (undo) ->', typ('tlOut', '5000', 'Escape'), flush=True)
        print('snow 0.93 ->', typ('snowOut', '0.93'), flush=True)
        print('snow 9 (clamped) ->', typ('snowOut', '9'), flush=True)
        print('snow 0,8 (decimal comma) ->', typ('snowOut', '0,8'), flush=True)
        print('treeline 3.500 (European thousands) ->', typ('tlOut', '3.500'), flush=True)
        js(win, "var n = document.getElementById('name'); n.focus(); n.value = 'Zürs am Arlberg!'; n.setSelectionRange(16, 16); n.dispatchEvent(new Event('input'));")
        time.sleep(0.6)
        print('name typed "Zürs am Arlberg!" ->', js(win, "JSON.stringify([document.getElementById('name').value, !document.getElementById('nameHint').classList.contains('hidden')])"), flush=True)
        print('server rule for a name that skips the box:', js(win, "JSON.stringify(null)"), flush=True)
        r = win.evaluate_js("window.pywebview.api.name_ok('Résort!')")
        print('   name_ok("Résort!") ->', r, flush=True)
        js(win, "var n = document.getElementById('name'); n.value = 'Killington Peak'; n.dispatchEvent(new Event('input'));")
        time.sleep(0.4)
        print('slope default ->', js(win, "document.getElementById('slope').value + ' / ' + document.getElementById('slopeOut').value + document.getElementById('slopeUnit').textContent"), flush=True)
        for t in ('40', '70', '10', '33'):
            typ('slopeOut', t)
            print('slope %s ->' % t, js(win, "document.getElementById('slope').value + ' / ' + document.getElementById('slopeOut').value"), flush=True)
        print('trees 123,456 ->', typ('treesOut', '123,456'), js(win, "document.getElementById('trees').value"), flush=True)
        print('trees 5 (clamped) ->', typ('treesOut', '5'), js(win, "document.getElementById('trees').value"), flush=True)
        print('trees 250000 ->', typ('treesOut', '250000'), js(win, "document.getElementById('trees').value"), flush=True)
        js(win, "document.querySelector('#cardSettings details').open = true")
        print('valley 1,500 ->', typ('valOut', '1,500'), flush=True)
        print('valley off ->', typ('valOut', 'off'), flush=True)
        js(win, "document.getElementById('panel').querySelector('#tab-create').scrollTop = 0")
        print('size 7.3 ->', typ('sizeOut', '7.3'), flush=True)
        print('size 50 (clamped) ->', typ('sizeOut', '50'), flush=True)
        time.sleep(1)
        shot('52_typed')
        js(win, "document.querySelector('[data-units=m]').click()")
        time.sleep(0.5)
        print('metres: treeline', typ('tlOut', '2500'), flush=True)
        js(win, "document.querySelector('[data-units=ft]').click()")
        js(win, "var b = document.getElementById('tlOut'); b.focus();")
        time.sleep(0.4)
        js(win, "document.getElementById('panel').querySelector('#tab-create').scrollTop = 400")
        time.sleep(0.4)
        shot('53_focused')
        js(win, "document.getElementById('tlOut').blur()")
        js(win, "showDone({name: 'Killington Peak', slug: 'x', biome_label: 'Northeast', thumb: document.getElementById('diorama').src, stats: {trees: 512345}})")
        time.sleep(1.5)
        shot('54_done')
        js(win, "document.querySelector('[data-tab=maps]').click()")
        time.sleep(1.5)
        shot('55_maps')
        print('errors', js(win, "JSON.stringify(window.__errs || [])"), flush=True)
    time.sleep(0.5)
    win.destroy()


def main():
    import webview
    paths.setup_logging()
    release_webview_profile()
    if SCEN == '3d-offline':
        for spec in server.LAYERS.values():
            spec['url'] = 'https://tiles.unreachable.invalid/{z}/{x}/{y}.png'
    url, _ = server.start()
    bridge = api.Api()
    win = webview.create_window(TITLE, url, js_api=bridge, width=1440, height=900, min_size=(1100, 680),
                                background_color='#0b1118')
    bridge._attach(win)
    win.events.shown += style_titlebar
    webview.start(drive, (win,), gui='edgechromium', private_mode=False, storage_path=str(paths.WEBVIEW))


if __name__ == '__main__':
    main()
