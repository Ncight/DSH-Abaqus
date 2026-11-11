# -*- coding: utf-8 -*-
"""Checker for h03_plasticity_bar.   py check.py <workdir>

Physics (from the ODB, last frame; valid for solid, plane-stress, or truss models in any orientation):
  axial_stress     largest Mises stress = (250e6 + 2e9 * 0.02) / 1.01 = 2.87129e8 Pa +-1 %
  peeq             largest PEEQ = 0.018564 +-2 %
  uniaxial_state   the stress state has to be uniform uniaxial tension: the hydrostatic pressure -(trace S)/3 must be
                   -sigma/3 = -9.571e7 Pa (+-3 %) at EVERY integration point.  Gripped ends, plane strain or any other
                   lateral restraint change the triaxiality and fail here.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,PEEQ')

    mises = cl.invariant(ex, 'S', 'mises', 'max', nodal=False)
    c.check_rel('axial_stress', mises, exp['axial_stress_Pa'], tol['axial_stress_rel'], True,
                'largest Mises stress at integration points (ODB)')
    peeq = cl.scalar(ex, 'PEEQ', 'max')
    if ex is not None and peeq is None:
        c.odb_error = 'no PEEQ field in the last frame of the ODB (available: %s)' % ', '.join(cl.available_fields(ex))
    c.check_rel('peeq', peeq, exp['peeq'], tol['peeq_rel'], True, 'largest PEEQ (ODB)')
    if ex is not None:
        c.odb_error = None

    p_max = cl.invariant(ex, 'S', 'pressure', 'max', nodal=False)
    p_min = cl.invariant(ex, 'S', 'pressure', 'min', nodal=False)
    p_exp, p_tol = exp['hydrostatic_pressure_Pa'], tol['hydrostatic_pressure_rel']
    types = cl.element_types(ex)
    truss_only = bool(types) and all(re.match(r'^T[23]D', t, re.I) for t in types)
    if (p_max is None or p_min is None) and truss_only:
        # one-dimensional elements carry axial stress only; Abaqus may offer no pressure invariant for them
        c.add('uniaxial_state', True, True, 'truss elements only', p_exp, p_tol,
              'truss elements (%s) are uniaxial by construction' % ', '.join(sorted(types)))
    elif p_max is None or p_min is None:
        c.check_rel('uniaxial_state', None, p_exp, p_tol, True, 'hydrostatic pressure invariant of S')
    else:
        worst = p_max if abs(p_max - p_exp) >= abs(p_min - p_exp) else p_min
        c.check_rel('uniaxial_state', worst, p_exp, p_tol, True,
                    'hydrostatic pressure -(trace S)/3 at integration points, range [%.5g, %.5g]; uniform uniaxial '
                    'tension gives -sigma/3 everywhere' % (p_min, p_max))

    c.load_result({'axial_stress_Pa': 'number', 'peeq': 'number'})
    c.report('axial_stress_Pa',
             [('max Mises', mises),
              ('max principal stress', cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=False)),
              ('largest stress component', cl.comp_absmax(ex, 'S'))],
             tol['report_rel'], critical=True)
    c.report('peeq', [('max PEEQ', peeq), ('min PEEQ', cl.scalar(ex, 'PEEQ', 'min'))], tol['report_rel'],
             critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
