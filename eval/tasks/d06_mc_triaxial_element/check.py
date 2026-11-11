# -*- coding: utf-8 -*-
"""Checker for d06_mc_triaxial_element.   py check.py <workdir>

The task fixes the specimen (height 1 m along y, base at y = 0) and asks for two steps: confinement, then shearing.
The FIRST step of the ODB is taken as the confinement step, the LAST general step as the shearing step.

End of the confinement step:
  confinement_displacement  largest |U| <= 1e-6 m: the initial stress is in equilibrium with the cell pressure
  confinement_stress        mean compressive stress (pressure invariant) = 100 kPa +-2 %, i.e. the specimen really
                            carries the cell pressure before it is sheared
End of the shearing step (integration-point values, uniform in a single element):
  axial_strain              largest |U2| = 0.05 m (5 % of the 1 m height), +-2 %
  confining_stress          least compressive principal stress = 100 kPa +-2 % (cell pressure kept)
  axial_stress_failure      most compressive principal stress = 100 Kp + 2 c sqrt(Kp) = 369.28 kPa, +-2 %
  deviator_stress_failure   Mises stress = 269.28 kPa, +-2 %
Compression is reported as positive.  Typical wrong models: friction angle in radians (142 kPa), unconfined strength
entered as cohesion (540 kPa), Drucker-Prager with beta = phi and d = c (196 kPa), cell pressure without initial
stress (the confinement step squeezes the specimen by 1 mm), pressure on one pair of faces only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,U')

    # ---- confinement step = first step of the ODB (needs a second step after it)
    st = cl.steps(ex)
    first = st[0].get('name') if st else None
    last = ((ex or {}).get('frame') or {}).get('step')
    ex1 = None
    if ex is not None:
        if first is None or first == last:
            c.odb_error = 'the ODB has no separate confinement step before the shearing step (steps: %s)' % (
                [s.get('name') for s in st])
        else:
            ex1 = c.extract(vars='S,U', step=first, frame=-1)
    u_conf = cl.max_mag(ex1, 'U')
    c.check_max('confinement_displacement', u_conf, tol['confinement_displacement_m'], True,
                'largest |U| at the end of the first step "%s"' % first)
    p_conf = cl.invariant(ex1, 'S', 'pressure', 'min', nodal=False)
    p_conf_max = cl.invariant(ex1, 'S', 'pressure', 'max', nodal=False)
    c.check_rel('confinement_stress', p_conf, exp['cell_pressure_kPa'], tol['stress_rel'], True,
                'mean compressive stress at the end of the first step (smallest %s, largest %s kPa)' % (
                    '%.5g' % p_conf if p_conf is not None else 'n/a',
                    '%.5g' % p_conf_max if p_conf_max is not None else 'n/a'))
    if ex is not None:
        c.odb_error = None

    # ---- end of shearing
    u2 = cl.comp_range(ex, 'U').get('U2')
    strain = max(abs(u2[0]), abs(u2[1])) / exp['height_m'] if u2 is not None else None
    c.check_rel('axial_strain', strain, exp['axial_strain'], tol['strain_rel'], True,
                'largest |U2| in the last frame / height 1 m')
    s3 = cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=False)
    s1 = cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=False)
    q = cl.invariant(ex, 'S', 'mises', 'max', nodal=False)
    lateral = -s3 if s3 is not None else None
    axial = -s1 if s1 is not None else None
    c.check_rel('confining_stress', lateral, exp['cell_pressure_kPa'], tol['stress_rel'], True,
                'least compressive principal stress in the last frame, compression positive')
    c.check_rel('axial_stress_failure', axial, exp['axial_stress_failure_kPa'], tol['stress_rel'], True,
                'most compressive principal stress in the last frame, compression positive')
    c.check_rel('deviator_stress_failure', q, exp['deviator_stress_failure_kPa'], tol['stress_rel'], True,
                'Mises stress in the last frame')

    c.load_result({'axial_stress_failure_kPa': 'number', 'deviator_stress_failure_kPa': 'number',
                   'confinement_max_displacement_m': 'number'})
    c.report('axial_stress_failure_kPa', [('most compressive principal stress', axial)], tol['report_rel'],
             critical=True)
    c.report('deviator_stress_failure_kPa',
             [('Mises stress', q), ('axial minus lateral stress', axial - lateral
                                    if (axial is not None and lateral is not None) else None)],
             tol['report_rel'], critical=True)
    rep = c.result.get('confinement_max_displacement_m')
    lim = tol['confinement_displacement_m']
    name = 'report_confinement_max_displacement_m'
    if rep is None:
        c.add(name, False, True, None, u_conf, lim, 'no valid "confinement_max_displacement_m" in result.json')
    elif u_conf is None:
        c.add(name, False, True, rep, None, lim, c._missing_note(''))
    else:
        ok = (abs(rep) <= lim and u_conf <= lim) or abs(abs(rep) - u_conf) <= 0.05 * u_conf
        c.add(name, ok, True, rep, u_conf, 'both <= %g or within 5 %%' % lim,
              'reported %.3g against %.3g in the ODB' % (rep, u_conf))


if __name__ == '__main__':
    cl.main(__file__, body)
