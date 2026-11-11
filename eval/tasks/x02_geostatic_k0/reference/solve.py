# -*- coding: utf-8 -*-
"""Reference solution for x02_geostatic_k0  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Horizontal soil layer, 20 m thick, m-t-s units (kN, kPa, t/m3): E = 30000 kPa, nu = 0.3, rho = 1.8 t/m3, g = 9.8.
Lateral boundaries on rollers, base held vertically.  The in-situ state is prescribed as an initial stress field
(vertical stress -rho g depth, horizontal stress K0 times that, K0 = nu/(1-nu) = 0.4286) and brought into equilibrium
with gravity in a *GEOSTATIC step: no settlement.  Closed form: -352.8 kPa vertical and -151.2 kPa horizontal at the
base; without the initial stress the layer settles rho g H^2 / (2 M) = 0.0874 m (M = 40385 kPa).

REF_VARIANT (environment variable):
  default             2-D plane-strain strip 2 m x 20 m, CPE4 2 x 20, vertical axis Y, geostatic step    -> must PASS
  column3d            3-D column 1 x 1 x 20 m, C3D8R, vertical axis Z                                    -> must PASS
  static_step         as default, equilibrium found in an ordinary *STATIC step                          -> must PASS
  coarse              as default with only 4 elements over the depth (stress points far from the base)   -> must PASS
  gravity_only        gravity without initial stress: the layer settles 87 mm                            -> must FAIL
  no_gravity          initial stress but no gravity: the layer heaves and the stress vanishes            -> must FAIL
  wrong_k0            initial stress with K0 = 1.0 (still no displacement: the column is confined)       -> must FAIL
  stiff_gravity_only  gravity only, E entered as 3.0e7 ("Pa"): settlement 8.7e-5 m, no in-situ state     -> must FAIL
  si_units            correct procedure in N / Pa / kg (stresses 1000 times larger)                      -> must FAIL
  fake_report         correct model, vertical stress written to result.json in Pa                        -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'geostatic'
KNOWN = ('default', 'column3d', 'static_step', 'coarse', 'gravity_only', 'no_gravity', 'wrong_k0',
         'stiff_gravity_only', 'si_units', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

HGT, WID = 20.0, 2.0
E, NU, RHO, G = 30000.0, 0.3, 1.8, 9.8
if VARIANT == 'si_units':
    E, RHO = 3.0e7, 1800.0
elif VARIANT == 'stiff_gravity_only':
    E = 3.0e7
K0 = 1.0 if VARIANT == 'wrong_k0' else NU / (1.0 - NU)
SV_BASE = -RHO * G * HGT
N_Y = 4 if VARIANT == 'coarse' else 20
three_d = VARIANT == 'column3d'
with_stress = VARIANT not in ('gravity_only', 'stiff_gravity_only')
with_gravity = VARIANT != 'no_gravity'
tol = 1.0e-6

m = mdb.Model(name='Soil')
mat = m.Material(name='Soil')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)

if three_d:
    s = m.ConstrainedSketch(name='plan', sheetSize=10.0)
    s.rectangle(point1=(0.0, 0.0), point2=(1.0, 1.0))
    p = m.Part(name='Soil', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=HGT)
    m.HomogeneousSolidSection(name='Sec', material='Soil')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    p.seedPart(size=1.0, deviationFactor=0.1, minSizeFactor=0.1)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Soil-1', part=p, dependent=ON)
    a.Set(name='ALL', cells=inst.cells)
    a.Set(name='BASE', faces=inst.faces.getByBoundingBox(zMax=tol))
    a.Set(name='SIDES_X', faces=inst.faces.getByBoundingBox(xMax=tol) + inst.faces.getByBoundingBox(xMin=1.0 - tol))
    a.Set(name='SIDES_Y', faces=inst.faces.getByBoundingBox(yMax=tol) + inst.faces.getByBoundingBox(yMin=1.0 - tol))
else:
    s = m.ConstrainedSketch(name='strip', sheetSize=50.0)
    s.rectangle(point1=(0.0, 0.0), point2=(WID, HGT))
    p = m.Part(name='Soil', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    m.HomogeneousSolidSection(name='Sec', material='Soil', thickness=1.0)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        vertical = abs(v1[1] - v0[1]) > tol
        p.seedEdgeByNumber(edges=(e,), number=N_Y if vertical else 2, constraint=FIXED)
    p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Soil-1', part=p, dependent=ON)
    a.Set(name='ALL', faces=inst.faces)
    a.Set(name='BASE', edges=inst.edges.getByBoundingBox(yMax=tol))
    a.Set(name='SIDES_X', edges=inst.edges.getByBoundingBox(xMax=tol) + inst.edges.getByBoundingBox(xMin=WID - tol))

if VARIANT == 'static_step':
    m.StaticStep(name='Geo', previous='Initial', nlgeom=OFF)
else:
    m.GeostaticStep(name='Geo', previous='Initial')
m.DisplacementBC(name='SidesX', createStepName='Initial', region=a.sets['SIDES_X'], u1=0.0)
if three_d:
    m.DisplacementBC(name='SidesY', createStepName='Initial', region=a.sets['SIDES_Y'], u2=0.0)
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u3=0.0)
else:
    m.DisplacementBC(name='Base', createStepName='Initial', region=a.sets['BASE'], u2=0.0)
if with_stress:
    # vertical stress 0 at the surface (elevation 20) and -rho g H at the base (elevation 0), lateral coefficient K0
    m.GeostaticStress(name='InSitu', region=a.sets['ALL'], stressMag1=0.0, vCoord1=HGT, stressMag2=SV_BASE,
                      vCoord2=0.0, lateralCoeff1=K0, lateralCoeff2=K0)
if with_gravity:
    if three_d:
        m.Gravity(name='SelfWeight', createStepName='Geo', comp3=-G)
    else:
        m.Gravity(name='SelfWeight', createStepName='Geo', comp2=-G)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF'))

job = mdb.Job(name=JOB, model='Soil', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
frame = odb.steps['Geo'].frames[-1]
iv, ih = (2, 0) if three_d else (1, 0)              # index of the vertical / a horizontal stress component
u_max = max(math.sqrt(math.fsum(c * c for c in v.data)) for v in frame.fieldOutputs['U'].values)
base = frame.fieldOutputs['S'].getSubset(region=odb.rootAssembly.nodeSets['BASE'], position=ELEMENT_NODAL).values
sv_base = math.fsum(v.data[iv] for v in base) / len(base)      # stresses extrapolated to the base nodes
sh_base = math.fsum(v.data[ih] for v in base) / len(base)
odb.close()

result = {'max_displacement_m': float(u_max), 'vertical_stress_base_kPa': float(sv_base),
          'horizontal_stress_base_kPa': float(sh_base)}
if VARIANT == 'fake_report':
    result['vertical_stress_base_kPa'] = float(sv_base) * 1000.0
    result['horizontal_stress_base_kPa'] = float(sh_base) * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
