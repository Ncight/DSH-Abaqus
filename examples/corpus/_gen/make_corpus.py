# -*- coding: utf-8 -*-
# Corpus generator for the INP preview tool.  Abaqus/CAE 2021, Python 2.7.
#
#   cd E:\DSH-Abaqus\examples\corpus\_gen\work
#   abaqus cae noGUI=..\make_corpus.py -- [model names ...]
#
# Every model writes <name>.inp into the current directory and adds an entry to cae_expected.json
# holding what CAE itself reports (node / element counts per instance, element types, bounding box,
# coordinate checksums and a sample of assembly-frame node coordinates).  The preview test runner
# compares the parser against those numbers.
from abaqus import *
from abaqusConstants import *
from caeModules import *
import regionToolset
import mesh
import json
import os
import sys
import traceback

TOL = 1.0e-6
EXPECT_FILE = 'cae_expected.json'
LOG = open('make_corpus.log', 'a')


def log(msg):
    LOG.write(msg + '\n')
    LOG.flush()


def load_expected():
    if os.path.isfile(EXPECT_FILE):
        f = open(EXPECT_FILE)
        try:
            return json.load(f)
        finally:
            f.close()
    return {}


EXPECT = load_expected()


def finish(m, job, flat=False, extra=None):
    """write the INP and record CAE's own view of the mesh"""
    a = m.rootAssembly
    a.regenerate()
    if flat:
        m.setValues(noPartsInputFile=ON)
    j = mdb.Job(name=job, model=m.name, memory=2, memoryUnits=GIGA_BYTES)
    j.writeInput(consistencyChecking=OFF)
    res = {'model': m.name, 'flat': bool(flat), 'instances': {}, 'meshNodes': 0, 'elements': 0, 'types': {},
           'refPoints': len(a.referencePoints), 'sample': []}
    lo = [1e300, 1e300, 1e300]
    hi = [-1e300, -1e300, -1e300]
    ssum = [0.0, 0.0, 0.0]
    ssq = 0.0
    for name in a.instances.keys():
        inst = a.instances[name]
        nodes = inst.nodes
        elems = inst.elements
        nn = len(nodes)
        ne = len(elems)
        rp = len(inst.referencePoints)
        res['instances'][name] = {'nodes': nn, 'elements': ne, 'refPoints': rp, 'part': inst.partName}
        res['meshNodes'] += nn
        res['elements'] += ne
        res['refPoints'] += rp
        stride = max(1, nn // 300)
        for i in range(nn):
            c = nodes[i].coordinates
            for k in range(3):
                v = float(c[k])
                if v < lo[k]:
                    lo[k] = v
                if v > hi[k]:
                    hi[k] = v
                ssum[k] += v
                ssq += v * v
            if i % stride == 0:
                res['sample'].append([name, int(nodes[i].label), float(c[0]), float(c[1]), float(c[2])])
        for i in range(ne):
            t = str(elems[i].type)
            res['types'][t] = res['types'].get(t, 0) + 1
    res['bbox'] = [lo, hi] if res['meshNodes'] else None
    res['coordSum'] = ssum
    res['coordSumSq'] = ssq
    if extra:
        res.update(extra)
    EXPECT[job + '.inp'] = res
    f = open(EXPECT_FILE, 'w')
    json.dump(EXPECT, f, indent=1, sort_keys=True)
    f.close()
    log('OK   %s: %d nodes, %d elements, %s' % (job, res['meshNodes'], res['elements'], res['types']))


# ------------------------------------------------------------------ small helpers
def block_part(m, name, dx, dy, dz):
    s = m.ConstrainedSketch(name='sk_' + name, sheetSize=10.0)
    s.rectangle(point1=(0.0, 0.0), point2=(dx, dy))
    p = m.Part(name=name, dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=dz)
    return p


def solid_section(m, p, sec, mat):
    m.HomogeneousSolidSection(name=sec, material=mat)
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName=sec)


def material(m, name, e, nu, rho):
    mat = m.Material(name=name)
    mat.Elastic(table=((e, nu),))
    mat.Density(table=((rho,),))
    return mat


def hex_mesh(p, size, code=C3D8R, lib=STANDARD):
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=code, elemLibrary=lib),))
    p.seedPart(size=size)
    p.generateMesh()


def contact_prop(m, name='IntProp', mu=0.3):
    cp = m.ContactProperty(name)
    cp.TangentialBehavior(formulation=PENALTY, table=((mu,),), fraction=0.005, maximumElasticSlip=FRACTION)
    cp.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON)
    return cp


