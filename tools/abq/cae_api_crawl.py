# -*- coding: utf-8 -*-
"""Crawl the Abaqus/CAE kernel object model and dump every method signature (__doc__) to api_index.json.
Run by `abq build-api`:  abaqus cae noGUI=cae_api_crawl.py      (Abaqus Python 2.7)
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import os

SKIP = set(['AbaqusMethod', 'str', 'int', 'float', 'tuple', 'list', 'dict', 'bool', 'SymbolicConstant', 'NoneType',
            'unicode', 'long', 'AbaqusBoolean', 'function', 'builtin_function_or_method', 'module', 'type'])
classes = {}
log = []


def t(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:
        log.append('%s: %s' % (getattr(fn, '__name__', fn), e))
        return None


def visit(o, path, depth=0):
    if o is None or depth > 8:
        return
    try:
        name = type(o).__name__
    except Exception:
        return
    if name in SKIP:
        return
    methods = getattr(o, '__methods__', None)
    members = getattr(o, '__members__', None)
    is_seq = False
    if methods is None and members is None:
        return
    first_time = name not in classes
    if first_time:
        entry = classes[name] = {'path': path, 'methods': {}, 'members': list(members or [])}
        for mn in (methods or []):
            try:
                entry['methods'][mn] = getattr(o, mn).__doc__
            except Exception:
                entry['methods'][mn] = None
    if name == 'Repository':
        try:
            for key in o.keys()[:60]:
                visit(o[key], '%s[%r]' % (path, key), depth + 1)
        except Exception:
            pass
        return
    if not first_time:
        # same class seen before, but this instance may own sub-objects the first one did not (e.g. material.plastic)
        for mem in (members or []):
            try:
                v = getattr(o, mem)
                if type(v).__name__ not in classes and type(v).__name__ not in SKIP:
                    visit(v, path + '.' + mem, depth + 1)
            except Exception:
                continue
        return
    try:
        n = len(o)
        is_seq = True
    except Exception:
        n = 0
    if is_seq and n:
        try:
            visit(o[0], path + '[0]', depth + 1)
            visit(o[n - 1], path + '[-1]', depth + 1)
        except Exception:
            pass
    for mem in (members or []):
        try:
            v = getattr(o, mem)
        except Exception:
            continue
        visit(v, path + '.' + mem, depth + 1)


def build_model():
    m = mdb.Model(name='Crawl')
    # --- parts: solid, shell, wire
    s = m.ConstrainedSketch(name='sk1', sheetSize=2.0)
    s.rectangle(point1=(0.0, 0.0), point2=(0.2, 0.1))
    p = m.Part(name='Solid', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=1.0)
    t(p.DatumPlaneByPrincipalPlane, principalPlane=XYPLANE, offset=0.5)
    t(lambda: p.PartitionCellByDatumPlane(datumPlane=p.datums[p.datums.keys()[-1]], cells=p.cells))
    s2 = m.ConstrainedSketch(name='sk2', sheetSize=2.0)
    s2.rectangle(point1=(0.0, 0.0), point2=(1.0, 1.0))
    ps = m.Part(name='Shell', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    t(ps.BaseShell, sketch=s2)
    s3 = m.ConstrainedSketch(name='sk3', sheetSize=2.0)
    s3.Line(point1=(0.0, 0.0), point2=(1.0, 0.0))
    pw = m.Part(name='Wire', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    t(pw.BaseWire, sketch=s3)
    s4 = m.ConstrainedSketch(name='sk4', sheetSize=2.0)
    t(s4.CircleByCenterPerimeter, center=(0.0, 0.0), point1=(0.1, 0.0))
    s5 = m.ConstrainedSketch(name='sk5', sheetSize=2.0)
    s5.Line(point1=(-1.0, 0.0), point2=(1.0, 0.0))
    pr = m.Part(name='Rigid', dimensionality=TWO_D_PLANAR, type=ANALYTIC_RIGID_SURFACE)
    t(pr.AnalyticRigidSurf2DPlanar, sketch=s5)
    # --- materials
    mat = m.Material(name='Steel')
    mat.Elastic(table=((2.06e8, 0.3),))
    mat.Density(table=((7.85,),))
    t(mat.Plastic, table=((2.5e5, 0.0), (4.0e5, 0.1)))
    t(mat.Damping, alpha=0.1, beta=0.0)
    t(mat.Expansion, table=((1.2e-5,),))
    t(mat.Conductivity, table=((50.0,),))
    t(mat.SpecificHeat, table=((460.0,),))
    mc = m.Material(name='Concrete')
    mc.Elastic(table=((3.0e7, 0.2),))
    mc.Density(table=((2.5,),))
    t(mc.ConcreteDamagedPlasticity, table=((30.0, 0.1, 1.16, 0.667, 0.0005),))
    t(lambda: mc.concreteDamagedPlasticity.ConcreteCompressionHardening(table=((2.0e4, 0.0), (3.0e4, 0.001))))
    t(lambda: mc.concreteDamagedPlasticity.ConcreteTensionStiffening(table=((2.0e3, 0.0), (2.0e2, 0.001))))
    mh = m.Material(name='Rubber')
    t(mh.Hyperelastic, materialType=ISOTROPIC, testData=OFF, type=NEO_HOOKE, table=((500.0, 1.0e-6),))
    t(m.Material(name='Soil').MohrCoulombPlasticity, table=((30.0, 5.0),))
    # --- sections
    m.HomogeneousSolidSection(name='SecSolid', material='Steel')
    t(m.HomogeneousShellSection, name='SecShell', material='Steel', thickness=0.01)
    t(m.RectangularProfile, name='Prof', a=0.1, b=0.2)
    t(m.BeamSection, name='SecBeam', integration=DURING_ANALYSIS, profile='Prof', material='Steel')
    t(m.TrussSection, name='SecTruss', material='Steel', area=0.01)
    p.SectionAssignment(region=regionToolset.Region(cells=p.cells), sectionName='SecSolid')
    t(lambda: ps.SectionAssignment(region=regionToolset.Region(faces=ps.faces), sectionName='SecShell'))
    t(lambda: pw.SectionAssignment(region=regionToolset.Region(edges=pw.edges), sectionName='SecBeam'))
    t(lambda: pw.assignBeamSectionOrientation(region=regionToolset.Region(edges=pw.edges), method=N1_COSINES, n1=(0.0, 0.0, -1.0)))
    t(lambda: p.Set(name='AllCells', cells=p.cells))
    t(lambda: p.Surface(name='Top', side1Faces=p.faces.getByBoundingBox(yMin=0.0999)))
    # --- mesh
    t(p.seedPart, size=0.05)
    t(lambda: p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),)))
    t(p.generateMesh)
    t(ps.seedPart, size=0.25)
    t(ps.generateMesh)
    t(pw.seedPart, size=0.25)
    t(pw.generateMesh)
    # --- assembly
    a = m.rootAssembly
    i1 = a.Instance(name='Solid-1', part=p, dependent=ON)
    i2 = t(a.Instance, name='Solid-2', part=p, dependent=ON)
    t(a.translate, instanceList=('Solid-2',), vector=(0.0, 0.1, 0.0))
    t(a.Instance, name='Shell-1', part=ps, dependent=ON)
    t(a.Instance, name='Wire-1', part=pw, dependent=ON)
    t(lambda: a.rotate(instanceList=('Wire-1',), axisPoint=(0, 0, 0), axisDirection=(0, 0, 1), angle=30.0))
    rp = t(a.ReferencePoint, point=(0.1, 0.3, 0.5))
    t(lambda: a.Set(name='RP', referencePoints=(a.referencePoints[rp.id],)))
    t(lambda: a.Set(name='Fix', faces=i1.faces.getByBoundingBox(zMax=1e-6)))
    t(lambda: a.Surface(name='S1Top', side1Faces=i1.faces.getByBoundingBox(yMin=0.0999)))
    t(lambda: a.Surface(name='S2Bot', side1Faces=i2.faces.getByBoundingBox(yMax=0.1001)))
    t(lambda: a.Surface(name='S2Top', side1Faces=i2.faces.getByBoundingBox(yMin=0.1999)))
    # --- steps
    m.StaticStep(name='Static', previous='Initial', nlgeom=ON)
    t(m.ImplicitDynamicsStep, name='DynImp', previous='Static')
    t(m.FrequencyStep, name='Freq', previous='DynImp', numEigen=5)
    t(m.BuckleStep, name='Buckle', previous='Freq', numEigen=3, vectors=6)
    t(m.StaticRiksStep, name='Riks', previous='Buckle')
    t(m.HeatTransferStep, name='Heat', previous='Riks')
    t(m.ViscoStep, name='Visco', previous='Heat', cetol=0.01)
    t(m.GeostaticStep, name='Geo', previous='Visco')
    t(m.SteadyStateDirectStep, name='SSD', previous='Geo', frequencyRange=((1.0, 10.0, 5, 1.0),))
    t(m.ModalDynamicsStep, name='ModalDyn', previous='SSD')
    m2 = mdb.Model(name='CrawlExp')
    t(m2.ExplicitDynamicsStep, name='Exp', previous='Initial', timePeriod=0.01)
    t(m2.ContactProperty, 'IntExp')
    t(m2.ContactExp, name='GC', createStepName='Initial')
    t(lambda: m2.FieldOutputRequest(name='FO2', createStepName='Exp', variables=('S', 'U')))
    t(lambda: m2.HistoryOutputRequest(name='HO2', createStepName='Exp', variables=('ALLIE', 'ALLKE')))
    # --- amplitudes, BCs, loads, fields
    t(m.TabularAmplitude, name='Amp', timeSpan=STEP, data=((0.0, 0.0), (1.0, 1.0)))
    t(m.SmoothStepAmplitude, name='Smooth', timeSpan=STEP, data=((0.0, 0.0), (1.0, 1.0)))
    t(lambda: m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['Fix']))
    t(lambda: m.DisplacementBC(name='Disp', createStepName='Static', region=a.sets['RP'], u1=0.0, u2=-0.001))
    t(lambda: m.VelocityBC(name='Vel', createStepName='DynImp', region=a.sets['RP'], v1=1.0))
    t(lambda: m.XsymmBC(name='XS', createStepName='Initial', region=a.sets['RP']))
    t(lambda: m.Pressure(name='P', createStepName='Static', region=a.surfaces['S2Top'], magnitude=100.0))
    t(lambda: m.ConcentratedForce(name='CF', createStepName='Static', region=a.sets['RP'], cf2=-1.0))
    t(lambda: m.Gravity(name='G', createStepName='Static', comp2=-9.8))
    t(lambda: m.SurfaceTraction(name='ST', createStepName='Static', region=a.surfaces['S2Top'], magnitude=1.0,
                                directionVector=((0, 0, 0), (1, 0, 0)), traction=GENERAL))
    t(lambda: m.Moment(name='M', createStepName='Static', region=a.sets['RP'], cm3=1.0))
    t(lambda: m.BodyForce(name='BF', createStepName='Static', region=a.sets['Fix'], comp1=1.0))
    t(lambda: m.Temperature(name='T0', createStepName='Initial', region=a.sets['Fix'], magnitudes=(20.0,)))
    t(lambda: m.GeostaticStress(name='GS', region=a.sets['Fix'], stressMag1=0.0, vCoord1=1.0, stressMag2=-100.0,
                               vCoord2=0.0, lateralCoeff1=0.5))
    # --- interactions and constraints
    cp = t(m.ContactProperty, 'IntProp')
    t(lambda: cp.TangentialBehavior(formulation=PENALTY, table=((0.3,),), fraction=0.005))
    t(lambda: cp.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON))
    t(lambda: m.SurfaceToSurfaceContactStd(name='Contact', createStepName='Initial', master=a.surfaces['S1Top'],
                                           slave=a.surfaces['S2Bot'], sliding=FINITE, interactionProperty='IntProp'))
    t(lambda: m.ContactStd(name='GCStd', createStepName='Initial'))
    t(lambda: m.Tie(name='Tie', master=a.surfaces['S1Top'], slave=a.surfaces['S2Bot']))
    t(lambda: m.Coupling(name='Cpl', controlPoint=a.sets['RP'], surface=a.surfaces['S2Top'], influenceRadius=WHOLE_SURFACE,
                         couplingType=KINEMATIC))
    t(lambda: m.RigidBody(name='RB', refPointRegion=a.sets['RP'], tieRegion=a.sets['Fix']))
    t(lambda: m.Equation(name='Eq', terms=((1.0, 'RP', 1), (-1.0, 'RP', 2))))
    t(lambda: m.EmbeddedRegion(name='Emb', embeddedRegion=a.sets['Fix'], hostRegion=None))
    t(lambda: m.StdStabilization(name='Stab'))
    t(lambda: m.FieldOutputRequest(name='FO', createStepName='Static', variables=('S', 'U', 'RF')))
    t(lambda: m.HistoryOutputRequest(name='HO', createStepName='Static', variables=('ALLIE', 'ALLSE')))
    t(mdb.Job, name='CrawlJob', model='Crawl')
    return m


def main():
    m = t(build_model)
    visit(mdb, 'mdb')
    visit(session, 'session')
    vp = session.viewports['Viewport: 1']
    odb_path = os.environ.get('ABQ_CRAWL_ODB', r'E:\DSH-Abaqus\.dsh-eval\runs\_smoke\odb\demo_beam.odb')
    if os.path.isfile(odb_path):
        o = t(session.openOdb, name=odb_path, readOnly=True)
        if o is not None:
            t(vp.setValues, displayedObject=o)
            visit(o, 'odb')
            visit(vp.odbDisplay, "session.viewports['Viewport: 1'].odbDisplay")
            t(lambda: visit(o.steps.values()[0].frames[-1].fieldOutputs['S'].values[0], 'fieldOutput.values[0]'))
            t(lambda: visit(o.steps.values()[0].frames[-1].fieldOutputs['S'].bulkDataBlocks[0], 'fieldOutput.bulkDataBlocks[0]'))
    # module-level constructors and helpers
    mods = {}
    for mod_name in ('mesh', 'regionToolset', 'part', 'assembly', 'material', 'section', 'step', 'interaction', 'load',
                     'job', 'sketch', 'visualization', 'xyPlot', 'displayGroupOdbToolset', 'displayGroupMdbToolset',
                     'connectorBehavior', 'odbAccess'):
        mod = globals().get(mod_name)
        if mod is None:
            try:
                mod = __import__(mod_name)
            except Exception:
                continue
        entry = {}
        for nm in dir(mod):
            if nm.startswith('_'):
                continue
            try:
                obj = getattr(mod, nm)
                doc = getattr(obj, '__doc__', None)
            except Exception:
                continue
            if isinstance(doc, basestring) and '(' in doc and type(obj).__name__ in ('AbaqusMethod', 'builtin_function_or_method', 'function', 'type', 'classobj'):
                entry[nm] = doc.strip().split('\n')[0][:1500]
        if entry:
            mods['module ' + mod_name] = {'path': mod_name, 'methods': entry, 'members': []}
    classes.update(mods)
    with open('api_index.json', 'w') as f:
        json.dump({'abaqus': '2021', 'classes': classes, 'log': [repr(x) for x in log[:200]]}, f, encoding='latin-1')
    print('API_CRAWL classes=%d methods=%d issues=%d' % (len(classes), sum(len(c['methods']) for c in classes.values()), len(log)))


main()
