# -*- coding: utf-8 -*-
"""Reference solution for h01_fixed_beam_udl  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Beam fixed at both ends, L = 4 m (along X), rectangular section 0.1 m wide x 0.2 m high (height along Y),
E = 3.0e10 Pa, nu = 0.2, uniform load q = 1.0e4 N/m in -Y, beam elements.
Closed form: mid-span deflection 3.3333e-3 m (Euler) / 3.43e-3 m (with shear), total reaction 4.0e4 N,
end moment q L^2 / 12 = 1.3333e4 N m.

REF_VARIANT (environment variable):
  default        20 B32 elements (shear flexible, quadratic)                               -> must PASS
  b31            40 B31 elements                                                            -> must PASS
  b33            10 B33 elements (Euler-Bernoulli)                                          -> must PASS
  pinned         both ends pinned instead of fixed (rotations free)                        -> must FAIL
  weak_axis      section turned by 90 degrees: the load acts along the 0.1 m side          -> must FAIL
  coarse_b31     4 B31 elements (lumped loads: fixing moment 6 % low)                      -> must FAIL
  solid          C3D20R solid model with the same loading (task demands beam elements)     -> must FAIL
  fake_report    correct model, result.json gives the reaction of ONE support as the total -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'fixedbeam'
L, B, H = 4.0, 0.1, 0.2
E, NU = 3.0e10, 0.2
Q = 1.0e4
CFG = {
    'default': (B32, 20), 'b31': (B31, 40), 'b33': (B33, 10), 'pinned': (B32, 20), 'weak_axis': (B32, 20),
    'coarse_b31': (B31, 4), 'solid': (C3D20R, 0), 'fake_report': (B32, 20),
}
if VARIANT not in CFG:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
elem_code, n_el = CFG[VARIANT]
tol = 1.0e-6

m = mdb.Model(name='FixedBeam')
mat = m.Material(name='Concrete')
mat.Elastic(table=((E, NU),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)

if VARIANT == 'solid':
    s = m.ConstrainedSketch(name='side', sheetSize=10.0)
    s.rectangle(point1=(0.0, 0.0), point2=(L, H))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=B)
    m.HomogeneousSolidSection(name='Sec', material='Concrete')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.seedPart(size=0.05, deviationFactor=0.1, minSizeFactor=0.1)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D20R, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='END_A', faces=inst.faces.getByBoundingBox(xMax=tol))
    a.Set(name='END_B', faces=inst.faces.getByBoundingBox(xMin=L - tol))
    a.Surface(name='TOPFACE', side1Faces=inst.faces.getByBoundingBox(yMin=H - tol))
    m.Pressure(name='UDL', createStepName='Load', region=a.surfaces['TOPFACE'], magnitude=Q / B)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))
else:
    s = m.ConstrainedSketch(name='axis', sheetSize=10.0)
    s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.RectangularProfile(name='Rect', a=B, b=H)          # a along local 1, b along local 2
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Concrete', poissonRatio=NU)
    region = regionToolset.Region(edges=p.edges)
    p.SectionAssignment(region=region, sectionName='Sec')
    # beam axis = X; n1 = -Z makes local 2 = +Y, i.e. the 0.2 m height points along the load
    n1 = (0.0, 1.0, 0.0) if VARIANT == 'weak_axis' else (0.0, 0.0, -1.0)
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=n1)
    p.seedEdgeByNumber(edges=p.edges, number=n_el, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='END_A', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    a.Set(name='END_B', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))
    a.Set(name='SPAN', edges=inst.edges)
    m.LineLoad(name='UDL', createStepName='Load', region=a.sets['SPAN'], comp2=-Q)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'UR', 'RF', 'RM', 'SF'))

if VARIANT == 'pinned':
    # translations held at both ends, twist held at one end, bending rotations free
    m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0, u3=0.0, ur1=0.0)
    m.DisplacementBC(name='PinB', createStepName='Initial', region=a.sets['END_B'], u1=0.0, u2=0.0, u3=0.0)
else:
    m.EncastreBC(name='FixA', createStepName='Initial', region=a.sets['END_A'])
    m.EncastreBC(name='FixB', createStepName='Initial', region=a.sets['END_B'])

job = mdb.Job(name=JOB, model='FixedBeam', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Load'].frames[-1]
ns = odb.rootAssembly.nodeSets
max_defl = max(abs(v.data[1]) for v in frame.fieldOutputs['U'].values)
rf_a = [v for v in frame.fieldOutputs['RF'].getSubset(region=ns['END_A']).values]
rf_b = [v for v in frame.fieldOutputs['RF'].getSubset(region=ns['END_B']).values]
total_reaction = abs(math.fsum(v.data[1] for v in rf_a + rf_b))
if VARIANT == 'solid':
    # fixing moment of end A about the section mid-height from the nodal reactions: M = sum(RF_x * (y - H/2))
    inst_o = odb.rootAssembly.instances['BEAM-1']
    end_moment = abs(math.fsum(v.data[0] * (inst_o.getNodeFromLabel(v.nodeLabel).coordinates[1] - H / 2.0)
                               for v in rf_a))
else:
    rm = frame.fieldOutputs['RM'].getSubset(region=ns['END_A']).values[0].data
    end_moment = max(abs(c) for c in rm)
odb.close()

result = {'max_deflection_m': float(max_defl), 'total_reaction_N': float(total_reaction),
          'end_moment_Nm': float(end_moment)}
if VARIANT == 'fake_report':
    result['total_reaction_N'] = float(total_reaction) / 2.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
