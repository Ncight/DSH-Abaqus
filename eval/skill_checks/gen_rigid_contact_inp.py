# -*- coding: utf-8 -*-
"""verification of the keyword snippets quoted in the direct-INP skill: rigid line elements + reference node + contact pair.
Plane strain elastic block 0.2 x 0.1 pressed by a rigid flat platen (R2D2 elements) moved down 1e-4 through its reference node.
Analytic: the block is laterally free only at the sides and bonded-free (frictionless) at the platen, fixed vertically at
the base with one node pinned -> uniaxial stress in plane strain: sigma_yy = -E/(1-nu^2) * (d/H); force = sigma * W."""
W, H, NX, NY = 0.2, 0.1, 20, 10
E, NU = 1.0e6, 0.3
D = 1.0e-4


def nid(i, j):
    return j * (NX + 1) + i + 1


def eid(i, j):
    return j * NX + i + 1


L = ['*Heading', 'rigid platen on elastic block, plane strain', '*Node']
for j in range(NY + 1):
    for i in range(NX + 1):
        L.append('%d, %.9g, %.9g' % (nid(i, j), W * i / NX, H * j / NY))
# rigid platen: two nodes above the block (a little wider than the block) and a reference node
L += ['9001, %.9g, %.9g' % (-0.02, H), '9002, %.9g, %.9g' % (W + 0.02, H), '9000, %.9g, %.9g' % (W / 2, H + 0.02)]
L.append('*Element, type=CPE4, elset=BLOCK')
for j in range(NY):
    for i in range(NX):
        L.append('%d, %d, %d, %d, %d' % (eid(i, j), nid(i, j), nid(i + 1, j), nid(i + 1, j + 1), nid(i, j + 1)))
L += ['*Element, type=R2D2, elset=PLATEN', '9001, 9002, 9001',     # node order sets which side is the contact side
      '*Nset, nset=REF', '9000',
      '*Nset, nset=BOTTOM, generate', '%d, %d, 1' % (nid(0, 0), nid(NX, 0)),
      '*Nset, nset=CORNER', '%d' % nid(0, 0),
      '*Elset, elset=TOPROW, generate', '%d, %d, 1' % (eid(0, NY - 1), eid(NX - 1, NY - 1)),
      '*Solid Section, elset=BLOCK, material=MAT', '1.0,',
      '*Material, name=MAT', '*Elastic', '%.9g, %.9g' % (E, NU),
      '*Rigid Body, ref node=REF, elset=PLATEN',
      '*Surface, type=ELEMENT, name=BLOCKTOP', 'TOPROW, S3',
      '*Surface, type=ELEMENT, name=PLATENSURF', 'PLATEN, SPOS',
      '*Surface Interaction, name=SMOOTH', '1.0,',
      '*Surface Behavior, pressure-overclosure=HARD',
      '*Contact Pair, interaction=SMOOTH, type=SURFACE TO SURFACE', 'BLOCKTOP, PLATENSURF',     # slave, master
      '*Boundary', 'BOTTOM, 2, 2', 'CORNER, 1, 1', 'REF, 1, 1', 'REF, 6, 6',
      '*Step, name=Press, nlgeom=NO, inc=100', '*Static', '0.25, 1., 1e-06, 0.25',
      '*Boundary', 'REF, 2, 2, %.9g' % (-D),
      '*Output, field', '*Node Output', 'U, RF', '*Element Output', 'S', '*Contact Output', 'CSTRESS, CDISP',
      '*End Step']
open('rigid_inp.inp', 'w').write('\n'.join(L) + '\n')
print('expected sigma_yy = %.6g, force = %.6g' % (-E / (1 - NU ** 2) * D / H, E / (1 - NU ** 2) * D / H * W))
