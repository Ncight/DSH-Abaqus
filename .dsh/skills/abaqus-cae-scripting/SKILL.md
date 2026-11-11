---
name: abaqus-cae-scripting
description: 写或改 Abaqus/CAE Python 建模脚本时加载。给出经过验证的脚本骨架（部件→材料→分区→网格→装配→分析步→边界载荷→导出 INP）、选几何建集合/表面的可靠写法、二维与轴对称部件、网格种子与单元类型设置、常见脚本报错的原因。任何需要从零建模的仿真任务都先读它。
---

# Abaqus/CAE 建模脚本

## 起步：从模板改，不要从空白写

本 skill 目录下有一份跑通过的模板 `template_solid_static.py`（三维实体静力，含分区、结构化网格、集合/表面、重力与压强、导出 INP）。读它，复制到工作目录改写。脚本顺序固定为：

参数 → 部件(草图+特征) → 材料/截面/截面指派 → 分区 → 网格(种子→控制→单元类型→生成) → 装配与集合/表面 → 分析步与输出 → 边界/载荷 → `writeInput()`

运行用 `abq script model.py`；它会报错误位置，并对新导出的 INP 自动体检。

## 必须遵守

- Abaqus 自带 **Python 2.7**：没有 f-string；整数相除是整除，尺寸写成浮点数（`L / 2.0` 或 `L = 2.0`）。
- 开头三行 import 缺一不可；`from caeModules import *` 提供 `mesh`、`regionToolset` 以及建分析步/载荷/接触的方法。
- `from abaqus import *` 之后，内置的 `sum` 被 Abaqus 的同名函数覆盖，不接受生成器：`sum(x for x in ...)` 会报 `TypeError: arg1 … 'generator'`。写成 `sum([x for x in ...])` 或 `math.fsum(...)`。（用 `abq python` 跑的读 ODB 脚本不受影响。）
- 脚本第一句用 `Mdb()` 开一个空数据库，保证重复运行结果一致。
- 所有尺寸、材料、载荷写成顶部的参数，后面只用参数，不写魔法数字。网格尺寸、作业名这类参数按模板写成 `PARAMS.get('SEED', 默认值)` 的形式，之后就能用 `abq sweep model.py --set SEED=0.02,0.01` 做网格收敛对比。
- 脚本只负责导出 INP：`mdb.Job(name=JOB, model='Model-1').writeInput(consistencyChecking=OFF)`。不要 `submit()`，求解交给 `abq run`。
- 方法的参数名拿不准就查：`abq api StaticStep`、`abq api seedEdgeByBias`。本机是 2021 版，Tie/接触的参数叫 `master=`、`slave=`。

## 选几何：用包围盒，少用 findAt

```python
TOL = 1e-6
fix  = a.Set(name='FixEnd', faces=inst.faces.getByBoundingBox(zMax=TOL))            # z=0 平面上的面
top  = a.Surface(name='Top', side1Faces=inst.faces.getByBoundingBox(yMin=H - TOL))  # 实体的外表面用 side1Faces
line = a.Set(name='Mid', edges=inst.edges.getByBoundingBox(zMin=L/2 - TOL, zMax=L/2 + TOL, yMax=TOL))
pts  = a.Set(name='Corner', vertices=inst.vertices.getByBoundingBox(xMax=TOL, yMax=TOL, zMax=TOL))
```

- `getByBoundingBox` 只返回**完全落在盒内**的面/边/点；没写的方向不限制。
- 选不到东西不会报错，只会得到空集合，之后边界/载荷就不起作用——建完集合后用 `abq check` 看每个集合的节点数是不是合理。
- 两段选取可以相加：`edges.getByBoundingBox(...) + edges.getByBoundingBox(...)`。
- `findAt(((x, y, z),))` 的点必须落在目标面/边的内部（不能在边界上），坐标不准就会失败；只在包围盒不好表达时用。
- 在 `part` 上选（`p.faces`）得到部件级集合，装配里叫 `实例名.集合名`；在实例上选（`inst.faces`）建的是装配级集合。边界、载荷、接触用装配级的最不容易出错。
- 分区或重新生成网格之后，之前取到的 `p.cells`、`p.faces` 引用已经失效，要重新取。
- 需要在几何内部某处加载或取结果，就先分区，让那里出现面/边/点。

