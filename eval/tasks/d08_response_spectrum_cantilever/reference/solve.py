# -*- coding: utf-8 -*-
"""Reference solution for d08_response_spectrum_cantilever  (run: abaqus cae noGUI=solve.py, cwd = work dir).

Units m - t - s (kN, kPa).  Vertical cantilever stick, H = 24 m along y, rectangular section 6 m x 1.0 m (depth 1.0 m
in the direction of the excitation x), E = 3.0e7 kPa, density 2.5 t/m3:
    EI = 1.5e7 kNm2,  mass per length 15 t/m,  total mass 360 t
    Euler-Bernoulli cantilever:  f1 = 0.9715 Hz (T1 = 1.029 s),  f2 = 6.0884 Hz,  f3 = 17.048 Hz
    effective modal masses 0.613076, 0.188300, 0.064732 of the total
Flat acceleration response spectrum Sa = 2.0 m/s2 in x, first three modes, SRSS:
    base shear  = 360 x 2.0 x sqrt(0.613076^2 + 0.188300^2 + 0.064732^2) = 464.11 kN
    top displacement = sqrt(0.084055^2 + 0.001186^2 + 0.000089^2) = 0.084063 m

REF_VARIANT (environment variable):
  default           2-D, 24 B23, 3 modes, SRSS                                                             -> PASS
  b21               shear-flexible B21 (f3 about 2 % lower)                                               -> PASS
  cqc               CQC combination (modes well separated: same as SRSS)                                  -> PASS
  spectrum_in_g     spectrum ordinate 2.0 taken as g (x 9.8)                                              -> FAIL
  abs_sum           absolute-sum combination                                                              -> FAIL
  one_mode          only the first mode extracted and combined                                            -> FAIL
  density_slip      density 2500 (kg/m3) in a kN - m - t model                                            -> FAIL
  axial_excitation  spectrum applied along the axis of the stick (y)                                      -> FAIL
  section_turned    section turned by 90 degrees (depth 6 m in the bending direction)                     -> FAIL
  fake_report       correct analysis, base shear reported in N                                            -> FAIL
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import math
import os
import sys

VARIANT = os.environ.get('REF_VARIANT', '') or 'default'
JOB = 'spectrum'
KNOWN = ('default', 'b21', 'cqc', 'spectrum_in_g', 'abs_sum', 'one_mode', 'density_slip', 'axial_excitation',
         'section_turned', 'fake_report')
if VARIANT not in KNOWN:
    raise ValueError('unknown REF_VARIANT %r' % VARIANT)

H, WIDTH, DEPTH = 24.0, 6.0, 1.0
E, NU = 3.0e7, 0.2
RHO = 2500.0 if VARIANT == 'density_slip' else 2.5
SA = 2.0 * (9.8 if VARIANT == 'spectrum_in_g' else 1.0)
N_MODES = 1 if VARIANT == 'one_mode' else 3
tol = 1.0e-6

m = mdb.Model(name='Tower')
mat = m.Material(name='Concrete')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))
if VARIANT == 'section_turned':
    m.RectangularProfile(name='Rect', a=DEPTH, b=WIDTH)
else:
    m.RectangularProfile(name='Rect', a=WIDTH, b=DEPTH)        # a out of plane, b = depth in the bending plane
m.BeamSection(name='Sec', integration=DURING_ANALYSIS, profile='Rect', material='Concrete', poissonRatio=NU)
s = m.ConstrainedSketch(name='axis', sheetSize=100.0)
s.Line(point1=(0.0, 0.0), point2=(0.0, H))
p = m.Part(name='Stick', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
p.BaseWire(sketch=s)
region = regionToolset.Region(edges=p.edges)
p.SectionAssignment(region=region, sectionName='Sec')
p.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))
p.seedEdgeByNumber(edges=p.edges, number=24, constraint=FIXED)
p.setElementType(regions=(p.edges,), elemTypes=(mesh.ElemType(elemCode=B21 if VARIANT == 'b21' else B23,
                                                              elemLibrary=STANDARD),))
p.generateMesh()
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Stick-1', part=p, dependent=ON)
a.Set(name='BASE', vertices=inst.vertices.getByBoundingBox(yMax=tol))
a.Set(name='TOP', vertices=inst.vertices.getByBoundingBox(yMin=H - tol))

m.SpectrumAmplitude(name='Flat', method=DEFINE, data=((SA, 0.01, 0.05), (SA, 1000.0, 0.05)),
                    specificationUnits=ACCELERATION)
m.FrequencyStep(name='Modes', previous='Initial', eigensolver=LANCZOS, numEigen=N_MODES)
direction = (0.0, 1.0, 0.0) if VARIANT == 'axial_excitation' else (1.0, 0.0, 0.0)
rule = {'abs_sum': ABS, 'cqc': CQC}.get(VARIANT, SRSS)
m.ResponseSpectrumStep(name='Spectrum', previous='Modes', components=(('Flat',) + direction + (1.0,),),
                       comp=SINGLE_DIRECTION, sum=rule, directDamping=((1, N_MODES, 0.05),))
# reactions and section forces of the spectrum step are combined from MODAL quantities: they have to be
# requested in the frequency step as well, otherwise the spectrum step has no RF at all
freq_vars = ('U',) if os.environ.get('REF_NO_MODAL_RF') else ('U', 'RF', 'SF')
m.fieldOutputRequests['F-Output-1'].setValues(variables=freq_vars)
m.FieldOutputRequest(name='F-Output-2', createStepName='Spectrum', variables=('U', 'RF', 'SF'))
m.EncastreBC(name='Base', createStepName='Initial', region=a.sets['BASE'])

job = mdb.Job(name=JOB, model='Tower', memory=2, memoryUnits=GIGA_BYTES)
job.submit()
job.waitForCompletion()

# ---- post-processing ----
odb = session.openOdb(name=JOB + '.odb', readOnly=True)
freqs = [float(fr.frequency) for fr in odb.steps['Modes'].frames if fr.mode > 0]
fr = odb.steps['Spectrum'].frames[-1]
asm = odb.rootAssembly
keys = list(fr.fieldOutputs.keys())
top = abs(float(fr.fieldOutputs['U'].getSubset(region=asm.nodeSets['TOP']).values[0].data[0]))
# Base shear.  Abaqus 2021 writes NO reaction forces in a response-spectrum step (requesting RF there and in the
# frequency step makes no difference), and Euler-Bernoulli elements have no transverse section force there either.
# What every model has are the effective masses of the frequency step (history output EM1 .. EM3 of the assembly,
# also printed in the .dat file): modal base shear = effective mass x spectral acceleration, combined like the step.
shear_sf = 0.0
if 'SF' in keys:
    shear_sf = max([max([abs(float(x)) for x in v.data[1:]]) for v in fr.fieldOutputs['SF'].values])
hist = odb.steps['Modes'].historyRegions['Assembly ASSEMBLY'].historyOutputs
em_key = 'EM2' if VARIANT == 'axial_excitation' else 'EM1'
eff = [float(v) for _mode, v in hist[em_key].data]
if VARIANT == 'abs_sum':
    shear_modal = SA * math.fsum(eff)
else:
    shear_modal = SA * math.sqrt(math.fsum([e * e for e in eff]))
if 'RF' in keys:
    shear = abs(float(fr.fieldOutputs['RF'].getSubset(region=asm.nodeSets['BASE']).values[0].data[0]))
    how = 'RF at the base node'
elif shear_sf > 1.0e-6 * shear_modal:
    shear, how = shear_sf, 'largest transverse section force of the spectrum step (Timoshenko elements)'
else:
    shear, how = shear_modal, 'effective masses %s x Sa, combined' % ', '.join('%.3f' % e for e in eff)
# base moment: section moments of the two lowest elements, extrapolated to the base
inst_o = asm.instances['STICK-1']
ys = dict((n.label, float(n.coordinates[1])) for n in inst_o.nodes)
el_y = dict((e.label, math.fsum([ys[c] for c in e.connectivity]) / len(e.connectivity)) for e in inst_o.elements)
by_el = {}
for v in fr.fieldOutputs['SM'].values:
    by_el.setdefault(v.elementLabel, []).append(abs(float(v.data[0])))
means = sorted((el_y[lab], math.fsum(vals) / len(vals)) for lab, vals in by_el.items())
(ya, ma), (yb, mb) = means[0], means[1]
moment = ma + (ma - mb) * ya / (yb - ya)
n_frames = len(odb.steps['Spectrum'].frames)
odb.close()
sys.__stdout__.write('d08 %s: frequencies %s Hz | base shear %.3f kN [%s] (modal %.3f, section force %.3f) | base '
                     'moment %.2f kNm | top displacement %.6f m | spectrum-step fields: %s\n' % (
                         VARIANT, ', '.join('%.4f' % f for f in freqs), shear, how, shear_modal, shear_sf, moment,
                         top, ' '.join(keys)))

result = {'frequencies_Hz': (freqs + [0.0, 0.0, 0.0])[:3], 'base_shear_kN': shear, 'top_displacement_m': top,
          'base_moment_kNm': moment}
if VARIANT == 'fake_report':
    result['base_shear_kN'] = shear * 1000.0
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
