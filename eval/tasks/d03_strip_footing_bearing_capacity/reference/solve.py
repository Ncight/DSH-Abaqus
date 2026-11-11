# -*- coding: utf-8 -*-
"""Reference solution for d03_strip_footing_bearing_capacity  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Rigid strip footing, width B = 2 m, on weightless undrained clay, cu = 100 kPa, plane
strain.  Prandtl:  q_ult = (2 + pi) cu = 514.16 kPa,  ultimate load per metre of footing = 1028.3 kN/m.
The clay is a Mises material: in plane strain the Tresca strength cu corresponds to a Mises yield stress
sqrt(3) cu = 173.2 kPa.   E = 30 000 kPa (E / cu = 300), nu = 0.49.
Model: half of the problem (x >= 0), 12 m wide and 8 m deep; the footing is the segment 0 <= x <= 1 m of the surface,
tied to a reference point that is pushed down 0.2 m; the load is the reaction at that reference point (x 2).

REF_VARIANT (environment variable):
  default           CPE8R, 0.125 m elements around the footing (1360 elements), smooth footing (only the
                    vertical displacement of the footing nodes is coupled to the reference point)          -> PASS
  rough             rough footing: horizontal displacement coupled to the (guided) reference point as well -> PASS
  cpe8rh            hybrid elements                                                                       -> PASS
  mc_phi0           Mohr-Coulomb with phi = 0, c = cu (Tresca) instead of the Mises equivalent            -> PASS
  full_model        both halves modelled (no symmetry)                                                    -> PASS
  direct_bc         displacement prescribed on the footing nodes themselves (rough), no reference point    -> PASS
  mises_2cu         Mises yield stress 2 cu = 200 kPa                                                     -> FAIL
  cpe4              linear CPE4 (selectively reduced integration, no locking): 6 % LOW at this mesh size -
                    the footing ends half an element short                                                -> PASS
  locking           linear triangles CPE3: volumetric locking of the nearly incompressible soil, +51 %    -> FAIL
  coarse_mesh       0.5 m elements (two under the half footing)                                           -> FAIL
  stop_early        footing pushed 0.02 m only                                                            -> FAIL
  force_control     pressure of 400 kPa on the footing instead of a displacement                          -> FAIL
  axisymmetric      CAX8R: a circular footing of radius 1 m                                               -> FAIL
  small_domain      soil layer only 0.4 m deep on a rigid base (3 m wide): the mechanism is cut off       -> FAIL
  elastic           no plasticity                                                                         -> FAIL
  fake_report       correct analysis, load of the half model reported                                     -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'footing'
KNOWN = ('default', 'rough', 'cpe8rh', 'mc_phi0', 'cpe4', 'full_model', 'direct_bc', 'mises_2cu', 'locking', 'coarse_mesh',
         'stop_early', 'force_control', 'axisymmetric', 'small_domain', 'elastic', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

B_HALF, CU = 1.0, 100.0
E, NU = 30000.0, 0.49
SY = (2.0 if VARIANT == 'mises_2cu' else math.sqrt(3.0)) * CU
WIDTH, DEPTH = (3.0, 0.4) if VARIANT == 'small_domain' else (12.0, 8.0)
X_FINE, Y_FINE = (3.0, 0.4) if VARIANT == 'small_domain' else (4.0, 3.0)
SIZE = float(os.environ.get('REF_MESH', '') or (0.5 if VARIANT == 'coarse_mesh' else 0.125))
PUSH = 0.02 if VARIANT == 'stop_early' else 0.2
full = VARIANT == 'full_model'
axi = VARIANT == 'axisymmetric'
tol = 1.0e-6
if VARIANT == 'locking':
    code = CPE3
elif VARIANT == 'cpe4':
    code = CPE4
elif VARIANT == 'cpe8rh':
    code = CPE8RH
elif axi:
    code = CAX8R
else:
    code = CPE8R
if os.environ.get('REF_ELEM'):                     # element study: REF_ELEM=CPE4R, CPE4H, CPE6M, CPE3 ...
    code = globals()[os.environ['REF_ELEM']]

m = mdb.Model(name='Footing')
mat = m.Material(name='Clay')
mat.Elastic(table=((E, NU),))
if VARIANT == 'mc_phi0':
    mat.MohrCoulombPlasticity(table=((0.0, 0.0),))
    mat.mohrCoulombPlasticity.MohrCoulombHardening(table=((CU, 0.0),))
elif VARIANT != 'elastic':
    mat.Plastic(table=((SY, 0.0),))

x_lines = [0.0, B_HALF] + ([X_FINE] if X_FINE < WIDTH - tol else []) + [WIDTH]
if full:
    x_lines = [-x for x in reversed(x_lines[1:])] + x_lines
y_lines = [0.0] + ([-Y_FINE] if Y_FINE < DEPTH - tol else []) + [-DEPTH]
s = m.ConstrainedSketch(name='soil', sheetSize=50.0)
if axi:
    s.ConstructionLine(point1=(0.0, -20.0), point2=(0.0, 5.0))
s.rectangle(point1=(x_lines[0], -DEPTH), point2=(WIDTH, 0.0))
p = m.Part(name='Soil', dimensionality=AXISYMMETRIC if axi else TWO_D_PLANAR, type=DEFORMABLE_BODY)
p.BaseShell(sketch=s)
for x in x_lines[1:-1]:
    p.PartitionFaceByDatumPlane(faces=p.faces[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=YZPLANE, offset=x).id])
for y in y_lines[1:-1]:
    p.PartitionFaceByDatumPlane(faces=p.faces[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=XZPLANE, offset=y).id])
if axi:
    m.HomogeneousSolidSection(name='Sec', material='Clay')
else:
    m.HomogeneousSolidSection(name='Sec', material='Clay', thickness=1.0)
p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
for e in p.edges:                                  # fine zone: SIZE; outside: 1 m (horizontal), 0.5 m (vertical)
    (xa, ya, _z), (xb, yb, _z2) = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    if abs(xb - xa) > abs(yb - ya):
        length, fine = abs(xb - xa), max(abs(xa), abs(xb)) <= X_FINE + tol
        n = length / (SIZE if fine else 1.0)
    else:
        length, fine = abs(yb - ya), max(abs(ya), abs(yb)) <= Y_FINE + tol
        n = length / (SIZE if fine else 0.5)
    p.seedEdgeByNumber(edges=(e,), number=max(1, int(round(n))), constraint=FIXED)
if str(code) in ('CPE3', 'CPE6', 'CPE6M', 'CPE3H', 'CPE6H'):
    p.setMeshControls(regions=p.faces, elemShape=TRI, technique=STRUCTURED)
else:
    p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=STANDARD),))
p.generateMesh()
n_el = len(p.elements)
if n_el > 20000:
    raise RuntimeError('mesh too large: %d elements' % n_el)

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Soil-1', part=p, dependent=ON)
x_min = x_lines[0]
foot_edges = inst.edges.getByBoundingBox(xMin=(-B_HALF if full else 0.0) - tol, xMax=B_HALF + tol, yMin=-tol)
a.Set(name='FOOTING', edges=foot_edges)
a.Surface(name='FOOTING', side1Edges=foot_edges)
a.Set(name='BOTTOM', edges=inst.edges.getByBoundingBox(yMax=-DEPTH + tol))
a.Set(name='RIGHT', edges=inst.edges.getByBoundingBox(xMin=WIDTH - tol))
a.Set(name='LEFT', edges=inst.edges.getByBoundingBox(xMax=x_min + tol))

m.StaticStep(name='Push', previous='Initial', initialInc=0.005, maxInc=0.02, minInc=1.0e-8, maxNumInc=5000,
             nlgeom=OFF)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'PEEQ', 'U', 'RF', 'CF'))
m.DisplacementBC(name='Bottom', createStepName='Initial', region=a.sets['BOTTOM'], u1=0.0, u2=0.0)
m.DisplacementBC(name='Right', createStepName='Initial', region=a.sets['RIGHT'], u1=0.0)
m.DisplacementBC(name='Left', createStepName='Initial', region=a.sets['LEFT'], u1=0.0)
use_rp = VARIANT not in ('direct_bc', 'force_control')
if VARIANT == 'force_control':
    m.Pressure(name='Load', createStepName='Push', region=a.surfaces['FOOTING'], magnitude=400.0)
elif use_rp:
    rp = a.ReferencePoint(point=(0.0, 0.5, 0.0))
    a.Set(name='RP', referencePoints=(a.referencePoints[rp.id],))
    # rigid footing: every node of the footing strip follows the vertical displacement of the reference point;
    # for a rough footing the horizontal displacement is coupled as well (the reference point is guided).
    # NOTE: a boundary condition on the remaining dof of a kinematically coupled node is IGNORED by Abaqus (warning
    # "DEGREE OF FREEDOM 1 IS NOT ACTIVE ... THIS BOUNDARY CONDITION IS IGNORED") - roughness has to go through
    # the coupling.
    m.Coupling(name='Footing', controlPoint=a.sets['RP'], surface=a.surfaces['FOOTING'],
               influenceRadius=WHOLE_SURFACE, couplingType=KINEMATIC, u1=ON if VARIANT == 'rough' else OFF, u2=ON,
               ur3=OFF)
    m.DisplacementBC(name='Guide', createStepName='Initial', region=a.sets['RP'], u1=0.0, ur3=0.0)
    m.DisplacementBC(name='Push', createStepName='Push', region=a.sets['RP'], u2=-PUSH)
else:
    m.DisplacementBC(name='Push', createStepName='Push', region=a.sets['FOOTING'], u2=-PUSH)
if VARIANT == 'direct_bc':
    m.DisplacementBC(name='Rough', createStepName='Initial', region=a.sets['FOOTING'], u1=0.0)

job = mdb.Job(name=JOB, model='Footing', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: footing load = reaction at the reference point (or sum over the footing nodes) ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frames = odb.steps['Push'].frames
asm = odb.rootAssembly
factor = 1.0 if (full or axi) else 2.0


def load(fr):
    rf = fr.fieldOutputs['RF']
    if use_rp:
        return factor * abs(float(rf.getSubset(region=asm.nodeSets['RP']).values[0].data[1]))
    if VARIANT == 'force_control':
        return factor * abs(math.fsum([float(v.data[1]) for v in rf.getSubset(region=asm.nodeSets['BOTTOM']).values]))
    return factor * abs(math.fsum([float(v.data[1]) for v in rf.getSubset(region=asm.nodeSets['FOOTING']).values]))


p_end = load(frames[-1])
hist = [(float(fr.frameValue), load(fr)) for fr in frames]
p_85 = min(hist, key=lambda h: abs(h[0] - 0.85))[1]
p_50 = min(hist, key=lambda h: abs(h[0] - 0.50))[1]
n_frames = len(frames)
odb.close()
prandtl = (2.0 + math.pi) * CU * 2.0 * B_HALF
sys.__stdout__.write('d03 %s: load %.2f kN/m (Prandtl %.2f, %+.2f %%), at 85 %% of the step %.2f (%+.3f %%), at 50 %% '
                     '%.2f; elements %d, frames %d\n' % (
                         VARIANT, p_end, prandtl, 100.0 * (p_end / prandtl - 1.0), p_85,
                         100.0 * (p_end - p_85) / p_end if p_end else 0.0, p_50, n_el, n_frames))

result = {'ultimate_load_kN_per_m': p_end}
if VARIANT == 'fake_report':
    result['ultimate_load_kN_per_m'] = p_end / 2.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
