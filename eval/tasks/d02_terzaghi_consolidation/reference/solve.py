# -*- coding: utf-8 -*-
"""Reference solution for d02_terzaghi_consolidation  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Saturated layer H = 10 m, drained at the top only, E = 10 000 kPa, nu = 0.3,
k = 1e-7 m/s, gamma_w = 9.8 kN/m3, surcharge q = 100 kPa applied suddenly, lateral strain prevented.
    constrained modulus  M = E (1-nu) / ((1+nu)(1-2nu)) = 13 461.5 kPa
    cv = k M / gamma_w = 1.3736e-4 m2/s,  Tv = cv t / H^2 = 0.2  at  t = 145 600 s
    Terzaghi:  u_base / q = 0.7723 (77.23 kPa),  U = 0.5041,  settlement 0.037446 m,  final q H / M = 0.074286 m

REF_VARIANT (environment variable):
  default           2-D column 1 x 10 m, 20 CPE8RP; load step of 300 s (one increment), then consolidation
                    steps ending at t = 145 600 s and at Tv = 3                                           -> PASS
  cpe4p             40 CPE4P                                                                              -> PASS
  c3d8p             3-D column, 20 C3D8P                                                                  -> PASS
  gamma_w_one       specific weight of the pore fluid 1.0 instead of 9.8                                  -> FAIL
  gamma_w_newton    specific weight 9800 (N/m3) in a kN model                                             -> FAIL
  k_per_day         k converted to m/day (8.64e-3) while time runs in seconds                             -> FAIL
  drained_both      pore pressure also zero at the base                                                   -> FAIL
  no_drainage       no pore pressure boundary condition at all                                            -> FAIL
  lateral_free      sides free to move laterally                                                          -> FAIL
  ramp_load         surcharge ramped over the first consolidation step                                    -> FAIL
  big_increments    two fixed increments up to t = 145 600 s                                              -> FAIL
  short_final       analysis stopped at Tv = 0.6: "final" settlement not reached                          -> FAIL
  total_reported    correct analysis, hydrostatic 98 kPa added to the reported base pressure              -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'consol'
KNOWN = ('default', 'cpe4p', 'c3d8p', 'gamma_w_one', 'gamma_w_newton', 'k_per_day', 'drained_both', 'no_drainage',
         'lateral_free', 'ramp_load', 'big_increments', 'short_final', 'total_reported')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

H, W = 10.0, 1.0
E, NU = 10000.0, 0.3
K_PERM = 8.64e-3 if VARIANT == 'k_per_day' else 1.0e-7
GAMMA_W = {'gamma_w_one': 1.0, 'gamma_w_newton': 9800.0}.get(VARIANT, 9.8)
Q = 100.0
M_C = E * (1.0 - NU) / ((1.0 + NU) * (1.0 - 2.0 * NU))
CV = 1.0e-7 * M_C / 9.8
T_TV = 0.2 * H * H / CV                               # 145 600 s
T_END = (0.6 if VARIANT == 'short_final' else 3.0) * H * H / CV
three_d = VARIANT == 'c3d8p'
n_y = 40 if VARIANT == 'cpe4p' else 20
tol = 1.0e-6

m = mdb.Model(name='Consol')
mat = m.Material(name='Clay')
mat.Elastic(table=((E, NU),))
mat.Permeability(specificWeight=GAMMA_W, inertialDragCoefficient=0.142887, table=((K_PERM, 1.0),))
s = m.ConstrainedSketch(name='col', sheetSize=50.0)
s.rectangle(point1=(0.0, 0.0), point2=(W, H))
if three_d:
    p = m.Part(name='Column', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=W)
    m.HomogeneousSolidSection(name='Sec', material='Clay')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8P, elemLibrary=STANDARD),))
else:
    p = m.Part(name='Column', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Clay', thickness=1.0)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    code = CPE4P if VARIANT == 'cpe4p' else CPE8RP
    # quadrilaterals only - with the default quad-dominated controls CAE refuses a quad-only element type
    p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=STANDARD),))
for e in p.edges:
    v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
    vertical = abs(v1[1] - v0[1]) > 0.5 * H
    p.seedEdgeByNumber(edges=(e,), number=n_y if vertical else 1, constraint=FIXED)
p.generateMesh()

a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Column-1', part=p, dependent=ON)
a.Set(name='ALLNODES', nodes=inst.nodes)
a.Set(name='BASE', nodes=inst.nodes.getByBoundingBox(yMax=tol))
a.Set(name='TOP', nodes=inst.nodes.getByBoundingBox(yMin=H - tol))
if three_d:
    a.Set(name='ALL', cells=inst.cells)
    a.Surface(name='TOP', side1Faces=inst.faces.getByBoundingBox(yMin=H - tol))
else:
    a.Set(name='ALL', faces=inst.faces)
    a.Surface(name='TOP', side1Edges=inst.edges.getByBoundingBox(yMin=H - tol))
m.VoidsRatio(name='VoidRatio', region=a.sets['ALL'], voidsRatio1=1.0)

# The surcharge goes on in a first, very short consolidation step of ONE increment (undrained response, pore
# pressure jumps to q); only then the automatic time stepping with its pore-pressure tolerance UTOL takes over.
# (Applied inside a UTOL-controlled step, the jump of 100 kPa can never satisfy the tolerance and the first
# increment is cut back until the analysis stops.)
# Its length is the smallest time increment the mesh can resolve without pore-pressure oscillations,
# gamma_w h^2 / (6 M k) = 300 s for h = 0.5 m (Vermeer & Verruijt); 300 s are 0.2 % of the time to Tv = 0.2.
# (With 1 s, linear elements such as C3D8P give 70 / 130 kPa instead of 100 kPa after loading and the next step
# cannot satisfy UTOL either.)
T_LOAD = 300.0
m.SoilsStep(name='Load', previous='Initial', timePeriod=T_LOAD, timeIncrementationMethod=FIXED, initialInc=T_LOAD,
            maxNumInc=10, creep=OFF, amplitude=STEP, utol=None)
if VARIANT == 'big_increments':
    m.SoilsStep(name='Consol1', previous='Load', timePeriod=T_TV - T_LOAD, timeIncrementationMethod=FIXED,
                initialInc=(T_TV - T_LOAD) / 2.0, maxNumInc=100, creep=OFF, amplitude=STEP, utol=None)
else:
    m.SoilsStep(name='Consol1', previous='Load', timePeriod=T_TV - T_LOAD, initialInc=400.0, minInc=1.0,
                maxInc=T_TV / 50.0, maxNumInc=5000, utol=10.0, creep=OFF,
                amplitude=RAMP if VARIANT == 'ramp_load' else STEP)
m.SoilsStep(name='Consol2', previous='Consol1', timePeriod=T_END - T_TV, initialInc=T_TV / 50.0, minInc=1.0,
            maxInc=(T_END - T_TV) / 40.0, maxNumInc=5000, utol=10.0, creep=OFF, amplitude=STEP)
load_step = 'Consol1' if VARIANT == 'ramp_load' else 'Load'
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'POR', 'VOIDR'), frequency=1)

if VARIANT == 'lateral_free':
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u1=0.0, u2=0.0)
elif three_d:
    m.DisplacementBC(name='Lateral', createStepName='Initial', region=a.sets['ALLNODES'], u1=0.0, u3=0.0)
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u2=0.0)
else:
    m.DisplacementBC(name='Lateral', createStepName='Initial', region=a.sets['ALLNODES'], u1=0.0)
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u2=0.0)
if VARIANT != 'no_drainage':
    m.PorePressureBC(name='DrainTop', createStepName='Load', region=a.sets['TOP'], magnitude=0.0)
    if VARIANT == 'drained_both':
        m.PorePressureBC(name='DrainBase', createStepName='Load', region=a.sets['BASE'], magnitude=0.0)
m.Pressure(name='Surcharge', createStepName=load_step, region=a.surfaces['TOP'], magnitude=Q)

job = mdb.Job(name=JOB, model='Consol', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
asm = odb.rootAssembly
f1 = odb.steps['Consol1'].frames[-1]
f2 = odb.steps['Consol2'].frames[-1]
u_base = float(f1.fieldOutputs['POR'].getSubset(region=asm.nodeSets['BASE']).values[0].data)
u_peak = max([float(fr.fieldOutputs['POR'].getSubset(region=asm.nodeSets['BASE']).values[0].data)
              for name in ('Load', 'Consol1') for fr in odb.steps[name].frames])
s_tv = abs(float(f1.fieldOutputs['U'].getSubset(region=asm.nodeSets['TOP']).values[0].data[1]))
s_end = abs(float(f2.fieldOutputs['U'].getSubset(region=asm.nodeSets['TOP']).values[0].data[1]))
u_end = float(f2.fieldOutputs['POR'].getSubset(region=asm.nodeSets['BASE']).values[0].data)
n1, n2 = len(odb.steps['Consol1'].frames), len(odb.steps['Consol2'].frames)
odb.close()
sys.__stdout__.write('d02 %s: t(Tv=0.2) = %.1f s  u_base = %.3f kPa (largest %.3f)  settlement %.6f m | end: '
                     'settlement %.6f m, u_base %.4f kPa | frames %d + %d\n' % (
                         VARIANT, T_TV, u_base, u_peak, s_tv, s_end, u_end, n1, n2))

result = {'excess_pore_pressure_base_kPa': u_base, 'settlement_at_tv_m': s_tv, 'final_settlement_m': s_end}
if VARIANT == 'total_reported':
    result['excess_pore_pressure_base_kPa'] = u_base + 9.8 * H
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
