# -*- coding: utf-8 -*-
"""Round trip (ANSYS plan, section 5): the same model solved directly in one MAPDL session and through
ans build -> CDB -> clean directory -> solve segment must give the same results (relative difference < 1e-6).

    py test_roundtrip.py [--only static,modal,...] [--keep]

Cases (tests/data):
  static     cant.build.mac + cant_static1.solve.dat    solid cantilever, tip force, one load step
  modal      cant.build.mac + cant_modal.solve.dat      the same cantilever, 6 modes
  contact    contact2.build.mac + contact2.solve.dat    a block pressed onto another, frictional contact
             (CONTA174 / TARGE170, MU 0.2), two load steps
  twosteps   cant.build.mac + cant_two.solve.dat        tip force, then the force deleted (FDELE) and gravity
Direct: one session, /PSEARCH,OFF + build commands + solve segment, started like every MAPDL process of the
toolbox (mapdl_run.run).  Round trip: `ans build`, `ans run` in a temp workspace with the gate off.  Both result
files are read by the same post-processing input (ans_post): the times / frequencies of every set, the largest
displacement of the deciding set (value, node, components), the reaction totals, the forces on the components,
the largest von Mises stress.  Needs MAPDL; one session at a time.  Exit code 1 on failure.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANS = os.path.dirname(HERE)
DATA = os.path.join(HERE, 'data')
sys.path.insert(0, ANS)
import ans_post  # noqa: E402
import mapdl_run  # noqa: E402

CASES = {'static': ('cant', 'cant_static1', 'static', ['NFIX', 'NTIP']),
         'modal': ('cant', 'cant_modal', 'modal', []),
         'contact': ('contact2', 'contact2', 'static', ['NBASE', 'NTOP']),
         'twosteps': ('cant', 'cant_two', 'static', ['NFIX'])}
TOL = 1e-6
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what), flush=True)
    if not cond:
        FAILS.append(what)


def direct(case, mac, solve, kind, comps, tmp):
    d = os.path.join(tmp, case + '_direct')
    os.makedirs(d)
    with open(os.path.join(DATA, mac + '.build.mac'), encoding='utf-8') as f:
        build = f.read()
    with open(os.path.join(DATA, solve + '.solve.dat'), encoding='latin-1') as f:
        seg = f.read()
    with open(os.path.join(d, 'direct.inp'), 'w', encoding='latin-1', newline='\n') as f:
        f.write('/PSEARCH,OFF\n' + build.rstrip('\n') + '\nFINISH\n' + seg.rstrip('\n') + '\nFINISH\n/EXIT,NOSAVE\n')
    r = mapdl_run.run(d, 'direct.inp', 'direct', timeout=1800)
    if not r['ok']:
        return None, r
    with open(os.path.join(d, 'post_input.inp'), 'w', encoding='latin-1', newline='\n') as f:
        f.write(ans_post.post_input('direct', 'rst', kind, comps))
    mapdl_run.run(d, 'post_input.inp', 'post', timeout=600, out='post.out')
    return ans_post.parse(d, comps), r


def roundtrip(case, mac, solve, comps, tmp):
    ws = os.path.join(tmp, case + '_ws')
    os.makedirs(os.path.join(ws, '.abq'))
    with open(os.path.join(ws, '.abq', 'config.json'), 'w') as f:
        f.write('{"gate": "off"}')
    shutil.copy2(os.path.join(DATA, mac + '.build.mac'), os.path.join(ws, case + '.build.mac'))
    shutil.copy2(os.path.join(DATA, solve + '.solve.dat'), os.path.join(ws, case + '.solve.dat'))
    env = dict(os.environ)
    for k in ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL', 'ABQ_GATE'):
        env.pop(k, None)
    log = []
    for args in (['build', case], ['run', case] + sum([['--comp', c] for c in comps], [])):
        p = subprocess.run([sys.executable, '-B', os.path.join(ANS, 'ans.py')] + args, cwd=ws, env=env,
                           capture_output=True, timeout=3600)
        log.append(p.stdout.decode('utf-8', 'replace'))
        if p.returncode != 0:
            return None, '\n'.join(log)
    with open(os.path.join(ws, '.ans', case, 'run', 'summary.json'), encoding='utf-8') as f:
        return json.load(f), '\n'.join(log)


def rel(a, b, scale):
    return abs(a - b) / max(abs(scale), 1e-300)


def compare(case, x, y):
    """largest relative difference over the quantities both runs report, and what it was"""
    worst = (0.0, '')

    def upd(d, what):
        nonlocal worst
        if d > worst[0]:
            worst = (d, what)
    sx, sy = x.get('sets') or [], y.get('sets') or []
    check(len(sx) == len(sy) and len(sx) > 0, '%s: %d result sets both ways' % (case, len(sx)))
    for a, b in zip(sx, sy):
        upd(rel(a['time'], b['time'], max(abs(a['time']), 1.0)), 'time of set %d' % a['set'])
        if a['freq'] or b['freq']:
            upd(rel(a['freq'], b['freq'], max(abs(a['freq']), abs(b['freq']))), 'frequency of set %d' % a['set'])
    ux, uy = x.get('umax'), y.get('umax')
    check(bool(ux) and bool(uy), '%s: largest displacement read both ways' % case)
    if len(sx) > 1 and sx[0]['freq'] and abs(sx[0]['freq'] - sx[1]['freq']) <= 1e-6 * sx[0]['freq']:
        # a repeated first eigenvalue (the square cantilever bends alike in Y and Z): any combination of the two
        # mode shapes is a mode shape, so the shapes of two runs need not agree - compare the frequencies only
        print('     first frequency is repeated (%.8g Hz twice): mode shapes are not compared' % sx[0]['freq'])
        return worst
    if ux and uy:
        upd(rel(ux['value'], uy['value'], ux['value']), 'largest displacement')
        for k in range(3):
            upd(rel(ux['u'][k], uy['u'][k], ux['value']), 'displacement component %d' % k)
        check(ux['node'] == uy['node'], '%s: at the same node (%d)' % (case, ux['node']))
    rx, ry = x.get('reaction') or {}, y.get('reaction') or {}
    if rx or ry:
        scale = max([abs(v) for v in rx.values()] + [1e-30])
        for k in set(rx) | set(ry):
            upd(rel(rx.get(k, 0.0), ry.get(k, 0.0), scale), 'reaction ' + k)
    for c in set(x.get('comps') or {}) | set(y.get('comps') or {}):
        a, b = (x.get('comps') or {}).get(c), (y.get('comps') or {}).get(c)
        check(bool(a) and bool(b), '%s: component %s read both ways' % (case, c))
        if a and b:
            scale = max(max(abs(v) for v in a['fsum']), 1e-30)
            for k in range(3):
                upd(rel(a['fsum'][k], b['fsum'][k], scale), 'FSUM %s on %s' % ('XYZ'[k], c))
    sxv, syv = x.get('seqv'), y.get('seqv')
    if sxv and syv:
        upd(rel(sxv['value'], syv['value'], sxv['value']), 'largest von Mises stress')
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    tmp = tempfile.mkdtemp(prefix='ans_roundtrip_')
    report = {}
    try:
        for case, (mac, solve, kind, comps) in CASES.items():
            if a.only and case not in a.only.split(','):
                continue
            print(case, flush=True)
            x, rx = direct(case, mac, solve, kind, comps, tmp)
            check(x is not None, '%s: direct run (%s s)' % (case, rx['seconds'] if isinstance(rx, dict) else '?'))
            y, log = roundtrip(case, mac, solve, comps, tmp)
            check(y is not None, '%s: ans build + ans run' % case)
            if x is None or y is None:
                if y is None:
                    print(log[-2000:])
                continue
            d, what = compare(case, x, y)
            check(d < TOL, '%s: largest relative difference %.1e (%s), limit %.0e' % (case, d, what or '-', TOL))
            report[case] = {'direct': x, 'roundtrip': y, 'maxRelDiff': d, 'where': what}
            u = x.get('umax') or {}
            print('     direct: sets %d, largest displacement %.10g at node %s; round trip %.10g' % (
                len(x.get('sets') or []), u.get('value', math.nan), u.get('node'), (y.get('umax') or {}).get('value', math.nan)))
            if x.get('reaction'):
                print('     reactions direct %s / round trip %s' % (
                    {k: '%.10g' % v for k, v in x['reaction'].items()}, {k: '%.10g' % v for k, v in (y.get('reaction') or {}).items()}))
            if kind == 'modal':
                print('     frequencies direct %s' % ['%.8g' % s['freq'] for s in x['sets']])
    finally:
        out = os.path.join(HERE, 'roundtrip_result.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if a.keep:
            print('kept', tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
