# -*- coding: utf-8 -*-
"""ans rst --lin (stress linearization with PRSECT, added 2026-11-16 for pressure-vessel assessment) against the
exact Lame solution of a closed thick cylinder.

    py test_rst_lin.py [--keep]

thickcyl.build.mac + thickcyl.solve.dat: a quarter ring, inner radius a = 0.1 m, outer b = 0.12 m, 0.1 m long,
SOLID186 mapped with 8 elements through the wall, symmetry on x = 0, y = 0, z = 0, internal pressure 10 MPa and the
closed-end thrust p a^2 / (b^2 - a^2) as a tension on z = 0.1.  Expected (Lame):
  sigma_r = A - B / r^2, sigma_t = A + B / r^2, sigma_z = A with A = p a^2 / (b^2 - a^2), B = p a^2 b^2 / (b^2 - a^2);
  membrane = the mean through the wall, bending at the start = (6 / t^2) * integral sigma(s) (t/2 - s) ds (all three
  components linearized, as PRSECT does), SINT = largest minus smallest principal stress, SEQV = von Mises.
Lines:  R0  radial at theta = 0 (global X = radial, Y = hoop, Z = axial): membrane hoop = p a / t exactly
        R45 radial at theta = 45 deg: same invariants, components rotated
        NEAR given 0.5 mm off the nodes: the nearest nodes are taken, the distance is reported, no warning
        OUT  end point 15 mm outside the wall: snapped to the outer surface, warned
Also the argument checks that need no MAPDL.  Needs MAPDL; one session at a time.  Exit code 1 on failure.
"""
import argparse
import json
import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANS = os.path.dirname(HERE)
sys.path.insert(0, ANS)
sys.path.insert(0, HERE)
import ans as anstool  # noqa: E402
from test_rst_extras import FAILS, ans, check, workspace  # noqa: E402

P_IN, A_IN, B_OUT = 1.0e7, 0.1, 0.12
TOL = 0.005          # PRSECT prints four significant digits; 8 quadratic elements through the wall


def lame():
    A = P_IN * A_IN ** 2 / (B_OUT ** 2 - A_IN ** 2)
    B = P_IN * A_IN ** 2 * B_OUT ** 2 / (B_OUT ** 2 - A_IN ** 2)
    t = B_OUT - A_IN
    n = 20000
    h = t / n
    mem = [0.0, 0.0, 0.0]
    ben = [0.0, 0.0, 0.0]
    for i in range(n + 1):
        s = i * h
        r = A_IN + s
        w = h / 3 * (1 if i in (0, n) else 4 if i % 2 else 2)        # Simpson
        sig = (A - B / r ** 2, A + B / r ** 2, A)
        for k in range(3):
            mem[k] += w * sig[k] / t
            ben[k] += w * sig[k] * (t / 2 - s) * 6 / t ** 2
    mb_i = [m + b for m, b in zip(mem, ben)]
    mb_o = [m - b for m, b in zip(mem, ben)]

    def inv(p):
        s1, s2, s3 = sorted(p, reverse=True)
        return s1 - s3, math.sqrt(((s1 - s2) ** 2 + (s2 - s3) ** 2 + (s3 - s1) ** 2) / 2)
    tot_i = (A - B / A_IN ** 2, A + B / A_IN ** 2, A)
    tot_o = (A - B / B_OUT ** 2, A + B / B_OUT ** 2, A)
    return {'membrane': mem, 'mbI': mb_i, 'mbO': mb_o, 'inv': {
        'membrane': inv(mem), 'mbI': inv(mb_i), 'mbO': inv(mb_o), 'totI': inv(tot_i), 'totO': inv(tot_o)},
        'hoopMembrane': P_IN * A_IN / t}


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-30)


def args_case():
    print('arguments', flush=True)
    ok, msg = anstool.parse_lins(['SCL1:0,0.5,0.1,0,0.512,0.11', '0,0,0,1,1,1'])
    check(msg is None and [n for n, _, _ in ok] == ['SCL1', 'SCL2'] and ok[0][2] == (0.0, 0.512, 0.11),
          'two lines, the second named SCL2 by default: %s' % ok)
    for bad, why in ((['1,2,3'], '6 个数'), (['A:1,1,1,1,1,1'], '同一个点'), (['1x:0,0,0,1,1,1'], '名字'),
                     (['S:0,0,0,1,1,1', 'S:0,0,0,2,2,2'], '重复'), (['%d,0,0,1,1,1' % k for k in range(11)], '最多')):
        _, msg = anstool.parse_lins(bad)
        check(msg is not None and why in msg, 'refused %s: %s' % (bad[:2], msg))


