# -*- coding: utf-8 -*-
"""Reference solution for d06_mc_triaxial_element  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  One element, drained triaxial compression of a Mohr-Coulomb soil:
c = 20 kPa, phi = 30 deg, E = 50 000 kPa, nu = 0.3; cell pressure 100 kPa; axial compression to 5 % strain.
    Kp = tan^2(45 + phi/2) = 3;  axial stress at failure = 100 x 3 + 2 x 20 x sqrt(3) = 369.28 kPa
    deviator stress at failure = 269.28 kPa (reached at 0.54 % axial strain, then constant)
The confinement is an initial stress state (-100 kPa in all directions) in equilibrium with the cell pressure on the
lateral faces and the held top: the confinement step produces no deformation.

REF_VARIANT (environment variable):
  default           1 m cube C3D8R, static confinement step, then top pushed down 0.05 m                  -> PASS
  geostatic_step    confinement as a *GEOSTATIC step                                                      -> PASS
  cax4r             axisymmetric specimen (radius 0.5 m, height 1 m), one CAX4R                           -> PASS
  associated        dilation angle = friction angle                                                       -> PASS
  phi_radians       friction angle entered as 0.5236 (radians)                                            -> FAIL
  no_initial_stress cell pressure applied without the initial stress: the specimen is squeezed first      -> FAIL
  tension_initial   initial stress +100 kPa (wrong sign)                                                  -> FAIL
  cohesion_ucs      cohesion yield stress entered as the unconfined strength 2c cos(phi)/(1 - sin(phi))   -> FAIL
  dp_beta_phi       linear Drucker-Prager with beta = phi, d = c instead of Mohr-Coulomb                  -> FAIL
  pressure_dropped  cell pressure removed in the shearing step (specimen sheared unconfined)              -> FAIL
  stop_early        axial strain 0.3 % only                                                               -> FAIL
  fake_report       correct analysis, deviator stress reported as the axial stress                        -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'triax'
KNOWN = ('default', 'geostatic_step', 'cax4r', 'associated', 'phi_radians', 'no_initial_stress', 'tension_initial',
         'cohesion_ucs', 'dp_beta_phi', 'pressure_dropped', 'stop_early', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

A = 1.0
E, NU = 50000.0, 0.3
C, PHI = 20.0, 30.0
CELL = 100.0
STRAIN = 0.003 if VARIANT == 'stop_early' else 0.05
axi = VARIANT == 'cax4r'
tol = 1.0e-6

m = mdb.Model(name='Triax')
mat = m.Material(name='Soil')
mat.Elastic(table=((E, NU),))
if VARIANT == 'dp_beta_phi':
    mat.DruckerPrager(table=((PHI, 1.0, 5.0),))
    mat.druckerPrager.DruckerPragerHardening(table=((C, 0.0),), type=SHEAR)
else:
    phi = math.radians(PHI) if VARIANT == 'phi_radians' else PHI
    mat.MohrCoulombPlasticity(table=((phi, PHI if VARIANT == 'associated' else 5.0),))
    coh = C
    if VARIANT == 'cohesion_ucs':
        coh = 2.0 * C * math.cos(math.radians(PHI)) / (1.0 - math.sin(math.radians(PHI)))
    mat.mohrCoulombPlasticity.MohrCoulombHardening(table=((coh, 0.0),))

s = m.ConstrainedSketch(name='sq', sheetSize=5.0)
if axi:
    s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 2.0))
    s.rectangle(point1=(0.0, 0.0), point2=(0.5, A))
    p = m.Part(name='Specimen', dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Soil')
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CAX4R, elemLibrary=STANDARD),))
else:
    s.rectangle(point1=(0.0, 0.0), point2=(A, A))
    p = m.Part(name='Specimen', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=A)
    m.HomogeneousSolidSection(name='Sec', material='Soil')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),))
p.seedPart(size=2.0 * A)
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Specimen-1', part=p, dependent=ON)
x_max = 0.5 if axi else A
a.Set(name='BOTTOM', nodes=inst.nodes.getByBoundingBox(yMax=tol))
a.Set(name='TOP', nodes=inst.nodes.getByBoundingBox(yMin=A - tol))
a.Set(name='X0', nodes=inst.nodes.getByBoundingBox(xMax=tol))
if axi:
    a.Set(name='ALL', faces=inst.faces)
    a.Surface(name='SIDE_X', side1Edges=inst.edges.getByBoundingBox(xMin=x_max - tol))
else:
    a.Set(name='ALL', cells=inst.cells)
    a.Set(name='Z0', nodes=inst.nodes.getByBoundingBox(zMax=tol))
    a.Surface(name='SIDE_X', side1Faces=inst.faces.getByBoundingBox(xMin=x_max - tol))
    a.Surface(name='SIDE_Z', side1Faces=inst.faces.getByBoundingBox(zMin=A - tol))

if VARIANT == 'geostatic_step':
    m.GeostaticStep(name='Confine', previous='Initial')
else:
    m.StaticStep(name='Confine', previous='Initial')
m.StaticStep(name='Shear', previous='Confine', initialInc=0.01, maxInc=0.02, minInc=1.0e-8, maxNumInc=2000)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'PE', 'PEEQ', 'U', 'RF'))

if VARIANT != 'no_initial_stress':
    s0 = CELL if VARIANT == 'tension_initial' else -CELL
    m.Stress(name='Confinement', region=a.sets['ALL'], distributionType=UNIFORM, sigma11=s0, sigma22=s0, sigma33=s0,
             sigma12=0.0, sigma13=0.0, sigma23=0.0)
m.DisplacementBC(name='Bottom', createStepName='Initial', region=a.sets['BOTTOM'], u2=0.0)
m.DisplacementBC(name='SymX', createStepName='Initial', region=a.sets['X0'], u1=0.0)
m.DisplacementBC(name='Top', createStepName='Initial', region=a.sets['TOP'], u2=0.0)
m.boundaryConditions['Top'].setValuesInStep(stepName='Shear', u2=-STRAIN * A)
m.Pressure(name='CellX', createStepName='Confine', region=a.surfaces['SIDE_X'], magnitude=CELL)
if not axi:
    m.DisplacementBC(name='SymZ', createStepName='Initial', region=a.sets['Z0'], u3=0.0)
    m.Pressure(name='CellZ', createStepName='Confine', region=a.surfaces['SIDE_Z'], magnitude=CELL)
if VARIANT == 'pressure_dropped':
    for name in ('CellX', 'CellZ'):
        m.loads[name].deactivate('Shear')

job = mdb.Job(name=JOB, model='Triax', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
f_c = odb.steps['Confine'].frames[-1]
f_s = odb.steps['Shear'].frames[-1]
u_conf = max([math.sqrt(math.fsum([float(x) ** 2 for x in v.data])) for v in f_c.fieldOutputs['U'].values])
st = f_s.fieldOutputs['S'].values[0]
axial = -float(st.minPrincipal)
lateral = -float(st.maxPrincipal)
deviator = float(st.mises)
n_frames = len(odb.steps['Shear'].frames)
odb.close()
sys.__stdout__.write('d06 %s: axial %.3f kPa  lateral %.3f kPa  deviator %.3f kPa  |U| after confinement %.3e m  '
                     'frames %d\n' % (VARIANT, axial, lateral, deviator, u_conf, n_frames))

result = {'axial_stress_failure_kPa': axial, 'deviator_stress_failure_kPa': deviator,
          'confinement_max_displacement_m': u_conf}
if VARIANT == 'fake_report':
    result['axial_stress_failure_kPa'] = deviator
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
