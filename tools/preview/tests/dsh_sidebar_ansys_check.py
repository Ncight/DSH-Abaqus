# -*- coding: utf-8 -*-
"""The ANSYS look and the automatic switch inside the right-sidebar Browser of DeepSeek Harness - ANSYS plan, step 4.

    py dsh_sidebar_ansys_check.py [--server-dir DIR] [--dsh-home DIR] [--dsh-port 3082] [--preview-port 8776]

Like dsh_sidebar_check.py (same ISOLATED DSH home, never the user's ~/.dsh; `dsh web --no-open`; headless Edge),
but the panel watches two workspaces, as start_preview.cmd does once extra_roots.local.txt names the ANSYS one:
  WS_ABQ  examples/demo_beam/demo_beam.inp            WS_ANS  tests/data/block_comps.cdb + its solve segment
The CDB is the newest file: inside the sidebar the page must show the ANSYS look (Outline tree, lettered
objects, program and workspace in the ribbon); then the INP is touched and the page must switch to the Abaqus
look by itself; then the CDB again.  Screenshots of the whole DSH window: tests/shots/dsh_sidebar_ansys_*.png.
Stops everything it started (by process id).  Exit code 1 when a check fails.
"""
import argparse
import os
import re
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
import dsh_sidebar_check as base  # noqa: E402

ROOT = base.ROOT
SHOTS = base.SHOTS
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    sys.stdout.flush()
    if not cond:
        FAILS.append(what)


def open_browser_tab(page, url):
    """close the greeting dialogs, open a Browser page in the sidebar, load url there -> the frame"""
    for _ in range(6):
        d = base.first_visible(page, ['[role=dialog] button:has-text("继续")', 'button:has-text("继续")',
                                      '[role=dialog] button:has-text("知道了")', '[role=dialog] button:has-text("跳过")',
                                      '[role=dialog] button[aria-label="关闭"]', 'button[aria-label="关闭"]'])
        if d is None:
            break
        d.click(timeout=5000)
        page.wait_for_timeout(900)
    page.keyboard.press('Control+t')
    page.wait_for_timeout(1500)
    addr = base.first_visible(page, ['input[type=url]', 'input[placeholder*="http" i]', 'input[aria-label*="address" i]',
                                     'input[aria-label*="地址"]', 'input[placeholder*="URL" i]', 'input[placeholder*="网址"]',
                                     'input[aria-label*="URL" i]', 'input[placeholder*="地址"]'])
    if addr is None:
        return None
    addr.fill(url)
    addr.press('Enter')
    for _ in range(100):
        fr = next((f for f in page.frames if f.url.startswith(url)), None)
        if fr is not None:
            return fr
        page.wait_for_timeout(200)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--dsh-home', default=os.path.join(ROOT, '.dsh-eval', 'home-web'))
    ap.add_argument('--dsh-port', type=int, default=3082)
    ap.add_argument('--preview-port', type=int, default=8776)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    home = os.path.abspath(a.dsh_home)
    real = os.path.abspath(os.path.join(os.path.expanduser('~'), '.dsh'))
    if home.lower() == real.lower() or home.lower().startswith(real.lower() + os.sep):
        raise SystemExit('refusing to use the real DSH home')
    os.makedirs(SHOTS, exist_ok=True)
    with heavy_gate.slot('dsh web + headless browser (ansys)', base.MIN_FREE_GB, a.wait_minutes):
        run(a, home)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


