# -*- coding: utf-8 -*-
"""Offline logic tests of the checkers - NO Abaqus is started.

    py selftest_checkers.py

The ODB extractor is replaced by hand-made extractor JSON, so only the decision logic of the check.py files is
exercised: symmetry factor and force- / displacement-driven reactions (t05), horizontal reactions (t06),
reference-load sources (h05), probe handling (h04), RM / element-type handling (h01), and for the hard (x01 - x08)
and domain (d01 - d08) suites the time-history logic (decay fit, wave arrival, consolidation, load plateaus, the
stress - strain path of a material test), symmetry factors and the report cross-checks.  The d05 cases integrate the
task's ground-motion asset with reference/make_record.py.  It runs in a few seconds and is meant as a quick
regression test after editing a checker or checklib.py.  It is NOT a substitute for validate_tasks.py, which runs
the real reference solutions and negative controls through Abaqus.
"""
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile

EVAL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(EVAL, 'lib'))
import checklib as cl  # noqa: E402

FAILS = []


def load_body(task):
    path = os.path.join(EVAL, 'tasks', task, 'check.py')
    spec = importlib.util.spec_from_file_location('check_' + task, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return path, mod.body


def run(task, ex, result, inp_text=None, sta_ok=True):
    """run the checker body on a synthetic extractor dict -> summary dict"""
    path, body = load_body(task)
    work = tempfile.mkdtemp(prefix='offl_')
    try:
        c = cl.Checker(path, work)
        with open(os.path.join(work, c.job + '.odb'), 'wb') as f:
            f.write(b'x')
        with open(os.path.join(work, c.job + '.sta'), 'w') as f:
            f.write(' THE ANALYSIS HAS COMPLETED SUCCESSFULLY\n' if sta_ok else ' THE ANALYSIS HAS NOT BEEN COMPLETED\n')
        if result is not None:
            with open(os.path.join(work, 'result.json'), 'w') as f:
                json.dump(result, f)
        if inp_text is not None:
            with open(os.path.join(work, c.job + '.inp'), 'w') as f:
                f.write(inp_text)
        # no Abaqus: one synthetic dict for every extraction, or a callable (**extract arguments) -> dict
        c.extract = ex if callable(ex) else (lambda *a, **k: ex)
        if ex is None:
            c.odb_error = 'synthetic: extractor failed'
        body(c)
        return c.summary()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def expect(label, summ, want_pass, must_fail=(), must_pass=()):
    failed = [c['name'] for c in summ['checks'] if not c['pass']]
    ok = summ['pass'] == want_pass and all(n in failed for n in must_fail) and not any(n in failed for n in must_pass)
    print('%-58s %-4s -> pass=%-5s failed=%s' % (label, 'ok' if ok else '!!', summ['pass'], failed))
    if not ok:
        FAILS.append(label)
        for c in summ['checks']:
            print('      ', c['name'], c['pass'], c['got'], c['expected'], c['note'][:140])


def vec(name, comps, sums=None, mag=None):
    f = {'type': 'VECTOR', 'components': dict((k, {'max': mx, 'min': mn}) for k, (mx, mn) in comps.items())}
    if mag is not None:
        f['maxMagnitude'] = {'value': mag}
    if sums is not None:
        f['sum'] = dict((k, v[0]) for k, v in sums.items())
        f['sumPos'] = dict((k, v[1]) for k, v in sums.items())
        f['sumNeg'] = dict((k, v[2]) for k, v in sums.items())
    return f


# ------------------------------------------------------------------ t05: transmitted force / symmetry factor
P0, A = 3.7214e9, 2.5328e-4
good5 = {'contact_force_N': 500.0, 'max_contact_pressure_Pa': P0, 'contact_radius_m': A}


def hertz(types, rf, cpress=P0, u_axis=2):
    u = {'U1': (1e-7, -1e-7), 'U2': (1e-7, -1e-7), 'U3': (1e-7, -1e-7)}
    u['U%d' % u_axis] = (0.0, -6.4e-6)
    return {'elementTypes': types, 'steps': [{'procedure': '*STATIC'}],
            'frame': {'available': ['CPRESS', 'RF', 'U']},
            'fields': {'CPRESS': {'type': 'SCALAR', 'max': cpress, 'min': 0.0},
                       'U': vec('U', u, mag=6.4e-6), 'RF': vec('RF', {}, sums=rf)}}


expect('t05 axisym, force driven', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (500.0, 500.0, 0.0)}), good5), True)
expect('t05 axisym, displacement driven (+-500)', run('t05_hertz_contact', hertz({'CAX4R': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (0.0, 500.0, -500.0)}), good5), True)
expect('t05 axisym, 125 N only (no symmetry factor in 2-D)', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (125.0, 125.0, 0.0)}, cpress=P0 * 0.63), good5), False, ['contact_force'])
expect('t05 3-D quarter, force driven, lateral pollution', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 180.0, -180.0), 'RF2': (125.0, 125.0, 0.0), 'RF3': (0.0, 190.0, -190.0)}), good5), True)
expect('t05 3-D quarter, displacement driven', run('t05_hertz_contact', hertz({'C3D8R': 9000}, {
    'RF1': (0.0, 180.0, -180.0), 'RF2': (0.0, 125.0, -125.0), 'RF3': (0.0, 190.0, -190.0)}), good5), True)
expect('t05 3-D quarter pressed along Z', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 180.0, -180.0), 'RF2': (0.0, 170.0, -170.0), 'RF3': (-125.0, 0.0, -125.0)}, u_axis=3), good5), True)
expect('t05 3-D half model', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 300.0, -300.0), 'RF2': (250.0, 250.0, 0.0), 'RF3': (0.0, 0.0, 0.0)}), good5), True)
expect('t05 3-D FULL model with only 250 N (pressure 21 % low)', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 0.0, 0.0), 'RF2': (250.0, 250.0, 0.0), 'RF3': (0.0, 0.0, 0.0)}, cpress=P0 * 0.794),
    dict(good5, contact_force_N=250.0, max_contact_pressure_Pa=P0 * 0.794)), False, ['max_contact_pressure'])
expect('t05 3-D quarter loaded with 500 N (= 2000 N in total)', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 400.0, -400.0), 'RF2': (500.0, 500.0, 0.0), 'RF3': (0.0, 400.0, -400.0)}, cpress=P0 * 1.587),
    dict(good5, max_contact_pressure_Pa=P0 * 1.587)), False, ['max_contact_pressure'])
expect('t05 no CPRESS in the ODB', run('t05_hertz_contact', {
    'elementTypes': {'CAX4': 10}, 'frame': {'available': ['U', 'RF']}, 'fields': {
        'U': vec('U', {'U1': (0, 0), 'U2': (0, -6e-6)}, mag=6e-6),
        'RF': vec('RF', {}, sums={'RF1': (0, 0, 0), 'RF2': (500.0, 500.0, 0.0)})}}, good5), False,
    ['max_contact_pressure'], ['contact_force'])
expect('t05 quarter model reports the model force 125 N', run('t05_hertz_contact', hertz({'C3D10': 9000}, {
    'RF1': (0.0, 180.0, -180.0), 'RF2': (125.0, 125.0, 0.0), 'RF3': (0.0, 190.0, -190.0)}),
    dict(good5, contact_force_N=125.0)), True)
expect('t05 extractor failed', run('t05_hertz_contact', None, good5), False,
       ['max_contact_pressure', 'contact_force'])

def hertz_nodal(types, rf_sums, rf_nodal):
    ex = hertz(types, rf_sums)
    ex['fields']['RF']['components'] = dict((k, {'max': mx, 'min': mn}) for k, (mx, mn) in rf_nodal.items())
    return ex


expect('t05 3-D full, driven C3D20R face with mixed-sign nodal RF, rigid RP', run('t05_hertz_contact', hertz_nodal(
    {'C3D20R': 9000}, {'RF1': (0.0, 0.0, 0.0), 'RF2': (0.0, 700.0, -700.0), 'RF3': (0.0, 0.0, 0.0)},
    {'RF1': (0.0, 0.0), 'RF2': (500.0, -40.0), 'RF3': (0.0, 0.0)}), good5), True)
expect('t05 axisym force driven, net 480 N (4 % low)', run('t05_hertz_contact', hertz_nodal(
    {'CAX4': 5000}, {'RF1': (0.0, 2.0, -2.0), 'RF2': (480.0, 480.0, 0.0)},
    {'RF1': (2.0, -2.0), 'RF2': (480.0, 0.0)}, ), dict(good5, contact_force_N=480.0)), False, ['contact_force'])
expect('t05 force driven: positive sum inflated, net is what counts', run('t05_hertz_contact', hertz_nodal(
    {'C3D20R': 9000}, {'RF1': (0.0, 0.0, 0.0), 'RF2': (500.0, 800.0, -300.0), 'RF3': (0.0, 0.0, 0.0)},
    {'RF1': (0.0, 0.0), 'RF2': (30.0, -12.0), 'RF3': (0.0, 0.0)}), good5), True)

expect('t05 N-mm-MPa model (CPRESS in MPa), result.json in SI', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (500.0, 500.0, 0.0)}, cpress=P0 / 1.0e6), good5), True)
expect('t05 N-mm-MPa model, pressure also REPORTED in MPa', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (500.0, 500.0, 0.0)}, cpress=P0 / 1.0e6),
    dict(good5, max_contact_pressure_Pa=P0 / 1.0e6)), False, ['report_max_contact_pressure_Pa'],
    ['max_contact_pressure', 'contact_force'])
expect('t05 N-mm-MPa model with a 20 % low pressure', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 2.0, -2.0), 'RF2': (500.0, 500.0, 0.0)}, cpress=0.8 * P0 / 1.0e6),
    dict(good5, max_contact_pressure_Pa=0.8 * P0)), False, ['max_contact_pressure'])
expect('t05 nothing in contact (CPRESS = 0)', run('t05_hertz_contact', hertz({'CAX4': 5000}, {
    'RF1': (0.0, 0.0, 0.0), 'RF2': (500.0, 500.0, 0.0)}, cpress=0.0), good5), False, ['max_contact_pressure'])


# ------------------------------------------------------------------ t06: horizontal reactions
def column(vert, lat1=(0.0, 0.0, 0.0), lat2=(0.0, 0.0, 0.0), umax=4.0833e-5, smin=-245.0, axis=3):
    names = ['RF1', 'RF2', 'RF3']
    order = [n for n in names if n != 'RF%d' % axis]
    sums = {'RF%d' % axis: vert, order[0]: lat1, order[1]: lat2}
    return {'elementTypes': {'C3D20R': 80}, 'steps': [{'procedure': '*STATIC'}], 'frame': {'available': ['S', 'U', 'RF']},
            'fields': {'U': vec('U', {'U1': (1e-7, -1e-7), 'U2': (1e-7, -1e-7), 'U3': (0.0, -umax)}, mag=umax),
                       'RF': vec('RF', {}, sums=sums),
                       'S': {'type': 'TENSOR_3D_FULL', 'invariants': {'minPrincipal': {'max': -1.0, 'min': smin}},
                             'elementNodal': {'minPrincipal': {'max': 0.0, 'min': smin}}}}}


good6 = {'base_reaction_kN': 245.0, 'top_settlement_m': 4.0833e-5, 'base_stress_kPa': -245.0}
expect('t06 C3D8R, all vertical reactions positive', run('t06_selfweight_units', column((245.0, 245.0, 0.0)), good6), True)
expect('t06 C3D20R, vertical nodal reactions of both signs', run('t06_selfweight_units',
       column((245.0, 408.3, -163.3)), good6), True)
expect('t06 column along Y', run('t06_selfweight_units', column((245.0, 245.0, 0.0), axis=2), good6), True)
expect('t06 fully fixed base (horizontal reactions)', run('t06_selfweight_units',
       column((245.0, 245.0, 0.0), (0.0, 18.0, -18.0), (0.0, 18.0, -18.0)), good6), False, ['free_lateral_expansion'])
expect('t06 N / Pa / kg model', run('t06_selfweight_units', column((245000.0, 245000.0, 0.0), smin=-245000.0),
       {'base_reaction_kN': 245.0, 'top_settlement_m': 4.0833e-5, 'base_stress_kPa': -245.0}), False,
       ['base_reaction', 'base_stress', 'report_base_reaction_kN'])
expect('t06 compression reported positive (non-critical)', run('t06_selfweight_units', column((245.0, 245.0, 0.0)),
       dict(good6, base_stress_kPa=245.0)), True, ['report_base_stress_kPa'])


# ------------------------------------------------------------------ h05: reference load sources
PCR = 6.5113e4
INP_1000 = '''*Nset, nset=TOP
 2,
*Step, name=Buckle, perturbation
*Buckle
4, , 10, 300
*Cload
TOP, 2, -1000.
*End Step
'''
INP_PRESSURE = '''*Step, name=Buckle, perturbation
*Buckle
4, , 10, 300
*Dsload
TOP, P, 795774.7
*End Step
'''


def buckle(eigs):
    return {'elementTypes': {'B32': 20}, 'steps': [{'procedure': '*BUCKLE', 'name': 'Buckle'}],
            'eigenvalues': [{'step': 'Buckle', 'mode': i + 1, 'value': v} for i, v in enumerate(eigs)],
            'fields': {'U': vec('U', {'U1': (1.0, -1.0)}, mag=1.0)}, 'frame': {'available': ['U']}}


expect('h05 correct, load 1000 N found in the INP', run('h05_euler_buckling', buckle([65.1, 65.1, 260.0]),
       {'critical_load_N': 65100.0, 'reference_load_N': 1000.0}, INP_1000), True)
