# -*- coding: utf-8 -*-
"""Input files behind the keyword blocks quoted in the skill abaqus-geotech (system Python, units m - t - s, kN, kPa).

    geo_wave.inp    1-D shear column with a viscous (dashpot) base and the incident wave applied as nodal forces
    geo_consol.inp  1-D consolidation: surcharge in a one-increment step, then automatic time stepping with UTOL
    geo_mc.inp      undrained strength in plane strain: Mohr-Coulomb (phi = 0) and Mises (sqrt(3) cu), one element each
    geo_cdp.inp     concrete damaged plasticity, one element in uniaxial tension with tension damage

The runner solves the four jobs; chk_geotech_read.py compares the results with the closed-form values.
"""
import math


def write(name, lines):
    with open(name, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('%s written (%d lines)' % (name, len(lines)))


def column(n_el, h, etype):
    """two nodes per level (x = 0 and x = 1), n_el elements stacked along y"""
    out = ['*Node']
    for j in range(n_el + 1):
        out.append('%d, 0.0, %.6f' % (2 * j + 1, j * h))
        out.append('%d, 1.0, %.6f' % (2 * j + 2, j * h))
    out.append('*Element, type=%s, elset=SOIL' % etype)
    for j in range(n_el):
        out.append('%d, %d, %d, %d, %d' % (j + 1, 2 * j + 1, 2 * j + 2, 2 * j + 4, 2 * j + 3))
    top = 2 * n_el + 1
    out += ['*Nset, nset=ALLN, generate', '1, %d, 1' % (top + 1), '*Nset, nset=BASE', '1, 2', '*Nset, nset=TOP',
            '%d, %d' % (top, top + 1), '*Elset, elset=ETOP', '%d,' % n_el]
    return out


# ------------------------------------------------------------------ viscous base + incident wave
RHO, VS, NU = 2.0, 200.0, 0.3
G = RHO * VS * VS
E = 2.0 * G * (1.0 + NU)
A_INC, FC, T0, DT, T_END = 0.1, 4.0, 0.3, 0.001, 1.5
AREA = 0.5                                              # tributary area of each of the two base nodes (width 1 m, thickness 1 m)
lines = ['*Heading', 'shear column, viscous base, incident Ricker wavelet'] + column(50, 1.0, 'CPE4')
lines += ['*Solid Section, elset=SOIL, material=SOIL', '1.0,',
          '*Material, name=SOIL', '*Elastic', '%.1f, %.2f' % (E, NU), '*Density', '%.2f,' % RHO,
          '*Element, type=DASHPOT1, elset=DASH', '1001, 1', '1002, 2',
          '*Dashpot, elset=DASH', '1', '%.3f,' % (RHO * VS * AREA),
          '*Amplitude, name=VINC']
pairs = []
for i in range(int(round(T_END / DT)) + 1):
    t = i * DT
    a = (math.pi * FC * (t - T0)) ** 2
    pairs.append('%.4f, %.8f' % (t, A_INC * (1.0 - 2.0 * a) * math.exp(-a)))
for i in range(0, len(pairs), 4):
    lines.append(', '.join(pairs[i:i + 4]))
lines += ['*Boundary', 'ALLN, 2, 2',
          '*Step, name=Wave, inc=5000', '*Dynamic, direct', '%.4f, %.2f' % (DT, T_END),
          '*Cload, amplitude=VINC', 'BASE, 1, %.3f' % (2.0 * RHO * VS * AREA),
          '*Output, field, frequency=1', '*Node Output', 'U, V', '*End Step']
write('geo_wave.inp', lines)

# ------------------------------------------------------------------ consolidation
E_S, NU_S, K_PERM, GAMMA_W, Q, H, N_EL = 20000.0, 0.25, 2.0e-8, 9.8, 100.0, 10.0, 20
M_C = E_S * (1.0 - NU_S) / ((1.0 + NU_S) * (1.0 - 2.0 * NU_S))
CV = K_PERM * M_C / GAMMA_W
T_TV = 0.2 * H * H / CV
T_FIN = 3.0 * H * H / CV
h = H / N_EL
T_LOAD = round(GAMMA_W * h * h / (6.0 * M_C * K_PERM), -1)
lines = ['*Heading', 'one-dimensional consolidation'] + column(N_EL, h, 'CPE4P')
lines += ['*Surface, type=ELEMENT, name=TOPS', 'ETOP, S3',
          '*Solid Section, elset=SOIL, material=CLAY', '1.0,',
          '*Material, name=CLAY', '*Elastic', '%.1f, %.2f' % (E_S, NU_S),
          '*Permeability, specific=%.2f' % GAMMA_W, '%.3e, 1.0' % K_PERM,
          '*Initial Conditions, type=RATIO', 'ALLN, 1.0',
          '*Boundary', 'ALLN, 1, 1', 'BASE, 2, 2',
          '*Step, name=Load, inc=10', '*Soils, consolidation', '%.1f, %.1f' % (T_LOAD, T_LOAD),
          '*Boundary', 'TOP, 8, 8, 0.0', '*Dsload', 'TOPS, P, %.1f' % Q,
          '*Output, field, frequency=1', '*Node Output', 'U, POR, RF', '*Element Output', 'S, VOIDR', '*End Step',
          '*Step, name=Consol1, inc=5000', '*Soils, consolidation, utol=10.0',
          '%.1f, %.1f, 1.0, %.1f' % (T_LOAD, T_TV - T_LOAD, (T_TV - T_LOAD) / 50.0), '*End Step',
          '*Step, name=Consol2, inc=5000', '*Soils, consolidation, utol=10.0',
          '%.1f, %.1f, 1.0, %.1f' % (T_TV / 50.0, T_FIN - T_TV, (T_FIN - T_TV) / 40.0), '*End Step']
write('geo_consol.inp', lines)

# ------------------------------------------------------------------ undrained strength, plane strain
CU = 50.0
lines = ['*Heading', 'undrained strength: Mohr-Coulomb phi = 0 and Mises sqrt(3) cu', '*Node',
         '1, 0., 0.', '2, 1., 0.', '3, 1., 1.', '4, 0., 1.', '11, 3., 0.', '12, 4., 0.', '13, 4., 1.', '14, 3., 1.',
         '*Element, type=CPE4, elset=E_MC', '1, 1, 2, 3, 4', '*Element, type=CPE4, elset=E_MISES', '2, 11, 12, 13, 14',
         '*Nset, nset=BOT', '1, 2, 11, 12', '*Nset, nset=LEFT', '1, 4, 11, 14', '*Nset, nset=TOP', '3, 4, 13, 14',
         '*Solid Section, elset=E_MC, material=CLAY_MC', '1.0,', '*Solid Section, elset=E_MISES, material=CLAY_MISES', '1.0,',
         '*Material, name=CLAY_MC', '*Elastic', '15000.0, 0.49', '*Mohr Coulomb', '0.0, 0.0',
         '*Mohr Coulomb Hardening', '%.1f, 0.0' % CU,
         '*Material, name=CLAY_MISES', '*Elastic', '15000.0, 0.49', '*Plastic', '%.4f, 0.0' % (math.sqrt(3.0) * CU),
         '*Boundary', 'BOT, 2, 2', 'LEFT, 1, 1',
         '*Step, name=Press, inc=1000', '*Static', '0.02, 1.0, 1e-06, 0.02', '*Boundary', 'TOP, 2, 2, -0.05',
         '*Output, field, frequency=1', '*Node Output', 'U, RF', '*Element Output', 'S, PEEQ', '*End Step']
write('geo_mc.inp', lines)

# ------------------------------------------------------------------ concrete damaged plasticity, uniaxial tension
lines = ['*Heading', 'concrete damaged plasticity: one element in uniaxial tension', '*Node',
         '1, 0., 0., 0.', '2, 1., 0., 0.', '3, 1., 1., 0.', '4, 0., 1., 0.',
         '5, 0., 0., 1.', '6, 1., 0., 1.', '7, 1., 1., 1.', '8, 0., 1., 1.',
         '*Element, type=C3D8, elset=CUBE', '1, 1, 2, 3, 4, 5, 6, 7, 8',
         '*Nset, nset=X0', '1, 4, 5, 8', '*Nset, nset=Y0', '1, 2, 5, 6', '*Nset, nset=Z0', '1, 2, 3, 4',
         '*Nset, nset=ZTOP', '5, 6, 7, 8',
         '*Solid Section, elset=CUBE, material=CONC', ',',
         '*Material, name=CONC', '*Elastic', '32000000.0, 0.2',
         '*Concrete Damaged Plasticity', '35.0, 0.1, 1.16, 0.6667, 0.0',
         '*Concrete Compression Hardening', '24000.0, 0.0', '32000.0, 0.0008', '20000.0, 0.004',
         '*Concrete Tension Stiffening, type=STRAIN', '2500.0, 0.0', '1500.0, 0.00015', '500.0, 0.0006', '100.0, 0.0015',
         '*Concrete Tension Damage, type=STRAIN', '0.0, 0.0', '0.35, 0.00015', '0.7, 0.0006', '0.9, 0.0015',
         '*Boundary', 'X0, 1, 1', 'Y0, 2, 2', 'Z0, 3, 3',
         '*Step, name=Pull, inc=1000', '*Static, direct', '0.01, 1.0', '*Boundary', 'ZTOP, 3, 3, 0.0005',
         '*Output, field, frequency=1', '*Node Output', 'U, RF', '*Element Output', 'S, E, PE, PEEQT, DAMAGET', '*End Step']
write('geo_cdp.inp', lines)
