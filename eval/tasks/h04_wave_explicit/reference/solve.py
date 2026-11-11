# -*- coding: utf-8 -*-
"""Reference solution for h04_wave_explicit  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Elastic bar 0 <= x <= 1 m, 0 <= y, z <= 0.01 m, E = 2.0e11 Pa, rho = 8000 kg/m3, nu = 0 (c = 5000 m/s).  At t = 0
the face x = 0 suddenly moves with a constant velocity of 1 m/s in +x, the other end is free; Abaqus/Explicit,
1.5e-4 s.  Closed form: compressive stress -rho c v = -4.0e7 Pa behind the front, front at x = 0.75 m at the end,
arrival at mid-length after 1.0e-4 s.

REF_VARIANT (environment variable):
  default         C3D8R, 200 x 2 x 2, velocity applied as a step, 100 output frames           -> must PASS
  truss           200 T3D2 elements on the bar axis                                           -> must PASS
  few_frames      default model with the Explicit default of 20 output frames                 -> must PASS
  ramp_velocity   velocity ramped linearly from 0 to 1 m/s over the step (not sudden)         -> must FAIL
  implicit        same problem solved with Abaqus/Standard implicit dynamics                  -> must FAIL
  wrong_duration  analysis time 1.5e-3 s instead of 1.5e-4 s                                  -> must FAIL
  pull            velocity applied in -x (the bar is pulled, tensile wave)                    -> must FAIL
  fake_report     correct model, stress written to result.json in MPa                         -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'wave'
L, W = 1.0, 0.01
E, NU, RHO = 2.0e11, 0.0, 8000.0
V0 = 1.0
T_END = 1.5e-4
N_X = 200
KNOWN = ('default', 'truss', 'few_frames', 'ramp_velocity', 'implicit', 'wrong_duration', 'pull', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
t_end = 1.5e-3 if VARIANT == 'wrong_duration' else T_END
implicit = VARIANT == 'implicit'
lib = STANDARD if implicit else EXPLICIT
tol = 1.0e-6

m = mdb.Model(name='Wave')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)

if VARIANT == 'truss':
    s = m.ConstrainedSketch(name='axis', sheetSize=2.0)
    s.Line(point1=(0.0, W / 2.0), point2=(L, W / 2.0))
    p = m.Part(name='Bar', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.TrussSection(name='Sec', material='Steel', area=W * W)
    p.SectionAssignment(region=regionToolset.Region(edges=p.edges), sectionName='Sec')
    p.seedEdgeByNumber(edges=p.edges, number=N_X, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=T3D2, elemLibrary=lib),))
    p.generateMesh()
    inst = a.Instance(name='Bar-1', part=p, dependent=ON)
    a.translate(instanceList=('Bar-1',), vector=(0.0, 0.0, W / 2.0))       # onto the bar axis y = z = 0.005
    a.Set(name='X0', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    a.Set(name='ALL', edges=inst.edges)
else:
    s = m.ConstrainedSketch(name='side', sheetSize=2.0)
    s.rectangle(point1=(0.0, 0.0), point2=(L, W))
    p = m.Part(name='Bar', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=W)
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        along_x = abs(v1[0] - v0[0]) > tol
        p.seedEdgeByNumber(edges=(e,), number=N_X if along_x else 2, constraint=FIXED)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=lib),))
    p.generateMesh()
    inst = a.Instance(name='Bar-1', part=p, dependent=ON)
    a.Set(name='X0', faces=inst.faces.getByBoundingBox(xMax=tol))
    a.Set(name='ALL', cells=inst.cells)

if implicit:
    m.ImplicitDynamicsStep(name='Wave', previous='Initial', timePeriod=t_end, nlgeom=OFF,
                           timeIncrementationMethod=FIXED, initialInc=T_END / 300.0, maxNumInc=100000, nohaf=ON)
else:
    m.ExplicitDynamicsStep(name='Wave', previous='Initial', timePeriod=t_end, nlgeom=OFF)

if VARIANT == 'ramp_velocity':
    m.TabularAmplitude(name='Amp', timeSpan=STEP, data=((0.0, 0.0), (t_end, 1.0)))
else:
    m.TabularAmplitude(name='Amp', timeSpan=STEP, data=((0.0, 1.0), (t_end, 1.0)))      # sudden, then constant
v1 = -V0 if VARIANT == 'pull' else V0
m.VelocityBC(name='Drive', createStepName='Wave', region=a.sets['X0'], v1=v1, amplitude='Amp')
if VARIANT == 'truss':
    m.DisplacementBC(name='Line', createStepName='Initial', region=a.sets['ALL'], u2=0.0, u3=0.0)
n_frames = 20 if VARIANT == 'few_frames' else 100
if implicit:
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'V', 'A'), frequency=3)
else:
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'V', 'A'), numIntervals=n_frames)

job = mdb.Job(name=JOB, model='Wave', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: axial stress histories of the elements at x = 0.25, 0.50 m ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
step = odb.steps['Wave']
inst_o = odb.rootAssembly.instances['BAR-1']
xyz = dict((n.label, n.coordinates) for n in inst_o.nodes)
cx = {}
for el in inst_o.elements:
    cx[el.label] = math.fsum(xyz[n][0] for n in el.connectivity) / len(el.connectivity)


def nearest_elements(x):
    d = min(abs(c - x) for c in cx.values())
    return set(lab for lab, c in cx.items() if abs(abs(c - x) - d) < 1.0e-9)


def history(x):
    labs = nearest_elements(x)
    out = []
    for fr in step.frames:
        vals = [v.data[0] for v in fr.fieldOutputs['S'].values if v.elementLabel in labs]
        out.append((fr.frameValue, math.fsum(vals) / len(vals)))
    return out


def arrival(hist, level):
    prev = None
    for t, sv in hist:
        if abs(sv) >= level:
            if prev is None:
                return t
            return prev[0] + (level - abs(prev[1])) * (t - prev[0]) / (abs(sv) - abs(prev[1]))
        prev = (t, sv)
    return None


last = step.frames[-1]
behind = [v.data[0] for v in last.fieldOutputs['S'].values if 0.10 <= cx[v.elementLabel] <= 0.50]
stress = math.fsum(behind) / len(behind)                 # plateau behind the front, compression negative
h25, h50 = history(0.25), history(0.50)
t25, t50 = arrival(h25, 0.5 * abs(stress)), arrival(h50, 0.5 * abs(stress))
odb.close()
speed = 0.25 / (t50 - t25) if (t25 is not None and t50 is not None and t50 > t25) else 0.0

result = {'wave_speed_m_s': float(speed), 'stress_behind_front_Pa': float(stress),
          'arrival_time_at_mid_s': float(t50) if t50 is not None else 0.0}
if VARIANT == 'fake_report':
    result['stress_behind_front_Pa'] = float(stress) / 1.0e6
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