# ------------------------------------------------------------------ clean models
def shell_roof():
    """Scordelis-Lo style cylindrical roof, S4R on one half and S3 on the other. SI (N, m, kg)."""
    import math
    m = mdb.Model(name='ShellRoof')
    R, L, half = 2.5, 5.0, math.radians(40.0)
    x1, y1 = R * math.sin(half), R * math.cos(half)
    s = m.ConstrainedSketch(name='arc', sheetSize=10.0)
    s.ArcByCenterEnds(center=(0.0, 0.0), point1=(-x1, y1), point2=(x1, y1), direction=CLOCKWISE)
    p = m.Part(name='Roof', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseShellExtrude(sketch=s, depth=L)
    dp = p.DatumPlaneByPrincipalPlane(principalPlane=XYPLANE, offset=L / 2.0)
    p.PartitionFaceByDatumPlane(datumPlane=p.datums[dp.id], faces=p.faces)
    material(m, 'Concrete-C30', 3.0e10, 0.2, 2500.0)
    m.HomogeneousShellSection(name='RoofSec', material='Concrete-C30', thickness=0.05)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='RoofSec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=S4R, elemLibrary=STANDARD),
                                                    mesh.ElemType(elemCode=S3, elemLibrary=STANDARD)))
    p.setMeshControls(regions=p.faces.getByBoundingBox(zMin=L / 2.0 - TOL), elemShape=TRI)
    p.seedPart(size=0.25)
    p.generateMesh()
    a = m.rootAssembly
    inst = a.Instance(name='Roof-1', part=p, dependent=ON)
    a.Set(name='Diaphragms', edges=inst.edges.getByBoundingBox(zMax=TOL) + inst.edges.getByBoundingBox(zMin=L - TOL))
    a.Set(name='PinCorner', vertices=inst.vertices.getByBoundingBox(zMax=TOL, xMax=-x1 + TOL))
    a.Surface(name='RoofTop', side2Faces=inst.faces)      # side 2 = the outer (upper) side of this shell: snow presses down
    m.StaticStep(name='Load', previous='Initial')
    m.DisplacementBC(name='Diaphragm', createStepName='Initial', region=a.sets['Diaphragms'], u1=SET, u2=SET)
    m.DisplacementBC(name='AxialPin', createStepName='Initial', region=a.sets['PinCorner'], u3=SET)
    m.Gravity(name='SelfWeight', createStepName='Load', comp2=-9.81)
    m.Pressure(name='Snow', createStepName='Load', region=a.surfaces['RoofTop'], magnitude=1500.0)
    finish(m, 'shell_roof_s4r_s3')


