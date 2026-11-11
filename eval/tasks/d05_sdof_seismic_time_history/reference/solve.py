# -*- coding: utf-8 -*-
"""Reference solution for d05_sdof_seismic_time_history  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Shear frame: two fixed-base columns 0.2 x 0.2 m, 4 m high, E = 3.0e7 kPa, 6 m apart,
rigid beam, lumped mass 12.5 t, 5 % damping.   k = 2 x 12 E I / h^3 = 1500 kN/m,  T = 0.5736 s.
Base excitation: the record `ground_motion.at2` in the work directory (PEER layout: 4 header lines, 1500 values in g
at dt = 0.01 s, five per line), applied in x.
Exact SDOF response to the piecewise-linear record (reference/make_record.py):
    peak relative roof displacement 0.029150 m,  peak absolute roof acceleration 3.5193 m/s2

REF_VARIANT (environment variable):
  default            B23 columns, base acceleration (record x 9.8), stiffness-proportional damping on the
                     column material (acts on the relative motion), implicit dt = 5 ms, output 10 ms       -> PASS
  alpha_mass         mass-proportional damping on the point mass instead: with a moving base it acts on the
                     ABSOLUTE velocity, i.e. adds the force -alpha m v_ground(t); here +1.8 % displacement    -> PASS
  fixed_base_force   fixed base, inertia force -m a_g(t) on the mass (relative formulation)                -> PASS
  b21_columns        shear-flexible B21 columns (stiffness -0.7 %)                                         -> PASS
  record_as_ms2      record values used as m/s2 (not multiplied by g)                                      -> FAIL
  first_column_only  only the first value of every line of the file is read (300 points)                   -> FAIL
  dt_misread         record time step taken as 0.02 s                                                      -> FAIL
  alpha_on_columns   mass-proportional damping put on the (massless) column material: no damping at all    -> FAIL
  coarse_dt          time increment 50 ms                                                                  -> FAIL
  flexible_beam      beam with the same section as the columns                                             -> FAIL
  absolute_reported  correct analysis, absolute roof displacement reported as the relative one             -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'frame'
KNOWN = ('default', 'alpha_mass', 'fixed_base_force', 'b21_columns', 'record_as_ms2', 'first_column_only',
         'dt_misread', 'alpha_on_columns', 'coarse_dt', 'flexible_beam', 'absolute_reported')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

G = 9.8
H, SPAN, B = 4.0, 6.0, 0.2
E, NU = 3.0e7, 0.2
MASS, ZETA = 12.5, 0.05
K = 2.0 * 12.0 * E * (B ** 4 / 12.0) / H ** 3
OMEGA = math.sqrt(K / MASS)
ALPHA, BETA = 2.0 * ZETA * OMEGA, 2.0 * ZETA / OMEGA
T_END = 15.0
DT = 0.05 if VARIANT == 'coarse_dt' else 0.005
OUT_EVERY = 1 if VARIANT == 'coarse_dt' else 2
fixed_base = VARIANT == 'fixed_base_force'
tol = 1.0e-6

# ---- the record: 4 header lines, then accelerations in g, five per line ----
with open('ground_motion.at2') as f:
    lines = f.read().splitlines()
dt_rec = float(lines[3].upper().split('DT=')[1].split()[0])
if VARIANT == 'dt_misread':
    dt_rec = 0.02
acc_g = []
for line in lines[4:]:
    vals = [float(x) for x in line.split()]
    acc_g.extend(vals[:1] if VARIANT == 'first_column_only' else vals)

m = mdb.Model(name='Frame')
col = m.Material(name='Column')
col.Elastic(table=((E, NU),))
col.Density(table=((1.0e-6,),))                     # the members carry no mass worth mentioning
if VARIANT == 'alpha_on_columns':
    col.Damping(alpha=ALPHA)
elif VARIANT not in ('alpha_mass', 'fixed_base_force'):
    col.Damping(beta=BETA)
stiff = m.Material(name='RigidBeam')
stiff.Elastic(table=((E * (1.0 if VARIANT == 'flexible_beam' else 1.0e5), NU),))
stiff.Density(table=((1.0e-6,),))
m.RectangularProfile(name='Col', a=B, b=B)
m.RectangularProfile(name='Beam', a=B, b=B if VARIANT == 'flexible_beam' else 1.0)
m.BeamSection(name='ColSec', integration=DURING_ANALYSIS, profile='Col', material='Column', poissonRatio=NU)
m.BeamSection(name='BeamSec', integration=DURING_ANALYSIS, profile='Beam', material='RigidBeam', poissonRatio=NU)

s = m.ConstrainedSketch(name='frame', sheetSize=20.0)
s.Line(point1=(0.0, 0.0), point2=(0.0, H))
s.Line(point1=(0.0, H), point2=(SPAN, H))
s.Line(point1=(SPAN, H), point2=(SPAN, 0.0))
p = m.Part(name='Frame', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
beam_edges = p.edges.findAt(((SPAN / 2.0, H, 0.0),))
col_edges = p.edges.findAt(((0.0, H / 2.0, 0.0),), ((SPAN, H / 2.0, 0.0),))
p.SectionAssignment(region=regionToolset.Region(edges=col_edges), sectionName='ColSec')
p.SectionAssignment(region=regionToolset.Region(edges=beam_edges), sectionName='BeamSec')
p.assignBeamSectionOrientation(region=regionToolset.Region(edges=p.edges), method=N1_COSINES, n1=(0.0, 0.0, -1.0))
p.seedEdgeByNumber(edges=col_edges, number=8, constraint=FIXED)
p.seedEdgeByNumber(edges=beam_edges, number=6, constraint=FIXED)
p.setElementType(regions=(col_edges,), elemTypes=(mesh.ElemType(
    elemCode=B21 if VARIANT == 'b21_columns' else B23, elemLibrary=STANDARD),))
p.setElementType(regions=(beam_edges,), elemTypes=(mesh.ElemType(elemCode=B23, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Frame-1', part=p, dependent=ON)
a.Set(name='BASES', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),), ((SPAN, 0.0, 0.0),)))
a.Set(name='BASE', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),)))
a.Set(name='ROOF', vertices=inst.vertices.findAt(((0.0, H, 0.0),)))
a.engineeringFeatures.PointMassInertia(name='RoofMass', region=a.sets['ROOF'], mass=MASS,
                                       alpha=ALPHA if VARIANT in ('alpha_mass', 'fixed_base_force') else 0.0,
                                       composite=0.0)

m.ImplicitDynamicsStep(name='Dyn', previous='Initial', timePeriod=T_END, application=TRANSIENT_FIDELITY,
                       timeIncrementationMethod=FIXED, initialInc=DT, maxNumInc=int(T_END / DT) + 100, nlgeom=OFF)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('U', 'V', 'A', 'RF'), frequency=OUT_EVERY)
m.TabularAmplitude(name='Record', timeSpan=STEP, data=tuple((i * dt_rec, v) for i, v in enumerate(acc_g)))
scale = 1.0 if VARIANT == 'record_as_ms2' else G
if fixed_base:
    m.EncastreBC(name='Fixed', createStepName='Initial', region=a.sets['BASES'])
    m.ConcentratedForce(name='Inertia', createStepName='Dyn', region=a.sets['ROOF'], cf1=-MASS * scale,
                        amplitude='Record')
else:
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASES'], u2=0.0, ur3=0.0)
    m.AccelerationBC(name='Quake', createStepName='Dyn', region=a.sets['BASES'], a1=scale, amplitude='Record')

job = mdb.Job(name=JOB, model='Frame', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
roof, base = odb.rootAssembly.nodeSets['ROOF'], odb.rootAssembly.nodeSets['BASE']
peak_rel = peak_abs_u = peak_acc = 0.0
n_frames = 0
for fr in odb.steps['Dyn'].frames:
    t = float(fr.frameValue)
    u_r = float(fr.fieldOutputs['U'].getSubset(region=roof).values[0].data[0])
    u_b = float(fr.fieldOutputs['U'].getSubset(region=base).values[0].data[0])
    acc = float(fr.fieldOutputs['A'].getSubset(region=roof).values[0].data[0])
    if fixed_base:                                 # relative formulation: add the ground acceleration
        x = t / dt_rec
        i = int(x)
        g0 = acc_g[i] if i < len(acc_g) else 0.0
        g1 = acc_g[i + 1] if i + 1 < len(acc_g) else 0.0
        acc += scale * (g0 + (g1 - g0) * (x - i))
    peak_rel = max(peak_rel, abs(u_r - u_b))
    peak_abs_u = max(peak_abs_u, abs(u_r))
    peak_acc = max(peak_acc, abs(acc))
    n_frames += 1
odb.close()
sys.__stdout__.write('d05 %s: peak relative displacement %.6f m, peak absolute acceleration %.5f m/s2, peak absolute '
                     'displacement %.4f m, record points %d, frames %d\n' % (
                         VARIANT, peak_rel, peak_acc, peak_abs_u, len(acc_g), n_frames))

result = {'peak_relative_displacement_m': peak_rel, 'peak_absolute_acceleration_m_s2': peak_acc}
if VARIANT == 'absolute_reported':
    result['peak_relative_displacement_m'] = peak_abs_u
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
