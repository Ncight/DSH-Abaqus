# -*- coding: utf-8 -*-
# 模板：用 abaqus python 读 ODB，把需要的量写成 JSON。运行：abq python read_odb_example.py <job>
# 这是 Abaqus 自带的 Python 2.7：没有 f-string；路径、键名用 str。
from odbAccess import openOdb
from abaqusConstants import *
import json
import sys

job = sys.argv[1] if len(sys.argv) > 1 else 'beam_demo'
odb = openOdb(job + '.odb', readOnly=True)
try:
    step = odb.steps[odb.steps.keys()[-1]]          # 最后一个分析步
    frame = step.frames[-1]                          # 最后一帧
    asm = odb.rootAssembly

    # 1) 某个节点集上的位移（装配级集合名在 ODB 里是大写；部件级集合在 instances['NAME-1'].nodeSets 里）
    nset = asm.nodeSets['MIDBOTTOM']
    u = frame.fieldOutputs['U'].getSubset(region=nset)
    u2 = [v.data[1] for v in u.values]               # v.data = (U1, U2, U3)
    mid_deflection = sum(u2) / len(u2)

    # 2) 支座反力合计
    rf = frame.fieldOutputs['RF'].getSubset(region=asm.nodeSets['FIXEND'])
    total_rf2 = sum(v.data[1] for v in rf.values)

    # 3) 应力：积分点上的 Mises 最大值及所在单元
    s = frame.fieldOutputs['S']
    worst = max(s.values, key=lambda v: v.mises)
    max_mises = worst.mises

    # 4) 整个模型的最大位移合量
    umax = max(v.magnitude for v in frame.fieldOutputs['U'].values)

    result = {'mid_deflection': float(mid_deflection), 'reaction_y': float(total_rf2),
              'max_mises': float(max_mises), 'max_mises_element': int(worst.elementLabel),
              'max_displacement': float(umax)}
finally:
    odb.close()

with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
print(json.dumps(result))
