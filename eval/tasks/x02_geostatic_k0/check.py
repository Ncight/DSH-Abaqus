# -*- coding: utf-8 -*-
"""Checker for x02_geostatic_k0.   py check.py <workdir>

The model has to be in m-t-s units (kN, kPa, t/m3).  Physics, all from the last frame of the ODB and independent of
the orientation, of 2-D / 3-D and of the mesh density:

  max_displacement   max |U| <= 1e-5 m.  A correct geostatic state gives ~1e-15 m; gravity without in-situ stress
                     gives 0.0874 m.  (The bound is 1e-5 and not 1e-4 because "gravity only" with the modulus
                     entered 1000 times too large - 3.0e7, i.e. Pa instead of kPa - settles 8.7e-5 m.)
  vertical_stress    the vertical stress is the most compressive principal stress everywhere and varies linearly
                     with depth, so (most compressive value) + (least compressive value) of the min-principal stress
                     over the model equals the stress at the base, -rho g H = -352.8 kPa, for any uniform mesh and
                     for integration-point as well as nodal values (a sampling point c*h above the base is balanced by
                     the one c*h below the surface).  +-4 %.
  k0_ratio           horizontal / vertical stress at the deepest stress point: (most compressive max-principal) /
                     (most compressive min-principal) = K0 = nu/(1-nu) = 0.4286 +-5 %.  In a laterally confined
                     column a wrong K0 does NOT cause displacements, so this is the only check that sees it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,U,RF')

    umax = cl.max_mag(ex, 'U')
    u_lim = tol['max_displacement_m']
    c.check_max('max_displacement', umax, u_lim, True,
                'max |U| in the last frame (ODB); without in-situ stress the layer settles %.4f m' %
                exp['settlement_without_initial_stress_m'])

    # element-nodal values when the extractor has them, otherwise integration-point values (both work, see above)
    sv_min = cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=True)
    sv_max = cl.invariant(ex, 'S', 'minPrincipal', 'max', nodal=True)
    sh_min = cl.invariant(ex, 'S', 'maxPrincipal', 'min', nodal=True)
    sv_base = sv_min + sv_max if (sv_min is not None and sv_max is not None) else None
    c.check_rel('vertical_stress', sv_base, exp['vertical_stress_base_kPa'], tol['vertical_stress_rel'], True,
                'min-principal stress: most compressive %s + least compressive %s = stress at the base, kPa' % (
                    '%.5g' % sv_min if sv_min is not None else 'n/a', '%.5g' % sv_max if sv_max is not None else 'n/a'))
    ratio = sh_min / sv_min if (sh_min is not None and sv_min not in (None, 0.0)) else None
    c.check_rel('k0_ratio', ratio, exp['k0'], tol['k0_rel'], True,
                'most compressive max-principal stress %s / most compressive min-principal stress %s' % (
                    '%.5g' % sh_min if sh_min is not None else 'n/a', '%.5g' % sv_min if sv_min is not None else 'n/a'))

    c.load_result({'max_displacement_m': 'number', 'vertical_stress_base_kPa': 'number',
                   'horizontal_stress_base_kPa': 'number'})
    rep_u = c.result.get('max_displacement_m')
    if rep_u is None:
        c.add('report_max_displacement_m', False, True, None, u_lim, 'upper bound',
              'no valid "max_displacement_m" in result.json')
    elif umax is None:
        c.add('report_max_displacement_m', False, True, rep_u, u_lim, 'upper bound', c._missing_note(''))
    else:
        # both numbers are essentially zero in a correct solution: require the same order of smallness
        ok = abs(rep_u) <= u_lim and umax <= u_lim or abs(abs(rep_u) - umax) <= 0.03 * umax
        c.add('report_max_displacement_m', ok, True, rep_u, umax, 'upper bound %g or 3 %% of the ODB value' % u_lim,
              'reported %.3g against max |U| = %.3g in the ODB' % (rep_u, umax))
    sv_ip = cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=False)
    sh_ip = cl.invariant(ex, 'S', 'maxPrincipal', 'min', nodal=False)
    c.report('vertical_stress_base_kPa',
             [('base value from the linear profile', sv_base), ('most compressive stress at element nodes', sv_min),
              ('most compressive stress at integration points', sv_ip)], tol['report_rel'], critical=True)
    k0 = exp['k0']
    c.report('horizontal_stress_base_kPa',
             [('K0 x base value from the linear profile', k0 * sv_base if sv_base is not None else None),
              ('most compressive horizontal stress at element nodes', sh_min),
              ('most compressive horizontal stress at integration points', sh_ip)], tol['report_rel'], critical=False)
    rv, rh = c.result.get('vertical_stress_base_kPa'), c.result.get('horizontal_stress_base_kPa')
    c.add('report_stress_sign', rv is not None and rh is not None and rv < 0.0 and rh < 0.0, False, [rv, rh],
          'negative (compression)', None, 'the task asks for compressive stresses to be reported as negative numbers')


if __name__ == '__main__':
    cl.main(__file__, body)
