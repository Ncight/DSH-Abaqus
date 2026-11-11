---
name: abaqus-inp-direct
description: 不经过 CAE、用 Python 直接生成 Abaqus 输入文件(INP)时加载。适用于几何规则、要精确控制节点位置的模型：结构化网格、向接触区或应力集中处渐密的网格、尺寸很小（亚毫米）的模型，或者 CAE 的草图/分区/网格反复失败的时候。给出经过验证的生成器模板、节点与单元编号规则、单元面编号、集合与表面、刚性单元加参考节点、接触对、边界、载荷、输出请求的关键字写法。
---

# 直接写 INP

## 什么时候走这条路

建模有两条路，按模型特点选，不必拘泥：

| | CAE 脚本（`abq script`） | 直接写 INP（Python 生成文本） |
|---|---|---|
| 适合 | 不规则几何、需要自动划分网格、多部件装配 | 规则几何（矩形、圆环、球冠、层状）、结构化或渐密网格、尺寸很小的模型 |
| 优点 | 几何和网格由 CAE 处理 | 每个节点的位置由你决定，不受 CAE 几何精度和网格算法影响，运行快 |
| 风险 | 特征创建失败、网格质量不可控 | 单元节点顺序、面编号、集合范围要自己保证正确 |

CAE 的草图、分区或网格连续两三次失败时，如果几何是规则的，就改走这条路，不要继续和 CAE 较劲。

## 模板

本 skill 目录下的 `gen_inp_template.py` 是跑通并与解析解核对过的生成器（平面应变土柱侧限压缩：沉降、应力、反力与理论一致）。用系统 Python 3 运行（`py gen.py`），生成后 `abq check job.inp` 看体检和集合节点数，再 `abq run job`。改写时保留它的结构：参数 → 坐标数组 → 节点 → 单元 → 集合/表面 → 截面/材料 → 边界 → 分析步（载荷、输出）。

## 必须自己保证的几件事

- **节点坐标**：二维/轴对称只写 x、y（轴对称 x 是半径 r ≥ 0，y 是轴向）。坐标用 `%.9g` 之类的格式写，够精度又不冗长。
- **渐密网格**：模板里的 `graded(n, total, ratio)` 给出按等比数列变化的节点坐标；接触区、孔边、固定端附近用小单元，远处用大单元，相邻单元尺寸比不超过约 1.5。
- **单元节点顺序**：四边形（CPE4、CPS4、CAX4 及带 R 的）按**逆时针**：左下、右下、右上、左上。顺时针会得到负面积报错。六面体 C3D8：先底面四个节点（从上往下看逆时针），再顶面对应的四个。
- **单元面编号**（加压强、定义接触面时要用）：
  - 四边形：S1 = 节点 1-2 边，S2 = 2-3，S3 = 3-4，S4 = 4-1。按上面的节点顺序就是 下、右、上、左。
  - 六面体：S1 = 底面(1-2-3-4)，S2 = 顶面(5-6-7-8)，S3 = 1-2-6-5，S4 = 2-3-7-6，S5 = 3-4-8-7，S6 = 4-1-5-8。
- **集合**：`*Nset, nset=NAME, generate` 的数据行是 `起始号, 终止号, 步长`；不规则的集合直接逐个列出，每行不超过 16 个。建完用 `abq check` 核对每个集合的节点数。
- **二维单元要截面厚度**：`*Solid Section` 的数据行写厚度（平面应变/平面应力通常写 1.0；轴对称不需要）。
- 关键字不区分大小写；数据行以逗号分隔；以 `**` 开头的是注释。

## 关键字写法（均来自跑通的输入文件）

```
*Element, type=CPE4, elset=BLOCK
1, 1, 2, 23, 22
*Nset, nset=BOTTOM, generate
1, 21, 1
*Elset, elset=TOPROW, generate
181, 200, 1
*Surface, type=ELEMENT, name=BLOCKTOP
TOPROW, S3
*Solid Section, elset=BLOCK, material=MAT
1.0,
*Material, name=MAT
*Elastic
1000000., 0.3
*Density
1.8,
*Boundary
BOTTOM, 2, 2
CORNER, 1, 1
*Step, name=Load, nlgeom=NO, inc=100
*Static
0.25, 1., 1e-06, 0.25
*Dsload
BLOCKTOP, P, 100.
*Cload
TIPNODES, 2, -10.
*Boundary
REF, 2, 2, -0.0001
*Output, field
*Node Output
U, RF
*Element Output
S, E
*Contact Output
CSTRESS, CDISP
*Output, history, variable=PRESELECT
*End Step
```

- `*Boundary` 数据行：`节点或节点集, 起始自由度, 终止自由度[, 给定值]`；写在 `*Step` 之前的是初始约束，写在分析步里的可以带非零给定值。
- `*Dsload` 的 `P` 是压强，正值压向表面；`*Cload` 是集中力，节点集里**每个节点**各加这么多。
- `*Static` 数据行：初始增量, 分析步时长, 最小增量, 最大增量。
- 模态分析把 `*Static` 换成 `*Frequency`（数据行写阶数），材料要有 `*Density`。重力：`*Dload` 数据行 `单元集, GRAV, 9.8, 0., -1., 0.`。

### 刚性面（刚性单元 + 参考节点）与接触对

```
** 平面/轴对称里的刚性线：R2D2（平面）或 RAX2（轴对称）单元；另设一个参考节点承载刚体的运动和合反力
*Element, type=R2D2, elset=PLATEN
9001, 9002, 9001
*Nset, nset=REF
9000
*Rigid Body, ref node=REF, elset=PLATEN
*Surface, type=ELEMENT, name=PLATENSURF
PLATEN, SPOS
*Surface Interaction, name=SMOOTH
1.0,
*Surface Behavior, pressure-overclosure=HARD
*Contact Pair, interaction=SMOOTH, type=SURFACE TO SURFACE
BLOCKTOP, PLATENSURF
*Boundary
REF, 1, 1
REF, 6, 6
```

- `*Contact Pair` 数据行是 `从面, 主面`；刚性面只能当主面。有摩擦时在 `*Surface Interaction` 下加 `*Friction` 和一行摩擦系数。
- 刚性线单元的接触侧（`SPOS`）在"从第 1 个节点指向第 2 个节点的方向逆时针转 90°"的那一侧。例：压在物体**上方**的水平刚性线要让法向朝下，节点顺序应是从右到左（上例 `9002, 9001`）；垫在物体**下方**的刚性平面法向朝上，节点顺序从左到右。算完如果接触压力和反力是 0，把两个节点的顺序对调。
- 参考节点没被约束的自由度要全部给定（平动 1、2 和转动 6），加载通过参考节点的位移或集中力施加，合力从参考节点的反力读。
- 默认的面-面接触用罚函数实现，有很小的穿透；位移很小的算例里它会让力比理论值低 1%～2%，属正常。

## 之后

`abq check` 体检 → `abq run` 求解。直接写的 INP 没有部件/实例，集合名就是全局名；ODB 里实例名是 `PART-1-1`。
