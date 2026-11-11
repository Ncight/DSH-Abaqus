# -*- coding: utf-8 -*-
"""Checker for t03_modal_cantilever.   py check.py <workdir>

Physics (from the ODB): the first three eigenfrequencies of the (last) *FREQUENCY step, each within +-3 % of the
Euler-Bernoulli values 41.78 / 83.55 / 261.8 Hz (1st weak-axis, 1st strong-axis, 2nd weak-axis bending; a converged
3-D model gives 41.89 / 83.16 / 259.5 Hz).  A planar model loses one bending plane, a coarse C3D8R mesh is 13 % low.
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
    if ex is not None and not freqs:
        c.odb_error = 'the ODB contains no eigenfrequencies (no *FREQUENCY step results)'
    for i, f_exp in enumerate(exp['frequencies_Hz']):
        got = freqs[i] if i < len(freqs) else None
        c.check_rel('frequency_%d' % (i + 1), got, f_exp, tol['frequency_rel'], True,
                    'mode %d of the frequency step (ODB), cycles per second' % (i + 1))
    c.odb_error = None if freqs else c.odb_error

    c.load_result({'frequencies_Hz': ('list', 3)})
    rep = c.result.get('frequencies_Hz')
    name = 'report_frequencies_Hz'
    if rep is None:
        c.add(name, False, True, None, freqs[:3] or None, tol['report_rel'],
              'no valid "frequencies_Hz" (list of 3 numbers) in result.json')
    elif len(freqs) < 3:
        c.add(name, False, True, rep, freqs or None, tol['report_rel'],
              'NOT EVALUATED: fewer than three eigenfrequencies in the ODB')
    else:
        rep_sorted = sorted(rep)
        errs = [(r - f) / f if f else float('inf') for r, f in zip(rep_sorted, freqs[:3])]
        worst = max(abs(e) for e in errs)
        note = 'reported (sorted) vs ODB modes 1-3: ' + ', '.join('%+.2f%%' % (100.0 * e) for e in errs)
        if rep != rep_sorted:
            note += ' | note: the reported list is not in ascending order'
        c.add(name, worst <= tol['report_rel'], True, rep, freqs[:3], tol['report_rel'], note)


if __name__ == '__main__':
    cl.main(__file__, body)
