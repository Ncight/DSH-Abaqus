# -*- coding: utf-8 -*-
"""Reference solution for h03_plasticity_bar  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Uniaxial tension of a prismatic steel bar 0.01 m x 0.01 m x 0.1 m (axis = X) to 2 % total axial strain, small
strains (nlgeom off).  E = 2.0e11 Pa, nu = 0.3, yield 250 MPa, linear isotropic hardening with plastic modulus
H = 2.0e9 Pa.  Closed form: stress (250e6 + 2e9 * 0.02) / 1.01 = 2.87129e8 Pa, PEEQ = 0.018564.

REF_VARIANT (environment variable):
  default       C3D8R 2 x 2 x 10, one end face held axially, lateral contraction free       -> must PASS
  truss         a single T3D2 truss element with the same material                         -> must PASS
  short_table   hardening table stops at a plastic strain of 0.01 (flat afterwards)        -> must FAIL
  fixed_end     both end faces fully gripped (no lateral contraction at the ends)          -> must FAIL
  elastic_only  plasticity forgotten                                                       -> must FAIL
  fake_report   correct model, result.json gives the total strain 0.02 as "peeq"           -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'plastic'
L, W = 0.1, 0.01
E, NU = 2.0e11, 0.3
SY, HMOD = 250.0e6, 2.0e9
STRAIN = 0.02
KNOWN = ('default', 'truss', 'short_table', 'fixed_end', 'elastic_only', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
tol = 1.0e-6

m = mdb.Model(name='Bar')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
if VARIANT == 'short_table':
    mat.Plastic(table=((SY, 0.0), (SY + HMOD * 0.01, 0.01)))
elif VARIANT != 'elastic_only':
    mat.Plastic(table=((SY, 0.0), (SY + HMOD * 0.1, 0.1)))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
m.StaticStep(name='Pull', previous='Initial', nlgeom=OFF, initialInc=0.05, minInc=1.0e-6, maxInc=0.05, maxNumInc=200)

if VARIANT == 'truss':
    s = m.ConstrainedSketch(name='axis', sheetSize=1.0)
    s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
    p = m.Part(name='Bar', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.TrussSection(name='Sec', material='Steel', area=W * W)
    p.SectionAssignment(region=regionToolset.Region(edges=p.edges), sectionName='Sec')
    p.seedEdgeByNumber(edges=p.edges, number=1, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=T3D2, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Bar-1', part=p, dependent=ON)
    a.Set(name='END_0', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    a.Set(name='END_L', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))
    m.DisplacementBC(name='Hold', createStepName='Initial', region=a.sets['END_0'], u1=0.0, u2=0.0, u3=0.0)
    m.DisplacementBC(name='Guide', createStepName='Initial', region=a.sets['END_L'], u2=0.0, u3=0.0)
    m.DisplacementBC(name='Pull', createStepName='Pull', region=a.sets['END_L'], u1=STRAIN * L)
else:
    s = m.ConstrainedSketch(name='section', sheetSize=1.0)
    s.rectangle(point1=(0.0, 0.0), point2=(L, W))
    p = m.Part(name='Bar', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=W)
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.seedPart(size=W / 2.0, deviationFactor=0.1, minSizeFactor=0.1)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Bar-1', part=p, dependent=ON)
    a.Set(name='END_0', faces=inst.faces.getByBoundingBox(xMax=tol))
    a.Set(name='END_L', faces=inst.faces.getByBoundingBox(xMin=L - tol))
    if VARIANT == 'fixed_end':
        m.EncastreBC(name='Hold', createStepName='Initial', region=a.sets['END_0'])
        m.DisplacementBC(name='Pull', createStepName='Pull', region=a.sets['END_L'], u1=STRAIN * L, u2=0.0, u3=0.0)
    else:
        a.Set(name='CORNER_A', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),)))
        a.Set(name='CORNER_B', vertices=inst.vertices.findAt(((0.0, W, 0.0),)))
        m.DisplacementBC(name='Hold', createStepName='Initial', region=a.sets['END_0'], u1=0.0)
        m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['CORNER_A'], u2=0.0, u3=0.0)
        m.DisplacementBC(name='RollerB', createStepName='Initial', region=a.sets['CORNER_B'], u3=0.0)
        m.DisplacementBC(name='Pull', createStepName='Pull', region=a.sets['END_L'], u1=STRAIN * L)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'PE', 'PEEQ', 'U', 'RF'))

job = mdb.Job(name=JOB, model='Bar', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: mean axial stress and mean PEEQ over all integration points at the end of the step ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Pull'].frames[-1]
s11 = [v.data[0] for v in frame.fieldOutputs['S'].values]
axial_stress = math.fsum(s11) / len(s11)
if 'PEEQ' in frame.fieldOutputs.keys():
    pq = [v.data for v in frame.fieldOutputs['PEEQ'].values]
    peeq = math.fsum(pq) / len(pq)
else:
    peeq = 0.0
odb.close()

result = {'axial_stress_Pa': float(axial_stress), 'peeq': float(peeq)}
if VARIANT == 'fake_report':
    result['peeq'] = STRAIN
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