def frame_truss():
    """3-D portal frame, B31 members and T3D2 braces. mm-t-s (N, mm, MPa)."""
    m = mdb.Model(name='Frame')
    W, H, D = 4000.0, 3000.0, 3000.0
    P = {'a0': (0, 0, 0), 'b0': (W, 0, 0), 'a1': (0, H, 0), 'b1': (W, H, 0),
         'c0': (0, 0, D), 'd0': (W, 0, D), 'c1': (0, H, D), 'd1': (W, H, D)}
    cols = [('a0', 'a1'), ('b0', 'b1'), ('c0', 'c1'), ('d0', 'd1')]
    xbeams = [('a1', 'b1'), ('c1', 'd1')]
    zbeams = [('a1', 'c1'), ('b1', 'd1')]
    braces = [('a0', 'b1'), ('c0', 'd1')]
    p = m.Part(name='Frame', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    pts = tuple((tuple(float(v) for v in P[i]), tuple(float(v) for v in P[j]))
                for i, j in cols + xbeams + zbeams + braces)
    p.WirePolyLine(points=pts, mergeType=IMPRINT, meshable=ON)

    def edges_of(pairs):
        mids = tuple((tuple((P[i][k] + P[j][k]) / 2.0 for k in range(3)),) for i, j in pairs)
        return p.edges.findAt(*mids)

    material(m, 'Steel-Q345', 2.06e5, 0.3, 7.85e-9)
    m.RectangularProfile(name='Rect200x300', a=200.0, b=300.0)
    m.BeamSection(name='BeamSec', integration=DURING_ANALYSIS, poissonRatio=0.0, profile='Rect200x300',
                  material='Steel-Q345', temperatureVar=LINEAR, consistentMassMatrix=False)
    m.TrussSection(name='BraceSec', material='Steel-Q345', area=1200.0)
    e_xy = edges_of(cols + xbeams)
    e_z = edges_of(zbeams)
    e_br = edges_of(braces)
    p.Set(name='Members', edges=e_xy + e_z)
    p.Set(name='Braces', edges=e_br)
    p.SectionAssignment(region=p.sets['Members'], sectionName='BeamSec')
    p.SectionAssignment(region=p.sets['Braces'], sectionName='BraceSec')
    p.assignBeamSectionOrientation(region=regionToolset.Region(edges=e_xy), method=N1_COSINES, n1=(0.0, 0.0, -1.0))
    p.assignBeamSectionOrientation(region=regionToolset.Region(edges=e_z), method=N1_COSINES, n1=(1.0, 0.0, 0.0))
    p.setElementType(regions=(e_xy + e_z,), elemTypes=(mesh.ElemType(elemCode=B31, elemLibrary=STANDARD),))
    p.setElementType(regions=(e_br,), elemTypes=(mesh.ElemType(elemCode=T3D2, elemLibrary=STANDARD),))
    p.seedEdgeBySize(edges=e_xy + e_z, size=500.0, constraint=FINER)
    p.seedEdgeByNumber(edges=e_br, number=1, constraint=FIXED)
    p.generateMesh()
    a = m.rootAssembly
    inst = a.Instance(name='Frame-1', part=p, dependent=ON)
    a.Set(name='Bases', vertices=inst.vertices.findAt(*tuple((tuple(float(v) for v in P[k]),) for k in ('a0', 'b0', 'c0', 'd0'))))
    a.Set(name='TopLeft', vertices=inst.vertices.findAt(((0.0, H, 0.0),), ((0.0, H, D),)))
    m.StaticStep(name='Push', previous='Initial')
    m.EncastreBC(name='Fixed', createStepName='Initial', region=a.sets['Bases'])
    m.ConcentratedForce(name='Wind', createStepName='Push', region=a.sets['TopLeft'], cf1=5.0e4)
    m.Gravity(name='SelfWeight', createStepName='Push', comp2=-9810.0)
    finish(m, 'frame_b31_t3d2')


def plate_hole():
    """Quarter plate with a hole, plane stress CPS4R + CPS3. mm-t-s."""
    m = mdb.Model(name='PlateHole')
    s = m.ConstrainedSketch(name='plate', sheetSize=400.0)
    s.Line(point1=(20.0, 0.0), point2=(100.0, 0.0))
    s.Line(point1=(100.0, 0.0), point2=(100.0, 100.0))
    s.Line(point1=(100.0, 100.0), point2=(0.0, 100.0))
    s.Line(point1=(0.0, 100.0), point2=(0.0, 20.0))
    s.ArcByCenterEnds(center=(0.0, 0.0), point1=(0.0, 20.0), point2=(20.0, 0.0), direction=CLOCKWISE)
    p = m.Part(name='Plate', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    dp = p.DatumPlaneByPrincipalPlane(principalPlane=YZPLANE, offset=60.0)
    p.PartitionFaceByDatumPlane(datumPlane=p.datums[dp.id], faces=p.faces)
    material(m, 'Aluminium', 7.0e4, 0.33, 2.7e-9)
    m.HomogeneousSolidSection(name='PlateSec', material='Aluminium', thickness=2.0)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='PlateSec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD),
                                                    mesh.ElemType(elemCode=CPS3, elemLibrary=STANDARD)))
    p.setMeshControls(regions=p.faces.getByBoundingBox(xMin=60.0 - TOL), elemShape=TRI)
    p.seedPart(size=5.0)
    p.generateMesh()
    a = m.rootAssembly
    inst = a.Instance(name='Plate-1', part=p, dependent=ON)
    a.Set(name='SymX', edges=inst.edges.getByBoundingBox(xMax=TOL))
    a.Set(name='SymY', edges=inst.edges.getByBoundingBox(yMax=TOL))
    a.Surface(name='PullEdge', side1Edges=inst.edges.getByBoundingBox(xMin=100.0 - TOL))
    m.StaticStep(name='Pull', previous='Initial')
    m.XsymmBC(name='SymX', createStepName='Initial', region=a.sets['SymX'])
    m.YsymmBC(name='SymY', createStepName='Initial', region=a.sets['SymY'])
    m.Pressure(name='Tension', createStepName='Pull', region=a.surfaces['PullEdge'], magnitude=-100.0)
    finish(m, 'plate_hole_cps4r_cps3')


