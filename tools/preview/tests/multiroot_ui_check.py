# -*- coding: utf-8 -*-
"""Browser test of the page with two workspaces (headless Edge through Playwright) - ANSYS plan, step 1.

    py multiroot_ui_check.py [--server-dir DIR] [--port 8770] [--wait-minutes 30]

Two temp workspaces WS_A and WS_B hold the same relative path job/m.inp (two different models) and B also
holds only_b.inp.  Checks that the page
  * opens the newest file of either workspace and labels every file with its workspace in the list;
  * loads the right one of the two job/m.inp when it is picked from the list;
  * keeps the unsent 标注 of the two job/m.inp apart;
  * writes an approval into the workspace the file is in;
  * with 跟随最新文件 on, moves to a file of the other workspace when that one becomes the newest;
  * raises no script error.
One headless browser and one small preview server; both are started and stopped here.
Waits (heavy_gate.py) until no other heavy test step runs and enough commit memory is free.
Screenshot: tests/shots/multiroot.png.  Exit code 1 when a check fails.
"""
import argparse
import json
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

ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
BEAM = os.path.join(ROOT, 'examples', 'demo_beam', 'demo_beam.inp')
POISSON = os.path.join(ROOT, 'examples', 'corpus', 'checks', 'chk_material_poisson.inp')
SHOTS = os.path.join(HERE, 'shots')
MIN_FREE_GB = 4.0
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    sys.stdout.flush()
    if not cond:
        FAILS.append(what)


def copy(src, dst, mtime):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.utime(dst, (mtime, mtime))


def wait_key(page, key, timeout=30000):
    page.wait_for_function('k => !window.__preview.busy && window.__preview.key === k', arg=key, timeout=timeout)
    page.wait_for_timeout(300)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='abq_preview_multiui_')
    wa, wb = os.path.join(tmp, 'WS_A'), os.path.join(tmp, 'WS_B')
    t0 = time.time() - 3600
    copy(BEAM, os.path.join(wa, 'job', 'm.inp'), t0 + 10)
    copy(POISSON, os.path.join(wb, 'job', 'm.inp'), t0 + 20)
    copy(BEAM, os.path.join(wb, 'only_b.inp'), t0 + 30)
    srv = None
    try:
        with heavy_gate.slot('headless browser (multiroot_ui_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
            srv = subprocess.Popen([sys.executable, os.path.join(a.server_dir, 'preview_server.py'), '--root', wa,
                                    '--root', wb, '--port', str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

                print('first load and the file list')
                wait_key(page, '1|only_b.inp')
                check(page.evaluate('window.__preview.path') == 'only_b.inp', 'opens the newest file of either workspace (B/only_b.inp)')
                opts = page.evaluate("[...document.getElementById('fileSel').options].map(o => [o.value, o.textContent])")
                check(opts == [['1|only_b.inp', '[WS_B] only_b.inp'], ['1|job/m.inp', '[WS_B] job/m.inp'], ['0|job/m.inp', '[WS_A] job/m.inp']],
                      'every file is labelled with its workspace: %s' % opts)
                page.screenshot(path=os.path.join(SHOTS, 'multiroot.png'))

                print('picking files')
                page.select_option('#fileSel', '0|job/m.inp')
                wait_key(page, '0|job/m.inp')
                n_a = page.evaluate('window.__preview.model.stats.elements')
                check(n_a == 1472 and not page.evaluate("document.getElementById('followChk').checked"),
                      'A/job/m.inp loaded (%d elements), following stopped' % n_a)
                page.select_option('#fileSel', '1|job/m.inp')
                wait_key(page, '1|job/m.inp')
                n_b = page.evaluate('window.__preview.model.stats.elements')
                check(n_b != 1472, 'B/job/m.inp is the other model (%d elements)' % n_b)

                print('unsent 标注 stay with their file')
                page.evaluate("window.__preview.addComment('材料 测试', '1')")
                keys = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('abq-preview-pending:'))")
                check('abq-preview-pending:WS_B|job/m.inp' in keys, 'stored under the workspace name: %s' % keys)
                page.select_option('#fileSel', '0|job/m.inp')
                wait_key(page, '0|job/m.inp')
                check(page.evaluate('window.__preview.pending.length') == 0, 'A/job/m.inp does not show the 标注 made on B/job/m.inp')
                page.select_option('#fileSel', '1|job/m.inp')
                wait_key(page, '1|job/m.inp')
                check(page.evaluate('window.__preview.pending.length') == 1, 'back on B/job/m.inp the 标注 is there again')

                print('approval goes to the workspace of the file')
                page.select_option('#fileSel', '1|only_b.inp')
                wait_key(page, '1|only_b.inp')
                errs = page.evaluate("window.__preview.model.checks.filter(c => c.level === 'error').length + (window.__preview.model.incomplete || []).length")
                check(errs == 0, 'B/only_b.inp has no 体检 error (approval without the confirm box)')
                page.evaluate("window.__preview.decide('approved')")
                page.wait_for_function("window.__preview.review && window.__preview.review.state === 'approved'", timeout=15000)
                rj = os.path.join(wb, '.abq', 'review.json')
                ok = os.path.isfile(rj) and json.load(open(rj, encoding='utf-8'))['files'].get('only_b.inp', {}).get('status') == 'approved'
                check(ok and not os.path.exists(os.path.join(wa, '.abq')), 'written to WS_B/.abq/review.json, nothing in WS_A')

                print('following the newest file across workspaces')
                page.evaluate("document.getElementById('followChk').checked = true")
                now = time.time()
                os.utime(os.path.join(wa, 'job', 'm.inp'), (now, now))
                wait_key(page, '0|job/m.inp', timeout=20000)
                check(page.evaluate("document.getElementById('fileSel').value") == '0|job/m.inp',
                      'A/job/m.inp became the newest: the page moved to it and the list shows it')
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
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