expect('h05 negative eigenvalues first', run('h05_euler_buckling', buckle([-70.0, 65.1, 65.1]),
       {'critical_load_N': 65100.0, 'reference_load_N': 1000.0}, INP_1000), True)
expect('h05 cantilever, honest report', run('h05_euler_buckling', buckle([16.28, 16.28]),
       {'critical_load_N': 16280.0, 'reference_load_N': 1000.0}, INP_1000), False, ['critical_load'],
       ['report_reference_load_N', 'report_critical_load_N'])
expect('h05 cantilever, fudged reference load 4000', run('h05_euler_buckling', buckle([16.28, 16.28]),
       {'critical_load_N': 65120.0, 'reference_load_N': 4000.0}, INP_1000), False,
       ['critical_load', 'report_reference_load_N'])
expect('h05 pressure load: reported reference used', run('h05_euler_buckling', buckle([65.1]),
       {'critical_load_N': 65100.0, 'reference_load_N': 1000.0}, INP_PRESSURE), True)
expect('h05 no INP at all: reported reference used', run('h05_euler_buckling', buckle([65.1]),
       {'critical_load_N': 65100.0, 'reference_load_N': 1000.0}, None), True)
expect('h05 no INP, no result.json', run('h05_euler_buckling', buckle([65.1]), None, None), False,
       ['critical_load', 'result_json'])
expect('h05 only negative eigenvalues (load in tension)', run('h05_euler_buckling', buckle([-65.1, -260.0]),
       {'critical_load_N': 65100.0, 'reference_load_N': 1000.0}, INP_1000), False, ['critical_load'])
expect('h05 honest but critical load mis-reported', run('h05_euler_buckling', buckle([65.1]),
       {'critical_load_N': 2.6e5, 'reference_load_N': 1000.0}, INP_1000), False, ['report_critical_load_N'],
       ['critical_load'])

# ------------------------------------------------------------------ h04: probes
SIG = -4.0e7


def wave(n_frames=100, t_end=1.5e-4, c=5000.0, plateau=SIG, proc='*DYNAMIC, EXPLICIT', dist=0.003, ring=0.02,
         rise=1.0e-5):
    def series(x):
        rows = []
        for i in range(n_frames + 1):
            t = t_end * i / n_frames
            ta = x / c
            if t <= ta - rise / 2:
                s = 0.0
            elif t >= ta + rise / 2:
                s = plateau * (1.0 + ring * (-1) ** i * max(0.0, 1.0 - (t - ta) / 3e-5))
            else:
                s = plateau * (t - (ta - rise / 2)) / rise
            rows.append([t, s, 0.0, 0.0, 0.0, 0.0, 0.0, abs(s)])
        return {'columns': ['time', 'S11', 'S22', 'S33', 'S12', 'S13', 'S23', 'mises'], 'rows': rows}

    def probe(x):
        return {'point': [x, 0.005, 0.005], 'element': {'label': 1, 'centroid': [x + dist, 0.0025, 0.0025],
                                                         'distance': dist}, 'series': {'S': series(x)}}
    return {'elementTypes': {'C3D8R': 800}, 'steps': [{'procedure': proc, 'timePeriod': t_end, 'totalTime': 0.0}],
            'fields': {}, 'frame': {'available': ['S']}, 'probes': [probe(0.25), probe(0.9), probe(0.5)]}


good4 = {'wave_speed_m_s': 5000.0, 'stress_behind_front_Pa': SIG, 'arrival_time_at_mid_s': 1.0e-4}
expect('h04 correct, 100 frames', run('h04_wave_explicit', wave(), good4), True, (),
       ['arrival_time_mid', 'report_wave_speed_m_s', 'report_arrival_time_at_mid_s', 'report_stress_sign'])
expect('h04 correct, 20 frames', run('h04_wave_explicit', wave(n_frames=20), good4), True, (),
       ['arrival_time_mid', 'report_wave_speed_m_s', 'report_arrival_time_at_mid_s'])
expect('h04 correct, 4 frames only (arrival not resolvable)', run('h04_wave_explicit', wave(n_frames=4), good4), True)
expect('h04 implicit procedure', run('h04_wave_explicit', wave(proc='*DYNAMIC'), good4), False,
       ['explicit_procedure'])
expect('h04 tension (pulled)', run('h04_wave_explicit', wave(plateau=-SIG),
       dict(good4, stress_behind_front_Pa=-SIG)), False, ['stress_behind_front'])
expect('h04 wave too fast (c = 7000)', run('h04_wave_explicit', wave(c=7000.0), good4), False,
       ['quiet_ahead_of_front'])
expect('h04 wrong duration', run('h04_wave_explicit', wave(t_end=1.5e-3), good4), False, ['analysis_time'])
expect('h04 bar not at the prescribed coordinates', run('h04_wave_explicit', wave(dist=0.4), good4), False,
       ['stress_behind_front', 'quiet_ahead_of_front'])
expect('h04 positive stress reported for compression', run('h04_wave_explicit', wave(),
       dict(good4, stress_behind_front_Pa=-SIG)), True, ['report_stress_sign'])
expect('h04 stress reported in MPa', run('h04_wave_explicit', wave(),
       dict(good4, stress_behind_front_Pa=-40.0)), False, ['report_stress_behind_front_Pa'])
expect('h04 no probes (S not in ODB)', run('h04_wave_explicit', dict(wave(), probes=[
    {'point': [0.25, 0.005, 0.005], 'series': {}}, {'point': [0.9, 0.005, 0.005], 'series': {}},
    {'point': [0.5, 0.005, 0.005], 'series': {}}]), good4), False, ['stress_behind_front', 'quiet_ahead_of_front'])

# ------------------------------------------------------------------ h01: RM present / absent, element types
def beam(types, rm=1.3333e4, with_rm=True, umax=3.43e-3, rf=4.0e4):
    fields = {'U': vec('U', {'U1': (0, 0), 'U2': (0, -umax), 'U3': (0, 0)}, mag=umax),
              'RF': vec('RF', {}, sums={'RF1': (0, 0, 0), 'RF2': (rf, rf, 0), 'RF3': (0, 0, 0)})}
    if with_rm:
        fields['RM'] = vec('RM', {'RM1': (0, 0), 'RM2': (0, 0), 'RM3': (rm, -rm)},
                           sums={'RM1': (0, 0, 0), 'RM2': (0, 0, 0), 'RM3': (0, rm, -rm)})
    return {'elementTypes': types, 'steps': [{'procedure': '*STATIC'}], 'fields': fields,
            'frame': {'available': list(fields)}}


good1 = {'max_deflection_m': 3.43e-3, 'total_reaction_N': 4.0e4, 'end_moment_Nm': 1.3333e4}
expect('h01 B32, RM in ODB', run('h01_fixed_beam_udl', beam({'B32': 20}), good1), True)
expect('h01 B21 planar', run('h01_fixed_beam_udl', beam({'B21': 40}), good1), True)
expect('h01 no RM field: reported moment used', run('h01_fixed_beam_udl', beam({'B31': 40}, with_rm=False), good1),
       True)
expect('h01 no RM field and wrong reported moment', run('h01_fixed_beam_udl', beam({'B31': 40}, with_rm=False),
       dict(good1, end_moment_Nm=2.0e4)), False, ['end_moment'])
expect('h01 solid elements', run('h01_fixed_beam_udl', beam({'C3D20R': 500}, with_rm=False), good1), False,
       ['beam_elements'], ['max_deflection', 'total_reaction'])
expect('h01 beam + spring elements', run('h01_fixed_beam_udl', beam({'B31': 40, 'SPRING1': 2}), good1), False,
       ['beam_elements'])
expect('h01 pinned (RM zero)', run('h01_fixed_beam_udl', beam({'B32': 20}, rm=0.0, umax=1.667e-2),
       dict(good1, max_deflection_m=1.667e-2, end_moment_Nm=0.0)), False, ['max_deflection', 'end_moment'])

# ------------------------------------------------------------------ h03: uniform uniaxial state
SIGY = 2.87129e8


def bar(types, mises=SIGY, peeq=0.018564, pressure=(-SIGY / 3.0, -SIGY / 3.0), with_peeq=True):
    inv = {'mises': {'max': mises, 'min': mises}, 'maxPrincipal': {'max': mises, 'min': mises}}
    if pressure is not None:
        inv['pressure'] = {'max': pressure[0], 'min': pressure[1]}
    fields = {'S': {'type': 'TENSOR_3D_FULL', 'invariants': inv, 'components': {'S11': {'max': mises, 'min': mises}}}}
    if with_peeq:
        fields['PEEQ'] = {'type': 'SCALAR', 'max': peeq, 'min': peeq}
    return {'elementTypes': types, 'steps': [{'procedure': '*STATIC'}], 'fields': fields,
            'frame': {'available': list(fields)}}


good3 = {'axial_stress_Pa': SIGY, 'peeq': 0.018564}
expect('h03 solid bar, uniform uniaxial', run('h03_plasticity_bar', bar({'C3D8R': 40}), good3), True)
expect('h03 truss without a pressure invariant', run('h03_plasticity_bar', bar({'T3D2': 1}, pressure=None), good3), True)
expect('h03 solid without a pressure invariant', run('h03_plasticity_bar', bar({'C3D8R': 40}, pressure=None), good3),
       False, ['uniaxial_state'])
expect('h03 gripped ends (triaxial near the grips)', run('h03_plasticity_bar',
       bar({'C3D8R': 40}, pressure=(-SIGY / 3.0, -1.6e8)), good3), False, ['uniaxial_state'])
expect('h03 hardening table too short (270 MPa)', run('h03_plasticity_bar',
       bar({'C3D8R': 40}, mises=2.7e8, peeq=0.01865, pressure=(-0.9e8, -0.9e8)),
       {'axial_stress_Pa': 2.7e8, 'peeq': 0.01865}), False, ['axial_stress'])
expect('h03 elastic only (no PEEQ field)', run('h03_plasticity_bar',
       bar({'C3D8R': 40}, mises=4.0e9, pressure=(-1.3333e9, -1.3333e9), with_peeq=False),
       {'axial_stress_Pa': 4.0e9, 'peeq': 0.0}), False, ['axial_stress', 'peeq'])
expect('h03 total strain reported as PEEQ', run('h03_plasticity_bar', bar({'C3D8R': 40}),
       dict(good3, peeq=0.02)), False, ['report_peeq'])


# ------------------------------------------------------------------ h02: shell elements
def plate(types, w=2.115e-4, smax=2.87e6, normal=None, en_principal=None):
    """smax = largest principal stress at integration points, normal = largest S11 / S22 (None: no components)"""
    stress = {'type': 'TENSOR_3D_PLANAR', 'invariants': {'maxPrincipal': {'max': smax, 'min': -smax},
                                                         'minPrincipal': {'max': smax, 'min': -smax},
                                                         'mises': {'max': smax, 'min': 0.0}},
              'elementNodal': {'maxPrincipal': {'max': en_principal or smax, 'min': -(en_principal or smax)}}}
    if normal is not None:
        stress['components'] = {'S11': {'max': normal, 'min': -normal}, 'S22': {'max': normal, 'min': -normal},
                                'S12': {'max': 0.7 * smax, 'min': -0.7 * smax}, 'S33': {'max': 0.0, 'min': 0.0}}
    return {'elementTypes': types, 'steps': [{'procedure': '*STATIC'}], 'frame': {'available': ['S', 'U']},
            'fields': {'U': vec('U', {'U1': (0, 0), 'U2': (0, 0), 'U3': (0, -w)}, mag=w), 'S': stress}}


good2 = {'center_deflection_m': 2.115e-4, 'max_bending_stress_Pa': 2.87e6}
expect('h02 S4R', run('h02_ss_plate_pressure', plate({'S4R': 1600}), good2), True)
expect('h02 S8R + STRI65', run('h02_ss_plate_pressure', plate({'S8R': 300, 'STRI65': 12}), good2), True)
expect('h02 continuum shells', run('h02_ss_plate_pressure', plate({'SC8R': 1600}), good2), True)
expect('h02 solid elements', run('h02_ss_plate_pressure', plate({'C3D8R': 1600}), good2), False, ['shell_elements'])
expect('h02 clamped edges', run('h02_ss_plate_pressure', plate({'S4R': 1600}, w=6.55e-5, smax=3.08e6),
       {'center_deflection_m': 6.55e-5, 'max_bending_stress_Pa': 3.08e6}), False, ['center_deflection'])
expect('h02 S8R: corner twist peak at element nodes (3.01e6), centre 2.89e6', run('h02_ss_plate_pressure',
       plate({'S8R': 400}, smax=2.891e6, normal=2.89e6, en_principal=3.0115e6), dict(good2, max_bending_stress_Pa=2.891e6)),
       True)
expect('h02 S8R fine mesh: corner peak above the centre value everywhere', run('h02_ss_plate_pressure',
       plate({'S8R': 6400}, smax=3.2e6, normal=2.88e6, en_principal=3.4e6), dict(good2, max_bending_stress_Pa=2.88e6)),
       True)
expect('h02 S4R 4 x 4: principal 2.77e6 but normal stress 2.53e6', run('h02_ss_plate_pressure',
       plate({'S4R': 16}, w=2.1413e-4, smax=2.7724e6, normal=2.5336e6),
       {'center_deflection_m': 2.1413e-4, 'max_bending_stress_Pa': 2.7724e6}), False, ['max_bending_stress'])