def axisym_cylinder():
    """Thick-walled cylinder under internal pressure, CAX4R. m-t-s (kN, m, kPa, t/m3)."""
    m = mdb.Model(name='AxiCyl')
    s = m.ConstrainedSketch(name='wall', sheetSize=2.0)
    s.sketchOptions.setValues(viewStyle=AXISYM)
    s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))
    s.rectangle(point1=(0.1, 0.0), point2=(0.2, 0.5))
    p = m.Part(name='Wall', dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    material(m, 'Steel', 2.06e8, 0.3, 7.85)
    m.HomogeneousSolidSection(name='WallSec', material='Steel', thickness=None)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='WallSec')
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CAX4R, elemLibrary=STANDARD),
                                                    mesh.ElemType(elemCode=CAX3, elemLibrary=STANDARD)))
    p.seedPart(size=0.0125)
    p.generateMesh()
    a = m.rootAssembly
    a.DatumCsysByThreePoints(coordSysType=CYLINDRICAL, origin=(0.0, 0.0, 0.0), point1=(1.0, 0.0, 0.0),
                             point2=(0.0, 0.0, -1.0))
    inst = a.Instance(name='Wall-1', part=p, dependent=ON)
    a.Set(name='Bottom', edges=inst.edges.getByBoundingBox(yMax=TOL))
    a.Surface(name='Bore', side1Edges=inst.edges.getByBoundingBox(xMax=0.1 + TOL))
    m.StaticStep(name='Pressurize', previous='Initial')
    m.DisplacementBC(name='AxialFix', createStepName='Initial', region=a.sets['Bottom'], u2=SET)
    m.Pressure(name='Internal', createStepName='Pressurize', region=a.surfaces['Bore'], magnitude=1.0e4)
    finish(m, 'axisym_cylinder_cax4r')


def quad_solids():
    """C3D20R block with a C3D10 boss tied on top. SI (N, m, kg)."""
    m = mdb.Model(name='QuadSolids')
    pA = block_part(m, 'Base', 0.3, 0.1, 0.3)
    s = m.ConstrainedSketch(name='boss', sheetSize=1.0)
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(0.06, 0.0))
    pB = m.Part(name='Boss', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    pB.BaseSolidExtrude(sketch=s, depth=0.12)
    material(m, 'Steel', 2.06e11, 0.3, 7850.0)
    solid_section(m, pA, 'BaseSec', 'Steel')
    solid_section(m, pB, 'BossSec', 'Steel')
    hex_mesh(pA, 0.05, C3D20R)
    pB.setMeshControls(regions=pB.cells, elemShape=TET, technique=FREE)
    pB.setElementType(regions=(pB.cells,), elemTypes=(mesh.ElemType(elemCode=C3D20R, elemLibrary=STANDARD),
                                                      mesh.ElemType(elemCode=C3D15, elemLibrary=STANDARD),
                                                      mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD)))
    pB.seedPart(size=0.03)
    pB.generateMesh()
    a = m.rootAssembly
    iA = a.Instance(name='Base-1', part=pA, dependent=ON)
    iB = a.Instance(name='Boss-1', part=pB, dependent=ON)
    # boss axis is the part Z axis: stand it up on the base (rotate -90 deg about X), then move to the top centre
    a.rotate(instanceList=('Boss-1',), axisPoint=(0.0, 0.0, 0.0), axisDirection=(1.0, 0.0, 0.0), angle=-90.0)
    a.translate(instanceList=('Boss-1',), vector=(0.15, 0.1, 0.15))
    a.Set(name='BaseBottom', faces=iA.faces.getByBoundingBox(yMax=TOL))
    a.Surface(name='BaseTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - TOL))
    a.Surface(name='BossBottom', side1Faces=iB.faces.getByBoundingBox(yMax=0.1 + TOL))
    a.Surface(name='BossTop', side1Faces=iB.faces.getByBoundingBox(yMin=0.22 - TOL))
    m.Tie(name='BossTie', master=a.surfaces['BaseTop'], slave=a.surfaces['BossBottom'])
    m.StaticStep(name='Load', previous='Initial')
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['BaseBottom'])
    m.Pressure(name='Press', createStepName='Load', region=a.surfaces['BossTop'], magnitude=2.0e6)
    finish(m, 'quad_solids_c3d10_c3d20r')


