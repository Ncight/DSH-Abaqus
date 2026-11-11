# -*- coding: utf-8 -*-
"""Reference solution for d04_cdp_tension_damage_element  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  One 0.1 m cube of concrete damaged plasticity in uniaxial tension along x:
E0 = 3.0e7 kPa, nu = 0.2, ft = 2000 kPa;
  tension stiffening  (cracking strain, stress kPa): (0, 2000) (0.0002, 1200) (0.0005, 600) (0.001, 200)
  tension damage      (cracking strain, dt):         (0, 0)    (0.0002, 0.3)  (0.0005, 0.6) (0.001, 0.85)
Total strain history (displacement of the face x = 0.1 m): 0 -> 0.0004 -> 0.00036 -> 0.0008.

Abaqus' definitions:  cracking strain e_ck = e - s/E0;  plastic strain e_pl = e_ck - dt/(1-dt) s/E0;
unloading modulus (1 - dt) E0;  stress s = (1 - dt) E0 (e - e_pl).   With the tables interpolated linearly in the
cracking strain:  e = 0.0004: e_ck = 3.7143e-4, s = 857.1 kPa, dt = 0.4714, unloading modulus 1.5857e7 kPa;
e = 0.00036 (unloaded): s = 222.9 kPa;  e = 0.0008: e_ck = 7.8767e-4, s = 369.9 kPa, dt = 0.7438.

REF_VARIANT (environment variable):
  default           one C3D8R, three static steps, strain increments of 1e-5                              -> PASS
  cps4r             one CPS4R (plane stress, thickness 0.1)                                               -> PASS
  c3d8              one fully integrated C3D8                                                             -> PASS
  one_step          one step, the displacement history as a tabular amplitude                             -> PASS
  series_5          five elements in series along x                                                       -> see variants.json
  no_damage         tension damage table left out: unloading with E0, no DAMAGET                          -> FAIL
  displacement_type tension stiffening and damage typed as DISPLACEMENT (numbers taken as metres)         -> FAIL
  total_strain      total strains (cracking strain + s/E0) entered in the stiffening table                -> FAIL
  viscosity         viscosity parameter 0.2 with a step time of 1                                         -> FAIL
  unit_mpa          tension stiffening stresses entered in MPa (2.0, 1.2, ...) in a kPa model             -> FAIL
  lateral_fixed     lateral faces held: uniaxial strain instead of uniaxial stress                        -> FAIL
  no_unload         monotonic loading to 0.0008 without the unloading branch                              -> FAIL
  fake_report       correct analysis, unloading modulus reported as E0                                    -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'cdp'
KNOWN = ('default', 'cps4r', 'c3d8', 'one_step', 'series_5', 'no_damage', 'displacement_type', 'total_strain',
         'viscosity', 'unit_mpa', 'lateral_fixed', 'no_unload', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

A = 0.1                                   # edge of the cube
E0, NU = 3.0e7, 0.2
STIFF = [(2000.0, 0.0), (1200.0, 0.0002), (600.0, 0.0005), (200.0, 0.001)]        # (stress, cracking strain)
DAMAGE = [(0.0, 0.0), (0.3, 0.0002), (0.6, 0.0005), (0.85, 0.001)]                # (dt, cracking strain)
STRAINS = (0.0004, 0.00036, 0.0008)
two_d = VARIANT == 'cps4r'
tol = 1.0e-6

if VARIANT == 'total_strain':
    STIFF = [(s_, e_ + s_ / E0 if e_ > 0.0 else 0.0) for s_, e_ in STIFF]
if VARIANT == 'unit_mpa':
    STIFF = [(s_ / 1000.0, e_) for s_, e_ in STIFF]

m = mdb.Model(name='CDP')
mat = m.Material(name='Concrete')
mat.Elastic(table=((E0, NU),))
mat.ConcreteDamagedPlasticity(table=((36.0, 0.1, 1.16, 0.6667, 0.2 if VARIANT == 'viscosity' else 0.0),))
cdp = mat.concreteDamagedPlasticity
cdp.ConcreteCompressionHardening(table=((21000.0, 0.0), (30000.0, 0.0010), (27000.0, 0.0020), (15000.0, 0.0050)))
kind = DISPLACEMENT if VARIANT == 'displacement_type' else STRAIN
cdp.ConcreteTensionStiffening(table=tuple(STIFF), type=kind)
if VARIANT != 'no_damage':
    cdp.ConcreteTensionDamage(table=tuple(DAMAGE), type=kind)

s = m.ConstrainedSketch(name='sq', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(A, A))
n_x = 5 if VARIANT == 'series_5' else 1
if two_d:
    p = m.Part(name='Cube', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Concrete', thickness=A)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD),))
else:
    p = m.Part(name='Cube', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=A)
    m.HomogeneousSolidSection(name='Sec', material='Concrete')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    code = C3D8 if VARIANT == 'c3d8' else C3D8R
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=STANDARD),))
for e in p.edges:
    v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    along_x = abs(v1[0] - v0[0]) > 0.5 * A
    p.seedEdgeByNumber(edges=(e,), number=n_x if along_x else 1, constraint=FIXED)
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Cube-1', part=p, dependent=ON)
a.Set(name='X0', nodes=inst.nodes.getByBoundingBox(xMax=tol))
a.Set(name='X1', nodes=inst.nodes.getByBoundingBox(xMin=A - tol))
a.Set(name='Y0', nodes=inst.nodes.getByBoundingBox(yMax=tol))
a.Set(name='Y1', nodes=inst.nodes.getByBoundingBox(yMin=A - tol))
a.Set(name='ALLEL', elements=inst.elements)
if not two_d:
    a.Set(name='Z0', nodes=inst.nodes.getByBoundingBox(zMax=tol))
    a.Set(name='Z1', nodes=inst.nodes.getByBoundingBox(zMin=A - tol))

u = [e_ * A for e_ in STRAINS]
if VARIANT == 'one_step':
    m.StaticStep(name='Test', previous='Initial', timePeriod=3.0, initialInc=0.02, maxInc=0.02, minInc=1.0e-8,
                 maxNumInc=2000)
    m.TabularAmplitude(name='History', timeSpan=STEP, data=((0.0, 0.0), (1.0, u[0]), (2.0, u[1]), (3.0, u[2])))
    steps = ['Test']
elif VARIANT == 'no_unload':
    m.StaticStep(name='Load', previous='Initial', initialInc=0.0125, maxInc=0.0125, minInc=1.0e-8, maxNumInc=2000)
    steps = ['Load']
else:
    # strain increment per frame: 1e-5 as the task demands; REF_STRAIN_STEP overrides it for convergence studies
    de = float(os.environ.get('REF_STRAIN_STEP', '') or 1.0e-5)
    i1, i2, i3 = de / STRAINS[0], de / (STRAINS[0] - STRAINS[1]), de * 0.88 / (STRAINS[2] - STRAINS[1])
    tp = float(os.environ.get('REF_STEP_TIME', '') or 1.0)          # step time period (study of its influence)
    m.StaticStep(name='Load1', previous='Initial', timePeriod=tp, initialInc=i1 * tp, maxInc=i1 * tp,
                 minInc=1.0e-9 * tp, maxNumInc=100000)
    m.StaticStep(name='Unload', previous='Load1', timePeriod=tp, initialInc=i2 * tp, maxInc=i2 * tp,
                 minInc=1.0e-9 * tp, maxNumInc=100000)
    m.StaticStep(name='Load2', previous='Unload', timePeriod=tp, initialInc=i3 * tp, maxInc=i3 * tp,
                 minInc=1.0e-9 * tp, maxNumInc=100000)
    steps = ['Load1', 'Unload', 'Load2']
variables = ('S', 'E', 'PE', 'PEEQT', 'U', 'RF') if VARIANT == 'no_damage' else ('S', 'E', 'PE', 'PEEQT', 'DAMAGET',
                                                                                'SDEG', 'U', 'RF')
m.fieldOutputRequests['F-Output-1'].setValues(variables=variables, frequency=1)

m.DisplacementBC(name='FixX', createStepName='Initial', region=a.sets['X0'], u1=0.0)
m.DisplacementBC(name='FixY', createStepName='Initial', region=a.sets['Y0'], u2=0.0)
if not two_d:
    m.DisplacementBC(name='FixZ', createStepName='Initial', region=a.sets['Z0'], u3=0.0)
if VARIANT == 'lateral_fixed':
    m.DisplacementBC(name='HoldY', createStepName='Initial', region=a.sets['Y1'], u2=0.0)
    m.DisplacementBC(name='HoldZ', createStepName='Initial', region=a.sets['Z1'], u3=0.0)
if VARIANT == 'one_step':
    m.DisplacementBC(name='Pull', createStepName='Test', region=a.sets['X1'], u1=1.0, amplitude='History')
elif VARIANT == 'no_unload':
    m.DisplacementBC(name='Pull', createStepName='Load', region=a.sets['X1'], u1=u[2])
else:
    m.DisplacementBC(name='Pull', createStepName='Load1', region=a.sets['X1'], u1=u[0])
    m.boundaryConditions['Pull'].setValuesInStep(stepName='Unload', u1=u[1])
    m.boundaryConditions['Pull'].setValuesInStep(stepName='Load2', u1=u[2])

job = mdb.Job(name=JOB, model='CDP', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: stress - strain path of the element (largest damage of the model for dt) ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
x1 = odb.rootAssembly.nodeSets['X1']
eps, sig, dam = [], [], []
for name in steps:
    for fr in odb.steps[name].frames:
        eps.append(float(fr.fieldOutputs['U'].getSubset(region=x1).values[0].data[0]) / A)
        vals = fr.fieldOutputs['S'].values
        sig.append(math.fsum([float(v.data[0]) for v in vals]) / len(vals))
        dam.append(max([float(v.data) for v in fr.fieldOutputs['DAMAGET'].values])
                   if 'DAMAGET' in fr.fieldOutputs.keys() else 0.0)
odb.close()
i_a = len(eps) - 1
for i in range(len(eps) - 1):
    if eps[i + 1] < eps[i] - 1.0e-9:
        i_a = i
        break
i_b = len(eps) - 1
for i in range(i_a + 1, len(eps) - 1):
    if eps[i + 1] > eps[i] + 1.0e-9:
        i_b = i
        break
modulus = (sig[i_a] - sig[i_b]) / (eps[i_a] - eps[i_b]) if eps[i_a] != eps[i_b] else 0.0
sys.__stdout__.write('d04 %s: peak %.2f | A: e = %.6e s = %.3f d = %.5f | B: e = %.6e s = %.3f | unloading modulus %.5e'
                     ' | end: e = %.6e s = %.3f d = %.5f | frames %d\n' % (
                         VARIANT, max(sig), eps[i_a], sig[i_a], dam[i_a], eps[i_b], sig[i_b], modulus,
                         eps[-1], sig[-1], dam[-1], len(eps)))

result = {'peak_stress_kPa': float(max(sig)), 'stress_at_0p0004_kPa': float(sig[i_a]),
          'unloading_modulus_kPa': float(modulus), 'damage_at_end': float(dam[-1])}
if VARIANT == 'fake_report':
    result['unloading_modulus_kPa'] = E0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
