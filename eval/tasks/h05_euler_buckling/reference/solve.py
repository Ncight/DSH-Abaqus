# -*- coding: utf-8 -*-
"""Reference solution for h05_euler_buckling  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Linear eigenvalue buckling of a pinned-pinned column: L = 2 m, solid circular section r = 0.02 m, E = 2.1e11 Pa,
nu = 0.3.  Euler: Pcr = pi^2 E I / L^2 = 6.5113e4 N  (I = pi r^4 / 4).

REF_VARIANT (environment variable):
  default         3-D beam model (axis = Y), 20 B32, reference load 1000 N -> eigenvalue 65.1      -> must PASS
  unit_load       40 B33 elements, reference load 1 N -> eigenvalue = Pcr                          -> must PASS
  solid_pressure  C3D20R solid column (axis = Z), end faces tied to pinned reference points, the
                  reference load is a PRESSURE on the top face (resultant 1000 N)                   -> must PASS
  fixed_fixed     end rotations restrained (4 x Pcr)                                               -> must FAIL
  cantilever      base fixed, top free (Pcr / 4)                                                   -> must FAIL
  fudged_ref      cantilever model, but result.json claims a reference load of 4000 N (1000 N was
                  applied) so that eigenvalue x "reference load" equals the textbook value          -> must FAIL
  fake_report     correct model, critical load in result.json taken from the fixed-fixed formula   -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import re

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'buckle'
L, RAD = 2.0, 0.02
E, NU = 2.1e11, 0.3
AREA = math.pi * RAD ** 2
KNOWN = ('default', 'unit_load', 'solid_pressure', 'fixed_fixed', 'cantilever', 'fudged_ref', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)
P_REF = 1.0 if VARIANT == 'unit_load' else 1000.0
tol = 1.0e-6

m = mdb.Model(name='Column')
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
m.BuckleStep(name='Buckle', previous='Initial', numEigen=4, vectors=10, maxIterations=300)

if VARIANT == 'solid_pressure':
    s = m.ConstrainedSketch(name='section', sheetSize=1.0)
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(RAD, 0.0))
    p = m.Part(name='Column', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=L)
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')
    # 40 elements along the column, element size 0.008 m on the two end circles (about 1000 C3D20R in all)
    p.seedPart(size=L / 40.0, deviationFactor=0.1, minSizeFactor=0.1)
    for e in p.edges:
        zs = [p.vertices[i].pointOn[0][2] for i in e.getVertices()]
        if max(zs) - min(zs) < tol:                      # an end circle
            p.seedEdgeBySize(edges=(e,), size=0.008, deviationFactor=0.05, constraint=FINER)
    p.setMeshControls(regions=p.cells, elemShape=HEX, technique=SWEEP, algorithm=ADVANCING_FRONT)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D20R, elemLibrary=STANDARD),))
    p.generateMesh()
    if len(p.elements) > 3000:               # keep the reference small (a first version had 6750 elements)
        raise RuntimeError('solid column mesh has %d elements - refusing to run a model this large' % len(p.elements))
    inst = a.Instance(name='Column-1', part=p, dependent=ON)
    rp_b = a.ReferencePoint(point=(0.0, 0.0, 0.0))
    rp_t = a.ReferencePoint(point=(0.0, 0.0, L))
    a.Set(name='RP_BOTTOM', referencePoints=(a.referencePoints[rp_b.id],))
    a.Set(name='RP_TOP', referencePoints=(a.referencePoints[rp_t.id],))
    a.Surface(name='BOTTOM', side1Faces=inst.faces.getByBoundingBox(zMax=tol))
    a.Surface(name='TOP', side1Faces=inst.faces.getByBoundingBox(zMin=L - tol))
    for name, rp_set, surf in (('TieBottom', 'RP_BOTTOM', 'BOTTOM'), ('TieTop', 'RP_TOP', 'TOP')):
        m.Coupling(name=name, controlPoint=a.sets[rp_set], surface=a.surfaces[surf], influenceRadius=WHOLE_SURFACE,
                   couplingType=KINEMATIC, localCsys=None, u1=ON, u2=ON, u3=ON, ur1=ON, ur2=ON, ur3=ON)
    # pinned: translations held, bending rotations free; twist held at the bottom
    m.DisplacementBC(name='PinBottom', createStepName='Initial', region=a.sets['RP_BOTTOM'], u1=0.0, u2=0.0, u3=0.0,
                     ur3=0.0)
    m.DisplacementBC(name='PinTop', createStepName='Initial', region=a.sets['RP_TOP'], u1=0.0, u2=0.0)
    m.Pressure(name='Axial', createStepName='Buckle', region=a.surfaces['TOP'], magnitude=P_REF / AREA)
else:
    elem_code, n_el = (B33, 40) if VARIANT == 'unit_load' else (B32, 20)
    s = m.ConstrainedSketch(name='axis', sheetSize=5.0)
    s.Line(point1=(0.0, 0.0), point2=(0.0, L))
    p = m.Part(name='Column', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseWire(sketch=s)
    m.CircularProfile(name='Circ', r=RAD)
    m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Circ', material='Steel', poissonRatio=NU)
    region = regionToolset.Region(edges=p.edges)
    p.SectionAssignment(region=region, sectionName='Sec')
    p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    p.seedEdgeByNumber(edges=p.edges, number=n_el, constraint=FIXED)
    p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()
    inst = a.Instance(name='Column-1', part=p, dependent=ON)
    a.Set(name='BOTTOM', vertices=inst.vertices.getByBoundingBox(yMax=tol))
    a.Set(name='TOP', vertices=inst.vertices.getByBoundingBox(yMin=L - tol))
    if VARIANT in ('cantilever', 'fudged_ref'):
        m.EncastreBC(name='FixBottom', createStepName='Initial', region=a.sets['BOTTOM'])
    elif VARIANT == 'fixed_fixed':
        m.EncastreBC(name='FixBottom', createStepName='Initial', region=a.sets['BOTTOM'])
        m.DisplacementBC(name='FixTop', createStepName='Initial', region=a.sets['TOP'], u1=0.0, u3=0.0, ur1=0.0,
                         ur2=0.0, ur3=0.0)
    else:
        # pinned-pinned: translations held (the top may move axially), twist held at the bottom
        m.DisplacementBC(name='PinBottom', createStepName='Initial', region=a.sets['BOTTOM'], u1=0.0, u2=0.0, u3=0.0,
                         ur2=0.0)
        m.DisplacementBC(name='PinTop', createStepName='Initial', region=a.sets['TOP'], u1=0.0, u3=0.0)
    m.ConcentratedForce(name='Axial', createStepName='Buckle', region=a.sets['TOP'], cf2=-P_REF)

job = mdb.Job(name=JOB, model='Column', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing: eigenvalues of the buckle step ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
eig = []
for fr in odb.steps['Buckle'].frames:
    mo = re.search(r'EigenValue\s*=\s*([-+0-9.eE]+)', fr.description)
    if mo:
        eig.append(float(mo.group(1)))
odb.close()
positive = sorted(v for v in eig if v > 0.0)
critical = positive[0] * P_REF

result = {'critical_load_N': float(critical), 'reference_load_N': float(P_REF)}
if VARIANT == 'fudged_ref':
    result = {'critical_load_N': float(positive[0]) * 4000.0, 'reference_load_N': 4000.0}
elif VARIANT == 'fake_report':
    result['critical_load_N'] = 4.0 * math.pi ** 2 * E * (math.pi * RAD ** 4 / 4.0) / L ** 2
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
