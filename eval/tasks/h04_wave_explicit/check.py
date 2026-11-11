# -*- coding: utf-8 -*-
"""Checker for h04_wave_explicit.   py check.py <workdir>

The task fixes the coordinates of the bar (0 <= x <= 1 m, axis = x), so the ODB is probed at fixed points:
the extractor returns the S11 history of the element whose centroid is nearest to each point.

Physics (from the ODB):
  explicit_procedure     the (last) step is an Abaqus/Explicit dynamic step, as the task demands
  analysis_time          total analysis time 1.5e-4 s +-1 %
  stress_behind_front    S11 at x = 0.25 m at the end of the analysis = -rho c v = -4.0e7 Pa +-6 % (signed: compression).
                         The last frame or the mean over the last quarter of the analysis is used, whichever is closer,
                         so that bulk-viscosity ringing right behind the front does not decide the verdict.
  quiet_ahead_of_front   |S11| at x = 0.9 m in the last frame below 10 % of 4.0e7 Pa (the front is at x = 0.75 m)
  arrival_time_mid       (non-critical, needs enough output frames) time at which |S11| at x = 0.5 m reaches half of
                         4.0e7 Pa, linearly interpolated between frames = 1.0e-4 s +-6 %
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    probes = [exp['probe_behind_m'], exp['probe_ahead_m'], exp['probe_mid_m']]
    ex = c.extract(vars='S', probes=probes, series='S')
    sigma = exp['stress_behind_front_Pa']

    st = cl.steps(ex)
    proc = str(st[-1].get('procedure', '')) if st else None
    if ex is None:
        c.add('explicit_procedure', False, True, None, '*DYNAMIC, EXPLICIT', None, 'NOT EVALUATED: ' + str(c.odb_error))
    else:
        c.add('explicit_procedure', bool(proc) and 'EXPLICIT' in proc.upper(), True, proc, '*DYNAMIC, EXPLICIT', None,
              'procedure of the last step in the ODB')
    total = None
    if st and cl.num(st[-1].get('timePeriod')) is not None:
        total = (cl.num(st[-1].get('totalTime')) or 0.0) + cl.num(st[-1].get('timePeriod'))
    c.check_rel('analysis_time', total, exp['analysis_time_s'], tol['analysis_time_rel'], True,
                'total time at the end of the last step (ODB)')

    def series(i, what):
        """S11 history at probe i, or [] with the reason stored for the notes"""
        s = cl.probe_series(ex, i, 'S', 'S11')
        loc = cl.probe_location(ex, i).get('element') or {}
        dist = cl.num(loc.get('distance'))
        if ex is not None and not s:
            c.odb_error = 'no S11 history at the %s probe (is S written to the ODB?)' % what
            return [], loc
        if s and (dist is None or dist > tol['probe_distance_max_m']):
            c.odb_error = ('no element near the %s probe point %s (nearest element centroid %s): the bar is not '
                           'at the coordinates the task prescribes' % (what, probes[i], loc.get('centroid')))
            return [], loc
        return s, loc

    # --- plateau behind the front, x = 0.25 m
    s25, loc25 = series(0, 'x = 0.25 m')
    got = None
    note = 'S11 of the element nearest to %s' % (exp['probe_behind_m'],)
    if s25:
        t_end = s25[-1][0]
        last = s25[-1][1]
        tail = [v for t, v in s25 if t >= 0.75 * t_end]
        mean_tail = sum(tail) / len(tail)
        got = min((last, mean_tail), key=lambda v: abs(v - sigma))
        note += ' (centroid %s): last frame %.5g, mean over the last quarter (%d frames) %.5g; %d frames in all' % (
            [round(x, 4) for x in loc25.get('centroid', [])], last, len(tail), mean_tail, len(s25))
    c.check_rel('stress_behind_front', got, sigma, tol['stress_behind_front_rel'], True, note)
    if ex is not None:
        c.odb_error = None

    # --- nothing has arrived at x = 0.9 m
    s90, loc90 = series(1, 'x = 0.9 m')
    ahead = abs(s90[-1][1]) if s90 else None
    c.check_max('quiet_ahead_of_front', ahead, tol['quiet_ahead_fraction_max'] * abs(sigma), True,
                '|S11| in the last frame at the element nearest to %s' % (exp['probe_ahead_m'],))
    if ex is not None:
        c.odb_error = None

    # --- arrival at mid-length (50 % level), needs a reasonably fine frame interval
    s50, _loc50 = series(2, 'x = 0.5 m')
    t50 = cl.crossing_time(s50, 0.5 * abs(sigma)) if s50 else None
    t25 = cl.crossing_time(s25, 0.5 * abs(sigma)) if s25 else None
    if ex is not None and s50 and t50 is None:
        c.odb_error = '|S11| at x = 0.5 m never reaches half of the expected plateau'
    dt = (s50[-1][0] - s50[0][0]) / (len(s50) - 1) if len(s50) > 1 else None
    c.check_rel('arrival_time_mid', t50, exp['arrival_time_at_mid_s'], tol['arrival_time_rel'], False,
                'time at which |S11| at x = 0.5 m reaches 2.0e7 Pa, interpolated between frames'
                + (' (frame interval %.3g s)' % dt if dt else ''))
    if ex is not None:
        c.odb_error = None

    c.load_result({'wave_speed_m_s': 'number', 'stress_behind_front_Pa': 'number', 'arrival_time_at_mid_s': 'number'})
    c.report('stress_behind_front_Pa', [('S11 plateau at x = 0.25 m', got)], tol['report_stress_rel'], critical=True)
    rep = c.result.get('stress_behind_front_Pa')
    c.add('report_stress_sign', rep is not None and rep < 0.0, False, rep, 'negative (compression)', None,
          'the task asks for compression to be reported as a negative stress')
    speeds = []
    if t50 and t25 and t50 > t25:
        speeds.append(('0.25 m / (t(0.5 m) - t(0.25 m))', 0.25 / (t50 - t25)))
    if t50:
        speeds.append(('0.5 m / t(0.5 m)', 0.5 / t50))
    if ex is not None and not speeds:
        c.odb_error = 'arrival times could not be derived from the ODB frames'
    c.report('wave_speed_m_s', speeds, tol['report_wave_speed_rel'], critical=False)
    c.report('arrival_time_at_mid_s', [('50 % arrival at x = 0.5 m', t50)], tol['report_arrival_time_rel'],
             critical=False)


if __name__ == '__main__':
    cl.main(__file__, body)
