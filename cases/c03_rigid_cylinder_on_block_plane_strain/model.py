# -*- coding: utf-8 -*-
# verification: analytical rigid indenter (2-D) with reference point pressed on an elastic block, plane strain
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()
m = mdb.models['Model-1']
s = m.ConstrainedSketch(name='block', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(0.20, 0.10))
blk = m.Part(name='Block', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
blk.BaseShell(sketch=s)
mat = m.Material(name='Soft')
mat.Elastic(table=((1.0e6, 0.3),))
m.HomogeneousSolidSection(name='Sec', material='Soft', thickness=1.0)
blk.SectionAssignment(region=blk.Set(name='All', faces=blk.faces), sectionName='Sec')
blk.seedPart(size=0.004)
blk.setElementType(regions=(blk.faces,), elemTypes=(mesh.ElemType(elemCode=CPE4R, elemLibrary=STANDARD),))
blk.generateMesh()

# analytical rigid surface: a wire sketch; its reference point carries all motion and the total reaction
s = m.ConstrainedSketch(name='arc', sheetSize=1.0)
# one arc segment of an analytical rigid surface must span less than 180 degrees: draw a half circle as two quarters
s.ArcByCenterEnds(center=(0.0, 0.05), point1=(-0.05, 0.05), point2=(0.0, 0.0), direction=COUNTERCLOCKWISE)
s.ArcByCenterEnds(center=(0.0, 0.05), point1=(0.0, 0.0), point2=(0.05, 0.05), direction=COUNTERCLOCKWISE)
rig = m.Part(name='Indenter', dimensionality=TWO_D_PLANAR, type=ANALYTIC_RIGID_SURFACE)
rig.AnalyticRigidSurf2DPlanar(sketch=s)
rp = rig.ReferencePoint(point=(0.0, 0.05, 0.0))

a = m.rootAssembly
ib = a.Instance(name='Block-1', part=blk, dependent=ON)
ir = a.Instance(name='Indenter-1', part=rig, dependent=ON)
a.translate(instanceList=('Indenter-1',), vector=(0.10, 0.10, 0.0))        # lowest point of the arc touches the top face
rpset = a.Set(name='RP', referencePoints=(ir.referencePoints[rp.id],))
# the side that faces the block. With this sketch direction it is side2; if the reaction and CPRESS come out as zero
# (warning: FACING IN THE WRONG DIRECTION), switch to side1Edges
master = a.Surface(name='IndenterSurf', side2Edges=ir.edges)
slave = a.Surface(name='BlockTop', side1Edges=ib.edges.getByBoundingBox(yMin=0.10 - TOL))
prop = m.ContactProperty('Frictionless')
prop.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON)
m.SurfaceToSurfaceContactStd(name='Indent', createStepName='Initial', master=master, slave=slave, sliding=FINITE,
                             interactionProperty='Frictionless')
m.StaticStep(name='Press', previous='Initial', nlgeom=ON, initialInc=0.1, maxInc=0.2, minInc=1e-6, maxNumInc=200)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'CSTRESS', 'CDISP'))
m.EncastreBC(name='Fix', createStepName='Initial', region=a.Set(name='Bottom', edges=ib.edges.getByBoundingBox(yMax=TOL)))
m.DisplacementBC(name='Push', createStepName='Press', region=rpset, u1=0.0, u2=-0.002, ur3=0.0)
mdb.Job(name='snip_rigid', model='Model-1').writeInput(consistencyChecking=OFF)
print('INP written: snip_rigid.inp')
