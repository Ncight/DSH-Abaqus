# -*- coding: utf-8 -*-
"""Checker for x03_cable_prestress_modes.   py check.py <workdir>

Physics (from the ODB, any orientation, 2-D or 3-D, beam or truss elements):
  frequency_1..3   the first three DISTINCT eigenfrequencies of the frequency step, each within +-2 % of the taut-string
                   values 9.005 / 18.01 / 27.02 Hz.  Frequencies closer than 0.5 % are merged (a 3-D model has every
                   transverse mode in two planes) and modes below 0.05 Hz are skipped (an unrestrained spin of a beam
                   model about its own axis is a rigid-body mode, not a transverse mode).  A 3-D model therefore needs
                   at least six extracted modes.  Without stress stiffening the wire has only its bending frequencies
                   (0.20 / 0.79 / 1.78 Hz) or, as a truss, none at all.
  tension          axial stress in the last frame of the general step that precedes the frequency step, times the
                   nominal wire area pi d^2 / 4 = 7.854e-5 m2: 20 kN +-2 %.  No general step, no tension.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='S')

    freqs = cl.frequencies(ex)
    dist = cl.distinct_frequencies(freqs, tol['merge_rel'], tol['rigid_body_below_Hz'])
    n = len(exp['frequencies_Hz'])
    if ex is not None:
        if not freqs:
            c.odb_error = 'the ODB contains no eigenfrequencies (no *FREQUENCY step results)'
        elif len(dist) < n:
            c.odb_error = ('only %d distinct frequencies among the %d extracted modes %s - extract more modes' % (
                len(dist), len(freqs), [round(f, 4) for f in freqs[:8]]))
    for i, f_exp in enumerate(exp['frequencies_Hz']):
        c.check_rel('frequency_%d' % (i + 1), dist[i] if i < len(dist) else None, f_exp, tol['frequency_rel'], True,
                    'distinct frequency no. %d of the frequency step (ODB); all extracted: %s' % (
                        i + 1, [round(f, 3) for f in freqs[:8]]))
    if ex is not None:
        c.odb_error = None

    sigma = cl.invariant(ex, 'S', 'mises', 'max', nodal=False) if cl.frame_is_general_step(ex) else None
    tension = sigma * exp['area_m2'] if sigma is not None else None
    if ex is not None and tension is None:
        c.odb_error = ('no stress output from a general (pretension) step before the frequency step: the frequency '
                       'extraction is not based on a stressed state, or S was not written')
    c.check_rel('tension', tension, exp['tension_N'], tol['tension_rel'], True,
                'largest axial (Mises) stress at the end of the pretension step x pi d^2/4')
    if ex is not None:
        c.odb_error = None

    c.load_result({'frequencies_Hz': ('list', n), 'tension_N': 'number'})
    rep = c.result.get('frequencies_Hz')
    name = 'report_frequencies_Hz'
    if rep is None:
        c.add(name, False, True, None, dist[:n] or None, tol['report_rel'],
              'no valid "frequencies_Hz" (list of %d numbers) in result.json' % n)
    elif len(dist) < n:
        c.add(name, False, True, rep, dist or None, tol['report_rel'],
              'NOT EVALUATED: fewer than %d distinct eigenfrequencies in the ODB' % n)
    else:
        errs = [(r - f) / f if f else float('inf') for r, f in zip(sorted(rep), dist[:n])]
        c.add(name, max(abs(e) for e in errs) <= tol['report_rel'], True, rep, dist[:n], tol['report_rel'],
              'reported (sorted) vs the first %d distinct ODB frequencies: %s' % (
                  n, ', '.join('%+.2f%%' % (100.0 * e) for e in errs)))
    c.report('tension_N', [('axial stress x nominal area', tension)], tol['report_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
