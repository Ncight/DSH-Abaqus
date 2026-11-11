# -*- coding: utf-8 -*-
"""Checker for t06_selfweight_units.   py check.py <workdir>

The model has to be built in the m-t-s system (kN, kPa, t/m3); the ODB numbers are checked in those units.

Physics (from the ODB, orientation independent):
  base_reaction        |net reaction| = 245 kN +-1 %   (an N / Pa / kg model gives 245000)
  top_settlement       max |U| = 4.0833e-5 m +-3 %
  base_stress          smallest min-principal stress at element nodes = -245 kPa +-6 %
  free_lateral_expansion   the base must only be held vertically (plus the minimum against rigid-body motion):
                       then every reaction is vertical.  Measured as sum of |horizontal nodal reactions| /
                       |net vertical reaction| (vertical = the global axis that carries the weight), which is 0
                       for the specified supports and clearly non-zero when the base is fully fixed (the
                       restrained Poisson expansion needs self-equilibrated horizontal reactions).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S,U,RF')

    rf = cl.vec_sum_mag(ex, 'RF')
    c.check_rel('base_reaction', rf, exp['base_reaction_kN'], tol['base_reaction_rel'], True,
                '|sum of RF over all nodes| in the ODB, must be in kN')
    umax = cl.max_mag(ex, 'U')
    c.check_rel('top_settlement', umax, exp['top_settlement_m'], tol['top_settlement_rel'], True, 'max |U| in the ODB')
    s_min = cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=True)
    c.check_rel('base_stress', s_min, exp['base_stress_kPa'], tol['base_stress_rel'], True,
                'smallest min-principal stress at element nodes (ODB), must be in kPa')

    # horizontal reactions: components other than the one that carries the weight.  (The vertical component itself
    # is not usable for this: second-order elements give base nodes vertical reactions of both signs.)
    s = cl.sums(ex, 'RF')
    ratio = None
    note = 'sum of |horizontal nodal reactions| / |net vertical reaction|; 0 when the base is free to expand'
    if s and rf:
        vertical = max(s, key=lambda ax: abs(s[ax]['sum']))
        lateral = sum(d['pos'] - d['neg'] for ax, d in s.items() if ax != vertical)
        ratio = lateral / rf
        note += ' (vertical = axis %d, horizontal reactions add up to %.4g)' % (vertical, lateral)
    c.check_max('free_lateral_expansion', ratio, tol['lateral_reaction_ratio_max'], True, note)

    c.load_result({'base_reaction_kN': 'number', 'top_settlement_m': 'number', 'base_stress_kPa': 'number'})
    c.report('base_reaction_kN', [('|sum RF|', rf)], tol['report_rel'], critical=True)
    c.report('top_settlement_m', [('max |U|', umax), ('max |U component|', cl.comp_absmax(ex, 'U'))],
             tol['report_rel'], critical=True)
    area = exp['section_area_m2']
    c.report('base_stress_kPa',
             [('min principal at element nodes', s_min),
              ('min principal at integration points', cl.invariant(ex, 'S', 'minPrincipal', 'min', nodal=False)),
              ('-|sum RF| / area', -rf / area if rf is not None else None)],
             tol['report_stress_rel'], critical=False, signed=True,
             note='compression must be reported as a negative number')


if __name__ == '__main__':
    cl.main(__file__, body)