expect('h02 compressive side reported (negative)', run('h02_ss_plate_pressure', plate({'S4R': 1600}),
       dict(good2, max_bending_stress_Pa=-2.87e6)), True)


# ====================================================================================== hard split x01 - x08
def modal(freqs, extra_steps=(), frame_step=None, fields=None):
    """extractor JSON of a job whose last step is a *FREQUENCY step called Freq"""
    steps = [{'name': n, 'procedure': p, 'nFrames': 2, 'totalTime': 0.0, 'timePeriod': 1.0} for n, p in extra_steps]
    steps.append({'name': 'Freq', 'procedure': '*FREQUENCY', 'nFrames': len(freqs) + 1, 'totalTime': 1.0 * len(steps),
                  'timePeriod': 0.0})
    fields = fields or {}
    return {'steps': steps, 'eigenvalues': [], 'elementTypes': {'B31': 100}, 'fields': fields,
            'frequencies': [{'step': 'Freq', 'mode': i + 1, 'frequency': f} for i, f in enumerate(freqs)],
            'frame': {'step': frame_step or 'Freq', 'available': list(fields)}}


# ------------------------------------------------------------------ x01: two lowest frequencies
X1 = 'x01_mixed_units_modal'
expect('x01 solid model 15.48 / 25.76 Hz', run(X1, modal([15.476, 25.763, 61.5]),
       {'frequencies_Hz': [15.476, 25.763]}), True)
expect('x01 reported in descending order', run(X1, modal([15.476, 25.763]),
       {'frequencies_Hz': [25.763, 15.476]}), True)
expect('x01 kg/m3 in a mm-N-MPa model (frequencies x 1/31623)', run(X1, modal([4.894e-4, 8.147e-4]),
       {'frequencies_Hz': [4.894e-4, 8.147e-4]}), False, ['frequency_1', 'frequency_2'], ['report_frequencies_Hz'])
expect('x01 clamped end faces', run(X1, modal([35.13, 58.22]), {'frequencies_Hz': [35.13, 58.22]}), False,
       ['frequency_1', 'frequency_2'])
expect('x01 planar model: the weak-axis mode is missing', run(X1, modal([25.81, 103.24]),
       {'frequencies_Hz': [25.81, 103.24]}), False, ['frequency_1', 'frequency_2'])
expect('x01 rigid-body mode at 0 Hz first', run(X1, modal([0.0, 15.48, 25.76]),
       {'frequencies_Hz': [15.48, 25.76]}), False, ['frequency_1'])
expect('x01 reported in rad/s', run(X1, modal([15.476, 25.763]), {'frequencies_Hz': [97.24, 161.87]}), False,
       ['report_frequencies_Hz'], ['frequency_1', 'frequency_2'])
expect('x01 only one mode extracted', run(X1, modal([15.476]), {'frequencies_Hz': [15.476, 25.8]}), False,
       ['frequency_2', 'report_frequencies_Hz'], ['frequency_1'])
expect('x01 no frequency step at all', run(X1, {'steps': [{'name': 'S', 'procedure': '*STATIC'}], 'frequencies': [],
                                             'fields': {}, 'frame': {'step': 'S'}},
       {'frequencies_Hz': [15.49, 25.81]}), False, ['frequency_1', 'frequency_2', 'report_frequencies_Hz'])


# ------------------------------------------------------------------ x02: geostatic equilibrium
def soil(u=2.3e-17, sv=(-352.8, 0.0), sh_min=-151.2, nodal=True, scale=1.0):
    """sv = (most, least) compressive min-principal stress, sh_min = most compressive max-principal stress"""
    inv = {'minPrincipal': {'min': sv[0] * scale, 'max': sv[1] * scale},
           'maxPrincipal': {'min': sh_min * scale, 'max': 0.0}}
    s = {'type': 'TENSOR_3D_PLANAR', 'invariants': inv}
    if nodal:
        s['elementNodal'] = inv
    return {'steps': [{'name': 'Geo', 'procedure': '*GEOSTATIC'}], 'elementTypes': {'CPE4': 40},
            'frame': {'step': 'Geo', 'available': ['S', 'U', 'RF']},
            'fields': {'S': s, 'U': vec('U', {'U1': (0.0, 0.0), 'U2': (0.0, -u)}, mag=u)}}


X2 = 'x02_geostatic_k0'
good_x2 = {'max_displacement_m': 1.0e-16, 'vertical_stress_base_kPa': -352.8, 'horizontal_stress_base_kPa': -151.2}
expect('x02 balanced geostatic state (nodal stresses)', run(X2, soil(), good_x2), True)
expect('x02 integration-point stresses only, 40 elements', run(X2, soil(sv=(-348.39, -4.41), sh_min=-149.31,
       nodal=False), good_x2), True)
expect('x02 coarse mesh, 4 elements (centroid stresses)', run(X2, soil(sv=(-308.7, -44.1), sh_min=-132.3,
       nodal=False), dict(good_x2, vertical_stress_base_kPa=-308.7, horizontal_stress_base_kPa=-132.3)), True)
expect('x02 gravity without in-situ stress: settles 87 mm', run(X2, soil(u=0.08736),
       dict(good_x2, max_displacement_m=0.08736)), False, ['max_displacement'], ['vertical_stress', 'k0_ratio'])
expect('x02 gravity only with E in Pa: settles 0.087 mm', run(X2, soil(u=8.736e-5),
       dict(good_x2, max_displacement_m=8.736e-5)), False, ['max_displacement'])
expect('x02 wrong K0 = 1 (no displacement in a confined column)', run(X2, soil(sh_min=-352.8),
       dict(good_x2, horizontal_stress_base_kPa=-352.8)), False, ['k0_ratio'], ['max_displacement', 'vertical_stress'])
expect('x02 model in Pa instead of kPa', run(X2, soil(scale=1000.0),
       dict(good_x2, vertical_stress_base_kPa=-352800.0, horizontal_stress_base_kPa=-151200.0)), False,
       ['vertical_stress'], ['k0_ratio'])
expect('x02 compression reported as positive numbers', run(X2, soil(),
       dict(good_x2, vertical_stress_base_kPa=352.8, horizontal_stress_base_kPa=151.2)), True, ['report_stress_sign'])
expect('x02 reports zero displacement, ODB has 87 mm', run(X2, soil(u=0.08736), good_x2), False,
       ['max_displacement', 'report_max_displacement_m'])
expect('x02 vertical stress reported 10 % off', run(X2, soil(), dict(good_x2, vertical_stress_base_kPa=-388.0)),
       False, ['report_vertical_stress_base_kPa'], ['vertical_stress'])

# ------------------------------------------------------------------ x03: pre-tensioned wire
SIG3 = 20000.0 / 7.853982e-5


def cable(freqs, sigma=SIG3, general=True):
    if not general:
        return modal(freqs)
    return modal(freqs, extra_steps=[('Pretension', '*STATIC')], frame_step='Pretension',
                 fields={'S': {'type': 'TENSOR_3D_FULL', 'invariants': {'mises': {'max': sigma, 'min': sigma}}}})


X3 = 'x03_cable_prestress_modes'
good_x3 = {'frequencies_Hz': [8.9952, 18.001, 27.029], 'tension_N': 19987.0}
pairs3 = [8.9952, 8.9952, 18.001, 18.001, 27.029, 27.029, 36.09, 36.09, 45.2, 45.2]
expect('x03 3-D beam model, every mode twice', run(X3, cable(pairs3, sigma=19987.0 / 7.853982e-5), good_x3), True)
expect('x03 planar model', run(X3, cable([9.0051, 18.008, 27.006, 36.0]),
       {'frequencies_Hz': [9.0051, 18.008, 27.006], 'tension_N': 20000.0}), True)
expect('x03 rigid-body spin at 0 Hz in front', run(X3, cable([0.0] + pairs3), good_x3), True)
expect('x03 pretension step without NLGEOM (bending frequencies)', run(X3, cable(
    [0.198, 0.198, 0.793, 0.793, 1.784, 1.784]), {'frequencies_Hz': [0.198, 0.793, 1.784], 'tension_N': 20000.0}),
    False, ['frequency_1', 'frequency_2', 'frequency_3'], ['tension'])
expect('x03 3-D model with only four modes', run(X3, cable(pairs3[:4]), good_x3), False,
       ['frequency_3', 'report_frequencies_Hz'], ['frequency_1', 'frequency_2'])
expect('x03 no pretension step in the job', run(X3, cable([0.198, 0.793, 1.784], general=False),
       {'frequencies_Hz': [0.198, 0.793, 1.784], 'tension_N': 0.0}), False, ['frequency_1', 'tension'])
expect('x03 80 kN instead of 20 kN', run(X3, cable([2 * f for f in pairs3], sigma=4 * SIG3),
       {'frequencies_Hz': [17.99, 36.0, 54.06], 'tension_N': 80000.0}), False, ['frequency_1', 'tension'])
expect('x03 duplicate pair reported as two modes', run(X3, cable(pairs3, sigma=19987.0 / 7.853982e-5),
       dict(good_x3, frequencies_Hz=[8.9952, 8.9952, 18.001])), False, ['report_frequencies_Hz'],
       ['frequency_1', 'frequency_2', 'frequency_3', 'tension'])
expect('x03 nominal tension reported, ODB has 15 kN', run(X3, cable(pairs3, sigma=15000.0 / 7.853982e-5),
       dict(good_x3, tension_N=20000.0)), False, ['tension', 'report_tension_N'])


# ------------------------------------------------------------------ x04: bimetal strip
def strip(u2=(2.0e-6, -2.9162e-3)):
    return {'steps': [{'name': 'Heat', 'procedure': '*STATIC'}], 'elementTypes': {'CPS8R': 400},
            'frame': {'step': 'Heat', 'available': ['S', 'U']},
            'fields': {'U': vec('U', {'U1': (2.15e-4, 0.0), 'U2': u2}, mag=max(abs(u2[0]), abs(u2[1])))}}


X4 = 'x04_bimetal_thermal'
expect('x04 plane stress, bends towards layer A', run(X4, strip(), {'tip_deflection_m': -2.9152e-3}), True)
expect('x04 3-D solid (+2.3 %)', run(X4, strip((1.0e-6, -2.9756e-3)), {'tip_deflection_m': -2.9748e-3}), True)
expect('x04 plane strain (+30 %)', run(X4, strip((2.0e-6, -3.7941e-3)), {'tip_deflection_m': -3.7941e-3}), False,
       ['tip_deflection'], ['report_tip_deflection_m'])
expect('x04 heated by 120 K (+20 %)', run(X4, strip((2.0e-6, -3.4995e-3)), {'tip_deflection_m': -3.4995e-3}), False,
       ['tip_deflection'])
expect('x04 layers the other way round', run(X4, strip((2.9162e-3, -2.0e-6)), {'tip_deflection_m': 2.9152e-3}),
       False, ['tip_deflection'], ['report_tip_deflection_m', 'report_sign'])
expect('x04 layers not bonded (only thickness expansion)', run(X4, strip((1.0e-6, -6.0e-7)),
       {'tip_deflection_m': 6.0e-7}), False, ['tip_deflection'])
expect('x04 model in millimetres', run(X4, strip((2.0e-3, -2.9162)), {'tip_deflection_m': -2.9162}), False,
       ['tip_deflection'])
expect('x04 magnitude reported without the sign', run(X4, strip(), {'tip_deflection_m': 2.9152e-3}), True,
       ['report_sign'], ['report_tip_deflection_m'])
expect('x04 deflection reported in mm', run(X4, strip(), {'tip_deflection_m': -2.9152}), False,
       ['report_tip_deflection_m'], ['tip_deflection'])
expect('x04 no displacement output', run(X4, {'steps': [], 'fields': {}, 'frame': {'step': 'Heat'}},
       {'tip_deflection_m': -2.9e-3}), False, ['tip_deflection', 'report_tip_deflection_m'])


# ------------------------------------------------------------------ x05: free decay after a sudden release
def decay_rows(zeta=0.02, f=16.70, t_end=0.6, dt=0.002, amp=1.1905e-3, removed=True, ripple=0.025, static=True):
    """tip history: static ramp to amp at total time 1, then the two-mode free decay (second mode = ripple)"""
    w = 2.0 * math.pi * f
    wd = w * math.sqrt(1.0 - zeta ** 2)
    rows = [[0.0, 0.0, 0.0, 0.0], [1.0, 0.0, amp, 0.0]] if static else []
    for i in range(int(round(t_end / dt)) + 1):
        t = i * dt
        y = 1.0
        if removed:
            y = math.exp(-zeta * w * t) * ((1.0 - ripple) * (math.cos(wd * t) + zeta * w / wd * math.sin(wd * t)) +
                                           ripple * math.cos(6.267 * w * t))
        rows.append([1.0 + t, 0.0, amp * y, 0.0])
    return rows


def decay(rows, dist=0.0):
    return {'steps': [{'name': 'Load', 'procedure': '*STATIC', 'nFrames': 2, 'totalTime': 0.0, 'timePeriod': 1.0},
                      {'name': 'Free', 'procedure': '*DYNAMIC', 'nFrames': len(rows) - 2, 'totalTime': 1.0,
                       'timePeriod': 0.6}],
            'elementTypes': {'B31': 40}, 'frame': {'step': 'Free', 'available': ['U', 'RF']},
            'fields': {'U': vec('U', {'U1': (0.0, 0.0), 'U2': (1.19e-3, -1.0e-3), 'U3': (0.0, 0.0)}, mag=1.19e-3)},
            'probes': [{'point': [1.0, 0.0, 0.0],
                        'node': {'instance': 'BEAM-1', 'label': 2, 'coords': [1.0 - dist, 0.0, 0.0], 'distance': dist},
                        'series': {'U': {'columns': ['time', 'U1', 'U2', 'U3'], 'rows': rows}}}]}


