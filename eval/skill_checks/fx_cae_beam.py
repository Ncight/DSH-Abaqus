# -*- coding: utf-8 -*-
# Fixture built with CAE: cantilever block with assembly-level sets TIP / FIXEND, a static step "Load" and a frequency
# step "Modes".  Writes fx_cae.inp (solved by the runner); chk_odb_recipes.py then reads fx_cae.odb.
from abaqus import *
from abaqusConstants import *
from caeModules import *

L, B, H = 1.0, 0.05, 0.1
TOL = 1e-6
Mdb()
m = mdb.models['Model-1']
s = m.ConstrainedSketch(name='sec', sheetSize=1.0)
s.rectangle(point1=(0.0, 0.0), point2=(B, H))
p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=L)
mat = m.Material(name='Steel')
mat.Elastic(table=((2.06e11, 0.3),))
mat.Density(table=((7850.0,),))
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')
p.seedPart(size=0.025)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8I, elemLibrary=STANDARD),))
p.generateMesh()
a = m.rootAssembly
inst = a.Instance(name='Beam-1', part=p, dependent=ON)
fix = a.Set(name='FixEnd', faces=inst.faces.getByBoundingBox(zMax=TOL))
tip = a.Set(name='Tip', faces=inst.faces.getByBoundingBox(zMin=L - TOL))
top = a.Surface(name='Top', side1Faces=inst.faces.getByBoundingBox(yMin=H - TOL))
m.StaticStep(name='Load', previous='Initial')
m.FrequencyStep(name='Modes', previous='Load', numEigen=3)
m.EncastreBC(name='Fix', createStepName='Initial', region=fix)
m.Pressure(name='P', createStepName='Load', region=top, magnitude=20000.0)       # 20 kPa on 1.0 x 0.05 -> 1000 N down
mdb.Job(name='fx_cae', model='Model-1').writeInput(consistencyChecking=OFF)
