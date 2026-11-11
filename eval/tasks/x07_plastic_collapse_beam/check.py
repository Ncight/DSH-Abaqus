# -*- coding: utf-8 -*-
"""Checker for x07_plastic_collapse_beam.   py check.py <workdir>

The task fixes the axes: beam along x, supports at x = 0 and x = 2 m, point load at midspan in -y.

  collapse_load     load carried in the LAST frame of the ODB = 4 Mp / L = 62.5 kN, +-4 %.  The load is the sum of
                    the support reactions: the larger of (sum of the positive, sum of the negative) nodal RF2, which
                    is right for displacement control (supports +P, loaded nodes -P) and for force control (supports
                    +P only).  Single nodal values are deliberately NOT used: under force control the largest nodal
                    reaction is one support, i.e. half the load - and half of the built-in beam's 8 Mp / L is
                    exactly the expected 4 Mp / L.  (Supports whose nodal reactions have both signs - a clamping
                    patch - inflate the sum; such a support is not a simple support anyway.)
  plateau_reached   the load has stopped growing while the beam keeps deflecting: between the frame at 85 % of the
                    step time and the last frame the load changes by <= 2 % and the largest |U2| grows by >= 5 %.
                    A force-controlled run that stops below (or just below) the collapse load, an elastic or a
                    hardening material and a too small prescribed deflection all fail here.
  elastic_stiffness (not critical) load / midspan deflection in the first increment = 48 EI / L^3 = 5.0e6 N/m
                    (4.96e6 with shear deformation), +-3 %; only when the node at (0, 0, 0) is a support and the
                    first increment is elastic.
Converged FE values: linear beam elements give 4 Mp / (L - le): +1.0 % with 100 elements, +5.3 % with 20;
50 B22 +0.85 %; plane-stress continuum (CPS4I 200 x 10) +2.5 %; both ends built in 8 Mp / L (+100 %); section
turned by 90 degrees -50 %.
A model that covers only half the span in x (bounding box of the mesh shorter than 0.6 L) is taken as a symmetry
model: its reactions are doubled.  result.json must give the load of the whole beam in any case.
(Note for whoever reads a failed job_completed here: a full model of linear beam elements with a node at midspan
stops with a numerical singularity as soon as the two elements next to the load are fully plastic - see the
reference solve.py for the remedies.)

result.json: collapse_load_N must agree with the ODB (3 %, critical); first_yield_load_N is compared with the
analytical 4 My / L = 41 667 N (+-5 %, not critical - the ODB cannot confirm it without knowing the increments).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402

SUPPORTS = 'sum of the support reactions (larger of the sums of positive / negative nodal RF2)'


def symmetry_factor(ex, span):
    """(2, extent) when the model covers only half the span in x (a symmetry model), else (1, extent)"""
    try:
        lo, hi = ex['bbox']
        extent = float(hi[0]) - float(lo[0])
    except Exception:
        return 1.0, None
    return (2.0 if extent < 0.6 * span else 1.0), extent


def load_candidates(ex, factor=1.0):
    out = []
    d = cl.sums(ex, 'RF').get(2)
    if d is not None:
        out.append((SUPPORTS, factor * max(d['pos'], abs(d['neg']))))
    return out


def deflection(ex):
    r = cl.comp_range(ex, 'U').get('U2')
    return max(abs(r[0]), abs(r[1])) if r is not None else None


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='RF,U', probes=[exp['midspan_point'], exp['support_point']], series='U,RF')

    factor, extent = symmetry_factor(ex, exp['span_m'])
    cands = load_candidates(ex, factor)
    lab, p_last = cands[0] if cands else ('', None)
    sym = ''
    if factor != 1.0:
        sym = ' | the model spans only %.3g m in x: taken as a half (symmetry) model, reactions x 2' % extent
    c.check_rel('collapse_load', p_last, exp['collapse_load_N'], tol['collapse_rel'], True,
                'load in the last frame (ODB): ' + (SUPPORTS if cands else 'no RF output') + sym)

    # ---- plateau: compare with the frame at 85 % of the step time
    w_series = cl.probe_series(ex, 0, 'U', 'U2')
    k, how = (None, 'no ODB data') if ex is None else cl.frame_at_fraction(ex, w_series, tol['plateau_fraction'])
    w_last = deflection(ex)
    p_a = w_a = None
    if k is not None:
        ex2 = c.extract(vars='RF,U', step=ex['frame']['step'], frame=k)
        p_a = dict(load_candidates(ex2, factor)).get(lab)
        w_a = deflection(ex2)
    name = 'plateau_reached'
    want = 'load change <= %g %% and deflection growth >= %g %% after %g %% of the step' % (
        100 * tol['plateau_load_rel'], 100 * tol['plateau_growth'], 100 * tol['plateau_fraction'])
    if None in (p_last, p_a, w_last, w_a) or not p_last or not w_a:
        c.add(name, False, True, None, want, None, 'NOT EVALUATED: ' + (c.odb_error or how))
    else:
        dp, dw = (p_last - p_a) / p_last, (w_last - w_a) / w_a
        ok = abs(dp) <= tol['plateau_load_rel'] and dw >= tol['plateau_growth']
        c.add(name, ok, True, {'load_change': dp, 'deflection_growth': dw}, want, None,
              '%s: load %.6g -> %.6g N, largest |U2| %.5g -> %.5g m' % (how, p_a, p_last, w_a, w_last))
    c.odb_error = None

    # ---- elastic stiffness from the first increment (beam-type models with a support node at the origin)
    rf_series = cl.probe_series(ex, 1, 'RF', 'RF2')
    node = cl.probe_location(ex, 1).get('node') or {}
    stiff, why = None, ''
    try:
        st = cl.steps(ex)
        i = [s.get('name') for s in st].index(ex['frame']['step'])
        off = sum(int(s['nFrames']) for s in st[:i])
        n = int(st[i]['nFrames'])
        same_len = len(rf_series) == len(w_series) == sum(int(s['nFrames']) for s in st)
        if not same_len or n < 3 or cl.num(node.get('distance')) is None or node['distance'] > 0.02:
            why = 'no support node at (0, 0, 0) with an RF history, or too few frames'
        else:
            r1, r_end = rf_series[off + 1][1], rf_series[off + n - 1][1]
            w1 = abs(w_series[off + 1][1])
            if not r_end or not w1 or p_last is None:
                why = 'the node at (0, 0, 0) carries no reaction'
            else:
                p1 = p_last * r1 / r_end
                if p1 > 0.8 * exp['first_yield_load_N']:
                    why = 'the first increment (%.4g N) is not safely elastic' % p1
                else:
                    stiff = p1 / w1
    except Exception as e:
        why = 'not available (%s)' % type(e).__name__
    if stiff is None and ex is not None:
        c.odb_error = why
    c.check_rel('elastic_stiffness', stiff, exp['elastic_stiffness_N_per_m'], tol['stiffness_rel'], False,
                'load / midspan deflection in the first increment (ODB)')
    c.odb_error = None

    c.load_result({'collapse_load_N': 'number', 'first_yield_load_N': 'number'})
    c.report('collapse_load_N', cands, tol['report_rel'], critical=True)
    rep = c.result.get('first_yield_load_N')
    if rep is None:
        c.add('report_first_yield_load_N', False, False, None, exp['first_yield_load_N'], tol['first_yield_rel'],
              'no valid "first_yield_load_N" in result.json')
    else:
        c.check_rel('report_first_yield_load_N', rep, exp['first_yield_load_N'], tol['first_yield_rel'], False,
                    'reported value against the analytical 4 My / L (the ODB cannot confirm it)', use_abs=True)


if __name__ == '__main__':
    cl.main(__file__, body)
