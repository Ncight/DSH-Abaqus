# -*- coding: utf-8 -*-
# verification of beam and shell snippets: a portal frame (B31) and a plate (S4R) in one model
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()
m = mdb.models['Model-1']
mat = m.Material(name='Steel')
mat.Elastic(table=((2.06e8, 0.3),))
mat.Density(table=((7.85,),))

# ---------------- beam: wire part drawn in the x-y plane ----------------
s = m.ConstrainedSketch(name='frame', sheetSize=20.0)
s.Line(point1=(0.0, 0.0), point2=(0.0, 3.0))
s.Line(point1=(0.0, 3.0), point2=(4.0, 3.0))
s.Line(point1=(4.0, 3.0), point2=(4.0, 0.0))
fr = m.Part(name='Frame', dimensionality=THREE_D, type=DEFORMABLE_BODY)
fr.BaseWire(sketch=s)
m.RectangularProfile(name='Rect', a=0.2, b=0.4)          # a = width along local 1, b = height along local 2
m.BeamSection(name='BeamSec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=0.3)
region = fr.Set(name='AllBeams', edges=fr.edges)
fr.SectionAssignment(region=region, sectionName='BeamSec')
# local 1-axis (n1) must not be parallel to any beam axis; here the frame lies in x-y, so n1 = global z works for all
fr.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
fr.seedPart(size=0.25)
fr.setElementType(regions=(fr.edges,), elemTypes=(mesh.ElemType(elemCode=B31, elemLibrary=STANDARD),))
fr.generateMesh()

# ---------------- shell: planar shell part in the x-y plane ----------------
s = m.ConstrainedSketch(name='plate', sheetSize=20.0)
s.rectangle(point1=(0.0, 0.0), point2=(2.0, 1.0))
pl = m.Part(name='Plate', dimensionality=THREE_D, type=DEFORMABLE_BODY)
pl.BaseShell(sketch=s)
m.HomogeneousShellSection(name='ShellSec', material='Steel', thickness=0.02, numIntPts=5)
pl.SectionAssignment(region=pl.Set(name='AllFaces', faces=pl.faces), sectionName='ShellSec')
pl.seedPart(size=0.1)
pl.setElementType(regions=(pl.faces,), elemTypes=(mesh.ElemType(elemCode=S4R, elemLibrary=STANDARD),))
pl.generateMesh()

a = m.rootAssembly
fi = a.Instance(name='Frame-1', part=fr, dependent=ON)
pi = a.Instance(name='Plate-1', part=pl, dependent=ON)
a.translate(instanceList=('Plate-1',), vector=(10.0, 0.0, 0.0))

m.StaticStep(name='Load', previous='Initial')
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'SF'))

feet = a.Set(name='Feet', vertices=fi.vertices.getByBoundingBox(yMax=TOL))
girder = a.Set(name='Girder', edges=fi.edges.getByBoundingBox(yMin=3.0 - TOL))
m.EncastreBC(name='FixFeet', createStepName='Initial', region=feet)
m.LineLoad(name='Udl', createStepName='Load', region=girder, comp2=-20.0)        # force per unit length, global y

edges = a.Set(name='PlateEdges', edges=pi.edges)
m.DisplacementBC(name='SimplySupported', createStepName='Initial', region=edges, u3=0.0)
corner = a.Set(name='Corner', vertices=pi.vertices.getByBoundingBox(xMax=10.0 + TOL, yMax=TOL))
m.DisplacementBC(name='PinCorner', createStepName='Initial', region=corner, u1=0.0, u2=0.0)
corner2 = a.Set(name='Corner2', vertices=pi.vertices.getByBoundingBox(xMin=12.0 - TOL, yMax=TOL))
m.DisplacementBC(name='RollCorner', createStepName='Initial', region=corner2, u2=0.0)
# shell faces have two sides: side1 = positive normal (SPOS), side2 = negative normal (SNEG)
top = a.Surface(name='PlateTop', side1Faces=pi.faces)
m.Pressure(name='Q', createStepName='Load', region=top, magnitude=5.0)

mdb.Job(name='snip_beam_shell', model='Model-1').writeInput(consistencyChecking=OFF)
print('INP written: snip_beam_shell.inp')
