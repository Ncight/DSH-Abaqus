# -*- coding: utf-8 -*-
"""Checker for h02_ss_plate_pressure.   py check.py <workdir>

Physics (from the ODB; valid for the complete plate and for symmetric half / quarter models):
  shell_elements      the task asks for shell elements: every element in the ODB must be a shell (S4R, S8R, S3 ...)
  center_deflection   max |U| = 0.00406 q a^4 / D = 2.1112e-4 m +-3 %  (clamped edges would give 31 % of that)
  max_bending_stress  bending stress at the plate centre = 6 (0.0479 q a^2) / t^2 = 2.874e6 Pa +-5 %.
                      It is measured as the largest in-plane NORMAL stress component (S11 / S22 in the local shell
                      axes) at the integration points of all section points in the ODB, not as the largest principal
                      stress: shear-flexible shells with freely rotating ("soft") simple supports build up a
                      twisting-stress peak next to the plate corners, and with element-nodal extrapolation that peak
                      reaches the centre value (S8R 20 x 20: 3.01e6 Pa at (0.05, 0.05) against 2.90e6 Pa at the
                      centre).  The normal components do not see the twist.  (If the plate edges are not parallel to
                      the local shell axes the two measures coincide again; the largest principal stress is used
                      only when no components are available.)
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402

SHELL = re.compile(r'^(S\d|STRI|SC\d)', re.I)


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,U')

    types = cl.element_types(ex)
    other = sorted(t for t in types if not SHELL.match(t))
    if ex is None:
        c.add('shell_elements', False, True, None, 'shell elements only', None, 'NOT EVALUATED: ' + str(c.odb_error))
    else:
        c.add('shell_elements', bool(types) and not other, True, types, 'shell elements only', None,
              ('non-shell element types in the model: ' + ', '.join(other)) if other else '')

    umax = cl.max_mag(ex, 'U')
    c.check_rel('center_deflection', umax, exp['center_deflection_m'], tol['center_deflection_rel'], True,
                'max |U| in the ODB')

    comps = cl.comp_range(ex, 'S')
    normal = [max(abs(mx), abs(mn)) for lab, (mx, mn) in comps.items() if lab in ('S11', 'S22')]
    s_ip = cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=False)
    s_en = cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=True)
    if normal:
        sigma = max(normal)
        note = 'largest |S11|, |S22| at integration points (ODB)'
        if s_en is not None:
            note += '; largest principal stress at element nodes anywhere in the plate: %.5g' % s_en
    else:
        sigma, note = s_ip, 'largest max-principal stress at integration points (no stress components in the ODB)'
    c.check_rel('max_bending_stress', sigma, exp['max_bending_stress_Pa'], tol['max_bending_stress_rel'], True, note)

    c.load_result({'center_deflection_m': 'number', 'max_bending_stress_Pa': 'number'})
    c.report('center_deflection_m', [('max |U|', umax), ('max |U component|', cl.comp_absmax(ex, 'U'))],
             tol['report_rel'], critical=True)
    c.report('max_bending_stress_Pa',
             [('largest normal stress component at integration points', sigma if normal else None),
              ('max principal at element nodes', s_en),
              ('max principal at integration points', s_ip),
              ('min principal at element nodes', cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=True)),
              ('min principal at integration points', cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=False)),
              ('Mises at element nodes', cl.invariant(ex, 'S', 'mises', 'max', nodal=True)),
              ('Mises at integration points', cl.invariant(ex, 'S', 'mises', 'max', nodal=False))],
             tol['report_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
