# -*- coding: utf-8 -*-
"""Browser test: the ANSYS look and the automatic switch between the two looks - ANSYS plan, step 4.

    py ansys_skin_check.py [--server-dir DIR] [--port 8775] [--wait-minutes 30]

Two temp workspaces, the way the panel runs once start_preview.cmd lists the ANSYS workspace too:
  WS_ABQ  beam.inp (examples/demo_beam)
  WS_ANS  job.cdb + job.solve.dat (tests/data/block_comps), ks.cdb (tests/data/ks_build)
Checks that the page
* opens the newest file (job.cdb) with the ANSYS look: body[data-solver=mapdl], title, Outline tree (Geometry,
  Materials, Coordinate Systems, Connections, Mesh, Named Selections, the analysis with its settings and load
  steps), the legend box with lettered objects and the letter tags in the graphics window, the program and
  workspace in the ribbon;
* changes the lettered objects with the load step (no pressure after SFDELE in load step 3);
* goes back to the Abaqus look by itself when an INP becomes the newest file (texts and title restored, no tags);
* does not jump to a newer file while a review comment is unsent, and follows again once it is gone;
* shows contact pairs, constraint equations, coupled sets and the local coordinate system of ks.cdb;
* raises no script error.
Screenshots: tests/shots/ansys_skin_block.png, ansys_skin_step3.png, ansys_skin_ks.png, ansys_skin_back_abaqus.png.
Exit code 1 on failure.
"""
import argparse
import os
import re
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
DATA = os.path.join(HERE, 'data')
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
    page.wait_for_timeout(700)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8775)
    ap.add_argument('--wait-minutes', type=float, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='abq_preview_skin_')
    wa, wn = os.path.join(tmp, 'WS_ABQ'), os.path.join(tmp, 'WS_ANS')
    t0 = time.time() - 3600
    copy(BEAM, os.path.join(wa, 'beam.inp'), t0 + 10)
    copy(os.path.join(DATA, 'ks_build.cdb'), os.path.join(wn, 'ks.cdb'), t0 + 5)
    copy(os.path.join(DATA, 'block_comps.cdb'), os.path.join(wn, 'job.cdb'), t0 + 20)
    copy(os.path.join(DATA, 'block_comps.solve.dat'), os.path.join(wn, 'job.solve.dat'), t0 + 20)
    srv = None
    try:
        with heavy_gate.slot('headless browser (ansys_skin_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
            srv = subprocess.Popen([sys.executable, os.path.join(a.server_dir, 'preview_server.py'), '--root', wa,
                                    '--root', wn, '--port', str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                ev = page.evaluate

                print('the newest file is a CDB: ANSYS look')
                wait_key(page, '1|job.cdb')
                check(ev('document.body.dataset.solver') == 'mapdl' and ev('document.title') == 'ANSYS 前处理预览',
                      'body[data-solver=mapdl], title "ANSYS 前处理预览"')
                check(ev("document.getElementById('treeBtn').textContent") == '☰ 大纲' and
                      ev("document.getElementById('tabTree').textContent") == '大纲', 'ribbon and tab say 大纲 (Outline)')
                tag = ev("document.getElementById('solverTag')")
                st = ev("[getComputedStyle(document.getElementById('solverTag')).display, document.getElementById('solverTag').textContent]")
                check(st[0] != 'none' and st[1] == 'ANSYS (MAPDL) · WS_ANS', 'program and workspace in the ribbon: %s' % st)
                tree = ev("document.getElementById('tree').innerText")
                want = ['几何', '材料', '坐标系', '连接', '网格', '命名选择', '分析设置', 'CDB 载荷状态', '载荷步 9', '求解段',
                        '固定支撑: NBASE', '压力: NTOP', '杀死单元: ERIGHT', '删除约束: NBASE']
                miss = [w for w in want if w not in tree]
                check(not miss, 'Outline tree items (missing: %s)' % miss)
                # step 6 (live run): a big EX in SI showed as EX=200000000000.0000 in the tree
                ex = re.findall(r'EX=\S+', tree)
                check(ex and all(re.fullmatch(r'EX=[\d,]+', e) or re.fullmatch(r'EX=\d{1,5}\.\d{4}', e) for e in ex),
                      'material labels: big values grouped, no decimals: %s' % ex)
                check('同时监视 ANSYS（MAPDL）的 .cdb 文件' in ev("document.getElementById('msg').innerText"),
                      'the log says ANSYS .cdb files are watched too')
                bg = ev("getComputedStyle(document.getElementById('vp')).backgroundImage")
                check('gradient' in bg and '255, 255, 255' in bg, 'blue-white background: %s' % bg[:80])
                page.select_option('#stepSel', '1')
                page.wait_for_timeout(900)
                leg = ev("document.getElementById('tagLegend').innerText")
                tags = ev("[...document.querySelectorAll('#tags .tag')].filter(t => t.style.display !== 'none').map(t => t.textContent)")
                check('静力结构' in leg and 'A固定支撑: NBASE' in leg.replace('\n', '') and '压力: NTOP' in leg and '力: NEND' in leg,
                      'legend box: analysis and lettered objects (%s)' % leg.replace('\n', ' | ')[:160])
                check(tags == ['A', 'B', 'C'], 'letter tags in the graphics window: %s' % tags)
                title = ev("document.getElementById('vpTitle').textContent")
                check('静力结构' in title and '载荷步 1' in title, 'title bar of the graphics window: %s' % title)
                page.screenshot(path=os.path.join(SHOTS, 'ansys_skin_block.png'))
                page.select_option('#stepSel', '4')
                page.wait_for_timeout(900)
                leg3 = ev("document.getElementById('tagLegend').innerText")
                check('压力' not in leg3 and '固定支撑' in leg3, 'load step 3 (after SFDELE): no pressure object (%s)' % leg3.replace('\n', ' | ')[:120])
                page.screenshot(path=os.path.join(SHOTS, 'ansys_skin_step3.png'))
                leaf = page.locator('#tree .leaf', has_text='压力: NTOP').first
                leaf.scroll_into_view_if_needed()
                leaf.click()
                page.wait_for_timeout(700)
                prop = ev("document.getElementById('prop').innerText")
                check('对象' in prop and 'NTOP' in prop and '求解段行号' in prop, 'details of the SF load: object, line of the solve segment')

                print('an INP becomes the newest file: Abaqus look again')
                os.utime(os.path.join(wa, 'beam.inp'), (time.time(), time.time()))
                wait_key(page, '0|beam.inp')
                check(ev('document.body.dataset.solver') == 'abaqus' and ev('document.title') == 'Abaqus 前处理预览',
                      'body[data-solver=abaqus], title "Abaqus 前处理预览"')
                check(ev("document.getElementById('treeBtn').textContent") == '☰ 模型树' and
                      ev("document.getElementById('tabTree').textContent") == '模型' and
                      ev("document.getElementById('stepSel').parentElement.querySelector('.wide').textContent") == '分析步: ',
                      'Abaqus texts are back (模型树 / 模型 / 分析步)')
                check(ev("document.querySelectorAll('#tags .tag').length") == 0 and ev("document.getElementById('tagLegend').innerHTML") == '',
                      'no letter tags, no legend box')
                check(ev("getComputedStyle(document.getElementById('tagLegend')).display") == 'none', 'legend box hidden')
                st = ev("[getComputedStyle(document.getElementById('solverTag')).display, document.getElementById('solverTag').textContent]")
                check(st[0] != 'none' and st[1] == 'Abaqus · WS_ABQ', 'two workspaces: the ribbon names program and workspace: %s' % st)
                tree = ev("document.getElementById('tree').innerText")
                check('部件' in tree and '装配' in tree and '几何' not in tree, 'Abaqus model tree')
                page.screenshot(path=os.path.join(SHOTS, 'ansys_skin_back_abaqus.png'))

                print('no jump while a review comment is unsent')
                ev("window.__preview.addComment('材料 / E', '200000')")
                os.utime(os.path.join(wn, 'job.cdb'), (time.time(), time.time()))
                time.sleep(4.5)
                check(ev('window.__preview.key') == '0|beam.inp', 'stays on beam.inp while the comment is unsent')
                check('有未提交的审查意见' in ev("document.getElementById('msg').innerText"), 'and says why in the log')
                ev("window.__preview.pending.length = 0")
                wait_key(page, '1|job.cdb')
                check(ev('document.body.dataset.solver') == 'mapdl', 'follows the newest file again once the comment is gone')

                print('ks.cdb: contact, CE, CP, local coordinate system')
                page.select_option('#fileSel', '1|ks.cdb')
                wait_key(page, '1|ks.cdb')
                ev("document.getElementById('rvPanel').classList.remove('open')")
                ev("document.querySelectorAll('#tree details').forEach(d => d.open = true)")
                tree = ev("document.getElementById('tree').innerText")
                want = ['接触 REAL 5 (绑定)', '接触 REAL 6', 'CE 1', 'CP 1 (UX)', '局部坐标系 11', 'ET 5: CONTA174', '实常数 R 4', '截面 2: BEAM RECT']
                miss = [w for w in want if w not in tree]
                check(not miss, 'connections, coordinate systems, mesh items (missing: %s)' % miss)
                leg = ev("document.getElementById('tagLegend').innerText")
                check('力: BEAM_TIP' in leg and '%FTAB%' in leg and '加速度' in leg and '温度 (单元): V1_HOT' in leg,
                      'legend: the table force, the acceleration, the element temperature (%s)' % leg.replace('\n', ' | ')[:200])
                page.screenshot(path=os.path.join(SHOTS, 'ansys_skin_ks.png'))
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
