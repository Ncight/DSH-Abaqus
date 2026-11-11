# -*- coding: utf-8 -*-
"""Checker for d08_response_spectrum_cantilever.   py check.py <workdir>

The task fixes the axes: stick along y from the base at the origin to the top at y = 24 m, planar model in x - y,
spectrum applied in x, beam elements.  The job has to contain a frequency step and a response-spectrum step.

  frequency_1 .. 3     first three eigenfrequencies of the frequency step: 0.9715 / 6.0884 / 17.048 Hz
                       (uniform Euler-Bernoulli cantilever), +-2 % / +-2 % / +-3 %.  Shear-flexible elements lower
                       the third one by 2.3 % for this slenderness - still inside.
  spectrum_step        the frame the extractor summarised belongs to a response-spectrum step (not to the frequency
                       step: its frames are mode shapes, not a response)
  top_displacement     largest |U1| of that step = 0.084063 m, +-3 %
  base_moment          bending moment at the base in that step = 7727.7 kNm, +-3 %: section moments SM of the two
                       lowest beam elements (read through a row of probe points), extrapolated linearly to y = 0.
                       This is the force quantity every beam model has in a response-spectrum step, and it tells the
                       combination rules apart (absolute sum: +10 %).
  base_shear           M Sa sqrt(sum of the squared effective-mass fractions) = 464.11 kN, +-3 %.
                       Abaqus 2021 writes NO reaction forces in a response-spectrum step (requesting RF there or in
                       the frequency step changes nothing), and Euler-Bernoulli elements (B23 / B33) have no
                       transverse shear force there either.  So:
                         - if the step has a nodal RF1, or a non-zero transverse section force (Timoshenko
                           elements B21 / B22 / B31), that ODB value is checked (`base_shear`) and the reported
                           number is cross-checked against it (`report_base_shear_kN`);
                         - otherwise the ODB cannot give the base shear: there is no `base_shear` check, and
                           `report_base_shear_kN` compares the value in result.json (obtained from the modal
                           effective masses) with the closed-form value.
Typical wrong results: spectrum entered in g (x 9.8), absolute sum instead of SRSS (base shear +34 %, base moment
+10 %, top displacement only +1.5 %), a single mode (base shear -4.9 %, no second and third frequency), density in
kg/m3, spectrum applied along the axis of the stick (no response), section turned by 90 degrees (frequencies x 6).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import checklib as cl  # noqa: E402

OFFSETS = ([0.0125 * j for j in range(1, 41)] + [0.5 + 0.05 * j for j in range(1, 21)] +
           [1.5 + 0.25 * j for j in range(1, 27)])


def body(c):
    exp, tol = c.exp, c.tol
    c.job_completed()
    probes = [[0.0, dy, 0.0] for dy in OFFSETS]
    ex = c.extract(vars='U,RF,SF,SM', probes=probes, series='SM')

    freqs = cl.frequencies(ex)
    n = len(exp['frequencies_Hz'])
    if ex is not None and not freqs:
        c.odb_error = 'the ODB contains no eigenfrequencies (no *FREQUENCY step results)'
    for i, (f_exp, f_tol) in enumerate(zip(exp['frequencies_Hz'], tol['frequency_rel'])):
        c.check_rel('frequency_%d' % (i + 1), freqs[i] if i < len(freqs) else None, f_exp, f_tol, True,
                    'mode %d of the frequency step (ODB); all extracted: %s' % (
                        i + 1, [round(f, 4) for f in freqs[:6]]))
    if ex is not None:
        c.odb_error = None

    step_name = ((ex or {}).get('frame') or {}).get('step')
    proc = ''
    for s in cl.steps(ex):
        if s.get('name') == step_name:
            proc = str(s.get('procedure', ''))
    is_rs = 'SPECTRUM' in proc.upper()
    c.add('spectrum_step', is_rs, True, proc or None, 'a *RESPONSE SPECTRUM step', None,
          'procedure of the last non-eigenvalue step "%s" of the ODB' % step_name)
    no_rs = 'the ODB has no response-spectrum step with results'
    if ex is not None and not is_rs:
        c.odb_error = no_rs

    u1 = cl.comp_range(ex, 'U').get('U1') if is_rs else None
    top = max(abs(u1[0]), abs(u1[1])) if u1 is not None else None
    c.check_rel('top_displacement', top, exp['top_displacement_m'], tol['response_rel'], True,
                'largest |U1| in the response-spectrum step (ODB)')

    # ---- base moment: section moments of the two lowest elements, extrapolated to the base
    elements, comp = {}, None
    for i in range(len(probes) if is_rs else 0):
        try:
            pr = ex['probes'][i]
            el = pr['element']
            ser = pr['series']['SM']
            row, cols = ser['rows'][-1], ser['columns']
        except Exception:
            continue
        yc = cl.num(el.get('centroid', [None, None])[1])
        vals = dict((lab, cl.num(v)) for lab, v in zip(cols[1:], row[1:]) if lab in ('SM1', 'SM2'))
        vals = dict((k, v) for k, v in vals.items() if v is not None)
        if yc is None or yc <= 0.0 or not vals:
            continue
        if comp is None:
            comp = max(vals, key=lambda k: abs(vals[k]))
        if comp in vals:
            elements[(el.get('instance'), el.get('label'))] = (yc, abs(vals[comp]))
    near = sorted(elements.values())[:2]
    m0, note = None, 'bending moment at the base'
    if len(near) == 2 and near[1][0] > near[0][0]:
        (ya, ma), (yb, mb) = near
        m0 = ma + (ma - mb) * ya / (yb - ya)
        note = ('%s of the two lowest elements (centred at y = %.4g and %.4g m): %.6g and %.6g kNm, extrapolated to '
                'the base' % (comp, ya, yb, ma, mb))
    elif is_rs:
        c.odb_error = 'no section moments SM of beam elements near the base in the response-spectrum step'
    c.check_rel('base_moment', m0, exp['base_moment_kNm'], tol['response_rel'], True, note)
    if is_rs:
        c.odb_error = None

    # ---- base shear: from the ODB when it has one (reaction, or transverse shear of Timoshenko elements)
    shear, how = None, ''
    rf = cl.comp_range(ex, 'RF').get('RF1') if is_rs else None
    if rf is not None and max(abs(rf[0]), abs(rf[1])) > 0.0:
        shear, how = max(abs(rf[0]), abs(rf[1])), 'largest nodal |RF1| in the response-spectrum step (ODB)'
    elif is_rs:
        sf = cl.comp_range(ex, 'SF')
        trans = [max(abs(v[0]), abs(v[1])) for k, v in sf.items() if k not in ('SF1', 'SF11')]
        if trans and max(trans) > 1e-6 * exp['base_shear_kN']:
            shear = max(trans)
            how = 'largest transverse section force in the response-spectrum step (ODB)'
    why = c.odb_error
    c.odb_error = None
    c.load_result({'frequencies_Hz': ('list', n), 'base_shear_kN': 'number', 'top_displacement_m': 'number',
                   'base_moment_kNm': 'number'})
    rep_shear = c.result.get('base_shear_kN')
    if shear is not None:
        c.check_rel('base_shear', shear, exp['base_shear_kN'], tol['response_rel'], True, how)
        c.report('base_shear_kN', [('base shear of the response-spectrum step', shear)], tol['report_rel'],
                 critical=True)
    elif not is_rs:
        c.odb_error = why or no_rs
        c.check_rel('base_shear', None, exp['base_shear_kN'], tol['response_rel'], True, 'base shear')
        c.report('base_shear_kN', [], tol['report_rel'], critical=True)
    else:
        # no `base_shear` check: the ODB has nothing to check; the reported number carries the requirement
        note = ('the response-spectrum step holds neither reaction forces nor a transverse section force '
                '(Euler-Bernoulli elements), so the reported base shear is compared with the closed-form value '
                '%.6g kN instead of an ODB value' % exp['base_shear_kN'])
        if rep_shear is None:
            c.add('report_base_shear_kN', False, True, None, exp['base_shear_kN'], tol['response_rel'],
                  note + ' | no valid "base_shear_kN" in result.json')
        else:
            err = (abs(rep_shear) - exp['base_shear_kN']) / exp['base_shear_kN']
            c.add('report_base_shear_kN', abs(err) <= tol['response_rel'], True, rep_shear, exp['base_shear_kN'],
                  tol['response_rel'], note + ' | error %+.2f%%' % (100.0 * err))
    c.odb_error = None

    rep = c.result.get('frequencies_Hz')
    name = 'report_frequencies_Hz'
    if rep is None:
        c.add(name, False, True, None, freqs[:n] or None, tol['report_rel'],
              'no valid "frequencies_Hz" (list of %d numbers) in result.json' % n)
    elif len(freqs) < n:
        c.add(name, False, True, rep, freqs or None, tol['report_rel'],
              'NOT EVALUATED: fewer than %d eigenfrequencies in the ODB' % n)
    else:
        errs = [(r - f) / f if f else float('inf') for r, f in zip(sorted(rep), freqs[:n])]
        c.add(name, max(abs(e) for e in errs) <= tol['report_rel'], True, rep, freqs[:n], tol['report_rel'],
              'reported (sorted) vs ODB modes 1-%d: %s' % (n, ', '.join('%+.2f%%' % (100.0 * e) for e in errs)))
    c.odb_error = why
    c.report('top_displacement_m', [('largest |U1| in the response-spectrum step', top)], tol['report_rel'],
             critical=True)
    sm = cl.comp_range(ex, 'SM') if is_rs else {}
    m_ip = max([max(abs(v[0]), abs(v[1])) for k, v in sm.items() if k in ('SM1', 'SM2')] or [0.0]) or None
    c.report('base_moment_kNm', [('moment extrapolated to the base', m0),
                                 ('largest section moment at an integration point', m_ip)], tol['report_rel'],
             critical=True)
    c.odb_error = None


if __name__ == '__main__':
    cl.main(__file__, body)
