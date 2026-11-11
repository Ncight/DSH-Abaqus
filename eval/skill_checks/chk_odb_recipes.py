# -*- coding: utf-8 -*-
# Runs, statement by statement, the ODB recipes quoted in the skill abaqus-postprocess (block "常用写法") on a fixture ODB.
#   abq python chk_odb_recipes.py fx_cae.odb      (model built with CAE: assembly-level sets)
#   abq python chk_odb_recipes.py fx_flat.odb     (input file written directly: sets live in instance PART-1-1)
# The lines between the two markers are the ones printed in the skill; keep them identical there.
from odbAccess import openOdb
from abaqusConstants import *
import json
import sys

path = sys.argv[1]
odb = openOdb(path, readOnly=True)
# ---- begin recipes
asm = odb.rootAssembly
step = odb.steps['Load']
frame = step.frames[-1]


def find_set(name, kind='nodeSets'):
    for holder in [asm] + [asm.instances[k] for k in asm.instances.keys()]:
        repo = getattr(holder, kind)
        if name.upper() in repo.keys():
            return repo[name.upper()]
    raise KeyError('no %s named %s' % (kind, name))


u = frame.fieldOutputs['U'].getSubset(region=find_set('Tip'))
u2 = [float(v.data[1]) for v in u.values]
rf = frame.fieldOutputs['RF'].getSubset(region=find_set('FixEnd'))
total_rf2 = float(sum([v.data[1] for v in rf.values]))
s = frame.fieldOutputs['S']
worst = max(s.values, key=lambda v: v.mises)
s11 = s.getScalarField(componentLabel='S11')
mises = s.getScalarField(invariant=MISES)
s_nodes = s.getSubset(position=ELEMENT_NODAL)
s_fix = s.getSubset(region=find_set('FixEnd', 'elementSets'))
modes = odb.steps['Modes']
freqs = [float(modes.frames[i].frequency) for i in range(1, len(modes.frames))]
v0 = u.values[0]
xyz = [float(c) for c in v0.instance.getNodeFromLabel(v0.nodeLabel).coordinates]
region = [k for k in step.historyRegions.keys() if k.startswith('Assembly')][0]
allie = step.historyRegions[region].historyOutputs['ALLIE'].data
result = {'tip_u2': sum(u2) / len(u2), 'reaction_y': total_rf2, 'max_mises': float(worst.mises),
          'max_mises_element': int(worst.elementLabel), 'frequencies_Hz': freqs, 'tip_xyz': xyz,
          'strain_energy': float(allie[-1][1])}
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
# ---- end recipes

problems = []


def expect(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        problems.append(what)


def raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


expect(abs(total_rf2 - 1000.0) < 1.0, 'reaction sum over FIXEND equals the applied 1000 N (%.3f)' % total_rf2)
expect(result['tip_u2'] < 0.0, 'tip moves down (%.6g)' % result['tip_u2'])
expect(len(freqs) == 3 and freqs[0] > 0.0, 'three positive frequencies %s' % freqs)
expect(len(s_nodes.values) >= len(s.values) and len(s_fix.values) > 0 and len(s11.values) == len(mises.values),
       'subsets by position / element set and scalar fields have values')
expect(abs(xyz[0] - 1.0) < 1e-6 or abs(xyz[2] - 1.0) < 1e-6, 'tip node coordinates %s' % xyz)
expect(json.load(open('result.json'))['frequencies_Hz'] == freqs, 'result.json reads back')
# the statements the skill warns against must really fail (otherwise the warning is wrong)
expect(raises(lambda: modes.frames[1:]), 'frames[1:] raises (ODB sequences cannot be sliced)')
expect(raises(lambda: u.values[:2]), 'values[:2] raises')
expect(raises(lambda: json.dumps(u.values[0].data[1])), 'json.dumps of a raw numpy.float32 raises')
expect(raises(lambda: frame.fieldOutputs['U'].position), 'FieldOutput has no .position')
expect(str(frame.fieldOutputs['U'].locations[0].position) == 'NODAL', 'FieldOutput.locations[0].position works')
expect(tuple(frame.fieldOutputs['U'].componentLabels)[:2] == ('U1', 'U2'), 'FieldOutput.componentLabels works')
expect(raises(lambda: frame.number) and frame.frameId >= 0 and frame.frameValue >= 0.0, 'OdbFrame: frameId / frameValue, no .number')
expect(modes.frames[2].mode == 2 and modes.frames[0].frequency == 0.0, 'frame.mode is the mode number; frames[0] of a frequency step is the base state')
expect(len(list(modes.frames)[1:]) == 3, 'list(frames)[1:] works')
expect(raises(lambda: asm.instances[asm.instances.keys()[0]].nodes.label), 'inst.nodes has no .label (it is an array of nodes)')
expect(asm.instances[asm.instances.keys()[0]].nodes[0].label >= 1, 'inst.nodes[0].label works')
expect(type(worst.mises) is float and type(u.values[0].magnitude) is float, 'v.mises and v.magnitude are plain floats')
inst0 = asm.instances[asm.instances.keys()[0]]
for label, fn in (('fo.location', lambda: frame.fieldOutputs['U'].location), ('fo.components', lambda: frame.fieldOutputs['U'].components),
                  ('v.node', lambda: u.values[0].node), ('v.coordinates', lambda: u.values[0].coordinates),
                  ('v.location', lambda: u.values[0].location), ('inst.nodes.coordinates', lambda: inst0.nodes.coordinates),
                  ('inst.elemSets', lambda: inst0.elemSets), ('step.increments', lambda: step.increments),
                  ('odb.jobName', lambda: odb.jobName), ("asm.nodeSets['tip'] (lower case)", lambda: asm.nodeSets['tip'])):
    expect(raises(fn), '%s raises, as the skill says' % label)
expect(frame.incrementNumber >= 0 and len(frame.description) > 0 and odb.name.lower().endswith('.odb'),
       'frame.incrementNumber, frame.description, odb.name exist')
expect(len(s.componentLabels) >= 3 and str(u.values[0].position) == 'NODAL' and inst0.elementSets is not None,
       's.componentLabels, v.position, inst.elementSets exist')
print('history region: %s; instances: %s; assembly node sets: %s' % (region, asm.instances.keys(), asm.nodeSets.keys()))
odb.close()
print('RECIPES %s' % ('OK' if not problems else 'FAILED: ' + '; '.join(problems)))
sys.exit(1 if problems else 0)
