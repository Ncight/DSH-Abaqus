# -*- coding: utf-8 -*-
"""abq mesh 指标的自检：理想单元应得 1 / 90° / 60°，已知畸变单元的值与手算一致，翻转单元被判 FAIL。
运行: py tools\\abq\\test_mesh.py"""
import math
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abq_mesh as M  # noqa: E402

ok = True


def check(name, got, want, tol=1e-6):
    global ok
    good = abs(got - want) <= tol * max(1.0, abs(want))
    ok &= good
    print('%-4s %-40s got %.6g  want %.6g' % ('OK' if good else 'FAIL', name, got, want))


cube = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)[None]
m = M.metrics('hex', cube)
check('hex 立方体 sjac', m['sjac'][0], 1.0)
check('hex 立方体 aspect', m['aspect'][0], 1.0)
check('hex 立方体 angle', m['angle_min'][0], 90.0)
check('hex 立方体 体积', m['measure'][0], 1.0)
box = cube * np.array([4.0, 1.0, 1.0])
check('hex 4x1x1 aspect', M.metrics('hex', box)['aspect'][0], 4.0)
sh = cube.copy()
sh[0, [4, 5, 6, 7], 0] += 1.0          # 顶面沿 x 剪切 45°
check('hex 剪切45° sjac', M.metrics('hex', sh)['sjac'][0], math.sqrt(0.5))
check('hex 剪切45° 最小角', M.metrics('hex', sh)['angle_min'][0], 45.0)
inv = cube[:, [4, 5, 6, 7, 0, 1, 2, 3]]   # 上下颠倒 = 翻转
check('hex 翻转 sjac', M.metrics('hex', inv)['sjac'][0], -1.0)

a = 1.0
tet = np.array([[1, 1, 1], [-1, 1, -1], [1, -1, -1], [-1, -1, 1]], float)[None]
mt = M.metrics('tet', tet)
check('tet 正四面体 sjac', mt['sjac'][0], 1.0)
check('tet 正四面体 shape', mt['shape'][0], 1.0)
check('tet 正四面体 angle', mt['angle_min'][0], 60.0)
flat = tet.copy()
flat[0, 3] = [0.0, 0.0, -1.0 + 1e-6]   # 几乎压扁
check('tet 压扁 shape≈0', M.metrics('tet', flat)['shape'][0], 0.0, tol=1e-4)

w = np.array([[0, 0, 0], [1, 0, 0], [0.5, math.sqrt(3) / 2, 0], [0, 0, 1], [1, 0, 1], [0.5, math.sqrt(3) / 2, 1]])[None]
check('wedge 理想 sjac', M.metrics('wedge', w)['sjac'][0], 1.0)

q = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)[None]
mq = M.metrics('quad', q)
check('quad 正方形 sjac', mq['sjac'][0], 1.0)
check('quad 正方形 warp', mq['warp'][0], 0.0)
qw = q.copy()
qw[0, 2, 2] = 0.2
check('quad 翘起 warp>0', float(M.metrics('quad', qw)['warp'][0] > 5), 1.0)
t = np.array([[0, 0, 0], [1, 0, 0], [0.5, math.sqrt(3) / 2, 0]])[None]
check('tri 正三角 shape', M.metrics('tri', t)['shape'][0], 1.0)

# 整条链：写一个 2 单元 INP（一个好、一个翻转）
inp = '''*Heading
*Node
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
5,0,0,1
6,1,0,1
7,1,1,1
8,0,1,1
9,2,0,0
10,2,1,0
11,2,0,1
12,2,1,1
*Element, type=C3D8R, elset=ALL
1,1,2,3,4,5,6,7,8
2,6,11,12,7,2,9,10,3
*Solid Section, elset=ALL, material=M
*Material, name=M
*Elastic
1.,0.3
'''
d = tempfile.mkdtemp()
p = os.path.join(d, 't.inp')
open(p, 'w').write(inp)
r = M.analyze(p)
check('INP 链 翻转单元数', r['inverted'], 1)
check('INP 链 结论 FAIL', float(r['verdict'] == 'FAIL'), 1.0)
check('INP 链 最差单元标签', r['worst'][0]['label'], 2)
print('ALL OK' if ok else 'SOME FAILED')
sys.exit(0 if ok else 1)
