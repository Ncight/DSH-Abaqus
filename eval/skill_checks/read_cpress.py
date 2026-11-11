# -*- coding: utf-8 -*-
# verify how contact pressure is read from an ODB (abaqus python): field name, value type, node coordinates.
# The four statements after "recipe" are the ones quoted in the skill abaqus-postprocess.
from odbAccess import openOdb
import json
import sys

odb = openOdb(sys.argv[1], readOnly=True)
frame = odb.steps[odb.steps.keys()[-1]].frames[-1]
print("plain 'CPRESS' key exists: %r" % ('CPRESS' in frame.fieldOutputs.keys()))
# ---- recipe
cp = [k for k in frame.fieldOutputs.keys() if k.split(' ')[0] == 'CPRESS'][0]
pts = []
for v in frame.fieldOutputs[cp].values:
    if v.data > 0.0:
        xyz = v.instance.getNodeFromLabel(v.nodeLabel).coordinates
        pts.append((float(xyz[0]), float(v.data)))
# ---- end recipe
f = frame.fieldOutputs[cp]
v = f.values[0]
print('field %r  type=%s  data=%r (%s)  magnitude=%r  nodeLabel=%r  instance=%r' % (
    cp, f.type, v.data, type(v.data).__name__, v.magnitude, v.nodeLabel, v.instance.name if v.instance else None))
pts.sort()
print('nodes in contact: %d, x from %.6g to %.6g, half width %.6g, peak %.6g' % (len(pts), pts[0][0], pts[-1][0],
                                                                                 (pts[-1][0] - pts[0][0]) / 2.0, max(p for _, p in pts)))
ok = 'CPRESS' not in frame.fieldOutputs.keys() and len(pts) > 0 and json.dumps(pts[0]) != ''
odb.close()
print('CPRESS %s' % ('OK' if ok else 'FAILED'))
sys.exit(0 if ok else 1)
