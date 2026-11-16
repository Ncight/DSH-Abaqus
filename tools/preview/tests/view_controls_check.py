# -*- coding: utf-8 -*-
"""Browser test: turning the model with the mouse, and Z up by default (headless Edge through Playwright).

    py view_controls_check.py [--server-dir DIR] [--port 8778] [--wait-minutes 30]

User, 2026-11-15: with OrbitControls the model could only be turned about the vertical axis (a vertical drag stops at
the pole, there is no turn about the line of sight), and the standard views had Y up while most models have Z up.
static/ViewControls.js is a virtual trackball and the vertical axis is Z by default.  A temp workspace with a 3-D CDB
(block_comps), a 3-D Abaqus input (demo_beam) and a plane-stress Abaqus input (plate_hole):

* 3-D models open with Z up, the menu says Z and can switch a file to Y (that file keeps it, also after a reload;
  other files keep Z);
* a vertical drag turns the model over the top (more than 150 degrees: impossible with OrbitControls), the up vector
  turns with it; a horizontal drag turns it about the screen's vertical axis;
* a drag that starts near the edge of the view turns it about the line of sight (the camera stays where it is);
* right drag pans (the line of sight stays parallel), the wheel zooms (wheel up: closer);
* 适应 (fit) keeps the orientation the model was turned to, Iso puts Z up again;
* the plane model lies in X-Y and keeps Y up; the menu is disabled while it is shown;
* no script errors.
Screenshots: tests/shots/view_zup_iso.png, view_turned.png.  Exit code 1 when a check fails.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_gate  # noqa: E402

SHOTS = os.path.join(HERE, 'shots')
EX = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'examples'))
MIN_FREE_GB = 4.0
FAILS = []

CAM = '''() => { const c = window.__preview.camera, t = window.__preview.controls.target;
  return { pos: c.position.toArray(), up: c.up.toArray(), tgt: t.toArray(), sel: document.getElementById('upSel').value,
           dis: document.getElementById('upSel').disabled }; }'''


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    sys.stdout.flush()
    if not cond:
        FAILS.append(what)


def sub(a, b):
    return [x - y for x, y in zip(a, b)]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def norm(a):
    n = math.sqrt(dot(a, a))
    return [x / n for x in a]


def ang(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(norm(a), norm(b))))))


def eye(s):
    return sub(s['pos'], s['tgt'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8778)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    ws = tempfile.mkdtemp(prefix='abq_preview_view_controls_')
    shutil.copy2(os.path.join(HERE, 'data', 'block_comps.cdb'), os.path.join(ws, 'block_comps.cdb'))
    shutil.copy2(os.path.join(EX, 'demo_beam', 'demo_beam.inp'), os.path.join(ws, 'demo_beam.inp'))
    shutil.copy2(os.path.join(EX, 'corpus', 'cae', 'plate_hole_cps4r_cps3.inp'), os.path.join(ws, 'plate_hole.inp'))
    t = time.time()
    for i, f in enumerate(('plate_hole.inp', 'demo_beam.inp', 'block_comps.cdb')):      # the CDB is the newest
        os.utime(os.path.join(ws, f), (t - 100 + i, t - 100 + i))
    srv = None
    try:
        with heavy_gate.slot('headless browser (view_controls_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
            srv = subprocess.Popen([sys.executable, os.path.join(a.server_dir, 'preview_server.py'), '--root', ws,
                                    '--port', str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            url = 'http://127.0.0.1:%d/' % a.port
            for _ in range(100):
                try:
                    urllib.request.urlopen(url + 'api/health', timeout=2).read()
                    break
                except OSError:
                    time.sleep(0.1)
            browser = p.chromium.launch(channel='msedge', headless=True,
                                        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            try:
                page = browser.new_page(viewport={'width': 1400, 'height': 900})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.goto(url)
                page.wait_for_function('window.__preview !== undefined')
                page.wait_for_function("!window.__preview.busy && window.__preview.path === 'block_comps.cdb'", timeout=60000)
                page.evaluate("document.getElementById('followChk').checked = false")
                page.wait_for_timeout(400)
                ev = page.evaluate

                def load(path):
                    ev('k => window.__preview.loadModel(k, false)', path)
                    page.wait_for_function("p => !window.__preview.busy && window.__preview.path === p", arg=path, timeout=60000)
                    page.wait_for_timeout(300)

                def drag(x0, y0, x1, y1, button='left', steps=24):
                    page.mouse.move(x0, y0)
                    page.mouse.down(button=button)
                    page.mouse.move(x1, y1, steps=steps)
                    page.mouse.up(button=button)
                    page.wait_for_timeout(150)

                print('a 3-D model opens with Z up')
                s0 = ev(CAM)
                check(s0['sel'] == 'z' and not s0['dis'], 'the menu says Z and can be changed: %s' % s0['sel'])
                check(ang(s0['up'], [0, 0, 1]) < 1e-6, 'camera up is +Z: %s' % [round(v, 3) for v in s0['up']])
                check(ang(eye(s0), [1, -1, 0.8]) < 1e-3, 'iso view looks from (1, -1, 0.8)')
                page.screenshot(path=os.path.join(SHOTS, 'view_zup_iso.png'))
                r = ev("() => { const b = window.__preview.renderer.domElement.getBoundingClientRect(); "
                       "return [b.left, b.top, b.width, b.height]; }")
                cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
                rad = min(r[2], r[3]) / 2

                print('a vertical drag turns the model over the top')
                drag(cx, cy - 0.35 * rad, cx, cy + 0.35 * rad)                       # 0.7 r down: 2.5 * 0.7 = 1.75 rad
                drag(cx, cy - 0.35 * rad, cx, cy + 0.35 * rad)                       # twice: 3.5 rad = 200 degrees
                s1 = ev(CAM)
                turned = ang(eye(s0), eye(s1))
                check(turned > 150, 'the line of sight turned %.0f degrees (OrbitControls stopped at the pole)' % turned)
                # the screen up before the drag: +Z made square to the line of sight (the iso view looks down 29.5 deg)
                e0 = norm(eye(s0))
                u0 = norm([u - dot(s0['up'], e0) * e for u, e in zip(s0['up'], e0)])
                check(abs(ang(u0, s1['up']) - turned) < 0.5,
                      'the screen up turned with it: %.0f degrees' % ang(u0, s1['up']))
                check(abs(dot(norm(eye(s1)), norm(s1['up']))) < 1e-6, 'up stays square to the line of sight')
                check(abs(math.dist(s0['pos'], s0['tgt']) - math.dist(s1['pos'], s1['tgt'])) < 1e-6 * math.dist(s0['pos'], s0['tgt']),
                      'the distance to the target is unchanged')
                page.screenshot(path=os.path.join(SHOTS, 'view_turned.png'))

                print('a horizontal drag turns it about the screen vertical')
                drag(cx - 0.3 * rad, cy, cx + 0.3 * rad, cy)
                s2 = ev(CAM)
                check(ang(eye(s1), eye(s2)) > 60, 'the line of sight turned %.0f degrees' % ang(eye(s1), eye(s2)))
                check(ang(s1['up'], s2['up']) < 1e-3, 'about the screen vertical: up kept (%.4f degrees)' % ang(s1['up'], s2['up']))

                print('a drag from the edge of the view turns it about the line of sight')
                x0, y0 = r[0] + 6, cy                                                 # left edge, outside 0.9 r
                a0 = math.atan2(-(y0 - cy), x0 - cx)
                a1 = a0 + math.radians(40)                                            # 40 degrees counter-clockwise
                x1, y1 = cx + (cx - x0) * math.cos(a1), cy - (cx - x0) * math.sin(a1)
                page.mouse.move(x0, y0)
                page.mouse.down()
                for k in range(1, 21):
                    ak = a0 + (a1 - a0) * k / 20
                    page.mouse.move(cx + (cx - x0) * math.cos(ak), cy - (cx - x0) * math.sin(ak))
                page.mouse.up()
                page.wait_for_timeout(150)
                s3 = ev(CAM)
                check(ang(eye(s2), eye(s3)) < 1e-3, 'the camera stays where it is (%.4f degrees)' % ang(eye(s2), eye(s3)))
                rolled = ang(s2['up'], s3['up'])
                check(abs(rolled - 40) < 2, 'the up vector turned about the line of sight by %.1f degrees (dragged 40)' % rolled)
                # the model follows the cursor: the world direction that pointed to the screen's right before now
                # points up-right (turned counter-clockwise), i.e. it has a positive component along the new up
                side2 = norm([s2['up'][1] * eye(s2)[2] - s2['up'][2] * eye(s2)[1], s2['up'][2] * eye(s2)[0] - s2['up'][0] * eye(s2)[2],
                              s2['up'][0] * eye(s2)[1] - s2['up'][1] * eye(s2)[0]])    # up x eye = screen right
                yup3 = dot(side2, norm(s3['up']))
                check(abs(yup3 - math.sin(math.radians(40))) < 0.03,
                      'the model turns with the cursor (counter-clockwise on screen): %.3f, expected sin 40 = 0.643' % yup3)

                print('pan and zoom')
                drag(cx, cy, cx + 120, cy + 60, button='right')
                s4 = ev(CAM)
                check(math.dist(s3['tgt'], s4['tgt']) > 0 and ang(eye(s3), eye(s4)) < 1e-6 and ang(s3['up'], s4['up']) < 1e-6,
                      'right drag moves the target (%.4g) and keeps the line of sight' % math.dist(s3['tgt'], s4['tgt']))
                page.mouse.move(cx, cy)
                page.mouse.wheel(0, -400)
                page.wait_for_timeout(200)
                s5 = ev(CAM)
                d4, d5 = math.dist(s4['pos'], s4['tgt']), math.dist(s5['pos'], s5['tgt'])
                check(d5 < d4 and ang(eye(s4), eye(s5)) < 1e-6, 'wheel up: closer (%.4g -> %.4g)' % (d4, d5))
                page.mouse.wheel(0, 400)
                page.wait_for_timeout(200)
                d6 = math.dist(ev(CAM)['pos'], ev(CAM)['tgt'])
                check(abs(d6 - d4) < 1e-6 * d4, 'wheel down by the same amount: back (%.4g)' % d6)

                print('fit keeps the orientation, Iso puts Z up again')
                page.click('#views button[data-v="fit"]')
                page.wait_for_timeout(300)
                s6 = ev(CAM)
                check(ang(s5['up'], s6['up']) < 1e-6 and ang(eye(s5), eye(s6)) < 1e-6, '适应: same orientation')
                page.click('#views button[data-v="iso"]')
                page.wait_for_timeout(300)
                s7 = ev(CAM)
                check(ang(s7['up'], [0, 0, 1]) < 1e-6 and ang(eye(s7), [1, -1, 0.8]) < 1e-3, 'Iso: Z up, from (1, -1, 0.8)')

                print('the menu switches a file to Y; that file keeps it, the others keep Z')
                page.select_option('#upSel', 'y')
                page.wait_for_timeout(300)
                s8 = ev(CAM)
                check(ang(s8['up'], [0, 1, 0]) < 1e-6 and ang(eye(s8), [1, 0.8, 1]) < 1e-3, 'Y up, iso from (1, 0.8, 1)')
                load('demo_beam.inp')
                s9 = ev(CAM)
                check(s9['sel'] == 'z' and ang(s9['up'], [0, 0, 1]) < 1e-6, 'another file (demo_beam.inp) opens with Z up')
                load('block_comps.cdb')
                s9b = ev(CAM)
                check(s9b['sel'] == 'y' and ang(s9b['up'], [0, 1, 0]) < 1e-6, 'block_comps.cdb comes back with Y up')
                page.reload()
                page.wait_for_function('window.__preview !== undefined')
                page.wait_for_function("!window.__preview.busy && window.__preview.path === 'block_comps.cdb'", timeout=60000)
                page.evaluate("document.getElementById('followChk').checked = false")
                page.wait_for_timeout(400)
                s9c = ev(CAM)
                check(s9c['sel'] == 'y' and ang(s9c['up'], [0, 1, 0]) < 1e-6, 'and still after reloading the page')
                page.select_option('#upSel', 'z')
                page.wait_for_timeout(300)
                check(ang(ev(CAM)['up'], [0, 0, 1]) < 1e-6, 'switched back to Z')

                print('a plane model keeps Y up')
                load('plate_hole.inp')
                s10 = ev(CAM)
                dim = ev('() => window.__preview.model.stats.dim')
                check(dim == '2d', 'plate_hole.inp is a plane model (%s)' % dim)
                check(ang(s10['up'], [0, 1, 0]) < 1e-6 and ang(eye(s10), [0, 0, 1]) < 1e-6,
                      'front view of the X-Y plane with Y up: up %s' % [round(v, 3) for v in s10['up']])
                check(s10['dis'] and s10['sel'] == 'y', 'the vertical-axis menu shows Y and is disabled for it')
                load('block_comps.cdb')
                s11 = ev(CAM)
                check(not s11['dis'] and s11['sel'] == 'z' and ang(s11['up'], [0, 0, 1]) < 1e-6,
                      'back to a 3-D model: menu enabled, Z up')

                check(not errors, 'no script errors %s' % (errors[:3] if errors else ''))
                page.close()
            finally:
                browser.close()
    finally:
        if srv:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except subprocess.TimeoutExpired:
                srv.kill()
        shutil.rmtree(ws, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
