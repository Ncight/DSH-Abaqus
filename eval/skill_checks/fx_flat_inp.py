# -*- coding: utf-8 -*-
"""Fixture written directly as an input file (no *Part / *Assembly): cantilever of 10 B21 elements with node sets TIP and
FIXEND, element set FIXEND, a static step "Load" and a frequency step "Modes".  Run with the system Python; the runner then
solves fx_flat.inp and chk_odb_recipes.py reads fx_flat.odb."""
L, N = 1.0, 10
lines = ['*Heading', 'flat fixture for the ODB recipes', '*Node']
for i in range(N + 1):
    lines.append('%d, %.6f, 0.0' % (i + 1, L * i / N))
lines.append('*Element, type=B21, elset=ALL')
for i in range(N):
    lines.append('%d, %d, %d' % (i + 1, i + 1, i + 2))
lines += ['*Nset, nset=FIXEND', '1,', '*Nset, nset=TIP', '%d,' % (N + 1), '*Elset, elset=FIXEND', '1,',
          '*Beam Section, elset=ALL, material=STEEL, section=RECT', '0.05, 0.1', '0., 0., -1.',
          '*Material, name=STEEL', '*Elastic', '2.06e11, 0.3', '*Density', '7850.,',
          '*Boundary', 'FIXEND, ENCASTRE',
          '*Step, name=Load', '*Static', '*Cload', 'TIP, 2, -1000.', '*Output, field', '*Node Output', 'U, RF',
          '*Element Output', 'S, SF', '*Output, history', '*Energy Output', 'ALLIE, ALLSE', '*End Step',
          '*Step, name=Modes', '*Frequency', '3,', '*End Step']
with open('fx_flat.inp', 'w') as f:
    f.write('\n'.join(lines) + '\n')
print('fx_flat.inp written')
