# -*- coding: utf-8 -*-
"""Browser test of the preview page with large synthetic models (headless Edge, software rendering).

    py ui_large_check.py [--port 8767] [--sizes solid:300000,solid:1000000,shell:500000]

For every model: time from the request to the finished first picture, share of the viewport that is drawn, JavaScript heap
of the page, time of a redraw after a view change, a screenshot (tests/shots/large_*.png).  The models are written to a
temporary folder outside the workspace and deleted afterwards.  Software rendering is the worst case; a desktop with a
graphics card redraws much faster.  One model at a time, and only with 6 GB of commit memory free.
"""
import argparse
import os
import shutil
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench_large  # noqa: E402
import heavy_gate  # noqa: E402
import ui_check  # noqa: E402

MIN_FREE_GB = 6.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8767)
    ap.add_argument('--sizes', default='solid:300000,solid:1000000,shell:500000')
    ap.add_argument('--wait-minutes', type=float, default=20)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    os.makedirs(ui_check.SHOTS, exist_ok=True)
    ws = tempfile.mkdtemp(prefix='abq_preview_large_')
    rows = []
    try:
        with heavy_gate.slot('headless browser (ui_large_check)', MIN_FREE_GB, a.wait_minutes), sync_playwright() as p:
            srv = ui_check.Server(os.path.abspath(os.path.join(HERE, '..')), ws, a.port, ['--max-auto-mb', '2000'])
            browser = p.chromium.launch(channel='msedge', headless=True,
                                        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                                              '--enable-precise-memory-info'])
            try:
                for spec in a.sizes.split(','):
                    kind, n = spec.split(':')
                    name = 'bench_%s_%s.inp' % (kind, n)
                    path = os.path.join(ws, name)
                    ne = (bench_large.write_solid if kind == 'solid' else bench_large.write_shell)(path, int(n))
                    mb = os.path.getsize(path) / 1048576.0
                    if heavy_gate.free_commit_gb() < MIN_FREE_GB:
                        print('skip %s: only %.1f GB of commit memory free' % (name, heavy_gate.free_commit_gb()))
                        os.remove(path)
                        continue
                    page, errors = ui_check.open_page(browser, srv.url, 1400, 900)
                    t0 = time.time()
                    ok = True
                    try:
                        page.evaluate("p => { document.getElementById('followChk').checked = false; window.__preview.loadModel(p, false); }", name)
                        page.wait_for_function('p => !window.__preview.busy && window.__preview.path === p && window.__preview.model !== null',
                                               arg=name, timeout=600000)
                    except Exception as e:  # noqa
                        ok = False
                        print('  FAIL %s did not finish loading: %s' % (name, str(e)[:200]))
                    t_load = time.time() - t0
                    row = {'model': '%s, %d elements, %.0f MB INP' % (kind, ne, mb), 'load_s': t_load, 'ok': ok}
                    if ok:
                        page.wait_for_timeout(500)
                        st = page.evaluate('() => { const m = window.__preview.model; return {n: m.stats.nodes, e: m.stats.elements}; }')
                        row['frac'] = page.evaluate('() => window.__preview.drawnFraction()')
                        row['heap_mb'] = page.evaluate('() => performance.memory ? performance.memory.usedJSHeapSize / 1048576 : -1')
                        # redraw after a view change: wait for three animation frames
                        row['redraw_s'] = page.evaluate("""async () => {
                            const t = performance.now();
                            window.__preview.setView('top');
                            for (let i = 0; i < 3; i++) await new Promise(r => requestAnimationFrame(r));
                            return (performance.now() - t) / 1000 / 3; }""")
                        page.evaluate("() => window.__preview.setView('iso')")
                        page.wait_for_timeout(800)
                        page.screenshot(path=os.path.join(ui_check.SHOTS, 'large_%s_%s.png' % (kind, n)))
                        row['counts'] = '%d nodes / %d elements shown in the page' % (st['n'], st['e'])
                        row['ok'] = (st['e'] == ne) and row['frac'] > 0.05 and not errors
                        if errors:
                            row['errors'] = errors[:3]
                    rows.append(row)
                    page.close()
                    os.remove(path)
            finally:
                browser.close()
                srv.stop()
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print()
    for r in rows:
        print('%s  %-42s load %5.1f s  drawn %4.0f%%  JS heap %6.0f MB  redraw %5.2f s/frame  %s %s' % (
            'ok  ' if r['ok'] else 'FAIL', r['model'], r['load_s'], 100 * r.get('frac', 0), r.get('heap_mb', -1),
            r.get('redraw_s', -1), r.get('counts', ''), r.get('errors', '')))
    return 0 if rows and all(r['ok'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
