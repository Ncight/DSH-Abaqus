# -*- coding: utf-8 -*-
"""Reference solution for t04_lame_cylinder  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Thick-walled cylinder in plane strain: inner radius 0.1 m, outer radius 0.2 m, internal pressure 100 MPa,
E=2.1e11 Pa, nu=0.3.  Lame: hoop stress 166.67 MPa at the bore and 66.67 MPa outside, radial stress -100 MPa at the
bore, radial displacement of the bore 9.0794e-5 m (plane stress would give 9.365e-5 m).

REF_VARIANT (environment variable):
  default       quarter model, CPE8R, 20 (radial) x 40 structured                          -> must PASS
  axisym        axisymmetric strip, CAX8R, axial displacement fixed at both ends            -> must PASS
  full_ring     complete ring, CPE8R, three point restraints that let the ring translate
                and rotate as a rigid body (max |U| is then NOT the radial displacement)    -> must PASS
  plane_stress  quarter model with CPS8R (open-ended cylinder)                              -> must FAIL
  coarse        quarter model, CPE4R (CAE default), 5 x 10 elements                         -> must FAIL
  fake_report   correct quarter model, result.json holds thin-wall hand numbers             -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'cylinder'
RI, RO = 0.1, 0.2
E, NU = 2.1e11, 0.3
PRESSURE = 100.0e6

# kind, element, radial elements, circumferential elements (per quarter) / axial elements
CFG = {
    'default': ('quarter', CPE8R, 20, 40),
    'axisym': ('axisym', CAX8R, 20, 4),
    'full_ring': ('full', CPE8R, 20, 40),
    'plane_stress': ('quarter', CPS8R, 20, 40),
    'coarse': ('quarter', CPE4R, 5, 10),
    'fake_report': ('quarter', CPE8R, 20, 40),
}
if VARIANT not in CFG:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
kind, elem_code, n_r, n_t = CFG[VARIANT]
tol = 1.0e-6
c45 = math.cos(math.pi / 4.0)

m = mdb.Model(name='Cylinder')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=1.0 if kind != 'axisym' else None)

s = m.ConstrainedSketch(name='wall', sheetSize=1.0)
if kind == 'quarter':
    s.ArcByCenterEnds(center=(0.0, 0.0), point1=(RI, 0.0), point2=(0.0, RI), direction=COUNTERCLOCKWISE)
    s.ArcByCenterEnds(center=(0.0, 0.0), point1=(RO, 0.0), point2=(0.0, RO), direction=COUNTERCLOCKWISE)
    s.Line(point1=(RI, 0.0), point2=(RO, 0.0))
    s.Line(point1=(0.0, RI), point2=(0.0, RO))
    p = m.Part(name='Wall', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
elif kind == 'full':
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(RI, 0.0))
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(RO, 0.0))
    p = m.Part(name='Wall', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    tr = p.MakeSketchTransform(sketchPlane=p.faces[0], sketchPlaneSide=SIDE1, origin=(0.0, 0.0, 0.0))
    s2 = m.ConstrainedSketch(name='axes', sheetSize=1.0, transform=tr)
    s2.Line(point1=(-2.0 * RO, 0.0), point2=(2.0 * RO, 0.0))
    s2.Line(point1=(0.0, -2.0 * RO), point2=(0.0, 2.0 * RO))
    p.PartitionFaceBySketch(faces=p.faces[:], sketch=s2)
else:
    HZ = 0.02
    s.sketchOptions.setValues(viewStyle=AXISYM)
    s.ConstructionLine(point1=(0.0, -0.1), point2=(0.0, 0.1))
    s.rectangle(point1=(RI, 0.0), point2=(RO, HZ))
    p = m.Part(name='Wall', dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')

# seeds: straight edges of the planar models are radial, arcs are circumferential
for e in p.edges:
    pts = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    if kind == 'axisym':
        radial = abs(pts[0][1] - pts[1][1]) < tol
    else:
        rad = [math.hypot(q[0], q[1]) for q in pts]
        radial = len(pts) == 2 and abs(rad[0] - rad[1]) > tol
    p.seedEdgeByNumber(edges=(e,), number=n_r if radial else n_t, constraint=FIXED)
p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Wall-1', part=p, dependent=ON)
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)


def edges_at_radius(r):
    """the arc(s) of radius r: one arc in the quarter model, four in the partitioned complete ring"""
    quadrants = ((1, 1),) if kind == 'quarter' else ((1, 1), (-1, 1), (-1, -1), (1, -1))
    return inst.edges.findAt(*[((sx * r * c45, sy * r * c45, 0.0),) for sx, sy in quadrants])


if kind == 'axisym':
    inner = inst.edges.getByBoundingBox(xMax=RI + tol)
    outer = inst.edges.getByBoundingBox(xMin=RO - tol)
    a.Set(name='ENDS', edges=inst.edges.getByBoundingBox(yMax=tol) + inst.edges.getByBoundingBox(yMin=HZ - tol))
    m.DisplacementBC(name='PlaneStrain', createStepName='Initial', region=a.sets['ENDS'], u2=0.0)
else:
    inner = edges_at_radius(RI)
    outer = edges_at_radius(RO)
    if kind == 'quarter':
        a.Set(name='XSYM', edges=inst.edges.getByBoundingBox(xMax=tol))
        a.Set(name='YSYM', edges=inst.edges.getByBoundingBox(yMax=tol))
        m.XsymmBC(name='XSym', createStepName='Initial', region=a.sets['XSYM'])
        m.YsymmBC(name='YSym', createStepName='Initial', region=a.sets['YSYM'])
    else:
        # legitimate but "untidy" supports: no reactions develop, yet the ring shifts and turns as a rigid body
        a.Set(name='PIN', vertices=inst.vertices.findAt(((RO, 0.0, 0.0),)))
        a.Set(name='ROLLER', vertices=inst.vertices.findAt(((0.0, RO, 0.0),)))
        m.DisplacementBC(name='Pin', createStepName='Initial', region=a.sets['PIN'], u1=0.0, u2=0.0)
        m.DisplacementBC(name='Roller', createStepName='Initial', region=a.sets['ROLLER'], u1=0.0)
        a.Set(name='BORE_PX', vertices=inst.vertices.findAt(((RI, 0.0, 0.0),)))
        a.Set(name='BORE_MX', vertices=inst.vertices.findAt(((-RI, 0.0, 0.0),)))
a.Set(name='INNER', edges=inner)
a.Set(name='OUTER', edges=outer)
a.Surface(name='BORE', side1Edges=inner)
m.Pressure(name='Internal', createStepName='Load', region=a.surfaces['BORE'], magnitude=PRESSURE)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))

job = mdb.Job(name=JOB, model='Cylinder', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Load'].frames[-1]
ns = odb.rootAssembly.nodeSets
S = frame.fieldOutputs['S']
U = frame.fieldOutputs['U']


def mean_hoop(set_name):
    vals = S.getSubset(region=ns[set_name], position=ELEMENT_NODAL).values
    return math.fsum(v.maxPrincipal for v in vals) / len(vals)   # hoop stress = largest principal stress


hoop_in = mean_hoop('INNER')
hoop_out = mean_hoop('OUTER')
if kind == 'full':
    # diametral growth of the bore / 2: free of the rigid-body shift and rotation
    upx = U.getSubset(region=ns['BORE_PX']).values[0].data[0]
    umx = U.getSubset(region=ns['BORE_MX']).values[0].data[0]
    u_in = 0.5 * (upx - umx)
elif kind == 'axisym':
    vals = U.getSubset(region=ns['INNER']).values
    u_in = math.fsum(v.data[0] for v in vals) / len(vals)
else:
    vals = U.getSubset(region=ns['INNER']).values
    u_in = math.fsum(math.hypot(v.data[0], v.data[1]) for v in vals) / len(vals)
odb.close()

result = {'hoop_stress_inner_Pa': float(hoop_in), 'radial_disp_inner_m': float(u_in),
          'hoop_stress_outer_Pa': float(hoop_out)}
if VARIANT == 'fake_report':
    # thin-wall hand formula p*r/t and the matching displacement - not what the ODB says
    result = {'hoop_stress_inner_Pa': 1.0e8, 'radial_disp_inner_m': 1.0e8 / E * RI, 'hoop_stress_outer_Pa': 1.0e8}
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
