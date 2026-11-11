# -*- coding: utf-8 -*-
"""Checker for d05_sdof_seismic_time_history.   py check.py <workdir>

The task fixes the geometry: column bases at (0, 0) and (6, 0), roof at y = 4 m, excitation along x.  Read from the
field output of the dynamic step (the last general step; its start is t = 0 of the record):
  U1, A1 of the node nearest to the roof joint (0, 4, 0)  and of the node nearest to the base (0, 0, 0).

  probes                        both nodes within 0.2 m of the prescribed points
  duration                      results up to the end of the record (15 s, 1 % slack)
  output_interval               frames no more than 0.01 s apart (5 % slack)
  peak_relative_displacement    largest |U1_roof - U1_base| = 0.029150 m, +-3 %
  peak_absolute_acceleration    largest absolute roof acceleration = 3.5193 m/s2, +-3 %
The expected values are the exact response of the 5 % damped SDOF system (k = 2 x 12EI/h^3 = 1500 kN/m, m = 12.5 t)
to the piecewise-linear record of the task asset (reference/make_record.py).

Two ways of applying the earthquake are understood:
  * moving base (acceleration / displacement boundary condition): A1 of the roof is already absolute;
  * fixed base with the inertia force -m a_g(t): the base does not move, and the ground acceleration - read here from
    the task's own copy of the record, in g x 9.8 - is added to A1 of the roof.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def read_record(path):
    """PEER-like file -> (dt, [acceleration in g])"""
    with open(path) as f:
        lines = f.read().splitlines()
    dt, start = None, 0
    for i, line in enumerate(lines[:10]):
        up = line.upper()
        if 'NPTS' in up and 'DT' in up:
            dt = float(up.split('DT=')[1].split()[0])
            start = i + 1
            break
    vals = []
    for line in lines[start:]:
        vals.extend(float(x) for x in line.split())
    return dt, vals


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U,A', probes=[exp['roof_point'], exp['base_point']], series='U,A')

    d = [cl.num((cl.probe_location(ex, i).get('node') or {}).get('distance')) for i in (0, 1)]
    worst = max(d) if None not in d else None
    c.check_max('probes', worst, tol['probe_distance_m'], True,
                'distance of the nearest nodes from the roof joint (0, 4, 0) and the base (0, 0, 0): %s' % d)
    ok = worst is not None and worst <= tol['probe_distance_m']

    def rows(i, var, comp):
        return cl.step_rows(ex, cl.probe_series(ex, i, var, comp)) if ok else []

    u_roof, u_base = rows(0, 'U', 'U1'), rows(1, 'U', 'U1')
    a_roof = rows(0, 'A', 'A1')
    if ex is not None and ok and (len(u_roof) < 10 or len(u_roof) != len(u_base)):
        c.odb_error = 'no U1 history of roof and base in the dynamic step'
        u_roof = u_base = []
    duration = u_roof[-1][0] if u_roof else None
    interval = max(u_roof[i + 1][0] - u_roof[i][0] for i in range(len(u_roof) - 1)) if u_roof else None
    c.check_min('duration', duration, exp['duration_s'] * (1 - tol['duration_slack']), True,
                'time covered by the roof history in step "%s"' % ((ex or {}).get('frame') or {}).get('step'))
    c.check_max('output_interval', interval, exp['max_output_interval_s'] * (1 + tol['interval_slack']), True,
                'largest spacing of the frames (%d frames)' % len(u_roof))

    peak_rel = max(abs(r[1] - b[1]) for r, b in zip(u_roof, u_base)) if u_roof else None
    peak_abs_u = max(abs(r[1]) for r in u_roof) if u_roof else None
    moving = bool(u_base) and max(abs(b[1]) for b in u_base) > 1e-7
    c.check_rel('peak_relative_displacement', peak_rel, exp['peak_relative_displacement_m'], tol['response_rel'],
                True, 'largest |U1 roof - U1 base| (ODB); the base %s' % (
                    'moves (largest |U1| of the roof itself %.4g m)' % peak_abs_u if moving else 'is fixed'))

    peak_acc, how = None, ''
    if a_roof and len(a_roof) == len(u_roof):
        if moving or not u_roof:
            peak_acc, how = max(abs(v) for _t, v in a_roof), 'A1 of the roof (moving base: absolute)'
        else:
            dt, acc_g = read_record(os.path.join(c.task_dir, 'assets', exp['record_file']))
            g = exp['g_m_s2']

            def ground(t):
                x = t / dt
                i = int(x)
                g0 = acc_g[i] if 0 <= i < len(acc_g) else 0.0
                g1 = acc_g[i + 1] if 0 <= i + 1 < len(acc_g) else 0.0
                return g * (g0 + (g1 - g0) * (x - i))
            peak_acc = max(abs(v + ground(t)) for t, v in a_roof)
            how = 'A1 of the roof + ground acceleration of the record (fixed base: relative formulation)'
    elif ex is not None and c.odb_error is None:
        c.odb_error = 'no A1 (acceleration) history of the roof in the dynamic step'
    c.check_rel('peak_absolute_acceleration', peak_acc, exp['peak_absolute_acceleration_m_s2'], tol['response_rel'],
                True, how or 'absolute roof acceleration')
    why = c.odb_error
    c.odb_error = None

    c.load_result({'peak_relative_displacement_m': 'number', 'peak_absolute_acceleration_m_s2': 'number'})
    c.odb_error = why
    c.report('peak_relative_displacement_m', [('largest |U1 roof - U1 base|', peak_rel)], tol['report_rel'],
             critical=True)
    c.report('peak_absolute_acceleration_m_s2', [('largest absolute roof acceleration', peak_acc)],
             tol['report_rel'], critical=True)
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
