# -*- coding: utf-8 -*-
"""Checker for x06_friction_slip.   py check.py <workdir>

The task fixes the axes and the way the plane is modelled: rigid plane y = 0 (normal +y), a rigid body whose
reference point is fixed; block pushed 1 mm in +x by a prescribed displacement.  Everything is read from the last
frame of the ODB.

  normal_force         normal reaction of the plane, 10 kN +-1 %
  sliding_force        tangential reaction of the plane = force needed to keep the block sliding, mu N = 3 kN +-2 %
  friction_ratio       sliding_force / normal_force = 0.3 +-2 %
  push_distance        largest |U1| in the model = 1 mm +-5 % (the prescribed push; the far side of the block moves
                       up to 2 % more because the compressed block bulges; a force-controlled or a tiny push
                       does not get there)
  contact_stress_ratio (not critical) largest |CSHEAR| / largest CPRESS = 0.3: every node of a sliding contact is
                       at the Coulomb limit
  slip_distance        (not critical) largest |CSLIP| >= 0.5 mm

How the reactions are measured.  A rigid plane held at its reference point carries the whole reaction in ONE node,
so the largest single nodal |RF1| / |RF2| of the model is the tangential / normal contact force, whatever elements
the block is made of.  Sums over the nodes must NOT be used for the tangential force: the pushed face is held flat
while the compressed block wants to bulge, which gives self-equilibrated nodal reactions of both signs on that face
(+-414 N in the reference model, even without any friction).  The net sum of RF2 is not used for the normal force
either: it only balances the applied pressure and would hide a block that is also held vertically somewhere else.
A model without a rigid plane held at one reference node therefore fails normal_force and sliding_force - the task
asks for that way of modelling in so many words.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def single_node(ex, axis):
    r = cl.comp_range(ex, 'RF').get('RF%d' % axis)
    if r is None:
        return []
    return [('largest single nodal |RF%d| (reference point of the rigid plane)' % axis, max(abs(r[0]), abs(r[1])))]


def sum_info(ex, axis):
    d = cl.sums(ex, 'RF').get(axis)
    if d is None:
        return 'no RF output'
    return 'for information: nodal RF%d sum of positive %.5g, sum of negative %.5g, net %.5g' % (
        axis, d['pos'], d['neg'], d['sum'])


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,RF,CPRESS,CSHEAR1,CSHEAR2,CSLIP1,CSLIP2')

    cn, ct = single_node(ex, 2), single_node(ex, 1)
    normal = cn[0][1] if cn else None
    tang = ct[0][1] if ct else None

    c.check_rel('normal_force', normal, exp['normal_force_N'], tol['normal_rel'], True,
                'normal reaction of the plane: largest single nodal |RF2| (ODB) | ' + sum_info(ex, 2))
    c.check_rel('sliding_force', tang, exp['sliding_force_N'], tol['sliding_rel'], True,
                'tangential reaction of the plane: largest single nodal |RF1| (ODB) | ' + sum_info(ex, 1))
    ratio = tang / normal if (tang is not None and normal) else None
    c.check_rel('friction_ratio', ratio, exp['friction_coefficient'], tol['ratio_rel'], True,
                'sliding_force / normal_force')

    u1 = cl.comp_range(ex, 'U').get('U1')
    push = max(abs(u1[0]), abs(u1[1])) if u1 is not None else None
    c.check_rel('push_distance', push, exp['push_m'], tol['push_rel'], True,
                'largest |U1| in the last frame (ODB): the block has to be pushed 1 mm')

    cpress = cl.scalar(ex, 'CPRESS', 'max')
    shear = [v for v in (cl.scalar(ex, 'CSHEAR1', 'max'), cl.scalar(ex, 'CSHEAR1', 'min'),
                         cl.scalar(ex, 'CSHEAR2', 'max'), cl.scalar(ex, 'CSHEAR2', 'min')) if v is not None]
    cshear = max(abs(v) for v in shear) if shear else None
    if ex is not None and (cpress is None or cshear is None):
        c.odb_error = 'no CPRESS / CSHEAR output in the last frame (contact output CSTRESS not requested, or no contact)'
    c.check_rel('contact_stress_ratio', cshear / cpress if (cshear is not None and cpress) else None,
                exp['friction_coefficient'], tol['contact_ratio_rel'], False,
                'largest |CSHEAR| %s / largest CPRESS %s' % (
                    '%.5g' % cshear if cshear is not None else 'n/a', '%.5g' % cpress if cpress is not None else 'n/a'))
    slips = [v for v in (cl.scalar(ex, 'CSLIP1', 'max'), cl.scalar(ex, 'CSLIP1', 'min'),
                         cl.scalar(ex, 'CSLIP2', 'max'), cl.scalar(ex, 'CSLIP2', 'min')) if v is not None]
    slip = max(abs(v) for v in slips) if slips else None
    if ex is not None:
        c.odb_error = None if slip is not None else 'no CSLIP output in the last frame (contact output CDISP)'
    c.check_min('slip_distance', slip, tol['min_slip_m'], False,
                'largest accumulated slip |CSLIP| on the contact surface')
    if ex is not None:
        c.odb_error = None

    c.load_result({'normal_force_N': 'number', 'sliding_force_N': 'number'})
    c.report('normal_force_N', cn, tol['report_rel'], critical=True)
    c.report('sliding_force_N', ct, tol['report_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
