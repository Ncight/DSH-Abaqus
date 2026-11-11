# -*- coding: utf-8 -*-
"""Reference solution for x08_torsion_coupling  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Solid circular shaft along z, L = 0.5 m, d = 0.04 m, E = 2.1e11 Pa, nu = 0.3 (G = 8.0769e10 Pa).  The end z = 0 is
fixed; a reference point at (0, 0, 0.5) is coupled to the whole end face z = 0.5 and carries a torque of 500 Nm.
    J = pi d^4 / 32 = 2.5133e-7 m4,  twist = T L / (G J) = 0.012315 rad,
    max shear stress = T r / J = 3.9789e7 Pa  (Mises = sqrt(3) tau = 6.892e7 Pa)

REF_VARIANT (environment variable):
  default             C3D20R, 4 mm in the section x 25 along the axis, kinematic coupling, moment at the RP   -> PASS
  distributing        distributing coupling instead of kinematic                                              -> PASS
  c3d8r_fine          C3D8R, 1.5 mm in the section x 40 along the axis                                        -> PASS
  c3d10_tet           free tetrahedral mesh C3D10, 6 mm                                                       -> PASS
  coarse_c3d8r        C3D8R, 5 mm: faceted section and centroid stresses                                      -> FAIL
  radius_mixup        cylinder of radius 0.04 (the diameter taken as the radius)                              -> FAIL
  wrong_axis          moment about x instead of z: bending                                                    -> FAIL
  few_nodes_coupling  RP coupled to four rim nodes only                                                       -> FAIL
  tangential_forces   no RP: four tangential rim forces with the diameter as lever arm (250 Nm)               -> FAIL
  fake_report         correct model, Mises stress written to result.json as the shear stress                  -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'torsion'
KNOWN = ('default', 'distributing', 'c3d8r_fine', 'c3d10_tet', 'coarse_c3d8r', 'radius_mixup', 'wrong_axis',
         'few_nodes_coupling', 'tangential_forces', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, D = 0.5, 0.04
R = D if VARIANT == 'radius_mixup' else D / 2.0
E, NU = 2.1e11, 0.3
TORQUE = 500.0
tol = 1.0e-6
if VARIANT == 'c3d8r_fine':
    code, size, n_axial = C3D8R, 0.0015, 40
elif VARIANT == 'coarse_c3d8r':
    code, size, n_axial = C3D8R, 0.005, 20
elif VARIANT == 'c3d10_tet':
    code, size, n_axial = C3D10, 0.006, 0
else:
    code, size, n_axial = C3D20R, 0.004 * (R / 0.02), 25

m = mdb.Model(name='Torsion')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
m.HomogeneousSolidSection(name='Sec', material='Steel')
s = m.ConstrainedSketch(name='circle', sheetSize=0.2)
s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(R, 0.0))
p = m.Part(name='Shaft', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=L)
for plane in (XZPLANE, YZPLANE):                       # four quadrants: rim vertices at 0 / 90 / 180 / 270 degrees
    p.PartitionCellByDatumPlane(cells=p.cells[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=plane, offset=0.0).id])
p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
p.seedPart(size=size, deviationFactor=0.05, minSizeFactor=0.1)
if n_axial:
    for e in p.edges:
        pts = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        if len(pts) == 2 and abs(pts[0][2] - pts[1][2]) > 0.5 * L:
            p.seedEdgeByNumber(edges=(e,), number=n_axial, constraint=FIXED)
    p.setMeshControls(regions=p.cells, elemShape=HEX, technique=SWEEP, algorithm=MEDIAL_AXIS)
else:
    p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=STANDARD),))
p.generateMesh()
if len(p.elements) > 60000:
    raise RuntimeError('mesh too large: %d elements' % len(p.elements))

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Shaft-1', part=p, dependent=ON)
a.Set(name='FIXED', faces=inst.faces.getByBoundingBox(zMax=tol))
tip_faces = inst.faces.getByBoundingBox(zMin=L - tol)
a.Surface(name='TIP', side1Faces=tip_faces)
rim = [(R, 0.0), (0.0, R), (-R, 0.0), (0.0, -R)]
rim_labels = []
for i, (x, y) in enumerate(rim):
    nd = inst.nodes.getByBoundingSphere(center=(x, y, L), radius=1.0e-5)
    a.Set(name='RIM%d' % i, nodes=nd)
    rim_labels.append(nd[0].label)
a.Set(name='RIM', nodes=inst.nodes.sequenceFromLabels(rim_labels))

m.StaticStep(name='Twist', previous='Initial')
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CF'))
m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FIXED'])
with_rp = VARIANT != 'tangential_forces'
if with_rp:
    rp = a.ReferencePoint(point=(0.0, 0.0, L))
    a.Set(name='RP', referencePoints=(a.referencePoints[rp.id],))
    if VARIANT == 'few_nodes_coupling':
        m.Coupling(name='Tip', controlPoint=a.sets['RP'], surface=a.sets['RIM'], influenceRadius=WHOLE_SURFACE,
                   couplingType=KINEMATIC, u1=ON, u2=ON, u3=ON, ur1=ON, ur2=ON, ur3=ON)
    else:
        m.Coupling(name='Tip', controlPoint=a.sets['RP'], surface=a.surfaces['TIP'], influenceRadius=WHOLE_SURFACE,
                   couplingType=DISTRIBUTING if VARIANT == 'distributing' else KINEMATIC,
                   u1=ON, u2=ON, u3=ON, ur1=ON, ur2=ON, ur3=ON)
    if VARIANT == 'wrong_axis':
        m.Moment(name='Torque', createStepName='Twist', region=a.sets['RP'], cm1=TORQUE)
    else:
        m.Moment(name='Torque', createStepName='Twist', region=a.sets['RP'], cm3=TORQUE)
else:
    f = TORQUE / (4.0 * D)                              # lever arm error: diameter instead of radius
    for i, (x, y) in enumerate(rim):                    # tangential direction (-y, x) / R
        m.ConcentratedForce(name='F%d' % i, createStepName='Twist', region=a.sets['RIM%d' % i],
                            cf1=-f * y / R, cf2=f * x / R)

job = mdb.Job(name=JOB, model='Torsion', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Twist'].frames[-1]
asm = odb.rootAssembly
if with_rp:
    ur = frame.fieldOutputs['UR'].getSubset(region=asm.nodeSets['RP']).values[0].data
    twist = math.sqrt(math.fsum([float(c) ** 2 for c in ur]))
else:
    u = frame.fieldOutputs['U'].getSubset(region=asm.nodeSets['RIM']).values
    twist = math.fsum([math.sqrt(v.data[0] ** 2 + v.data[1] ** 2) for v in u]) / len(u) / R
stress = frame.fieldOutputs['S'].getSubset(position=ELEMENT_NODAL)
mises = max([v.mises for v in stress.values])
tresca = max([v.tresca for v in stress.values])
n_el = len(asm.instances['SHAFT-1'].elements)
odb.close()
tau = mises / math.sqrt(3.0)
sys.__stdout__.write('x08 %s: twist %.6e rad  Mises max %.5e  tau = Mises/sqrt3 %.5e  Tresca/2 %.5e  elements %d\n' % (
    VARIANT, twist, mises, tau, tresca / 2.0, n_el))

result = {'twist_angle_rad': float(twist), 'max_shear_stress_Pa': float(tau)}
if VARIANT == 'fake_report':
    result['max_shear_stress_Pa'] = float(mises)
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