def lin_case(tmp):
    print('lin', flush=True)
    ex = lame()
    ws = workspace(tmp, 'lin', 'thickcyl', 'thickcyl')
    rc, log = ans(ws, 'build', 'lin')
    check(rc == 0, 'ans build')
    c45, s45 = math.cos(math.pi / 4), math.sin(math.pi / 4)
    lines = ['R0:0.1,0,0.05,0.12,0,0.05',
             'R45:%r,%r,0.05,%r,%r,0.05' % (0.1 * c45, 0.1 * s45, 0.12 * c45, 0.12 * s45),
             'NEAR:0.1,0,0.0505,0.12,0,0.0505',
             'OUT:0.1,0,0.05,0.135,0,0.05']
    args = ['run', 'lin']
    for l in lines:
        args += ['--lin', l]
    rc, log = ans(ws, *args)
    check(rc == 0, 'ans run --lin x4')
    print(log[-3000:])
    check('应力线性化' in log, 'the summary prints the linearization')
    rd = os.path.join(ws, '.ans', 'lin', 'run')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        summ = json.load(f)
    lin = {d['name']: d for d in summ.get('lin') or []}
    check(sorted(lin) == ['NEAR', 'OUT', 'R0', 'R45'], 'four lines in summary.json: %s' % sorted(lin))
    worst = 0.0
    r0 = lin.get('R0') or {}
    scale = ex['hoopMembrane']     # components are compared on the scale of the stress (the radial one is near 0)
    if r0.get('membrane'):
        m = r0['membrane']
        for key, k in (('SX', 0), ('SY', 1), ('SZ', 2)):
            d = abs(m[key] - ex['membrane'][k]) / scale
            worst = max(worst, d)
            check(d < TOL, 'R0 membrane %s %.5g, Lame %.5g (%.3f%% of p a / t)' % (key, m[key], ex['membrane'][k], 100 * d))
        d = rel(m['SY'], ex['hoopMembrane'])
        check(d < TOL, 'R0 membrane hoop %.5g = p a / t = %.5g (%.3f%%)' % (m['SY'], ex['hoopMembrane'], 100 * d))
        for key, k in (('SX', 0), ('SY', 1)):
            for where, exk in (('I', 'mbI'), ('O', 'mbO')):
                got = r0['mb'][where][key]
                d = abs(got - ex[exk][k]) / scale
                worst = max(worst, d)
                check(d < TOL, 'R0 membrane+bending %s at %s %.5g, Lame %.5g (%.3f%% of p a / t)' % (
                    key, where, got, ex[exk][k], 100 * d))
    for name in ('R0', 'R45', 'NEAR'):
        d0 = lin.get(name) or {}
        if not d0.get('membrane'):
            check(False, '%s: PRSECT values' % name)
            continue
        for sec, key, exk in (('membrane', None, 'membrane'), ('mb', 'I', 'mbI'), ('mb', 'O', 'mbO'),
                              ('total', 'I', 'totI'), ('total', 'O', 'totO')):
            v = d0[sec] if key is None else d0[sec][key]
            for j, inv in enumerate(('SINT', 'SEQV')):
                d = rel(v[inv], ex['inv'][exk][j])
                worst = max(worst, d)
                check(d < TOL, '%s %s%s %s %.5g, Lame %.5g (%.3f%%)' % (
                    name, sec, '' if key is None else ' ' + key, inv, v[inv], ex['inv'][exk][j], 100 * d))
    nr = lin.get('NEAR') or {}
    check(nr.get('snap') and all(abs(s - 0.0005) < 1e-9 for s in nr['snap']),
          'NEAR: both ends 0.5 mm from the given points: %s' % nr.get('snap'))
    o = lin.get('OUT') or {}
    check(o.get('snap') and abs(o['snap'][1] - 0.015) < 1e-9 and abs(o['xyz'][1][0] - 0.12) < 1e-9,
          'OUT: end snapped to the outer surface, 15 mm away: %s' % o.get('snap'))
    check('⚠ 端点离给定位置' in log and log.count('⚠ 端点离给定位置') == 1, 'OUT (and only OUT) is warned')
    rc2, log2 = ans(ws, 'rst', 'lin', '--lin', 'R0:0.1,0,0.05,0.12,0,0.05')
    with open(os.path.join(rd, 'summary.json'), encoding='utf-8') as f:
        s2 = json.load(f)
    check(rc2 == 0 and [d['name'] for d in s2.get('lin') or []] == ['R0'] and
          s2['lin'][0]['membrane']['SINT'] == r0.get('membrane', {}).get('SINT'), 'ans rst --lin gives the same R0')
    return {'worstRel': worst, 'lame': ex, 'lin': summ.get('lin')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    tmp = tempfile.mkdtemp(prefix='ans_rst_lin_')
    report = {}
    try:
        args_case()
        report['lin'] = lin_case(tmp)
    finally:
        with open(os.path.join(HERE, 'rst_lin_result.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if a.keep:
            print('kept', tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
