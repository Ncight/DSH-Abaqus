# -*- coding: utf-8 -*-
"""Reference solution for d01_site_response_viscous_boundary  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Shear column of homogeneous elastic soil, H = 50 m, width 4 m (plane strain thickness 1),
rho = 2.0 t/m3, Vs = 200 m/s, nu = 0.3  ->  G = 80 000 kPa, E = 208 000 kPa, Vp = 374.2 m/s.
Vertically incident SH wave: incident particle velocity = Ricker wavelet (fc = 4 Hz, t0 = 0.3 s, peak 0.1 m/s).
Base = viscous (Lysmer) boundary: dashpot rho Vs per unit area, incident wave entered as the equivalent shear
traction 2 rho Vs v_inc(t) on the same boundary.
    surface velocity = 2 v_inc(t - H/Vs):  peak 0.2 m/s at t = 0.3 + 0.25 = 0.55 s, nothing left after ~0.9 s.

REF_VARIANT (environment variable):
  default          2-D, CPE4R 1 x 50 (h = 1 m), all vertical dofs held, grounded dashpots + nodal forces,
                   implicit dynamics dt = 1 ms, V every 5 ms                                              -> PASS
  periodic_ties    left and right nodes of every level tied in both dofs instead of holding u2            -> PASS
  solid_3d         3-D column 4 x 4 x 50 m, C3D8R, implicit                                               -> PASS
  explicit_2d      Abaqus/Explicit, two-node dashpots to fixed points                                     -> PASS
  rigid_base       base velocity prescribed = incident wavelet (no absorbing boundary): energy trapped    -> FAIL
  force_factor_1   equivalent force rho Vs v instead of 2 rho Vs v                                        -> FAIL
  no_dashpot       force on a free base, no dashpot                                                       -> FAIL
  dashpot_vp       dashpot coefficient rho Vp instead of rho Vs                                           -> FAIL
  not_area_scaled  dashpot and force per node taken as the per-unit-area values (tributary area 2 m2)     -> FAIL
  coarse_mesh      h = 10 m (5 elements per wavelength at 4 Hz, 2 at 10 Hz)                               -> FAIL
  coarse_dt        dt = 20 ms                                                                             -> FAIL
  free_sides       vertical dofs free (only the base held vertically), four elements across the width:
                   the column bends like a cantilever instead of shearing (peak +22 %, 0.07 s late)        -> FAIL
  fake_report      correct analysis, time of the peak reported as the wavelet time t0 = 0.3 s             -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'site'
KNOWN = ('default', 'periodic_ties', 'solid_3d', 'explicit_2d', 'rigid_base', 'force_factor_1', 'no_dashpot',
         'dashpot_vp', 'not_area_scaled', 'coarse_mesh', 'coarse_dt', 'free_sides', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

H, B = 50.0, 4.0
RHO, VS, NU = 2.0, 200.0, 0.3
G = RHO * VS ** 2
E = 2.0 * G * (1.0 + NU)
VP = VS * math.sqrt(2.0 * (1.0 - NU) / (1.0 - 2.0 * NU))
FC, T0, V_PEAK = 4.0, 0.3, 0.1
T_END, T_LATE = 1.5, 1.0
three_d = VARIANT == 'solid_3d'
explicit = VARIANT == 'explicit_2d'
h = 10.0 if VARIANT == 'coarse_mesh' else 1.0
NX = 4 if VARIANT == 'free_sides' else 1            # elements across the width (one is enough for pure shear)
DT = 0.02 if VARIANT == 'coarse_dt' else 0.001
OUT_EVERY = 1 if VARIANT == 'coarse_dt' else 5
tol = 1.0e-6


def ricker(t):
    a = (math.pi * FC * (t - T0)) ** 2
    return V_PEAK * (1.0 - 2.0 * a) * math.exp(-a)


m = mdb.Model(name='Site')
mat = m.Material(name='Soil')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
s = m.ConstrainedSketch(name='col', sheetSize=200.0)
s.rectangle(point1=(0.0, 0.0), point2=(B, H))
lib = EXPLICIT if explicit else STANDARD
if three_d:
    p = m.Part(name='Column', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=B)
    m.HomogeneousSolidSection(name='Sec', material='Soil')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=lib),))
else:
    p = m.Part(name='Column', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Soil', thickness=1.0)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4R, elemLibrary=lib),))
for e in p.edges:                                   # one element across the column, h along the height
    v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    vertical = abs(v1[1] - v0[1]) > 0.5 * H
    p.seedEdgeByNumber(edges=(e,), number=int(round(H / h)) if vertical else NX, constraint=FIXED)
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Column-1', part=p, dependent=ON)
base_nodes = inst.nodes.getByBoundingBox(yMax=tol)
top_nodes = inst.nodes.getByBoundingBox(yMin=H - tol)
a.Set(name='BASE', nodes=base_nodes)
a.Set(name='TOP', nodes=top_nodes)
probe = [nd for nd in top_nodes if abs(nd.coordinates[0]) < tol and abs(nd.coordinates[2]) < tol]
a.Set(name='PROBE', nodes=inst.nodes.sequenceFromLabels((probe[0].label,)))     # the surface point (0, 50, 0)
a.Set(name='ALLNODES', nodes=inst.nodes)
n_base = len(base_nodes)
area_node = (B * B if three_d else B * 1.0) / n_base        # tributary area of one base node (NX = 1: all equal)
base_sets = [('BASE', area_node)]                           # (node set, tributary area of each of its nodes)
if NX > 1:                                                  # several elements across: the end nodes carry half
    ordered = sorted(base_nodes, key=lambda nd: nd.coordinates[0])
    xs = [nd.coordinates[0] for nd in ordered]
    base_sets = []
    for i, nd in enumerate(ordered):
        width = 0.5 * ((xs[i] - xs[i - 1] if i > 0 else 0.0) + (xs[i + 1] - xs[i] if i < len(xs) - 1 else 0.0))
        a.Set(name='BASE%d' % i, nodes=inst.nodes.sequenceFromLabels((nd.label,)))
        base_sets.append(('BASE%d' % i, width * 1.0))

if explicit:
    m.ExplicitDynamicsStep(name='Dyn', previous='Initial', timePeriod=T_END, nlgeom=OFF)
    # timeMarks: cut the increments so that the frames fall exactly on the 5 ms marks (the default lets them
    # slip by up to one stable increment, 2.7 ms here)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('U', 'V', 'A', 'S'), numIntervals=int(T_END / 0.005),
                                                  timeMarks=ON)
else:
    m.ImplicitDynamicsStep(name='Dyn', previous='Initial', timePeriod=T_END, application=TRANSIENT_FIDELITY,
                           timeIncrementationMethod=FIXED, initialInc=DT, maxNumInc=int(T_END / DT) + 100,
                           nlgeom=OFF)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('U', 'V', 'A', 'S'), frequency=OUT_EVERY)
m.TabularAmplitude(name='Ricker', timeSpan=STEP, data=tuple((0.001 * i, ricker(0.001 * i)) for i in range(1501)))

# ---- lateral conditions: a shear column - no vertical motion, every level moves as one ----
if VARIANT == 'free_sides':
    m.DisplacementBC(name='BaseVert', createStepName='Initial', region=a.sets['BASE'], u2=0.0)
elif VARIANT == 'periodic_ties':
    m.DisplacementBC(name='BaseVert', createStepName='Initial', region=a.sets['BASE'], u2=0.0)
    levels = {}
    for nd in inst.nodes:
        levels.setdefault(round(nd.coordinates[1], 6), []).append(nd)
    for i, y in enumerate(sorted(levels)):
        pair = sorted(levels[y], key=lambda n: n.coordinates[0])
        a.Set(name='L%d' % i, nodes=inst.nodes.sequenceFromLabels((pair[0].label,)))
        a.Set(name='R%d' % i, nodes=inst.nodes.sequenceFromLabels((pair[1].label,)))
        m.Equation(name='TieX%d' % i, terms=((1.0, 'R%d' % i, 1), (-1.0, 'L%d' % i, 1)))
        if y > tol:
            m.Equation(name='TieY%d' % i, terms=((1.0, 'R%d' % i, 2), (-1.0, 'L%d' % i, 2)))
elif three_d:
    m.DisplacementBC(name='NoVert', createStepName='Initial', region=a.sets['ALLNODES'], u2=0.0, u3=0.0)
else:
    m.DisplacementBC(name='NoVert', createStepName='Initial', region=a.sets['ALLNODES'], u2=0.0)

# ---- base: viscous boundary + equivalent force of the incident wave ----
c_area = RHO * (VP if VARIANT == 'dashpot_vp' else VS)          # dashpot per unit area
f_area = (1.0 if VARIANT == 'force_factor_1' else 2.0) * RHO * VS   # force per unit area and unit velocity
scale = 1.0 if VARIANT == 'not_area_scaled' else area_node
if VARIANT == 'rigid_base':
    m.VelocityBC(name='BaseMotion', createStepName='Dyn', region=a.sets['BASE'], v1=1.0, amplitude='Ricker')
else:
    if VARIANT != 'no_dashpot':
        if explicit:
            # Abaqus/Explicit has no grounded dashpot: two-node dashpots to fixed points 10 m to the side
            for i, nd in enumerate(base_nodes):
                x, y, z = nd.coordinates
                rp = a.ReferencePoint(point=(x - 10.0, y, z))
                a.Set(name='GND%d' % i, referencePoints=(a.referencePoints[rp.id],))
                a.Set(name='BN%d' % i, nodes=inst.nodes.sequenceFromLabels((nd.label,)))
                m.EncastreBC(name='Ground%d' % i, createStepName='Initial', region=a.sets['GND%d' % i])
                a.engineeringFeatures.TwoPointSpringDashpot(
                    name='Dashpot%d' % i, regionPairs=((a.sets['BN%d' % i], a.sets['GND%d' % i]),), axis=NODAL_LINE,
                    springBehavior=OFF, dashpotBehavior=ON, dashpotCoefficient=c_area * scale)
        else:
            for name, area in base_sets:
                a.engineeringFeatures.SpringDashpotToGround(
                    name='Dashpot' + name[4:], region=a.sets[name], orientation=None, dof=1, springBehavior=OFF,
                    dashpotBehavior=ON, dashpotCoefficient=c_area * (1.0 if VARIANT == 'not_area_scaled' else area))
    for name, area in base_sets:
        m.ConcentratedForce(name='Wave' + name[4:], createStepName='Dyn', region=a.sets[name],
                            cf1=f_area * (1.0 if VARIANT == 'not_area_scaled' else area), amplitude='Ricker')

job = mdb.Job(name=JOB, model='Site', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: horizontal velocity of the surface ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
top = odb.rootAssembly.nodeSets['PROBE']
ts, vs = [], []
for fr in odb.steps['Dyn'].frames:
    ts.append(float(fr.frameValue))
    vs.append(float(fr.fieldOutputs['V'].getSubset(region=top).values[0].data[0]))
odb.close()
k_peak = max(range(len(vs)), key=lambda i: abs(vs[i]))
peak, t_peak = abs(vs[k_peak]), ts[k_peak]
late = max([abs(v) for t, v in zip(ts, vs) if t >= T_LATE - 1.0e-9] or [0.0])
sys.__stdout__.write('d01 %s: peak %.5f m/s at t = %.4f s, late residual %.5f (%.2f %% of the peak), frames %d\n' % (
    VARIANT, peak, t_peak, late, 100.0 * late / peak if peak else 0.0, len(ts)))

result = {'peak_surface_velocity_m_s': float(peak), 'time_of_peak_s': float(t_peak),
          'late_residual_ratio': float(late / peak) if peak else 0.0}
if VARIANT == 'fake_report':
    result['time_of_peak_s'] = T0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
