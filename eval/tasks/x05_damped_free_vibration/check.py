# -*- coding: utf-8 -*-
"""Checker for x05_damped_free_vibration.   py check.py <workdir>

The task fixes the geometry: cantilever along +x from the origin, tip at (1, 0, 0), static tip force in y, then a
sudden release.  Everything is read from the U2 history of the node nearest to (1, 0, 0), taken from the field output
of ALL steps of the ODB (static loading step + direct-integration dynamic step):

  tip_probe                a node within 0.02 m of (1, 0, 0)
  static_deflection        largest |U2| of the history = P L^3 / 3EI = 1.1905e-3 m, +-3 %
  free_vibration_duration  at least 0.6 s of results after the release
  output_interval          largest interval between U frames after the release <= T1/20 (2.99 ms, 5 % slack)
  first_frequency          natural frequency of the decay, 16.71 Hz +-2 %
  damping_ratio            0.02 +-10 %  (logarithmic decrement 0.1257)
The last two come from a least-squares fit of exp(-a t)(A cos wt + B sin wt) to all samples after the release
(checklib.free_decay_fit), zeta = a / sqrt(a^2 + w^2): no peak picking, so the sampling of the peaks and the 2.5 %
ripple of the second mode (which mass-proportional damping hardly damps) do not matter.
Typical wrong results: no damping -> zeta ~ 0;  alpha or beta computed with f in Hz instead of rad/s -> zeta = 0.32 %
or 12.6 %;  quasi-static (backward Euler) time integration -> zeta ~ 4.6 %;  load left on, or ramped down over the
step -> no oscillation about zero at all.

result.json: first_frequency_Hz and damping_ratio must agree with the fit (3 % / 15 %);  the damping ratio implied
by the reported Rayleigh coefficients at the measured frequency, alpha/(2w) + beta w/2, must agree with the measured
one (15 %) - this ties the reported coefficients to what the model actually does.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U', probes=[exp['tip_point']], series='U')

    node = cl.probe_location(ex, 0).get('node') or {}
    dist = cl.num(node.get('distance'))
    c.check_max('tip_probe', dist, tol['probe_distance_m'], True,
                'distance between (1, 0, 0) and the nearest node of the model%s' % (
                    ' (node %s at %s)' % (node.get('label'), [round(float(x), 4) for x in node.get('coords', [])])
                    if node else ''))
    series = cl.probe_series(ex, 0, 'U', 'U2')
    if dist is None or dist > tol['probe_distance_m']:
        series = []
    fit = cl.free_decay_fit(series) if series else {'ok': False, 'peak': None,
                                                    'reason': 'no U2 history at a node at the tip (1, 0, 0)'}
    if ex is not None and not series:
        c.odb_error = fit['reason']
    c.check_rel('static_deflection', fit.get('peak'), exp['static_deflection_m'], tol['static_rel'], True,
                'largest |U2| of the tip history over all steps (ODB)')
    t1 = 1.0 / exp['first_frequency_Hz']
    # 0.5 % slack: with very dense output the first few frames after the release still count as the plateau
    # (the deflection needs 0.4 ms to fall below 99.9 % of its largest value)
    c.check_min('free_vibration_duration', fit.get('duration'), exp['min_free_vibration_s'] * (1 - 5e-3), True,
                'time covered by U frames after the release (release at total time %s)' % (
                    '%.4g' % fit['t_release'] if fit.get('t_release') is not None else 'n/a'))
    c.check_max('output_interval', fit.get('max_interval'), t1 / 20.0 * (1 + tol['interval_slack']), True,
                'largest spacing of the U frames after the release; required <= T1/20 = %.4g s (%d frames)' % (
                    t1 / 20.0, fit.get('n_samples') or 0))
    if ex is not None and series and not fit['ok']:
        c.odb_error = fit['reason']
    info = ''
    if fit['ok']:
        info = ' | fit of %d samples, residual %.1f %% of the signal, %d zero crossings' % (
            fit['n_samples'], 100.0 * fit['rms'], fit['n_crossings'])
    c.check_rel('first_frequency', fit.get('freq_n'), exp['first_frequency_Hz'], tol['frequency_rel'], True,
                'natural frequency of the tip decay (ODB)' + info)
    c.check_rel('damping_ratio', fit.get('zeta'), exp['damping_ratio'], tol['damping_rel'], True,
                'damping ratio of the tip decay (ODB); logarithmic decrement %s' % (
                    '%.4f' % fit['log_dec'] if fit.get('log_dec') is not None else 'n/a'))
    why = c.odb_error
    c.odb_error = None

    c.load_result({'first_frequency_Hz': 'number', 'damping_ratio': 'number',
                   'rayleigh_alpha': 'number', 'rayleigh_beta': 'number'})
    c.odb_error = why
    c.report('first_frequency_Hz', [('natural frequency of the decay', fit.get('freq_n')),
                                    ('damped frequency of the decay', fit.get('freq_d'))],
             tol['report_rel'], critical=True)
    zeta = fit.get('zeta')
    small = 0.1 * exp['damping_ratio']
    rep_z = c.result.get('damping_ratio')
    if rep_z is not None and zeta is not None and abs(rep_z) < small and abs(zeta) < small:
        # an (almost) undamped response: 1e-6 against -5e-5 is agreement, not a 100 % error
        c.add('report_damping_ratio', True, True, rep_z, zeta, tol['report_damping_rel'],
              'reported and measured damping ratio both negligible (< %.3g)' % small)
    else:
        c.report('damping_ratio', [('damping ratio of the decay', zeta)], tol['report_damping_rel'], critical=True)

    ra, rb = c.result.get('rayleigh_alpha'), c.result.get('rayleigh_beta')
    rtol = tol['report_rayleigh_rel']
    if ra is None or rb is None:
        c.add('report_rayleigh', False, True, None, zeta, rtol,
              'no valid "rayleigh_alpha" / "rayleigh_beta" in result.json')
    elif zeta is None:
        c.add('report_rayleigh', False, True, [ra, rb], None, rtol, c._missing_note(''))
    else:
        w = 2.0 * math.pi * fit['freq_n']
        implied = ra / (2.0 * w) + rb * w / 2.0
        if abs(zeta) < small and abs(implied) < small:
            ok, txt = True, 'both negligible'
        else:
            err = (implied - zeta) / abs(zeta) if zeta else float('inf')
            ok, txt = abs(err) <= rtol, '%+.1f%%' % (100.0 * err)
        c.add('report_rayleigh', ok, True, implied, zeta, rtol,
              'damping ratio implied by the reported coefficients at the measured frequency, alpha/(2w) + beta w/2 '
              '= %.5f, against %.5f from the decay: %s' % (implied, zeta, txt))
    c.odb_error = None

    pairs, note = cl.inp_rayleigh(c.path(c.job + '.inp'))
    if ra is None or rb is None:
        c.add('report_rayleigh_inp', False, False, None, pairs or None, 0.01, 'no reported coefficients')
    elif not pairs:
        c.add('report_rayleigh_inp', abs(ra) < 1e-12 and abs(rb) < 1e-12, False, [ra, rb], None, 0.01,
              note + ' (the reported coefficients can only be confirmed when both are zero)')
    else:
        def close(x, y):
            return abs(x - y) <= 0.01 * max(abs(x), abs(y)) or max(abs(x), abs(y)) < 1e-12
        c.add('report_rayleigh_inp', any(close(ra, a) and close(rb, b) for a, b in pairs), False, [ra, rb],
              [list(p) for p in pairs], 0.01, note)


if __name__ == '__main__':
    cl.main(__file__, body)
