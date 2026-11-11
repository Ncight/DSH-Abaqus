# -*- coding: utf-8 -*-
"""Reference solution for x06_friction_slip  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Elastic block 0.1 x 0.1 x 0.1 m (E = 2.0e9 Pa, nu = 0.3) on the rigid plane y = 0, Coulomb friction mu = 0.3.
Step 1: 10 kN normal load (pressure 1.0e6 Pa on the top face).  Step 2: the block is pushed 1 mm in +x by a
prescribed displacement of its face x = 0; it sticks for the first 0.04 mm and then slides as a whole.
Sliding force = mu N = 3000 N, read as the reaction at the reference point of the rigid plane.

REF_VARIANT (environment variable):
  default           3-D, C3D8R 8x8x8, analytical rigid plane, penalty friction, face x = 0 pushed          -> PASS
  lagrange          friction enforced with Lagrange multipliers                                         -> PASS
  c3d20r            C3D20R 5x5x5 (nodal reactions of mixed sign on the pushed face)                      -> PASS
  plane_2d          2-D plane strain CPE4R 10x10, thickness 0.1                                          -> PASS
  push_top          the top face is pushed instead of the side face                                     -> PASS
  discrete_rigid    plane meshed with R3D4 discrete rigid elements                                      -> PASS
  frictionless      no friction                                                                         -> FAIL
  rough             ROUGH friction without separation (bonded): no slip, the block is sheared by 1 mm   -> FAIL
  fixed_base        no contact at all, bottom face of the block fixed, top face pushed                  -> FAIL
  small_push        pushed 0.02 mm only: still sticking                                                 -> FAIL
  mu_typo           mu = 0.03                                                                           -> FAIL
  wrong_normal      pressure 1.0e5 Pa (1 kN)                                                            -> FAIL
  force_below_slip  pushed with a 2 kN force instead of a displacement: sticks                          -> FAIL
  fake_report       correct model, sliding force written to result.json in kN                           -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'slip'
KNOWN = ('default', 'lagrange', 'c3d20r', 'plane_2d', 'push_top', 'discrete_rigid', 'frictionless', 'rough',
         'fixed_base', 'small_push', 'mu_typo', 'wrong_normal', 'force_below_slip', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

A = 0.1
E, NU = 2.0e9, 0.3
MU = 0.03 if VARIANT == 'mu_typo' else 0.3
PRESSURE = 1.0e5 if VARIANT == 'wrong_normal' else 1.0e6
PUSH = 2.0e-5 if VARIANT == 'small_push' else 1.0e-3
two_d = VARIANT == 'plane_2d'
with_plane = VARIANT != 'fixed_base'
tol = 1.0e-6

m = mdb.Model(name='Slip')
mat = m.Material(name='Block')
mat.Elastic(table=((E, NU),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)

# ---- the block ----
s = m.ConstrainedSketch(name='block', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(A, A))
if two_d:
    pb = m.Part(name='Block', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    pb.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Block', thickness=A)
    pb.SectionAssignment(region=regionToolset.Region(faces=pb.faces), sectionName='Sec')
    pb.seedPart(size=A / 10)
    pb.setElementType(regions=(pb.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4R, elemLibrary=STANDARD),))
else:
    pb = m.Part(name='Block', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    pb.BaseSolidExtrude(sketch=s, depth=A)
    m.HomogeneousSolidSection(name='Sec', material='Block')
    pb.SectionAssignment(region=regionToolset.Region(cells=pb.cells), sectionName='Sec')
    quad = VARIANT == 'c3d20r'
    pb.seedPart(size=A / (5 if quad else 8))
    pb.setElementType(regions=(pb.cells,), elemTypes=(mesh.ElemType(elemCode=C3D20R if quad else C3D8R,
                                                                    elemLibrary=STANDARD),))
pb.generateMesh()
ib = a.Instance(name='Block-1', part=pb, dependent=ON)
if two_d:
    a.Surface(name='BOTTOM', side1Edges=ib.edges.getByBoundingBox(yMax=tol))
    a.Surface(name='TOP', side1Edges=ib.edges.getByBoundingBox(yMin=A - tol))
    a.Surface(name='LEFT', side1Edges=ib.edges.getByBoundingBox(xMax=tol))
    a.Set(name='LEFT', edges=ib.edges.getByBoundingBox(xMax=tol))
    a.Set(name='TOP', edges=ib.edges.getByBoundingBox(yMin=A - tol))
    a.Set(name='BOTTOM', edges=ib.edges.getByBoundingBox(yMax=tol))
else:
    a.Surface(name='BOTTOM', side1Faces=ib.faces.getByBoundingBox(yMax=tol))
    a.Surface(name='TOP', side1Faces=ib.faces.getByBoundingBox(yMin=A - tol))
    a.Surface(name='LEFT', side1Faces=ib.faces.getByBoundingBox(xMax=tol))
    a.Set(name='LEFT', faces=ib.faces.getByBoundingBox(xMax=tol))
    a.Set(name='TOP', faces=ib.faces.getByBoundingBox(yMin=A - tol))
    a.Set(name='BOTTOM', faces=ib.faces.getByBoundingBox(yMax=tol))

# ---- the rigid plane y = 0 with its reference point ----
if with_plane:
    s2 = m.ConstrainedSketch(name='plane', sheetSize=1.0)
    s2.Line(point1=(-0.1, 0.0), point2=(0.3, 0.0))
    if two_d:
        pp = m.Part(name='Plane', dimensionality=TWO_D_PLANAR, type=ANALYTIC_RIGID_SURFACE)
        pp.AnalyticRigidSurf2DPlanar(sketch=s2)
    elif VARIANT == 'discrete_rigid':
        pp = m.Part(name='Plane', dimensionality=THREE_D, type=DISCRETE_RIGID_SURFACE)
        pp.BaseShellExtrude(sketch=s2, depth=0.3)
        pp.seedPart(size=0.05)
        pp.setElementType(regions=(pp.faces,), elemTypes=(mesh.ElemType(elemCode=R3D4, elemLibrary=STANDARD),))
        pp.generateMesh()
    else:
        pp = m.Part(name='Plane', dimensionality=THREE_D, type=ANALYTIC_RIGID_SURFACE)
        pp.AnalyticRigidSurfExtrude(sketch=s2, depth=0.6)
    rp = pp.ReferencePoint(point=(0.0, 0.0, 0.0))
    ip = a.Instance(name='Plane-1', part=pp, dependent=ON)
    if two_d:
        a.Surface(name='PLANE', side1Edges=ip.edges)          # side 1 = left of the sketch direction = +y
    elif VARIANT == 'discrete_rigid':
        a.translate(instanceList=('Plane-1',), vector=(0.0, 0.0, -0.1))
        ip = a.instances['Plane-1']
        if ip.faces[0].getNormal()[1] > 0.0:
            a.Surface(name='PLANE', side1Faces=ip.faces)
        else:
            a.Surface(name='PLANE', side2Faces=ip.faces)
    else:
        a.Surface(name='PLANE', side1Faces=ip.faces)
    a.Set(name='SUPPORT', referencePoints=(ip.referencePoints[rp.id],))

    cp = m.ContactProperty('Friction')
    # 'rough' is combined with no separation (a bonded interface); with separation allowed the sheared block
    # tips over its front edge and the run does not converge
    cp.NormalBehavior(pressureOverclosure=HARD, allowSeparation=OFF if VARIANT == 'rough' else ON)
    if VARIANT == 'frictionless':
        cp.TangentialBehavior(formulation=FRICTIONLESS)
    elif VARIANT == 'rough':
        cp.TangentialBehavior(formulation=ROUGH)
    elif VARIANT == 'lagrange':
        cp.TangentialBehavior(formulation=LAGRANGE, table=((MU,),))
    else:
        cp.TangentialBehavior(formulation=PENALTY, table=((MU,),), maximumElasticSlip=FRACTION, fraction=0.005)
    m.SurfaceToSurfaceContactStd(name='BlockOnPlane', createStepName='Initial', master=a.surfaces['PLANE'],
                                 slave=a.surfaces['BOTTOM'], sliding=FINITE, interactionProperty='Friction')
else:
    a.Set(name='SUPPORT', faces=ib.faces.getByBoundingBox(yMax=tol))

# ---- steps, loads, boundary conditions ----
m.StaticStep(name='Press', previous='Initial', initialInc=0.5, maxInc=1.0, minInc=1.0e-6, maxNumInc=100)
m.StaticStep(name='Push', previous='Press', initialInc=0.02, maxInc=0.1, minInc=1.0e-7, maxNumInc=500)
variables = ('S', 'U', 'RF', 'CF', 'CSTRESS', 'CDISP') if with_plane else ('S', 'U', 'RF', 'CF')
m.fieldOutputRequests['F-Output-1'].setValues(variables=variables)
m.Pressure(name='Normal', createStepName='Press', region=a.surfaces['TOP'], magnitude=PRESSURE)
m.EncastreBC(name='Support', createStepName='Initial', region=a.sets['SUPPORT'])
# (with a fixed bottom face the side face cannot be pushed: its bottom edge would get two conflicting conditions)
pushed = a.sets['TOP'] if VARIANT in ('push_top', 'fixed_base') else a.sets['LEFT']
if two_d:
    m.DisplacementBC(name='Push', createStepName='Initial', region=pushed, u1=0.0)
else:
    m.DisplacementBC(name='Push', createStepName='Initial', region=pushed, u1=0.0, u3=0.0)
if VARIANT == 'force_below_slip':
    m.boundaryConditions['Push'].setValuesInStep(stepName='Push', u1=FREED)
    m.Pressure(name='Shove', createStepName='Push', region=a.surfaces['LEFT'], magnitude=2000.0 / (A * A))
else:
    m.boundaryConditions['Push'].setValuesInStep(stepName='Push', u1=PUSH)

job = mdb.Job(name=JOB, model='Slip', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: reaction of the support (reference point of the plane) at the end of the push ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Push'].frames[-1]
vals = frame.fieldOutputs['RF'].getSubset(region=odb.rootAssembly.nodeSets['SUPPORT']).values
normal = abs(math.fsum([v.data[1] for v in vals]))
tangential = abs(math.fsum([v.data[0] for v in vals]))
odb.close()
sys.__stdout__.write('x06 %s: N = %.3f  F = %.3f  F/N = %.5f\n' % (
    VARIANT, normal, tangential, tangential / normal if normal else 0.0))

result = {'normal_force_N': float(normal), 'sliding_force_N': float(tangential)}
if VARIANT == 'fake_report':
    result['sliding_force_N'] = float(tangential) / 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
