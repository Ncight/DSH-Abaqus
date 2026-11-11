# -*- coding: utf-8 -*-
"""Checker for h01_fixed_beam_udl.   py check.py <workdir>

Physics (from the ODB, valid for planar or spatial beam models in any orientation):
  beam_elements    the task asks for beam elements: every element in the ODB must be a beam (B21, B31, B32, B33 ...)
  max_deflection   max |U| inside the window that brackets the Euler-Bernoulli (3.3333e-3 m) and the shear-flexible
                   (3.43e-3 m) mid-span deflection
  total_reaction   |net reaction| = q L = 4.0e4 N +-1 %  (task.md asks for the complete span, no symmetry)
  end_moment       fixing moment q L^2 / 12 = 1.3333e4 N m +-3 %: largest |RM| component in the ODB; when the ODB has
                   no RM field the value reported in result.json is used instead (stated in the note)
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402

BEAM = re.compile(r'^(B2|B3|PIPE2|PIPE3)', re.I)


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,RF,RM')

    types = cl.element_types(ex)
    other = sorted(t for t in types if not BEAM.match(t))
    if ex is None:
        c.add('beam_elements', False, True, None, 'beam elements only', None, 'NOT EVALUATED: ' + str(c.odb_error))
    else:
        c.add('beam_elements', bool(types) and not other, True, types, 'beam elements only', None,
              ('non-beam element types in the model: ' + ', '.join(other)) if other else '')

    umax = cl.max_mag(ex, 'U')
    lo, hi = tol['max_deflection_range_m']
    c.check_range('max_deflection', umax, lo, hi, True,
                  'max |U| in the ODB; Euler %.4e m, with shear %.4e m' % (exp['max_deflection_euler_m'],
                                                                         exp['max_deflection_m']))
    rf = cl.vec_sum_mag(ex, 'RF')
    c.check_rel('total_reaction', rf, exp['total_reaction_N'], tol['total_reaction_rel'], True,
                '|sum of RF over all nodes| in the ODB')

    c.load_result({'max_deflection_m': 'number', 'total_reaction_N': 'number', 'end_moment_Nm': 'number'})
    rm = cl.comp_absmax(ex, 'RM')
    m_exp, m_tol = exp['end_moment_Nm'], tol['end_moment_rel']
    if rm is not None:
        c.check_rel('end_moment', rm, m_exp, m_tol, True, 'largest |RM| component in the ODB')
    elif ex is None:
        c.check_rel('end_moment', None, m_exp, m_tol, True, 'largest |RM| component in the ODB')
    else:
        c.check_rel('end_moment', c.result.get('end_moment_Nm'), m_exp, m_tol, True,
                    'the ODB has no RM field: value reported in result.json used instead', use_abs=True)

    c.report('max_deflection_m', [('max |U|', umax), ('max |U component|', cl.comp_absmax(ex, 'U'))],
             tol['report_rel'], critical=True)
    c.report('total_reaction_N', [('|sum RF|', rf)], tol['report_rel'], critical=True)
    if rm is not None or ex is None:
        c.report('end_moment_Nm', [('largest |RM| component', rm)], tol['report_rel'], critical=True)
    else:
        rep = c.result.get('end_moment_Nm')
        c.add('report_end_moment_Nm', rep is not None, False, rep, None, tol['report_rel'],
              'not cross-checked: the ODB has no RM field (the reported value was used for end_moment)')


if __name__ == '__main__':
    cl.main(__file__, body)
