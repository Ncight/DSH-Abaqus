# -*- coding: utf-8 -*-
"""Reference solution for t02_plate_hole  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Plane-stress plate 0.4 m x 0.2 m x 0.01 m with a central hole d = 0.02 m, E=2.1e11 Pa, nu=0.3, uniform tension
100 MPa on the two short edges.  Peak stress at the hole edge ~ 3.03 x 100 MPa (Howland, d/W = 0.1).

REF_VARIANT (environment variable):
  default       quarter model, CPS8R, structured ring around the hole (24 x 20, graded)    -> must PASS
  full_plate    complete plate, CPS8R free mesh, hole edge size 0.0008 (~80 on the circle)  -> must PASS
  coarse        complete plate, CPS4R (CAE default), global size 0.01, no refinement        -> must FAIL (peak missed)
  cps8r_global  complete plate, CPS8R but only a global size 0.005, no local refinement     -> must FAIL (peak missed)
  wrong_load    quarter model, tension entered as 100 (Pa) instead of 100e6                 -> must FAIL
  fake_report   correct quarter model, max stress written to result.json in MPa             -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'plate_hole'
LX, LY, T, R = 0.4, 0.2, 0.01, 0.01
RING = 3.0 * R                      # outer radius of the structured ring of the quarter model
E, NU = 2.1e11, 0.3
SIGMA = 100.0e6

# quarter: (kind, quad element, elements on the quarter arc, radial elements in the ring, radial bias, global size)
# full   : (kind, quad element, size on the hole edge or None, global size)
QUARTER = ('quarter', CPS8R, 24, 20, 10.0, 0.008)
CFG = {
    'default': QUARTER,
    'full_plate': ('full', CPS8R, 0.0008, 0.008),
    'coarse': ('full', CPS4R, None, 0.01),
    'cps8r_global': ('full', CPS8R, None, 0.005),
    'wrong_load': QUARTER,
    'fake_report': QUARTER,
}
if VARIANT not in CFG:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
cfg = CFG[VARIANT]
kind, elem_code = cfg[0], cfg[1]
second_order = elem_code in (CPS8R, CPS8)
tri_code = CPS6M if second_order else CPS3
tol = 1.0e-6
hx, hy = LX / 2.0, LY / 2.0

m = mdb.Model(name='PlateHole')
s = m.ConstrainedSketch(name='plate', sheetSize=1.0)
if kind == 'quarter':
    s.Line(point1=(R, 0.0), point2=(hx, 0.0))
    s.Line(point1=(hx, 0.0), point2=(hx, hy))
    s.Line(point1=(hx, hy), point2=(0.0, hy))
    s.Line(point1=(0.0, hy), point2=(0.0, R))
    s.ArcByCenterEnds(center=(0.0, 0.0), point1=(0.0, R), point2=(R, 0.0), direction=CLOCKWISE)
else:
    s.rectangle(point1=(-hx, -hy), point2=(hx, hy))
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(R, 0.0))
p = m.Part(name='Plate', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
p.BaseShell(sketch=s)

mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=T)

if kind == 'quarter':
    n_theta, n_r, bias, global_size = cfg[2:]
    # ring R..RING around the hole: structured, graded towards the hole edge
    tr = p.MakeSketchTransform(sketchPlane=p.faces[0], sketchPlaneSide=SIDE1, origin=(0.0, 0.0, 0.0))
    s2 = m.ConstrainedSketch(name='ring', sheetSize=1.0, transform=tr)
    s2.ArcByCenterEnds(center=(0.0, 0.0), point1=(0.0, RING), point2=(RING, 0.0), direction=CLOCKWISE)
    p.PartitionFaceBySketch(faces=p.faces[:], sketch=s2)
    p.seedPart(size=global_size, deviationFactor=0.1, minSizeFactor=0.1)
    arcs, end1, end2 = [], [], []
    for e in p.edges:
        pts = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        rad = [math.hypot(q[0], q[1]) for q in pts]
        if abs(rad[0] - rad[1]) < tol and rad[0] < RING + tol:
            arcs.append(e)                              # the two arcs r = R and r = RING
        elif max(rad) < RING + tol:
            (end1 if rad[0] < rad[1] else end2).append(e)   # radial edges, small elements at the hole
    p.seedEdgeByNumber(edges=arcs, number=n_theta, constraint=FIXED)
    bias_args = dict(biasMethod=SINGLE, ratio=bias, number=n_r, constraint=FIXED)
    if end1:
        bias_args['end1Edges'] = end1
    if end2:
        bias_args['end2Edges'] = end2
    p.seedEdgeByBias(**bias_args)
    ring_face = p.faces.findAt(((0.5 * (R + RING) * math.cos(0.7), 0.5 * (R + RING) * math.sin(0.7), 0.0),))
    outer_face = p.faces.findAt(((0.75 * hx, 0.75 * hy, 0.0),))
    p.setMeshControls(regions=ring_face, elemShape=QUAD, technique=STRUCTURED)
    p.setMeshControls(regions=outer_face, elemShape=QUAD, algorithm=ADVANCING_FRONT)
else:
    hole_size, global_size = cfg[2:]
    p.seedPart(size=global_size, deviationFactor=0.1, minSizeFactor=0.1)
    if hole_size:
        hole_edges = p.edges.getByBoundingBox(xMin=-R - tol, xMax=R + tol, yMin=-R - tol, yMax=R + tol)
        p.seedEdgeBySize(edges=hole_edges, size=hole_size, deviationFactor=0.05, constraint=FINER)
    p.setMeshControls(regions=p.faces, elemShape=QUAD, algorithm=ADVANCING_FRONT)
p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),
                                                mesh.ElemType(elemCode=tri_code, elemLibrary=STANDARD)))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Plate-1', part=p, dependent=ON)
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)
sigma = 100.0 if VARIANT == 'wrong_load' else SIGMA
if kind == 'quarter':
    a.Set(name='XSYM', edges=inst.edges.getByBoundingBox(xMax=tol))
    a.Set(name='YSYM', edges=inst.edges.getByBoundingBox(yMax=tol))
    m.XsymmBC(name='XSym', createStepName='Initial', region=a.sets['XSYM'])
    m.YsymmBC(name='YSym', createStepName='Initial', region=a.sets['YSYM'])
    a.Surface(name='PULL', side1Edges=inst.edges.getByBoundingBox(xMin=hx - tol))
else:
    # statically determinate supports: no reaction develops under the self-equilibrated tension
    a.Set(name='PIN', vertices=inst.vertices.getByBoundingBox(xMax=-hx + tol, yMax=-hy + tol))
    a.Set(name='ROLLER', vertices=inst.vertices.getByBoundingBox(xMin=hx - tol, yMax=-hy + tol))
    m.DisplacementBC(name='Pin', createStepName='Initial', region=a.sets['PIN'], u1=0.0, u2=0.0)
    m.DisplacementBC(name='Roller', createStepName='Initial', region=a.sets['ROLLER'], u2=0.0)
    a.Surface(name='PULL', side1Edges=inst.edges.getByBoundingBox(xMin=hx - tol) +
              inst.edges.getByBoundingBox(xMax=-hx + tol))
m.Pressure(name='Tension', createStepName='Load', region=a.surfaces['PULL'], magnitude=-sigma)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))

job = mdb.Job(name=JOB, model='PlateHole', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: peak maximum principal stress extrapolated to the element nodes ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Load'].frames[-1]
sfield = frame.fieldOutputs['S'].getSubset(position=ELEMENT_NODAL)
smax = max(v.maxPrincipal for v in sfield.values)
odb.close()

result = {'max_stress_Pa': float(smax), 'stress_concentration_factor': float(smax) / SIGMA}
if VARIANT == 'fake_report':
    result['max_stress_Pa'] = float(smax) / 1.0e6
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
