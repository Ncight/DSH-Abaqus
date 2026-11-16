# -*- coding: utf-8 -*-
"""The solve segment of an ANSYS job (solve_segment.py) and its load steps in the preview - ANSYS plan, step 3.

    py test_solve_segment.py [--module-dir DIR] [--port 8773]

Data in tests/data, each CDB written by MAPDL 2025 R1 from the .mac next to it (dsh-ansys step 3):
  cant3d.cdb + cant3d_{static,modal,harmonic,transient,buckle}.solve.dat
      a solid cantilever and one solve segment per analysis type; each ran in MAPDL after CDREAD without an
      error (tip deflection 3.17e-4 m for 100 N, first frequency 40.93 Hz, buckling load factor 2.59e5)
  block_comps.cdb + .solve.dat
      a block with node and element components; nine load steps that use the load commands on components
      (SF, SFDELE, FDELE, BF, BFDELE, DDELE + D, EKILL, EALIVE).  In MAPDL: SF on a node component overwrote
      the face pressures read from the CDB, SFDELE removed them.
  bolt.cdb + .solve.dat
      a bar with a pretension section (SECTYPE ...,PRETENSION) and SLOAD
Checks: the whitelist rules with their line numbers; the load steps (names, analysis types, end times as
MAPDL counts them: 1, 2, 3 when TIME is left out; 5, 6 after TIME,5); the state each step draws; the
checks; the panel server (listed, reloaded when only the solve segment changes, fingerprint parts).
Exit code 1 on failure.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8773)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, os.path.abspath(a.module_dir))
    import mapdl_model
    import solve_segment as ss

    print('whitelist rules')
    nodes, elems, params = ['NTIP', 'NFIX', 'NALL'], ['EOUTER'], ['LOAD', 'P0']

    def viol(text):
        return ss.parse(text, nodes, elems, params, [1])['violations']
    check(viol('/SOLU\nANTYPE,STATIC\nF,NTIP,FZ,-4\nD,NFIX,ALL,0\nOUTRES,ALL,ALL,NTIP\nSOLVE\nFINISH\n') == [],
          'a plain segment passes')
    seg = ss.parse('/SOLU\n solve\nSOLVXYZ\nsolv\n', nodes, elems, params, [1])
    check(seg['violations'] == [] and [s['solve'] for s in seg['steps']] == [2, 3, 4],
          'commands by their first four characters, any case, leading blanks: " solve", SOLVXYZ, solv solve')
    cases = [
        ('F,NTIP,FZ,-4 $ /SYS,calc', '$'),
        ('X=1', '参数赋值'),
        ('F,NTIP,FZ,%LOAD%', '%参数%'),
        ('*DO,I,1,3', '*DO 不允许'),
        ('*SET,X,1', '*SET 不允许'),
        ('/INPUT,evil,mac', '/INPUT 不允许'),
        ('/POST1', '/POST1 不允许'),
        ('/SYS,calc.exe', '/SYS 不允许'),
        ('NSEL,S,LOC,X,0', 'NSEL 不在求解段白名单内'),
        ('CMSEL,S,NTIP', 'CMSEL 不在求解段白名单内'),
        ('SFE,1,1,PRES,,100', 'SFE 不在求解段白名单内'),
        ('D,1,UX,0', '不能写编号'),
        ('F,NTIP,FZ,-100*2', '数字字面量'),
        ('F,NTIP,FZ,LOAD', '和 CDB 里的参数 (*SET) 同名'),
        ('F,NOPE,FZ,-1', '组件 NOPE 在 CDB 里没有'),
        ('EKILL,NTIP', 'EKILL 要单元组件, NTIP 是节点组件'),
        ('D,EOUTER,ALL,0', 'D 要节点组件, EOUTER 是单元组件'),
        ('SLOAD,2,PL01,TINY,FORC,10,1,2', '预紧截面'),
        ('D,NFIX,ALL,0,,5,1', '必须空着'),
        ('OUTRES,ALL,ALL,NOPE', '组件 NOPE 在 CDB 里没有'),
        ('F,,FZ,-1', '没写作用对象'),
        ('TIME,1,2', '参数多于'),
        ('HARFRQ,0,100,,,ARR', '必须空着'),
    ]
    text = '/SOLU\n' + '\n'.join(c for c, _ in cases) + '\nSOLVE\n'
    v = ss.parse(text, nodes, elems, params, [1])['violations']
    by_line = {ln: msg for ln, msg in v}
    for i, (cmd, want) in enumerate(cases):
        ln = i + 2
        check(want in by_line.get(ln, ''), 'line %d %-34s -> %s' % (ln, cmd, by_line.get(ln, '(not reported)')[:70]))
    check(sorted(by_line) == list(range(2, len(cases) + 2)), 'the %d lines are refused, no other line' % len(cases))
    check(viol('F,NTIP,FZ,-4 ! a comment with $ and = in it') == [], '$ and = after ! are only a comment')
    seg = ss.parse('/SOLU\nANTYPE,STATIC\nSOLVE\nEKILL,EOUTER\nSOLVE\n', nodes, elems, params, [1])
    check(ss.needs_nropt_full(seg) == [4], 'EKILL in a linear static analysis without NROPT,FULL first: line 4')
    seg = ss.parse('/SOLU\nANTYPE,STATIC\nNROPT,FULL\nSOLVE\nEKILL,EOUTER\nSOLVE\n', nodes, elems, params, [1])
    check(ss.needs_nropt_full(seg) == [], 'NROPT,FULL before the first SOLVE: accepted')
    seg = ss.parse('/SOLU\nANTYPE,STATIC\nSOLVE\nNROPT,FULL\nEKILL,EOUTER\nSOLVE\n', nodes, elems, params, [1])
    check(ss.needs_nropt_full(seg) == [5], 'NROPT,FULL only after the first SOLVE: still refused (MAPDL error)')

    print('load steps of the four analysis types (cantilever)')
    cant = os.path.join(DATA, 'cant3d.cdb')
    want = {'static': [('Static', '1', ['F']), ('Static', '2', ['F']), ('Static', '3', [])],
            'modal': [('Modal', None, [])],
            'harmonic': [('Harmonic', None, ['F'])],
            'transient': [('Transient', '0.05', ['F']), ('Transient', '0.1', ['FDELE'])],
            'buckle': [('Static', '1', ['F']), ('Buckle', None, [])]}
    for kind, steps in want.items():
        m = mapdl_model.Model(cant, solve_path=os.path.join(DATA, 'cant3d_%s.solve.dat' % kind))
        v = json.loads(m.view_json({'path': 'cant3d.cdb'}).decode('utf-8'))
        got = [(s['proc'], s['params'].get('TIME'), [l['kw'] for l in s['loads']]) for s in v['steps'][1:]]
        bad = [c['msg'] for c in v['checks'] if c['level'] in ('error', 'warn')]
        check(got == steps and v['steps'][0]['name'] == 'CDB' and not bad and v['solve']['violations'] == [],
              '%-9s %s' % (kind, got))
    m = mapdl_model.Model(cant, solve_path=os.path.join(DATA, 'cant3d_static.solve.dat'))
    st = m.view()['steps']
    f1 = st[1]['loads'][0]
    check(f1['perNode'] == -4.0 and f1['total'] == -100.0 and '× 25 节点 = -100' in ' '.join(f1['rows'][0]),
          'F on a component is per node: -4 N x 25 nodes = -100 N (%s)' % f1['rows'][0])
    check(st[1]['procParams'] == {'NLGEOM': 'OFF', 'TIME': '1', 'NSUBST': '1'} and st[1]['outputs'][0]['vars'] == ['ALL', 'ALL'],
          'settings and outputs of load step 1: %s' % st[1]['procParams'])
    tmp = tempfile.mkdtemp(prefix='seg_test_')
    try:
        p = os.path.join(tmp, 'tdef.solve.dat')
        with open(p, 'w') as f:
            f.write('/SOLU\nANTYPE,STATIC\nTIME,5\nF,NTIP,FZ,-4\nSOLVE\nF,NTIP,FZ,-8\nSOLVE\n')
        m = mapdl_model.Model(cant, solve_path=p)
        check([s['params']['TIME'] for s in m.view()['steps'][1:]] == ['5', '6'],
              'TIME,5 then left out: 5, 6 (MAPDL 2025 R1: TIME AT END OF THE LOAD STEP 5.0, 6.0)')
        # no density in the CDB, modal in the solve segment -> error
        with open(cant, encoding='latin-1') as f:
            lines = f.read().split('\n')
        k = next(i for i, l in enumerate(lines) if l.startswith('MPDATA') and 'DENS' in l)
        nod = os.path.join(tmp, 'nodens.cdb')
        with open(nod, 'w', encoding='latin-1', newline='\n') as f:
            f.write('\n'.join(lines[:k] + lines[k + 1:]))
        m = mapdl_model.Model(nod, solve_path=os.path.join(DATA, 'cant3d_modal.solve.dat'))
        check(any('没有密度' in c['msg'] and 'Modal' in c['msg'] for c in m.checks()),
              'a modal solve segment on a CDB without density is an error')
        m = mapdl_model.Model(nod, solve_path=os.path.join(DATA, 'cant3d_static.solve.dat'))
        check(not any('没有密度' in c['msg'] for c in m.checks()), 'a static one is not')
        m = mapdl_model.Model(nod, solve_path=os.path.join(DATA, 'cant3d_buckle.solve.dat'))
        check(not any('没有密度' in c['msg'] for c in m.checks()), 'eigenvalue buckling does not need density either')
        bad = os.path.join(tmp, 'bad.solve.dat')
        with open(bad, 'w') as f:
            f.write('/SOLU\nANTYPE,STATIC\nF,NTIP,FZ,-4\n/SYS,calc.exe\nSOLVE\nF,NTIP,FZ,-8\n')
        m = mapdl_model.Model(cant, solve_path=bad)
        msgs = [c['msg'] for c in m.checks() if c['level'] in ('error', 'warn')]
        check(any('第 4 行: /SYS 不允许' in x for x in msgs) and any('最后一次 SOLVE 之后还有 1 条命令 (第 6 行)' in x for x in msgs),
              'checks: the refused line and the commands after the last SOLVE: %s' % msgs)
        check(m.view()['solve']['violations'] == [{'line': 4, 'msg': '/SYS 不允许 (斜杠命令只放行 /SOLU)'}], 'view solve.violations')
        m = mapdl_model.Model(cant, solve_path=False)
        check(any('还没有求解段' in c['msg'] for c in m.checks()) and not m.view()['solve']['present'],
              'without a solve segment: "还没有求解段"')
        nosolu = os.path.join(tmp, 'nosolu.solve.dat')
        with open(nosolu, 'w') as f:
            f.write('ANTYPE,STATIC\nF,NTIP,FZ,-4\nSOLVE\n')
        m = mapdl_model.Model(cant, solve_path=nosolu)
        check(any(c['level'] == 'error' and '第一条命令应是 /SOLU' in c['msg'] for c in m.checks()),
              'a solve segment that does not start with /SOLU is an error')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('the state each load step draws (block_comps)')
    m = mapdl_model.Model(os.path.join(DATA, 'block_comps.cdb'))
    v = m.view()
    check(len(v['steps']) == 10 and v['solve']['present'] and v['solve']['steps'] == 9, '10 steps: the CDB state + 9 load steps')
    counts = []
    for k in range(10):
        sy = m.symbols(k)
        counts.append((len(sy['press']) // 6, len(sy['cload']) // 6, [b['moving'] for b in sy['bc']]))
    check([c[0] for c in counts] == [16, 16, 16, 0, 0, 0, 0, 0, 0, 0],
          'pressure faces 16 (CDB), 16 (SF 2000 overwrites), then 0 after SFDELE: %s' % [c[0] for c in counts])
    check([c[1] for c in counts] == [9, 9, 9, 9, 0, 0, 0, 0, 0, 0], 'tip forces 9, then 0 after FDELE')
    check([c[2] for c in counts] == [[False]] * 7 + [[True]] * 3, 'base held, then moved by D,NBASE,UX,0.001 from step 7')
    sf = v['steps'][2]['loads'][0]
    check(sf['kw'] == 'SF' and sf['faces'] == 16 and sf['rows'][0][:3] == ['NTOP', 'PRES', '2000'], 'SF on NTOP: 16 element faces')
    check([b['type'] or b['command'] for b in v['steps'][7]['bcs']] == ['DDELE 删除约束', 'D'], 'load step 7: DDELE then D')
    for lid, n in (('sld1', 16), ('sld3', 9), ('sld6', 16)):
        h = m.highlight('load', lid)
        check(h['n'] == n, 'highlight %s %s: %d' % (lid, m.load_by_id[lid]['kw'], h['n']))
    check(m.highlight('bc', 'sbc1')['n'] == 9, 'highlight DDELE on NBASE: 9 nodes')

    print('SLOAD (bolt)')
    m = mapdl_model.Model(os.path.join(DATA, 'bolt.cdb'))
    v = m.view()
    sl = v['steps'][1]['loads'][0]
    check(sl['kw'] == 'SLOAD' and sl['section'] == 1 and sl['n'] == 9 and v['solve']['violations'] == [],
          'SLOAD on pretension section 1 (%d PRETS179 elements)' % sl['n'])
    check(len(v['steps']) == 3 and not [c for c in v['checks'] if c['level'] in ('error', 'warn')], 'two load steps, no finding')

    print('preview server')
    ws = tempfile.mkdtemp(prefix='seg_ws_')
    shutil.copy2(os.path.join(DATA, 'block_comps.cdb'), os.path.join(ws, 'job.cdb'))
    srv = subprocess.Popen([sys.executable, os.path.join(a.module_dir, 'preview_server.py'), '--root', ws, '--port',
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
        _, d = get('/api/list')
        m0 = d['files'][0]['mtime']
        _, d = get('/api/model?path=job.cdb')
        check(len(d['steps']) == 1 and not d['solve']['present'], 'no solve segment yet: the CDB state only')
        _, r = get('/api/review?path=job.cdb')
        check(r['parts']['solve'] is None, 'review parts: no solve digest')
        time.sleep(1.1)
        shutil.copy2(os.path.join(DATA, 'block_comps.solve.dat'), os.path.join(ws, 'job.solve.dat'))
        _, d = get('/api/list')
        check(d['files'][0]['mtime'] > m0, 'the list shows the newer time of the solve segment (the page reloads)')
        _, d = get('/api/model?path=job.cdb')
        check(len(d['steps']) == 10 and d['solve']['steps'] == 9, 'the model is read again with its 9 load steps')
        _, r = get('/api/review?path=job.cdb')
        check(r['parts']['solve'] is not None and r['parts']['cdb'] is not None, 'review parts: both digests')
        time.sleep(1.1)
        with open(os.path.join(ws, 'job.solve.dat'), 'a') as f:
            f.write('/SOLU\nNSEL,S,LOC,X,0\nSOLVE\n')
        _, d = get('/api/model?path=job.cdb')
        check(len(d['steps']) == 11 and d['solve']['violations'] and d['solve']['violations'][0]['line'] == 25,
              'changed solve segment, same CDB: read again, NSEL refused at line 25 (%s)' % d['solve']['violations'][:1])
        _, s = get('/api/symbols?path=job.cdb&step=3')
        check(s['press'] == [] and len(s['cload']) == 9 * 6, 'symbols of load step 3 (after SFDELE): no pressure, 9 forces')
    finally:
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