X5 = 'x05_damped_free_vibration'
good_x5 = {'first_frequency_Hz': 16.70, 'damping_ratio': 0.0200, 'rayleigh_alpha': 4.1998, 'rayleigh_beta': 0.0}
INP5 = '*Material, name=Steel\n*Damping, alpha=4.1998\n*Density\n7850.,\n*Elastic\n2.1e+11, 0.3\n'
expect('x05 alpha damping, 2 %', run(X5, decay(decay_rows()), good_x5, inp_text=INP5), True)
expect('x05 beta damping, no ripple', run(X5, decay(decay_rows(ripple=0.0)),
       dict(good_x5, rayleigh_alpha=0.0, rayleigh_beta=3.8097e-4),
       inp_text='*Material, name=Steel\n*Damping, beta=0.00038097\n'), True)
expect('x05 frames exactly every T/20', run(X5, decay(decay_rows(dt=0.00299)), good_x5, inp_text=INP5), True)
expect('x05 no static step in the series (starts deflected)', run(X5, decay(decay_rows(static=False)), good_x5,
       inp_text=INP5), True)
expect('x05 undamped, honest report', run(X5, decay(decay_rows(zeta=0.0)),
       dict(good_x5, damping_ratio=-3.0e-5, rayleigh_alpha=0.0, rayleigh_beta=0.0)), False, ['damping_ratio'],
       ['first_frequency', 'report_damping_ratio', 'report_rayleigh'])
expect('x05 alpha from f in Hz (zeta 0.32 %), honest report', run(X5, decay(decay_rows(zeta=0.003183)),
       dict(good_x5, damping_ratio=0.00318, rayleigh_alpha=0.668)), False, ['damping_ratio'],
       ['first_frequency', 'report_damping_ratio', 'report_rayleigh'])
expect('x05 beta from f in Hz (zeta 12.6 %)', run(X5, decay(decay_rows(zeta=0.1257, ripple=0.0)),
       dict(good_x5, damping_ratio=0.1257, rayleigh_alpha=0.0, rayleigh_beta=2.3937e-3)), False, ['damping_ratio'],
       ['first_frequency', 'report_rayleigh'])
expect('x05 numerical damping on top (zeta 4.6 %), coefficients as specified', run(X5, decay(decay_rows(zeta=0.0462)),
       dict(good_x5, damping_ratio=0.0462), inp_text=INP5), False, ['damping_ratio', 'report_rayleigh'],
       ['report_damping_ratio', 'report_rayleigh_inp'])
expect('x05 target values reported, model is undamped', run(X5, decay(decay_rows(zeta=0.0)), good_x5), False,
       ['damping_ratio', 'report_damping_ratio', 'report_rayleigh'])
expect('x05 load never removed', run(X5, decay(decay_rows(removed=False)),
       dict(good_x5, first_frequency_Hz=0.0, damping_ratio=0.0)), False,
       ['first_frequency', 'damping_ratio', 'free_vibration_duration'], ['static_deflection', 'tip_probe'])
expect('x05 frames every 0.01 s', run(X5, decay(decay_rows(dt=0.01)), good_x5, inp_text=INP5), False,
       ['output_interval'], ['first_frequency', 'damping_ratio', 'free_vibration_duration'])
expect('x05 only 0.3 s of free vibration', run(X5, decay(decay_rows(t_end=0.3)), good_x5, inp_text=INP5), False,
       ['free_vibration_duration'], ['first_frequency', 'damping_ratio', 'output_interval'])
expect('x05 beam not at the prescribed place (nearest node 0.3 m away)', run(X5, decay(decay_rows(), dist=0.3),
       good_x5), False, ['tip_probe', 'static_deflection', 'first_frequency'])
expect('x05 tip force 20 N', run(X5, decay(decay_rows(amp=2.381e-3)), good_x5, inp_text=INP5), False,
       ['static_deflection'], ['first_frequency', 'damping_ratio'])
expect('x05 logarithmic decrement reported as damping ratio', run(X5, decay(decay_rows()),
       dict(good_x5, damping_ratio=0.1257), inp_text=INP5), False, ['report_damping_ratio'],
       ['damping_ratio', 'report_rayleigh'])
expect('x05 other coefficients in the input file than reported', run(X5, decay(decay_rows()),
       dict(good_x5, rayleigh_alpha=1.0, rayleigh_beta=2.9027e-4), inp_text=INP5), True, ['report_rayleigh_inp'],
       ['report_rayleigh'])


# ------------------------------------------------------------------ x06: block sliding on a rigid plane
def block(rf1=(120.0, -2999.7), rf2=(10000.0, 0.0), sum1=(0.0, 3414.0, -3414.0), sum2=(10000.0, 10000.0, 0.0),
          u1=1.0194e-3, cpress=1.30e6, cshear=-0.39e6, slip=9.99e-4, contact=True):
    fields = {'RF': vec('RF', {'RF1': rf1, 'RF2': rf2, 'RF3': (50.0, -50.0)},
                        sums={'RF1': sum1, 'RF2': sum2, 'RF3': (0.0, 300.0, -300.0)}),
              'U': vec('U', {'U1': (u1, 0.0), 'U2': (0.0, -5.0e-5), 'U3': (1.0e-5, -1.0e-5)}, mag=u1)}
    if contact:
        pair = '   ASSEMBLY_BOTTOM/ASSEMBLY_PLANE'
        fields['CPRESS' + pair] = {'type': 'SCALAR', 'max': cpress, 'min': 0.0}
        fields['CSHEAR1' + pair] = {'type': 'SCALAR', 'max': 0.0, 'min': cshear}
        fields['CSHEAR2' + pair] = {'type': 'SCALAR', 'max': 2.0e3, 'min': -2.0e3}
        fields['CSLIP1' + pair] = {'type': 'SCALAR', 'max': slip, 'min': 0.0}
        fields['CSLIP2' + pair] = {'type': 'SCALAR', 'max': 1.0e-6, 'min': -1.0e-6}
    return {'steps': [{'name': 'Press', 'procedure': '*STATIC'}, {'name': 'Push', 'procedure': '*STATIC'}],
            'elementTypes': {'C3D8R': 512}, 'frame': {'step': 'Push', 'available': list(fields)}, 'fields': fields}


X6 = 'x06_friction_slip'
good_x6 = {'normal_force_N': 10000.0, 'sliding_force_N': 2999.7}
expect('x06 sliding block, plane held at its reference point', run(X6, block(), good_x6), True)
expect('x06 frictionless: +-414 N on the pushed face must not count', run(X6, block(
    rf1=(20.7, -20.7), sum1=(0.0, 414.2, -414.2), cshear=0.0), dict(good_x6, sliding_force_N=0.0)), False,
    ['sliding_force', 'friction_ratio'], ['normal_force', 'push_distance'])
expect('x06 friction coefficient 0.03', run(X6, block(rf1=(30.0, -300.0), sum1=(0.0, 566.0, -566.0), cshear=-3.9e4),
       dict(good_x6, sliding_force_N=300.0)), False, ['sliding_force', 'friction_ratio'],
       ['normal_force', 'report_sliding_force_N'])
expect('x06 1 kN normal load (ratio still 0.3)', run(X6, block(rf1=(12.0, -300.0), rf2=(1000.0, 0.0),
       sum1=(0.0, 317.0, -317.0), sum2=(1000.0, 1000.0, 0.0), cpress=1.3e5, cshear=-3.9e4),
       {'normal_force_N': 1000.0, 'sliding_force_N': 300.0}), False, ['normal_force', 'sliding_force'],
       ['friction_ratio'])
expect('x06 pushed 0.02 mm only (sticking)', run(X6, block(rf1=(60.0, -1062.4), sum1=(0.0, 1241.0, -1241.0),
       u1=3.76e-5, cshear=-1.6e5, slip=2.6e-5), dict(good_x6, sliding_force_N=1062.4)), False,
       ['sliding_force', 'push_distance', 'slip_distance'], ['normal_force'])
expect('x06 pushed with 2 kN force', run(X6, block(rf1=(0.0, -2000.0), sum1=(-2000.0, 0.0, -2000.0), u1=8.2e-5,
       cshear=-2.7e5, slip=4.4e-5), dict(good_x6, sliding_force_N=2000.0)), False,
       ['sliding_force', 'friction_ratio', 'push_distance'])
expect('x06 bonded interface (476 kN)', run(X6, block(rf1=(9.0e3, -4.765e5), sum1=(0.0, 4.9e5, -4.9e5), u1=1.19e-3,
       cshear=-2.2e7), dict(good_x6, sliding_force_N=4.765e5)), False, ['sliding_force', 'friction_ratio'])
expect('x06 plane held at many nodes instead of one reference point', run(X6, block(
    rf1=(40.0, -61.0), rf2=(210.0, 0.0)), good_x6), False,
    ['normal_force', 'sliding_force', 'report_normal_force_N', 'report_sliding_force_N'], ['push_distance'])
expect('x06 block also held vertically on the pushed face (9 kN reach the plane)', run(X6, block(
    rf1=(150.0, -2700.0), rf2=(9000.0, 0.0), sum2=(10000.0, 10000.0, 0.0)),
    {'normal_force_N': 9000.0, 'sliding_force_N': 2700.0}), False, ['normal_force', 'sliding_force'],
    ['friction_ratio', 'report_normal_force_N'])
expect('x06 no contact output in the ODB', run(X6, block(contact=False), good_x6), True,
       ['contact_stress_ratio', 'slip_distance'])
expect('x06 sliding force reported in kN', run(X6, block(), dict(good_x6, sliding_force_N=2.9997)), False,
       ['report_sliding_force_N'], ['sliding_force'])
expect('x06 theoretical 3000 N reported, ODB has 300 N', run(X6, block(rf1=(30.0, -300.0), cshear=-3.9e4),
       dict(good_x6, sliding_force_N=3000.0)), False, ['sliding_force', 'report_sliding_force_N'])

# ------------------------------------------------------------------ x07: plastic collapse of a beam
K7, PY7, PC7 = 4.9622e6, 42087.0, 63136.0          # elastic stiffness, first yield and plateau of the 100 x B21 model
WY7 = PY7 / K7


def p_ideal(w, pc=PC7, k=K7):
    """elastic up to 2/3 of the plateau, then a quick approach of the plateau"""
    py = pc * PY7 / PC7
    wy = py / k
    return k * w if w <= wy else pc - (pc - py) * math.exp(-(w - wy) / 1.5e-3)


def w_under_force(p, pc=PC7, k=K7):
    py = pc * PY7 / PC7
    return p / k if p <= py else py / k + 1.5e-3 * math.log((pc - py) / (pc - p))


def collapse(p_of_t, w_of_t, n=51, control='displacement', half=False, messy_supports=False):
    """callable replacing Checker.extract: frame k of n equally spaced frames (default: the last one)"""
    times = [i / float(n - 1) for i in range(n)]
    share = 0.5 if half else 1.0                    # part of the load carried by the modelled supports

    def extract(vars=None, probes=(), series=None, step=None, frame=None, history=False):
        k = n - 1 if frame is None else frame
        p, w = share * p_of_t(times[k]), w_of_t(times[k])
        if control == 'force':
            comps, sums = (p / 2.0, 0.0), (p, p, 0.0)
        elif half:
            comps, sums = (p, -p), (0.0, p, -p)
        elif messy_supports:                        # continuum: support reactions of both signs, one loaded node
            comps, sums = (0.4 * p, -p), (0.0, 1.1 * p, -1.1 * p)
        else:
            comps, sums = (p / 2.0, -p), (0.0, p, -p)
        rows_u = [[t, 0.0, -w_of_t(t)] for t in times]
        rows_rf = [[t, 0.0, share * p_of_t(t) / (1.0 if half else 2.0)] for t in times]
        return {'steps': [{'name': 'Push', 'procedure': '*STATIC', 'nFrames': n, 'totalTime': 0.0, 'timePeriod': 1.0}],
                'frame': {'step': 'Push', 'index': k, 'available': ['RF', 'U']},
                'bbox': [[0.0, 0.0, 0.0], [1.0 if half else 2.0, 0.0, 0.0]], 'elementTypes': {'B21': 100},
                'fields': {'RF': vec('RF', {'RF1': (0.0, 0.0), 'RF2': comps}, sums={'RF1': (0.0, 0.0, 0.0), 'RF2': sums}),
                           'U': vec('U', {'U1': (0.0, 0.0), 'U2': (0.0, -w)}, mag=w)},
                'probes': [{'node': {'label': 2, 'distance': 0.0, 'coords': [1.0, 0.0, 0.0]},
                            'series': {'U': {'columns': ['time', 'U1', 'U2'], 'rows': rows_u},
                                       'RF': {'columns': ['time', 'RF1', 'RF2'], 'rows': [[t, 0.0, 0.0] for t in times]}}},
                           {'node': {'label': 1, 'distance': 0.3 if messy_supports else 0.0, 'coords': [0.0, 0.0, 0.0]},
                            'series': {'U': {'columns': ['time', 'U1', 'U2'], 'rows': [[t, 0.0, 0.0] for t in times]},
                                       'RF': {'columns': ['time', 'RF1', 'RF2'], 'rows': rows_rf}}}]}
    return extract


