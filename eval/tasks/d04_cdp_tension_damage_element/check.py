# -*- coding: utf-8 -*-
"""Checker for d04_cdp_tension_damage_element.   py check.py <workdir>

The task fixes the specimen: a 0.1 m cube (or a 0.1 x 0.1 m plane-stress square) in 0 <= x, y, z <= 0.1 m, pulled
along x by the displacement of the face x = 0.1 m.  Read from the field output of ALL frames of the ODB:
  total strain   U1 of the node nearest to (0.1, 0, 0), divided by 0.1 m
  stress         S11 of the element nearest to the centre (0.05, 0.05, 0.05)  (mean of its integration points)
  damage         DAMAGET, largest value in the last frame
The strain history has to be 0 -> 0.0004 (A) -> 0.00036 (B) -> 0.0008 (end); A is the first frame after which the
strain decreases, B the first frame after A where it increases again.

  load_probe          a node within 0.01 m of (0.1, 0, 0)
  strain_at_A / _B / _end   0.0004 / 0.00036 / 0.0008, +-1 %
  output_density      strain step between consecutive frames <= 1e-5 (5 % slack) - needed to catch the peak
  uniaxial_state      |S22|, |S33| at A <= 1 % of S11
  peak_stress         largest S11 of all frames = ft = 2000 kPa, +-3 %
  stress_at_0p0004    S11 at A = 857.1 kPa, +-3 %
  unloading_modulus   (S11_A - S11_B) / (e_A - e_B) = (1 - dt) E0 = 1.5857e7 kPa, +-3 %
  stress_at_end       S11 in the last frame = 369.9 kPa, +-4 %
  damage_at_end       0.7438, +-2 %
Expected values are hand values from Abaqus' definitions (cracking strain e - s/E0, plastic strain
e_ck - dt/(1-dt) s/E0, stiffness (1-dt) E0) with the tables interpolated linearly in the cracking strain.
What Abaqus 2021 actually returns for this test (reference solve.py, any element type, converged in the strain
increment): 861 - 865 kPa at A (+0.5 ... +0.95 %), unloading modulus 1.592e7 - 1.598e7 (+0.4 ... +0.75 %), 372.6 -
375.7 kPa at the end (+0.7 ... +1.6 %), damage 0.7403 - 0.7422 (-0.5 ... -0.2 %).  Two reasons: Abaqus interpolates
the converted tables in the PLASTIC strain, and its hardening variable PEEQT runs 0.3 - 0.8 % behind the axial plastic
strain PE11 (ramped boundary condition: 0.8 %, amplitude-driven: 0.3 %) - hence the spread and the tolerances.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,U,DAMAGET', probes=[exp['load_point'], exp['centre_point']], series='U,S,DAMAGET')

    node = cl.probe_location(ex, 0).get('node') or {}
    dist = cl.num(node.get('distance'))
    c.check_max('load_probe', dist, tol['probe_distance_m'], True,
                'distance between (0.1, 0, 0) and the nearest node of the model')

    edge = exp['edge_m']
    u = cl.probe_series(ex, 0, 'U', 'U1')
    s11 = cl.probe_series(ex, 1, 'S', 'S11')
    lateral = [cl.probe_series(ex, 1, 'S', lab) for lab in ('S22', 'S33')]
    ok_probe = dist is not None and dist <= tol['probe_distance_m']
    eps = [v / edge for _t, v in u] if ok_probe else []
    sig = [v for _t, v in s11]
    if ex is not None:
        if not eps or not sig:
            c.odb_error = 'no U / S field output history for the specimen'
            eps, sig = [], []
        elif len(eps) != len(sig):
            c.odb_error = 'U and S are not available in the same frames (%d / %d)' % (len(eps), len(sig))
            eps, sig = [], []
    n = len(eps)

    i_a = i_b = None
    for i in range(n - 1):
        if eps[i + 1] < eps[i] - 1e-9:
            i_a = i
            break
    if i_a is not None:
        i_b = n - 1
        for i in range(i_a + 1, n - 1):
            if eps[i + 1] > eps[i] + 1e-9:
                i_b = i
                break
    targets = exp['strain_history']
    no_unload = 'the strain history has no unloading branch'
    for name, idx, target in (('strain_at_A', i_a, targets[0]), ('strain_at_B', i_b, targets[1]),
                              ('strain_at_end', n - 1 if n else None, targets[2])):
        if n and idx is None and c.odb_error is None:
            c.odb_error = no_unload
        c.check_rel(name, eps[idx] if idx is not None else None, target, tol['strain_rel'], True,
                    'total strain U1 / 0.1 m (frame %s of %d)' % (idx, n))
        if c.odb_error == no_unload:
            c.odb_error = None
    step = max(abs(eps[i + 1] - eps[i]) for i in range(n - 1)) if n > 1 else None
    c.check_max('output_density', step, exp['max_strain_step'] * (1 + tol['step_slack']), True,
                'largest change of the total strain between consecutive frames (%d frames)' % n)

    lat = None
    if i_a is not None and all(len(x) == n for x in lateral) and sig[i_a]:
        lat = max(abs(x[i_a][1]) for x in lateral) / abs(sig[i_a])
    c.check_max('uniaxial_state', lat, tol['lateral_stress_ratio'], True,
                'largest lateral stress (S22, S33) at A as a fraction of S11')

    c.check_rel('peak_stress', max(sig) if sig else None, exp['peak_stress_kPa'], tol['stress_rel'], True,
                'largest S11 of all frames (ODB)')
    if n and i_a is None:
        c.odb_error = no_unload
    s_a = sig[i_a] if i_a is not None else None
    c.check_rel('stress_at_0p0004', s_a, exp['stress_at_0p0004_kPa'], tol['stress_rel'], True,
                'S11 at the end of the first loading branch (A)')
    modulus = None
    if i_a is not None and i_b is not None and eps[i_a] != eps[i_b]:
        modulus = (sig[i_a] - sig[i_b]) / (eps[i_a] - eps[i_b])
    c.check_rel('unloading_modulus', modulus, exp['unloading_modulus_kPa'], tol['modulus_rel'], True,
                'secant between A and B: (%s - %s) kPa over the strain change' % (
                    '%.5g' % s_a if s_a is not None else 'n/a',
                    '%.5g' % sig[i_b] if i_b is not None else 'n/a'))
    if c.odb_error == no_unload:
        c.odb_error = None
    c.check_rel('stress_at_end', sig[-1] if sig else None, exp['stress_at_end_kPa'], tol['end_stress_rel'], True,
                'S11 in the last frame')
    dmg = cl.scalar(ex, 'DAMAGET', 'max')
    if ex is not None and dmg is None and c.odb_error is None:
        c.odb_error = 'no DAMAGET field output in the last frame (no tension damage defined, or not requested)'
    c.check_rel('damage_at_end', dmg, exp['damage_at_end'], tol['damage_rel'], True,
                'largest DAMAGET in the last frame (ODB)')
    why = c.odb_error
    c.odb_error = None

    c.load_result({'peak_stress_kPa': 'number', 'stress_at_0p0004_kPa': 'number',
                   'unloading_modulus_kPa': 'number', 'damage_at_end': 'number'})
    c.odb_error = why
    c.report('peak_stress_kPa', [('largest S11 of all frames', max(sig) if sig else None)], tol['report_rel'],
             critical=True)
    c.report('stress_at_0p0004_kPa', [('S11 at A', s_a)], tol['report_rel'], critical=True)
    c.report('unloading_modulus_kPa', [('secant between A and B', modulus)], tol['report_modulus_rel'],
             critical=True)
    c.report('damage_at_end', [('largest DAMAGET in the last frame', dmg)], tol['report_rel'], critical=True)
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
