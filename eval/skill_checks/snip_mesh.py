# -*- coding: utf-8 -*-
# 同一块带孔板：四面体 C3D10 一份、扫掠六面体 C3D8R 一份（abaqus-elements-mesh：abq mesh）
from abaqus import *
from abaqusConstants import *
from caeModules import *
m = mdb.models['Model-1']
s = m.ConstrainedSketch(name='s', sheetSize=200.)
s.rectangle((0., 0.), (100., 40.))
s.CircleByCenterPerimeter((30., 20.), (38., 20.))
p = m.Part(name='BLK', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=20.)
p.seedPart(size=4.0)
p.setMeshControls(regions=p.cells, elemShape=TET, technique=FREE)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD),))
p.generateMesh()
p2 = m.Part(name='BLK2', objectToCopy=p)
p2.deleteMesh()
p2.setMeshControls(regions=p2.cells, elemShape=HEX_DOMINATED, technique=SWEEP)
p2.setElementType(regions=(p2.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),mesh.ElemType(elemCode=C3D6, elemLibrary=STANDARD)))
p2.generateMesh()
m.Material(name='STEEL').Elastic(table=((210000., 0.3),))
m.HomogeneousSolidSection(name='SEC', material='STEEL')
for pp in (p, p2):
    pp.SectionAssignment(region=pp.Set(name='ALL', cells=pp.cells), sectionName='SEC')
a = m.rootAssembly
a.Instance(name='B1', part=p, dependent=ON)
a.Instance(name='B2', part=p2, dependent=ON)
a.translate(instanceList=('B2',), vector=(0., 60., 0.))
mdb.Job(name='snip_mesh', model='Model-1').writeInput(consistencyChecking=OFF)