X7 = 'x07_plastic_collapse_beam'
good_x7 = {'collapse_load_N': PC7, 'first_yield_load_N': PY7}
push50 = lambda t: 0.05 * t                                                                    # noqa: E731
expect('x07 displacement control to 50 mm, plateau', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50), good_x7),
       True)
expect('x07 Riks-like run (force loading, plateau reached)', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50,
       control='force'), good_x7), True)
expect('x07 half (symmetry) model: reactions doubled', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50,
       half=True), good_x7), True)
expect('x07 half model reporting the half load', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50, half=True),
       dict(good_x7, collapse_load_N=PC7 / 2.0)), False, ['report_collapse_load_N'], ['collapse_load', 'plateau_reached'])
expect('x07 clamping support patches (nodal reactions of both signs inflate the sum)', run(X7, collapse(
    lambda t: p_ideal(0.05 * t, pc=64078.0), push50, messy_supports=True), dict(good_x7, collapse_load_N=64078.0)),
    False, ['collapse_load', 'report_collapse_load_N'], ['plateau_reached'])
expect('x07 built-in beam under force control: one support carries 63.8 kN, the load is 127.6 kN', run(X7, collapse(
    lambda t: p_ideal(0.05 * t, pc=127551.0, k=1.9408e7), push50, control='force'),
    {'collapse_load_N': 127551.0, 'first_yield_load_N': 85034.0}), False, ['collapse_load'], ['plateau_reached'])
expect('x07 force control stopped at 55 kN', run(X7, collapse(lambda t: 55000.0 * t,
       lambda t: w_under_force(55000.0 * t), control='force'), dict(good_x7, collapse_load_N=55000.0)), False,
       ['collapse_load', 'plateau_reached'])
expect('x07 force control stopped just below collapse (62 kN)', run(X7, collapse(lambda t: 62000.0 * t,
       lambda t: w_under_force(62000.0 * t), control='force'), dict(good_x7, collapse_load_N=62000.0)), False,
       ['plateau_reached'], ['collapse_load'])
expect('x07 62 kN applied and then held (nothing moves)', run(X7, collapse(lambda t: 62000.0 * min(1.0, 2.0 * t),
       lambda t: w_under_force(62000.0 * min(1.0, 2.0 * t)), control='force'), dict(good_x7, collapse_load_N=62000.0)),
       False, ['plateau_reached'], ['collapse_load'])
expect('x07 pushed 9 mm only', run(X7, collapse(lambda t: p_ideal(0.009 * t), lambda t: 0.009 * t),
       dict(good_x7, collapse_load_N=p_ideal(0.009))), False, ['collapse_load', 'plateau_reached'])
expect('x07 elastic material', run(X7, collapse(lambda t: K7 * 0.05 * t, push50),
       dict(good_x7, collapse_load_N=K7 * 0.05)), False, ['collapse_load', 'plateau_reached'], ['elastic_stiffness'])
expect('x07 hardening: the load keeps growing', run(X7, collapse(
    lambda t: p_ideal(0.05 * t) + 4.0e5 * max(0.0, 0.05 * t - WY7), push50),
    dict(good_x7, collapse_load_N=p_ideal(0.05) + 4.0e5 * (0.05 - WY7))), False, ['collapse_load', 'plateau_reached'])
expect('x07 both ends built in (8 Mp / L)', run(X7, collapse(lambda t: p_ideal(0.05 * t, pc=127551.0, k=1.9408e7),
       push50), {'collapse_load_N': 127551.0, 'first_yield_load_N': 85034.0}), False,
       ['collapse_load', 'elastic_stiffness'], ['plateau_reached'])
expect('x07 20 linear elements (+5.3 %)', run(X7, collapse(lambda t: p_ideal(0.05 * t, pc=65795.0), push50),
       dict(good_x7, collapse_load_N=65795.0)), False, ['collapse_load'], ['plateau_reached'])
expect('x07 a single increment (two frames)', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50, n=2), good_x7),
       False, ['plateau_reached'], ['collapse_load'])
expect('x07 job stopped by a singularity at the plateau', run(X7, collapse(lambda t: p_ideal(0.0146 * t),
       lambda t: 0.0146 * t, n=25), dict(good_x7, collapse_load_N=p_ideal(0.0146)), sta_ok=False), False,
       ['job_completed'])
expect('x07 collapse load reported in kN', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50),
       dict(good_x7, collapse_load_N=63.136)), False, ['report_collapse_load_N'], ['collapse_load'])
expect('x07 first-yield load 30 % off (not critical)', run(X7, collapse(lambda t: p_ideal(0.05 * t), push50),
       dict(good_x7, first_yield_load_N=55000.0)), True, ['report_first_yield_load_N'])


# ------------------------------------------------------------------ x08: torsion through a coupled reference point
def shaft(ur=0.012316, umax=2.4632e-4, mises=6.8916e7, mises_ip=None, cm=(0.0, 0.0, 500.0), rm=None):
    s3 = math.sqrt(3.0)
    ip = mises if mises_ip is None else mises_ip
    fields = {'U': vec('U', {'U1': (umax, -umax), 'U2': (umax, -umax), 'U3': (1.0e-9, -1.0e-9)}, mag=umax),
              'S': {'type': 'TENSOR_3D_FULL',
                    'invariants': {'mises': {'max': ip, 'min': 0.0}, 'tresca': {'max': 2.0 * ip / s3, 'min': 0.0}},
                    'elementNodal': {'mises': {'max': mises, 'min': 0.0},
                                     'tresca': {'max': 2.0 * mises / s3, 'min': 0.0}}}}
    if ur is not None:
        fields['UR'] = vec('UR', {'UR1': (0.0, 0.0), 'UR2': (0.0, 0.0), 'UR3': (ur, 0.0)}, mag=ur)
    if cm is not None:
        fields['CM'] = vec('CM', dict(('CM%d' % (i + 1), (max(v, 0.0), min(v, 0.0))) for i, v in enumerate(cm)),
                           sums=dict(('CM%d' % (i + 1), (v, max(v, 0.0), min(v, 0.0))) for i, v in enumerate(cm)))
    if rm is not None:
        fields['RM'] = vec('RM', {'RM1': (0.0, 0.0), 'RM2': (0.0, 0.0), 'RM3': (0.0, -rm)},
                           sums={'RM1': (0.0, 0.0, 0.0), 'RM2': (0.0, 0.0, 0.0), 'RM3': (-rm, 0.0, -rm)})
    return {'steps': [{'name': 'Twist', 'procedure': '*STATIC'}], 'elementTypes': {'C3D20R': 4000},
            'frame': {'step': 'Twist', 'available': list(fields)}, 'fields': fields}


X8 = 'x08_torsion_coupling'
TAU8 = 3.9789e7
good_x8 = {'twist_angle_rad': 0.012316, 'max_shear_stress_Pa': TAU8}
expect('x08 quadratic hexahedra, kinematic coupling', run(X8, shaft(), good_x8), True)
expect('x08 torque only visible as reaction moment at a second RP', run(X8, shaft(cm=None, rm=500.0), good_x8), True)
expect('x08 no CM / RM output (not critical)', run(X8, shaft(cm=None), good_x8), True, ['applied_torque'])
expect('x08 Tresca / 2 reported', run(X8, shaft(), dict(good_x8, max_shear_stress_Pa=3.979e7)), True)
expect('x08 coarse C3D8R: centroid stress 12 % low', run(X8, shaft(ur=0.01262, mises=6.03e7),
       {'twist_angle_rad': 0.01262, 'max_shear_stress_Pa': 6.03e7 / math.sqrt(3.0)}), False, ['max_shear_stress'])
expect('x08 radius 0.04 instead of 0.02', run(X8, shaft(ur=7.697e-4, umax=3.079e-5, mises=8.6145e6),
       {'twist_angle_rad': 7.697e-4, 'max_shear_stress_Pa': 4.9736e6}), False, ['twist_angle', 'max_shear_stress'],
       ['applied_torque'])
expect('x08 moment about a transverse axis (bending)', run(X8, shaft(ur=9.474e-3, umax=2.37e-3, mises=7.96e7,
       cm=(500.0, 0.0, 0.0)), {'twist_angle_rad': 9.474e-3, 'max_shear_stress_Pa': 4.59e7}), False,
       ['twist_angle', 'max_shear_stress'], ['applied_torque'])
expect('x08 coupling to four rim nodes (stress concentration)', run(X8, shaft(ur=0.0131, umax=2.62e-4, mises=4.1e8),
       {'twist_angle_rad': 0.0131, 'max_shear_stress_Pa': 2.37e8}), False, ['max_shear_stress'])
expect('x08 rim forces with half the torque, no reference point', run(X8, shaft(ur=None, umax=1.25e-4, mises=9.0e7,
       cm=None), {'twist_angle_rad': 6.25e-3, 'max_shear_stress_Pa': 5.2e7}), False, ['twist_angle'])
expect('x08 250 Nm at the reference point', run(X8, shaft(ur=0.006158, umax=1.2316e-4, mises=3.4458e7,
       cm=(0.0, 0.0, 250.0)), {'twist_angle_rad': 0.006158, 'max_shear_stress_Pa': 1.9894e7}), False,
       ['twist_angle', 'max_shear_stress', 'applied_torque'])
expect('x08 von Mises stress reported as shear stress', run(X8, shaft(),
       dict(good_x8, max_shear_stress_Pa=6.8916e7)), False, ['report_max_shear_stress_Pa'],
       ['twist_angle', 'max_shear_stress'])
expect('x08 twist reported in degrees', run(X8, shaft(), dict(good_x8, twist_angle_rad=0.7056)), False,
       ['report_twist_angle_rad'], ['twist_angle'])
expect('x08 theoretical stress reported, ODB 12 % lower', run(X8, shaft(mises=6.03e7), good_x8), False,
       ['max_shear_stress', 'report_max_shear_stress_Pa'])


# ====================================================================================== domain suite d01 - d08
# ------------------------------------------------------------------ d01: site response, viscous boundary
def ricker(t, fc=4.0, t0=0.3, peak=0.1):
    a = (math.pi * fc * (t - t0)) ** 2
    return peak * (1.0 - 2.0 * a) * math.exp(-a)


def site(surface, dt=0.005, t_end=1.5, dist=0.0, static_before=False, with_v=True, eigen_step=False):
    """surface(t) = horizontal surface velocity; U1 is its running integral"""
    n = int(round(t_end / dt)) + 1
    t0 = 1.0 if static_before else 0.0
    rows_v = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]] if static_before else []
    rows_u = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]] if static_before else []
    u = 0.0
    for i in range(n):
        t = i * dt
        if i:
            u += 0.5 * dt * (surface(t) + surface(t - dt))
        rows_v.append([t0 + t, surface(t), 0.0])
        rows_u.append([t0 + t, u, 0.0])
    steps = [{'name': 'Static', 'procedure': '*STATIC', 'nFrames': 2, 'totalTime': 0.0, 'timePeriod': 1.0}] \
        if static_before else []
    freqs = []
    if eigen_step:                                 # its mode-shape frames are not part of a probe series
        steps.append({'name': 'Modes', 'procedure': '*FREQUENCY', 'nFrames': 6, 'totalTime': t0, 'timePeriod': 0.0})
        freqs = [{'step': 'Modes', 'mode': 1, 'frequency': 1.0}]
    steps.append({'name': 'Dyn', 'procedure': '*DYNAMIC', 'nFrames': n, 'totalTime': t0, 'timePeriod': t_end})
    series = {'U': {'columns': ['time', 'U1', 'U2'], 'rows': rows_u}}
    if with_v:
        series['V'] = {'columns': ['time', 'V1', 'V2'], 'rows': rows_v}
    return {'steps': steps, 'frequencies': freqs, 'frame': {'step': 'Dyn', 'available': ['U', 'V']}, 'fields': {},
            'probes': [{'node': {'label': 51, 'distance': dist, 'coords': [0.0, 50.0, 0.0]}, 'series': series}]}


D1 = 'd01_site_response_viscous_boundary'
good_d1 = {'peak_surface_velocity_m_s': 0.2, 'time_of_peak_s': 0.55, 'late_residual_ratio': 0.001}
free_field = lambda t: 2.0 * ricker(t - 0.25)                                                   # noqa: E731
trapped = lambda t: 2.0 * (ricker(t - 0.25) - ricker(t - 0.75) + ricker(t - 1.25))             # noqa: E731
expect('d01 absorbing base: 2 x incident, delayed by H/Vs', run(D1, site(free_field), good_d1), True)
expect('d01 a static step before the dynamic one (time origin)', run(D1, site(free_field, static_before=True),
       good_d1), True)
expect('d01 a frequency step in the same job', run(D1, site(free_field, eigen_step=True), good_d1), True)
expect('d01 no V output: velocity from U by differences', run(D1, site(free_field, with_v=False), good_d1), True)
expect('d01 rigid base (wave trapped)', run(D1, site(trapped), dict(good_d1, late_residual_ratio=1.0)), False,
       ['late_residual'], ['peak_velocity', 'peak_time', 'report_late_residual_ratio'])
expect('d01 equivalent force with factor 1', run(D1, site(lambda t: ricker(t - 0.25)),
       dict(good_d1, peak_surface_velocity_m_s=0.1)), False, ['peak_velocity'], ['peak_time', 'late_residual'])
expect('d01 dashpot with Vp (30 % reflected)', run(D1, site(
    lambda t: 1.3946 * ricker(t - 0.25) - 0.4244 * ricker(t - 0.75) + 0.129 * ricker(t - 1.25)),
    {'peak_surface_velocity_m_s': 0.13946, 'time_of_peak_s': 0.55, 'late_residual_ratio': 0.304}), False,
    ['peak_velocity', 'late_residual'], ['peak_time'])