def two_blocks(m, gap):
    """lower block 0.4 x 0.1 x 0.4, upper block 0.2 x 0.1 x 0.2 sitting `gap` above it. m-t-s."""
    pA = block_part(m, 'Lower', 0.4, 0.1, 0.4)
    pB = block_part(m, 'Upper', 0.2, 0.1, 0.2)
    material(m, 'Steel', 2.06e8, 0.3, 7.85)
    material(m, 'Rubber', 1.0e4, 0.45, 1.2)
    solid_section(m, pA, 'LowerSec', 'Steel')
    solid_section(m, pB, 'UpperSec', 'Rubber')
    hex_mesh(pA, 0.05)
    hex_mesh(pB, 0.04)
    a = m.rootAssembly
    iA = a.Instance(name='Lower-1', part=pA, dependent=ON)
    iB = a.Instance(name='Upper-1', part=pB, dependent=ON)
    a.translate(instanceList=('Upper-1',), vector=(0.1, 0.1 + gap, 0.1))
    a.Set(name='LowerBottom', faces=iA.faces.getByBoundingBox(yMax=TOL))
    a.Set(name='UpperTop', faces=iB.faces.getByBoundingBox(yMin=0.2 + gap - TOL))
    a.Surface(name='LowerTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - TOL))
    a.Surface(name='UpperBottom', side1Faces=iB.faces.getByBoundingBox(yMax=0.1 + gap + TOL))
    a.Surface(name='UpperTopSurf', side1Faces=iB.faces.getByBoundingBox(yMin=0.2 + gap - TOL))
    return a, iA, iB


def contact_pair(job='contact_pair_blocks', gap=0.001):
    m = mdb.Model(name='ContactPair' if job == 'contact_pair_blocks' else job)
    a, iA, iB = two_blocks(m, gap)
    contact_prop(m)
    m.StaticStep(name='Press', previous='Initial', nlgeom=ON, initialInc=0.1)
    m.SurfaceToSurfaceContactStd(name='BlockContact', createStepName='Initial', master=a.surfaces['LowerTop'],
                                 slave=a.surfaces['UpperBottom'], sliding=FINITE, interactionProperty='IntProp')
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['LowerBottom'])
    m.DisplacementBC(name='PushDown', createStepName='Press', region=a.sets['UpperTop'], u1=0.0, u2=-0.003, u3=0.0)
    finish(m, job)


def general_contact():
    m = mdb.Model(name='GeneralContact')
    a, iA, iB = two_blocks(m, 0.0)
    contact_prop(m)
    m.StaticStep(name='Press', previous='Initial', nlgeom=ON, initialInc=0.1)
    m.ContactStd(name='AllContact', createStepName='Initial')
    m.interactions['AllContact'].includedPairs.setValuesInStep(stepName='Initial', useAllstar=ON)
    m.interactions['AllContact'].contactPropertyAssignments.appendInStep(
        stepName='Initial', assignments=((GLOBAL, SELF, 'IntProp'),))
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['LowerBottom'])
    m.DisplacementBC(name='Guide', createStepName='Initial', region=a.sets['UpperTop'], u1=0.0, u3=0.0)
    m.Pressure(name='Press', createStepName='Press', region=a.surfaces['UpperTopSurf'], magnitude=200.0)
    finish(m, 'general_contact_blocks')


