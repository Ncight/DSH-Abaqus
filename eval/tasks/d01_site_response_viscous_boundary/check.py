# -*- coding: utf-8 -*-
"""Checker for d01_site_response_viscous_boundary.   py check.py <workdir>

The task fixes the geometry: soil column 0 <= y <= 50 m (surface at y = 50), horizontal motion along x.  Everything
is read from the horizontal velocity history of the surface node nearest to (0, 50, 0), taken from the field output
of the dynamic step (the last general step of the ODB; its start is t = 0 of the wavelet):

  surface_probe    a node within 3 m of (0, 50, 0)
  duration         results up to t = 1.5 s
  output_interval  V frames no more than 5 ms apart (5 % slack)
  peak_velocity    largest |V1| before t = 1.0 s = 2 x 0.1 m/s = 0.2 m/s, +-3 %  (free surface doubles the incident
                   wave)
  peak_time        t0 + H/Vs = 0.30 + 0.25 = 0.55 s, +-0.01 s
  late_residual    largest |V1| for t >= 1.0 s, as a fraction of the peak, <= 0.05: with a correct absorbing base the
                   wave leaves the model for good (the reference gives 0.1 - 0.5 %)
Typical wrong models: prescribed base motion (rigid base) - first arrival correct, but the wave is trapped (residual
~1);  equivalent force with factor 1 - peak 0.1;  force without dashpot - peak 0.4 and trapped;  dashpot with Vp -
peak 0.139, residual 0.30;  per-node values not scaled by the tributary area - peak 0.133, residual 0.33;  column
free to bend - no resemblance at all.

If the ODB has no V output at the surface the velocity is obtained from U by central differences (noted).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='V,U', probes=[exp['surface_point']], series='V,U')

    node = cl.probe_location(ex, 0).get('node') or {}
    dist = cl.num(node.get('distance'))
    c.check_max('surface_probe', dist, tol['probe_distance_m'], True,
                'distance between (0, 50, 0) and the nearest node%s' % (
                    ' (node %s at %s)' % (node.get('label'), [round(float(x), 3) for x in node.get('coords', [])])
                    if node else ''))

    t_start, how = cl.step_start(ex), 'V1 field output'
    series = cl.probe_series(ex, 0, 'V', 'V1')
    if not series:
        u = cl.probe_series(ex, 0, 'U', 'U1')
        series = [(u[i][0], (u[i + 1][1] - u[i - 1][1]) / (u[i + 1][0] - u[i - 1][0]))
                  for i in range(1, len(u) - 1) if u[i + 1][0] > u[i - 1][0]]
        how = 'no V output: central differences of U1'
    if dist is None or dist > tol['probe_distance_m'] or t_start is None:
        series = []
    hist = [(t - t_start, v) for t, v in series if t >= t_start - 1e-9] if series else []
    if ex is not None and len(hist) < 5:
        c.odb_error = 'no horizontal velocity (or displacement) history at the surface in the dynamic step'
        hist = []

    duration = hist[-1][0] if hist else None
    interval = max(hist[i + 1][0] - hist[i][0] for i in range(len(hist) - 1)) if hist else None
    c.check_min('duration', duration, exp['total_time_s'] * (1 - 5e-3), True,
                'time covered by the surface history in step "%s"' % ((ex or {}).get('frame') or {}).get('step'))
    c.check_max('output_interval', interval, exp['max_output_interval_s'] * (1 + tol['interval_slack']), True,
                'largest spacing of the frames (%d frames, %s)' % (len(hist), how))

    peak = t_peak = late = None
    # the peak is looked for before t = 1.0 s (the direct wave); what comes after that is the "late" window.
    # (If a reflecting base sends the wave back with undiminished amplitude, the global maximum could just as well
    #  be one of the later arrivals.)
    early = [i for i in range(len(hist)) if hist[i][0] < exp['late_from_s'] - 1e-9]
    if early:
        k = max(early, key=lambda i: abs(hist[i][1]))
        t_peak, peak = hist[k][0], abs(hist[k][1])
        tail = [abs(v) for t, v in hist if t >= exp['late_from_s'] - 1e-9]
        if tail and peak > 0.0:
            late = max(tail) / peak
    c.check_rel('peak_velocity', peak, exp['peak_surface_velocity_m_s'], tol['peak_rel'], True,
                'largest |V1| at the surface (ODB, %s)' % how)
    t_exp, dt = exp['time_of_peak_s'], tol['peak_time_abs_s']
    c.check_range('peak_time', t_peak, t_exp - dt, t_exp + dt, True,
                  'time of the largest surface velocity, counted from the start of the dynamic step')
    if ex is not None and hist and late is None:
        c.odb_error = 'no frames at t >= %.3g s' % exp['late_from_s']
    c.check_max('late_residual', late, tol['late_residual_max'], True,
                'largest |V1| for t >= %.3g s divided by the peak' % exp['late_from_s'])
    why = c.odb_error
    c.odb_error = None

    c.load_result({'peak_surface_velocity_m_s': 'number', 'time_of_peak_s': 'number', 'late_residual_ratio': 'number'})
    c.odb_error = why
    c.report('peak_surface_velocity_m_s', [('largest |V1| at the surface', peak)], tol['report_rel'], critical=True)
    for key, got, lim, what in (('time_of_peak_s', t_peak, tol['report_time_abs_s'], 'time of the peak'),
                                ('late_residual_ratio', late, tol['report_residual_abs'], 'late residual ratio')):
        rep = c.result.get(key)
        name = 'report_' + key
        if rep is None:
            c.add(name, False, True, None, got, lim, 'no valid "%s" in result.json' % key)
        elif got is None:
            c.add(name, False, True, rep, None, lim, c._missing_note(''))
        else:
            c.add(name, abs(abs(rep) - got) <= lim, True, rep, got, lim,
                  'reported %s against the ODB: difference %+.4g (allowed %.3g)' % (what, abs(rep) - got, lim))
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
