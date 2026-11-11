# -*- coding: utf-8 -*-
"""Checker for t04_lame_cylinder.   py check.py <workdir>

Physics (from the ODB; valid for quarter / half / complete rings, axisymmetric strips and 3-D slices):
  hoop_stress_inner    largest max-principal stress at element nodes = hoop stress at the bore, 166.67 MPa +-2 %
  radial_stress_inner  smallest min-principal stress at element nodes = radial stress at the bore, -100 MPa +-4 %
  radial_disp_inner    radial displacement of the bore, 9.0794e-5 m +-2 % (plane stress: 9.365e-5 m, +3.15 %).
                       Measured as max |U|; because a complete ring on point supports may carry a rigid-body shift
                       or rotation in U, the value (inner radius) x (largest principal strain at element nodes) -
                       the hoop strain at the bore, which is free of rigid-body motion - is accepted as well.
  plane_strain_state   hydrostatic pressure -(s_r + s_theta + s_z)/3 is uniform in the Lame solution:
                       -28.89 MPa in plane strain (s_z = nu (s_r + s_theta) = 20 MPa), -22.22 MPa in plane stress.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,E,LE,U')

    hoop_in = cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=True)
    c.check_rel('hoop_stress_inner', hoop_in, exp['hoop_stress_inner_Pa'], tol['hoop_stress_inner_rel'], True,
                'largest max-principal stress at element nodes (ODB)')
    rad_in = cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=True)
    c.check_rel('radial_stress_inner', rad_in, exp['radial_stress_inner_Pa'], tol['radial_stress_inner_rel'], True,
                'smallest min-principal stress at element nodes (ODB)')

    # radial displacement of the bore: max |U|, or inner radius x hoop strain when U carries rigid-body motion
    umax = cl.max_mag(ex, 'U')
    eps = None
    for name in ('E', 'LE'):
        eps = cl.invariant(ex, name, 'maxPrincipal', 'max', nodal=True)
        if eps is not None:
            break
    u_eps = exp['inner_radius_m'] * eps if eps is not None else None
    u_exp, u_tol = exp['radial_disp_inner_m'], tol['radial_disp_inner_rel']
    cands = [(lab, v) for lab, v in (('max |U|', umax), ('inner radius x max principal strain', u_eps)) if v is not None]
    if not cands:
        c.check_rel('radial_disp_inner', None, u_exp, u_tol, True, 'radial displacement of the bore')
    else:
        lab, best = min(cands, key=lambda lv: abs(lv[1] - u_exp))
        note = '%s (ODB)' % lab
        if len(cands) == 2:
            note += '; max |U| = %.5g, inner radius x hoop strain = %.5g' % (umax, u_eps)
        note += '; plane stress would give %.4e' % exp['plane_stress_radial_disp_m']
        c.check_rel('radial_disp_inner', best, u_exp, u_tol, True, note)

    p_max = cl.invariant(ex, 'S', 'pressure', 'max', nodal=False)
    p_min = cl.invariant(ex, 'S', 'pressure', 'min', nodal=False)
    p_exp, p_tol = exp['hydrostatic_pressure_Pa'], tol['hydrostatic_pressure_rel']
    if p_max is None or p_min is None:
        c.check_rel('plane_strain_state', None, p_exp, p_tol, True, 'hydrostatic pressure invariant of S')
    else:
        worst = p_max if abs(p_max - p_exp) >= abs(p_min - p_exp) else p_min
        c.check_rel('plane_strain_state', worst, p_exp, p_tol, True,
                    'hydrostatic pressure -(trace S)/3 at integration points, range [%.5g, %.5g]; plane strain %.4e, '
                    'plane stress %.4e' % (p_min, p_max, p_exp, exp['plane_stress_hydrostatic_pressure_Pa']))

    c.load_result({'hoop_stress_inner_Pa': 'number', 'radial_disp_inner_m': 'number', 'hoop_stress_outer_Pa': 'number'})
    c.report('hoop_stress_inner_Pa',
             [('max principal at element nodes (max)', hoop_in),
              ('max principal at integration points (max)', cl.invariant(ex, 'S', 'maxPrincipal', 'max', nodal=False))],
             tol['report_rel'], critical=True)
    c.report('radial_disp_inner_m', [('max |U|', umax), ('inner radius x hoop strain', u_eps)],
             tol['report_rel'], critical=True)
    c.report('hoop_stress_outer_Pa',
             [('max principal at element nodes (min)', cl.invariant(ex, 'S', 'maxPrincipal', 'min', nodal=True)),
              ('max principal at integration points (min)', cl.invariant(ex, 'S', 'maxPrincipal', 'min', nodal=False))],
             tol['report_rel'], critical=False)


if __name__ == '__main__':
    cl.main(__file__, body)
