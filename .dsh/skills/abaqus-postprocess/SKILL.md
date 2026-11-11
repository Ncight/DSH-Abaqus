---
name: abaqus-postprocess
description: 从 Abaqus 结果库(ODB)取数、出图、写结果文件时加载。说明 abq odb 的各种取数方式（全场最值与位置、反力合计、某个集合上的合计/平均及其时程、指定坐标处的时程、指定分析步与帧、模态频率）、需要更细的量时用 abaqus python 读 ODB 的已验证写法和最常见的接口报错、云图/振型出图，以及取数时最常见的错误（取错帧、取错位置、积分点值与节点值混用、集合名大小写）。
---

# 读结果、出图、写结果文件

## 先用 abq odb，多数量不用写脚本

```
abq odb job.odb                                  全部常用场量在最后一帧的最值与位置、反力/集中力合计、能量、频率
abq odb job.odb --vars U,S,RF                    只要这些场量
abq odb job.odb --step Load --frame 5            指定分析步和帧
abq odb job.odb --sets                           列出结果库里的全部集合（名字、节点/单元数、在装配上还是在实例里）
abq odb job.odb --set FixEnd --vars RF,U         某个集合上：RF/CF 给合计，其他场量给平均和范围；节点不多时逐个列出
abq odb job.odb --set FixEnd --series RF         该集合上逐帧的合计（RF、CF）或平均（U、S 等）——荷载-位移曲线、反力时程
abq odb job.odb --probe 0.25,0.005,0.005 --series S,U,V     离该坐标最近的节点/单元上各帧的时程
abq odb job.odb --full                           所有张量都给不变量，并列出该帧可用的场量名
```

`--set` 和 `--probe` 可以写多次、可以同用；带了它们时只输出这部分，时程表列首尾几行和每列的全程极值及其时刻，全部数据在 `job.summary.json` 里。集合名大小写不限。**关心的位置在建模时就建成集合**（加载点、支座、要取平均的面、监测点），取数时按名字要，比按坐标找节点可靠。

输出怎么读：

- **U**：`最大合量` 是位移矢量模的最大值及所在节点；`分量范围` 是各分量的最小～最大值（带符号）。
- **RF / CF / RM**：`合计` 是全模型代数和，等于施加的总载荷（方向相反）——这是最可靠的平衡核对。两端都有约束、净合计为 0 时，用 `--set 支座集合名` 取每个支座各自的合计。全模型的 `正值合计 / 负值合计` 只在节点反力同号时才等于传递的力；二次单元（C3D20R、CPS8R 等）受约束面上的节点反力本身有正有负，不能用它。
- **S**：`积分点` 一行是积分点上的值，`外推到单元节点` 是外推到单元角点的值。应力集中、表面应力要对比解析解时用外推值（更接近表面真值）；线性减缩单元每个单元只有一个积分点，两者相同，只能靠加密网格。
- **最大主值 / 最小主值**：拉为正。环向/轴向等特定方向的应力，在轴线与坐标轴对齐时可以直接读分量（如轴对称模型的 S33 是环向应力）。
- **频率**：直接列出各阶 Hz。**屈曲特征值**：乘以参考载荷得临界载荷。特征值分析步的帧是归一化的振型，位移数值没有物理意义，时程里不包含它们。
- **能量比**：见 `abq verify` 的判断标准。
- 显示为 0 的量是相对该场量级小于十亿分之一的舍入误差。所有数都是模型自身单位制下的原始值。

## 写结果文件

任务书要求 `result.json` 之类的文件时：数值取自 `abq run`/`abq odb` 的输出或自己的后处理脚本，键名和单位完全按任务书。**只从正式交付作业（任务书指定的作业名）的结果库取数**，临时试算作业的数不写进结果文件。数字一取齐就写文件，不要留到最后；写完读回一遍，确认是合法 JSON、每个键都有、值是数字而不是字符串。不要把估算值或理论值填进去冒充计算结果。

