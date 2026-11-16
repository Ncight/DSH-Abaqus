# -*- coding: utf-8 -*-
"""Mesh quality of a CDB (ans mesh): the same element measures as abq mesh (tools/abq/abq_mesh.metrics), on the
mesh mapdl_model reads.  Contact, target and surface-effect elements, beams, links, springs and point masses
are counted but not measured.

    r = analyze(model, worst=10)      # model: mapdl_model.Model
Measures: aspect (longest / shortest edge), face angles, scaled Jacobian at the corners (1 = ideal, <= 0 =
inverted), shape factor (tetrahedra, triangles), warping (quadrilateral shells).  Two sets of limits from
abq_mesh: LIMIT (the defaults of Abaqus/CAE's mesh check - beyond them a solver usually warns) and GOOD (common
engineering recommendations).  Needs numpy.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'abq'))
import abq_mesh  # noqa: E402

FAM_CN = abq_mesh.FAM_CN
METRIC_CN = abq_mesh.METRIC_CN


def analyze(m, worst=10):
    me = m.mesh
    xyz = np.frombuffer(me.xyz, dtype=np.float64).reshape(-1, 3) if len(me.xyz) else np.zeros((0, 3))
    groups = {}
    counted = {}
    skipped = {}
    for e in range(len(me.e_code)):
        code = me.e_code[e]
        fam, nc, _ = m.fams[code]
        name = m.types[code].split('#')[0]
        if code in m._overlay or fam is None or fam in ('line', 'point'):
            skipped[name] = skipped.get(name, 0) + 1
            continue
        counted[fam] = counted.get(fam, 0) + 1
        groups.setdefault(fam, []).append(e)
    res = {'nodes': len(m.n_label), 'elements': len(me.e_code), 'families': counted, 'skipped': skipped,
           'metrics': {}, 'worst': [], 'inverted': 0, 'limitFail': 0, 'goodFail': 0}
    cand = []
    bad_limit, bad_good = set(), set()
    for fam, idx in groups.items():
        nc = abq_mesh.NCORNER_OF[fam] if hasattr(abq_mesh, 'NCORNER_OF') else {'hex': 8, 'wedge': 6, 'pyr': 5, 'tet': 4,
                                                                                   'quad': 4, 'tri': 3, 'quad2d': 4,
                                                                                   'tri2d': 3}[fam]
        conn = np.array([[me.e_nodes[me.e_off[e] + i] for i in range(nc)] for e in idx], dtype=np.int64)
        X = xyz[conn]
        r = abq_mesh.metrics(fam, X)
        mm = {}
        for k in ('aspect', 'angle_min', 'angle_max', 'sjac', 'shape', 'warp'):
            if k not in r:
                continue
            v = r[k]
            ent = {'worst': float(v.min() if k in ('angle_min', 'sjac', 'shape') else v.max()), 'median': float(np.median(v))}
            for table, key, store in ((abq_mesh.ABAQUS, 'limit', bad_limit), (abq_mesh.GOOD, 'good', bad_good)):
                lim = abq_mesh._limits(table, k, fam)
                if lim is None:
                    continue
                sense, val = lim
                fail = (v < val) if sense == 'min' else (v > val)
                ent[key + 'Limit'] = val
                ent[key + 'Fail'] = int(fail.sum())
                for j in np.nonzero(fail)[0]:
                    store.add(idx[j])
                    if key == 'good':
                        dist = (val - v[j]) / max(abs(val), 1e-30) if sense == 'min' else (v[j] - val) / max(abs(val), 1e-30)
                        cand.append((dist, idx[j], k, float(v[j])))
            mm[k] = ent
        inv = np.nonzero(r['sjac'] <= 0)[0]
        if len(inv):
            mm['inverted'] = int(len(inv))
            res['inverted'] += int(len(inv))
            for j in inv:
                cand.append((1e9, idx[j], 'sjac', float(r['sjac'][j])))
        res['metrics'][fam] = mm
    res['limitFail'] = len(bad_limit)
    res['goodFail'] = len(bad_good)
    n_eval = sum(counted.values())
    res['evaluated'] = n_eval
    res['goodPct'] = 100.0 * (n_eval - len(bad_good)) / n_eval if n_eval else 100.0
    seen = set()
    for dist, e, k, val in sorted(cand, key=lambda t: -t[0]):
        if e in seen:
            continue
        seen.add(e)
        o = me.e_off[e]
        nc = m.fams[me.e_code[e]][1]
        c = xyz[[me.e_nodes[o + i] for i in range(nc)]].mean(axis=0)
        res['worst'].append({'element': int(m.e_label[e]), 'type': m.types[me.e_code[e]].split('#')[0], 'metric': k,
                             'value': val, 'centroid': [float(x) for x in c], 'inverted': k == 'sjac' and val <= 0})
        if len(res['worst']) >= worst:
            break
    res['verdict'] = 'FAIL' if res['inverted'] else 'WARN' if res['limitFail'] else 'PASS'
    return res
