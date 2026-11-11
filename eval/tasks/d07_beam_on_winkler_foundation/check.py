# -*- coding: utf-8 -*-
"""Checker for d07_beam_on_winkler_foundation.   py check.py <workdir>

The task fixes the axes: beam along x from 0 to 40 m, point load at x = 20 m acting in -y.

  deflection_under_load   largest |U2| of the model = P lambda / (2k) = 3.7047e-3 m, +-3 %
                          (the deflection line of a long beam on an elastic foundation peaks under the load)
  moment_under_load       P / (4 lambda) = 351.46 kNm, +-4 %
How the moment is read.  The bending moment has a sharp peak at the load (it falls by 250 kNm per metre on either
side), and Abaqus stores section moments at the integration points, not at the nodes: the largest stored value is
2 % low with 0.25 m cubic elements (B23), 4 % with 0.5 m, and 9 % low with 0.25 m linear ones (B21, one value per
element).  The checker takes the best estimate of the moment AT the load point that the ODB offers, the larger of
  (a) the section moment extrapolated to the element nodes by Abaqus (ELEMENT_NODAL), if its extreme sits at the load
      point - for cubic and quadratic beam elements this is the moment of the model at the node (160 B23: -0.1 %,
      40 B23 of 1 m: -2 %);
  (b) the mean section moments of the two beam elements next to the load on one side (read through a row of probe
      points from 16 m to 24 m; the +x side, or the -x side when only the left half is modelled), extrapolated
      linearly to the load point - the better value for linear elements, whose moment is constant within an element
      (400 B21: -0.5 %, 160 B21: -0.9 %, shear flexibility included).
Both approach the peak from below, so the larger one is the better one.  A mesh that cannot resolve the peak fails
this check - as it should (20 B23 of 2 m: -9 %).
The reported moment has to agree with this value within 4 %: the largest integration-point moment of a reasonably
fine cubic mesh does (B23 0.25 m: -1.9 %, 0.5 m: -3.7 %, 0.625 m: -4.5 %), the raw element moment of linear elements
does not unless the mesh is very fine (160 B21: -8.2 %) - it is not the moment under the load.

A shear-flexible (Timoshenko) beam gives a 1 - 2 % larger deflection and a 1 % smaller moment for this section: inside
the tolerances, so B21 / B22 elements are accepted although the task speaks of Euler-Bernoulli theory.
Typical wrong results: subgrade modulus not multiplied by the beam width (deflection +15 %, moment +5 %), nodal
springs not multiplied by the node spacing (deflection -65 %), a beam much shorter than 40 m.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402

OFFSETS = ([0.0125 * j for j in range(1, 41)] + [0.5 + 0.05 * j for j in range(1, 21)] +
           [1.5 + 0.25 * j for j in range(1, 11)])
AT_LOAD_M = 0.01            # an extreme "at the load point": within 1 cm of x = 20 m


def element_nodal_peak(ex, comp, x_load):
    """largest |section moment| among the values Abaqus extrapolates to the element nodes (fields.SM.elementNodal of
    the extractor; the bending component SM1 / SM2 is labelled SM11 / SM22 there) - or None when the extractor has no
    such block or the extreme is not at the load point"""
    try:
        comps = ex['fields']['SM']['elementNodal']['components'] or {}
    except Exception:
        return None
    for label in (comp, {'SM1': 'SM11', 'SM2': 'SM22'}.get(comp)):
        info = comps.get(label) if label else None
        if not isinstance(info, dict):
            continue
        lo, hi = cl.num(info.get('min')), cl.num(info.get('max'))
        if lo is None or hi is None:
            continue
        value, where = (abs(lo), info.get('minAt')) if abs(lo) >= abs(hi) else (abs(hi), info.get('maxAt'))
        try:
            x = cl.num(where['coords'][0])
        except Exception:
            x = None
        if x is not None and abs(x - x_load) <= AT_LOAD_M and value > 0.0:
            return value
    return None


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    x_load = exp['load_point'][0]
    # a row of probe points on either side of the load (a half model may be the right or the left half)
    probes = [[x_load + dx, 0.0, 0.0] for dx in OFFSETS] + [[x_load - dx, 0.0, 0.0] for dx in OFFSETS]
    ex = c.extract(vars='U,SM', probes=probes, series='SM')

    u2 = cl.comp_range(ex, 'U').get('U2')
    w0 = max(abs(u2[0]), abs(u2[1])) if u2 is not None else None
    c.check_rel('deflection_under_load', w0, exp['deflection_under_load_m'], tol['deflection_rel'], True,
                'largest |U2| in the last frame (ODB)')

    # ---- mean section moment of the beam elements next to the load, per side: {element: (distance, {SM1, SM2})}
    sides = {1: {}, -1: {}}
    for i in range(len(probes)):
        try:
            pr = ex['probes'][i]
            el = pr['element']
            ser = pr['series']['SM']
            row = ser['rows'][-1]
            cols = ser['columns']
        except Exception:
            continue
        xc = cl.num(el.get('centroid', [None])[0])
        if xc is None or abs(xc - x_load) < 1e-9:
            continue
        vals = dict((lab, cl.num(v)) for lab, v in zip(cols[1:], row[1:]) if lab in ('SM1', 'SM2'))
        vals = dict((k, v) for k, v in vals.items() if v is not None)
        if vals:
            sides[1 if xc > x_load else -1][(el.get('instance'), el.get('label'))] = (abs(xc - x_load), vals)
    found = sorted(list(sides[1].values()) + list(sides[-1].values()), key=lambda dv: dv[0])
    # the bending component: the larger one in the element nearest to the load
    comp = max(found[0][1], key=lambda k: abs(found[0][1][k])) if found else None
    m_x, notes = None, []
    for side in (1, -1):
        near = sorted((d, v[comp]) for d, v in sides[side].values() if comp in v)[:2]
        if len(near) == 2 and near[1][0] > near[0][0]:
            (da, ma), (db, mb) = near
            m_x = abs(ma + (ma - mb) * da / (db - da))
            notes.append('%s of the two elements next to the load on the %s side, centred %.4g and %.4g m from it '
                         '(%.5g and %.5g kNm), extrapolated to the load point: %.5g' % (
                             comp, '+x' if side > 0 else '-x', da, db, ma, mb, m_x))
            break
    if m_x is None and ex is not None:
        c.odb_error = ('no section moments SM of beam elements next to the load (x = %.4g ... %.4g m): beam '
                       'elements with SM field output are required' % (x_load - OFFSETS[-1], x_load + OFFSETS[-1]))
    m_en = element_nodal_peak(ex, comp, x_load)
    if m_en is not None:
        notes.append('%s extrapolated to the element nodes by Abaqus, extreme at the load point: %.5g' % (comp, m_en))
    estimates = [v for v in (m_x, m_en) if v is not None]
    m0 = max(estimates) if (m_x is not None and estimates) else None
    sm = cl.comp_range(ex, 'SM')
    m_ip = max([max(abs(v[0]), abs(v[1])) for k, v in sm.items() if k in ('SM1', 'SM2')] or [0.0]) or None
    how = 'bending moment under the load'
    if notes:
        how = 'moment at the load point = the larger of: ' + '; '.join(notes)
        if m_ip is not None:
            how += ' (largest value stored at an integration point: %.5g)' % m_ip
    c.check_rel('moment_under_load', m0, exp['moment_under_load_kNm'], tol['moment_rel'], True, how)
    why = c.odb_error
    c.odb_error = None

    c.load_result({'deflection_under_load_m': 'number', 'moment_under_load_kNm': 'number'})
    c.report('deflection_under_load_m', [('largest |U2|', w0)], tol['report_rel'], critical=True)
    c.odb_error = why
    c.report('moment_under_load_kNm', [('moment at the load point', m0)], tol['report_moment_rel'], critical=True)
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
