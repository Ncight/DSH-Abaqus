# -*- coding: utf-8 -*-
"""Checker for d03_strip_footing_bearing_capacity.   py check.py <workdir>

The task fixes the axes: ground surface y = 0, soil below, footing centred on x = 0 (0 <= |x| <= 1 m), pushed in -y.

Footing load read from the nodal reactions RF2 of a frame:
  S  = larger of (sum of the positive, |sum of the negative|) nodal RF2 - the vertical force that goes through the
       model: reaction of the pushed footing nodes, or of the supports under force control;
  N1 = largest single nodal |RF2|.
  If one node carries at least 90 % of S (the footing is driven through a reference point) the load is N1 - exact;
  otherwise it is S (displacement prescribed on the footing nodes themselves, or force control).
  A mesh whose smallest x coordinate is about 0 is a half (symmetry) model: the load is doubled.
  result.json must give the load of the whole footing per metre run in any case.

  collapse_load     load in the last frame within +-7 % of Prandtl's (2 + pi) cu B = 1028.3 kN/m.
                    What reasonable meshes give with 0.125 m elements around the footing (reference solve.py):
                    CPE8RH +0.4 %, CPE6M +0.8 %, CPE8R +2.5 % (smooth footing) / +4.8 % (rough), CPE8 +4.4 %,
                    CPE4R -4.9 %, CPE4 / CPE4H -6.3 % (with linear elements the footing effectively ends half an
                    element short).  0.25 m CPE8R: +4.9 %; 0.5 m: +8.8 % (fails).  Hence 7 %.
  plateau_reached   between the frame at 85 % of the step time and the last frame the load changes by <= 2 % while
                    the largest settlement grows by >= 5 %  (same criterion as x07)
Typical wrong results: Mises yield stress 2 cu instead of sqrt(3) cu (+15.5 % on top of the mesh error), linear
triangles with a nearly incompressible soil (volumetric locking: +51 % and no plateau), coarse mesh, a soil layer
thinner than the failure mechanism, an axisymmetric model (circular footing), pushing too little, force control.
(Not wrong: a soil block as small as 1.5 x 1 m with roller sides gives the same load within 0.3 % - the mechanism
under a smooth footing is that local.)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def model_load(ex):
    """-> (load carried by the modelled part of the footing, how it was read)"""
    d = cl.sums(ex, 'RF').get(2)
    r = cl.comp_range(ex, 'RF').get('RF2')
    if d is None or r is None:
        return None, 'no RF output'
    s = max(d['pos'], abs(d['neg']))
    n1 = max(abs(r[0]), abs(r[1]))
    if s > 0.0 and n1 >= 0.9 * s:
        return n1, 'reaction of a single node (reference point of the footing) %.6g; sum %.6g' % (n1, s)
    return s, 'sum of the nodal reactions %.6g (largest single node %.6g)' % (s, n1)


def half_model_factor(ex):
    try:
        lo, hi = ex['bbox']
        x0, x1 = float(lo[0]), float(hi[0])
    except Exception:
        return 1.0, ''
    if x0 >= -0.01 * max(abs(x1), 1.0):
        return 2.0, ' | the mesh starts at x = %.3g: half (symmetry) model, load x 2' % x0
    return 1.0, ''


def settlement(ex):
    r = cl.comp_range(ex, 'U').get('U2')
    return max(abs(r[0]), abs(r[1])) if r is not None else None


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='RF,U', probes=[exp['footing_point']], series='U')

    factor, sym = half_model_factor(ex)
    p_model, how = model_load(ex) if ex is not None else (None, '')
    p_last = factor * p_model if p_model is not None else None
    ref = exp['ultimate_load_kN_per_m']
    c.check_range('collapse_load', p_last, ref * (1 - tol['collapse_below']), ref * (1 + tol['collapse_above']), True,
                  'footing load per metre in the last frame (ODB): %s%s | Prandtl %.6g, %s' % (
                      how, sym, ref, '%+.2f %%' % (100.0 * (p_last / ref - 1.0)) if p_last is not None else 'n/a'))

    series = cl.probe_series(ex, 0, 'U', 'U2')
    k, note = (None, 'no ODB data') if ex is None else cl.frame_at_fraction(ex, series, tol['plateau_fraction'])
    w_last = settlement(ex)
    p_a = w_a = None
    if k is not None:
        ex2 = c.extract(vars='RF,U', step=ex['frame']['step'], frame=k)
        pm, _how = model_load(ex2) if ex2 is not None else (None, '')
        p_a = factor * pm if pm is not None else None
        w_a = settlement(ex2)
    want = 'load change <= %g %% and settlement growth >= %g %% after %g %% of the step' % (
        100 * tol['plateau_load_rel'], 100 * tol['plateau_growth'], 100 * tol['plateau_fraction'])
    if None in (p_last, p_a, w_last, w_a) or not p_last or not w_a:
        c.add('plateau_reached', False, True, None, want, None, 'NOT EVALUATED: ' + (c.odb_error or note))
    else:
        dp, dw = (p_last - p_a) / p_last, (w_last - w_a) / w_a
        c.add('plateau_reached', abs(dp) <= tol['plateau_load_rel'] and dw >= tol['plateau_growth'], True,
              {'load_change': dp, 'settlement_growth': dw}, want, None,
              '%s: load %.6g -> %.6g kN/m, largest |U2| %.5g -> %.5g m' % (note, p_a, p_last, w_a, w_last))
    c.odb_error = None

    c.load_result({'ultimate_load_kN_per_m': 'number'})
    c.report('ultimate_load_kN_per_m', [('footing load per metre in the last frame', p_last)], tol['report_rel'],
             critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
