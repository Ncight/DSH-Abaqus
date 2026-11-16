# -*- coding: utf-8 -*-
"""The CDB reader (mapdl_model.py) on CDB files written by MAPDL 2025 R1, and what it refuses.

    py test_mapdl_model.py [--module-dir DIR] [--port 8771]

tests/data/ks_build.cdb is the "kitchen sink" of step 0 of the ANSYS plan (tests/data/ks_build.mac wrote it):
bonded and frictional contact, SOLID185 bricks and degenerate tetrahedra, SOLID187, SHELL181, BEAM188, MASS21,
COMBIN14, CE, CP, a table force, a node with rotated axes, pressures, temperatures, gravity.  The expected
numbers below were checked against MAPDL itself (dsh-ansys step 2: CDREAD + NWRITE / EWRITE, the reaction
of a fully held model, IRLF mass per element type): mesh identical, load resultant (-40000, -1000, 0) N,
mass 4631.87 kg.  tests/data/cant.cdb is a 10-element BEAM188 cantilever.
Then tampered copies (a /SYS line, an abbreviated SOLVE, a "$", a command hidden in a data block, an *IF, a
parameter assignment, a *CREATE macro, the unblocked format) must each be reported with its line, and the
preview server must list and serve a CDB as mapdl.  Exit code 1 on failure.
"""
import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def tampered(src, dst, edit):
    with open(src, encoding='latin-1') as f:
        lines = f.read().split('\n')
    lines = edit(lines)
    with open(dst, 'w', encoding='latin-1', newline='\n') as f:
        f.write('\n'.join(lines))
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8771)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, os.path.abspath(a.module_dir))
    import mapdl_model

    print('kitchen sink (step 0)')
    t0 = time.time()
    m = mapdl_model.Model(os.path.join(DATA, 'ks_build.cdb'))
    v = json.loads(m.view_json({'path': 'ks_build.cdb'}).decode('utf-8'))
    st = v['stats']
    check(v['solver'] == 'mapdl' and st['nodes'] == 2684 and st['elements'] == 2404,
          'solver mapdl, 2684 nodes, 2404 elements (%.2f s)' % (time.time() - t0))
    check(st['types'] == {'SOLID185': 1138, 'SOLID187': 804, 'CONTA174': 200, 'TARGE170': 200, 'SHELL181': 50,
                          'BEAM188': 10, 'MASS21': 1, 'COMBIN14': 1}, 'element types: %s' % st['types'])
    check(st['shapes'].get('SOLID185 tet') == 538 and st['shapes'].get('SOLID185 hex') == 600,
          'SOLID185 drawn as 600 bricks and 538 tetrahedra (degenerate K=L, M=N=O=P): %s' % st['shapes'])
    check(v['cdb']['violations'] == [] and not [c for c in v['checks'] if c['level'] in ('error', 'warn')],
          'no whitelist finding, no error or warning: %s' % [c['msg'] for c in v['checks'] if c['level'] != 'info'][:3])
    pairs = {c['real']: c for c in v['cdb']['contacts']}
    check(set(pairs) == {5, 6} and pairs[5]['bonded'] and not pairs[6]['bonded'] and pairs[6]['mu'] == 0.2,
          'contact pairs by real constant: 5 bonded, 6 standard with mu 0.2')
    check(len(v['ties']) == 1 and len(v['contactPairs']) == 2, 'the bonded pair is also listed as a tie')
    kinds = sorted(c['kw'] for c in v['constraints'])
    check(kinds == ['CE', 'CP'], 'one constraint equation and one coupled set: %s' % kinds)
    ns = {s['name']: s['n'] for s in v['nsets']}
    es = {s['name']: s['n'] for s in v['elsets']}
    check(ns.get('BEAM_TIP') == 1 and ns.get('V2_N') == 363 and es.get('V5_E') == 538 and es.get('V1_HOT') == 30,
          'components: %s / %s' % (ns, es))
    loads = v['steps'][0]['loads']
    by = {}
    for l in loads:
        by.setdefault(l['kw'], []).append(l)
    check([l['rows'][0][0] for l in by['F']] == ['BEAM_TIP', '2618'] and by['F'][0].get('table') == 'FTAB',
          'forces: the table force on component BEAM_TIP, -500 on node 2618')
    pres = sorted(l['value'] for l in by['SFE'])
    check(pres == [1000.0, 200000.0] and sorted(l['n'] for l in by['SFE']) == [20, 50], 'pressures 1000 (50 faces) and 200000 (20 faces)')
    check(by['BF'][0]['rows'][0][:3] == ['V2_N', 'TEMP', '50'] and by['BFE'][0]['rows'][0][:3] == ['V1_HOT', 'TEMP', '30'],
          'temperatures on V2_N (BF) and V1_HOT (BFE)')
    check(by['ACEL'][0]['vector'] == [0.0, 0.0, 9.81], 'ACEL 9.81 in Z')
    bcs = v['steps'][0]['bcs']
    check(any(b.get('rotatedNodes') == 1 and b['labels'] == ['UX'] for b in bcs) and st['rotatedNodes'] == 1,
          'the UX constraint on the rotated node 2678 is marked as rotated')
    check(not any(set(b['labels']) & {'ROTX', 'ROTY', 'ROTZ'} and b['n'] > 7 for b in bcs),
          'D,ALL on solid nodes does not show rotation constraints')
    lr = m.load_resultant()
    check(max(abs(x - y) for x, y in zip(lr['force'], (-40000.0, -1000.0, 0.0))) < 1e-6 and lr['complete'],
          'load resultant %s (MAPDL: -40000, -1000, 0; table FTAB evaluated at TIME 1)' % [round(x, 6) for x in lr['force']])
    check(abs(st['mass'] - 4631.87) < 0.01 and st['massComplete'], 'mass %.6g (MAPDL IRLF: 4631.87)' % st['mass'])
    sy = m.symbols(0)
    check(len(sy['press']) > 0 and len(sy['cload']) > 0 and len(sy['grav']) == 1 and len(sy['bc']) == 3,
          'symbols: pressures, forces, gravity and 3 BC groups')
    hl = m.highlight('surface', '_CONTA_R5')
    check(hl['n'] == 100 and len(hl['tris']) > 0, 'the contact side of pair 5 can be highlighted (%d faces)' % hl['n'])

    print('cantilever')
    c = mapdl_model.Model(os.path.join(DATA, 'cant.cdb'))
    cv = json.loads(c.view_json({'path': 'cant.cdb'}).decode('utf-8'))
    check(cv['stats']['types'] == {'BEAM188': 10} and abs(cv['stats']['mass'] - 78.5) < 1e-9,
          'BEAM188 x 10, mass 7850 x 0.01 x 1 = 78.5')

    print('two bricks that touch without a contact pair (tests/data/two_blocks.mac)')
    tb = mapdl_model.Model(os.path.join(DATA, 'two_blocks.cdb'))
    msgs = [c['msg'] for c in tb.checks() if c['level'] == 'warn']
    check(any('外表面正对着' in x and '接触对的两侧' in x for x in msgs), 'the untied interface is reported')
    check(any('刚体位移' in x and 'UX、UY、UZ' in x for x in msgs), 'the unrestrained upper brick is reported')

    print('what the reader refuses (tampered copies of cant.cdb)')
    tmp = tempfile.mkdtemp(prefix='mapdl_model_test_')
    try:
        src = os.path.join(DATA, 'cant.cdb')

        def before_go(extra):
            def edit(lines):
                k = max(i for i, l in enumerate(lines) if l.strip().upper() == '/GO')
                return lines[:k] + [extra] + lines[k:]
            return edit
        cases = [('sys.cdb', before_go('/SYS,calc.exe'), '命令 /SYS 不在 CDB 白名单内'),
                 ('solv.cdb', before_go('SOLVXYZ'), '命令 SOLVXYZ 不在 CDB 白名单内'),
                 ('dollar.cdb', before_go('NUMOFF,NODE,1 $ /SYS,calc'), '一行里有 $'),
                 ('if.cdb', before_go('*IF,X,EQ,1,THEN'), '*IF 只允许'),
                 ('assign.cdb', before_go('ABC=1'), '参数赋值不在白名单内'),
                 ('create.cdb', before_go('*CREATE,evil,mac'), '*CREATE 只允许写初始状态文件'),
                 ('use.cdb', before_go('*USE,evil'), '命令 *USE 不在 CDB 白名单内')]
        for name, edit, want in cases:
            p = tampered(src, os.path.join(tmp, name), edit)
            mm = mapdl_model.Model(p)
            hits = [x for x in mm.violations if want in x[1]]
            check(bool(hits), '%-11s -> line %s: %s' % (name, hits[0][0] if hits else '?', hits[0][1][:60] if hits else mm.violations[:2]))

        def in_nblock(lines):
            k = next(i for i, l in enumerate(lines) if l.startswith('NBLOCK'))
            return lines[:k + 3] + ['/SYS,calc.exe'] + lines[k + 3:]
        mm = mapdl_model.Model(tampered(src, os.path.join(tmp, 'block.cdb'), in_nblock))
        check(any('NBLOCK 的数据行不符合格式' in x[1] for x in mm.violations), 'a command inside the NBLOCK data is reported')
        mm = mapdl_model.Model(os.path.join(DATA, 'cant.cdb'))
        check(mm.violations == [], 'the untouched file has no finding')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('preview server')
    ws = tempfile.mkdtemp(prefix='mapdl_ws_')
    shutil.copy2(os.path.join(DATA, 'ks_build.cdb'), os.path.join(ws, 'ks_build.cdb'))
    p = subprocess.Popen([sys.executable, os.path.join(a.module_dir, 'preview_server.py'), '--root', ws, '--port',
                          str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def get(path):
            for _ in range(100):
                try:
                    cn = http.client.HTTPConnection('127.0.0.1', a.port, timeout=60)
                    cn.request('GET', path, headers={'Host': '127.0.0.1:%d' % a.port})
                    r = cn.getresponse()
                    return r.status, json.loads(r.read().decode('utf-8'))
                except (ConnectionError, OSError):
                    time.sleep(0.1)
            return None, None
        st_, d = get('/api/list')
        check(st_ == 200 and d['files'] and d['files'][0].get('solver') == 'mapdl', 'listed with solver mapdl: %s' % (d or {}).get('files'))
        st_, d = get('/api/model?path=ks_build.cdb')
        check(st_ == 200 and d.get('solver') == 'mapdl' and d['stats']['elements'] == 2404 and len(d['geom']['tris']) > 0,
              'served by mapdl_model with geometry')
        st_, d = get('/api/symbols?path=ks_build.cdb&step=0')
        check(st_ == 200 and d['press'] and d['grav'], 'symbols of the load state')
        st_, d = get('/api/review?path=ks_build.cdb')
        check(st_ == 200 and d['state'] == 'none' and d['parts']['solve'] is None, 'review state: no decision, no solve segment yet')
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        shutil.rmtree(ws, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
