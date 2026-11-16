# -*- coding: utf-8 -*-
"""Run every example the ANSYS skills quote, through the toolbox, and compare with independent numbers.

    py run_all.py [--skills E:\\DSH-Ansys\\.dsh\\skills] [--only WORD] [--keep]

Each check gets its own temp workspace (gate off): the example's build commands and solve segment are copied in as
<job>.build.mac / <job>.solve.dat, then `ans build`, `ans run` (with --comp where the expectation needs it); the
numbers come from .ans/<job>/run/summary.json, which MAPDL wrote from the results file (ans rst).  The expected
values are hand calculations (beam theory, Lame, Euler) or equilibrium, with the tolerance the element model is
known to need; a few checks only assert that the run completes with the expected number of result sets.
Run this after editing a skill, after changing the ans tool and after an ANSYS upgrade.  One MAPDL at a time.
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
DEFAULT_SKILLS = r'E:\DSH-Ansys\.dsh\skills'
B = 'ansys-apdl-build/template_cantilever.build.mac'

# (title, build file, solve file, components for ans rst, [(path, kind, value, tolerance, where the value comes from)])
#   kind rel: |got - value| <= tol * |value|;  eq: got == value;  ge: got >= value;  range: value <= got <= tol
CHECKS = [
    ('实体悬臂梁静力 (SOLID185)', B, 'ansys-apdl-build/template_cantilever.solve.dat', ['NTIP'],
     [('comps.NTIP.umax', 'rel', 3.2e-4, 0.02, '梁理论 PL³/(3EI) = 100×1³/(3×2e11×5.208e-7) = 3.2e-4 m')]),
    ('壳悬臂板静力 (SHELL181)', 'ansys-apdl-build/template_shell.build.mac', 'ansys-apdl-build/template_shell.solve.dat', ['NTIP'],
     [('comps.NTIP.umax', 'rel', 5.0e-4, 0.05, '梁理论 5×1³/(3×2e11×(0.2×0.01³/12)) = 5.0e-4 m')]),
    ('梁悬臂静力 (BEAM188)', 'ansys-apdl-build/template_beam.build.mac', 'ansys-apdl-build/template_beam.solve.dat', ['NTIP'],
     [('comps.NTIP.umax', 'rel', 3.206e-4, 0.01, 'PL³/(3EI) + PL/(κGA) = 3.200e-4 + 0.006e-4 m')]),
    ('静力三个载荷步', B, 'ansys-solve-segment/static_steps.solve.dat', ['NTIP'],
     [('sets.len', 'eq', 3, None, '三个 SOLVE'), ('sets.-1.time', 'eq', 3.0, None, '第三步没写 TIME：上一步加 1'),
      ('comps.NTIP.umax', 'rel', 6.4e-4, 0.02, '200 N 时梁理论 6.4e-4 m')]),
    ('模态 (MODOPT,LANB)', B, 'ansys-solve-segment/modal.solve.dat', [],
     [('sets.len', 'eq', 6, None, 'MODOPT 6 阶'),
      ('sets.0.freq', 'rel', 40.76, 0.01, '悬臂梁一阶 (1.875²/2π)√(EI/ρAL⁴) = 40.76 Hz')]),
    ('谐响应 (HROPT,FULL)', B, 'ansys-solve-segment/harmonic.solve.dat', [],
     [('amplitude.len', 'eq', 50, None, 'NSUBST 50 个频率点'),
      ('amplitude.peakfreq', 'range', 38.0, 42.0, '共振在一阶频率 40.9 Hz 附近（2 Hz 一个点）')]),
    ('瞬态 (TRNOPT,FULL)', B, 'ansys-solve-segment/transient.solve.dat', [],
     [('sets.len', 'eq', 100, None, '0.1 s / DELTIM 0.001，OUTRES,ALL,ALL'), ('sets.-1.time', 'rel', 0.1, 1e-9, 'TIME,0.1')]),
    ('特征值屈曲 (PSTRES + BUCKLE)', B, 'ansys-solve-segment/buckle.solve.dat', [],
     [('sets.0.freq', 'rel', 2.570e5, 0.02, '欧拉 π²EI/(4L²) = 2.570e5 N（1 N 下的载荷因子）')]),
    ('杀死/激活单元 (NROPT,FULL + EKILL)', 'ansys-solve-segment/ekill.build.mac', 'ansys-solve-segment/ekill.solve.dat', [],
     [('sets.len', 'ge', 3, None, '三个 SOLVE 都算完'),
      ('reaction.FZ', 'rel', 4000.0, 1e-6, '最后一步 EALIVE 后：顶面压力 1000 Pa × 4 m² 全部回到基座')]),
    ('螺栓预紧 (SLOAD)', 'ansys-solve-segment/bolt.build.mac', 'ansys-solve-segment/bolt.solve.dat', ['NLEFT'],
     [('comps.NLEFT.fsum.0', 'absrel', 1000.0, 1e-4, '预紧力 1000 N 由两端支座承担：左端节点力合计 1000 N')]),
    ('轴对称厚壁圆筒内压 (PLANE182 KEYOPT(3)=1)', 'ansys-elements/axisym_cylinder.build.mac',
     'ansys-elements/axisym_cylinder.solve.dat', ['NINNER'],
     [('comps.NINNER.umax', 'rel', 9.833e-6, 0.01,
       'Lamé（两端自由）u(a) = p a²[(1-ν)a + (1+ν)b²/a]/(E(b²-a²)) = 9.833e-6 m')]),
    ('摩擦接触压紧两步 (CONTA174/TARGE170)', 'ansys-contact/contact_pressed.build.mac', 'ansys-contact/contact_pressed.solve.dat',
     ['NBASE'], [('sets.-1.step', 'eq', 2, None, '两个载荷步'),
                 ('reaction.FZ', 'rel', 720000.0, 1e-6, '平衡：2 MPa × 0.6 m × 0.6 m')]),
    ('绑定接触受拉 (KEYOPT(12)=5)', 'ansys-contact/contact_bonded.build.mac', 'ansys-contact/contact_bonded.solve.dat', ['NBASE'],
     [('reaction.FZ', 'rel', -360000.0, 1e-6, '平衡：-1 MPa × 0.36 m²，绑定面把拉力传下去')]),
    ('应力线性化 (PRSECT, ans rst --lin)', 'ansys-postprocess/lin_cylinder.build.mac', 'ansys-postprocess/lin_cylinder.solve.dat',
     ['--lin=R0:0.1,0,0.05,0.12,0,0.05'],
     [('lin.0.membrane.SINT', 'rel', 5.4545e7, 0.003, 'Lamé：壁厚平均 σθ − σr = pa/t + pa/(a+b) = 50 + 4.545 MPa'),
      ('lin.0.membrane.SY', 'rel', 5.0e7, 0.003, '薄膜环向 = pa/t = 10 × 0.1 / 0.02 = 50 MPa（Lamé 精确成立）'),
      ('lin.0.mb.I.SINT', 'rel', 6.4479e7, 0.003, 'Lamé 三个分量都线性化后，内表面薄膜+弯曲的 σ1 − σ3（数值积分）')]),
]


def get(d, path):
    cur = d
    for part in path.split('.'):
        if part == 'len':
            return len(cur)
        if part == 'peakfreq':
            return max(cur, key=lambda x: x['umax'])['freq']
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def judge(got, kind, value, tol):
    if kind == 'eq':
        return got == value
    if kind == 'ge':
        return got >= value
    if kind == 'range':
        return value <= got <= tol
    if kind == 'absrel':
        return abs(abs(got) - abs(value)) <= tol * abs(value)
    return abs(got - value) <= tol * abs(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skills', default=DEFAULT_SKILLS)
    ap.add_argument('--only', default='')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    env = dict(os.environ)
    for k in ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL', 'ABQ_GATE'):
        env.pop(k, None)
    tmp = tempfile.mkdtemp(prefix='ans_skill_checks_')
    fails, results = [], []
    try:
        for i, (title, build, solve, comps, expect) in enumerate(CHECKS, 1):
            if a.only and a.only not in title:
                continue
            ws = os.path.join(tmp, 'c%02d' % i)
            os.makedirs(os.path.join(ws, '.abq'))
            with open(os.path.join(ws, '.abq', 'config.json'), 'w') as f:
                f.write('{"gate": "off"}')
            job = 'chk%02d' % i
            shutil.copy2(os.path.join(a.skills, build), os.path.join(ws, job + '.build.mac'))
            shutil.copy2(os.path.join(a.skills, solve), os.path.join(ws, job + '.solve.dat'))
            ok, why, log = True, [], ''
            extra = sum([['--lin', c[6:]] if c.startswith('--lin=') else ['--comp', c] for c in comps], [])
            for args in (['build', job], ['run', job] + extra):
                p = subprocess.run([sys.executable, '-B', os.path.join(ANS, 'ans.py')] + args, cwd=ws, env=env,
                                   capture_output=True, timeout=3600)
                log += p.stdout.decode('utf-8', 'replace')
                if p.returncode != 0:
                    ok = False
                    why.append('ans %s 退出码 %d' % (args[0], p.returncode))
                    break
            summ = {}
            if ok:
                with open(os.path.join(ws, '.ans', job, 'run', 'summary.json'), encoding='utf-8') as f:
                    summ = json.load(f)
            lines = []
            for path, kind, value, tol, src in expect:
                try:
                    got = get(summ, path)
                except (KeyError, IndexError, TypeError, ValueError):
                    got = None
                good = got is not None and judge(got, kind, value, tol)
                ok = ok and good
                dev = ''
                if isinstance(got, (int, float)) and kind in ('rel', 'absrel') and value:
                    dev = ' (偏差 %+.2f%%)' % (100.0 * (abs(got) - abs(value)) / abs(value) if kind == 'absrel'
                                             else 100.0 * (got - value) / abs(value))
                lines.append('     %s %s = %s, 期望 %s%s；%s' % ('ok  ' if good else 'FAIL', path, got, value if kind != 'range'
                                                                 else '%s～%s' % (value, tol), dev, src))
            print('%s %s' % ('ok  ' if ok else 'FAIL', title), flush=True)
            for l in lines:
                print(l, flush=True)
            if not ok:
                fails.append(title)
                if why:
                    print('     ' + '; '.join(why))
                    print('\n'.join('     | ' + l for l in log.splitlines()[-25:]))
            results.append({'title': title, 'ok': ok, 'checks': lines, 'summary': summ})
    finally:
        with open(os.path.join(HERE, 'last_run.json'), 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        if a.keep:
            print('kept', tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK (%d)' % len(results) if not fails else '%d FAILED: %s' % (len(fails), fails)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
