# -*- coding: utf-8 -*-
"""Shows the preview page inside the right-sidebar Browser of DeepSeek Harness (Web profile).

    py dsh_sidebar_check.py [--server-dir DIR] [--dsh-home DIR] [--dsh-port 3081] [--preview-port 8766]
                            [--explore]

What it does, once:
  1. makes sure an ISOLATED DSH home exists (never the user's ~/.dsh) and that its web profile
     enables the shipped `ui-sidebar-browser` entry (profile patch, as the package README describes);
  2. starts the preview server and `dsh web --no-open` on spare loopback ports;
  3. opens the DSH web UI in headless Edge, expands the right sidebar, opens a Browser tab on the
     preview URL and checks that the page inside the sandboxed iframe has loaded a model, draws with
     WebGL and keeps polling;
  4. saves screenshots to tests/shots/dsh_*.png and stops everything it started (by process id).

--explore only dumps what the DSH page offers (buttons, labels, screenshots) to help locate controls.
Waits (heavy_gate.py) until no other heavy test step runs and MIN_FREE_GB of commit memory is free.
"""
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_gate  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SHOTS = os.path.join(HERE, 'shots')
DSH = r'D:\DSH\resources\runtime\cli\bin\dsh.cmd'
MIN_FREE_GB = 4.0
PATCH = """# Isolated DSH home used only to test the Abaqus preview page in the right-sidebar Browser.
# The Browser entry ships disabled in Web profiles; enable it (see dsh-client-ui-sidebar-browser README).
- id: ui-sidebar-browser
  disabled: false
"""


