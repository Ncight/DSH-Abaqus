---
name: abaqus-contact
description: 模型里有接触或绑定(Tie)时加载：两个部件相互挤压、压头/垫块/轴承/螺栓、刚性面压弹性体、部件之间需要连接。给出经过验证的接触对脚本写法（可变形体对可变形体、解析刚性面带参考点）、主从面选择、网格与加载顺序、让接触收敛的做法、以及接触结果怎么读怎么核对。
---

# 接触与 Tie

## 先决定用哪种连接

- 两个部件始终粘在一起、不关心界面滑移和脱开 → **Tie**（最稳，不产生非线性）。
- 会脱开、滑移，或关心接触压力 → **接触对**（面-面接触）。
- 部件很多、谁碰谁说不清 → 通用接触（`m.ContactStd(...)` / 显式 `m.ContactExp(...)`）。
- 相邻部件之间什么都不定义 = 没有连接，各算各的；`abq check` 的体检会提醒。

## 接触对：可变形体对可变形体（已验证的写法）

```python
# 主面 = 较硬/较粗/较大的一侧；从面 = 较软/较细的一侧。从面网格不要比主面粗。
master = a.Surface(name='PunchBottom', side1Edges=ip.edges.getByBoundingBox(yMax=0.10 + TOL))  # 三维用 side1Faces
slave  = a.Surface(name='BaseTop',     side1Edges=ib.edges.getByBoundingBox(yMin=0.10 - TOL))

prop = m.ContactProperty('Friction')
prop.NormalBehavior(pressureOverclosure=HARD, allowSeparation=ON)
prop.TangentialBehavior(formulation=PENALTY, table=((0.3,),), fraction=0.005, maximumElasticSlip=FRACTION)  # 无摩擦就不写这句
m.SurfaceToSurfaceContactStd(name='PunchOnBase', createStepName='Initial', master=master, slave=slave,
                             sliding=FINITE, interactionProperty='Friction', adjustMethod=NONE)

# 第 1 步用很小的位移把接触建立起来，第 2 步再加真正的载荷
m.StaticStep(name='Touch', previous='Initial', nlgeom=ON, initialInc=0.1, maxInc=0.25, minInc=1e-6, maxNumInc=200)
m.StaticStep(name='Press', previous='Touch', initialInc=0.1, maxInc=0.25, minInc=1e-6, maxNumInc=200)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CSTRESS', 'CDISP'))
m.DisplacementBC(name='PushPunch', createStepName='Touch', region=ptop, u1=0.0, u2=-1.0e-5)
m.boundaryConditions['PushPunch'].setValuesInStep(stepName='Press', u2=-5.0e-4)
```

## 解析刚性面 + 参考点（已验证的写法）

```python
s = m.ConstrainedSketch(name='arc', sheetSize=1.0)
# 解析刚性面的单段圆弧必须小于 180°：半圆要画成两段四分之一圆
s.ArcByCenterEnds(center=(0.0, 0.05), point1=(-0.05, 0.05), point2=(0.0, 0.0), direction=COUNTERCLOCKWISE)
s.ArcByCenterEnds(center=(0.0, 0.05), point1=(0.0, 0.0), point2=(0.05, 0.05), direction=COUNTERCLOCKWISE)
rig = m.Part(name='Indenter', dimensionality=TWO_D_PLANAR, type=ANALYTIC_RIGID_SURFACE)   # 轴对称用 AXISYMMETRIC
rig.AnalyticRigidSurf2DPlanar(sketch=s)
rp = rig.ReferencePoint(point=(0.0, 0.05, 0.0))            # 刚体的全部运动和合反力都在参考点上
ir = a.Instance(name='Indenter-1', part=rig, dependent=ON)
rpset = a.Set(name='RP', referencePoints=(ir.referencePoints[rp.id],))
master = a.Surface(name='IndenterSurf', side2Edges=ir.edges)   # 朝向从面的那一侧；选错侧接触不会发生
m.DisplacementBC(name='Push', createStepName='Press', region=rpset, u1=0.0, u2=-0.002, ur3=0.0)  # 参考点的自由度要全部约束或给定
```

