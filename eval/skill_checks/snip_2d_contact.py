# -*- coding: utf-8 -*-
# verification of snippets used in the contact skill: 2-D plane strain, two deformable bodies, frictional contact
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()
m = mdb.models['Model-1']

# base block 0.20 x 0.10, punch 0.06 x 0.04 sitting on top, centred
s = m.ConstrainedSketch(name='base', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(0.20, 0.10))
base = m.Part(name='Base', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
base.BaseShell(sketch=s)
s = m.ConstrainedSketch(name='punch', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(0.06, 0.04))
punch = m.Part(name='Punch', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
punch.BaseShell(sketch=s)

# partition the base so that a fine-mesh zone exists under the punch
sk = m.ConstrainedSketch(name='cut', sheetSize=1.0)
sk.Line(point1=(0.05, 0.10), point2=(0.05, 0.06))
sk.Line(point1=(0.05, 0.06), point2=(0.15, 0.06))
sk.Line(point1=(0.15, 0.06), point2=(0.15, 0.10))
base.PartitionFaceBySketch(faces=base.faces, sketch=sk)

for p, name, e in ((base, 'Soft', 1.0e6), (punch, 'Stiff', 2.0e8)):
    mat = m.Material(name=name)
    mat.Elastic(table=((e, 0.3),))
    m.HomogeneousSolidSection(name='Sec' + name, material=name, thickness=1.0)
    p.SectionAssignment(region=p.Set(name='All', faces=p.faces), sectionName='Sec' + name)

# mesh: global seed, then local size on the edges of the refined zone
base.seedPart(size=0.01)
fine = base.edges.getByBoundingBox(xMin=0.05 - TOL, xMax=0.15 + TOL, yMin=0.06 - TOL, yMax=0.10 + TOL)
base.seedEdgeBySize(edges=fine, size=0.0025, constraint=FINER)
base.setMeshControls(regions=base.faces, elemShape=QUAD, technique=FREE)
base.setElementType(regions=(base.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4R, elemLibrary=STANDARD),
                                                      mesh.ElemType(elemCode=CPE3, elemLibrary=STANDARD)))
base.generateMesh()
punch.seedPart(size=0.005)
punch.setElementType(regions=(punch.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4R, elemLibrary=STANDARD),))
punch.generateMesh()

a = m.rootAssembly
ib = a.Instance(name='Base-1', part=base, dependent=ON)
ip = a.Instance(name='Punch-1', part=punch, dependent=ON)
a.translate(instanceList=('Punch-1',), vector=(0.07, 0.10, 0.0))

bottom = a.Set(name='Bottom', edges=ib.edges.getByBoundingBox(yMax=TOL))
ptop = a.Set(name='PunchTop', edges=ip.edges.getByBoundingBox(yMin=0.14 - TOL))
# master = stiffer / coarser body, slave = softer / finer body
master = a.Surface(name='PunchBottom', side1Edges=ip.edges.getByBoundingBox(yMax=0.10 + TOL))
slave = a.Surface(name='BaseTop', side1Edges=ib.edges.getByBoundingBox(yMin=0.10 - TOL))

prop = m.ContactProperty('Friction')
prop.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON)
prop.TangentialBehavior(formulation=PENALTY, table=((0.3,),), fraction=0.005, maximumElasticSlip=FRACTION)
m.SurfaceToSurfaceContactStd(name='PunchOnBase', createStepName='Initial', master=master, slave=slave,
                             sliding=FINITE, interactionProperty='Friction', adjustMethod=NONE)

# step 1 establishes contact under displacement control, step 2 applies the real load
m.StaticStep(name='Touch', previous='Initial', nlgeom=ON, initialInc=0.1, maxInc=0.25, minInc=1e-6, maxNumInc=200)
m.StaticStep(name='Press', previous='Touch', initialInc=0.1, maxInc=0.25, minInc=1e-6, maxNumInc=200)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CSTRESS', 'CDISP'))
m.HistoryOutputRequest(name='Energy', createStepName='Touch', variables=('ALLIE', 'ALLSE', 'ALLSD', 'ALLAE', 'ALLWK'))

m.EncastreBC(name='FixBase', createStepName='Initial', region=bottom)
m.DisplacementBC(name='PushPunch', createStepName='Touch', region=ptop, u1=0.0, u2=-1.0e-5)
m.boundaryConditions['PushPunch'].setValuesInStep(stepName='Press', u2=-5.0e-4)

mdb.Job(name='snip_contact', model='Model-1').writeInput(consistencyChecking=OFF)
print('INP written: snip_contact.inp')
