# -*- coding: utf-8 -*-
"""Checker for t05_hertz_contact.   py check.py <workdir>

Unit system of the model.  The contact patch is 0.25 mm in radius; a model drawn in metres needs ~1e-5 m elements,
which is at the limit of the geometric precision of Abaqus/CAE (the reference shows the free mesher collapsing
elements there).  Working in N - mm - MPa is the usual way out, so task.md allows the ODB to be in SI (m, N, Pa) or
in N - mm - MPa.  The checker recognises the system from the magnitude of CPRESS (the two differ by 1e6) and converts
to Pa; the force is in newtons in both.  result.json must be in SI either way.

Physics (from the ODB):
  max_contact_pressure   largest CPRESS within +-8 % of the Hertz peak pressure p0 = 3.7211e9 Pa.
  contact_force          force transmitted through the contact = 500 N +-3 %.  It is read from the reaction forces
                         along the pressing direction (the axis with the largest displacement):
                           - force-driven model (net reaction clearly non-zero): |net reaction|, exact by equilibrium;
                           - displacement-driven model (net reaction ~ 0, the driven nodes and the support carry
                             +F and -F): sum of the positive reactions, |sum of the negative ones| or the largest
                             single nodal reaction (reference node of a rigid plane), whichever matches best.
                         In an axisymmetric model Abaqus reports nodal forces for the full circumference, so the
                         value is the total force (verified with the reference).  A 3-D model that uses 1 or 2
                         symmetry planes carries F/2 or F/4; that is accepted (the factor is stated in the note)
                         because the pressure check already pins the total force: p0 ~ F^(1/3), so a total force that
                         is off by a factor 2 moves the peak pressure by 21-26 %.
  frictionless           (non-critical) no contact shear stress in the ODB.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='CPRESS,CSHEAR1,CSHEAR2,RF,U')

    p_odb = cl.scalar(ex, 'CPRESS', 'max')
    p_exp = exp['max_contact_pressure_Pa']
    p_max, system = None, 'unknown'
    if p_odb is not None and p_odb > 0.0:
        # SI model: CPRESS in Pa.  N-mm-MPa model: CPRESS in MPa.  Take the system whose expectation is nearer.
        system, to_pa = min((('SI (m, N, Pa)', 1.0), ('N-mm-MPa', 1.0e6)),
                            key=lambda u: abs(math.log10(p_odb * u[1] / p_exp)))
        p_max = p_odb * to_pa
    if ex is not None and p_odb is None:
        c.odb_error = 'no CPRESS field in the last frame of the ODB (available: %s)' % ', '.join(
            cl.available_fields(ex))
    elif ex is not None and p_max is None:
        c.odb_error = 'CPRESS is zero everywhere in the last frame: nothing is in contact'
    c.check_rel('max_contact_pressure', p_max, p_exp, tol['max_contact_pressure_rel'], True,
                'largest CPRESS in the ODB = %s, read as %s, in Pa, against the Hertz peak pressure' % (
                    '%.6g' % p_odb if p_odb is not None else 'n/a', system))
    if ex is not None:
        c.odb_error = None

    # transmitted force along the pressing direction, with the symmetry factor of the model
    f_exp = exp['contact_force_N']
    axis = cl.dominant_axis(ex, 'U')
    s = cl.sums(ex, 'RF').get(axis) if axis else None
    types = cl.element_types(ex)
    three_d = any(t.upper().startswith(('C3D', 'R3D', 'SC', 'S3', 'S4', 'S8')) for t in types)
    f_model = f_total = None
    k = 1
    if s:
        gross = max(s['pos'], abs(s['neg']))
        rng = cl.comp_range(ex, 'RF').get('RF%d' % axis)
        node_max = max(abs(rng[0]), abs(rng[1])) if rng else None
        if abs(s['sum']) > 0.02 * gross:
            # force-driven: the supports balance the applied force, the net reaction IS the transmitted force
            cands = [('|net reaction|', abs(s['sum']))]
        else:
            # displacement-driven: driven nodes and support carry +F and -F.  The sum of one sign is F unless a
            # driven face of second-order elements has nodal reactions of both signs; then the single largest
            # nodal reaction (reference node of the rigid plane / of a coupling) is the cleaner measure.
            cands = [('sum of positive reactions', s['pos']), ('|sum of negative reactions|', abs(s['neg']))]
            if node_max is not None:
                cands.append(('largest single nodal reaction', node_max))
        factors = (1, 2, 4) if three_d else (1,)
        lab, f_model, k = min(((lb, v, q) for lb, v in cands for q in factors), key=lambda t: abs(t[2] * t[1] - f_exp))
        f_total = k * f_model
        note = ('reaction forces along axis %d (largest displacement): net %.5g, positive %.5g, negative %.5g, largest '
                'nodal %s -> %s = %.5g carried by the model; symmetry factor %d' % (
                    axis, s['sum'], s['pos'], s['neg'], '%.5g' % node_max if node_max is not None else 'n/a',
                    lab, f_model, k))
    else:
        note = 'reaction forces (RF) along the pressing direction'
        if ex is not None:
            c.odb_error = 'no RF / U field in the last frame of the ODB'
    c.check_rel('contact_force', f_total, f_exp, tol['contact_force_rel'], True, note)
    if ex is not None:
        c.odb_error = None

    shear = max([abs(v) for v in (cl.scalar(ex, 'CSHEAR1', 'max'), cl.scalar(ex, 'CSHEAR1', 'min'),
                                  cl.scalar(ex, 'CSHEAR2', 'max'), cl.scalar(ex, 'CSHEAR2', 'min'))
                 if v is not None] or [0.0])
    if ex is None or p_odb is None or p_odb <= 0.0:
        c.check_max('frictionless', None, tol['shear_to_pressure_max'], False, 'max |CSHEAR| / max CPRESS')
    else:
        c.check_max('frictionless', shear / p_odb, tol['shear_to_pressure_max'], False,
                    'max |CSHEAR| / max CPRESS (no CSHEAR output counts as zero)')

    c.load_result({'contact_force_N': 'number', 'max_contact_pressure_Pa': 'number', 'contact_radius_m': 'number'})
    c.report('max_contact_pressure_Pa', [('max CPRESS converted to Pa', p_max)], tol['report_rel'], critical=True)
    c.report('contact_force_N', [('transmitted force x symmetry factor', f_total),
                                 ('force carried by the model', f_model)], tol['report_rel'], critical=True)
    # the contact radius cannot be taken from the extractor's summary: compare with the Hertz value
    a_rep = c.result.get('contact_radius_m')
    a_exp, a_tol = exp['contact_radius_m'], tol['contact_radius_rel']
    if a_rep is None:
        c.add('report_contact_radius_m', False, False, None, a_exp, a_tol, 'no valid "contact_radius_m" in result.json')
    else:
        err = (abs(a_rep) - a_exp) / a_exp
        c.add('report_contact_radius_m', abs(err) <= a_tol, False, a_rep, a_exp, a_tol,
              'reported vs Hertz contact radius: %+.2f%% (not derivable from the ODB summary)' % (100.0 * err))


if __name__ == '__main__':
    cl.main(__file__, body)
