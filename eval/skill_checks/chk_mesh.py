# -*- coding: utf-8 -*-
"""abq mesh 的计数与 Abaqus 自带 verifyMeshQuality 逐项对照（在 run_all 的检查目录里运行）。
前置：snip_mesh.inp 已由 abq mesh --abaqus 处理过；这里再生成扰动网格 mesh_mix.inp 并同样处理。
对照项：长宽比 >10、面内角 <10/5 与 >160/170、形状因子 <0.0001、分析检查错误数 = 翻转单元数。
已知差别：严重翘曲且带凹角的四边形面，abq mesh 的大角计数是 Abaqus 的超集（更严），只要求不少于 Abaqus。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(ROOT, 'abq.cmd')) and os.path.dirname(ROOT) != ROOT:   # 本脚本会被复制到检查目录运行
    ROOT = os.path.dirname(ROOT)
ABQ = os.path.join(ROOT, 'abq.cmd')
ok = True


def compare(stem):
    global ok
    mine = json.load(open(stem + '_mesh.json', encoding='utf-8'))
    ab = mine.get('abaqus_check') or {}
    tot = {}
    for pv in ab.values():
        for k, v in pv.items():
            if isinstance(v, dict):
                tot[k] = tot.get(k, 0) + (v.get('failedElements') or 0)
    mt = mine['metrics']

    def m(fam, key, field='abaqus_fail'):
        return (mt.get(fam, {}).get(key) or {}).get(field, 0)

    pairs = [
        ('aspect', sum(m(f, 'aspect') for f in mt), tot.get('aspect', 0), 'eq'),
        ('shape_tet', m('tet', 'shape'), tot.get('shape_tet', 0), 'eq'),
        ('shape_tri', m('tri', 'shape'), tot.get('shape_tri', 0), 'eq'),
        ('small_angle_tet', m('tet', 'angle_min'), tot.get('small_angle_tet', 0), 'eq'),
        ('small_angle_tri', m('tri', 'angle_min'), tot.get('small_angle_tri', 0), 'eq'),
        ('small_angle_hex', m('hex', 'angle_min'), tot.get('small_angle_hex', 0), 'eq'),
        ('small_angle_quad', m('quad', 'angle_min'), tot.get('small_angle_quad', 0), 'eq'),
        ('large_angle_tet', m('tet', 'angle_max'), tot.get('large_angle_tet', 0), 'eq'),
        ('large_angle_hex', m('hex', 'angle_max'), tot.get('large_angle_hex', 0), 'ge'),
        ('large_angle_quad', m('quad', 'angle_max'), tot.get('large_angle_quad', 0), 'ge'),
        ('analysis errors = inverted', mine['inverted'], mine.get('abaqus_errors', 0), 'eq'),
    ]
    for name, a, b, how in pairs:
        good = (a == b) if how == 'eq' else (a >= b)
        ok &= good
        print('%-4s %-12s %-28s abq mesh %6d   Abaqus %6d' % ('OK' if good else 'FAIL', stem, name, a, b))


compare('snip_mesh')
subprocess.run([sys.executable, 'gen_mesh_mix.py', 'mesh_mix.inp'], check=True)
subprocess.run(['cmd.exe', '/d', '/c', ABQ, 'mesh', 'mesh_mix.inp', '--abaqus'], capture_output=True)   # 退出码 1 = FAIL，预期
compare('mesh_mix')
print('MESH OK' if ok else 'MESH MISMATCH')
sys.exit(0 if ok else 1)
