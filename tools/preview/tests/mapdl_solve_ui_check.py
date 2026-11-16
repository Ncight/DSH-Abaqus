# -*- coding: utf-8 -*-
"""Browser test: the load steps of a solve segment in the preview page (headless Edge) - ANSYS plan, step 3.

    py mapdl_solve_ui_check.py [--server-dir DIR] [--port 8774] [--wait-minutes 30]

A temp workspace with tests/data/block_comps.cdb and its solve segment as job.cdb / job.solve.dat.  The page
(with the ANSYS look since step 4) must list the CDB state and the nine load steps in the step selector and the
tree, list the lettered objects of the state of the chosen step (pressure gone after SFDELE in load step 3, the
tip forces gone after FDELE in load step 4), reload by itself when only the solve segment
changes and show the refused line with its number among the checks, with no script error.
Screenshots: tests/shots/mapdl_solve_steps.png, tests/shots/mapdl_solve_refused.png.  Exit code 1 on failure.
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
    ap.add_argument('--port', type=int, default=8774)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    ws = tempfile.mkdtemp(prefix='abq_preview_solve_ui_')
    data = os.path.join(HERE, 'data')
    shutil.copy2(os.path.join(data, 'block_comps.cdb'), os.path.join(ws, 'job.cdb'))
    shutil.copy2(os.path.join(data, 'block_comps.solve.dat'), os.path.join(ws, 'job.solve.dat'))
    srv = None
    try:
        with heavy_gate.slot('headless browser (mapdl_solve_ui_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
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
                page.wait_for_function("!window.__preview.busy && window.__preview.path === 'job.cdb'", timeout=60000)
                page.wait_for_timeout(500)
                opts = page.evaluate("[...document.querySelectorAll('#stepSel option')].map(o => o.textContent)")
                check(opts == ['初始 (无载荷)', 'CDB'] + ['载荷步 %d' % k for k in range(1, 10)], 'step selector: %s' % opts)
                tree = page.evaluate("document.getElementById('tree').innerText")
                check('载荷步 9' in tree and '删除面载荷: NTOP' in tree and '杀死单元: ERIGHT' in tree and '删除约束: NBASE' in tree,
                      'the tree lists the load steps and their commands')

                def legend_at(step):
                    page.select_option('#stepSel', str(step))
                    page.wait_for_timeout(700)
                    return page.evaluate("document.getElementById('tagLegend').innerText")
                l2, l3, l4 = legend_at(2), legend_at(3), legend_at(4)
                check('压力: NTOP 2000' in l2 and '力: NEND' in l2, 'load step 2: pressure 2000 and forces (%s)' % l2.replace('\n', ' | '))
                check('压力' not in l3 and '力: NEND' in l3, 'load step 3 (after SFDELE): no pressure (%s)' % l3.replace('\n', ' | '))
                check('压力' not in l4 and '力: NEND' not in l4 and '固定支撑' in l4,
                      'load step 4 (after FDELE): only the constraints (%s)' % l4.replace('\n', ' | '))
                legend_at(2)
                page.screenshot(path=os.path.join(SHOTS, 'mapdl_solve_steps.png'))
                time.sleep(1.1)
                with open(os.path.join(ws, 'job.solve.dat'), 'a') as f:
                    f.write('/SOLU\nNSEL,S,LOC,X,0\nSOLVE\n')
                page.wait_for_function("document.querySelectorAll('#stepSel option').length === 12", timeout=20000)
                page.wait_for_timeout(500)
                page.evaluate("document.querySelector('[data-tab=chk]') && document.querySelector('[data-tab=chk]').click()")
                chk = page.evaluate("document.getElementById('checks').innerText")
                check('第 25 行: 命令 NSEL 不在求解段白名单内' in chk, 'reloaded by itself; the refused line with its number')
                page.screenshot(path=os.path.join(SHOTS, 'mapdl_solve_refused.png'))
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
