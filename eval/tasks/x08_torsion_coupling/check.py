# -*- coding: utf-8 -*-
"""Checker for x08_torsion_coupling.   py check.py <workdir>

The task prescribes a reference point coupled to the whole free end face of the shaft, with the torque applied at
that reference point.  From the last frame of the ODB:

  twist_angle       rotation of the reference point = largest rotation magnitude |UR| in the model (solid elements
                    have no rotational degrees of freedom, so the reference point is the only node with UR):
                    T L / (G J) = 0.012315 rad, +-2 %.  Without any UR output (torque applied as nodal forces) the
                    twist is taken as the largest |U| divided by the nominal radius 0.02 m.
  max_shear_stress  largest von Mises stress / sqrt(3) = T r / J = 3.9789e7 Pa, +-4 %.  Element-nodal (extrapolated)
                    values are used when the extractor provides them.  In pure torsion the stress is the same in
                    every cross-section, so the maximum over the model is the surface value - unless the torque is
                    introduced through a few nodes, which shows up here as a stress concentration.
                    A linear reduced-integration element reports the stress at its centroid: the outer ring of
                    elements must be thinner than 8 % of the radius to get within 4 %, or quadratic elements used.
  applied_torque    (not critical) resultant of the concentrated moments CM, or else the largest reaction moment RM,
                    500 Nm +-1 %; only when the ODB has CM / RM output.
Typical wrong results: radius 0.04 instead of 0.02 -> twist / 16, stress / 8;  moment about a transverse axis ->
bending, rotation 0.0095 rad;  torque from rim forces with the diameter as lever arm -> half the twist.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,UR,S,RF,RM,CF,CM')

    ur = cl.max_mag(ex, 'UR')
    umax = cl.max_mag(ex, 'U')
    twist_cands = []
    if ur is not None:
        twist_cands.append(('rotation of the reference point (largest |UR|)', ur))
    elif umax is not None:
        twist_cands.append(('largest |U| / nominal radius (no UR output in the ODB)', umax / exp['radius_m']))
    twist = twist_cands[0][1] if twist_cands else None
    c.check_rel('twist_angle', twist, exp['twist_angle_rad'], tol['twist_rel'], True,
                (twist_cands[0][0] if twist_cands else 'twist') + ' (ODB); largest |U| = %s m' % (
                    '%.5g' % umax if umax is not None else 'n/a'))

    mises_n = cl.invariant(ex, 'S', 'mises', 'max', nodal=True)
    mises_ip = cl.invariant(ex, 'S', 'mises', 'max', nodal=False)
    tresca_n = cl.invariant(ex, 'S', 'tresca', 'max', nodal=True)
    tau = mises_n / math.sqrt(3.0) if mises_n is not None else None
    c.check_rel('max_shear_stress', tau, exp['max_shear_stress_Pa'], tol['stress_rel'], True,
                'largest von Mises stress %s / sqrt(3) (ODB; at integration points: %s)' % (
                    '%.5g' % mises_n if mises_n is not None else 'n/a',
                    '%.5g' % mises_ip if mises_ip is not None else 'n/a'))

    torque, how = None, ''
    if cl.has_field(ex, 'CM'):
        torque, how = cl.vec_sum_mag(ex, 'CM'), 'resultant of the concentrated moments CM'
    if not torque and cl.has_field(ex, 'RM'):
        torque, how = cl.comp_absmax(ex, 'RM'), 'largest reaction moment component RM'
    if ex is not None and not torque:
        torque = None
        c.odb_error = 'no concentrated moment (CM) or reaction moment (RM) in the ODB'
    c.check_rel('applied_torque', torque, exp['torque_Nm'], tol['torque_rel'], False, how or 'applied torque')
    if ex is not None:
        c.odb_error = None

    c.load_result({'twist_angle_rad': 'number', 'max_shear_stress_Pa': 'number'})
    c.report('twist_angle_rad', twist_cands + [('largest |U| / nominal radius', umax / exp['radius_m']
                                                 if umax is not None else None)], tol['report_rel'], critical=True)
    s3 = math.sqrt(3.0)
    c.report('max_shear_stress_Pa',
             [('Mises / sqrt(3) at element nodes', tau),
              ('Mises / sqrt(3) at integration points', mises_ip / s3 if mises_ip is not None else None),
              ('Tresca / 2 at element nodes', tresca_n / 2.0 if tresca_n is not None else None)],
             tol['report_stress_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
