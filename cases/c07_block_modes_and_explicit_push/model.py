# -*- coding: utf-8 -*-
# verification of dynamics / plasticity snippets: frequency model + explicit model with smooth loading and plasticity
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6
Mdb()


def block(model):
    s = model.ConstrainedSketch(name='sk', sheetSize=2.0)
    s.rectangle(point1=(0.0, 0.0), point2=(0.1, 0.1))
    p = model.Part(name='Block', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=0.5)
    mat = model.Material(name='Steel')
    mat.Elastic(table=((2.06e8, 0.3),))
    mat.Density(table=((7.85,),))
    # *Plastic: (true stress, plastic strain) pairs; the first plastic strain must be 0 and stress must not decrease
    mat.Plastic(table=((3.45e5, 0.0), (4.5e5, 0.05), (5.0e5, 0.15)))
    model.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
    p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')
    p.seedPart(size=0.025)
    return p


# ---------------- model 1: natural frequencies ----------------
m1 = mdb.models['Model-1']
p = block(m1)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8I, elemLibrary=STANDARD),))
p.generateMesh()
a = m1.rootAssembly
inst = a.Instance(name='Block-1', part=p, dependent=ON)
m1.FrequencyStep(name='Modes', previous='Initial', numEigen=6)
m1.EncastreBC(name='Fix', createStepName='Initial', region=a.Set(name='Base', faces=inst.faces.getByBoundingBox(zMax=TOL)))
mdb.Job(name='snip_freq', model='Model-1').writeInput(consistencyChecking=OFF)

# ---------------- model 2: Abaqus/Explicit, quasi-static style loading ----------------
m2 = mdb.Model(name='Explicit')
p = block(m2)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=EXPLICIT,
                                                              hourglassControl=ENHANCED),))
p.generateMesh()
a = m2.rootAssembly
inst = a.Instance(name='Block-1', part=p, dependent=ON)
T = 0.01
m2.ExplicitDynamicsStep(name='Push', previous='Initial', timePeriod=T, nlgeom=ON)
m2.SmoothStepAmplitude(name='Ramp', timeSpan=STEP, data=((0.0, 0.0), (T, 1.0)))
m2.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'V', 'PEEQ', 'RF'), numIntervals=20)
m2.historyOutputRequests['H-Output-1'].setValues(variables=('ALLIE', 'ALLKE', 'ALLAE', 'ALLWK', 'ALLPD', 'ETOTAL'),
                                                 numIntervals=100)
m2.EncastreBC(name='Fix', createStepName='Initial', region=a.Set(name='Base', faces=inst.faces.getByBoundingBox(zMax=TOL)))
top = a.Set(name='Top', faces=inst.faces.getByBoundingBox(zMin=0.5 - TOL))
m2.DisplacementBC(name='Push', createStepName='Push', region=top, u3=-0.01, amplitude='Ramp')
mdb.Job(name='snip_explicit', model='Explicit', explicitPrecision=DOUBLE_PLUS_PACK).writeInput(consistencyChecking=OFF)
print('INP written: snip_freq.inp, snip_explicit.inp')
