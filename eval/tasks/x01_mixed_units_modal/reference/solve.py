# -*- coding: utf-8 -*-
"""Reference solution for x01_mixed_units_modal  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Simply supported steel beam, data given in mixed units: span 3000 mm, section 60 mm x 100 mm, E = 206 GPa, nu = 0.3,
density 7.85 g/cm3.  Both end sections are held transversely (both directions) and may rotate about both transverse
axes; the axial displacement is held at one end only.  Euler-Bernoulli: 15.49 Hz (bending about the weak axis,
depth 60 mm) and 25.81 Hz (strong axis, depth 100 mm).

3-D solid model: the transverse displacements are restrained on the whole end faces (that leaves the faces free to
rotate, because rotation of a cross-section only moves its points axially) and the axial displacement at ONE point,
the centroid of one end face.  Restraining the axial displacement of a whole face would clamp that end.

REF_VARIANT (environment variable):
  default           3-D solid, C3D20R 4 x 4 x 60, SI units (m, Pa, kg/m3)                       -> must PASS
  mm_tonne          the same solid model in mm - N - tonne (MPa, 7.85e-9 t/mm3)                 -> must PASS
  beam_b33          3-D beam model, 40 B33 elements, SI                                         -> must PASS
  density_kg_in_mm  mm - MPa geometry and modulus, density entered as 7850 (kg/m3)              -> must FAIL (x 1e-6)
  density_t_in_si   SI geometry and modulus, density entered as 7.85e-9 (t/mm3)                 -> must FAIL (x 1e6)
  clamped           both end faces fully fixed                                                  -> must FAIL
  axial_on_face     axial displacement restrained on a whole end face (that end is clamped)     -> must FAIL
  beam_2d           planar beam model: only one bending plane exists                            -> must FAIL
  coarse_c3d8r      C3D8R (CAE default), 2 x 4 x 60                                             -> must FAIL
  fake_report       correct model, result.json lists the two weak-axis modes 15.49 / 61.94      -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'ssbeam_modal'
KNOWN = ('default', 'mm_tonne', 'beam_b33', 'density_kg_in_mm', 'density_t_in_si', 'clamped', 'axial_on_face',
         'beam_2d', 'coarse_c3d8r', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

# data of the task in SI and in mm - N - tonne
if VARIANT in ('mm_tonne', 'density_kg_in_mm'):
    L, B, H, E, RHO = 3000.0, 60.0, 100.0, 206000.0, 7.85e-9
else:
    L, B, H, E, RHO = 3.0, 0.060, 0.100, 2.06e11, 7850.0
NU = 0.3
if VARIANT == 'density_kg_in_mm':
    RHO = 7850.0
elif VARIANT == 'density_t_in_si':
    RHO = 7.85e-9
tol = 1.0e-6 * L

m = mdb.Model(name='Beam')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
m.FrequencyStep(name='Freq', previous='Initial', eigensolver=LANCZOS, numEigen=6)

if VARIANT in ('beam_b33', 'beam_2d'):
    planar = VARIANT == 'beam_2d'
    s = m.ConstrainedSketch(name='axis', sheetSize=4.0 * L)
    s.Line(point1=(0.0, 0.0), point2=(L, 0.0))
    p = m.Part(name='Beam', dimensionality=TWO_D_PLANAR if planar else THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.RectangularProfile(name='Rect', a=B, b=H)
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=NU)
    region = regionToolset.Region(edges=p.edges)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    p.seedEdgeByNumber(edges=p.edges, number=40, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B23 if planar else B33,
                                                                  elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='END_A', vertices=inst.vertices.getByBoundingBox(xMax=tol))
    a.Set(name='END_B', vertices=inst.vertices.getByBoundingBox(xMin=L - tol))
    if planar:
        m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0)
        m.DisplacementBC(name='RollerB', createStepName='Initial', region=a.sets['END_B'], u2=0.0)
    else:
        # pinned / roller in both bending planes; the twist is held at one end
        m.DisplacementBC(name='PinA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0, u3=0.0,
                         ur1=0.0)
        m.DisplacementBC(name='RollerB', createStepName='Initial', region=a.sets['END_B'], u2=0.0, u3=0.0)
else:
    elem_code, counts = (C3D8R, (1, 2, 60)) if VARIANT == 'coarse_c3d8r' else (C3D20R, (2, 2, 60))
    s = m.ConstrainedSketch(name='section', sheetSize=4.0 * H)
    s.rectangle(point1=(-B / 2.0, -H / 2.0), point2=(B / 2.0, H / 2.0))
    p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=L)
    # two mid-planes: they put a vertex on the centroid of each end face (counts are per half width / half depth)
    p.PartitionCellByDatumPlane(cells=p.cells[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=YZPLANE, offset=0.0).id])
    p.PartitionCellByDatumPlane(cells=p.cells[:], datumPlane=p.datums[p.DatumPlaneByPrincipalPlane(
        principalPlane=XZPLANE, offset=0.0).id])
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    for e in p.edges:
        v0, v1 = [p.vertices[i].pointOn[0] for i in e.getVertices()]
        axis = max(range(3), key=lambda k: abs(v1[k] - v0[k]))
        p.seedEdgeByNumber(edges=(e,), number=counts[axis], constraint=FIXED)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Beam-1', part=p, dependent=ON)
    a.Set(name='END_A', faces=inst.faces.getByBoundingBox(zMax=tol))
    a.Set(name='END_B', faces=inst.faces.getByBoundingBox(zMin=L - tol))
    a.Set(name='CENTROID_A', vertices=inst.vertices.findAt(((0.0, 0.0, 0.0),)))
    if VARIANT == 'clamped':
        m.EncastreBC(name='FixA', createStepName='Initial', region=a.sets['END_A'])
        m.EncastreBC(name='FixB', createStepName='Initial', region=a.sets['END_B'])
    else:
        m.DisplacementBC(name='TransA', createStepName='Initial', region=a.sets['END_A'], u1=0.0, u2=0.0)
        m.DisplacementBC(name='TransB', createStepName='Initial', region=a.sets['END_B'], u1=0.0, u2=0.0)
        axial = a.sets['END_A'] if VARIANT == 'axial_on_face' else a.sets['CENTROID_A']
        m.DisplacementBC(name='Axial', createStepName='Initial', region=axial, u3=0.0)

job = mdb.Job(name=JOB, model='Beam', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: eigenfrequencies (cycles/s) from the ODB frames ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
freqs = sorted(float(fr.frequency) for fr in odb.steps['Freq'].frames if fr.mode > 0)
odb.close()

result = {'frequencies_Hz': freqs[:2]}
if VARIANT == 'fake_report':
    result = {'frequencies_Hz': [15.49, 61.94]}
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
