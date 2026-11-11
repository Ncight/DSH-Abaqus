# -*- coding: utf-8 -*-
"""Checker for d02_terzaghi_consolidation.   py check.py <workdir>

The task fixes the geometry: soil column 0 <= y <= 10 m, base at y = 0 (impervious), surface at y = 10 m (drained,
loaded).  Read from the field output of ALL frames of the ODB (total time; the surcharge acts from t = 0):
  POR at the node nearest to (0, 0, 0)   - pore pressure at the base
  U2  at the node nearest to (0, 10, 0)  - surface settlement
Excess pore pressure = POR(t) - POR(first frame), so a model that carries a hydrostatic pressure is read correctly.

  probes                 both nodes within 1 m of the prescribed points
  frame_at_tv            a frame at t = 145 600 s (Tv = 0.2), within 0.5 %
  initial_excess_pressure  largest excess pore pressure at the base = the surcharge, 100 kPa +-3 %
                         (undrained response to a suddenly applied load)
  excess_pore_pressure_base  at Tv = 0.2: 0.7723 x 100 = 77.23 kPa, +-3 %
  settlement_at_tv       0.5041 x 0.074286 = 0.037446 m, +-3 %
  final_settlement       last frame: q H / M = 0.074286 m, +-2 %  (M = constrained modulus 13 461.5 kPa);
                         this also requires the analysis to run until consolidation is practically complete
Typical wrong models: specific weight of water 1 or 9800 instead of 9.8 (cv off by 9.8 / 1000), permeability in the
wrong time unit, both ends drained (base pressure 0), no drainage, sides free (final settlement +22 %), load ramped.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='POR,U', probes=[exp['base_point'], exp['surface_point']], series='POR,U')

    d = [cl.num((cl.probe_location(ex, i).get('node') or {}).get('distance')) for i in (0, 1)]
    worst = max(d) if None not in d else None
    c.check_max('probes', worst, tol['probe_distance_m'], True,
                'distance of the nearest nodes from (0, 0, 0) and (0, 10, 0): %s' % d)
    ok = worst is not None and worst <= tol['probe_distance_m']
    por = cl.probe_series(ex, 0, 'POR', 'POR') if ok else []
    u2 = cl.probe_series(ex, 1, 'U', 'U2') if ok else []
    if ex is not None and ok and (len(por) < 3 or len(u2) < 3):
        c.odb_error = 'no POR history at the base / no U history at the surface (pore pressure elements? output?)'
        por, u2 = [], []

    t_tv = exp['time_at_tv_s']

    def nearest(series):
        return min(range(len(series)), key=lambda i: abs(series[i][0] - t_tv)) if series else None

    k_p, k_u = nearest(por), nearest(u2)
    miss = None
    if k_p is not None and k_u is not None:
        miss = max(abs(por[k_p][0] - t_tv), abs(u2[k_u][0] - t_tv)) / t_tv
    c.check_max('frame_at_tv', miss, tol['frame_time_rel'], True,
                'relative distance of the nearest frame from t = %.6g s (Tv = 0.2)' % t_tv)
    at_tv = miss is not None and miss <= tol['frame_time_rel']
    if ex is not None and por and not at_tv and c.odb_error is None:
        c.odb_error = 'no frame at t = %.6g s' % t_tv

    p0 = por[0][1] if por else None
    excess = [(t, v - p0) for t, v in por]
    peak = max(v for _t, v in excess) if excess else None
    c.check_rel('initial_excess_pressure', peak, exp['surcharge_kPa'], tol['pressure_rel'], True,
                'largest excess pore pressure at the base over all frames (POR minus its value in the first frame'
                ' %s)' % ('%.4g' % p0 if p0 is not None else 'n/a'))
    u_tv = excess[k_p][1] if at_tv else None
    s_tv = abs(u2[k_u][1]) if at_tv else None
    c.check_rel('excess_pore_pressure_base', u_tv, exp['excess_pore_pressure_base_kPa'], tol['pressure_rel'], True,
                'excess pore pressure at the base at Tv = 0.2 (ODB)')
    c.check_rel('settlement_at_tv', s_tv, exp['settlement_at_tv_m'], tol['settlement_rel'], True,
                '|U2| of the surface at Tv = 0.2 (ODB)')
    if c.odb_error and c.odb_error.startswith('no frame at'):
        c.odb_error = None
    s_end = abs(u2[-1][1]) if u2 else None
    c.check_rel('final_settlement', s_end, exp['final_settlement_m'], tol['final_rel'], True,
                '|U2| of the surface in the last frame (t = %s s); excess pore pressure left at the base: %s kPa' % (
                    '%.4g' % u2[-1][0] if u2 else 'n/a', '%.3g' % excess[-1][1] if excess else 'n/a'))
    why = c.odb_error
    c.odb_error = None

    c.load_result({'excess_pore_pressure_base_kPa': 'number', 'settlement_at_tv_m': 'number',
                   'final_settlement_m': 'number'})
    c.odb_error = why or (None if at_tv or ex is None else 'no frame at t = %.6g s' % t_tv)
    c.report('excess_pore_pressure_base_kPa', [('excess pore pressure at the base at Tv = 0.2', u_tv)],
             tol['report_rel'], critical=True)
    c.report('settlement_at_tv_m', [('surface settlement at Tv = 0.2', s_tv)], tol['report_rel'], critical=True)
    c.odb_error = why
    c.report('final_settlement_m', [('surface settlement in the last frame', s_end)], tol['report_rel'],
             critical=True)
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
