# -*- coding: utf-8 -*-
"""Generates the task asset `assets/ground_motion.at2` and prints the exact SDOF reference response.

    py make_record.py            (pure Python 3, no third-party packages)

The record is SYNTHETIC: a sum of 191 cosines between 0.5 and 10 Hz with random phases (fixed seed), shaped by a
Kanai-Tajimi-like amplitude spectrum and a build-up / strong-motion / decay envelope, 15 s at dt = 0.01 s, scaled to a
peak of 0.2 g.  It is stored in the layout of a PEER NGA `.AT2` file: four header lines, accelerations in units of g,
five values per line.

The reference response is the exact solution of  u'' + 2 zeta w u' + w^2 u = -a_g(t)  for a piecewise-linear a_g
(Nigam & Jennings 1969) with the analytic stiffness of the shear frame, k = 2 x 12 E I / h^3.
"""
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, '..', 'assets', 'ground_motion.at2')
G = 9.8
DT, NPTS = 0.01, 1500
PEAK_G = 0.2
SEED = 20261110

# shear frame of the task
E_MOD, B_COL, H_COL, MASS, ZETA = 3.0e7, 0.2, 4.0, 12.5, 0.05
K_FRAME = 2.0 * 12.0 * E_MOD * (B_COL ** 4 / 12.0) / H_COL ** 3            # 1500 kN/m
OMEGA = math.sqrt(K_FRAME / MASS)                                          # 10.9545 rad/s, T = 0.5736 s
# (T was put on a flat part of this record's response spectrum: +-1 % in T changes both answers by about 1 %;
#  at T = 0.50 s the same change would move the acceleration by 4.5 %.)


def envelope(t):
    if t < 2.0:
        e = (t / 2.0) ** 2
    elif t < 8.0:
        e = 1.0
    else:
        e = math.exp(-0.55 * (t - 8.0))
    if t > 13.5:                                   # cosine taper to exactly zero at the end
        e *= 0.5 * (1.0 + math.cos(math.pi * (t - 13.5) / 1.49))
    return e


def synthesize():
    rng = random.Random(SEED)
    comps = []
    f = 0.5
    while f <= 10.0 + 1e-9:
        r = f / 2.5                                # Kanai-Tajimi shape, fg = 2.5 Hz, zeta_g = 0.6
        amp = math.sqrt((1.0 + (2 * 0.6 * r) ** 2) / ((1.0 - r * r) ** 2 + (2 * 0.6 * r) ** 2))
        comps.append((2.0 * math.pi * f, amp, rng.uniform(0.0, 2.0 * math.pi)))
        f += 0.05
    acc = []
    for i in range(NPTS):
        t = i * DT
        acc.append(envelope(t) * math.fsum(a * math.cos(w * t + ph) for w, a, ph in comps))
    peak = max(abs(v) for v in acc)
    return [v * PEAK_G / peak for v in acc]


