# -*- coding: utf-8 -*-
# verification of axisymmetric snippets: ring under external pressure, biased seeds
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()
m = mdb.models['Model-1']
RI, RO, HGT = 0.5, 0.8, 0.2            # axisymmetric sketch: x = r (must be >= 0), y = axial coordinate
s = m.ConstrainedSketch(name='ring', sheetSize=5.0)
s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))     # the axis of symmetry
s.rectangle(point1=(RI, 0.0), point2=(RO, HGT))
p = m.Part(name='Ring', dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
p.BaseShell(sketch=s)

mat = m.Material(name='Steel')
mat.Elastic(table=((2.06e8, 0.3),))
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
p.SectionAssignment(region=p.Set(name='All', faces=p.faces), sectionName='Sec')

# biased seeding along the radial edges: small elements at the inner radius (end1/end2 depends on edge direction)
radial = p.edges.getByBoundingBox(yMax=TOL) + p.edges.getByBoundingBox(yMin=HGT - TOL)
p.seedEdgeByBias(biasMethod=SINGLE, end1Edges=radial, ratio=4.0, number=20, constraint=FINER)
axial = p.edges.getByBoundingBox(xMax=RI + TOL) + p.edges.getByBoundingBox(xMin=RO - TOL)
p.seedEdgeByNumber(edges=axial, number=10, constraint=FINER)
p.setMeshControls(regions=p.faces, elemShape=QUAD, technique=STRUCTURED)
p.setElementType(regions=(p.faces,), elemTypes=(mesh.ElemType(elemCode=CAX4R, elemLibrary=STANDARD),))
p.generateMesh()

a = m.rootAssembly
inst = a.Instance(name='Ring-1', part=p, dependent=ON)
m.StaticStep(name='Load', previous='Initial')
outer = a.Surface(name='Outer', side1Edges=inst.edges.getByBoundingBox(xMin=RO - TOL))
ends = a.Set(name='Ends', edges=inst.edges.getByBoundingBox(yMax=TOL) + inst.edges.getByBoundingBox(yMin=HGT - TOL))
m.DisplacementBC(name='NoAxial', createStepName='Initial', region=ends, u2=0.0)      # plane-strain-like: no axial strain
m.Pressure(name='P', createStepName='Load', region=outer, magnitude=1000.0)
mdb.Job(name='snip_axisym', model='Model-1').writeInput(consistencyChecking=OFF)
# report which end of each radial edge is "end1" so the bias direction can be checked in the INP / preview
print('INP written: snip_axisym.inp')