def free_commit_gb():
    class MS(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    st = MS()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPageFile / 2.0 ** 30


def kill_tree(p):
    """stop a process we started together with its children (by pid, never by name)"""
    if p.poll() is None:
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def start_preview(server_dir, port):
    root = os.path.join(ROOT, 'examples', 'corpus', 'cae')
    p = subprocess.Popen([sys.executable, os.path.join(server_dir, 'preview_server.py'), '--root', root,
                          '--port', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/api/health' % port, timeout=2).read()
            return p
        except OSError:
            time.sleep(0.1)
    kill_tree(p)
    raise SystemExit('preview server did not start')


def start_dsh(home, port, log):
    env = dict(os.environ, DSH_HOME=home)
    env.pop('ELECTRON_RUN_AS_NODE', None)
    p = subprocess.Popen(['cmd', '/c', DSH, '--profile', 'web', '--no-open', '--port', str(port)], env=env,
                         cwd=home, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    found = {}

    def pump():
        for raw in p.stdout:
            line = raw.decode('utf-8', 'replace').rstrip()
            log.append(line)
            m = re.search(r'dsh web:\s*(\S+)', line)
            if m:
                found['url'] = m.group(1)
    threading.Thread(target=pump, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < 120 and 'url' not in found and p.poll() is None:
        time.sleep(0.2)
    return p, found.get('url')


def dump_ui(page, tag):
    """what can be clicked on the page right now"""
    items = page.evaluate("""() => [...document.querySelectorAll('button, [role=button], [role=tab], a, input, [aria-label]')]
        .filter(e => e.offsetParent !== null).slice(0, 400)
        .map(e => ({tag: e.tagName, text: (e.innerText || '').trim().slice(0, 40), aria: e.getAttribute('aria-label'),
                    title: e.getAttribute('title'), testid: e.getAttribute('data-testid'), cls: (e.className || '').toString().slice(0, 60),
                    x: Math.round(e.getBoundingClientRect().x), y: Math.round(e.getBoundingClientRect().y),
                    w: Math.round(e.getBoundingClientRect().width)}))""")
    with open(os.path.join(SHOTS, 'dsh_ui_%s.json' % tag), 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    page.screenshot(path=os.path.join(SHOTS, 'dsh_ui_%s.png' % tag))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--dsh-home', default=os.path.join(ROOT, '.dsh-eval', 'home-web'))
    ap.add_argument('--dsh-port', type=int, default=3081)
    ap.add_argument('--preview-port', type=int, default=8766)
    ap.add_argument('--explore', action='store_true')
    ap.add_argument('--wait-minutes', type=float, default=30, help='how long to wait for free memory')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    home = os.path.abspath(a.dsh_home)
    real = os.path.abspath(os.path.join(os.path.expanduser('~'), '.dsh'))
    if home.lower() == real.lower() or home.lower().startswith(real.lower() + os.sep):
        raise SystemExit('refusing to use the real DSH home')
    os.makedirs(SHOTS, exist_ok=True)
    # dsh web + one browser + the small preview server form ONE heavy step: nothing else may run meanwhile
    with heavy_gate.slot('dsh web + headless browser', MIN_FREE_GB, a.wait_minutes):
        return run(a, home)


def run(a, home):
    patch = os.path.join(home, 'profiles', 'web', 'cordis.patch.yml')
    os.makedirs(os.path.dirname(patch), exist_ok=True)
    if not os.path.isfile(patch) or 'ui-sidebar-browser' not in open(patch, encoding='utf-8').read():
        with open(patch, 'a', encoding='utf-8', newline='\n') as f:
            f.write(PATCH)
        print('profile patch written:', patch)
    preview = start_preview(a.server_dir, a.preview_port)
    preview_url = 'http://127.0.0.1:%d/' % a.preview_port
    log = []
    dsh = None
    ok = False
    try:
        dsh, url = start_dsh(home, a.dsh_port, log)
        if not url:
            print('dsh web did not announce a URL; output:\n  ' + '\n  '.join(log[-25:]))
            return 1
        print('dsh web is up:', re.sub(r'token=[^&]+', 'token=***', url))
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True,
                                        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
            try:
                page = browser.new_page(viewport={'width': 1500, 'height': 900})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)[:200]))
                page.goto(url, wait_until='domcontentloaded')
                page.wait_for_timeout(6000)
                items = dump_ui(page, '1_start')
                print('start page: %d visible controls; title %r' % (len(items), page.title()))
                if a.explore:
                    for it in items[:120]:
                        print('   ', json.dumps(it, ensure_ascii=False))
                try:
                    ok = drive(page, preview_url, a)
                except Exception as e:      # keep what the page looked like, then clean up as usual
                    print('driving the DSH page failed: %s' % str(e).splitlines()[0][:300])
                    try:
                        items = dump_ui(page, '9_error')
                        for it in items[:150]:
                            print('   ', json.dumps(it, ensure_ascii=False))
                    except Exception:
                        pass
                if errors:
                    print('page errors:', errors[:5])
            finally:
                browser.close()
    finally:
        if dsh is not None:
            kill_tree(dsh)
        kill_tree(preview)
        with open(os.path.join(SHOTS, 'dsh_web_output.log'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(re.sub(r'token=[^&\s]+', 'token=***', l) for l in log))
    print('RESULT:', 'preview renders inside the DSH sidebar Browser' if ok else 'NOT verified')
    return 0 if ok else 1


def first_visible(page, selectors):
    for s in selectors:
        loc = page.locator(s)
        try:
            n = loc.count()
        except Exception:
            n = 0
        for i in range(min(n, 5)):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    return None


def drive(page, preview_url, a):
    """expand the right sidebar, open a Browser tab on the preview URL, check the page in the iframe"""
    ADDR = ['input[type=url]', 'input[placeholder*="http" i]', 'input[aria-label*="address" i]',
            'input[aria-label*="地址"]', 'input[placeholder*="URL" i]', 'input[placeholder*="网址"]',
            'input[aria-label*="URL" i]', 'input[placeholder*="地址"]']
    BROWSER = ['button:has-text("Browser")', 'button:has-text("浏览器")', '[role=button]:has-text("Browser")',
               '[role=button]:has-text("浏览器")', '[role=tab]:has-text("浏览器")', '[role=menuitem]:has-text("浏览器")',
               '[role=menuitem]:has-text("Browser")', '[aria-label*="浏览器"]', '[aria-label*="Browser"]',
               '[title*="浏览器"]', '[title*="Browser"]']
    SIDEBAR = ['button[aria-label*="idebar" i][aria-label*="right" i]', 'button[aria-label*="右侧"]',
               'button[aria-label*="侧栏"]', 'button[aria-label*="侧边栏"]', 'button[title*="idebar" i]',
               'button[title*="侧边栏"]', 'button[title*="侧栏"]', 'button[aria-label*="panel" i]']
    addr = None
    # 0. a fresh DSH home greets with two dialogs: "预览版说明" (继续) and "开始使用 - 登录 / 添加 API Key"
    #    (closed with its 关闭 button: no account is needed to look at the layout); nothing can be clicked
    #    behind them
    for _ in range(6):
        d = first_visible(page, ['[role=dialog] button:has-text("继续")', 'button:has-text("继续")',
                                 '[role=dialog] button:has-text("知道了")', '[role=dialog] button:has-text("跳过")',
                                 '[role=dialog] button:has-text("Continue")', '[role=dialog] button[aria-label="关闭"]',
                                 '[class*="dialog"] button[aria-label="关闭"]', 'button[aria-label="关闭"]',
                                 '[role=dialog] button[aria-label="Close"]'])
        if d is None:
            break
        d.click(timeout=5000)
        page.wait_for_timeout(900)
    dump_ui(page, '2_dialogs_closed')
    # 1. through the sidebar guide: expand the right sidebar, pick 浏览器 / Browser (a new tab if need be)
    b = first_visible(page, BROWSER)
    if b is None:
        for sel in SIDEBAR:
            btn = first_visible(page, [sel])
            if btn is None:
                continue
            btn.click(timeout=5000)
            page.wait_for_timeout(1500)
            b = first_visible(page, BROWSER)
            if b is not None:
                break
        dump_ui(page, '2_sidebar')
    if b is None:
        plus = first_visible(page, ['button[aria-label="新标签页"]', 'button[aria-label*="New tab" i]'])
        if plus is not None:
            plus.click(timeout=5000)
            page.wait_for_timeout(1200)
            dump_ui(page, '2_new_tab')
            b = first_visible(page, BROWSER)
    if b is None:
        # 2. the command `browser.new` (Ctrl+T by default) opens a Browser page in the docked pane
        page.keyboard.press('Control+t')
        page.wait_for_timeout(1500)
        dump_ui(page, '2_after_ctrl_t')
        addr = first_visible(page, ADDR)
    if addr is None:
        if b is None:
            print('found neither the Browser command nor a Browser entry in the sidebar guide '
                  '(is the profile patch applied? does the start page need a login?); see shots/dsh_ui_*.json')
            return False
        b.click(timeout=8000)
        page.wait_for_timeout(1500)
        items = dump_ui(page, '3_browser_tab')
        addr = first_visible(page, ADDR)
        if addr is None and a.explore:
            for it in items[:150]:
                print('   ', json.dumps(it, ensure_ascii=False))
    if addr is None:
        print('no address field in the Browser tab; see shots/dsh_ui_3_browser_tab.json')
        return False
    addr.fill(preview_url)
    addr.press('Enter')
    # 4. the page inside the iframe
    frame = None
    for _ in range(100):
        frame = next((f for f in page.frames if f.url.startswith(preview_url)), None)
        if frame is not None:
            break
        page.wait_for_timeout(200)
    if frame is None:
        dump_ui(page, '4_no_frame')
        print('the Browser tab did not create a frame for', preview_url)
        return False
    frame.wait_for_function('window.__preview && window.__preview.model && !window.__preview.busy', timeout=60000)
    page.wait_for_timeout(1500)
    info = frame.evaluate("""() => { const p = window.__preview, m = p.model; return {
        file: m.file, nodes: m.stats.nodes, elements: m.stats.elements, width: innerWidth, height: innerHeight,
        narrow: document.body.classList.contains('narrow'), drawn: p.drawnFraction(),
        webgl: !!p.renderer.getContext(), review: document.getElementById('rvState').textContent,
        log: document.getElementById('msg').innerText.split('\\n').slice(-3) }; }""")
    el = page.locator('iframe').first
    sandbox = el.get_attribute('sandbox') if el.count() else None
    print('inside the sidebar frame: %s' % json.dumps(info, ensure_ascii=False))
    print('iframe sandbox attribute: %r' % sandbox)
    page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_preview.png'))
    # polling: the page follows a newer file by itself - touch another INP and wait for the switch
    before = info['file']
    other = os.path.join(ROOT, 'examples', 'corpus', 'cae', 'frame_b31_t3d2.inp')
    if before == 'frame_b31_t3d2.inp':
        other = os.path.join(ROOT, 'examples', 'corpus', 'cae', 'shell_roof_s4r_s3.inp')
    st = os.stat(other)
    polled = False
    try:
        os.utime(other, None)
        frame.wait_for_function('f => window.__preview.model && window.__preview.model.file === f',
                                arg=os.path.basename(other), timeout=15000)
        polled = True
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_preview_followed.png'))
    except Exception as e:
        print('polling check failed:', str(e)[:200])
    finally:
        os.utime(other, (st.st_atime, st.st_mtime))
    # a model tree click inside the frame (interaction through the sandbox)
    try:
        if frame.evaluate("() => document.body.classList.contains('collapsed')"):
            frame.click('#treeBtn')
        frame.locator('#tree .leaf:visible').first.click(timeout=8000)      # leaves of folded branches are hidden
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SHOTS, 'dsh_sidebar_preview_tree.png'))
    except Exception as e:
        print('tree click failed:', str(e)[:200])
    print('checks: model loaded=%s  webgl drawn=%.1f%%  polling follows new file=%s  frame %dx%d px narrow=%s' % (
        bool(info['nodes']), 100 * info['drawn'], polled, info['width'], info['height'], info['narrow']))
    return bool(info['nodes']) and info['drawn'] > 0.003 and polled


if __name__ == '__main__':
    sys.exit(main())
