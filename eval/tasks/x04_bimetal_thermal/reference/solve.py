# -*- coding: utf-8 -*-
"""Reference solution for x04_bimetal_thermal  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Bimetallic strip cantilever along +x, clamped at x = 0, length 0.1 m, width 0.01 m, two bonded layers of 1 mm:
layer A (-1 mm <= y <= 0): E = 2.0e11 Pa, alpha = 1.2e-5 /K;  layer B (0 <= y <= 1 mm): E = 1.0e11 Pa,
alpha = 2.0e-5 /K;  nu = 0.3.  Stress free at 20 C, heated uniformly to 120 C.  The strip is narrow and free to
deform across its width: plane stress in the x-y plane (or a 3-D model).
Timoshenko's bimetal formula: curvature 0.58182 1/m, the strip bends towards layer A: tip deflection -2.909e-3 m.

REF_VARIANT (environment variable):
  default          2-D plane stress, CPS8R, 100 x (2 + 2) elements                                    -> must PASS
  cps4i            2-D plane stress, CPS4I incompatible-mode elements, 100 x (2 + 2)                   -> must PASS
  solid3d          3-D solid C3D20R 50 x (2 + 2) x 4 (the clamp hinders the transverse curvature)      -> must PASS
  nlgeom_on        default with NLGEOM (the deflection changes by 0.03 %)                              -> must PASS
  swapped_moduli   E of the two layers exchanged: Timoshenko's formula is symmetric in the modular
                   ratio for equal thicknesses, the deflection is the same (documented blind spot)     -> passes
  plane_strain     CPE8R instead of plane stress (curvature x 1.3)                                     -> must FAIL
  no_initial_temp  120 C applied without defining the 20 C stress-free temperature (rise of 120 K)     -> must FAIL
  equal_alpha      both layers with alpha = 1.2e-5                                                     -> must FAIL
  unbonded         the two layers are separate bodies without a tie                                    -> must FAIL
  swapped_layers   layer B below, layer A on top: bends the other way                                  -> must FAIL
  coarse_cps4r     CPS4R, one element per layer, 25 along the length                                   -> must FAIL
  cps4_full        CPS4 full integration 100 x (2 + 2), aspect ratio 2: shear locking, -9.7 %          -> must FAIL
  fake_report      correct model, deflection written to result.json in mm                              -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'bimetal'
KNOWN = ('default', 'cps4i', 'solid3d', 'nlgeom_on', 'swapped_moduli', 'plane_strain', 'no_initial_temp',
         'equal_alpha', 'unbonded', 'swapped_layers', 'coarse_cps4r', 'cps4_full', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, T, W = 0.1, 0.001, 0.01
NU = 0.3
LAYER_A = [2.0e11, 1.2e-5]                      # E, alpha of the layer on the -y side
LAYER_B = [1.0e11, 1.2e-5 if VARIANT == 'equal_alpha' else 2.0e-5]
if VARIANT == 'swapped_layers':
    LAYER_A, LAYER_B = LAYER_B, LAYER_A
if VARIANT == 'swapped_moduli':
    LAYER_A[0], LAYER_B[0] = LAYER_B[0], LAYER_A[0]
T_REF, T_HOT = 20.0, 120.0
three_d = VARIANT == 'solid3d'
if VARIANT == 'coarse_cps4r':
    elem_code, n_x, n_t = CPS4R, 25, 1
elif VARIANT == 'cps4i':
    elem_code, n_x, n_t = CPS4I, 100, 2
elif VARIANT == 'cps4_full':
    elem_code, n_x, n_t = CPS4, 100, 2
elif VARIANT == 'plane_strain':
    elem_code, n_x, n_t = CPE8R, 100, 2
elif three_d:
    elem_code, n_x, n_t = C3D20R, 50, 2
else:
    elem_code, n_x, n_t = CPS8R, 100, 2
tol = 1.0e-7

m = mdb.Model(name='Bimetal')
for name, (e_mod, alpha) in (('MatLower', LAYER_A), ('MatUpper', LAYER_B)):
    mat = m.Material(name=name)
    mat.Elastic(table=((e_mod, NU),))
    mat.Expansion(table=((alpha,),))
    if three_d:
        m.HomogeneousSolidSection(name=name.replace('Mat', 'Sec'), material=name)
    else:
        m.HomogeneousSolidSection(name=name.replace('Mat', 'Sec'), material=name, thickness=W)
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)


def seed_and_mesh(p):
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        axis = max(range(3), key=lambda k: abs(v1[k] - v0[k]))
        p.seedEdgeByNumber(edges=(e,), number=(n_x, n_t, 4)[axis], constraint=FIXED)
    regions = p.cells if three_d else p.faces
    p.setElementType(regions=(regions,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()


def strip_part(name, y0, y1):
    s = m.ConstrainedSketch(name='sk' + name, sheetSize=1.0)
    s.rectangle(point1=(0.0, y0), point2=(L, y1))
    if three_d:
        p = m.Part(name=name, dimensionality=THREE_D, type=DEFORMABLE_BODY)
        p.BaseSolidExtrude(sketch=s, depth=W)
    else:
        p = m.Part(name=name, dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
        p.BaseShell(sketch=s)
    return p


if VARIANT == 'unbonded':
    # two bodies that merely lie on top of each other: nothing joins them
    for name, (y0, y1), sec in (('Lower', (-T, 0.0), 'SecLower'), ('Upper', (0.0, T), 'SecUpper')):
        p = strip_part(name, y0, y1)
        p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName=sec)
        seed_and_mesh(p)
        a.Instance(name=name + '-1', part=p, dependent=ON)
    insts = [a.instances['Lower-1'], a.instances['Upper-1']]
    everything = insts[0].faces + insts[1].faces
    clamp = insts[0].edges.getByBoundingBox(xMax=tol) + insts[1].edges.getByBoundingBox(xMax=tol)
    tip = insts[0].vertices.findAt(((L, 0.0, 0.0),))
else:
    p = strip_part('Strip', -T, T)
    plane = p.datums[p.DatumPlaneByPrincipalPlane(principalPlane=XZPLANE, offset=0.0).id]
    if three_d:
        p.PartitionCellByDatumPlane(cells=p.cells[:], datumPlane=plane)
        p.SectionAssignment(region=regionToolset.Region(cells=p.cells.findAt(((L / 2, -T / 2, W / 2),))),
                            sectionName='SecLower')
        p.SectionAssignment(region=regionToolset.Region(cells=p.cells.findAt(((L / 2, T / 2, W / 2),))),
                            sectionName='SecUpper')
    else:
        p.PartitionFaceByDatumPlane(faces=p.faces[:], datumPlane=plane)
        p.SectionAssignment(region=regionToolset.Region(faces=p.faces.findAt(((L / 2, -T / 2, 0.0),))),
                            sectionName='SecLower')
        p.SectionAssignment(region=regionToolset.Region(faces=p.faces.findAt(((L / 2, T / 2, 0.0),))),
                            sectionName='SecUpper')
    seed_and_mesh(p)
    inst = a.Instance(name='Strip-1', part=p, dependent=ON)
    if three_d:
        everything = inst.cells
        clamp = inst.faces.getByBoundingBox(xMax=tol)
    else:
        everything = inst.faces
        clamp = inst.edges.getByBoundingBox(xMax=tol)
    tip = inst.vertices.findAt(((L, 0.0, 0.0),))
if three_d:
    a.Set(name='ALL', cells=everything)
    a.Set(name='CLAMP', faces=clamp)
else:
    a.Set(name='ALL', faces=everything)
    a.Set(name='CLAMP', edges=clamp)
a.Set(name='TIP', vertices=tip)

m.StaticStep(name='Heat', previous='Initial', nlgeom=ON if VARIANT == 'nlgeom_on' else OFF)
m.EncastreBC(name='Clamp', createStepName='Initial', region=a.sets['CLAMP'])
if VARIANT == 'no_initial_temp':
    m.Temperature(name='Temp', createStepName='Heat', region=a.sets['ALL'], distributionType=UNIFORM,
                  crossSectionDistribution=CONSTANT_THROUGH_THICKNESS, magnitudes=(T_HOT,))
else:
    m.Temperature(name='Temp', createStepName='Initial', region=a.sets['ALL'], distributionType=UNIFORM,
                  crossSectionDistribution=CONSTANT_THROUGH_THICKNESS, magnitudes=(T_REF,))
    m.predefinedFields['Temp'].setValuesInStep(stepName='Heat', magnitudes=(T_HOT,))
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'THE', 'U', 'RF', 'NT'))

job = mdb.Job(name=JOB, model='Bimetal', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: transverse displacement of the tip (interface point at x = L) ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Heat'].frames[-1]
tip_u2 = frame.fieldOutputs['U'].getSubset(region=odb.rootAssembly.nodeSets['TIP']).values[0].data[1]
u2_all = [v.data[1] for v in frame.fieldOutputs['U'].values]
u1_all = [v.data[0] for v in frame.fieldOutputs['U'].values]
odb.close()
sys.__stdout__.write('x04 %s: tip U2 = %.6e, U2 range [%.6e, %.6e], U1 max %.6e\n' % (
    VARIANT, tip_u2, min(u2_all), max(u2_all), max(u1_all)))

result = {'tip_deflection_m': float(tip_u2)}
if VARIANT == 'fake_report':
    result['tip_deflection_m'] = float(tip_u2) * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
