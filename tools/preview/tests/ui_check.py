# -*- coding: utf-8 -*-
"""Browser test of the preview page (headless Edge through Playwright): screenshots of corpus models,
the review / approval workflow, the narrow-pane layout and the "file too large" notice.

    py ui_check.py [--server-dir DIR] [--port 8766] [--only shots|review|narrow|large|iframe] [--wait-minutes 30]

One headless browser, one small preview server at a time; both are started and stopped here.
Waits (heavy_gate.py) until no other heavy test step runs and MIN_FREE_GB of commit memory is free.
Screenshots go to tests/shots/.  Exit code 1 when a check fails.
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
CORPUS = os.path.join(ROOT, 'examples', 'corpus')
SHOTS = os.path.join(HERE, 'shots')
MIN_FREE_GB = 4.0
FAILS = []

# (file, screenshot name, steps)   steps: ('view', name) | ('color', mode) | ('leaf', text) | ('tab', 'chk')
MODELS = [
    ('cae/shell_roof_s4r_s3.inp', 'shell_roof_iso', []),
    ('cae/shell_roof_s4r_s3.inp', 'shell_roof_from_below', [('view', 'bottom')]),
    ('cae/frame_b31_t3d2.inp', 'frame_beams_trusses', [('color', 'sec')]),
    ('cae/plate_hole_cps4r_cps3.inp', 'plate_hole_2d', []),
    ('cae/axisym_cylinder_cax4r.inp', 'axisym_cylinder', []),
    ('cae/quad_solids_c3d10_c3d20r.inp', 'quad_solids_tie_highlight', [('leaf', 'Tie: BossTie')]),
    ('cae/rigid_analytical_punches.inp', 'rigid_analytical_contact_highlight', [('leaf', '接触对: BlockTop / PunchCyl-1.PunchCylSurf')]),
    ('cae/explicit_taylor_bar.inp', 'explicit_taylor_bar', [('color', 'inst')]),
    ('cae/rotated_instances.inp', 'rotated_instances', [('color', 'inst')]),
    ('cae/contact_pair_blocks.inp', 'contact_pair_highlight', [('leaf', '接触对: UpperBottom / LowerTop')]),
    ('official/pipewhip_s4rs.inp', 'official_pipewhip_generated_mesh', []),
    ('official/knee_bolster_cpair.inp', 'official_knee_bolster', [('color', 'sec')]),
    ('official/boltpipeflange_axi_gkax6.inp', 'official_boltpipeflange_axi', [('color', 'sec')]),
    ('official/xshell2solid_builtupbeam.inp', 'official_shell2solid', [('color', 'inst')]),
    ('checks/chk_tie_overhang.inp', 'check_tie_overhang', [('leaf', 'Tie: PadTie'), ('tab', 'chk')]),
    ('checks/chk_adjacent_untied.inp', 'check_adjacent_untied', [('tab', 'chk')]),
]


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    sys.stdout.flush()
    if not cond:
        FAILS.append(what)


class Server(object):
    def __init__(self, server_dir, root, port, extra=()):
        self.p = subprocess.Popen([sys.executable, os.path.join(server_dir, 'preview_server.py'), '--root', root,
                                   '--port', str(port)] + list(extra), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        self.url = 'http://127.0.0.1:%d/' % port
        import urllib.request
        for _ in range(60):
            try:
                urllib.request.urlopen(self.url + 'api/health', timeout=2).read()
                return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise SystemExit('preview server did not start')

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def open_page(browser, url, width=1400, height=900):
    page = browser.new_page(viewport={'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
    page.goto(url)
    page.wait_for_function('window.__preview !== undefined')
    return page, errors


def load(page, path):
    for _ in range(20):          # the page's own polling may be loading something at this very moment
        page.wait_for_function('!window.__preview.busy')
        page.evaluate("p => { document.getElementById('followChk').checked = false; return window.__preview.loadModel(p, false); }", path)
        if page.evaluate('p => !window.__preview.busy && window.__preview.path === p', path):
            break
        page.wait_for_timeout(200)
    page.wait_for_function('p => !window.__preview.busy && window.__preview.path === p', arg=path, timeout=60000)
    page.wait_for_timeout(350)


def click_leaf(page, text):
    loc = page.locator('#tree .leaf', has_text=text).first
    loc.scroll_into_view_if_needed()
    loc.click()
    page.wait_for_timeout(450)


def run_shots(browser, a):
    print('screenshots of corpus models')
    srv = Server(a.server_dir, CORPUS, a.port)
    try:
        page, errors = open_page(browser, srv.url)
        for rel, name, steps in [m for m in MODELS if os.path.isfile(os.path.join(CORPUS, m[0].replace('/', os.sep)))]:
            load(page, rel)
            m = page.evaluate('() => { const m = window.__preview.model; return m && {n: m.stats.nodes, e: m.stats.elements, dim: m.stats.dim}; }')
            if not m:
                check(False, '%s did not load' % rel)
                continue
            for kind, arg in steps:
                if kind == 'view':
                    page.evaluate('v => window.__preview.setView(v)', arg)
                elif kind == 'color':
                    page.select_option('#colorSel', arg)
                elif kind == 'leaf':
                    click_leaf(page, arg)
                elif kind == 'tab':
                    page.click('#tabChk')
            page.wait_for_timeout(300)
            frac = page.evaluate('() => window.__preview.drawnFraction()')
            lay = page.evaluate("() => ({head: document.getElementById('propHead').getBoundingClientRect().height,"
                                " sel: document.getElementById('fileSel').value})")
            if lay['head'] < 16:        # a long model tree must not squeeze the header of the property pane
                check(False, '%s: the 属性 header is only %.0f px high' % (rel, lay['head']))
            if lay['sel'] != rel:
                check(False, '%s: the file list shows %r instead' % (rel, lay['sel']))
            page.screenshot(path=os.path.join(SHOTS, name + '.png'))
            check(frac > 0.004, '%-42s %6d nodes %6d elements, model covers %4.1f%% of the viewport -> shots/%s.png' % (
                rel, m['n'], m['e'], 100 * frac, name))
        check(not errors, 'no script errors on the page %s' % (errors[:3] if errors else ''))
        page.close()
    finally:
        srv.stop()


def workspace():
    ws = tempfile.mkdtemp(prefix='abq_preview_ui_')
    os.makedirs(os.path.join(ws, 'job'))
    shutil.copy2(os.path.join(ROOT, 'examples', 'demo_beam', 'demo_beam.inp'), os.path.join(ws, 'job'))
    shutil.copy2(os.path.join(CORPUS, 'checks', 'chk_material_poisson.inp'), os.path.join(ws, 'job'))
    return ws


def run_review(browser, a):
    print('review workflow')
    ws = workspace()
    srv = Server(a.server_dir, ws, a.port)
    rel = 'job/demo_beam.inp'
    rj = os.path.join(ws, '.abq', 'review.json')
    try:
        page, errors = open_page(browser, srv.url)
        load(page, rel)
        check(page.inner_text('#rvState') == '未审查', 'bar shows 未审查 for a new file')
        # annotate the density of Steel from the property pane
        click_leaf(page, 'Steel')
        row = page.locator('#prop tr', has_text='*Density').first
        row.locator('.ann').click()
        check(page.locator('#rvPanel.open').count() == 1 and page.inner_text('#rvCount') == '1', 'a 标注 click opens the panel with one comment')
        c = page.evaluate('() => window.__preview.pending[0]')
        check(c['target'] == '材料 Steel / *Density' and c['current'].startswith('7.85'), 'comment is pre-filled: %s' % json.dumps(c, ensure_ascii=False))
        page.locator('#rvList input').first.fill('密度应为 7.85 t/m³, 请核对单位制')
        # a 体检 row can be annotated too; then delete it again
        page.click('#tabChk')
        n_ann = page.locator('#checks .ann').count()
        if n_ann:
            page.locator('#checks .ann').first.click()
            check(page.inner_text('#rvCount') == '2', 'a 体检 row adds a second comment')
            page.locator('#rvList .del').nth(1).click()
        check(page.inner_text('#rvCount') == '1', 'the comment list shows one entry after deleting')
        page.fill('#rvNote', '总体: 材料参数请统一成 m-t-s 制')
        page.screenshot(path=os.path.join(SHOTS, 'review_panel_with_comment.png'))
        # copy the instructions (same text as the .md)
        page.click('#rvCopy')
        page.wait_for_function('window.__preview.lastCopied !== null')
        copied = page.evaluate('() => window.__preview.lastCopied')
        check('审查意见单' in copied and '材料 Steel / *Density' in copied and '密度应为 7.85' in copied, '复制修改指令 yields the 审查意见单 text')
        if page.locator('#modal.open').count():
            page.click('#mdOk')
        check(not os.path.exists(rj), 'copying does not record a decision')
        # reject
        page.click('#rvReject')
        page.wait_for_function("document.getElementById('rvState').textContent === '已退回'", timeout=10000)
        e = json.load(open(rj, encoding='utf-8'))['files'][rel]
        check(e['status'] == 'rejected' and e['comments'][0]['target'] == '材料 Steel / *Density' and '7.85 t/m³' in e['comments'][0]['note']
              and e['note'].startswith('总体'), 'reject is written to review.json with the comment and the note')
        md = open(os.path.join(ws, '.abq', 'review', 'demo_beam.md'), encoding='utf-8').read()
        def no_time(t):
            return '\n'.join(l for l in t.split('\n') if not l.startswith('- 时间：'))
        check(no_time(md) == no_time(copied), 'the .md is the text that was copied (apart from the time stamp)')
        page.screenshot(path=os.path.join(SHOTS, 'review_rejected.png'))
        # approve a clean file directly
        page.click('#rvApprove')
        page.wait_for_function("document.getElementById('rvState').textContent === '已批准'", timeout=10000)
        check(json.load(open(rj, encoding='utf-8'))['files'][rel]['status'] == 'approved', 'approve is written to review.json')
        page.screenshot(path=os.path.join(SHOTS, 'review_approved.png'))
        # the approval lapses when the INP changes on disk
        with open(os.path.join(ws, 'job', 'demo_beam.inp'), 'ab') as f:
            f.write(b'** edited after approval\n')
        page.wait_for_function("document.getElementById('rvState').textContent.indexOf('需重新审查') >= 0", timeout=15000)
        check(page.evaluate("() => document.getElementById('rvState').className") == 'stale', 'a changed file shows 批准后文件已变更，需重新审查')
        page.screenshot(path=os.path.join(SHOTS, 'review_lapsed_after_change.png'))
        # a file with 体检 errors needs the extra confirmation
        load(page, 'job/chk_material_poisson.inp')
        page.click('#rvApprove')
        page.wait_for_selector('#modal.open')
        check(page.is_disabled('#mdOk'), 'approving a file with 体检 errors opens a confirmation whose button is locked')
        page.screenshot(path=os.path.join(SHOTS, 'review_confirm_errors.png'))
        page.click('#mdCancel')
        check('job/chk_material_poisson.inp' not in json.load(open(rj, encoding='utf-8'))['files'], 'cancelling records nothing')
        page.click('#rvApprove')
        page.wait_for_selector('#modal.open')
        page.check('#mdBody input[type=checkbox]')
        check(not page.is_disabled('#mdOk'), 'ticking the box unlocks it')
        page.click('#mdOk')
        page.wait_for_function("document.getElementById('rvState').textContent === '已批准'", timeout=10000)
        check(json.load(open(rj, encoding='utf-8'))['files']['job/chk_material_poisson.inp']['status'] == 'approved',
              'the confirmed approval is recorded')
        # reject needs something to say
        page.click('#rvReject')
        page.wait_for_selector('#modal.open')
        check('至少' in page.inner_text('#mdBody'), 'an empty rejection is not accepted')
        page.click('#mdOk')
        check(not errors, 'no script errors on the page %s' % (errors[:3] if errors else ''))
        page.close()
    finally:
        srv.stop()
        shutil.rmtree(ws, ignore_errors=True)


def run_narrow(browser, a):
    print('narrow pane (as in a sidebar browser)')
    srv = Server(a.server_dir, CORPUS, a.port)
    try:
        for width in (480, 640):
            page, errors = open_page(browser, srv.url, width, 820)
            load(page, 'cae/quad_solids_c3d10_c3d20r.inp')
            check(page.evaluate("() => document.body.classList.contains('narrow') && document.body.classList.contains('collapsed')"),
                  '%d px: narrow layout, tree folded away' % width)
            over = page.evaluate('() => document.documentElement.scrollWidth - document.documentElement.clientWidth')
            vpw = page.evaluate("() => document.getElementById('vp').clientWidth")
            check(over <= 0 and vpw >= width - 12, '%d px: no horizontal overflow, viewport %d px wide' % (width, vpw))
            frac = page.evaluate('() => window.__preview.drawnFraction()')
            check(frac > 0.01, '%d px: model drawn (%.1f%% of the viewport)' % (width, 100 * frac))
            for sel in ('#rvApprove', '#rvReject', '#rvToggle', '#rvCopy', '#treeBtn', '#moreBtn'):
                box = page.locator(sel).bounding_box()
                if not box or box['x'] < 0 or box['x'] + box['width'] > width + 1:
                    check(False, '%d px: %s is outside the pane' % (width, sel))
            page.screenshot(path=os.path.join(SHOTS, 'narrow_%d.png' % width))
            page.click('#treeBtn')
            click_leaf(page, 'Tie: BossTie')
            page.screenshot(path=os.path.join(SHOTS, 'narrow_%d_tree_open.png' % width))
            page.click('#treeBtn')
            page.click('#moreBtn')
            page.select_option('#colorSel', 'mat')
            page.screenshot(path=os.path.join(SHOTS, 'narrow_%d_options.png' % width))
            check(not errors, '%d px: no script errors %s' % (width, errors[:3] if errors else ''))
            page.close()
    finally:
        srv.stop()


def run_large(browser, a):
    print('file above the automatic size limit')
    ws = workspace()
    srv = Server(a.server_dir, ws, a.port, ['--max-auto-mb', '0.05'])
    try:
        page, errors = open_page(browser, srv.url, 900, 700)
        load(page, 'job/demo_beam.inp')
        info = page.evaluate('() => window.__preview.tooLarge')
        check(bool(info) and page.locator('#big.open').count() == 1 and page.evaluate('() => window.__preview.model') is None,
              'the page shows the notice instead of parsing: %s' % json.dumps(info, ensure_ascii=False)[:200])
        btn = page.inner_text('#bigBtn')
        check(btn.startswith('仍然解析（预计占用约') and btn.endswith('GB 内存）'), 'button reads: %s' % btn)
        check(page.is_disabled('#rvApprove') and page.is_disabled('#rvReject'), 'an unparsed file cannot be approved or rejected')
        page.screenshot(path=os.path.join(SHOTS, 'too_large_notice.png'))
        if info and info.get('canForce'):
            page.click('#bigBtn')
            page.wait_for_function('window.__preview.model !== null', timeout=30000)
            check(page.locator('#big.open').count() == 0, 'after the explicit request the model is shown')
        else:
            check(page.is_disabled('#bigBtn') and '拒绝' in page.inner_text('#bigWhy'),
                  'with little memory free the button is locked and says why: %s' % page.inner_text('#bigWhy'))
        check(not errors, 'no script errors on the page %s' % (errors[:3] if errors else ''))
        page.close()
    finally:
        srv.stop()
        shutil.rmtree(ws, ignore_errors=True)


SANDBOX = 'allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox'


def run_iframe(browser, a):
    """the page inside a 480 px sandboxed iframe of a page from another origin - the way a sidebar
    browser (DeepSeek Harness, Web profile) embeds it"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    print('embedded in a sandboxed 480 px iframe of another origin')
    ws = workspace()
    srv = Server(a.server_dir, ws, a.port)
    html = ('<!doctype html><meta charset="utf-8"><title>host application</title>'
            '<body style="margin:0;background:#1e2430;display:flex;height:100vh">'
            '<div style="flex:1;color:#cfd6e4;font:14px sans-serif;padding:24px">host application'
            ' (http://localhost:%d - another origin)<br><br>the preview runs in the frame on the right</div>'
            '<iframe id="f" src="%s" sandbox="%s" referrerpolicy="no-referrer" '
            'style="width:480px;height:100%%;border:0;border-left:2px solid #55607a"></iframe></body>'
            % (a.port + 1, srv.url, SANDBOX)).encode('utf-8')

    class Host(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
    host = ThreadingHTTPServer(('127.0.0.1', a.port + 1), Host)
    threading.Thread(target=host.serve_forever, daemon=True).start()
    rel = 'job/demo_beam.inp'
    try:
        page = browser.new_page(viewport={'width': 1100, 'height': 820})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto('http://localhost:%d/' % (a.port + 1))
        frame = None
        for _ in range(100):
            frame = next((f for f in page.frames if f.url.startswith(srv.url)), None)
            if frame is not None:
                break
            page.wait_for_timeout(100)
        check(frame is not None, 'the frame navigates to the preview server')
        frame.wait_for_function('window.__preview && window.__preview.model && !window.__preview.busy', timeout=60000)
        page.wait_for_timeout(600)
        info = frame.evaluate("""() => ({w: innerWidth, narrow: document.body.classList.contains('narrow'),
            drawn: window.__preview.drawnFraction(), file: window.__preview.model.file,
            origin: location.origin, parentSame: (() => { try { return !!parent.document; } catch (e) { return false; } })(),
            over: document.documentElement.scrollWidth - document.documentElement.clientWidth})""")
        check(info['w'] == 480 and info['narrow'] and info['over'] <= 0, 'frame is 480 px wide, narrow layout, no horizontal overflow')
        check(info['drawn'] > 0.01, 'WebGL draws inside the sandboxed frame (%.1f%% of the viewport covered)' % (100 * info['drawn']))
        check(not info['parentSame'], 'the host page is another origin (the frame cannot reach it)')
        page.screenshot(path=os.path.join(SHOTS, 'iframe_480_sandboxed.png'))
        # polling keeps working: make the other file the newest one and wait for the page to follow
        other = 'chk_material_poisson.inp' if info['file'] == 'demo_beam.inp' else 'demo_beam.inp'
        os.utime(os.path.join(ws, 'job', other), None)
        frame.wait_for_function('f => window.__preview.model && window.__preview.model.file === f && !window.__preview.busy',
                                arg=other, timeout=20000)
        check(True, 'polling follows the newest file inside the frame (%s)' % other)
        # review from inside the frame (same-origin POST of the framed page)
        frame.evaluate("p => { document.getElementById('followChk').checked = false; return window.__preview.loadModel(p, false); }", rel)
        frame.wait_for_function('p => !window.__preview.busy && window.__preview.path === p', arg=rel, timeout=30000)
        frame.click('#treeBtn')
        frame.locator('#tree .leaf', has_text='Steel').first.click()
        frame.locator('#prop tr', has_text='*Density').first.locator('.ann').click()
        frame.click('#treeBtn')
        frame.locator('#rvList input').first.fill('改为 7.85')
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, 'iframe_480_review_panel.png'))
        frame.click('#rvCopy')
        frame.wait_for_function('window.__preview.lastCopied !== null')
        if frame.locator('#modal.open').count():
            frame.click('#mdOk')
        frame.click('#rvReject')
        frame.wait_for_function("document.getElementById('rvState').textContent === '已退回'", timeout=10000)
        rj = os.path.join(ws, '.abq', 'review.json')
        check(os.path.isfile(rj) and json.load(open(rj, encoding='utf-8'))['files'][rel]['status'] == 'rejected',
              'a decision made inside the frame is written to review.json')
        page.screenshot(path=os.path.join(SHOTS, 'iframe_480_rejected.png'))
        check(not errors, 'no script errors %s' % (errors[:3] if errors else ''))
        page.close()
    finally:
        host.shutdown()
        host.server_close()
        srv.stop()
        shutil.rmtree(ws, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--only', default='')
    ap.add_argument('--wait-minutes', type=float, default=30, help='how long to wait for free memory')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(SHOTS, exist_ok=True)
    parts = [('shots', run_shots), ('review', run_review), ('narrow', run_narrow), ('large', run_large),
             ('iframe', run_iframe)]
    # one heavy process at a time and only with memory to spare: wait for the slot (see heavy_gate.py)
    with heavy_gate.slot('headless browser (ui_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True,
                                    args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        try:
            for name, fn in parts:
                if a.only and a.only != name:
                    continue
                try:
                    fn(browser, a)
                except Exception as e:
                    check(False, '%s aborted: %s: %s' % (name, type(e).__name__, str(e)[:300]))
        finally:
            browser.close()
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED:\n  %s' % (len(FAILS), '\n  '.join(FAILS))))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
