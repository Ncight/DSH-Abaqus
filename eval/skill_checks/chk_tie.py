# -*- coding: utf-8 -*-
# Verification of the Tie statement quoted in the skill abaqus-contact (2021 argument names master= / slave=) and of the
# position-tolerance remark.  Two stacked blocks of the same material with different meshes:
#   chk_tie      blocks touching, tied            -> uniform compression, top moves down by p (h1 + h2) / E
#   chk_tie_gap  0.002 gap, specified tolerance   -> still tied (job completes)
# chk_tie_read.py checks the displacement of chk_tie.odb.
from abaqus import *
from abaqusConstants import *
from caeModules import *

TOL = 1e-6


def build(model_name, job, gap, tolerance):
    m = mdb.Model(name=model_name)
    parts = {}
    for name, h, seed in (('Beam', 0.2, 0.05), ('Pad', 0.1, 0.025)):
        s = m.ConstrainedSketch(name=name, sheetSize=2.0)
        s.rectangle(point1=(0.0, 0.0), point2=(0.2, h))
        p = m.Part(name=name, dimensionality=THREE_D, type=DEFORMABLE_BODY)
        p.BaseSolidExtrude(sketch=s, depth=0.2)
        p.seedPart(size=seed)
        p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD),))
        p.generateMesh()
        parts[name] = p
    mat = m.Material(name='Steel')
    mat.Elastic(table=((2.0e8, 0.0),))                     # nu = 0: the exact solution is uniform compression
    m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=None)
    a = m.rootAssembly
    insts = {}
    for name, p in parts.items():
        p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')
        insts[name] = a.Instance(name=name + '-1', part=p, dependent=ON)
    a.translate(instanceList=('Pad-1',), vector=(0.0, 0.2 + gap, 0.0))
    a.Surface(name='BeamTop', side1Faces=insts['Beam'].faces.getByBoundingBox(yMin=0.2 - TOL, yMax=0.2 + TOL))
    a.Surface(name='PadBottom', side1Faces=insts['Pad'].faces.getByBoundingBox(yMin=0.2 + gap - TOL, yMax=0.2 + gap + TOL))
    a.Surface(name='PadTop', side1Faces=insts['Pad'].faces.getByBoundingBox(yMin=0.3 + gap - TOL))
    a.Set(name='PadTopNodes', faces=insts['Pad'].faces.getByBoundingBox(yMin=0.3 + gap - TOL))
    base = a.Set(name='Base', faces=insts['Beam'].faces.getByBoundingBox(yMax=TOL))
    m.StaticStep(name='Load', previous='Initial')
    m.EncastreBC(name='Fix', createStepName='Initial', region=base)
    m.Pressure(name='P', createStepName='Load', region=a.surfaces['PadTop'], magnitude=1000.0)
    if tolerance is None:
        m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'])
    else:
        m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'],
              positionToleranceMethod=SPECIFIED, positionTolerance=tolerance)
    mdb.Job(name=job, model=model_name).writeInput(consistencyChecking=OFF)


build('Touch', 'chk_tie', 0.0, None)
build('Gap', 'chk_tie_gap', 0.002, 0.003)
