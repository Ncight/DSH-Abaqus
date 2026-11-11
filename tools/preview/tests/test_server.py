# -*- coding: utf-8 -*-
"""API test of the preview server: model endpoint, size guard, review workflow and its security rules.

    py test_server.py [--server-dir DIR] [--port 8766]

Starts its own server on a throw-away workspace (a temp folder with copies of two small INPs), so the
real <root>/.abq/review.json is never touched, runs the checks with urllib (no browser) and stops the
server again.  Exit code 1 on failure.
"""
import argparse
import gzip
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
SRC = [os.path.join(ROOT, 'examples', 'demo_beam', 'demo_beam.inp'),
       os.path.join(ROOT, 'examples', 'corpus', 'checks', 'chk_material_poisson.inp')]
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


class Client(object):
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        h = {'Host': host or '127.0.0.1:%d' % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        raw = r.read()
        if r.getheader('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        c.close()
        try:
            data = json.loads(raw.decode('utf-8'))
        except ValueError:
            data = raw
        return r.status, data, r

    def get(self, path, **kw):
        return self.request('GET', path, **kw)

    def post(self, path, obj, ctype='application/json', headers=None, raw=None, **kw):
        h = {'Content-Type': ctype} if ctype else {}
        h.update(headers or {})
        body = raw if raw is not None else json.dumps(obj).encode('utf-8')
        return self.request('POST', path, body=body, headers=h, **kw)


def start(server_dir, root, port, extra=()):
    p = subprocess.Popen([sys.executable, os.path.join(server_dir, 'preview_server.py'), '--root', root,
                          '--port', str(port)] + list(extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = Client(port)
    for _ in range(50):
        try:
            if c.get('/api/health')[0] == 200:
                return p, c
        except OSError:
            time.sleep(0.1)
    p.terminate()
    raise SystemExit('server did not start: %s' % p.stdout.read().decode('utf-8', 'replace'))


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8766)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ws = tempfile.mkdtemp(prefix='abq_preview_ws_')
    os.makedirs(os.path.join(ws, 'job'))
    for s in SRC:
        shutil.copy2(s, os.path.join(ws, 'job'))
    rel = 'job/demo_beam.inp'
    inp = os.path.join(ws, 'job', 'demo_beam.inp')
    p, c = start(a.server_dir, ws, a.port)
    try:
        print('model endpoints')
        st, d, _ = c.get('/api/list')
        check(st == 200 and {f['path'] for f in d['files']} == {rel, 'job/chk_material_poisson.inp'}, 'list shows the two INPs')
        st, d, r = c.get('/api/model?path=' + rel, headers={'Accept-Encoding': 'gzip'})
        check(st == 200 and r.getheader('Content-Encoding') == 'gzip' and d['stats']['elements'] == 1472,
              'model as gzip JSON (%d elements)' % (d['stats']['elements'] if st == 200 else -1))
        check(len(d['geom']['tris']) > 0 and d['path'] == rel and 'parseSeconds' in d, 'geometry and file info present')
        st, d2, r = c.get('/api/model?path=' + rel)
        check(st == 200 and r.getheader('Content-Encoding') is None and d2['stats'] == d['stats'], 'same model without gzip')
        st, h, _ = c.get('/api/highlight?path=%s&kind=surface&name=PadBottom' % rel)
        check(st == 200 and h['n'] > 0 and len(h['tris']) > 0, 'highlight of a surface')
        st, s, _ = c.get('/api/symbols?path=%s&step=0' % rel)
        check(st == 200 and s['bc'] and s['press'], 'symbols of step 1')
        st, d, _ = c.get('/api/model?path=job/chk_material_poisson.inp')
        st, hl, _ = c.get('/api/health')
        check(len(hl['cached']) == 1 and hl['cached'][0] == 'chk_material_poisson.inp', 'only one parsed model is kept: %s' % hl['cached'])
        st, d, _ = c.get('/api/model?path=../outside.inp')
        check(st == 500 and 'error' in d, 'path outside the workspace is refused')
        st, d, _ = c.get('/api/list', host='evil.example:%d' % a.port)
        check(st == 403, 'foreign Host header is refused (DNS rebinding)')

        print('review: read')
        st, d, _ = c.get('/api/review?path=' + rel)
        check(st == 200 and d['state'] == 'none' and d['entry'] is None and d['current'] is False and d['sha256'] == sha(inp),
              'no decision yet')

        print('review: what must be refused')
        good = {'path': rel, 'status': 'approved', 'note': '', 'comments': []}
        check(c.post('/api/review', good, ctype='text/plain')[0] == 415, 'Content-Type text/plain -> 415')
        check(c.post('/api/review', good, ctype='application/x-www-form-urlencoded')[0] == 415, 'form encoding -> 415')
        check(c.post('/api/review', good, ctype=None)[0] == 415, 'no Content-Type -> 415')
        check(c.post('/api/review', good, headers={'Origin': 'http://evil.example'})[0] == 403, 'foreign Origin -> 403')
        check(c.post('/api/review', good, headers={'Origin': 'http://127.0.0.1:%d' % (a.port + 1)})[0] == 403,
              'another local port as Origin -> 403')
        check(c.post('/api/review', good, headers={'Origin': 'null'})[0] == 403, 'Origin null (sandboxed frame) -> 403')
        check(c.post('/api/review', good, host='evil.example')[0] == 403, 'foreign Host -> 403')
        check(c.post('/api/review', dict(good, status='maybe'))[0] == 400, 'unknown status -> 400')
        check(c.post('/api/review', dict(good, path='../../x.inp'))[0] == 400, 'path outside the workspace -> 400')
        check(c.post('/api/review', dict(good, path='job/nothing.inp'))[0] == 400, 'missing file -> 400')
        check(c.post('/api/review', None, raw=b'{not json')[0] == 400, 'broken JSON -> 400')
        check(c.post('/api/review', dict(good, note='x' * 300000))[0] == 413, 'oversized body -> 413')
        check(not os.path.exists(os.path.join(ws, '.abq')), 'nothing was written by the refused requests')

        print('review: dry run, approve')
        st, d, _ = c.post('/api/review', dict(good, status='rejected', note='只是预览', dryRun=True))
        check(st == 200 and d.get('dryRun') and '退回修改' in d['markdown'] and not os.path.exists(os.path.join(ws, '.abq')),
              'dryRun returns the text and writes nothing')
        st, d, _ = c.post('/api/review', good, headers={'Origin': 'http://127.0.0.1:%d' % a.port})
        check(st == 200 and d['ok'] and d['state'] == 'approved', 'approve with our own Origin -> 200')
        rj = os.path.join(ws, '.abq', 'review.json')
        md = os.path.join(ws, '.abq', 'review', 'demo_beam.md')
        check(os.path.isfile(rj) and os.path.isfile(md), 'review.json and review/demo_beam.md exist')
        data = json.load(open(rj, encoding='utf-8'))
        e = data['files'].get(rel, {})
        check(data.get('version') == 1 and set(data) == {'version', 'files'}, 'review.json: {version: 1, files: {...}}')
        check(set(e) == {'sha256', 'status', 'decidedAt', 'note', 'comments'}, 'entry has exactly the agreed keys: %s' % sorted(e))
        check(e.get('sha256') == sha(inp) and e.get('status') == 'approved' and e.get('comments') == [], 'sha256 / status / comments')
        check(len(e.get('decidedAt', '')) == 19 and e['decidedAt'][10] == 'T', 'decidedAt is a local ISO time: %s' % e.get('decidedAt'))
        text = open(md, encoding='utf-8').read()
        check('审查意见单' in text and rel in text and '批准求解' in text and e['sha256'] in text, 'markdown names file, decision and fingerprint')
        check(not [f for f in os.listdir(os.path.join(ws, '.abq')) if f.endswith('.tmp')], 'no temporary file left behind')
        st, d, _ = c.get('/api/review?path=' + rel)
        check(d['state'] == 'approved' and d['current'] is True, 'state reads back as approved / current')

        print('review: the approval lapses when the file changes')
        with open(inp, 'ab') as f:
            f.write(b'** touched by the test\n')
        st, d, _ = c.get('/api/review?path=' + rel)
        check(d['state'] == 'stale' and d['current'] is False and d['entry']['status'] == 'approved' and d['sha256'] == sha(inp),
              'state is stale, stored sha no longer matches')

        print('review: reject with comments')
        comments = [{'target': '材料 Steel / *Density', 'current': '7850', 'note': '应为 7.85 t/m³'},
                    {'target': '载荷 *DSLOAD: PadTop, P, 500.0000 / 数据 1', 'current': 'PadTop, P, 500.0000', 'note': '压强改为 50'}]
        st, d, _ = c.post('/api/review', {'path': rel, 'status': 'rejected', 'note': '密度单位错了\n第二行', 'comments': comments})
        check(st == 200 and d['state'] == 'rejected', 'reject -> 200')
        e = json.load(open(rj, encoding='utf-8'))['files'][rel]
        check(e['status'] == 'rejected' and e['comments'] == comments and e['note'] == '密度单位错了\n第二行' and e['sha256'] == sha(inp),
              'entry carries the comments, the note and the new fingerprint')
        text = open(md, encoding='utf-8').read()
        check(all(s in text for s in ('退回修改', '材料 Steel / *Density', '当前值：7850', '要求改为：应为 7.85 t/m³', '密度单位错了')),
              'markdown lists target / current value / requested change and the note')
        check(text == d['markdown'], 'the response carries the same text as the .md file')
        st, d, _ = c.post('/api/review', {'path': 'job/chk_material_poisson.inp', 'status': 'rejected', 'note': 'x', 'comments': []})
        files = json.load(open(rj, encoding='utf-8'))['files']
        check(set(files) == {rel, 'job/chk_material_poisson.inp'}, 'a second file is added without losing the first')
        check('体检' in d['markdown'] and '泊松比' in d['markdown'], 'a rejection quotes the 体检 errors of the loaded model')
    finally:
        stop(p)

    print('size guard (server restarted with --max-auto-mb 0.05)')
    p, c = start(a.server_dir, ws, a.port, ['--max-auto-mb', '0.05'])
    try:
        st, d, _ = c.get('/api/model?path=' + rel)
        check(st == 200 and d.get('tooLarge') is True and d['limitMB'] == 0.05 and d['sizeMB'] > 0.1 and d['estimatedMemoryGB'] > 0,
              'file above the limit is not parsed: %s' % {k: d.get(k) for k in ('sizeMB', 'limitMB', 'estimatedMemoryGB', 'availableCommitGB', 'canForce')})
        check(c.get('/api/health')[1]['cached'] == [], 'nothing was parsed')
        st, d2, _ = c.get('/api/model?path=%s&force=1' % rel)
        check(d2.get('tooLarge') is True and not d2.get('refused'), 'force=1 without the page header is ignored')
        st, d2, _ = c.get('/api/model?path=%s&force=1' % rel, headers={'X-Abq-Preview': '1', 'Origin': 'http://evil.example'})
        check(d2.get('tooLarge') is True and not d2.get('refused'), 'force=1 from a foreign Origin is ignored')
        st, d3, _ = c.get('/api/model?path=%s&force=1' % rel, headers={'X-Abq-Preview': '1'})
        avail, need = d['availableCommitGB'], d['estimatedMemoryGB'] + d['reserveGB']
        if avail is not None and avail < need:
            check(d3.get('tooLarge') and d3.get('refused') and '拒绝' in d3.get('reason', ''),
                  'explicit request refused, %.1f GB free < %.1f GB needed: %s' % (avail, need, d3.get('reason')))
        else:
            check('stats' in d3, 'explicit request parses the file (%.1f GB free)' % (avail or -1))
        st, s, _ = c.get('/api/symbols?path=%s&step=0' % rel)
        check(('tooLarge' in s) == ('stats' not in d3), 'symbols do not trigger a parse behind the guard')
    finally:
        stop(p)
        shutil.rmtree(ws, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
