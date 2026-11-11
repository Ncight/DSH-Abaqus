# -*- coding: utf-8 -*-
"""Checker for x04_bimetal_thermal.   py check.py <workdir>

The task fixes the coordinates: strip along +x from the clamp at x = 0, thickness along y, layer A (stiff, small
expansion) at y < 0, layer B at y > 0.  Heated by 100 K the strip bends towards layer A, i.e. the tip moves in -y.

Physics (from the last frame of the ODB, 2-D plane stress or 3-D solid, any mesh):
  tip_deflection   the U2 component of largest magnitude in the model, WITH its sign: -kappa L^2 / 2 = -2.909e-3 m
                   (Timoshenko's bimetal formula, kappa = 0.58182 1/m), +-4 %.  The deflection of a cantilever of
                   constant curvature grows monotonically towards the tip, so the extreme U2 is the tip deflection;
                   the thickness expansion of the layers adds only 2e-6 m.
                   Converged references: 2-D plane stress -2.916e-3 (+0.25 %), 3-D solid -2.976e-3 (+2.3 %: the
                   clamped end face suppresses the transverse curvature over the first few millimetres) - hence 4 %
                   and not 3 %.  Wrong idealisations land far outside: plane strain -3.79e-3 (+30 %), 120 K instead
                   of 100 K (no stress-free temperature defined) -3.50e-3 (+20 %), one reduced-integration element
                   per layer -3.97e-3 (+36 %), layers not joined or equal expansion ~0, layers the other way round
                   +2.916e-3.
  (The deflection does not depend on which layer is the stiffer one - the formula is symmetric in the modular ratio
   for equal thicknesses - so exchanged moduli cannot be detected from the deflection.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,S')

    u2 = cl.comp_range(ex, 'U').get('U2')
    tip = None
    if u2 is not None:
        tip = u2[0] if abs(u2[0]) >= abs(u2[1]) else u2[1]
    if ex is not None and tip is None:
        c.odb_error = 'no displacement component U2 in the last frame of the ODB'
    c.check_rel('tip_deflection', tip, exp['tip_deflection_m'], tol['deflection_rel'], True,
                'U2 of largest magnitude in the last frame, with sign (ODB); U2 range %s' % (
                    '[%.5g, %.5g]' % (u2[1], u2[0]) if u2 is not None else 'n/a'))
    if ex is not None:
        c.odb_error = None

    c.load_result({'tip_deflection_m': 'number'})
    c.report('tip_deflection_m', [('largest |U2|', tip)], tol['report_rel'], critical=True)
    rep = c.result.get('tip_deflection_m')
    same_sign = rep is not None and tip is not None and rep * tip > 0.0
    c.add('report_sign', same_sign, False, rep, tip, None,
          'the task asks for the signed U2 of the tip (the direction of bending)')


if __name__ == '__main__':
    cl.main(__file__, body)
