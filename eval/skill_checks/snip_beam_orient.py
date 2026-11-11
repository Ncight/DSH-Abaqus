# -*- coding: utf-8 -*-
# check which profile dimension is the bending depth: cantilever along x in the x-y plane, n1 = -z, tip load in y
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()
m = mdb.models['Model-1']
mat = m.Material(name='Steel')
mat.Elastic(table=((2.06e8, 0.3),))
s = m.ConstrainedSketch(name='w', sheetSize=20.0)
s.Line(point1=(0.0, 0.0), point2=(4.0, 0.0))
p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
m.RectangularProfile(name='Rect', a=0.2, b=0.4)
m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=0.3)
r = p.Set(name='All', edges=p.edges)
p.SectionAssignment(region=r, sectionName='Sec')
p.assignBeamSectionOrientation(region=r, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
p.seedPart(size=0.2)
p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B33, elemLibrary=STANDARD),))
p.generateMesh()
a = m.rootAssembly
i = a.Instance(name='Beam-1', part=p, dependent=ON)
m.StaticStep(name='Load', previous='Initial')
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'SF'))
m.EncastreBC(name='Fix', createStepName='Initial', region=a.Set(name='Root', vertices=i.vertices.getByBoundingBox(xMax=TOL)))
m.ConcentratedForce(name='P', createStepName='Load', region=a.Set(name='Tip', vertices=i.vertices.getByBoundingBox(xMin=4.0 - TOL)), cf2=-10.0)
mdb.Job(name='snip_orient', model='Model-1').writeInput(consistencyChecking=OFF)
