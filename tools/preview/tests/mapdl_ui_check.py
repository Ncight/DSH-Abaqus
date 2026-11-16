# -*- coding: utf-8 -*-
"""Browser test: a MAPDL CDB in the preview page (headless Edge through Playwright) - ANSYS plan, step 2.

    py mapdl_ui_check.py [--server-dir DIR] [--port 8772] [--wait-minutes 30]

A temp workspace with tests/data/ks_build.cdb.  The page (with the ANSYS look since step 4) must load it, draw it,
build the model tree, list the lettered BC / load objects of its load state, highlight the two sides of a contact
pair, and raise no script error.  Screenshots: tests/shots/mapdl_ks_build.png and
tests/shots/mapdl_ks_contact.png.  Exit code 1 when a check fails.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_gate  # noqa: E402

SHOTS = os.path.join(HERE, 'shots')
MIN_FREE_GB = 4.0
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    sys.stdout.flush()
    if not cond:
        FAILS.append(what)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8772)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    ws = tempfile.mkdtemp(prefix='abq_preview_mapdl_ui_')
    shutil.copy2(os.path.join(HERE, 'data', 'ks_build.cdb'), os.path.join(ws, 'ks_build.cdb'))
    srv = None
    try:
        with heavy_gate.slot('headless browser (mapdl_ui_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
            srv = subprocess.Popen([sys.executable, os.path.join(a.server_dir, 'preview_server.py'), '--root', ws,
                                    '--port', str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            url = 'http://127.0.0.1:%d/' % a.port
            import urllib.request
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
                page.wait_for_function("!window.__preview.busy && window.__preview.path === 'ks_build.cdb'", timeout=60000)
                page.wait_for_timeout(500)
                m = page.evaluate('() => { const m = window.__preview.model; return {solver: m.solver, n: m.stats.nodes, '
                                  'e: m.stats.elements, steps: m.steps.length}; }')
                check(m == {'solver': 'mapdl', 'n': 2684, 'e': 2404, 'steps': 1}, 'the CDB is loaded: %s' % m)
                frac = page.evaluate('() => window.__preview.drawnFraction()')
                check(frac > 0.01, 'the model covers %.1f%% of the viewport' % (100 * frac))
                tree = page.evaluate("document.getElementById('tree').innerText")
                check('MAT1' in tree and 'SOLID185' in tree and 'REAL 5' in tree, 'model tree lists materials, sections and contact pairs')
                legend = page.evaluate("document.getElementById('tagLegend').innerText")
                check('固定支撑' in legend and '压力' in legend and '加速度' in legend,
                      'lettered BC / pressure / acceleration objects of the load state (ANSYS look): %s'
                      % legend.replace('\n', ' | ')[:160])
                page.screenshot(path=os.path.join(SHOTS, 'mapdl_ks_build.png'))
                leaf = page.locator('#tree .leaf', has_text='REAL 5').first
                leaf.scroll_into_view_if_needed()
                leaf.click()
                page.wait_for_timeout(800)
                check('接触单元' in page.evaluate("document.getElementById('prop').innerText"), 'a contact pair can be picked')
                page.screenshot(path=os.path.join(SHOTS, 'mapdl_ks_contact.png'))
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
