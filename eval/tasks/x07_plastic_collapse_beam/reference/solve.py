# -*- coding: utf-8 -*-
"""Reference solution for x07_plastic_collapse_beam  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Simply supported beam along x, span 2 m (supports at x = 0 and x = 2), rectangular section 0.05 wide x 0.10 deep,
E = 2.0e11 Pa, elastic - perfectly plastic with yield stress 250 MPa, point load at midspan in -y.
    My = sy b h^2 / 6 = 20833 Nm,  Mp = sy b h^2 / 4 = 31250 Nm
    first yield  Py = 4 My / L = 41667 N,   plastic collapse  Pc = 4 Mp / L = 62500 N
A beam element samples the moment at its integration point, not at the loaded node, so the FE collapse load is
4 Mp / (L - le) for linear elements of length le: +1.0 % with 100 elements, +5.3 % with 20.

The trap of this task.  With linear beam elements and a node at midspan, the TWO elements next to the load become
fully plastic together.  The tangent stiffness then has a zero-energy mode (the two halves rotate the same way about
the held midspan node) and Abaqus stops with "numerical singularity ... D.O.F. 6" as soon as the plateau is reached -
with displacement control and with Riks alike.  Remedies that work: hold the rotation of the midspan node (exact by
symmetry), model half the beam, use quadratic elements (B22), or give the material a numerically negligible
hardening slope.

REF_VARIANT (environment variable):
  default           100 B21, midspan rotation held (symmetry), deflection prescribed to 50 mm in 50 increments -> PASS
  b22               50 B22, nothing held at midspan                                                           -> PASS
  b31_3d            3-D model, 100 B31, midspan rotation held                                                 -> PASS
  half_symmetry     half the beam (x = 0 .. 1), 50 B21, symmetry conditions at midspan                        -> PASS
  tiny_hardening    100 B21, nothing held at midspan, hardening slope E/10000                                 -> PASS
  riks              100 B21, midspan rotation held, force loading with the Riks method, stopped at 50 mm      -> PASS
  plane_stress      2-D continuum, CPS4I 200 x 10, supports at the bottom corners                             -> PASS
  b21_unrestrained  100 B21, nothing held at midspan, perfect plasticity: stops at the plateau                -> FAIL
  force_early       force control, 55 kN: the job completes, no collapse reached                              -> FAIL
  small_push        deflection prescribed to 9 mm only (just beyond first yield)                              -> FAIL
  elastic           no plasticity                                                                             -> FAIL
  hardening         linear hardening (H = E/20) instead of perfect plasticity                                 -> FAIL
  clamped           both ends built in: collapse at 8 Mp / L                                                  -> FAIL
  swapped_section   section 0.10 wide x 0.05 deep (profile dimensions exchanged)                              -> FAIL
  coarse_b21        20 B21                                                                                    -> FAIL
  nlgeom_pinned     NLGEOM with both ends held horizontally: catenary action, no plateau                      -> FAIL
  fake_report       correct model, collapse load written to result.json in kN                                 -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'collapse'
KNOWN = ('default', 'b22', 'b31_3d', 'half_symmetry', 'tiny_hardening', 'riks', 'plane_stress', 'b21_unrestrained',
         'force_early', 'small_push', 'elastic', 'hardening', 'clamped', 'swapped_section', 'coarse_b21',
         'nlgeom_pinned', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

L, B, H = 2.0, 0.05, 0.10
E, NU, SY = 2.0e11, 0.3, 250.0e6
PUSH = 0.009 if VARIANT == 'small_push' else 0.05
continuum = VARIANT == 'plane_stress'
three_d = VARIANT == 'b31_3d'
half = VARIANT == 'half_symmetry'
hold_mid_rotation = VARIANT not in ('b22', 'tiny_hardening', 'b21_unrestrained', 'plane_stress', 'half_symmetry')
tol = 1.0e-6

m = mdb.Model(name='Collapse')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
if VARIANT == 'hardening':
    mat.Plastic(table=((SY, 0.0), (SY + 2.0e8, 0.02)))
elif VARIANT == 'tiny_hardening':
    mat.Plastic(table=((SY, 0.0), (SY + 2.0e6, 0.1)))          # slope E/10000
elif VARIANT != 'elastic':
    mat.Plastic(table=((SY, 0.0),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)

if continuum:
    s = m.ConstrainedSketch(name='sk', sheetSize=4.0)
    s.rectangle(point1=(0.0, -H / 2), point2=(L, H / 2))
    p = m.Part(name='Beam', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    p.PartitionFaceByDatumPlane(faces=p.faces[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=YZPLANE, offset=L / 2).id])
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=B)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.seedPart(size=0.01)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPS4I, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='LEFT', vertices=inst.vertices.findAt(((0.0, -H / 2, 0.0),)))
    a.Set(name='RIGHT', vertices=inst.vertices.findAt(((L, -H / 2, 0.0),)))
    a.Set(name='MID', vertices=inst.vertices.findAt(((L / 2, H / 2, 0.0),)))
else:
    s = m.ConstrainedSketch(name='axis', sheetSize=4.0)
    s.Line(point1=(0.0, 0.0), point2=(L / 2, 0.0))
    if not half:
        s.Line(point1=(L / 2, 0.0), point2=(L, 0.0))
    p = m.Part(name='Beam', dimensionality=THREE_D if three_d else TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    region = regionToolset.Region(edges=p.edges)
    if VARIANT == 'swapped_section':
        m.RectangularProfile(name='Rect', a=H, b=B)
    else:
        m.RectangularProfile(name='Rect', a=B, b=H)        # a along local 1 (out of plane), b = depth in the plane
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=NU)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    n_half = {'b22': 25, 'coarse_b21': 10}.get(VARIANT, 50)
    code = B22 if VARIANT == 'b22' else (B31 if three_d else B21)
    p.seedEdgeByNumber(edges=p.edges, number=n_half, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='LEFT', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    if not half:
        a.Set(name='RIGHT', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))
    a.Set(name='MID', vertices=inst.vertices.getByBoundingBox(xMin=L / 2 - tol, xMax=L / 2 + tol))
supports = ('LEFT',) if half else ('LEFT', 'RIGHT')

nl = ON if VARIANT == 'nlgeom_pinned' else OFF
if VARIANT == 'riks':
    m.StaticRiksStep(name='Push', previous='Initial', maxNumInc=400, initialArcInc=0.05, minArcInc=1.0e-8,
                     maxArcInc=0.1, nodeOn=ON, maximumDisplacement=-PUSH, region=a.sets['MID'], dof=2, nlgeom=nl)
else:
    m.StaticStep(name='Push', previous='Initial', initialInc=0.02, maxInc=0.02, minInc=1.0e-8, maxNumInc=2000,
                 nlgeom=nl)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'PE', 'PEEQ', 'U', 'RF', 'CF'))

if VARIANT == 'clamped':
    m.EncastreBC(name='Left', createStepName='Initial', region=a.sets['LEFT'])
    m.EncastreBC(name='Right', createStepName='Initial', region=a.sets['RIGHT'])
elif half:
    m.DisplacementBC(name='Left', createStepName='Initial', region=a.sets['LEFT'], u2=0.0)
    m.DisplacementBC(name='Symmetry', createStepName='Initial', region=a.sets['MID'], u1=0.0, ur3=0.0)
elif three_d:
    m.DisplacementBC(name='Left', createStepName='Initial', region=a.sets['LEFT'], u1=0.0, u2=0.0, u3=0.0, ur1=0.0)
    m.DisplacementBC(name='Right', createStepName='Initial', region=a.sets['RIGHT'], u2=0.0, u3=0.0)
else:
    m.DisplacementBC(name='Left', createStepName='Initial', region=a.sets['LEFT'], u1=0.0, u2=0.0)
    if VARIANT == 'nlgeom_pinned':
        m.DisplacementBC(name='Right', createStepName='Initial', region=a.sets['RIGHT'], u1=0.0, u2=0.0)
    else:
        m.DisplacementBC(name='Right', createStepName='Initial', region=a.sets['RIGHT'], u2=0.0)
if hold_mid_rotation and VARIANT != 'clamped':
    # by symmetry the midspan section does not rotate; holding it removes the singular two-hinge mode
    m.DisplacementBC(name='MidRotation', createStepName='Initial', region=a.sets['MID'], ur3=0.0)
if VARIANT == 'force_early':
    m.ConcentratedForce(name='Load', createStepName='Push', region=a.sets['MID'], cf2=-55000.0)
elif VARIANT == 'riks':
    m.ConcentratedForce(name='Load', createStepName='Push', region=a.sets['MID'], cf2=-70000.0)
else:
    m.DisplacementBC(name='Load', createStepName='Push', region=a.sets['MID'], u2=-PUSH)

job = mdb.Job(name=JOB, model='Collapse', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: load = sum of the support reactions (x 2 for the half model) ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frames = odb.steps['Push'].frames
asm = odb.rootAssembly
factor = 2.0 if half else 1.0


def support_load(fr):
    rf = fr.fieldOutputs['RF']
    return factor * abs(math.fsum([rf.getSubset(region=asm.nodeSets[n]).values[0].data[1] for n in supports]))


collapse = support_load(frames[-1])
# first yield from the first (elastic) increment: stresses scale with the load until the extreme fibre yields
f1 = frames[1]
p1 = support_load(f1)
if continuum:
    # bending stress in the bottom elements next to midspan, extrapolated from the integration point to the fibre
    inst_o = asm.instances['BEAM-1']
    centroid = {}
    for el in inst_o.elements:
        xy = [inst_o.getNodeFromLabel(n).coordinates for n in el.connectivity]
        centroid[el.label] = (math.fsum([c[0] for c in xy]) / len(xy), math.fsum([c[1] for c in xy]) / len(xy))
    s_fibre = 0.0
    for v in f1.fieldOutputs['S'].getSubset(position=CENTROID).values:
        cx, cy = centroid[v.elementLabel]
        if abs(cx - L / 2) < 0.011 and cy < -0.04:
            s_fibre = max(s_fibre, abs(v.data[0]) * (H / 2) / abs(cy))
    s1 = s_fibre
else:
    s1 = max([v.mises for v in f1.fieldOutputs['S'].values])
first_yield = p1 * SY / s1 if s1 > 0.0 else 0.0
n_frames = len(frames)
odb.close()
sys.__stdout__.write('x07 %s: collapse %.1f N  first yield %.1f N (frame 1: P = %.1f, stress %.4e)  frames = %d\n' % (
    VARIANT, collapse, first_yield, p1, s1, n_frames))

result = {'collapse_load_N': float(collapse), 'first_yield_load_N': float(first_yield)}
if VARIANT == 'fake_report':
    result['collapse_load_N'] = float(collapse) / 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
