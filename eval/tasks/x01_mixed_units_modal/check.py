# -*- coding: utf-8 -*-
"""Checker for x01_mixed_units_modal.   py check.py <workdir>

Physics (from the ODB): the first two eigenfrequencies of the (last) *FREQUENCY step, each within +-3 % of the
Euler-Bernoulli values 15.49 Hz (weak-axis bending) and 25.81 Hz (strong-axis bending) of the simply supported beam.
Frequencies are in Hz whatever consistent unit system the model uses, so no unit conversion is involved - but a
density in the wrong unit shifts them by factors of 31.6 to 1e6.  Typical wrong models: clamped or axially held end
faces (24 - 58 Hz), planar models (one bending plane missing), a rigid-body mode at 0 Hz because the axial restraint
was forgotten.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U')

    freqs = cl.frequencies(ex)
    n = len(exp['frequencies_Hz'])
    if ex is not None and not freqs:
        c.odb_error = 'the ODB contains no eigenfrequencies (no *FREQUENCY step results)'
    for i, f_exp in enumerate(exp['frequencies_Hz']):
        got = freqs[i] if i < len(freqs) else None
        c.check_rel('frequency_%d' % (i + 1), got, f_exp, tol['frequency_rel'], True,
                    'mode %d of the frequency step (ODB), cycles per second' % (i + 1))
    if ex is not None:
        c.odb_error = None

    c.load_result({'frequencies_Hz': ('list', n)})
    rep = c.result.get('frequencies_Hz')
    name = 'report_frequencies_Hz'
    if rep is None:
        c.add(name, False, True, None, freqs[:n] or None, tol['report_rel'],
              'no valid "frequencies_Hz" (list of %d numbers) in result.json' % n)
    elif len(freqs) < n:
        c.add(name, False, True, rep, freqs or None, tol['report_rel'],
              'NOT EVALUATED: fewer than %d eigenfrequencies in the ODB' % n)
    else:
        rep_sorted = sorted(rep)
        errs = [(r - f) / f if f else float('inf') for r, f in zip(rep_sorted, freqs[:n])]
        worst = max(abs(e) for e in errs)
        c.add(name, worst <= tol['report_rel'], True, rep, freqs[:n], tol['report_rel'],
              'reported (sorted) vs ODB modes 1-%d: %s' % (n, ', '.join('%+.2f%%' % (100.0 * e) for e in errs)))


if __name__ == '__main__':
    cl.main(__file__, body)
