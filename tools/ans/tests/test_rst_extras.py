# -*- coding: utf-8 -*-
"""What ans rst adds after the first live run (dsh-ansys step 6): stresses over a component, which way each mode
moves, and the end of every load step.  All are checked against listings MAPDL prints itself, read by a second,
independent post-processing input.

    py test_rst_extras.py [--keep]

stress   cant.build.mac + cant_static1.solve.dat, ans run --comp NFIX --comp NTIP --comp EOUTER (EOUTER is an
         element component: its nodes are the nodes of those elements, and only its elements are averaged at them).
         Expected: the largest and smallest SX, SY, SZ, SEQV over each component's nodes in PRNSOL,S,COMP /
         PRNSOL,S,PRIN (for EOUTER listed with only its elements selected); the node the tool names must carry that
         value; the node count of EOUTER equals the nodes of its elements (NSLE).
modes    rect.build.mac + rect_modal.solve.dat (50 mm high, 25 mm wide).  Expected: mode 1 moves in Z, mode 2 in Y
         (the beam is twice as stiff in Y), the frequencies are those of the result sets, and the largest |UX|, |UY|,
         |UZ| of every mode equal PRNSOL,U,COMP of that mode.
steps    contact2.build.mac + contact2.solve.dat (two load steps: 1 MPa, then 2 MPa on the 0.6 x 0.6 m top).
         Expected: the end of both load steps is listed, with reaction totals equal to PRRSOL after SET,1,LAST /
         SET,2,LAST in a second session and to p x 0.36 m2; ans rst --step 1 reads load step 1 for the main
         summary; --step 3 is refused.
Needs MAPDL; one session at a time.  Exit code 1 on failure.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANS = os.path.dirname(HERE)
DATA = os.path.join(HERE, 'data')
sys.path.insert(0, ANS)
import mapdl_run  # noqa: E402

FAILS = []
TOL = 1e-6


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what), flush=True)
    if not cond:
        FAILS.append(what)


def table(path, ncol):
    """rows <int node> + ncol floats from a MAPDL listing written with /HEADER off; a row may wrap"""
    out, cur = {}, None
    with open(path, encoding='latin-1') as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            try:
                vals = [float(x) for x in t]
            except ValueError:
                cur = None
                continue
            if t[0].lstrip('-').isdigit():
                cur = int(t[0])
                out[cur] = vals[1:]
            elif cur is not None:
                out[cur] += vals
    return {n: v[:ncol] for n, v in out.items() if len(v) >= ncol}


def workspace(tmp, case, mac, solve):
    ws = os.path.join(tmp, case + '_ws')
    os.makedirs(os.path.join(ws, '.abq'))
    with open(os.path.join(ws, '.abq', 'config.json'), 'w') as f:
        f.write('{"gate": "off"}')
    shutil.copy2(os.path.join(DATA, mac + '.build.mac'), os.path.join(ws, case + '.build.mac'))
    shutil.copy2(os.path.join(DATA, solve + '.solve.dat'), os.path.join(ws, case + '.solve.dat'))
    return ws


def ans(ws, *args):
    env = dict(os.environ)
    for k in ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL', 'ABQ_GATE'):
        env.pop(k, None)
    p = subprocess.run([sys.executable, '-B', os.path.join(ANS, 'ans.py')] + list(args), cwd=ws, env=env,
                       capture_output=True, timeout=3600)
    return p.returncode, p.stdout.decode('utf-8', 'replace')


def listing(rundir, job, body, header=False):
    """a second post-processing session on the job's results; body: lines after SET.  header: keep the column
    labels and totals of the listings (PRRSOL)"""
    inp = ['/PSEARCH,OFF', '/POST1', 'FILE,%s,rst' % job, '/PAGE,99999,,99999', '/FORMAT,,E,24,14'] + \
        ([] if header else ['/HEADER,OFF,OFF,OFF,OFF,OFF,OFF']) + body + ['FINISH', '/EXIT,NOSAVE']
    with open(os.path.join(rundir, 'listing.inp'), 'w', encoding='latin-1', newline='\n') as f:
        f.write('\n'.join(inp) + '\n')
    return mapdl_run.run(rundir, 'listing.inp', 'listing', timeout=600, out='listing.out')


def out(name, lines):
    return ['/OUTPUT,%s,txt' % name] + lines + ['/OUTPUT']


def stress_case(tmp):
    print('stress', flush=True)
    ws = workspace(tmp, 'stress', 'cant', 'cant_static1')
    rc, log = ans(ws, 'build', 'stress')
    check(rc == 0, 'ans build')
    rc, log = ans(ws, 'run', 'stress', '--comp', 'NFIX', '--comp', 'NTIP', '--comp', 'EOUTER')
    check(rc == 0, 'ans run --comp NFIX --comp NTIP --comp EOUTER')
    check('节点应力' in log, 'the summary prints the component stresses')
    rd = os.path.join(ws, '.ans', 'stress', 'run')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        summ = json.load(f)
    # node components: stresses averaged over all elements at a node.  Element component: only its own elements are
    # selected, so the nodes on its boundary average only its elements (as results scoped to a body)
    r = listing(rd, 'stress', ['SET,LAST', 'ALLSEL,ALL'] + out('l_n', ['NLIST']) + out('l_s', ['PRNSOL,S,COMP']) +
                out('l_p', ['PRNSOL,S,PRIN']) + ['ESEL,S,CENT,X,0.5,1', 'NSLE,S'] + out('l_eo', ['NLIST']) +
                out('l_eo_s', ['PRNSOL,S,COMP']) + out('l_eo_p', ['PRNSOL,S,PRIN']) + ['ALLSEL,ALL'])
    check(r['ok'], 'independent listing session')
    xyz = table(os.path.join(rd, 'l_n.txt'), 3)
    s_all = table(os.path.join(rd, 'l_s.txt'), 6)
    p_all = table(os.path.join(rd, 'l_p.txt'), 5)
    eo = set(table(os.path.join(rd, 'l_eo.txt'), 3))
    src = {'NFIX': (s_all, p_all), 'NTIP': (s_all, p_all),
           'EOUTER': (table(os.path.join(rd, 'l_eo_s.txt'), 6), table(os.path.join(rd, 'l_eo_p.txt'), 5))}
    sets = {'NFIX': {n for n, c in xyz.items() if abs(c[0]) < 1e-9},
            'NTIP': {n for n, c in xyz.items() if abs(c[0] - 1) < 1e-9}, 'EOUTER': eo}
    check(len(eo) == sum(1 for c in xyz.values() if c[0] > 0.5 - 1e-9), 'EOUTER: %d nodes = the nodes at x >= 0.5' % len(eo))
    worst = 0.0
    for c, nodes in sets.items():
        s, p = src[c]
        cv = (summ.get('comps') or {}).get(c) or {}
        st = cv.get('stress')
        check(bool(st), '%s: stresses in summary.json' % c)
        if not st:
            continue
        check(cv.get('nodes') == len(nodes), '%s: %s nodes (expected %d)' % (c, cv.get('nodes'), len(nodes)))
        for key, col, lst in (('SX', 0, s), ('SY', 1, s), ('SZ', 2, s), ('SEQV', 4, p)):
            vals = {n: lst[n][col] for n in nodes if n in lst}
            hi, lo = max(vals.values()), min(vals.values())
            scale = max(abs(hi), abs(lo), 1e-30)
            got = st[key]
            d = max(abs(got['max'] - hi), abs(got['min'] - lo)) / scale
            worst = max(worst, d)
            check(d < TOL, '%s %s: max %.6g / min %.6g, listing %.6g / %.6g' % (c, key, got['max'], got['min'], hi, lo))
            at_hi, at_lo = vals.get(got['maxNode']), vals.get(got['minNode'])
            check(at_hi is not None and abs(at_hi - hi) <= TOL * scale and at_lo is not None and abs(at_lo - lo) <= TOL * scale,
                  '%s %s: nodes %d / %d carry those values' % (c, key, got['maxNode'], got['minNode']))
    return {'worstRel': worst, 'comps': summ.get('comps')}


def modes_case(tmp):
    print('modes', flush=True)
    ws = workspace(tmp, 'modes', 'rect', 'rect_modal')
    rc, log = ans(ws, 'build', 'modes')
    check(rc == 0, 'ans build')
    rc, log = ans(ws, 'run', 'modes')
    check(rc == 0, 'ans run')
    check('以 Z 向为主' in log, 'the summary prints the direction of each mode')
    rd = os.path.join(ws, '.ans', 'modes', 'run')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        summ = json.load(f)
    md = summ.get('modes') or []
    check(len(md) == 6, '6 modes listed (%d)' % len(md))
    if len(md) < 2:
        return {}
    check(md[0]['dominant'] == 'Z' and md[1]['dominant'] == 'Y',
          'mode 1 moves in %s, mode 2 in %s (expected Z, Y)' % (md[0]['dominant'], md[1]['dominant']))
    ratio = md[1]['freq'] / md[0]['freq']
    check(1.9 < ratio < 2.1, 'f2 / f1 = %.4f (twice as stiff in Y: 2)' % ratio)
    check(all(abs(m['freq'] - st['freq']) <= 1e-9 * st['freq'] for m, st in zip(md, summ['sets'])),
          'mode frequencies are those of the result sets')
    body = []
    for k in range(1, len(md) + 1):
        body += ['SET,1,%d' % k] + out('l_u%d' % k, ['PRNSOL,U,COMP'])
    r = listing(rd, 'modes', body)
    check(r['ok'], 'independent listing session')
    worst = 0.0
    for m in md:
        u = table(os.path.join(rd, 'l_u%d.txt' % m['mode']), 3)
        exp = [max(abs(v[i]) for v in u.values()) for i in range(3)]
        d = max(abs(a - b) for a, b in zip(m['umax'], exp)) / max(exp)
        worst = max(worst, d)
        check(d < TOL, 'mode %d: |U| max %s, listing %s' % (m['mode'], ['%.6g' % v for v in m['umax']], ['%.6g' % v for v in exp]))
    return {'worstRel': worst, 'modes': md}


def steps_case(tmp):
    """the end of every load step, and --step N (user, 2026-11-15: intermediate load steps could not be read)"""
    print('steps', flush=True)
    sys.path.insert(0, ANS)
    import ans_post
    ws = workspace(tmp, 'steps', 'contact2', 'contact2')
    rc, log = ans(ws, 'build', 'steps')
    check(rc == 0, 'ans build')
    rc, log = ans(ws, 'run', 'steps', '--comp', 'NBASE')
    check(rc == 0, 'ans run --comp NBASE (two load steps: 1 MPa, then 2 MPa on 0.6 x 0.6 m)')
    check('各载荷步末' in log, 'the summary lists the end of every load step')
    rd = os.path.join(ws, '.ans', 'steps', 'run')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        summ = json.load(f)
    st = summ.get('steps') or []
    check([s['step'] for s in st] == [1, 2], 'load steps listed: %s' % [s['step'] for s in st])
    check(summ.get('main', {}).get('step') == 2, 'the main summary is the end of load step 2: %s' % summ.get('main'))
    r = listing(rd, 'steps', ['SET,1,LAST'] + out('l_r1', ['PRRSOL']) + ['SET,2,LAST'] + out('l_r2', ['PRRSOL']),
                header=True)
    check(r['ok'], 'independent listing session (SET,1,LAST / SET,2,LAST + PRRSOL)')
    worst = 0.0
    for s, p in zip(st, (1.0e6, 2.0e6)):
        exp = ans_post.read_totals(os.path.join(rd, 'l_r%d.txt' % s['step'])) or {}
        got = s.get('reaction') or {}
        scale = max([abs(v) for v in exp.values()] + [1e-30])
        d = max([abs(got.get(k, 0.0) - v) / scale for k, v in exp.items()] + [0.0 if exp else 1.0])
        worst = max(worst, d)
        check(d < TOL and set(got) == set(exp), 'load step %d: reaction totals %s, listing %s' % (
            s['step'], {k: '%.8g' % v for k, v in got.items()}, {k: '%.8g' % v for k, v in exp.items()}))
        fz = got.get('FZ', 0.0)
        check(abs(fz - p * 0.36) < 1e-6 * p * 0.36, 'load step %d: FZ %.8g = %.0f Pa x 0.36 m2' % (s['step'], fz, p))
        nb = (s.get('comps') or {}).get('NBASE')
        check(nb is not None and abs(nb['fsum'][2] + p * 0.36) < 1e-6 * p * 0.36,
              'load step %d: FSUM on NBASE %s (the elements push the base down)' % (s['step'], nb and '%.8g' % nb['fsum'][2]))
    check(abs(summ['reaction']['FZ'] - st[-1]['reaction']['FZ']) < 1e-9 * abs(st[-1]['reaction']['FZ']),
          'the main reaction is that of load step 2')
    rc, log = ans(ws, 'rst', 'steps', '--step', '1', '--comp', 'NBASE')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        s1 = json.load(f)
    check(rc == 0 and s1.get('main', {}).get('step') == 1 and s1.get('askedStep') == 1,
          'ans rst --step 1: main summary from load step 1 (%s)' % s1.get('main'))
    check(abs(s1['reaction']['FZ'] - 360000.0) < 1e-6 * 360000.0 and
          abs(s1['comps']['NBASE']['fsum'][2] + 360000.0) < 1e-6 * 360000.0,
          '--step 1: reaction FZ %.8g, FSUM on NBASE %.8g' % (s1['reaction']['FZ'], s1['comps']['NBASE']['fsum'][2]))
    check('载荷步 1 末' in log and '--step 1' in log, 'the printed summary says which load step it read')
    rc, log = ans(ws, 'rst', 'steps', '--step', '3')
    check(rc == 2 and '不在 1～2' in log, '--step 3 is refused (two load steps)')
    return {'worstRel': worst, 'steps': st}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    tmp = tempfile.mkdtemp(prefix='ans_rst_extras_')
    report = {}
    try:
        report['stress'] = stress_case(tmp)
        report['modes'] = modes_case(tmp)
        report['steps'] = steps_case(tmp)
    finally:
        with open(os.path.join(HERE, 'rst_extras_result.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if a.keep:
            print('kept', tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