expect('d01 wave arrives 0.03 s late (wave speed off)', run(D1, site(lambda t: 2.0 * ricker(t - 0.28)),
       dict(good_d1, time_of_peak_s=0.58)), False, ['peak_time'], ['peak_velocity', 'late_residual'])
expect('d01 frames every 0.01 s', run(D1, site(free_field, dt=0.01), good_d1), False, ['output_interval'],
       ['peak_velocity', 'peak_time'])
expect('d01 only 1.2 s computed', run(D1, site(free_field, t_end=1.2), good_d1), False, ['duration'],
       ['peak_velocity', 'late_residual'])
expect('d01 column not at the prescribed place', run(D1, site(free_field, dist=12.0), good_d1), False,
       ['surface_probe', 'peak_velocity'])
expect('d01 wavelet time t0 reported as the time of the peak', run(D1, site(free_field),
       dict(good_d1, time_of_peak_s=0.3)), False, ['report_time_of_peak_s'], ['peak_time'])
expect('d01 residual reported as zero while the wave is trapped', run(D1, site(trapped), good_d1), False,
       ['late_residual', 'report_late_residual_ratio'])


# ------------------------------------------------------------------ d02: Terzaghi consolidation
def terzaghi(tv):
    """(excess pore pressure at the impervious base / q, average degree of consolidation)"""
    u = s = 0.0
    for k in range(80):
        mm = math.pi * (2 * k + 1) / 2.0
        e = math.exp(-mm * mm * tv)
        u += 2.0 / mm * math.sin(mm) * e
        s += 2.0 / (mm * mm) * e
    return u, 1.0 - s


T_TV = 145600.0


def consol(times=None, speed=1.0, hydro=0.0, final=0.0742857, dist=0.0, q=100.0):
    if times is None:
        times = [300.0 * 1.25 ** k for k in range(27)] + [T_TV] + [T_TV * 1.25 ** k for k in range(1, 13)]
    rows_p, rows_u = [[0.0, hydro]], [[0.0, 0.0, 0.0]]
    for t in sorted(times):
        ub, deg = terzaghi(speed * 0.2 * t / T_TV)
        rows_p.append([t, hydro + q * ub])
        rows_u.append([t, 0.0, -final * deg])
    node = {'label': 1, 'distance': dist, 'coords': [0.0, 0.0, 0.0]}
    return {'steps': [{'name': 'Consol', 'procedure': '*SOILS', 'nFrames': len(rows_p), 'totalTime': 0.0,
                       'timePeriod': rows_p[-1][0]}], 'frequencies': [],
            'frame': {'step': 'Consol', 'available': ['POR', 'U']}, 'fields': {},
            'probes': [{'node': node, 'series': {'POR': {'columns': ['time', 'POR'], 'rows': rows_p}}},
                       {'node': node, 'series': {'U': {'columns': ['time', 'U1', 'U2'], 'rows': rows_u}}}]}


D2 = 'd02_terzaghi_consolidation'
good_d2 = {'excess_pore_pressure_base_kPa': 77.23, 'settlement_at_tv_m': 0.037446, 'final_settlement_m': 0.07424}
expect('d02 Terzaghi solution', run(D2, consol(), good_d2), True)
expect('d02 model with hydrostatic pore pressure (98 kPa at the base)', run(D2, consol(hydro=98.0), good_d2), True)
expect('d02 no frame at Tv = 0.2', run(D2, consol(times=[300.0 * 1.3 ** k for k in range(35)]), good_d2), False,
       ['frame_at_tv', 'excess_pore_pressure_base', 'settlement_at_tv'], ['final_settlement'])
expect('d02 specific weight of water 1.0 (9.8 times too fast)', run(D2, consol(speed=9.8),
       {'excess_pore_pressure_base_kPa': 1.24, 'settlement_at_tv_m': 0.0737, 'final_settlement_m': 0.074286}),
       False, ['excess_pore_pressure_base', 'settlement_at_tv'], ['final_settlement', 'initial_excess_pressure'])
expect('d02 specific weight 9800 (1000 times too slow)', run(D2, consol(speed=0.001),
       {'excess_pore_pressure_base_kPa': 100.0, 'settlement_at_tv_m': 0.0015, 'final_settlement_m': 0.0047}),
       False, ['excess_pore_pressure_base', 'settlement_at_tv', 'final_settlement'])
expect('d02 sides free (final settlement +22 %)', run(D2, consol(final=0.090593),
       {'excess_pore_pressure_base_kPa': 77.23, 'settlement_at_tv_m': 0.045666, 'final_settlement_m': 0.0906}),
       False, ['settlement_at_tv', 'final_settlement'])
expect('d02 stopped at Tv = 0.6', run(D2, consol(times=[300.0 * 1.25 ** k for k in range(27)] + [T_TV, 2 * T_TV,
       3 * T_TV]), dict(good_d2, final_settlement_m=0.0606)), False, ['final_settlement'],
       ['excess_pore_pressure_base', 'settlement_at_tv'])
expect('d02 load ramped (largest excess pressure 92 kPa)', run(D2, consol(q=92.3),
       {'excess_pore_pressure_base_kPa': 71.3, 'settlement_at_tv_m': 0.037446, 'final_settlement_m': 0.07424}),
       False, ['initial_excess_pressure', 'excess_pore_pressure_base'])
expect('d02 total pore pressure reported (hydrostatic model)', run(D2, consol(hydro=98.0),
       dict(good_d2, excess_pore_pressure_base_kPa=175.23)), False, ['report_excess_pore_pressure_base_kPa'],
       ['excess_pore_pressure_base'])
expect('d02 column not at the prescribed place', run(D2, consol(dist=5.0), good_d2), False,
       ['probes', 'excess_pore_pressure_base'])


# ------------------------------------------------------------------ d04: CDP element in tension
def cdp_path(damage=True, unload=True, lateral=0.0, de=1.0e-5, stress_scale=1.0, edge=0.1, damage_field=True):
    """strain / stress / damage path from Abaqus' definitions, tables interpolated in the cracking strain"""
    e0 = 3.0e7
    tab_s = [(0.0, 2000.0), (0.0002, 1200.0), (0.0005, 600.0), (0.001, 200.0)]
    tab_d = [(0.0, 0.0), (0.0002, 0.3), (0.0005, 0.6), (0.001, 0.85)]

    def interp(tab, x):
        for (x0, y0), (x1, y1) in zip(tab[:-1], tab[1:]):
            if x <= x1:
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return tab[-1][1]

    def backbone(eps):
        if eps * e0 <= 2000.0:
            return eps * e0, 0.0
        lo, hi = 0.0, 0.001
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if mid + interp(tab_s, mid) / e0 < eps:
                lo = mid
            else:
                hi = mid
        return interp(tab_s, lo), (interp(tab_d, lo) if damage else 0.0)

    eps, sig, dam = [0.0], [0.0], [0.0]

    def load_to(target):
        while eps[-1] < target - 1e-12:
            e = min(target, eps[-1] + de)
            s, d = backbone(e)
            eps.append(e)
            sig.append(s)
            dam.append(d)

    load_to(0.0004)
    if unload:
        s_a, d_a = sig[-1], dam[-1]
        for k in range(1, 5):
            e = 0.0004 - 1.0e-5 * k
            eps.append(e)
            sig.append(s_a - (1.0 - d_a) * e0 * (0.0004 - e))
            dam.append(d_a)
        for k in range(1, 4):
            e = 0.00036 + 1.0e-5 * k
            eps.append(e)
            sig.append(s_a - (1.0 - d_a) * e0 * (0.0004 - e))
            dam.append(d_a)
        eps.append(0.0004)
        sig.append(s_a)
        dam.append(d_a)
    load_to(0.0008)
    sig = [stress_scale * s for s in sig]
    t = [float(i) for i in range(len(eps))]
    node = {'label': 2, 'distance': 0.0, 'coords': [0.1, 0.0, 0.0]}
    series = {'S': {'columns': ['time', 'S11', 'S22', 'S33', 'S12', 'S13', 'S23', 'mises'],
                    'rows': [[ti, s, lateral * s, lateral * s, 0.0, 0.0, 0.0, abs(s)] for ti, s in zip(t, sig)]}}
    fields = {}
    if damage_field:
        series['DAMAGET'] = {'columns': ['time', 'DAMAGET'], 'rows': [[ti, d] for ti, d in zip(t, dam)]}
        fields['DAMAGET'] = {'type': 'SCALAR', 'max': dam[-1], 'min': dam[-1]}
    return {'steps': [{'name': 'Test', 'procedure': '*STATIC', 'nFrames': len(eps), 'totalTime': 0.0,
                       'timePeriod': 1.0}], 'frequencies': [], 'frame': {'step': 'Test', 'available': list(fields)},
            'fields': fields, 'elementTypes': {'C3D8R': 1},
            'probes': [{'node': node, 'series': {'U': {'columns': ['time', 'U1', 'U2', 'U3'],
                                                       'rows': [[ti, e * edge, 0.0, 0.0] for ti, e in zip(t, eps)]}}},
                       {'node': node, 'element': {'label': 1, 'centroid': [0.05, 0.05, 0.05], 'distance': 0.0},
                        'series': series}]}


D4 = 'd04_cdp_tension_damage_element'
good_d4 = {'peak_stress_kPa': 1985.0, 'stress_at_0p0004_kPa': 857.14, 'unloading_modulus_kPa': 1.5857e7,
           'damage_at_end': 0.7438}
expect('d04 hand values (tables linear in the cracking strain)', run(D4, cdp_path(), good_d4), True)
expect('d04 Abaqus-like values (stress 1 % above the hand values)', run(D4, cdp_path(stress_scale=1.0095),
       dict(good_d4, stress_at_0p0004_kPa=865.3, unloading_modulus_kPa=1.6008e7, peak_stress_kPa=2003.8)), True)
expect('d04 no tension damage: unloading with E0', run(D4, cdp_path(damage=False, damage_field=False),
       dict(good_d4, unloading_modulus_kPa=3.0e7, damage_at_end=0.0)), False,
       ['unloading_modulus', 'damage_at_end'], ['peak_stress', 'stress_at_0p0004'])
expect('d04 monotonic loading, no unloading branch', run(D4, cdp_path(unload=False), good_d4), False,
       ['strain_at_A', 'unloading_modulus', 'stress_at_0p0004'], ['peak_stress', 'damage_at_end', 'strain_at_end'])
expect('d04 lateral faces held (lateral stress 25 %)', run(D4, cdp_path(lateral=0.25), good_d4), False,
       ['uniaxial_state'], ['stress_at_0p0004'])
expect('d04 frames every 4e-5 of strain', run(D4, cdp_path(de=4.0e-5), dict(good_d4, peak_stress_kPa=1856.0)),
       False, ['output_density', 'peak_stress'])
expect('d04 stresses in MPa', run(D4, cdp_path(stress_scale=0.001), {
    'peak_stress_kPa': 1.985, 'stress_at_0p0004_kPa': 0.857, 'unloading_modulus_kPa': 1.5857e4,
    'damage_at_end': 0.7438}), False, ['peak_stress', 'stress_at_0p0004', 'unloading_modulus'])
expect('d04 1 m specimen with the 0.1 m displacements (strain 10 times too small on our scale)', run(D4,
       cdp_path(edge=0.01), good_d4), False, ['strain_at_A', 'strain_at_end'])
expect('d04 E0 reported as the unloading modulus', run(D4, cdp_path(), dict(good_d4, unloading_modulus_kPa=3.0e7)),
       False, ['report_unloading_modulus_kPa'], ['unloading_modulus'])
expect('d04 damage reported 0.85 (last table value)', run(D4, cdp_path(), dict(good_d4, damage_at_end=0.85)),
       False, ['report_damage_at_end'], ['damage_at_end'])


# ------------------------------------------------------------------ d03: strip footing on undrained clay
def footing(p_of_t, w_of_t, n=51, mode='rp', half=True, noise=0.03):
    """callable replacing Checker.extract.  mode: 'rp' (footing driven through one reference node), 'nodes'
    (displacement on the footing nodes), 'force' (pressure on the footing).  p_of_t = load of the WHOLE footing."""
    times = [i / float(n - 1) for i in range(n)]
    share = 0.5 if half else 1.0

    def extract(vars=None, probes=(), series=None, step=None, frame=None, history=False):
        k = n - 1 if frame is None else frame
        p, w = share * p_of_t(times[k]), w_of_t(times[k])
        far = noise * p                              # uplift reactions of far boundary nodes: both signs grow
        if mode == 'rp':
            comps, sums = (0.02 * p, -p), (0.0, p + far, -(p + far))
        elif mode == 'nodes':
            comps, sums = (0.02 * p, -0.2 * p), (0.0, p + far, -(p + far))
        else:
            comps, sums = (0.02 * p, -0.001 * p), (p, p + far, -far)
        return {'steps': [{'name': 'Push', 'procedure': '*STATIC', 'nFrames': n, 'totalTime': 0.0, 'timePeriod': 1.0}],
                'frequencies': [], 'frame': {'step': 'Push', 'index': k, 'available': ['RF', 'U']},
                'bbox': [[0.0 if half else -12.0, -8.0, 0.0], [12.0, 0.0, 0.0]], 'elementTypes': {'CPE8R': 1360},
                'fields': {'RF': vec('RF', {'RF1': (0.3 * p, -0.3 * p), 'RF2': comps},
                                     sums={'RF1': (0.0, p, -p), 'RF2': sums}),
                           'U': vec('U', {'U1': (0.1 * w, -0.1 * w), 'U2': (0.3 * w, -w)}, mag=w)},
                'probes': [{'node': {'label': 1, 'distance': 0.0, 'coords': [0.0, 0.0, 0.0]},
                            'series': {'U': {'columns': ['time', 'U1', 'U2'],
                                             'rows': [[t, 0.0, -w_of_t(t)] for t in times]}}}]}
    return extract