def rigid_analytical():
    """block indented by an extruded analytical rigid punch (rotated instance) and a revolved one. m-t-s."""
    m = mdb.Model(name='RigidAnalytical')
    pA = block_part(m, 'Block', 0.6, 0.1, 0.3)
    material(m, 'Aluminium', 7.0e7, 0.33, 2.7)
    solid_section(m, pA, 'BlockSec', 'Aluminium')
    hex_mesh(pA, 0.025)
    # extruded punch: two quarter arcs (r = 0.05) plus a straight flank on the +x side only
    s = m.ConstrainedSketch(name='punch', sheetSize=1.0)
    s.ArcByCenterEnds(center=(0.0, 0.05), point1=(-0.05, 0.05), point2=(0.0, 0.0), direction=COUNTERCLOCKWISE)
    s.ArcByCenterEnds(center=(0.0, 0.05), point1=(0.0, 0.0), point2=(0.05, 0.05), direction=COUNTERCLOCKWISE)
    s.Line(point1=(0.05, 0.05), point2=(0.07, 0.10))
    pC = m.Part(name='PunchCyl', dimensionality=THREE_D, type=ANALYTIC_RIGID_SURFACE)
    pC.AnalyticRigidSurfExtrude(sketch=s, depth=0.4)
    pC.ReferencePoint(point=(0.0, 0.05, 0.0))
    # revolved punch: sphere cap r = 0.04 with a conical shoulder
    s2 = m.ConstrainedSketch(name='ball', sheetSize=1.0)
    s2.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))
    s2.ArcByCenterEnds(center=(0.0, 0.04), point1=(0.0, 0.0), point2=(0.04, 0.04), direction=COUNTERCLOCKWISE)
    s2.Line(point1=(0.04, 0.04), point2=(0.05, 0.09))
    pS = m.Part(name='PunchBall', dimensionality=THREE_D, type=ANALYTIC_RIGID_SURFACE)
    pS.AnalyticRigidSurfRevolve(sketch=s2)
    pS.ReferencePoint(point=(0.0, 0.04, 0.0))
    a = m.rootAssembly
    iA = a.Instance(name='Block-1', part=pA, dependent=ON)
    iC = a.Instance(name='PunchCyl-1', part=pC, dependent=ON)
    iS = a.Instance(name='PunchBall-1', part=pS, dependent=ON)
    a.rotate(instanceList=('PunchCyl-1',), axisPoint=(0.0, 0.0, 0.0), axisDirection=(0.0, 1.0, 0.0), angle=30.0)
    a.translate(instanceList=('PunchCyl-1',), vector=(0.15, 0.101, 0.15))
    a.translate(instanceList=('PunchBall-1',), vector=(0.45, 0.101, 0.15))
    a.Set(name='BlockBottom', faces=iA.faces.getByBoundingBox(yMax=TOL))
    a.Surface(name='BlockTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - TOL))
    a.Surface(name='PunchCylSurf', side2Faces=iC.faces)
    a.Surface(name='PunchBallSurf', side1Faces=iS.faces)
    rc = iC.referencePoints
    rs = iS.referencePoints
    a.Set(name='RP-Cyl', referencePoints=(rc[rc.keys()[0]],))
    a.Set(name='RP-Ball', referencePoints=(rs[rs.keys()[0]],))
    contact_prop(m)
    m.StaticStep(name='Indent', previous='Initial', nlgeom=ON, initialInc=0.05)
    m.SurfaceToSurfaceContactStd(name='CylOnBlock', createStepName='Initial', master=a.surfaces['PunchCylSurf'],
                                 slave=a.surfaces['BlockTop'], sliding=FINITE, interactionProperty='IntProp')
    m.SurfaceToSurfaceContactStd(name='BallOnBlock', createStepName='Initial', master=a.surfaces['PunchBallSurf'],
                                 slave=a.surfaces['BlockTop'], sliding=FINITE, interactionProperty='IntProp')
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['BlockBottom'])
    m.DisplacementBC(name='CylDown', createStepName='Indent', region=a.sets['RP-Cyl'],
                     u1=0.0, u2=-0.004, u3=0.0, ur1=0.0, ur2=0.0, ur3=0.0)
    m.DisplacementBC(name='BallDown', createStepName='Indent', region=a.sets['RP-Ball'],
                     u1=0.0, u2=-0.004, u3=0.0, ur1=0.0, ur2=0.0, ur3=0.0)
    extra = {'analytic': {'PunchCyl-1': {'type': 'CYLINDER', 'translate': [0.15, 0.101, 0.15], 'rotY': 30.0},
                          'PunchBall-1': {'type': 'REVOLUTION', 'translate': [0.45, 0.101, 0.15]}}}
    finish(m, 'rigid_analytical_punches', extra=extra)


