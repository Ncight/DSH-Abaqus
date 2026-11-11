# -*- coding: utf-8 -*-
# Reads the four jobs written by gen_geotech_inp.py and compares them with the closed-form values quoted in the skill
# abaqus-geotech (abaqus python, Python 2.7).
from __future__ import print_function
import math
import sys
from odbAccess import openOdb

problems = []


def expect(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        problems.append(what)


def node_value(frame, name, label, comp=None):
    for v in frame.fieldOutputs[name].values:
        if v.nodeLabel == label:
            return float(v.data) if comp is None else float(v.data[comp])
    raise KeyError('node %d not in %s' % (label, name))


# ---------------------------------------------------------------- viscous base + incident wave
odb = openOdb('geo_wave.odb', readOnly=True)
st = odb.steps['Wave']
peak, late = 0.0, 0.0
for i in range(len(st.frames)):
    fr = st.frames[i]
    v = abs(node_value(fr, 'V', 101, 0))
    peak = max(peak, v)
    if fr.frameValue >= 1.0:
        late = max(late, v)
odb.close()
expect(abs(peak / 0.2 - 1.0) < 0.01, 'free surface: peak velocity %.5f = 2 x incident amplitude 0.1 (%+.2f %%)' % (peak, 100 * (peak / 0.2 - 1)))
expect(late / peak < 0.02, 'viscous base absorbs the reflected wave: residual after 1.0 s is %.2f %% of the peak' % (100 * late / peak))

# ---------------------------------------------------------------- consolidation
E_S, NU_S, Q, H = 20000.0, 0.25, 100.0, 10.0
M_C = E_S * (1.0 - NU_S) / ((1.0 + NU_S) * (1.0 - 2.0 * NU_S))
u_ratio = sum(4.0 / math.pi * (-1) ** m / (2 * m + 1) * math.exp(-((2 * m + 1) * math.pi / 2.0) ** 2 * 0.2) for m in range(50))
odb = openOdb('geo_consol.odb', readOnly=True)
p_load = node_value(odb.steps['Load'].frames[-1], 'POR', 1)
p_tv = node_value(odb.steps['Consol1'].frames[-1], 'POR', 1)
s_fin = -node_value(odb.steps['Consol2'].frames[-1], 'U', 41, 1)
p_fin = node_value(odb.steps['Consol2'].frames[-1], 'POR', 1)
odb.close()
expect(abs(p_load / Q - 1.0) < 0.03, 'pore pressure at the base right after loading %.2f = surcharge 100' % p_load)
expect(abs(p_tv / (u_ratio * Q) - 1.0) < 0.02, 'base pore pressure at Tv = 0.2: %.2f, Terzaghi %.2f' % (p_tv, u_ratio * Q))
expect(abs(s_fin / (Q * H / M_C) - 1.0) < 0.01, 'final settlement %.5f = q H / M = %.5f' % (s_fin, Q * H / M_C))
expect(abs(p_fin) < 0.01 * Q, 'pore pressure dissipated at Tv = 3: %.3f' % p_fin)

# ---------------------------------------------------------------- undrained strength in plane strain
odb = openOdb('geo_mc.odb', readOnly=True)
fr = odb.steps['Press'].frames[-1]
s22 = {}
for v in fr.fieldOutputs['S'].values:
    s22.setdefault(v.elementLabel, []).append(float(v.data[1]))
odb.close()
for label, name in ((1, 'Mohr-Coulomb phi = 0, c = cu'), (2, 'Mises with yield stress sqrt(3) cu')):
    val = sum(s22[label]) / len(s22[label])
    expect(abs(val / -100.0 - 1.0) < 0.01, '%s: plane-strain compressive strength %.2f = 2 cu = 100' % (name, -val))

# ---------------------------------------------------------------- concrete damaged plasticity
E0 = 32000000.0
ck = [0.0, 0.00015, 0.0006, 0.0015]
sig = [2500.0, 1500.0, 500.0, 100.0]
dam = [0.0, 0.35, 0.7, 0.9]
pl = [ck[i] - dam[i] / (1.0 - dam[i]) * sig[i] / E0 for i in range(4)]         # cracking strain -> plastic strain


def interp(x, xs, ys):
    for i in range(len(xs) - 1):
        if x <= xs[i + 1]:
            return ys[i] + (ys[i + 1] - ys[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])
    return ys[-1]


def state(eps, xs):
    """stress and damage at total strain eps; tables interpolated linearly over xs (plastic or cracking strain)"""
    lo, hi = 0.0, eps
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if xs is pl:
            trial = (1.0 - interp(mid, xs, dam)) * E0 * (eps - mid)            # sigma = (1 - d) E0 (eps - eps_pl)
        else:
            trial = E0 * (eps - mid)                                           # naive: eps = sigma / E0 + eps_ck
        if trial > interp(mid, xs, sig):
            lo = mid
        else:
            hi = mid
    return interp(lo, xs, sig), interp(lo, xs, dam), lo


EPS = 0.0005
s_ref, d_ref, pl_ref = state(EPS, pl)
s_naive, d_naive, _ = state(EPS, ck)
odb = openOdb('geo_cdp.odb', readOnly=True)
fr = odb.steps['Pull'].frames[-1]
s33 = sum(float(v.data[2]) for v in fr.fieldOutputs['S'].values) / len(fr.fieldOutputs['S'].values)
dt = sum(float(v.data) for v in fr.fieldOutputs['DAMAGET'].values) / len(fr.fieldOutputs['DAMAGET'].values)
pe = sum(float(v.data) for v in fr.fieldOutputs['PEEQT'].values) / len(fr.fieldOutputs['PEEQT'].values)
pe33 = sum(float(v.data[2]) for v in fr.fieldOutputs['PE'].values) / len(fr.fieldOutputs['PE'].values)
peak = max(sum(float(v.data[2]) for v in f.fieldOutputs['S'].values) / 8.0
           for f in [odb.steps['Pull'].frames[i] for i in range(len(odb.steps['Pull'].frames))])
odb.close()
# 1. stress and damage are the tables (converted to plastic strain) looked up at PEEQT
expect(abs(s33 / interp(pe, pl, sig) - 1.0) < 0.001, 'stress %.2f = stress table at PEEQT %.2f' % (s33, interp(pe, pl, sig)))
expect(abs(dt - interp(pe, pl, dam)) < 0.001, 'DAMAGET %.4f = damage table at PEEQT %.4f' % (dt, interp(pe, pl, dam)))
# 2. the damaged elastic law holds with the plastic strain component PE33
expect(abs(s33 / ((1.0 - dt) * E0 * (EPS - pe33)) - 1.0) < 0.001, 'stress = (1 - d) E0 (eps - PE33) = %.2f' % ((1.0 - dt) * E0 * (EPS - pe33)))
# 3. a hand calculation that assumes PEEQT = PE33 is close, not exact (PE33 is slightly larger than PEEQT)
expect(abs(s33 / s_ref - 1.0) < 0.015, 'hand calculation over plastic strain %.2f (%+.2f %%); over cracking strain %.2f (%+.2f %%)'
       % (s_ref, 100 * (s_ref / s33 - 1), s_naive, 100 * (s_naive / s33 - 1)))
expect(0.0 < pe33 / pe - 1.0 < 0.01, 'PE33 %.7f is %.2f %% larger than PEEQT %.7f' % (pe33, 100 * (pe33 / pe - 1), pe))
expect(abs(peak / 2500.0 - 1.0) < 0.01, 'peak stress %.1f = tensile strength 2500' % peak)

print('GEOTECH %s' % ('OK' if not problems else 'FAILED: ' + '; '.join(problems)))
sys.exit(1 if problems else 0)