D3 = 'd03_strip_footing_bearing_capacity'
P_FE = 1054.1                                         # reference mesh: Prandtl 1028.3 + 2.5 %
curve = lambda t, pu=P_FE: pu * (1.0 - math.exp(-9.0 * t))                                      # noqa: E731
push = lambda t: 0.2 * t                                                                        # noqa: E731
good_d3 = {'ultimate_load_kN_per_m': P_FE}
expect('d03 half model, footing driven through a reference point', run(D3, footing(curve, push), good_d3), True)
expect('d03 full model', run(D3, footing(curve, push, half=False), good_d3), True)
expect('d03 half model, displacement on the footing nodes (3 % far-field reactions in the sum)', run(D3, footing(
    curve, push, mode='nodes'), good_d3), True)
expect('d03 Mises yield stress 2 cu (+15.5 %)', run(D3, footing(lambda t: curve(t, 1217.2), push),
       {'ultimate_load_kN_per_m': 1217.2}), False, ['collapse_load'], ['plateau_reached'])
expect('d03 coarse mesh (+9 %)', run(D3, footing(lambda t: curve(t, 1121.0), push), {'ultimate_load_kN_per_m': 1121.0}),
       False, ['collapse_load'], ['plateau_reached'])
expect('d03 linear elements, 0.125 m (6.3 % low)', run(D3, footing(lambda t: curve(t, 963.7), push),
       {'ultimate_load_kN_per_m': 963.7}), True)
expect('d03 linear elements, 0.2 m (10 % low)', run(D3, footing(lambda t: curve(t, 925.5), push),
       {'ultimate_load_kN_per_m': 925.5}), False, ['collapse_load'], ['plateau_reached'])
expect('d03 pushed too little (no plateau)', run(D3, footing(lambda t: curve(0.12 * t), lambda t: 0.024 * t),
       {'ultimate_load_kN_per_m': curve(0.12)}), False, ['collapse_load', 'plateau_reached'])
expect('d03 locking: load still climbing at the end', run(D3, footing(lambda t: 900.0 * t ** 0.5 + 300.0 * t, push),
       {'ultimate_load_kN_per_m': 1200.0}), False, ['collapse_load', 'plateau_reached'])
expect('d03 force control with 400 kPa', run(D3, footing(lambda t: 800.0 * t, lambda t: 0.03 * t * (1 + t),
       mode='force'), {'ultimate_load_kN_per_m': 800.0}), False, ['collapse_load', 'plateau_reached'])
expect('d03 load of the half model reported', run(D3, footing(curve, push), {'ultimate_load_kN_per_m': P_FE / 2}),
       False, ['report_ultimate_load_kN_per_m'], ['collapse_load', 'plateau_reached'])
expect('d03 Prandtl value reported, model 15 % stronger', run(D3, footing(lambda t: curve(t, 1217.2), push),
       {'ultimate_load_kN_per_m': 1028.3}), False, ['collapse_load', 'report_ultimate_load_kN_per_m'])


# ------------------------------------------------------------------ d06: Mohr-Coulomb triaxial element
def triax(axial=369.2, lateral=100.0, u_conf=1.6e-17, p_conf=100.0, strain=0.05, two_steps=True):
    def stress(a, l):
        return {'type': 'TENSOR_3D_FULL', 'invariants': {
            'minPrincipal': {'min': -a, 'max': -a}, 'maxPrincipal': {'min': -l, 'max': -l},
            'mises': {'min': a - l, 'max': a - l}, 'pressure': {'min': (a + 2 * l) / 3.0, 'max': (a + 2 * l) / 3.0}}}

    steps = [{'name': 'Shear', 'procedure': '*STATIC', 'nFrames': 53, 'totalTime': 1.0 if two_steps else 0.0}]
    if two_steps:
        steps.insert(0, {'name': 'Confine', 'procedure': '*STATIC', 'nFrames': 2, 'totalTime': 0.0})

    def extract(vars=None, probes=(), series=None, step=None, frame=None, history=False):
        if step == 'Confine':
            fields = {'U': vec('U', {'U1': (u_conf, 0.0), 'U2': (0.0, 0.0), 'U3': (u_conf, 0.0)}, mag=u_conf),
                      'S': stress(p_conf, p_conf)}
        else:
            fields = {'U': vec('U', {'U1': (0.02, 0.0), 'U2': (0.0, -strain), 'U3': (0.02, 0.0)}, mag=strain),
                      'S': stress(axial, lateral)}
        return {'steps': steps, 'frequencies': [], 'frame': {'step': step or 'Shear', 'available': ['S', 'U']},
                'fields': fields, 'elementTypes': {'C3D8R': 1}}
    return extract


D6 = 'd06_mc_triaxial_element'
good_d6 = {'axial_stress_failure_kPa': 369.2, 'deviator_stress_failure_kPa': 269.2,
           'confinement_max_displacement_m': 1.6e-17}
expect('d06 confinement as initial stress, failure at 369 kPa', run(D6, triax(), good_d6), True)
expect('d06 no separate confinement step', run(D6, triax(two_steps=False), good_d6), False,
       ['confinement_displacement', 'confinement_stress'], ['axial_stress_failure'])
expect('d06 cell pressure without initial stress (squeezed 1.5 mm)', run(D6, triax(u_conf=1.47e-3, p_conf=86.67),
       dict(good_d6, confinement_max_displacement_m=1.47e-3)), False,
       ['confinement_displacement', 'confinement_stress'],
       ['axial_stress_failure', 'report_confinement_max_displacement_m'])
expect('d06 friction angle in radians', run(D6, triax(axial=142.21),
       dict(good_d6, axial_stress_failure_kPa=142.21, deviator_stress_failure_kPa=42.21)), False,
       ['axial_stress_failure', 'deviator_stress_failure'], ['confinement_displacement', 'confining_stress'])
expect('d06 cell pressure dropped during shearing', run(D6, triax(axial=69.28, lateral=0.0),
       dict(good_d6, axial_stress_failure_kPa=69.28, deviator_stress_failure_kPa=69.28)), False,
       ['confining_stress', 'axial_stress_failure'], ['confinement_displacement'])
expect('d06 stopped at 0.3 % strain', run(D6, triax(axial=250.0, strain=0.003),
       dict(good_d6, axial_stress_failure_kPa=250.0, deviator_stress_failure_kPa=150.0)), False,
       ['axial_strain', 'axial_stress_failure'])
expect('d06 deviator reported as axial stress', run(D6, triax(), dict(good_d6, axial_stress_failure_kPa=269.2)),
       False, ['report_axial_stress_failure_kPa'], ['axial_stress_failure'])
expect('d06 zero reported for the confinement step, ODB has 1.5 mm', run(D6, triax(u_conf=1.47e-3, p_conf=86.67),
       good_d6), False, ['confinement_displacement', 'report_confinement_max_displacement_m'])
expect('d06 tension reported with a minus sign (magnitudes compared)', run(D6, triax(),
       dict(good_d6, axial_stress_failure_kPa=-369.2)), True)


