# -*- coding: utf-8 -*-
"""Reference solution for x05_damped_free_vibration  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Steel cantilever along +x from the origin, L = 1.0 m, 0.02 x 0.02 m, E = 2.1e11 Pa, rho = 7850 kg/m3.
A static tip force of 10 N in +y (deflection P L^3 / 3EI = 1.1905e-3 m) is removed suddenly; the free vibration is
integrated for 0.6 s.  Rayleigh damping is to give 2 % of critical damping in the first mode:
    f1 = 16.71 Hz, w1 = 104.99 rad/s  ->  alpha = 2 zeta w1 = 4.200 1/s  (or beta = 2 zeta / w1 = 3.810e-4 s)
    logarithmic decrement 0.1257, i.e. the amplitude falls to 28 % in the ten periods of 0.6 s.

REF_VARIANT (environment variable):
  default           40 B31, alpha only, implicit dynamics (transient fidelity), dt = 5e-4, U every 0.002 s   -> PASS
  beta_only         stiffness-proportional damping only                                                    -> PASS
  two_coeff         alpha and beta chosen for 2 % in modes 1 and 2                                         -> PASS
  plane_stress      2-D continuum model, CPS4I 50 x 4, tip load as a shear traction                        -> PASS
  no_damping        no damping at all                                                                      -> FAIL
  alpha_hz          alpha = 2 zeta f1 with f1 in Hz instead of rad/s (2 pi too small: zeta = 0.32 %)       -> FAIL
  beta_hz           beta = 2 zeta / f1 with f1 in Hz (2 pi too large: zeta = 12.6 %)                       -> FAIL
  general_section   damping defined in the material, beam section integrated BEFORE the analysis
                    (*BEAM GENERAL SECTION does not take the damping of the material)                      -> FAIL
  quasi_static      correct alpha, but APPLICATION=QUASI-STATIC (backward Euler: heavy numerical damping)  -> FAIL
  coarse_dt         correct alpha, dt = 6e-3 (a tenth of the period): period elongation, sparse output     -> FAIL
  load_not_removed  the tip force stays on in the dynamic step                                             -> FAIL
  ramp_removed      the tip force is ramped down over the dynamic step instead of being removed at once    -> FAIL
  sparse_output     correct analysis, U written every 0.01 s only                                          -> FAIL
  short_run         correct analysis, only 0.3 s of free vibration                                         -> FAIL
  fake_report       correct analysis, result.json gives the logarithmic decrement as the damping ratio     -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'decay'
KNOWN = ('default', 'beta_only', 'two_coeff', 'plane_stress', 'no_damping', 'alpha_hz', 'beta_hz', 'general_section',
         'quasi_static', 'coarse_dt', 'load_not_removed', 'ramp_removed', 'sparse_output', 'short_run', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, B = 1.0, 0.02
E, NU, RHO = 2.1e11, 0.3, 7850.0
P = 10.0
ZETA = 0.02
INERTIA, AREA = B ** 4 / 12.0, B * B
W1 = 1.8751040687 ** 2 * math.sqrt(E * INERTIA / (RHO * AREA * L ** 4))       # rad/s
W2 = 4.6940911330 ** 2 * math.sqrt(E * INERTIA / (RHO * AREA * L ** 4))
F1 = W1 / (2.0 * math.pi)
ALPHA, BETA = 2.0 * ZETA * W1, 0.0
if VARIANT == 'beta_only':
    ALPHA, BETA = 0.0, 2.0 * ZETA / W1
elif VARIANT == 'two_coeff':
    ALPHA, BETA = 2.0 * ZETA * W1 * W2 / (W1 + W2), 2.0 * ZETA / (W1 + W2)
elif VARIANT == 'no_damping':
    ALPHA, BETA = 0.0, 0.0
elif VARIANT == 'alpha_hz':
    ALPHA, BETA = 2.0 * ZETA * F1, 0.0
elif VARIANT == 'beta_hz':
    ALPHA, BETA = 0.0, 2.0 * ZETA / F1
T_FREE = 0.3 if VARIANT == 'short_run' else 0.6
DT = 6.0e-3 if VARIANT == 'coarse_dt' else 5.0e-4
OUT_EVERY = {'coarse_dt': 1, 'sparse_output': 20}.get(VARIANT, 4)
continuum = VARIANT == 'plane_stress'
tol = 1.0e-6

m = mdb.Model(name='Decay')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
if ALPHA or BETA:
    mat.Damping(alpha=ALPHA, beta=BETA)
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)

if continuum:
    s = m.ConstrainedSketch(name='sk', sheetSize=2.0)
    s.rectangle(point1=(0.0, -B / 2), point2=(L, B / 2))
    p = m.Part(name='Beam', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    p.PartitionFaceByDatumPlane(faces=p.faces[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=XZPLANE, offset=0.0).id])
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=B)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        p.seedEdgeByNumber(edges=(e,), number=50 if abs(v1[0] - v0[0]) > abs(v1[1] - v0[1]) else 2, constraint=FIXED)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPS4I, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='ROOT', edges=inst.edges.getByBoundingBox(xMax=tol))
    a.Set(name='TIP', vertices=inst.vertices.findAt(((L, 0.0, 0.0),)))
    a.Surface(name='TIPFACE', side1Edges=inst.edges.getByBoundingBox(xMin=L - tol))
else:
    s = m.ConstrainedSketch(name='axis', sheetSize=2.0)
    s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    region = regionToolset.Region(edges=p.edges)
    m.RectangularProfile(name='Square', a=B, b=B)
    if VARIANT == 'general_section':
        # *BEAM GENERAL SECTION carries its own E, G and density and does not look at the material: the
        # *DAMPING of the material is silently ignored (the section's own alphaDamping / betaDamping are zero)
        m.BeamSection(name='Sec', integration=BEFORE_ANALYSIS, profile='Square', material='Steel', poissonRatio=NU,
                      table=((E, E / (2.0 * (1.0 + NU))),), density=RHO)
    else:
        m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Square', material='Steel', poissonRatio=NU)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    p.seedEdgeByNumber(edges=p.edges, number=40, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B31, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='ROOT', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    a.Set(name='TIP', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))

m.StaticStep(name='Load', previous='Initial')
m.ImplicitDynamicsStep(name='Free', previous='Load', timePeriod=T_FREE,
                       application=QUASI_STATIC if VARIANT == 'quasi_static' else TRANSIENT_FIDELITY,
                       amplitude=RAMP if VARIANT == 'ramp_removed' else STEP,
                       timeIncrementationMethod=FIXED, initialInc=DT, maxNumInc=int(T_FREE / DT) + 100, nlgeom=OFF)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('U', 'RF'))
m.fieldOutputRequests['F-Output-1'].setValuesInStep(stepName='Free', frequency=OUT_EVERY)
m.EncastreBC(name='Clamp', createStepName='Initial', region=a.sets['ROOT'])
if continuum:
    m.SurfaceTraction(name='Tip', createStepName='Load', region=a.surfaces['TIPFACE'], magnitude=P / (B * B),
                      directionVector=((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)), distributionType=UNIFORM,
                      traction=GENERAL, follower=OFF)
else:
    m.ConcentratedForce(name='Tip', createStepName='Load', region=a.sets['TIP'], cf2=P)
if VARIANT != 'load_not_removed':
    m.loads['Tip'].deactivate('Free')

job = mdb.Job(name=JOB, model='Decay', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: tip displacement history of the free vibration -> frequency and damping ratio ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
tip = odb.rootAssembly.nodeSets['TIP']
ts, us = [], []
for fr in odb.steps['Free'].frames:
    ts.append(float(fr.frameValue))
    us.append(float(fr.fieldOutputs['U'].getSubset(region=tip).values[0].data[1]))
odb.close()


def analyse(ts, us):
    """frequency from the zero crossings, damping ratio from a straight line through ln|peak| of the half cycles"""
    y = [u / us[0] for u in us]
    cross = []
    for i in range(len(y) - 1):
        if y[i] * y[i + 1] < 0.0:
            cross.append((ts[i] + (ts[i + 1] - ts[i]) * y[i] / (y[i] - y[i + 1]), i))
    if len(cross) < 4:
        return 0.0, 0.0
    wd = math.pi * (len(cross) - 1) / (cross[-1][0] - cross[0][0])
    xs, ys = [], []
    for (t_a, i_a), (t_b, i_b) in zip(cross[:-1], cross[1:]):
        j = max(range(i_a + 1, i_b + 1), key=lambda k: abs(y[k]))
        xs.append(ts[j])
        ys.append(math.log(abs(y[j])))
    xm, ym = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    rate = -math.fsum([(x - xm) * (v - ym) for x, v in zip(xs, ys)]) / math.fsum([(x - xm) ** 2 for x in xs])
    return wd / (2.0 * math.pi), rate / math.sqrt(rate ** 2 + wd ** 2)


freq, zeta = analyse(ts, us)
sys.__stdout__.write('x05 %s: static %.6e  f = %.4f Hz  zeta = %.5f  alpha = %.5g  beta = %.5g  frames = %d\n' % (
    VARIANT, us[0], freq, zeta, ALPHA, BETA, len(ts)))
result = {'first_frequency_Hz': float(freq), 'damping_ratio': float(zeta),
          'rayleigh_alpha': float(ALPHA), 'rayleigh_beta': float(BETA)}
if VARIANT == 'fake_report':
    result['damping_ratio'] = 2.0 * math.pi * zeta / math.sqrt(1.0 - zeta ** 2)
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
