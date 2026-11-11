# -*- coding: utf-8 -*-
"""Checker for h05_euler_buckling.   py check.py <workdir>

Physics:
  critical_load   (first positive eigenvalue of the *BUCKLE step in the ODB) x (reference load) within +-3 % of
                  pi^2 E I / L^2 = 6.5113e4 N.
                  The ODB of a buckle step does not contain the applied load (verified: even with CF requested the
                  buckle frames only carry U, UR, RF, RM, and those are mode-shape quantities).  The reference load
                  is therefore taken from
                    1. the job's own input file <job>.inp: resultant of the *CLOAD records of the *BUCKLE step
                       (node sets are resolved and counted);
                    2. result.json "reference_load_N" - only when 1. is not possible, e.g. when the load is a
                       pressure (*DSLOAD), the input file is missing or an earlier step already carries load.
                  The note of the check says which source was used.  With source 1 a model with the wrong end
                  conditions cannot be "repaired" by reporting a different reference load.
Report:
  report_reference_load_N   the reported reference load equals the one found in the input file (+-1 %); critical,
                            because a mismatch means the reported critical load is not what was computed.
                            When the load cannot be read back it is accepted as reported (the note says so).
  report_critical_load_N    the reported critical load equals eigenvalue x reference load (+-3 %).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    ex = c.extract(vars='U')

    eig = cl.eigenvalues(ex)
    positive = sorted(v for v in eig if v > 0.0)
    lam = positive[0] if positive else None

    c.load_result({'critical_load_N': 'number', 'reference_load_N': 'number'})
    p_rep = c.result.get('reference_load_N')

    # reference load actually applied in the buckle step
    p_inp, inp_note = cl.buckle_reference_load(c.path(c.job + '.inp'))
    if p_inp is not None and p_inp <= 0.0:
        p_inp, inp_note = None, 'the *CLOAD records of the buckle step sum to zero'
    if p_inp is not None:
        p_ref, source = p_inp, inp_note
    elif p_rep is not None:
        p_ref, source = abs(p_rep), 'result.json (not verifiable: %s)' % inp_note
    else:
        p_ref, source = None, 'no reference load available (%s; none in result.json)' % inp_note

    pcr = lam * p_ref if (lam is not None and p_ref is not None) else None
    if ex is not None and lam is None:
        c.odb_error = 'the ODB contains no positive buckling eigenvalue (eigenvalues: %s)' % (eig or 'none')
    elif ex is not None and p_ref is None:
        c.odb_error = source
    note = 'first positive eigenvalue %s x reference load %s [%s]' % (
        '%.6g' % lam if lam is not None else 'n/a', '%.6g' % p_ref if p_ref is not None else 'n/a', source)
    c.check_rel('critical_load', pcr, exp['critical_load_N'], tol['critical_load_rel'], True, note)
    if ex is not None:
        c.odb_error = None

    if p_rep is None:
        c.add('report_reference_load_N', False, True, None, p_inp, tol['reference_load_rel'],
              'no valid "reference_load_N" in result.json')
    elif p_inp is None:
        c.add('report_reference_load_N', True, True, p_rep, None, tol['reference_load_rel'],
              'accepted as reported - the applied load cannot be read back (%s)' % inp_note)
    else:
        err = (abs(p_rep) - p_inp) / p_inp
        c.add('report_reference_load_N', abs(err) <= tol['reference_load_rel'], True, p_rep, p_inp,
              tol['reference_load_rel'], 'reported vs applied reference load [%s]: %+.2f%%' % (inp_note, 100.0 * err))
    c.report('critical_load_N', [('eigenvalue x reference load', pcr)], tol['report_rel'], critical=True)


if __name__ == '__main__':
    cl.main(__file__, body)
