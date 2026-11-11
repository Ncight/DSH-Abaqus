# -*- coding: utf-8 -*-
"""Checker for t02_plate_hole.   py check.py <workdir>

Physics (from the ODB, independent of orientation and of the use of symmetry):
  max_stress   largest maximum-principal stress extrapolated to the element nodes, within +-4 % of 303 MPa
               (Howland / Heywood, gross Kt = 3.03 for d/W = 0.1; a converged FE model gives 303.7 MPa).
               A mesh without local refinement at the hole misses the peak by 7 % (CPS8R) to 50 % (CPS4R).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S')

    s_en = cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=True)
    c.check_rel('max_stress', s_en, exp['max_stress_Pa'], tol['max_stress_rel'], True,
                'max principal stress at element nodes (ODB)')

    c.load_result({'max_stress_Pa': 'number', 'stress_concentration_factor': 'number'})
    cands = [('max principal at element nodes', s_en),
             ('max principal at integration points', cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=False)),
             ('Mises at element nodes', cl.invariant(ex, 'S', 'mises', 'max', nodal=True)),
             ('Mises at integration points', cl.invariant(ex, 'S', 'mises', 'max', nodal=False))]
    c.report('max_stress_Pa', cands, tol['report_rel'], critical=True)
    nominal = exp['nominal_stress_Pa']
    c.report('stress_concentration_factor', [(lab + ' / 100 MPa', v / nominal) for lab, v in cands if v is not None],
             tol['report_rel'], critical=False)


if __name__ == '__main__':
    cl.main(__file__, body)