def run(a, home):
    patch = os.path.join(home, 'profiles', 'web', 'cordis.patch.yml')
    os.makedirs(os.path.dirname(patch), exist_ok=True)
    if not os.path.isfile(patch) or 'ui-sidebar-browser' not in open(patch, encoding='utf-8').read():
        with open(patch, 'a', encoding='utf-8', newline='\n') as f:
            f.write(base.PATCH)
    tmp = tempfile.mkdtemp(prefix='abq_preview_dshans_')
    wa, wn = os.path.join(tmp, 'WS_ABQ'), os.path.join(tmp, 'WS_ANS')
    os.makedirs(wa)
    os.makedirs(wn)
    t0 = time.time() - 3600
    for src, dst, t in ((os.path.join(ROOT, 'examples', 'demo_beam', 'demo_beam.inp'), os.path.join(wa, 'beam.inp'), t0 + 10),
                        (os.path.join(HERE, 'data', 'block_comps.cdb'), os.path.join(wn, 'job.cdb'), t0 + 20),
                        (os.path.join(HERE, 'data', 'block_comps.solve.dat'), os.path.join(wn, 'job.solve.dat'), t0 + 20)):
        shutil.copyfile(src, dst)
        os.utime(dst, (t, t))
    preview = subprocess.Popen([sys.executable, os.path.join(a.server_dir, 'preview_server.py'), '--root', wa, '--root', wn,
                                '--port', str(a.preview_port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = 'http://127.0.0.1:%d/' % a.preview_port
    for _ in range(60):
        try:
            urllib.request.urlopen(url + 'api/health', timeout=2).read()
            break
        except OSError:
            time.sleep(0.1)
    log, dsh = [], None
    try:
        dsh, dsh_url = base.start_dsh(home, a.dsh_port, log)
        check(bool(dsh_url), 'dsh web is up')
        if not dsh_url:
            return
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True,
                                        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 900})
                page.goto(dsh_url, wait_until='domcontentloaded')
                page.wait_for_timeout(6000)
                fr = base.open_frame(page, url, a)
                check(fr is not None, 'the preview page opens in the sidebar Browser')
                if fr is None:
                    return
                errors = []
                fr.wait_for_function("window.__preview && window.__preview.key === '1|job.cdb' && !window.__preview.busy", timeout=60000)
                fr.select_option('#stepSel', '1')          # load step 1: support, force and pressure in effect
                page.wait_for_timeout(2000)
                info = fr.evaluate("""() => ({solver: document.body.dataset.solver, title: document.title,
                    tag: document.getElementById('solverTag').textContent, narrow: document.body.classList.contains('narrow'),
                    legend: document.getElementById('tagLegend').innerText, tags: document.querySelectorAll('#tags .tag').length,
                    drawn: window.__preview.drawnFraction(), w: innerWidth, h: innerHeight})""")
                check(info['solver'] == 'mapdl' and info['title'] == 'ANSYS 前处理预览' and info['tag'] == 'ANSYS (MAPDL) · WS_ANS',
                      'inside the sidebar: ANSYS look for job.cdb (%s, %dx%d px, narrow=%s)' % (info['tag'], info['w'], info['h'], info['narrow']))
                check('固定支撑' in info['legend'] and info['tags'] >= 3 and info['drawn'] > 0.003,
                      'lettered objects and the model are drawn (%d tags, %.1f%% of the view)' % (info['tags'], 100 * info['drawn']))
                page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_ansys_cdb.png'))
                os.utime(os.path.join(wa, 'beam.inp'), None)
                fr.wait_for_function("window.__preview.key === '0|beam.inp' && !window.__preview.busy", timeout=20000)
                page.wait_for_timeout(1500)
                info = fr.evaluate("""() => ({solver: document.body.dataset.solver, title: document.title,
                    tag: document.getElementById('solverTag').textContent, tags: document.querySelectorAll('#tags .tag').length})""")
                check(info['solver'] == 'abaqus' and info['title'] == 'Abaqus 前处理预览' and info['tags'] == 0,
                      'the INP became the newest file: Abaqus look by itself (%s)' % info['tag'])
                page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_ansys_inp.png'))
                os.utime(os.path.join(wn, 'job.cdb'), None)
                fr.wait_for_function("window.__preview.key === '1|job.cdb' && !window.__preview.busy", timeout=20000)
                page.wait_for_timeout(1500)
                check(fr.evaluate('document.body.dataset.solver') == 'mapdl', 'and back to the ANSYS look when the CDB is newest again')
                page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_ansys_back.png'))
                check(not errors, 'no script errors')
            finally:
                browser.close()
    finally:
        if dsh is not None:
            base.kill_tree(dsh)
        base.kill_tree(preview)
        shutil.rmtree(tmp, ignore_errors=True)
        with open(os.path.join(SHOTS, 'dsh_web_output_ansys.log'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(re.sub(r'token=[^&\s]+', 'token=***', l) for l in log))


if __name__ == '__main__':
    sys.exit(main())