# ------------------------------------------------------------------ d05: SDOF frame under a ground-motion record
def _d05_reference():
    path = os.path.join(EVAL, 'tasks', 'd05_sdof_seismic_time_history', 'reference', 'make_record.py')
    spec = importlib.util.spec_from_file_location('d05_make_record', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MK5 = _d05_reference()
DT5, ACC5_G = MK5.read_at2(MK5.ASSET)


def frame_history(moving=True, zeta=0.05, input_scale=MK5.G, every=1, t_end=15.0, dist=0.0, with_a=True):
    acc = [input_scale * v for v in ACC5_G]
    _pu, _pa, hist = MK5.sdof_exact(acc, DT5, MK5.OMEGA, zeta, t_end)
    ug = vg = 0.0
    rows_ur, rows_ub, rows_ar, rows_ab = [], [], [], []
    for i, (t, u, _v, a_abs) in enumerate(hist):
        a_g = acc[i] if i < len(acc) else 0.0
        if i:
            a_prev = acc[i - 1] if i - 1 < len(acc) else 0.0
            v_new = vg + 0.5 * DT5 * (a_prev + a_g)
            ug += 0.5 * DT5 * (vg + v_new)
            vg = v_new
        if i % every:
            continue
        base = ug if moving else 0.0
        rows_ub.append([t, base, 0.0])
        rows_ur.append([t, base + u, 0.0])
        rows_ab.append([t, a_g if moving else 0.0, 0.0])
        rows_ar.append([t, a_abs if moving else a_abs - a_g, 0.0])
    cols_u, cols_a = ['time', 'U1', 'U2'], ['time', 'A1', 'A2']
    roof = {'U': {'columns': cols_u, 'rows': rows_ur}}
    base_s = {'U': {'columns': cols_u, 'rows': rows_ub}}
    if with_a:
        roof['A'] = {'columns': cols_a, 'rows': rows_ar}
        base_s['A'] = {'columns': cols_a, 'rows': rows_ab}
    return {'steps': [{'name': 'Dyn', 'procedure': '*DYNAMIC', 'nFrames': len(rows_ur), 'totalTime': 0.0,
                       'timePeriod': t_end}], 'frequencies': [], 'fields': {},
            'frame': {'step': 'Dyn', 'available': ['U', 'A']},
            'probes': [{'node': {'label': 2, 'distance': dist, 'coords': [0.0, 4.0, 0.0]}, 'series': roof},
                       {'node': {'label': 1, 'distance': dist, 'coords': [0.0, 0.0, 0.0]}, 'series': base_s}]}


def with_frequency_step(ex):
    """the same job with a frequency step in front (its mode-shape frames are not part of the probe series)"""
    ex = dict(ex)
    ex['steps'] = [{'name': 'Modes', 'procedure': '*FREQUENCY', 'nFrames': 4, 'totalTime': 0.0, 'timePeriod': 0.0}] + \
        list(ex['steps'])
    ex['frequencies'] = [{'step': 'Modes', 'mode': 1, 'frequency': 1.7435}]
    return ex


D5 = 'd05_sdof_seismic_time_history'
good_d5 = {'peak_relative_displacement_m': 0.029150, 'peak_absolute_acceleration_m_s2': 3.5193}
expect('d05 moving base, exact SDOF response', run(D5, frame_history(), good_d5), True)
expect('d05 a frequency step in the same job', run(D5, with_frequency_step(frame_history()), good_d5), True)
expect('d05 fixed base with inertia force (ground acceleration added by the checker)', run(D5, frame_history(
    moving=False), good_d5), True)
expect('d05 record in g taken as m/s2', run(D5, frame_history(input_scale=1.0),
       {'peak_relative_displacement_m': 0.002975, 'peak_absolute_acceleration_m_s2': 0.35911}), False,
       ['peak_relative_displacement', 'peak_absolute_acceleration'], ['duration', 'output_interval'])
expect('d05 no damping', run(D5, frame_history(zeta=1.0e-9),
       {'peak_relative_displacement_m': 0.062463, 'peak_absolute_acceleration_m_s2': 7.4956}), False,
       ['peak_relative_displacement', 'peak_absolute_acceleration'])
expect('d05 frames every 0.02 s', run(D5, frame_history(every=2), good_d5), False, ['output_interval'],
       ['peak_relative_displacement'])
expect('d05 only 10 s computed', run(D5, frame_history(t_end=10.0), good_d5), False, ['duration'],
       ['peak_relative_displacement', 'peak_absolute_acceleration'])
expect('d05 no acceleration output', run(D5, frame_history(with_a=False), good_d5), False,
       ['peak_absolute_acceleration', 'report_peak_absolute_acceleration_m_s2'], ['peak_relative_displacement'])
expect('d05 absolute roof displacement reported', run(D5, frame_history(),
       dict(good_d5, peak_relative_displacement_m=0.2203)), False, ['report_peak_relative_displacement_m'],
       ['peak_relative_displacement'])
expect('d05 relative acceleration reported for a fixed-base model', run(D5, frame_history(moving=False),
       dict(good_d5, peak_absolute_acceleration_m_s2=2.6)), False, ['report_peak_absolute_acceleration_m_s2'],
       ['peak_absolute_acceleration'])
expect('d05 frame not at the prescribed place', run(D5, frame_history(dist=1.5), good_d5), False,
       ['probes', 'peak_relative_displacement'])


# ------------------------------------------------------------------ d07: beam on Winkler foundation
LAM7 = (24000.0 / (4.0 * 375000.0)) ** 0.25


def winkler(h=0.25, k_line=24000.0, with_sm=True, spring_elements=False, w_scale=1.0, sides=(1, -1), nodal=True,
            linear=False, nodal_at=20.0):
    """extractor output of a beam on a Winkler foundation meshed with elements of length h.
    sides: which halves of the beam exist (1: x > 20, -1: x < 20); nodal: with the element-nodal block of the
    extractor; linear: B21-like elements (one moment per element, so the nodal extrapolation gives the same value);
    nodal_at: x of the node that carries the extreme element-nodal moment"""
    lam = (k_line / (4.0 * 375000.0)) ** 0.25

    def moment(x):                                  # Hetenyi, x = distance from the load
        return 500.0 / (4.0 * lam) * math.exp(-lam * x) * (math.cos(lam * x) - math.sin(lam * x))

    import check_d07_offsets as off                 # the probe points the checker asks for
    probes = []
    for sgn in (1, -1):                             # the checker probes the +x side first, then the -x side
        for dx in off.OFFSETS:
            if sgn in sides:
                k = int(dx / h)                     # element number k covers [k h, (k + 1) h] from the load
                side = sgn
            else:
                k, side = 0, -sgn                   # nothing on that side: the nearest element is across the load
            xc = 20.0 + side * (k + 0.5) * h
            pr = {'node': {'label': 1, 'distance': 0.0},
                  'element': {'instance': 'BEAM-1', 'label': (1000 if side > 0 else 3000) + k, 'centroid': [xc, 0.0, 0.0],
                              'distance': abs(20.0 + sgn * dx - xc)}, 'series': {}}
            if spring_elements and sgn in sides and min(dx % h, h - dx % h) < 0.2 * h:
                pr['element'] = {'instance': 'BEAM-1', 'label': 5000 + int(round(dx / h)),      # a one-node spring
                                 'centroid': [20.0 + sgn * round(dx / h) * h, 0.0, 0.0], 'distance': 0.0}
            elif with_sm:
                pr['series']['SM'] = {'columns': ['time', 'SM1'], 'rows': [[0.0, 0.0], [1.0, -moment((k + 0.5) * h)]]}
            probes.append(pr)
    w0 = w_scale * 500.0 * lam / (2.0 * k_line)
    m_ip = moment((0.5 if linear else 0.1127) * h)
    # moment of the model at the load node: the foundation reaction of the first half element acts at the node
    m_node = moment(0.5 * h) if linear else moment(0.0) - k_line * w0 * h * h / 12.0
    fields = {'U': vec('U', {'U1': (0.0, 0.0), 'U2': (0.0002, -w0)}, mag=w0)}
    if with_sm:
        fields['SM'] = vec('SM', {'SM1': (80.0, -m_ip)})
        if nodal:
            fields['SM']['elementNodal'] = {'components': {
                'SM11': {'max': 80.0, 'min': -m_node, 'maxAt': {'coords': [15.5, 0.0, 0.0], 'node': 65, 'element': 62},
                         'minAt': {'coords': [nodal_at, 0.0, 0.0], 'node': 2, 'element': 80}},
                'SM22': {'max': 0.0, 'min': 0.0, 'maxAt': {'coords': [0.0, 0.0, 0.0]}, 'minAt': {'coords': [0.0, 0.0, 0.0]}}}}
    return {'steps': [{'name': 'Load', 'procedure': '*STATIC', 'nFrames': 2, 'totalTime': 0.0, 'timePeriod': 1.0}],
            'frequencies': [], 'frame': {'step': 'Load', 'available': list(fields)}, 'fields': fields,
            'elementTypes': {'B21' if linear else 'B23': int(40 / h)}, 'probes': probes}


def _d07_offsets():
    path = os.path.join(EVAL, 'tasks', 'd07_beam_on_winkler_foundation', 'check.py')
    spec = importlib.util.spec_from_file_location('check_d07_offsets', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules['check_d07_offsets'] = mod


_d07_offsets()
D7 = 'd07_beam_on_winkler_foundation'
good_d7 = {'deflection_under_load_m': 3.70475e-3, 'moment_under_load_kNm': 351.46}
expect('d07 0.25 m cubic elements: moment at the load node', run(D7, winkler(), good_d7), True)
expect('d07 0.1 m elements', run(D7, winkler(h=0.1), good_d7), True)
expect('d07 0.5 m elements', run(D7, winkler(h=0.5), good_d7), True)
expect('d07 extractor without the element-nodal block', run(D7, winkler(nodal=False), good_d7), True)
expect('d07 nodal springs stored as elements of the same instance', run(D7, winkler(spring_elements=True), good_d7),
       True)
expect('d07 left half of the beam only (symmetry at the load)', run(D7, winkler(sides=(-1,)), good_d7), True)
expect('d07 right half of the beam only', run(D7, winkler(sides=(1,)), good_d7), True)
expect('d07 largest integration-point moment reported (2 % low)', run(D7, winkler(),
       dict(good_d7, moment_under_load_kNm=344.5)), True)
expect('d07 1 m cubic elements, moment at the load node reported', run(D7, winkler(h=1.0),
       dict(good_d7, moment_under_load_kNm=344.05)), True)
expect('d07 1 m cubic elements, integration-point moment reported', run(D7, winkler(h=1.0),
       dict(good_d7, moment_under_load_kNm=323.6)), False, ['report_moment_under_load_kNm'],
       ['deflection_under_load', 'moment_under_load'])
expect('d07 0.1 m linear elements, element moments extrapolated', run(D7, winkler(h=0.1, linear=True),
       dict(good_d7, moment_under_load_kNm=351.1)), True)
expect('d07 0.25 m linear elements, raw element moment reported', run(D7, winkler(linear=True),
       dict(good_d7, moment_under_load_kNm=320.9)), False, ['report_moment_under_load_kNm'],
       ['deflection_under_load', 'moment_under_load'])
expect('d07 1 m linear elements (peak of the moment lost)', run(D7, winkler(h=1.0, linear=True),
       dict(good_d7, moment_under_load_kNm=320.0)), False, ['moment_under_load'], ['deflection_under_load'])
expect('d07 element-nodal extreme away from the load is not used', run(D7, winkler(h=2.0, nodal_at=26.0),
       dict(good_d7, moment_under_load_kNm=250.0)), False, ['moment_under_load'], ['deflection_under_load'])
expect('d07 2 m elements (peak of the moment lost)', run(D7, winkler(h=2.0), dict(good_d7, moment_under_load_kNm=250.0)),
       False, ['moment_under_load'], ['deflection_under_load'])
expect('d07 subgrade modulus not multiplied by the width', run(D7, winkler(k_line=20000.0),
       {'deflection_under_load_m': 4.2476e-3, 'moment_under_load_kNm': 367.85}), False,
       ['deflection_under_load', 'moment_under_load'])
expect('d07 springs 4 times too stiff', run(D7, winkler(k_line=96000.0),
       {'deflection_under_load_m': 1.3098e-3, 'moment_under_load_kNm': 248.5}), False,
       ['deflection_under_load', 'moment_under_load'])
expect('d07 no section moment output', run(D7, winkler(with_sm=False), good_d7), False,
       ['moment_under_load', 'report_moment_under_load_kNm'], ['deflection_under_load'])
expect('d07 deflection reported in mm', run(D7, winkler(), dict(good_d7, deflection_under_load_m=3.70475)), False,
       ['report_deflection_under_load_m'], ['deflection_under_load', 'moment_under_load'])


# ------------------------------------------------------------------ d08: response spectrum of a cantilever
def _d08_offsets():
    path = os.path.join(EVAL, 'tasks', 'd08_response_spectrum_cantilever', 'check.py')
    spec = importlib.util.spec_from_file_location('check_d08_offsets', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OFFSETS


OFF8 = _d08_offsets()


def tower(freqs=(0.9715, 6.0884, 17.048), shear=464.11, moment=7727.65, top=0.084063, spectrum_step=True,
          source=None, h=1.0, with_sm=True):
    """source: where a base shear sits in the spectrum step - None (Euler-Bernoulli elements in Abaqus 2021: nowhere),
    'SF' (Timoshenko elements: transverse section force) or 'RF'.  Section moments fall linearly from the base."""
    steps = [{'name': 'Modes', 'procedure': '*FREQUENCY', 'nFrames': len(freqs) + 1, 'totalTime': 0.0}]
    if spectrum_step:
        steps.append({'name': 'Spectrum', 'procedure': '*RESPONSE SPECTRUM', 'nFrames': 2, 'totalTime': 0.0})
    slope = 452.4 * moment / 7727.65                 # d(SRSS moment)/dy at the base
    fields = {'U': vec('U', {'U1': (top, 0.0), 'U2': (0.0, 0.0)}, mag=top),
              'SF': vec('SF', {'SF11': (0.0, 0.0), 'SF12': (0.0, 0.0), 'SF22': (shear if source == 'SF' else 0.0, 0.0)})}
    if with_sm:
        fields['SM'] = vec('SM', {'SM1': (moment - slope * 0.1127 * h, 0.0), 'SM2': (0.0, 0.0), 'SM3': (0.0, 0.0)})
    if source == 'RF':
        fields['RF'] = vec('RF', {'RF1': (shear, 0.0), 'RF2': (0.0, 0.0)},
                           sums={'RF1': (shear, shear, 0.0), 'RF2': (0.0, 0.0, 0.0)})
    probes = []
    for dy in OFF8:
        k = int(dy / h)
        yc = (k + 0.5) * h
        pr = {'node': {'label': 1, 'distance': 0.0},
              'element': {'instance': 'STICK-1', 'label': k + 1, 'centroid': [0.0, yc, 0.0], 'distance': abs(dy - yc)},
              'series': {}}
        if with_sm and spectrum_step:
            pr['series']['SM'] = {'columns': ['time', 'SM1', 'SM2', 'SM3'],
                                  'rows': [[0.0, 0.0, 0.0, 0.0], [1.0, moment - slope * yc, 0.0, 0.0]]}
        probes.append(pr)
    return {'steps': steps, 'eigenvalues': [], 'elementTypes': {'B23': 24},
            'frequencies': [{'step': 'Modes', 'mode': i + 1, 'frequency': f} for i, f in enumerate(freqs)],
            'frame': {'step': 'Spectrum' if spectrum_step else 'Modes', 'available': list(fields)},
            'fields': fields, 'probes': probes}


D8 = 'd08_response_spectrum_cantilever'
good_d8 = {'frequencies_Hz': [0.9715, 6.0884, 17.048], 'base_shear_kN': 464.11, 'base_moment_kNm': 7727.6,
           'top_displacement_m': 0.084063}
expect('d08 Euler-Bernoulli model: no base shear in the ODB, report against the closed form', run(D8, tower(),
       good_d8), True)
expect('d08 Timoshenko model: transverse section force in the ODB', run(D8, tower(freqs=(0.9696, 6.022, 16.648),
       source='SF', shear=463.96, top=0.084247, h=0.5), dict(good_d8, frequencies_Hz=[0.9696, 6.022, 16.648],
                                                            base_shear_kN=463.96)), True)
expect('d08 base shear available as a reaction force', run(D8, tower(source='RF'), good_d8), True)
expect('d08 coarse stick (4 m elements): base moment still recovered', run(D8, tower(h=4.0), good_d8), True)
expect('d08 absolute sum (base moment +10 %, base shear +34 %)', run(D8, tower(moment=8519.4, top=0.08533),
       dict(good_d8, base_shear_kN=623.6, base_moment_kNm=8519.4, top_displacement_m=0.08533)), False,
       ['base_moment', 'report_base_shear_kN'], ['top_displacement', 'frequency_3'])
expect('d08 absolute sum, base shear nevertheless reported from the SRSS formula', run(D8, tower(moment=8519.4,
       top=0.08533), dict(good_d8, base_moment_kNm=8519.4, top_displacement_m=0.08533)), False, ['base_moment'],
       ['report_base_shear_kN'])
expect('d08 one mode only', run(D8, tower(freqs=(0.9715,), moment=7696.3, top=0.084055),
       {'frequencies_Hz': [0.9715, 0.0, 0.0], 'base_shear_kN': 441.41, 'base_moment_kNm': 7696.3,
        'top_displacement_m': 0.084055}), False, ['frequency_2', 'frequency_3', 'report_base_shear_kN'],
       ['frequency_1', 'top_displacement', 'base_moment'])
expect('d08 spectrum in g', run(D8, tower(moment=75731.0, top=0.82382),
       dict(good_d8, base_shear_kN=4548.3, base_moment_kNm=75731.0, top_displacement_m=0.82382)), False,
       ['top_displacement', 'base_moment', 'report_base_shear_kN'], ['frequency_1'])
expect('d08 Timoshenko model with the absolute sum (ODB shear 623 kN)', run(D8, tower(source='SF', shear=623.4,
       moment=8519.4, top=0.08533), dict(good_d8, base_shear_kN=623.4, base_moment_kNm=8519.4,
                                         top_displacement_m=0.08533)), False, ['base_shear', 'base_moment'],
       ['report_base_shear_kN'])
expect('d08 no response-spectrum step (mode shapes would be read as response)', run(D8, tower(
    spectrum_step=False, top=1.0), good_d8), False, ['spectrum_step', 'base_shear', 'top_displacement', 'base_moment'],
    ['frequency_1', 'frequency_2', 'frequency_3'])
expect('d08 no section moments in the spectrum step', run(D8, tower(with_sm=False), good_d8), False,
       ['base_moment', 'report_base_moment_kNm'], ['top_displacement'])
expect('d08 base shear reported in N', run(D8, tower(), dict(good_d8, base_shear_kN=464110.0)), False,
       ['report_base_shear_kN'], ['top_displacement', 'base_moment'])
expect('d08 Timoshenko model, closed-form shear reported while the ODB has 15 % less', run(D8, tower(source='SF',
       shear=394.0), good_d8), False, ['base_shear', 'report_base_shear_kN'])
expect('d08 frequencies reported in rad/s', run(D8, tower(), dict(good_d8, frequencies_Hz=[6.104, 38.25, 107.1])),
       False, ['report_frequencies_Hz'], ['frequency_1'])

print()
print('%d offline logic test(s) failed%s' % (len(FAILS), ': ' + '; '.join(FAILS) if FAILS else ''))
sys.exit(1 if FAILS else 0)
