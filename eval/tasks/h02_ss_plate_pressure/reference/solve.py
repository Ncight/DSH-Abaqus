# -*- coding: utf-8 -*-
"""Reference solution for h02_ss_plate_pressure  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Square plate 1 m x 1 m, t = 0.01 m, simply supported on all four edges, uniform pressure 1000 Pa, E = 2.1e11 Pa,
nu = 0.3, shell elements, small deflections.  Navier / Timoshenko: centre deflection 0.00406 q a^4 / D = 2.1112e-4 m,
centre bending moment 0.0479 q a^2 -> surface stress 2.874e6 Pa.

REF_VARIANT (environment variable):
  default       complete plate, S4R, 40 x 40                                               -> must PASS
  s8r           complete plate, S8R, 20 x 20                                               -> must PASS
  quarter       quarter plate with two symmetry edges, S4R, 20 x 20                        -> must PASS
  s4r_10x10     complete plate, S4R, 10 x 10 (a modest mesh that is still accurate enough)  -> must PASS
  clamped       all four edges fully fixed instead of simply supported                    -> must FAIL
  corner_supported  plate held at its four corners only, edges free                       -> must FAIL
  coarse        complete plate, S4R, 4 x 4 (nearest stress point 0.18 m from the centre)   -> must FAIL
  solid         C3D8R solid plate, one element through the thickness (task demands shells) -> must FAIL
  fake_report   correct model, bending stress written to result.json in MPa                -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'plate'
A, T = 1.0, 0.01
E, NU = 2.1e11, 0.3
Q = 1000.0
CFG = {
    'default': ('full', S4R, 40), 's8r': ('full', S8R, 20), 'quarter': ('quarter', S4R, 20),
    's4r_10x10': ('full', S4R, 10), 'clamped': ('full', S4R, 40), 'corner_supported': ('full', S4R, 40),
    'coarse': ('full', S4R, 4), 'solid': ('solid', C3D8R, 40), 'fake_report': ('full', S4R, 40),
}
if VARIANT not in CFG:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
kind, elem_code, n_el = CFG[VARIANT]
side = A / 2.0 if kind == 'quarter' else A          # the quarter model covers 0 <= x, y <= a/2, centre at (a/2, a/2)
tol = 1.0e-6

m = mdb.Model(name='Plate')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
s = m.ConstrainedSketch(name='plate', sheetSize=5.0)
s.rectangle(point1=(0.0, 0.0), point2=(side, side))
p = m.Part(name='Plate', dimensionality=THREE_D, type=DEFORMABLE_BODY)
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)

if kind == 'solid':
    p.BaseSolidExtrude(sketch=s, depth=T)
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.seedPart(size=side / n_el, deviationFactor=0.1, minSizeFactor=0.1)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Plate-1', part=p, dependent=ON)
    # "simple support": hold w on the bottom edges of the four side faces
    bottom_edges = inst.edges.getByBoundingBox(zMax=tol)
    a.Set(name='SUPPORT', edges=bottom_edges)
    a.Surface(name='TOP', side1Faces=inst.faces.getByBoundingBox(zMin=T - tol))
else:
    p.BaseShell(sketch=s)
    m.HomogeneousShellSection(name='Sec', material='Steel', thickness=T, numIntPts=5)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.seedEdgeByNumber(edges=p.edges, number=n_el, constraint=FIXED)
    p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Plate-1', part=p, dependent=ON)
    if kind == 'quarter':
        support = inst.edges.getByBoundingBox(xMax=tol) + inst.edges.getByBoundingBox(yMax=tol)
        a.Set(name='SYM_X', edges=inst.edges.getByBoundingBox(xMin=side - tol))     # plane x = a/2
        a.Set(name='SYM_Y', edges=inst.edges.getByBoundingBox(yMin=side - tol))     # plane y = a/2
    else:
        support = inst.edges
    if VARIANT == 'corner_supported':
        a.Set(name='SUPPORT', vertices=inst.vertices)
    else:
        a.Set(name='SUPPORT', edges=support)
    a.Surface(name='TOP', side1Faces=inst.faces)

a.Set(name='CORNER_A', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),)))
a.Set(name='CORNER_B', vertices=inst.vertices.findAt(((side, 0.0, 0.0),)))
if VARIANT == 'clamped':
    m.EncastreBC(name='Clamp', createStepName='Initial', region=a.sets['SUPPORT'])
else:
    m.DisplacementBC(name='Support', createStepName='Initial', region=a.sets['SUPPORT'], u3=0.0)
    if kind == 'quarter':
        m.XsymmBC(name='SymX', createStepName='Initial', region=a.sets['SYM_X'])
        m.YsymmBC(name='SymY', createStepName='Initial', region=a.sets['SYM_Y'])
    else:
        # in-plane rigid-body motions only
        m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['CORNER_A'], u1=0.0, u2=0.0)
        m.DisplacementBC(name='RollerB', createStepName='Initial', region=a.sets['CORNER_B'], u2=0.0)
m.Pressure(name='Pressure', createStepName='Load', region=a.surfaces['TOP'], magnitude=Q)
if kind == 'solid':
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))
else:
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'UR', 'RF', 'RM', 'SF'))

job = mdb.Job(name=JOB, model='Plate', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Load'].frames[-1]
w_max = max(abs(v.data[2]) for v in frame.fieldOutputs['U'].values)
s_max = max(v.maxPrincipal for v in frame.fieldOutputs['S'].values)      # all section points written to the ODB
odb.close()

result = {'center_deflection_m': float(w_max), 'max_bending_stress_Pa': float(s_max)}
if VARIANT == 'fake_report':
    result['max_bending_stress_Pa'] = float(s_max) / 1.0e6
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
