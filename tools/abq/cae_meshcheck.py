# -*- coding: utf-8 -*-
"""Abaqus 自己的网格检查（abq mesh --abaqus 调用）。读 INP，对每个部件跑 verifyMeshQuality，结果写 JSON。
Run by abq.py:  abaqus cae noGUI=cae_meshcheck.py -- <params.json>      (Abaqus Python 2.7)
"""
from abaqus import *
from abaqusConstants import *
from caeModules import *
import json
import sys

P = json.load(open(sys.argv[-1]))
m = mdb.ModelFromInputFile(name='MESHCHK', inputFileName=str(P['path']))
CHECKS = [('analysis', ANALYSIS_CHECKS, None, None),
          ('aspect', ASPECT_RATIO, 10.0, None),
          ('shape_tri', SHAPE_FACTOR, 0.0001, TRI), ('shape_tet', SHAPE_FACTOR, 0.0001, TET),
          ('small_angle_quad', SMALL_ANGLE, 10.0, QUAD), ('small_angle_hex', SMALL_ANGLE, 10.0, HEX),
          ('small_angle_tri', SMALL_ANGLE, 5.0, TRI), ('small_angle_tet', SMALL_ANGLE, 5.0, TET),
          ('large_angle_quad', LARGE_ANGLE, 160.0, QUAD), ('large_angle_hex', LARGE_ANGLE, 160.0, HEX),
          ('large_angle_tri', LARGE_ANGLE, 170.0, TRI), ('large_angle_tet', LARGE_ANGLE, 170.0, TET)]


def lab(seq):
    out = []
    try:
        for e in seq:
            out.append(e.label)
    except Exception:
        pass
    return out


res = {}
for pname, p in m.parts.items():
    n = len(p.elements)
    if not n:
        continue
    r = {'elements': n}
    for key, crit, thr, shape in CHECKS:
        kw = {'criterion': crit}
        if thr is not None:
            kw['threshold'] = thr
        if shape is not None:
            kw['elemShape'] = shape
        try:
            d = p.verifyMeshQuality(**kw)
        except Exception as ex:
            r[key] = {'error': str(ex)[:200]}
            continue
        item = {}
        for k in d.keys():
            v = d[k]
            if k in ('failedElements', 'warningElements', 'naElements'):
                ls = lab(v)
                item[k] = len(ls)
                item[k + '_labels'] = ls[:20]
            elif k == 'worstElement':
                try:
                    item[k] = v.label
                except Exception:
                    pass
            else:
                try:
                    item[k] = float(v)
                except Exception:
                    item[k] = str(v)[:80]
        r[key] = item
    res[pname] = r
json.dump(res, open(str(P['out']), 'w'), indent=1)
print('MESHCHK_DONE %d parts' % len(res))