`abq odb` 的完整输出在 `job.summary.json` 里（全精度），可以用系统 Python 直接读它来写结果文件，不必再开 ODB：`frequencies[i].frequency`、`fields.RF.sum.RF2`、`fields.U.maxMagnitude.value`、`setResults[i].fields.RF.sum`（顺序同 `columns`）、`setResults[i].series.RF.rows`、`probes[i].series.U.rows`。

## 需要更细的量：abaqus python 读 ODB

用 `abq python post.py` 运行（Abaqus 自带的 Python 2.7）。下面每一行都在本机实跑过（CAE 建的模型和直接写的 INP 各一个）：

```python
from odbAccess import openOdb
from abaqusConstants import *
import json
odb = openOdb('job.odb', readOnly=True)
asm = odb.rootAssembly
step = odb.steps['Load']                          # 最后一个分析步: odb.steps[odb.steps.keys()[-1]]
frame = step.frames[-1]                           # 最后一帧；frames[0] 是初始状态。负下标可以用，切片 frames[1:] 不行


def find_set(name, kind='nodeSets'):              # kind: 'nodeSets' 或 'elementSets'。ODB 里集合名一律大写
    for holder in [asm] + [asm.instances[k] for k in asm.instances.keys()]:
        repo = getattr(holder, kind)              # CAE 的装配级集合在 asm 上；直接写的 INP（没有 *Assembly）集合在实例 'PART-1-1' 上
        if name.upper() in repo.keys():
            return repo[name.upper()]
    raise KeyError('no %s named %s' % (kind, name))


u = frame.fieldOutputs['U'].getSubset(region=find_set('Tip'))
u2 = [float(v.data[1]) for v in u.values]         # v.data = (U1, U2, U3)，是 numpy.float32：写 JSON 前必须 float()
rf = frame.fieldOutputs['RF'].getSubset(region=find_set('FixEnd'))
total_rf2 = float(sum([v.data[1] for v in rf.values]))
s = frame.fieldOutputs['S']                       # 积分点上的值
worst = max(s.values, key=lambda v: v.mises)      # v.mises、v.maxPrincipal、v.minPrincipal、v.magnitude 是普通 float；v.elementLabel、v.integrationPoint
s11 = s.getScalarField(componentLabel='S11')      # 单个分量（分量名见 s.componentLabels）
mises = s.getScalarField(invariant=MISES)         # 不变量
s_nodes = s.getSubset(position=ELEMENT_NODAL)     # 外推到单元节点
s_fix = s.getSubset(region=find_set('FixEnd', 'elementSets'))
modes = odb.steps['Modes']
freqs = [float(modes.frames[i].frequency) for i in range(1, len(modes.frames))]   # 第 i 帧是第 i 阶；frames[0] 是基态；阶次是 frame.mode
v0 = u.values[0]
xyz = [float(c) for c in v0.instance.getNodeFromLabel(v0.nodeLabel).coordinates]  # 节点坐标（变形前）
region = [k for k in step.historyRegions.keys() if k.startswith('Assembly')][0]   # CAE 模型是 'Assembly ASSEMBLY'，直接写的 INP 是 'Assembly Assembly-1'
allie = step.historyRegions[region].historyOutputs['ALLIE'].data                 # ((时间, 值), ...)，普通 float
result = {'tip_u2': sum(u2) / len(u2), 'reaction_y': total_rf2, 'max_mises': float(worst.mises),
          'max_mises_element': int(worst.elementLabel), 'frequencies_Hz': freqs, 'tip_xyz': xyz,
          'strain_energy': float(allie[-1][1])}
with open('result.json', 'w') as f:
    json.dump(result, f, indent=1)
odb.close()
```

接触压力（已实测）：接触对的场名带面对后缀，如 `'CPRESS   ASSEMBLY_BLOCKTOP/ASSEMBLY_PUNCH'`，没有名为 `'CPRESS'` 的键。

```python
cp = [k for k in frame.fieldOutputs.keys() if k.split(' ')[0] == 'CPRESS'][0]
pts = []
for v in frame.fieldOutputs[cp].values:           # 标量场: v.data 是一个数
    if v.data > 0.0:
        xyz = v.instance.getNodeFromLabel(v.nodeLabel).coordinates
        pts.append((float(xyz[0]), float(v.data)))  # (位置, 压力)，可按位置排序、求最远接触点
```

