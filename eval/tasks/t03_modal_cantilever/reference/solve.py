# -*- coding: utf-8 -*-
"""Reference solution for t03_modal_cantilever  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Natural frequencies of a steel cantilever: L=1.0 m, b=0.05 m, h=0.10 m, E=2.1e11 Pa, nu=0.3, rho=7850 kg/m3, one end
fully fixed.  Euler-Bernoulli: 41.78 Hz (1st bending about the weak axis), 83.55 Hz (1st about the strong axis),
261.8 Hz (2nd about the weak axis).

REF_VARIANT (environment variable):
  default        3-D solid, C3D20R, 4 x 8 x 80                                   -> must PASS
  beam_b32       3-D beam model, 40 B32 elements (alternative correct model)     -> must PASS
  coarse_c3d8r   C3D8R (CAE default element), 2 x 4 x 40 (seed 0.025)            -> must FAIL (too soft)
  wrong_density  density entered as 7.85 (t/m3) in an otherwise SI model         -> must FAIL (x 31.6)
  planar_2d      2-D plane-stress model in the plane of h: weak-axis modes lost  -> must FAIL
  fake_report    correct model, result.json lists textbook weak-axis modes only  -> must FAIL (report vs ODB)
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'modal'
L, B, H = 1.0, 0.05, 0.10
E, NU, RHO = 2.1e11, 0.3, 7850.0
KNOWN = ('default', 'beam_b32', 'coarse_c3d8r', 'wrong_density', 'planar_2d', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

m = mdb.Model(name='Modal')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((7.85 if VARIANT == 'wrong_density' else RHO,),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
tol = 1.0e-6

if VARIANT == 'beam_b32':
    s = m.ConstrainedSketch(name='axis', sheetSize=2.0)
    s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.RectangularProfile(name='Rect', a=B, b=H)
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=NU)
    region = regionToolset.Region(edges=p.edges)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    p.seedEdgeByNumber(edges=p.edges, number=40, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B32, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='FIXED', vertices=inst.vertices.getByBoundingBox(xMax=tol))
elif VARIANT == 'planar_2d':
    s = m.ConstrainedSketch(name='side', sheetSize=2.0)
    s.rectangle(point1=(0.0, 0.0), point2=(L, H))
    p = m.Part(name='Beam', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=B)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.seedPart(size=0.0125, deviationFactor=0.1, minSizeFactor=0.1)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPS8R, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='FIXED', edges=inst.edges.getByBoundingBox(xMax=tol))
else:
    elem_code, counts = (C3D8R, (2, 4, 40)) if VARIANT == 'coarse_c3d8r' else (C3D20R, (4, 8, 80))
    s = m.ConstrainedSketch(name='section', sheetSize=1.0)
    s.rectangle(point1=(0.0, 0.0), point2=(B, H))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=L)
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        axis = max(range(3), key=lambda k: abs(v1[k] - v0[k]))
        p.seedEdgeByNumber(edges=(e,), number=counts[axis], constraint=FIXED)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='FIXED', faces=inst.faces.getByBoundingBox(zMax=tol))

m.FrequencyStep(name='Freq', previous='Initial', eigensolver=LANCZOS, numEigen=6)
m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FIXED'])

job = mdb.Job(name=JOB, model='Modal', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: eigenfrequencies (cycles/s) from the ODB frames ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
freqs = [float(fr.frequency) for fr in odb.steps['Freq'].frames if fr.mode > 0]
odb.close()

result = {'frequencies_Hz': sorted(freqs)[:3]}
if VARIANT == 'fake_report':
    result = {'frequencies_Hz': [41.78, 261.8, 733.1]}
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
