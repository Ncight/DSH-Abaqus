# -*- coding: utf-8 -*-
"""Reference solution for t06_selfweight_units  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Concrete column 1 m x 1 m x 10 m (axis = Z) under its own weight, m-t-s unit system (force kN, stress kPa,
density t/m3): E = 3.0e7 kPa, nu = 0.2, rho = 2.5 t/m3, g = 9.8 m/s2.  The base is held vertically; two point
restraints stop the rigid-body motions without hindering the lateral (Poisson) expansion.
Closed form: base reaction 245 kN, top settlement 4.0833e-5 m, base stress -245 kPa.

REF_VARIANT (environment variable):
  default       C3D20R, 2 x 2 x 20                                                         -> must PASS
  c3d8r         C3D8R (CAE default), 4 x 4 x 40                                            -> must PASS
  si_units      consistent N / Pa / kg input (E = 3.0e10, rho = 2500): reaction 245000     -> must FAIL
  mixed_units   E in kPa but density 2500 (kg/m3): 1000 times too heavy                    -> must FAIL
  fixed_base    correct units, but the whole base is fully fixed (ENCASTRE)                -> must FAIL
  fake_report   correct model, result.json written in N / Pa instead of kN / kPa           -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'column'
A, HGT = 1.0, 10.0
E, NU, RHO, G = 3.0e7, 0.2, 2.5, 9.8
KNOWN = ('default', 'c3d8r', 'si_units', 'mixed_units', 'fixed_base', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
if VARIANT == 'si_units':
    E, RHO = 3.0e10, 2500.0
elif VARIANT == 'mixed_units':
    RHO = 2500.0
elem_code, counts = (C3D8R, (4, 4, 40)) if VARIANT == 'c3d8r' else (C3D20R, (2, 2, 20))

m = mdb.Model(name='Column')
s = m.ConstrainedSketch(name='section', sheetSize=5.0)
s.rectangle(point1=(0.0, 0.0), point2=(A, A))
p = m.Part(name='Column', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=HGT)
mat = m.Material(name='Concrete')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
m.HomogeneousSolidSection(name='Sec', material='Concrete')
p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
for e in p.edges:
    v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    axis = max(range(3), key=lambda k: abs(v1[k] - v0[k]))
    p.seedEdgeByNumber(edges=(e,), number=counts[axis], constraint=FIXED)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Column-1', part=p, dependent=ON)
tol = 1.0e-6
a.Set(name='BASE', faces=inst.faces.getByBoundingBox(zMax=tol))
a.Set(name='TOP', faces=inst.faces.getByBoundingBox(zMin=HGT - tol))
a.Set(name='CORNER_A', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),)))
a.Set(name='CORNER_B', vertices=inst.vertices.findAt(((A, 0.0, 0.0),)))
a.Set(name='ALL', cells=inst.cells)

m.StaticStep(name='Gravity', previous='Initial', nlgeom=OFF)
if VARIANT == 'fixed_base':
    m.EncastreBC(name='Base', createStepName='Initial', region=a.sets['BASE'])
else:
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u3=0.0)
    m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['CORNER_A'], u1=0.0, u2=0.0)
    m.DisplacementBC(name='RollerB', createStepName='Initial', region=a.sets['CORNER_B'], u2=0.0)
m.Gravity(name='SelfWeight', createStepName='Gravity', comp3=-G)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))

job = mdb.Job(name=JOB, model='Column', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Gravity'].frames[-1]
ns = odb.rootAssembly.nodeSets
reaction = math.fsum(v.data[2] for v in frame.fieldOutputs['RF'].getSubset(region=ns['BASE']).values)
u_top = [v.data[2] for v in frame.fieldOutputs['U'].getSubset(region=ns['TOP']).values]
settlement = -math.fsum(u_top) / len(u_top)
s_base = [v.data[2] for v in frame.fieldOutputs['S'].getSubset(region=ns['BASE'], position=ELEMENT_NODAL).values]
base_stress = math.fsum(s_base) / len(s_base)          # vertical stress S33 on the base, compression negative
odb.close()

result = {'base_reaction_kN': float(reaction), 'top_settlement_m': float(settlement),
          'base_stress_kPa': float(base_stress)}
if VARIANT == 'fake_report':
    result['base_reaction_kN'] = float(reaction) * 1000.0
    result['base_stress_kPa'] = float(base_stress) * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
