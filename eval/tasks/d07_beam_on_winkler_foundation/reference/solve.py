# -*- coding: utf-8 -*-
"""Reference solution for d07_beam_on_winkler_foundation  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Beam on an elastic (Winkler) foundation: L = 40 m along x, section 1.2 m wide x 0.5 m
deep, E = 3.0e7 kPa (EI = 375 000 kNm2), modulus of subgrade reaction 20 000 kN/m3, point load 500 kN at midspan.
    foundation stiffness per unit length  k = 20 000 x 1.2 = 24 000 kN/m2
    lambda = (k / 4EI)^(1/4) = 0.35566 1/m,  lambda L = 14.2  (a "long" beam: the ends do not matter)
    Hetenyi:  deflection under the load  P lambda / (2k) = 3.7047e-3 m,  moment under the load  P / (4 lambda) = 351.46 kNm

REF_VARIANT (environment variable):
  default            160 B23 (0.25 m), a grounded spring k x tributary length at every node                -> PASS
  b21                400 shear-flexible B21 (0.1 m)                                                        -> PASS
  ip_reported        default model, the largest integration-point moment reported (2 % below the nodal
                     value with 0.25 m cubic elements)                                                     -> PASS
  mesh_1m            40 B23 (1 m), springs every 1 m; the moment Abaqus extrapolates to the load node
                     is reported (2 % below Hetenyi)                                                       -> PASS
  half_symmetry      half the beam (x = 20 .. 40 m) with symmetry at the load, P/2                         -> PASS
  foundation_keyword element foundation (*FOUNDATION on the beam elements) instead of springs              -> PASS
  ks_no_width        subgrade modulus used as stiffness per length (not multiplied by the 1.2 m width)     -> FAIL
  spring_no_spacing  nodal spring = stiffness per length (not multiplied by the 0.25 m node spacing)       -> FAIL
  short_beam         only 6 m of beam under the load                                                       -> FAIL
  coarse_mesh        20 B23 (2 m), springs every 2 m                                                       -> FAIL
  b21_ip_reported    160 B21 (0.25 m), the largest element moment reported: linear elements carry one
                     value per element, 8 % below the moment at the load point                             -> FAIL
  fake_report        correct analysis, deflection reported in mm                                           -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'winkler'
KNOWN = ('default', 'b21', 'ip_reported', 'mesh_1m', 'half_symmetry', 'foundation_keyword', 'ks_no_width',
         'spring_no_spacing', 'short_beam', 'coarse_mesh', 'b21_ip_reported', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, WIDTH, DEPTH = 40.0, 1.2, 0.5
E, NU = 3.0e7, 0.2
KS, P = 20000.0, 500.0
X_LOAD = L / 2.0
k_line = KS * (1.0 if VARIANT == 'ks_no_width' else WIDTH)           # foundation stiffness per unit length
h = {'b21': 0.1, 'coarse_mesh': 2.0, 'mesh_1m': 1.0}.get(VARIANT, 0.25)
linear = VARIANT in ('b21', 'b21_ip_reported')
half = VARIANT == 'half_symmetry'
x0, x1 = 0.0, L
if VARIANT == 'short_beam':
    x0, x1 = X_LOAD - 3.0, X_LOAD + 3.0
if half:
    x0 = X_LOAD
tol = 1.0e-6

m = mdb.Model(name='Winkler')
mat = m.Material(name='Concrete')
mat.Elastic(table=((E, NU),))
m.RectangularProfile(name='Rect', a=WIDTH, b=DEPTH)            # a = width (out of plane), b = depth (in plane)
m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Concrete', poissonRatio=NU)
s = m.ConstrainedSketch(name='axis', sheetSize=100.0)
if x0 < X_LOAD - tol:
    s.Line(point1=(x0, 0.0), point2=(X_LOAD, 0.0))
s.Line(point1=(X_LOAD, 0.0), point2=(x1, 0.0))
p = m.Part(name='Beam', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
region = regionToolset.Region(edges=p.edges)
p.SectionAssignment(region=region, sectionName='Sec')
p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
p.seedPart(size=h, deviationFactor=0.1, minSizeFactor=0.1)
p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B21 if linear else B23,
                                                              elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Beam-1', part=p, dependent=ON)
a.Set(name='LOAD', vertices=inst.vertices.getByBoundingBox(xMin=X_LOAD - tol, xMax=X_LOAD + tol))
a.Set(name='ALLEL', elements=inst.elements)
end_labels, inner_labels = [], []
for nd in inst.nodes:
    x = nd.coordinates[0]
    (end_labels if (abs(x - x0) < tol or abs(x - x1) < tol) else inner_labels).append(nd.label)
a.Set(name='INNER', nodes=inst.nodes.sequenceFromLabels(inner_labels))
a.Set(name='ENDS', nodes=inst.nodes.sequenceFromLabels(end_labels))
a.Set(name='END0', nodes=inst.nodes.getByBoundingBox(xMax=x0 + tol))

m.StaticStep(name='Load', previous='Initial')
# 'SF' = section forces AND moments: the ODB gets the fields SF and SM ('SM' itself is not a valid request key)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('U', 'RF', 'CF', 'SF', 'S'))
if VARIANT == 'foundation_keyword':
    pass                                              # the *FOUNDATION keyword is inserted below
else:
    k_node = k_line * (1.0 if VARIANT == 'spring_no_spacing' else h)
    a.engineeringFeatures.SpringDashpotToGround(name='Soil', region=a.sets['INNER'], orientation=None, dof=2,
                                                springBehavior=ON, springStiffness=k_node, dashpotBehavior=OFF)
    a.engineeringFeatures.SpringDashpotToGround(name='SoilEnds', region=a.sets['ENDS'], orientation=None, dof=2,
                                                springBehavior=ON, springStiffness=k_node / 2.0, dashpotBehavior=OFF)
# the beam has no axial support of its own
m.DisplacementBC(name='Axial', createStepName='Initial', region=a.sets['END0'], u1=0.0)
if half:
    m.DisplacementBC(name='Symmetry', createStepName='Initial', region=a.sets['LOAD'], ur3=0.0)
m.ConcentratedForce(name='P', createStepName='Load', region=a.sets['LOAD'], cf2=-(P / 2.0 if half else P))

if VARIANT == 'foundation_keyword':
    a.regenerate()
    kb = m.keywordBlock
    kb.synchVersions(storeNodesAndElements=False)
    idx = [i for i, blk in enumerate(kb.sieBlocks) if blk.strip().lower().startswith('*step')][0]
    # element foundation acting in the global y direction, stiffness per unit length of beam
    kb.insert(idx - 1, '*Foundation\nALLEL, FY, %r' % k_line)

job = mdb.Job(name=JOB, model='Winkler', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
fr = odb.steps['Load'].frames[-1]
asm = odb.rootAssembly
w0 = abs(float(fr.fieldOutputs['U'].getSubset(region=asm.nodeSets['LOAD']).values[0].data[1]))
inst_o = asm.instances['BEAM-1']
xs = dict((n.label, float(n.coordinates[0])) for n in inst_o.nodes)
el_x = dict((e.label, math.fsum([xs[c] for c in e.connectivity]) / len(e.connectivity)) for e in inst_o.elements)
by_el = {}
for v in fr.fieldOutputs['SM'].values:
    by_el.setdefault(v.elementLabel, []).append(float(v.data[0]))
means = sorted((el_x[lab], math.fsum(vals) / len(vals)) for lab, vals in by_el.items() if el_x[lab] > X_LOAD)
(xa, ma), (xb, mb) = means[0], means[1]
m0 = abs(ma + (ma - mb) * (xa - X_LOAD) / (xb - xa))          # element means extrapolated to the load point
m_ip = max([abs(float(v.data[0])) for v in fr.fieldOutputs['SM'].values])
# the moment Abaqus extrapolates from the integration points to the load node (the nodal moment of a cubic B23)
load_label = asm.nodeSets['LOAD'].nodes[0][0].label
m_node = max([abs(float(v.data[0])) for v in fr.fieldOutputs['SM'].getSubset(position=ELEMENT_NODAL).values
              if v.nodeLabel == load_label] or [0.0])
n_el = len(inst_o.elements)
odb.close()
lam = (KS * WIDTH / (4.0 * E * WIDTH * DEPTH ** 3 / 12.0)) ** 0.25
sys.__stdout__.write('d07 %s: deflection %.6e m (Hetenyi %.6e), moment: element means extrapolated %.3f kNm, at the '
                     'load node (element-nodal) %.3f, largest at an integration point %.3f (Hetenyi %.3f), '
                     'elements %d\n' % (VARIANT, w0, P * lam / (2.0 * KS * WIDTH), m0, m_node, m_ip, P / (4.0 * lam),
                                        n_el))

reported = m0
if VARIANT in ('ip_reported', 'b21_ip_reported'):
    reported = m_ip
elif VARIANT == 'mesh_1m':
    reported = m_node
result = {'deflection_under_load_m': w0, 'moment_under_load_kNm': reported}
if VARIANT == 'fake_report':
    result['deflection_under_load_m'] = w0 * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
