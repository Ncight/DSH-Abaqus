# -*- coding: utf-8 -*-
"""Reference solution for t01_cantilever_static  (run: abaqus cae noGUI=solve.py, cwd = work dir).

3-D solid cantilever, L=1.0 m (along Z), b=0.05 m (X), h=0.10 m (Y), E=2.1e11 Pa, nu=0.3, fully fixed at z=0,
total transverse force P=1000 N (-Y) applied as a uniform shear traction on the free end face.

REF_VARIANT (environment variable):
  default        C3D20R, 4 x 8 x 80 elements                               -> must PASS
  c3d8i          C3D8I,  4 x 8 x 80 (alternative correct model)             -> must PASS
  c3d10          C3D10 free tet mesh, size 0.025 (alternative correct)      -> must PASS
  coarse_c3d8r   C3D8R (CAE default element), 2 x 4 x 40 (seed 0.025)       -> must FAIL (too soft, +6.6 %)
  locked_c3d8    C3D8 fully integrated, 1 x 1 x 4 elongated elements        -> must FAIL (shear locking, too stiff)
  linear_tet     C3D4 free tet mesh, size 0.025                             -> must FAIL (too stiff)
  wrong_load     "1000" entered as a traction of 1000 Pa (total 5 N)        -> must FAIL
  fake_report    correct model, deflection written to result.json in mm     -> must FAIL (report does not match ODB)
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'cantilever'
L, B, H = 1.0, 0.05, 0.10
E, NU = 2.1e11, 0.3
P = 1000.0

# element code, (elements across b, through h, along L) for hexes or a global size for tets
CFG = {
    'default': (C3D20R, (4, 8, 80)),
    'c3d8i': (C3D8I, (4, 8, 80)),
    'c3d10': (C3D10, 0.025),
    'coarse_c3d8r': (C3D8R, (2, 4, 40)),
    'locked_c3d8': (C3D8, (1, 1, 4)),
    'linear_tet': (C3D4, 0.025),
    'wrong_load': (C3D20R, (4, 8, 80)),
    'fake_report': (C3D20R, (4, 8, 80)),
}
if VARIANT not in CFG:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
elem_code, density = CFG[VARIANT]

m = mdb.Model(name='Cantilever')
s = m.ConstrainedSketch(name='section', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(B, H))
p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=L)

mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
m.HomogeneousSolidSection(name='Sec', material='Steel')
p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')

if isinstance(density, tuple):
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        axis = max(range(3), key=lambda k: abs(v1[k] - v0[k]))
        p.seedEdgeByNumber(edges=(e,), number=density[axis], constraint=FIXED)
else:
    p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
    p.seedPart(size=density, deviationFactor=0.1, minSizeFactor=0.1)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Beam-1', part=p, dependent=ON)
tol = 1.0e-6
fix_faces = inst.faces.getByBoundingBox(zMax=tol)
tip_faces = inst.faces.getByBoundingBox(zMin=L - tol)
a.Set(name='FIXED', faces=fix_faces)
a.Set(name='TIP', faces=tip_faces)
a.Surface(name='TIPSURF', side1Faces=tip_faces)

m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)
m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FIXED'])
traction = P / (B * H)
if VARIANT == 'wrong_load':
    traction = 1000.0
m.SurfaceTraction(name='TipShear', createStepName='Load', region=a.surfaces['TIPSURF'], magnitude=traction,
                  directionVector=((0.0, 0.0, 0.0), (0.0, -1.0, 0.0)), distributionType=UNIFORM, traction=GENERAL,
                  follower=OFF)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))

job = mdb.Job(name=JOB, model='Cantilever', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: the numbers for result.json come from the ODB ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Load'].frames[-1]
tip = odb.rootAssembly.nodeSets['TIP']
fixed = odb.rootAssembly.nodeSets['FIXED']
u_tip = [v.data[1] for v in frame.fieldOutputs['U'].getSubset(region=tip).values]
rf = [v.data[1] for v in frame.fieldOutputs['RF'].getSubset(region=fixed).values]
tip_deflection = abs(math.fsum(u_tip) / len(u_tip))   # mean transverse displacement of the free end face
reaction = abs(math.fsum(rf))
odb.close()

result = {'tip_deflection_m': tip_deflection, 'reaction_force_N': reaction}
if VARIANT == 'fake_report':
    result['tip_deflection_m'] = tip_deflection * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
