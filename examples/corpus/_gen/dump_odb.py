# -*- coding: utf-8 -*-
# Abaqus Python 2.7.  Usage:  abaqus python dump_odb.py job.odb out.json
# Dumps what the solver itself ended up with after a datacheck: node / element counts per instance,
# element types, bounding box and a deterministic sample of node coordinates.  Used as ground truth
# for the preview parser (see odb_truth.py).
import json
import sys

from odbAccess import openOdb

SAMPLE = 400


def main(odb_path, out_path):
    odb = openOdb(odb_path, readOnly=True)
    asm = odb.rootAssembly
    res = {'instances': {}, 'nodes': 0, 'elements': 0, 'types': {}, 'bbox': None, 'sample': [], 'analytic': []}
    lo = [1e300, 1e300, 1e300]
    hi = [-1e300, -1e300, -1e300]

    def take(name, nodes, elements):
        nn = len(nodes)
        ne = len(elements)
        res['instances'][name] = {'nodes': nn, 'elements': ne}
        res['nodes'] += nn
        res['elements'] += ne
        stride = max(1, nn // SAMPLE)
        for i in range(nn):
            nd = nodes[i]
            c = nd.coordinates
            x, y, z = float(c[0]), float(c[1]), float(c[2])
            if x < lo[0]: lo[0] = x
            if y < lo[1]: lo[1] = y
            if z < lo[2]: lo[2] = z
            if x > hi[0]: hi[0] = x
            if y > hi[1]: hi[1] = y
            if z > hi[2]: hi[2] = z
            if i % stride == 0:
                res['sample'].append([name, int(nd.label), x, y, z])
        for i in range(ne):
            t = str(elements[i].type)
            res['types'][t] = res['types'].get(t, 0) + 1

    for name in asm.instances.keys():
        inst = asm.instances[name]
        take(name, inst.nodes, inst.elements)
        try:
            a = inst.analyticSurface
            if a is not None:
                rec = {'instance': name, 'name': str(a.name), 'segments': len(a.segments) if a.segments else 0}
                try:        # how the solver understood the surface: its local axes and profile points
                    rec['type'] = str(a.type)
                    rec['localCoordData'] = [[float(v) for v in row] for row in a.localCoordData]
                    rec['profile'] = [[str(s.type)] + [[float(v) for v in row] for row in s.data]
                                      for s in a.segments]
                except Exception as e:
                    rec['detailError'] = str(e)
                res['analytic'].append(rec)
        except Exception:
            pass
    try:
        if len(asm.nodes) or len(asm.elements):
            take('', asm.nodes, asm.elements)
    except Exception:
        pass
    if res['nodes']:
        res['bbox'] = [lo, hi]
    odb.close()
    f = open(out_path, 'w')
    json.dump(res, f)
    f.close()


if __name__ == '__main__':
    main(sys.argv[-2], sys.argv[-1])
