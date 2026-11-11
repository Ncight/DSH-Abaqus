# -*- coding: utf-8 -*-
"""Reference solution for x03_cable_prestress_modes  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Steel wire, L = 10 m, diameter 10 mm, E = 2.0e11 Pa, rho = 7850 kg/m3, both ends pinned, pretension 20 kN.
Taut-string frequencies f_n = n/(2L) sqrt(T/(rho A)) = 9.005, 18.01, 27.02 Hz (bending stiffness adds 0.02 - 0.2 %).

The transverse stiffness of the wire is almost entirely geometric (stress) stiffness.  It only enters the frequency
extraction when the pretension is applied in a preceding general step WITH geometric nonlinearity (NLGEOM); the
*FREQUENCY step is then a perturbation about that stressed state.

REF_VARIANT (environment variable):
  default               3-D beam model, 100 B31, tension applied as a force at a sliding end, NLGEOM       -> must PASS
  planar_b21            2-D beam model, 100 B21 (no duplicate modes)                                        -> must PASS
  truss_initial_stress  100 T3D2 truss elements, pretension as initial stress, both ends pinned             -> must PASS
  no_nlgeom             pretension step without NLGEOM: no stress stiffening, bending frequencies 0.2 Hz    -> must FAIL
  no_prestress          frequency step on the unstressed wire                                               -> must FAIL
  wrong_tension         80 kN (stress x pi d^2 instead of pi d^2 / 4)                                       -> must FAIL
  wrong_density         density entered as 7.85                                                             -> must FAIL
  coarse                only 6 B31 elements                                                                 -> must FAIL
  fake_report           correct model, result.json lists the duplicate pair 9.0 / 9.0 / 18.0                -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'cable'
KNOWN = ('default', 'planar_b21', 'truss_initial_stress', 'no_nlgeom', 'no_prestress', 'wrong_tension',
         'wrong_density', 'coarse', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, DIA = 10.0, 0.010
E, NU, RHO = 2.0e11, 0.3, 7850.0
AREA = math.pi * DIA ** 2 / 4.0
TENSION = 80000.0 if VARIANT == 'wrong_tension' else 20000.0
if VARIANT == 'wrong_density':
    RHO = 7.85
N_EL = 6 if VARIANT == 'coarse' else 100
planar = VARIANT == 'planar_b21'
truss = VARIANT == 'truss_initial_stress'
prestress = VARIANT != 'no_prestress'
tol = 1.0e-6

m = mdb.Model(name='Cable')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
s = m.ConstrainedSketch(name='axis', sheetSize=30.0)
s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
p = m.Part(name='Wire', dimensionality=TWO_D_PLANAR if planar else THREE_D, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
region = regionToolset.Region(edges=p.edges)
if truss:
    m.TrussSection(name='Sec', material='Steel', area=AREA)
    p.SectionAssignment(region=region, sectionName='Sec')
    elem_code = T3D2
else:
    m.CircularProfile(name='Circ', r=DIA / 2.0)
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Circ', material='Steel', poissonRatio=NU)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    elem_code = B21 if planar else B31
p.seedEdgeByNumber(edges=p.edges, number=N_EL, constraint=FIXED)
p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Wire-1', part=p, dependent=ON)
a.Set(name='END_A', vertices=inst.vertices.getByBoundingBox(xMax=tol))
a.Set(name='END_B', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))
a.Set(name='ALL', edges=inst.edges)

previous = 'Initial'
if prestress:
    m.StaticStep(name='Pretension', previous='Initial', nlgeom=OFF if VARIANT == 'no_nlgeom' else ON)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CF'))
    previous = 'Pretension'
m.FrequencyStep(name='Freq', previous=previous, eigensolver=LANCZOS, numEigen=6 if planar else 10)

if truss:
    # both ends pinned; the tension is an initial stress that the first step brings into equilibrium
    m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0, u3=0.0)
    m.DisplacementBC(name='PinB', createStepName='Initial', region=a.sets['END_B'], u1=0.0, u2=0.0, u3=0.0)
    m.Stress(name='Pretension', region=a.sets['ALL'], distributionType=UNIFORM, sigma11=TENSION / AREA)
elif planar:
    m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0)
    m.DisplacementBC(name='SlideB', createStepName='Initial', region=a.sets['END_B'], u2=0.0)
else:
    # pinned at A (the twist is held there as well), B slides along the wire and carries the tension
    m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0, u3=0.0, ur1=0.0)
    m.DisplacementBC(name='SlideB', createStepName='Initial', region=a.sets['END_B'], u2=0.0, u3=0.0)
if prestress and not truss:
    m.ConcentratedForce(name='Tension', createStepName='Pretension', region=a.sets['END_B'], cf1=TENSION)

job = mdb.Job(name=JOB, model='Cable', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
tension = 0.0
if prestress:
    vals = odb.steps['Pretension'].frames[-1].fieldOutputs['S'].values
    tension = math.fsum(v.data[0] for v in vals) / len(vals) * AREA        # axial stress x area
freqs = sorted(float(fr.frequency) for fr in odb.steps['Freq'].frames if fr.mode > 0)
odb.close()
distinct = []
for fq in freqs:                           # the two transverse planes of the 3-D model give every frequency twice
    if fq > 0.05 and not (distinct and abs(fq - distinct[-1]) <= 0.005 * distinct[-1]):
        distinct.append(fq)

result = {'frequencies_Hz': distinct[:3], 'tension_N': float(tension)}
if VARIANT == 'fake_report':
    result['frequencies_Hz'] = [freqs[0], freqs[1], freqs[2]]
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
