# -*- coding: utf-8 -*-
"""Checker for t01_cantilever_static.   py check.py <workdir>

Physics (from the ODB, orientation independent):
  tip_deflection   max |U| over the model inside the window that brackets the Euler (3.8095e-4 m) and Timoshenko
                   (3.8392e-4 m) values; a converged 3-D model gives 3.83e-4.  Under-integrated coarse C3D8R meshes are
                   too soft, coarse fully integrated C3D8 meshes lock and are too stiff - both fall outside.
  reaction_force   |net reaction| = 1000 N (a half model would give 500 N; task.md asks for the complete beam).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,RF')

    umax = cl.max_mag(ex, 'U')
    lo, hi = tol['tip_deflection_range_m']
    c.check_range('tip_deflection', umax, lo, hi, True,
                  'max |U| in the ODB; Euler %.4e m, with shear %.4e m' % (exp['tip_deflection_euler_m'],
                                                                         exp['tip_deflection_m']))
    rf = cl.vec_sum_mag(ex, 'RF')
    c.check_rel('reaction_force', rf, exp['reaction_force_N'], tol['reaction_force_rel'], True,
                '|sum of RF over all nodes| in the ODB')

    c.load_result({'tip_deflection_m': 'number', 'reaction_force_N': 'number'})
    c.report('tip_deflection_m', [('max |U|', umax), ('max |U component|', cl.comp_absmax(ex, 'U'))],
             tol['report_rel'], critical=True)
    c.report('reaction_force_N', [('|sum RF|', rf)], tol['report_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
