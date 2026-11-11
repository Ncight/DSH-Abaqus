# -*- coding: utf-8 -*-
"""Reference solution for t05_hertz_contact  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Elastic sphere R = 0.01 m (E=2.1e11 Pa, nu=0.3) pressed on a rigid frictionless plane with a total force of 500 N.
Hertz: contact radius a = 2.5329e-4 m, peak pressure p0 = 3.7211e9 Pa, approach a^2/R = 6.416e-6 m.

Model: axisymmetric lower half of the sphere (contact point at the origin, axis = Y), analytical rigid plane y = 0.
The equatorial plane is tied to a reference point (it stays plane, as between two platens) which carries the load.

Units and scale.  The contact zone needs elements of about 13 micrometres.  Drawn in metres that is only ~10 times
the geometric precision of Abaqus/CAE (1e-6): the free mesher then produces collapsed elements (three coincident
nodes) and the input processor stops with "area of 1 elements is zero".  The part is therefore drawn and meshed at
millimetre scale.  For the SI model the input file written by CAE is converted to metres (node coordinates and the
segments of the rigid surface are multiplied by 1e-3; material, load and boundary data are entered in N / Pa / m) and
the job runs from that file.  Variant mm_units keeps the millimetre model and uses the consistent N - mm - MPa system
(E = 2.1e5 MPa).  result.json is written in SI units in every case.

REF_VARIANT (environment variable):
  default          SI model, CAX4, element size a/20 in the contact zone, force control (CF2 = -500 N)  -> must PASS
  mm_units         the same model solved in N - mm - MPa (ODB in MPa / mm, result.json in SI)           -> must PASS
  disp_driven      SI model, the equatorial plane is pushed down by a prescribed displacement that is
                   calibrated with a trial job so that the contact force is 500 N                       -> must PASS
  cax4r            SI model with the CAE default element CAX4R                                          -> must PASS
  quarter3d        SI 3-D model of a quarter of the half sphere (two symmetry planes), C3D10 mesh
                   graded towards the contact point, 125 N on the quarter (reaction = F/4)              -> must PASS
  coarse           global element size R/20 = 0.5 mm, no refinement at the contact                      -> must FAIL
  per_radian_load  load entered "per radian": CF2 = -500/(2 pi) N (Abaqus wants the full circle)        -> must FAIL
  fake_report      correct model, peak pressure written to result.json in MPa                           -> must FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'hertz'
KNOWN = ('default', 'mm_units', 'disp_driven', 'cax4r', 'quarter3d', 'coarse', 'per_radian_load', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

R_M, E_PA, NU, FORCE = 0.01, 2.1e11, 0.3, 500.0
A_M = (3.0 * FORCE * R_M / (4.0 * E_PA / (1.0 - NU ** 2))) ** (1.0 / 3.0)      # Hertz contact radius, m
DELTA_M = A_M ** 2 / R_M                                                       # Hertz approach, m

LS = 1000.0                                        # CAE drawing units per metre: the mesh is made in millimetres
SOLVER_LEN = 1000.0 if VARIANT == 'mm_units' else 1.0       # length units of the solved model per metre
E_SOLVER = E_PA / SOLVER_LEN ** 2                  # Pa, or MPa in the N - mm - MPa system
R = R_M * LS                                       # lengths below are in CAE drawing units (mm)
A_C = A_M * LS
elem_code = CAX4R if VARIANT == 'cax4r' else CAX4
R_ZONE = 3.0 * A_C                                 # refined zone around the contact point
if VARIANT == 'coarse':
    H_CONTACT = H_FAR = R / 20.0
else:
    H_CONTACT, H_FAR = A_C / 20.0, R / 16.0
tol = 1.0e-6


def contact_and_step(m, a):
    prop = m.ContactProperty('Frictionless')
    prop.TangentialBehavior(formulation=FRICTIONLESS)
    prop.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON, constraintEnforcementMethod=DEFAULT)
    m.SurfaceToSurfaceContactStd(name='Contact', createStepName='Initial', master=a.surfaces['PLANE'],
                                 slave=a.surfaces['BALL'], sliding=FINITE, interactionProperty='Frictionless')
    m.StaticStep(name='Press', previous='Initial', nlgeom=OFF, initialInc=0.1, minInc=1.0e-8, maxInc=0.25,
                 maxNumInc=200)
    m.EncastreBC(name='PlaneFixed', createStepName='Initial', region=a.sets['PLANE_RP'])
    m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'CF', 'CSTRESS', 'CDISP'))


def build(model_name, drive, value):
    """drive = 'force' (value = total force, N) or 'disp' (value = downward displacement of the equator in the
    length unit of the solved model)"""
    m = mdb.Model(name=model_name)
    s = m.ConstrainedSketch(name='sphere', sheetSize=100.0)
    s.sketchOptions.setValues(viewStyle=AXISYM)
    s.ConstructionLine(point1=(0.0, -50.0), point2=(0.0, 50.0))
    s.Line(point1=(0.0, 0.0), point2=(0.0, R))
    s.Line(point1=(0.0, R), point2=(R, R))
    s.ArcByCenterEnds(center=(0.0, R), point1=(R, R), point2=(0.0, 0.0), direction=CLOCKWISE)
    p = m.Part(name='Sphere', dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    mat = m.Material(name='Steel')
    mat.Elastic(table=((E_SOLVER, NU),))
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)

    # refined zone: circle of radius R_ZONE around the contact point
    tr = p.MakeSketchTransform(sketchPlane=p.faces[0], sketchPlaneSide=SIDE1, origin=(0.0, 0.0, 0.0))
    s2 = m.ConstrainedSketch(name='zone', sheetSize=100.0, transform=tr)
    s2.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(R_ZONE, 0.0))
    p.PartitionFaceBySketch(faces=p.faces[:], sketch=s2)
    p.SectionAssignment(region=regionToolset.Region(faces=p.faces), sectionName='Sec')

    p.seedPart(size=H_FAR, deviationFactor=0.1, minSizeFactor=0.1)
    fine, end1, end2 = [], [], []
    for e in p.edges:
        d = [math.hypot(*p.vertices[i].pointOn[0][:2]) for i in e.getVertices()]
        if max(d) < R_ZONE * 1.001:
            fine.append(e)                                   # edges of the refined zone
        elif min(d) < R_ZONE * 1.001:
            (end1 if d[0] < d[1] else end2).append(e)        # edges leaving the zone: grade away from it
    if H_CONTACT < H_FAR:
        p.seedEdgeBySize(edges=fine, size=H_CONTACT, deviationFactor=0.1, constraint=FINER)
        bias = dict(biasMethod=SINGLE, minSize=H_CONTACT, maxSize=H_FAR, constraint=FINER)
        if end1:
            bias['end1Edges'] = end1
        if end2:
            bias['end2Edges'] = end2
        p.seedEdgeByBias(**bias)
    p.setMeshControls(regions=p.faces, elemShape=QUAD, algorithm=ADVANCING_FRONT)
    p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD),))
    p.generateMesh()

    s3 = m.ConstrainedSketch(name='plane', sheetSize=100.0)
    s3.sketchOptions.setValues(viewStyle=AXISYM)
    s3.ConstructionLine(point1=(0.0, -50.0), point2=(0.0, 50.0))
    s3.Line(point1=(0.0, 0.0), point2=(0.5 * R, 0.0))
    pr = m.Part(name='Plane', dimensionality=AXISYMMETRIC, type=ANALYTIC_RIGID_SURFACE)
    pr.AnalyticRigidSurf2DPlanar(sketch=s3)
    rp_plane = pr.ReferencePoint(point=(0.0, 0.0, 0.0))      # rigid-body reference node of the analytical part

    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    ib = a.Instance(name='Sphere-1', part=p, dependent=ON)
    ip = a.Instance(name='Plane-1', part=pr, dependent=ON)
    rp_top = a.ReferencePoint(point=(0.0, R, 0.0))
    a.Set(name='PLANE_RP', referencePoints=(ip.referencePoints[rp_plane.id],))
    a.Set(name='TOP_RP', referencePoints=(a.referencePoints[rp_top.id],))
    a.Set(name='AXIS', edges=ib.edges.getByBoundingBox(xMax=tol))
    a.Surface(name='TOP', side1Edges=ib.edges.getByBoundingBox(yMin=R - tol))
    # the two pieces of the spherical outline: inside and outside the refined zone
    phi_zone = 2.0 * math.asin(R_ZONE / (2.0 * R))
    a.Surface(name='BALL', side1Edges=ib.edges.findAt(
        *[((R * math.sin(phi), R - R * math.cos(phi), 0.0),) for phi in (0.5 * phi_zone,
                                                                         0.5 * (phi_zone + math.pi / 2.0))]))
    # side 1 of an analytical rigid edge lies to the left of its sketch direction (+x here): the +y side
    a.Surface(name='PLANE', side1Edges=ip.edges)

    m.Coupling(name='Equator', controlPoint=a.sets['TOP_RP'], surface=a.surfaces['TOP'],
               influenceRadius=WHOLE_SURFACE, couplingType=KINEMATIC, localCsys=None, u1=OFF, u2=ON, ur3=OFF)
    contact_and_step(m, a)
    m.XsymmBC(name='Axis', createStepName='Initial', region=a.sets['AXIS'])
    m.DisplacementBC(name='TopGuide', createStepName='Initial', region=a.sets['TOP_RP'], u1=0.0, ur3=0.0)
    if drive == 'force':
        m.ConcentratedForce(name='Load', createStepName='Press', region=a.sets['TOP_RP'], cf2=-value)
    else:
        m.DisplacementBC(name='Push', createStepName='Press', region=a.sets['TOP_RP'], u2=-value)
    return m


def build_3d(model_name, total_force):
    """quarter of the lower half sphere (symmetry planes x = 0 and z = 0), free C3D10 mesh graded to the contact"""
    h_contact, h_far = A_C / 10.0, R / 4.0
    m = mdb.Model(name=model_name)
    s = m.ConstrainedSketch(name='sphere', sheetSize=100.0)
    s.ConstructionLine(point1=(0.0, -50.0), point2=(0.0, 50.0))
    s.Line(point1=(0.0, 0.0), point2=(0.0, R))
    s.Line(point1=(0.0, R), point2=(R, R))
    s.ArcByCenterEnds(center=(0.0, R), point1=(R, R), point2=(0.0, 0.0), direction=CLOCKWISE)
    p = m.Part(name='Sphere', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidRevolve(sketch=s, angle=90.0, flipRevolveDirection=OFF)
    mat = m.Material(name='Steel')
    mat.Elastic(table=((E_SOLVER, NU),))
    m.HomogeneousSolidSection(name='Sec', material='Steel')
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='Sec')

    p.seedPart(size=h_far, deviationFactor=0.1, minSizeFactor=0.1)
    end1, end2 = [], []
    for e in p.edges:                       # the three edges that meet at the contact point (the origin)
        vs = e.getVertices()
        if len(vs) != 2:
            continue
        d = [math.sqrt(math.fsum(c * c for c in p.vertices[i].pointOn[0])) for i in vs]
        if min(d) < tol:
            (end1 if d[0] < d[1] else end2).append(e)
    bias = dict(biasMethod=SINGLE, minSize=h_contact, maxSize=h_far, constraint=FINER)
    if end1:
        bias['end1Edges'] = end1
    if end2:
        bias['end2Edges'] = end2
    p.seedEdgeByBias(**bias)
    p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD),))
    p.generateMesh()
    if len(p.elements) > 60000:              # keep the reference small: this machine is short of memory
        raise RuntimeError('3-D mesh has %d elements - refusing to run a model this large' % len(p.elements))

    s3 = m.ConstrainedSketch(name='plane', sheetSize=100.0)
    s3.Line(point1=(-2.0 * R, 0.0), point2=(2.0 * R, 0.0))
    pr = m.Part(name='Plane', dimensionality=THREE_D, type=ANALYTIC_RIGID_SURFACE)
    pr.AnalyticRigidSurfExtrude(sketch=s3, depth=4.0 * R)
    rp_plane = pr.ReferencePoint(point=(0.0, 0.0, 0.0))

    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    ib = a.Instance(name='Sphere-1', part=p, dependent=ON)
    ip = a.Instance(name='Plane-1', part=pr, dependent=ON)
    a.Set(name='PLANE_RP', referencePoints=(ip.referencePoints[rp_plane.id],))
    sym_z = ib.faces.getByBoundingBox(zMin=-tol, zMax=tol)
    sym_x = ib.faces.getByBoundingBox(xMin=-tol, xMax=tol)
    top = ib.faces.getByBoundingBox(yMin=R - tol)
    a.Set(name='SYM_Z', faces=sym_z)
    a.Set(name='SYM_X', faces=sym_x)
    a.Surface(name='TOP', side1Faces=top)
    flat = set(f.index for f in sym_z) | set(f.index for f in sym_x) | set(f.index for f in top)
    ball = [f for f in ib.faces if f.index not in flat]         # the only face left is the spherical one
    a.Surface(name='BALL', side1Faces=ib.faces[ball[0].index:ball[0].index + 1])
    a.Surface(name='PLANE', side1Faces=ip.faces)

    contact_and_step(m, a)
    m.ZsymmBC(name='SymZ', createStepName='Initial', region=a.sets['SYM_Z'])
    m.XsymmBC(name='SymX', createStepName='Initial', region=a.sets['SYM_X'])
    # the same pressure as on the complete equatorial plane (solver units): the quarter carries total_force / 4
    area = math.pi * (R_M * SOLVER_LEN) ** 2
    m.Pressure(name='Load', createStepName='Press', region=a.surfaces['TOP'], magnitude=total_force / area)
    return m


def scale_input(src, dst, factor):
    """copy an input file, multiplying node coordinates and analytical-rigid-surface data by `factor`"""
    with open(src, 'rb') as f:
        lines = f.read().decode('latin-1').splitlines()
    out, mode = [], None
    for line in lines:
        st = line.strip()
        if st.startswith('**'):
            out.append(line)
            continue
        if st.startswith('*'):
            key = st.lower().replace(' ', '')
            if key == '*node' or key.startswith('*node,'):
                mode = 'coords'
            elif key.startswith('*surface,') and ('type=segments' in key or 'type=cylinder' in key):
                mode = 'surface'
            else:
                mode = None
            out.append(line)
            continue
        if mode is not None and factor != 1.0:
            new = []
            for i, x in enumerate(v.strip() for v in st.split(',')):
                if (mode == 'coords' and i == 0) or x == '':
                    new.append(x)
                    continue
                try:
                    new.append('%.10g' % (float(x) * factor))
                except ValueError:
                    new.append(x)                    # START / LINE / CIRCL
            line = ', '.join(new)
        out.append(line)
    with open(dst, 'wb') as f:
        f.write('\n'.join(out).encode('latin-1') + b'\n')


def run(job_name, model_name):
    """write the input from CAE, convert the coordinates to the length unit of the solved model, run it"""
    mdb.Job(name=job_name + '_cae', model=model_name).writeInput(consistencyChecking=OFF)
    scale_input(job_name + '_cae.inp', job_name + '.inp', SOLVER_LEN / LS)
    job = mdb.JobFromInputFile(name=job_name, inputFileName=job_name + '.inp', memory=2, memoryUnits=GIGA_BYTES)
    job.submit()
    job.waitForCompletion()


def post(job_name, symmetry_factor=1):
    """-> (total contact force N, max CPRESS Pa, contact radius m) from the ODB"""
    odb = session.openOdb(name=job_name + '.odb', readOnly=True)
    frame = odb.steps['Press'].frames[-1]
    rf = frame.fieldOutputs['RF'].getSubset(region=odb.rootAssembly.nodeSets['PLANE_RP']).values[0].data[1]
    pts = []
    for v in frame.fieldOutputs['CPRESS'].values:
        if v.instance is None:
            continue
        node = v.instance.getNodeFromLabel(v.nodeLabel)
        pts.append((math.hypot(float(node.coordinates[0]), float(node.coordinates[2])), float(v.data)))
    odb.close()
    p_max = max(q[1] for q in pts)
    # contact radius: Hertz pressure obeys p^2 = p0^2 (1 - r^2/a^2) -> straight-line fit of p^2 against r^2
    sel = [(r * r, q * q) for r, q in pts if q > 0.2 * p_max]
    if len(sel) >= 3:
        n = float(len(sel))
        sx, sy = math.fsum(x for x, y in sel), math.fsum(y for x, y in sel)
        sxx, sxy = math.fsum(x * x for x, y in sel), math.fsum(x * y for x, y in sel)
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        icpt = (sy - slope * sx) / n
        a_fit = math.sqrt(-icpt / slope)
    else:
        a_fit = max([r for r, q in pts if q > 0.0] or [0.0])
    return abs(float(rf)) * symmetry_factor, float(p_max) * SOLVER_LEN ** 2, float(a_fit) / SOLVER_LEN


if VARIANT == 'disp_driven':
    # trial run at the Hertz approach, then scale the displacement with F ~ delta^1.5
    build('Trial', 'disp', DELTA_M * SOLVER_LEN)
    run('hertz_trial', 'Trial')
    f_trial = post('hertz_trial')[0]
    build('Hertz', 'disp', DELTA_M * SOLVER_LEN * (FORCE / f_trial) ** (2.0 / 3.0))
elif VARIANT == 'per_radian_load':
    build('Hertz', 'force', FORCE / (2.0 * math.pi))
elif VARIANT == 'quarter3d':
    build_3d('Hertz', FORCE)
else:
    build('Hertz', 'force', FORCE)
run(JOB, 'Hertz')
force, p_max, a_fit = post(JOB, 4 if VARIANT == 'quarter3d' else 1)      # report the force of the whole sphere

result = {'contact_force_N': force, 'max_contact_pressure_Pa': p_max, 'contact_radius_m': a_fit}
if VARIANT == 'fake_report':
    result['max_contact_pressure_Pa'] = p_max / 1.0e6
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