def explicit_impact():
    """Taylor bar hitting a discrete rigid wall, Abaqus/Explicit, general contact. SI (N, m, kg)."""
    m = mdb.Model(name='ExplicitImpact')
    s = m.ConstrainedSketch(name='bar', sheetSize=1.0)
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(0.01, 0.0))
    pB = m.Part(name='Bar', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    pB.BaseSolidExtrude(sketch=s, depth=0.06)
    mat = material(m, 'Copper', 1.1e11, 0.34, 8960.0)
    mat.Plastic(table=((9.0e7, 0.0), (2.5e8, 0.3)))
    solid_section(m, pB, 'BarSec', 'Copper')
    pB.setElementType(regions=(pB.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=EXPLICIT),
                                                      mesh.ElemType(elemCode=C3D6, elemLibrary=EXPLICIT)))
    pB.seedPart(size=0.003)
    pB.generateMesh()
    s2 = m.ConstrainedSketch(name='wall', sheetSize=1.0)
    s2.rectangle(point1=(-0.04, -0.04), point2=(0.04, 0.04))
    pW = m.Part(name='Wall', dimensionality=THREE_D, type=DISCRETE_RIGID_SURFACE)
    pW.BaseShell(sketch=s2)
    pW.ReferencePoint(point=(0.0, 0.0, 0.0))
    pW.setElementType(regions=(pW.faces,), elemTypes=(mesh.ElemType(elemCode=R3D4, elemLibrary=EXPLICIT),
                                                      mesh.ElemType(elemCode=R3D3, elemLibrary=EXPLICIT)))
    pW.seedPart(size=0.01)
    pW.generateMesh()
    a = m.rootAssembly
    iB = a.Instance(name='Bar-1', part=pB, dependent=ON)
    iW = a.Instance(name='Wall-1', part=pW, dependent=ON)
    a.translate(instanceList=('Bar-1',), vector=(0.0, 0.0, 0.0005))
    a.Set(name='BarAll', cells=iB.cells)
    rw = iW.referencePoints
    a.Set(name='WallRP', referencePoints=(rw[rw.keys()[0]],))
    contact_prop(m, mu=0.1)
    m.ExplicitDynamicsStep(name='Impact', previous='Initial', timePeriod=8.0e-5)
    m.ContactExp(name='AllContact', createStepName='Initial')
    m.interactions['AllContact'].includedPairs.setValuesInStep(stepName='Initial', useAllstar=ON)
    m.interactions['AllContact'].contactPropertyAssignments.appendInStep(
        stepName='Initial', assignments=((GLOBAL, SELF, 'IntProp'),))
    m.EncastreBC(name='WallFixed', createStepName='Initial', region=a.sets['WallRP'])
    m.Velocity(name='V0', region=a.sets['BarAll'], velocity1=0.0, velocity2=0.0, velocity3=-190.0, omega=0.0)
    finish(m, 'explicit_taylor_bar')


