# -*- coding: utf-8 -*-
# Demo model for the preview panel: steel beam + rubber pad (tie), m-t-s units (kPa, t/m3).
# Run:  abaqus cae noGUI=make_demo.py
from abaqus import *
from abaqusConstants import *
from caeModules import *
import regionToolset

m = mdb.Model(name='Demo')

s = m.ConstrainedSketch(name='a', sheetSize=2.0)
s.rectangle(point1=(0.0, 0.0), point2=(0.2, 0.1))
pA = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
pA.BaseSolidExtrude(sketch=s, depth=1.0)

s2 = m.ConstrainedSketch(name='b', sheetSize=2.0)
s2.rectangle(point1=(0.0, 0.0), point2=(0.2, 0.05))
pB = m.Part(name='Pad', dimensionality=THREE_D, type=DEFORMABLE_BODY)
pB.BaseSolidExtrude(sketch=s2, depth=0.3)

mat = m.Material(name='Steel')
mat.Elastic(table=((2.06e8, 0.3),))
mat.Density(table=((7.85,),))
mat2 = m.Material(name='Rubber')
mat2.Elastic(table=((1.0e4, 0.45),))
mat2.Density(table=((1.2,),))

m.HomogeneousSolidSection(name='SecSteel', material='Steel')
m.HomogeneousSolidSection(name='SecRubber', material='Rubber')
pA.SectionAssignment(region=regionToolset.Region(cells=pA.cells), sectionName='SecSteel')
pB.SectionAssignment(region=regionToolset.Region(cells=pB.cells), sectionName='SecRubber')

a = m.rootAssembly
iA = a.Instance(name='Beam-1', part=pA, dependent=ON)
iB = a.Instance(name='Pad-1', part=pB, dependent=ON)
a.translate(instanceList=('Pad-1',), vector=(0.0, 0.1, 0.35))

pA.seedPart(size=0.025)
pA.generateMesh()
pB.seedPart(size=0.025)
pB.generateMesh()
a.regenerate()

tol = 1e-6
a.Set(name='FixEnd', faces=iA.faces.getByBoundingBox(zMax=tol))
a.Set(name='TipCorners', vertices=iA.vertices.getByBoundingBox(zMin=1.0 - tol, yMin=0.1 - tol))
a.Surface(name='PadTop', side1Faces=iB.faces.getByBoundingBox(yMin=0.15 - tol))
a.Surface(name='BeamTop', side1Faces=iA.faces.getByBoundingBox(yMin=0.1 - tol))
a.Surface(name='PadBottom', side1Faces=iB.faces.getByBoundingBox(yMax=0.1 + tol))

m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'])

m.StaticStep(name='Load', previous='Initial', nlgeom=OFF)
m.EncastreBC(name='Fix', createStepName='Initial', region=a.sets['FixEnd'])
m.Pressure(name='PadPressure', createStepName='Load', region=a.surfaces['PadTop'], magnitude=500.0)
m.ConcentratedForce(name='TipForce', createStepName='Load', region=a.sets['TipCorners'], cf2=-10.0)

job = mdb.Job(name='demo_beam', model='Demo')
job.writeInput()

# Probe: can noGUI mode export a viewport image?
msg = ''
try:
    vp = session.viewports['Viewport: 1']
    vp.setValues(displayedObject=a)
    session.printToFile(fileName='nogui_probe', format=PNG, canvasObjects=(vp,))
    msg = 'printToFile OK'
except Exception as e:
    msg = 'printToFile FAILED: %s: %s' % (type(e).__name__, e)
f = open('nogui_print_probe.txt', 'w')
f.write(msg + '\n')
f.close()