def write_at2(acc, path):
    lines = ['SYNTHETIC GROUND MOTION FOR BENCHMARK TASK D05 (NOT A RECORDED EARTHQUAKE)',
             'BAND-LIMITED 0.5-10 HZ, ENVELOPED, HORIZONTAL COMPONENT, FIRST VALUE AT T = 0',
             'ACCELERATION TIME SERIES IN UNITS OF G',
             'NPTS=  %5d, DT=   %.4f SEC' % (len(acc), DT)]
    for i in range(0, len(acc), 5):
        lines.append(''.join('%15.7E' % v for v in acc[i:i + 5]))
    with open(path, 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def read_at2(path):
    """-> (dt, [acceleration in g]) of a PEER-like file: header lines, then free-format numbers"""
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


def sdof_exact(acc, dt, omega, zeta, t_end=None):
    """exact response to a piecewise-linear base acceleration acc (m/s2), at rest at t = 0.
    -> (peak |u_rel|, peak |a_abs|, time history [(t, u, v, a_abs)]); zero input after the record ends"""
    wd = omega * math.sqrt(1.0 - zeta * zeta)
    e = math.exp(-zeta * omega * dt)
    s, c = math.sin(wd * dt), math.cos(wd * dt)
    z = zeta / math.sqrt(1.0 - zeta * zeta)
    a11 = e * (z * s + c)
    a12 = e * s / wd
    a21 = -omega / math.sqrt(1.0 - zeta * zeta) * e * s
    a22 = e * (c - z * s)
    w2, w3 = omega ** 2, omega ** 3
    t1 = (2.0 * zeta * zeta - 1.0) / (w2 * dt)
    t2 = 2.0 * zeta / (w3 * dt)
    b11 = e * ((t1 + zeta / omega) * s / wd + (t2 + 1.0 / w2) * c) - t2
    b12 = -e * (t1 * s / wd + t2 * c) - 1.0 / w2 + t2
    b21 = (e * ((t1 + zeta / omega) * (c - z * s) - (t2 + 1.0 / w2) * (wd * s + zeta * omega * c)) + 1.0 / (w2 * dt))
    b22 = -e * (t1 * (c - z * s) - t2 * (wd * s + zeta * omega * c)) - 1.0 / (w2 * dt)
    n = len(acc) if t_end is None else int(round(t_end / dt)) + 1
    u = v = 0.0
    hist = [(0.0, 0.0, 0.0, 0.0)]
    pu = pa = 0.0
    for i in range(n - 1):
        g0 = acc[i] if i < len(acc) else 0.0
        g1 = acc[i + 1] if i + 1 < len(acc) else 0.0
        u, v = (a11 * u + a12 * v + b11 * g0 + b12 * g1, a21 * u + a22 * v + b21 * g0 + b22 * g1)
        a_abs = -2.0 * zeta * omega * v - w2 * u
        hist.append(((i + 1) * dt, u, v, a_abs))
        pu, pa = max(pu, abs(u)), max(pa, abs(a_abs))
    return pu, pa, hist


if __name__ == '__main__':
    acc_g = synthesize()
    os.makedirs(os.path.dirname(ASSET), exist_ok=True)
    write_at2(acc_g, ASSET)
    dt, back = read_at2(ASSET)
    assert dt == DT and len(back) == NPTS
    acc = [G * v for v in back]
    vel = dis = 0.0
    pv = pd = 0.0
    for i in range(len(acc) - 1):                   # ground velocity / displacement by the trapezoidal rule
        v_new = vel + 0.5 * DT * (acc[i] + acc[i + 1])
        dis += 0.5 * DT * (vel + v_new)
        vel = v_new
        pv, pd = max(pv, abs(vel)), max(pd, abs(dis))
    print('record: %d points, dt %.3f s, peak %.5f g at t = %.2f s; peak ground velocity %.4f m/s, displacement '
          '%.4f m; end velocity %.4f m/s, end displacement %.4f m' % (
              len(back), dt, max(abs(v) for v in back), DT * max(range(len(back)), key=lambda i: abs(back[i])),
              pv, pd, vel, dis))
    print('frame: k = %.3f kN/m, m = %.2f t, omega = %.5f rad/s, T = %.5f s' % (
        K_FRAME, MASS, OMEGA, 2.0 * math.pi / OMEGA))
    for label, w, zt, t_end in (('reference (zeta 5 %)', OMEGA, ZETA, 15.0),
                                ('undamped', OMEGA, 1.0e-9, 15.0),
                                ('record taken as m/s2', None, ZETA, 15.0),
                                ('Timoshenko columns (k -0.72 %)', OMEGA * math.sqrt(1.0 / 1.0072), ZETA, 15.0),
                                ('k +3 %', OMEGA * math.sqrt(1.03), ZETA, 15.0),
                                ('k -3 %', OMEGA * math.sqrt(0.97), ZETA, 15.0)):
        if w is None:
            pu, pa, _h = sdof_exact(back, DT, OMEGA, zt, t_end)
        else:
            pu, pa, _h = sdof_exact(acc, DT, w, zt, t_end)
        print('%-32s peak relative displacement %.6f m, peak absolute acceleration %.5f m/s2' % (label, pu, pa))