## 二维与轴对称

```python
p = m.Part(name='Plate', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)   # 平面应力/平面应变由单元类型决定
p.BaseShell(sketch=s)                                                          # 二维实体用 BaseShell
m.HomogeneousSolidSection(name='Sec', material='Steel', thickness=0.01)        # thickness = 面外厚度
p.SectionAssignment(region=p.Set(name='All', faces=p.faces), sectionName='Sec')
edge_surf = a.Surface(name='Right', side1Edges=inst.edges.getByBoundingBox(xMin=W - TOL))  # 二维的表面由边组成
```

`BaseShell` 的草图必须是一个**闭合**轮廓。例如轴对称的球：半圆弧加一条沿轴线的直线把它封闭（`s.ArcByCenterEnds(...)` + `s.Line(point1=(0.0, 0.0), point2=(0.0, 2*R))`）；轮廓不闭合会报"创建壳的…特征失败"。

轴对称：`dimensionality=AXISYMMETRIC`，草图里 x 是半径 r（必须 ≥ 0）、y 是轴向，先画一条过原点的竖直构造线 `s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))` 作对称轴。轴对称模型里集中力和反力是**整圈的合计值**；轴线上的节点要约束 U1。

在二维面上切出加密区或内部边界：`p.PartitionFaceBySketch(faces=p.faces, sketch=sk)`（`sk` 里只画切割线）。

## 尺寸很小的模型：换单位制

CAE 的几何精度约为 0.000001 个模型单位。需要的最小单元或最小几何特征小于约 0.0001 个模型单位时（例如用米建模，接触区单元只有十几微米），会出现草图/分区特征创建失败，自由网格里出现面积为零的塌缩单元（数据检查报 `area … zero, small, or negative`）。不要在原单位制里反复调网格：把整个模型改成 mm 制（长度 mm、力 N、应力 MPa、密度 t/mm³，钢 E=206000、ρ=0.00000000785）重建，结果再换算回任务书要求的单位。判断方法：先估出关心区域的尺寸（接触半宽、圆角半径、孔径），除以 10 得到单元尺寸，再看它相对模型单位是不是小于 0.0001。

## 网格

```python
p.seedPart(size=0.01)                                              # 全局尺寸
p.seedEdgeBySize(edges=fine_edges, size=0.0025, constraint=FINER)  # 局部加密
p.seedEdgeByNumber(edges=some_edges, number=10, constraint=FINER)  # 指定份数
p.seedEdgeByBias(biasMethod=SINGLE, end1Edges=radial, ratio=4.0, number=20, constraint=FINER)  # 向一端加密
p.setMeshControls(regions=p.cells, elemShape=HEX, technique=STRUCTURED)   # 二维: regions=p.faces, elemShape=QUAD
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8I, elemLibrary=STANDARD),))
p.generateMesh()
```

- 偏置加密朝哪一端由边的方向决定：先生成，用 `abq check` 或预览面板看小单元在不在该密的一端，不对就把 `end1Edges` 换成 `end2Edges`。
- 结构化/扫掠网格划不出来时：先分区成简单块，或改 `elemShape=TET, technique=FREE`（二维 `QUAD_DOMINATED, FREE`）。
- 一个区域同时给四边形和三角形单元类型：`elemTypes=(mesh.ElemType(elemCode=CPE4R, ...), mesh.ElemType(elemCode=CPE3, ...))`。
- 实例用 `dependent=ON` 时在**部件**上划网格。
- 选哪种单元、网格多密，见 skill `abaqus-elements-mesh`。