def flat_noparts():
    """the demo beam + tied pad, written without parts / instances. m-t-s."""
    m = mdb.Model(name='FlatDemo')
    pA = block_part(m, 'Beam', 0.2, 0.1, 1.0)
    pB = block_part(m, 'Pad', 0.2, 0.05, 0.3)
    material(m, 'Steel', 2.06e8, 0.3, 7.85)
    material(m, 'Rubber', 1.0e4, 0.45, 1.2)
    solid_section(m, pA, 'SecSteel', 'Steel')
    solid_section(m, pB, 'SecRubber', 'Rubber')
    hex_mesh(pA, 0.025)
    hex_mesh(pB, 0.025)
    a = m.rootAssembly
    iA = a.Instance(name='Beam-1', part=pA, dependent=ON)
    iB = a.Instance(name='Pad-1', part=pB, dependent=ON)
    a.translate(instanceList=('Pad-1',), vector=(0.0, 0.1, 0.35))
    a.Set(name='FixEnd', faces=iA.faces.getByBoundingBox(zMax=TOL))
    a.Set(name='TipCorners', vertices=iA.vertices.getByBoundingBox(zMin=1.0 - TOL, yMin=0.1 - TOL))
    a.Surface(name='PadTop', side1Faces=iB.faces.getByBoundingBox(yMin=0.15 - TOL))
    a.Surface(name='BeamTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - TOL))
    a.Surface(name='PadBottom', side1Faces=iB.faces.getByBoundingBox(yMax=0.1 + TOL))
    m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'])
    m.StaticStep(name='Load', previous='Initial')
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FixEnd'])
    m.Pressure(name='PadPressure', createStepName='Load', region=a.surfaces['PadTop'], magnitude=500.0)
    m.ConcentratedForce(name='TipForce', createStepName='Load', region=a.sets['TipCorners'], cf2=-10.0)
    finish(m, 'flat_noparts_beam_pad', flat=True)


def rotated_instance():
    """three instances of one L bracket: as built, translated + rotated, and rotated twice. mm-t-s."""
    m = mdb.Model(name='Rotated')
    s = m.ConstrainedSketch(name='L', sheetSize=400.0)
    pts = ((0.0, 0.0), (120.0, 0.0), (120.0, 20.0), (20.0, 20.0), (20.0, 80.0), (0.0, 80.0), (0.0, 0.0))
    for i in range(len(pts) - 1):
        s.Line(point1=pts[i], point2=pts[i + 1])
    p = m.Part(name='Bracket', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=40.0)
    material(m, 'Steel', 2.06e5, 0.3, 7.85e-9)
    solid_section(m, p, 'BracketSec', 'Steel')
    hex_mesh(p, 10.0)
    p.Set(name='Foot', faces=p.faces.getByBoundingBox(yMax=TOL))
    p.Surface(name='Web', side1Faces=p.faces.getByBoundingBox(xMax=TOL))
    a = m.rootAssembly
    a.Instance(name='Bracket-1', part=p, dependent=ON)
    a.Instance(name='Bracket-2', part=p, dependent=ON)
    a.Instance(name='Bracket-3', part=p, dependent=ON)
    a.rotate(instanceList=('Bracket-2',), axisPoint=(10.0, 0.0, 0.0), axisDirection=(1.0, 1.0, 0.5), angle=37.0)
    a.translate(instanceList=('Bracket-2',), vector=(200.0, 30.0, -50.0))
    a.rotate(instanceList=('Bracket-3',), axisPoint=(0.0, 0.0, 0.0), axisDirection=(0.0, 0.0, 1.0), angle=90.0)
    a.rotate(instanceList=('Bracket-3',), axisPoint=(0.0, 0.0, 0.0), axisDirection=(1.0, 0.0, 0.0), angle=-60.0)
    a.translate(instanceList=('Bracket-3',), vector=(-150.0, 0.0, 100.0))
    m.StaticStep(name='Load', previous='Initial')
    for k in ('1', '2', '3'):
        inst = a.instances['Bracket-' + k]
        m.EncastreBC(name='Fix-' + k, createStepName='Initial', region=inst.sets['Foot'])
        m.Pressure(name='Press-' + k, createStepName='Load', region=inst.surfaces['Web'], magnitude=1.5)
    finish(m, 'rotated_instances')


# ------------------------------------------------------------------ models that must trigger a check
def tie_demo(job, shift=(0.0, 0.1, 0.35), tol=None):
    m = mdb.Model(name=job)
    pA = block_part(m, 'Beam', 0.2, 0.1, 1.0)
    pB = block_part(m, 'Pad', 0.2, 0.05, 0.3)
    material(m, 'Steel', 2.06e8, 0.3, 7.85)
    material(m, 'Rubber', 1.0e4, 0.45, 1.2)
    solid_section(m, pA, 'SecSteel', 'Steel')
    solid_section(m, pB, 'SecRubber', 'Rubber')
    hex_mesh(pA, 0.025)
    hex_mesh(pB, 0.025)
    a = m.rootAssembly
    iA = a.Instance(name='Beam-1', part=pA, dependent=ON)
    iB = a.Instance(name='Pad-1', part=pB, dependent=ON)
    a.translate(instanceList=('Pad-1',), vector=shift)
    y0 = shift[1]
    a.Set(name='FixEnd', faces=iA.faces.getByBoundingBox(zMax=TOL))
    a.Surface(name='PadTop', side1Faces=iB.faces.getByBoundingBox(yMin=y0 + 0.05 - TOL))
    a.Surface(name='BeamTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - TOL))
    a.Surface(name='PadBottom', side1Faces=iB.faces.getByBoundingBox(yMax=y0 + TOL))
    if tol is None:
        m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'])
    elif tol > 0:
        m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'],
              positionToleranceMethod=SPECIFIED, positionTolerance=tol, adjust=ON)
    m.StaticStep(name='Load', previous='Initial')
    m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FixEnd'])
    m.Pressure(name='PadPressure', createStepName='Load', region=a.surfaces['PadTop'], magnitude=500.0)
    finish(m, job)


def chk_tie_gap():
    tie_demo('chk_tie_gap', shift=(0.0, 0.104, 0.35))            # 4 mm above the beam, default tolerance


def chk_tie_overhang():
    tie_demo('chk_tie_overhang', shift=(0.1, 0.1, 0.35))         # half of the pad hangs over the side


def tie_tolerance_ok():
    tie_demo('tie_gap_within_tolerance', shift=(0.0, 0.104, 0.35), tol=0.01)   # clean: gap < given tolerance


def chk_adjacent_untied():
    tie_demo('chk_adjacent_untied', tol=-1.0)                    # pad sits on the beam, nothing joins them


def chk_contact_far():
    contact_pair(job='chk_contact_far', gap=3.0)


MODELS = [shell_roof, frame_truss, plate_hole, axisym_cylinder, quad_solids, contact_pair, general_contact,
          rigid_analytical, explicit_impact, flat_noparts, rotated_instance, chk_tie_gap, chk_tie_overhang,
          tie_tolerance_ok, chk_adjacent_untied, chk_contact_far]

wanted = [x for x in sys.argv if x in [f.__name__ for f in MODELS]]
for fn in MODELS:
    if wanted and fn.__name__ not in wanted:
        continue
    try:
        fn()
    except Exception:
        log('FAIL %s\n%s' % (fn.__name__, traceback.format_exc()))
LOG.close()
