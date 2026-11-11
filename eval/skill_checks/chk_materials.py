# -*- coding: utf-8 -*-
# Verification of the material statements quoted in the skill abaqus-materials-units: concrete damaged plasticity and
# Mohr-Coulomb.  Two one-element jobs (unit cube C3D8R, uniaxial stress state):
#   chk_cdp : tension to a total strain of 0.0006  -> stress and tension damage follow from the tables
#   chk_mc  : unconfined compression to 2 % strain -> axial stress = -2 c cos(phi) / (1 - sin(phi))
# The runner solves both; chk_materials_read.py compares the ODB values with the hand calculation.
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6


def cube(model_name, job, top_disp, variables):
    m = mdb.Model(name=model_name)
    s = m.ConstrainedSketch(name='s', sheetSize=4.0)
    s.rectangle(point1=(0.0, 0.0), point2=(1.0, 1.0))
    p = m.Part(name='Cube', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=1.0)
    p.seedPart(size=1.0)
    p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),))
    p.generateMesh()
    a = m.rootAssembly
    inst = a.Instance(name='Cube-1', part=p, dependent=ON)
    m.StaticStep(name='Load', previous='Initial', nlgeom=OFF, initialInc=0.01, maxInc=0.01, minInc=1e-8, maxNumInc=1000)
    m.fieldOutputRequests['F-Output-1'].setValues(variables=variables)
    m.DisplacementBC(name='X0', createStepName='Initial', region=a.Set(name='X0', faces=inst.faces.getByBoundingBox(xMax=TOL)), u1=0.0)
    m.DisplacementBC(name='Y0', createStepName='Initial', region=a.Set(name='Y0', faces=inst.faces.getByBoundingBox(yMax=TOL)), u2=0.0)
    m.DisplacementBC(name='Z0', createStepName='Initial', region=a.Set(name='Z0', faces=inst.faces.getByBoundingBox(zMax=TOL)), u3=0.0)
    m.DisplacementBC(name='Top', createStepName='Load', region=a.Set(name='Top', faces=inst.faces.getByBoundingBox(zMin=1.0 - TOL)), u3=top_disp)
    return m, p, job


# ---------------------------------------------------------------- concrete damaged plasticity (SI: Pa)
m, p, job = cube('CDP', 'chk_cdp', 0.0006, ('S', 'E', 'PE', 'U', 'RF', 'DAMAGET', 'DAMAGEC', 'SDEG', 'PEEQT'))
mc = m.Material(name='Concrete')
mc.Elastic(table=((3.0e10, 0.2),))
mc.Density(table=((2400.0,),))
mc.ConcreteDamagedPlasticity(table=((30.0, 0.1, 1.16, 0.667, 0.0005),))
mc.concreteDamagedPlasticity.ConcreteCompressionHardening(table=((2.0e7, 0.0), (3.0e7, 0.001), (2.0e7, 0.004)))
mc.concreteDamagedPlasticity.ConcreteTensionStiffening(table=((3.0e6, 0.0), (1.5e6, 0.0002), (0.3e6, 0.001)))
mc.concreteDamagedPlasticity.ConcreteCompressionDamage(table=((0.0, 0.0), (0.0, 0.001), (0.5, 0.004)))
mc.concreteDamagedPlasticity.ConcreteTensionDamage(table=((0.0, 0.0), (0.4, 0.0002), (0.8, 0.001)))
m.HomogeneousSolidSection(name='Sec', material='Concrete', thickness=None)
p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')
mdb.Job(name=job, model='CDP').writeInput(consistencyChecking=OFF)

# ---------------------------------------------------------------- Mohr-Coulomb (m-t-s: kPa)
m, p, job = cube('MC', 'chk_mc', -0.02, ('S', 'E', 'PE', 'PEEQ', 'U', 'RF'))
phi_deg, psi_deg, c = 30.0, 5.0, 20.0
soil = m.Material(name='Soil')
soil.Elastic(table=((50000.0, 0.3),))
soil.Density(table=((1.9,),))
soil.MohrCoulombPlasticity(table=((phi_deg, psi_deg),))
soil.mohrCoulombPlasticity.MohrCoulombHardening(table=((c, 0.0),))
m.HomogeneousSolidSection(name='Sec', material='Soil', thickness=None)
p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')
mdb.Job(name=job, model='MC').writeInput(consistencyChecking=OFF)