## 分析步、输出、边界、载荷

```python
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF, initialInc=0.1, maxInc=1.0, minInc=1e-6, maxNumInc=200)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CF'))
m.EncastreBC(name='Fix', createStepName='Initial', region=fix)
m.DisplacementBC(name='Push', createStepName='Load', region=top_set, u2=-0.001)   # 没写的自由度保持自由
m.boundaryConditions['Push'].setValuesInStep(stepName='Load2', u2=-0.005)          # 在后续分析步改值
m.Pressure(name='P', createStepName='Load', region=top, magnitude=50.0)           # 正值压向表面
m.ConcentratedForce(name='F', createStepName='Load', region=pts, cf2=-10.0)       # 集合里每个节点各加这么多
m.SurfaceTraction(name='T', createStepName='Load', region=top, magnitude=20.0,
                  directionVector=((0.0, 0.0, 0.0), (0.0, -1.0, 0.0)), traction=GENERAL)  # 指定方向的面力
m.Gravity(name='G', createStepName='Load', comp2=-9.8)                            # 需要材料有密度
```

- `ConcentratedForce` 加在含 n 个节点的集合上，总力是 n 倍。要把一个总力均匀作用在面上，用 `SurfaceTraction` 或 `Pressure`（数值 = 总力 / 面积）。
- 约束固定端用 `EncastreBC`；只约束法向用 `DisplacementBC(u3=0.0)` 或对称边界 `XsymmBC/YsymmBC/ZsymmBC`。约束要刚好消除刚体位移，不多不少。
- 参考点：`rp = a.ReferencePoint(point=(x, y, z))`，`a.Set(name='RP', referencePoints=(a.referencePoints[rp.id],))`。

## 报错对照

| 报错 | 原因 |
|---|---|
| `'Model' object has no attribute 'Tie'`（或 StaticStep、Pressure…） | 少了 `from caeModules import *` |
| `keyword error on xxx` / 参数个数不对 | 参数名写错，用 `abq api 方法名` 查 |
| `NameError: name 'XXX' is not defined` | 符号常量拼错，或少了 `from abaqusConstants import *` |
| `KeyError: xxx` | 按名字取的对象不存在（集合、材料、分析步名字不一致） |
| findAt 找不到 | 坐标没落在几何上，改用包围盒 |
| 特征创建失败（草图、拉伸、分区） | 草图不闭合或自相交；分区线没有把面完全切开；尺寸相对几何精度太小（见"尺寸很小的模型"） |
| 解析刚性表面特征失败 | 见 skill `abaqus-contact` 里的四条规则（轴对称要对称轴构造线、轮廓不闭合、不跨轴、单段弧小于 180°） |
| 无法划分网格 | 需要分区，或改自由网格 |
| `Invalid variables are specified in an output request` | 输出变量名在该分析步/单元下无效，先只留 S、U、RF |
| `TypeError: arg1; … 'generator'` | 对生成器用了 `sum()`，改成列表或 `math.fsum` |
| `TypeError: … 参数过多` | 参数个数不对，用 `abq api 方法名` 查签名 |
| `AttributeError: 'NoneType' object has no attribute …` | 上一步返回了 None。最常见的是 `findAt`：没找到、或把 x、y、z 分开传时它**不报错而是返回 None**。正确形式：`findAt((x, y, z))` 取一个对象，`findAt(((x1, y1, z1),), ((x2, y2, z2),))` 取一组；点要落在目标内部 |
| `IndexError: … index out of range` | 对空序列取了下标：上一步的选取/草图/分区没有生成对象（`getByBoundingBox` 选空不报错） |

接触见 `abaqus-contact`，梁壳见 `abaqus-beams-shells`，动力见 `abaqus-dynamics`，材料与单位见 `abaqus-materials-units`，读结果见 `abaqus-postprocess`，算不动时见 `abaqus-debug`。
