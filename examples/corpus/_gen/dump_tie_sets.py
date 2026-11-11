# -*- coding: utf-8 -*-
"""abaqus python dump_tie_sets.py job.odb out.json

Lists the node sets of the output database whose names have to do with ties / warnings and how many
nodes each holds.  Abaqus stores the slave nodes it did and did not tie as internal node sets, while
the data file stops printing the per-node warnings after ten of them."""
from __future__ import print_function
import json
import sys
from odbAccess import openOdb

odb = openOdb(sys.argv[1], readOnly=True)
out = {}
a = odb.rootAssembly


def size(ns):
    n = 0
    try:
        for part in ns.nodes:
            try:
                n += len(part)
            except TypeError:
                n += 1
    except Exception:
        pass
    return n


for name in a.nodeSets.keys():
    up = name.upper()
    if 'TIE' in up or 'WARN' in up or 'ADJUST' in up or 'UNCONSTR' in up:
        out['assembly:' + name] = size(a.nodeSets[name])
for iname in a.instances.keys():
    inst = a.instances[iname]
    for name in inst.nodeSets.keys():
        up = name.upper()
        if 'TIE' in up or 'WARN' in up or 'ADJUST' in up or 'UNCONSTR' in up:
            out[iname + ':' + name] = len(inst.nodeSets[name].nodes)
odb.close()
json.dump(out, open(sys.argv[2], 'w'), indent=1, sort_keys=True)
print(json.dumps(out, indent=1, sort_keys=True))