- 刚性面只能当主面，不用划网格、不用赋材料。
- **朝向**：`side1Edges` 还是 `side2Edges` 取决于草图的绘制方向。算完如果反力和 CPRESS 全是 0、并有警告 `FACING IN THE WRONG DIRECTION`，就把 side1 换成 side2 重算。壳和梁的表面同理（`side1Faces`/`side2Faces`）。
- 刚性平面：草图画一条直线即可；三维刚性面用离散刚体或 `AnalyticRigidSurfExtrude`/`AnalyticRigidSurfRevolve`（参数用 `abq api` 查）。
- **轴对称的解析刚性面**（已实测）：部件用 `dimensionality=AXISYMMETRIC, type=ANALYTIC_RIGID_SURFACE`，仍然调用 `AnalyticRigidSurf2DPlanar(sketch=s)`。四条规则，违反任何一条都报"解析刚性表面特征失败"：
  1. 草图里先画对称轴构造线：`s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))`；
  2. 轮廓是一条**不闭合**的线/弧链（不能画成矩形等闭合图形）；
  3. 轮廓全部在 r ≥ 0 一侧，可以从轴线出发，不能跨过轴线；
  4. 单段圆弧小于 180°。
  轴对称刚性平面就是一条从轴线出发的水平线：`s.Line(point1=(0.0, 0.0), point2=(R_plane, 0.0))`，再 `rig.ReferencePoint(point=(0.0, 0.0, 0.0))`。

## 让接触算得动

1. **初始位置**：两面刚好贴合（间隙为 0）最好。有间隙时，被推动的部件在接触前没有约束，纯力加载会出现刚体位移而发散。
2. **加载方式**：优先位移控制（给压头/参考点位移，从反力读出力）。必须加指定的力时：先用一个小位移步建立接触，下一步去掉该位移约束改加力；或直接用位移试算，由"力—位移"关系调整位移使反力等于目标值（接触问题近似满足幂律，线接触力∝位移的 1 次多一点，点接触力∝位移的 1.5 次，迭代两三次即可到 1% 以内）。
3. **增量步**：`initialInc` 取 0.05～0.1，`minInc=1e-6`，`maxNumInc` 给够。
4. **网格**：接触区内至少 8～10 个单元（先按解析解估接触半宽），接触面用线性单元，从面不比主面粗。接触半宽常常只有零点几毫米：按"接触半宽 ÷ 10"算出单元尺寸，若小于约 0.0001 个模型单位（用米建模时很常见），整个模型改用 N–mm–MPa 建，否则会出现特征创建失败和面积为零的单元；只在接触区附近分区加密，远处放粗。几何规则（球、圆柱、平板）时，更稳的做法是直接生成 INP：节点按解析坐标布置、向接触点渐密，刚性面用刚性单元加参考节点（见 skill `abaqus-inp-direct`）。
5. **几何非线性**：有明显滑移或转动时 `nlgeom=ON`，`sliding=FINITE`；小滑移、小变形可用 `sliding=SMALL`。
6. 还不收敛：看 `abq run` 给出的残差位置；检查主从面是否选反、表面是否选对了面（`abq check` 列出每个表面的面数）、是否有初始穿透；最后才考虑加少量接触稳定（并用 `abq verify` 确认 ALLSD/ALLIE < 5%）。

## 读结果、做核对

- `abq odb job.odb --vars U,RF,CPRESS,S`：接触压力场的名字是 `CPRESS   <从面>/<主面>`，给出最大值和所在节点。
- 传递的总力 = 被约束一侧的反力合计。最稳妥的读法是把力或位移加在参考点上（刚性面、耦合约束），直接读参考点的反力；两端都有约束时全模型净合力为 0，可以看输出里的"正值合计 / 负值合计"，但二次单元的节点反力正负混杂时这两个数不可靠，要按节点集分别求和（`abq odb job.odb --set 集合名 --vars RF`）。
- 轴对称模型的反力是整圈合计；平面应变/平面应力模型的反力对应截面厚度（`HomogeneousSolidSection` 的 `thickness`）。
- **接触区尺寸**：取从面上接触压力大于 0 的节点，离接触中心最远的那个节点的距离就是接触半径（半宽）。它的分辨率就是节点间距，网格不够密时会整单元地跳，报告里写明节点间距。读各节点压力和坐标的写法见 `abaqus-postprocess`。
- 核对：接触压力在接触面上的积分应等于总力；与解析解（Hertz 等）比峰值压力和接触半宽；两者都对网格敏感，加密一次确认。
- `abq verify` 提示"接触压力处处为 0"说明接触根本没建立。

## Tie

```python
m.Tie(name='PadTie', master=a.surfaces['BeamTop'], slave=a.surfaces['PadBottom'])   # 从面取网格较细的一侧
```

- 从面节点离主面超过位置容差就**不会被绑定**，而且只给一条警告。两面之间有缝时设 `positionToleranceMethod=SPECIFIED, positionTolerance=缝宽的 1.5 倍`，并在 `abq check` 的体检和求解警告里确认没有未绑定节点。
- 同一个节点不要既被 Tie 当从节点又加位移边界条件，会过约束。