这些写法会报错（都是实测到的常见错误），右边是正确写法：

| 会报错的写法 | 报错 | 正确写法 |
|---|---|---|
| `step.frames[1:]`、`fo.values[:10]` | `OdbError: Slice for an ODB Sequence is not supported` | `for i in range(1, len(step.frames)): fr = step.frames[i]`，或先 `list(step.frames)` 再切片 |
| `json.dump` 里直接放 `v.data[0]` | `TypeError: … is not JSON serializable` | `float(v.data[0])`；整组 `[float(x) for x in v.data]` |
| `fo.position`、`fo.location` | `'FieldOutput' object has no attribute` | `fo.locations[0].position`；单个值上是 `v.position` |
| `fo.components` | 同上 | `fo.componentLabels` |
| `v.node`、`v.coordinates`、`v.location` | `'FieldValue' object has no attribute` | `v.nodeLabel` + `v.instance.getNodeFromLabel(v.nodeLabel).coordinates` |
| `inst.nodes.label`、`inst.nodes.coordinates` | `'OdbMeshNodeArray' object has no attribute` | 逐个取：`for n in inst.nodes: n.label, n.coordinates` |
| `frame.number`、`step.increments`、`odb.jobName` | `has no attribute` | `frame.frameId`、`frame.frameValue`（分析步内时间）、`frame.incrementNumber`、`frame.description`；`odb.name` |
| `asm.nodeSets['tip']`、集合找不到 | `KeyError` | 名字大写；直接写的 INP 的集合在 `asm.instances['PART-1-1'].nodeSets` 里；用上面的 `find_set`，或 `abq odb job.odb --sets` 看有哪些 |
| `inst.elemSets` | `has no attribute` | `inst.elementSets` |

- 这是 Python 2.7；键名用普通字符串；`print` 是语句或带括号的单参数形式；脚本里有中文要在第一行写 `# -*- coding: utf-8 -*-`。
- 对象的方法拿不准：`abq api getSubset`、`abq api FieldOutput.`。
- 路径上的分布：取一组节点（建模时建集合），按坐标排序后输出。
- 本 skill 目录下的 `read_odb_example.py` 是另一份跑通过的完整模板。

## 出图

```
abq render job.odb --var S                        Mises 云图（变形后，自动放大变形）
abq render job.odb --var U --comp U2 --view Front 位移分量
abq render job.odb --var S --inv "Max. Principal" 最大主应力
abq render job.odb --var U --step Modes --frame 2 第 2 阶振型
abq render job.inp                                求解前的模型图：网格 + 边界/载荷/约束符号
```

生成后用 `read_image` 看图，确认变形形态、最大值位置符合预期（例如悬臂梁最大位移在自由端、最大应力在固定端），再把图片路径写进报告。视角可选 Iso/Front/Back/Top/Bottom/Left/Right；`--undeformed` 不显示变形，`--scale` 指定变形放大倍数。

## 常见取数错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 取了 `frames[0]` | 全是 0 | 用最后一帧，或指定帧 |
| 多个分析步时没指定步 | 取到别的分析步的结果 | `--step 名字` |
| 用全模型最大应力代表"某处"的应力 | 取到约束边缘/加载点的奇异峰值 | 用 `--probe` 或 `--set` 取指定位置的值 |
| 拿积分点应力和理论表面应力比 | 偏低 | 用外推到节点的值，并加密网格 |
| 对称模型的反力/载荷没还原 | 差 2 倍或 4 倍 | 乘回对称份数，并在报告里说明 |
| 轴对称模型的反力当成单位弧度的 | 差 2π | 轴对称反力已是整圈合计 |
| 显式分析帧太少 | 看不到峰值/到达时刻 | 建模时加大 `numIntervals`，或用历程输出 |
| 把振型帧当成位移结果 | 数值无物理意义 | 物理位移取静力/动力分析步的帧 |
