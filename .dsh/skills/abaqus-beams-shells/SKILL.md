---
name: abaqus-beams-shells
description: 用梁单元、桁架单元或壳单元建模时加载：框架、梁、柱、桁架、板、筒壳、薄壁结构。给出经过验证的线框部件+截面剖面+截面方向写法、壳截面与厚度、简支/固支/铰接边界的正确约束、线载荷与面压、内力与弯矩输出(SF/SM/RM)的读法，以及梁壳建模的常见错误。
---

# 梁单元与壳单元

## 梁（已验证的写法）

```python
s = m.ConstrainedSketch(name='frame', sheetSize=20.0)
s.Line(point1=(0.0, 0.0), point2=(0.0, 3.0))                # 线框草图：每条线是一根杆
s.Line(point1=(0.0, 3.0), point2=(4.0, 3.0))
fr = m.Part(name='Frame', dimensionality=THREE_D, type=DEFORMABLE_BODY)
fr.BaseWire(sketch=s)                                        # 草图在部件的 x-y 平面内

m.RectangularProfile(name='Rect', a=0.2, b=0.4)              # a 沿截面局部 1 轴，b 沿局部 2 轴
m.BeamSection(name='BeamSec', integration=DURING_ANALYSIS, profile='Rect', material='Steel', poissonRatio=0.3)
region = fr.Set(name='AllBeams', edges=fr.edges)
fr.SectionAssignment(region=region, sectionName='BeamSec')
fr.assignBeamSectionOrientation(region=region, method=N1_COSINES, n1=(0.0, 0.0, -1.0))   # 必须指定
fr.seedPart(size=0.25)
fr.setElementType(regions=(fr.edges,), elemTypes=(mesh.ElemType(elemCode=B31, elemLibrary=STANDARD),))
fr.generateMesh()

feet   = a.Set(name='Feet', vertices=fi.vertices.getByBoundingBox(yMax=TOL))    # 支座是顶点集合
girder = a.Set(name='Girder', edges=fi.edges.getByBoundingBox(yMin=3.0 - TOL))
m.EncastreBC(name='FixFeet', createStepName='Initial', region=feet)
m.LineLoad(name='Udl', createStepName='Load', region=girder, comp2=-20.0)       # 单位长度上的力，全局方向
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'RF', 'SF'))  # SF 同时带出 SM
```

要点：

- **截面方向**：`n1` 是截面局部 1 轴的大致方向，不能与任何一根梁的轴线平行。局部 2 轴 = 梁轴 × n1 的方向。矩形截面的 `a` 沿 1 轴、`b` 沿 2 轴——绕 1 轴弯曲时截面高度是 `b`。平面框架常用做法：结构放在 x-y 平面，`n1=(0,0,-1)`，此时面内弯曲的截面高度是 `b`。**先想清楚哪个尺寸是受弯高度**，写反了刚度差 (h/b)² 倍。
- 其他剖面：`CircularProfile(name, r)`、`PipeProfile(name, r, t)`、`IProfile(...)`、`BoxProfile(...)`，参数用 `abq api CircularProfile` 查。
- **单元**：`B31` 含剪切变形（深梁、与实体结果对比时更准）；`B33` 是欧拉梁（细长梁、对照 qL⁴/(384EI) 这类公式时用它，或接受 B31 多出来的几个百分点剪切挠度）；`B32` 二次。每根杆至少 8～10 个单元，才能得到准确的挠度曲线和内力分布。
- **约束**：梁节点有 6 个自由度。固支 = 6 个全约束（`EncastreBC`）；铰支 = 只约束平动（`DisplacementBC(u1=0.0, u2=0.0, u3=0.0)`），转动放开。三维空间里的直梁还要防止绕自身轴线转动和面外刚体运动，否则出现零主元——平面问题可以约束所有节点的面外位移和面外转动（u3、ur1、ur2）。
- **二维梁**：`dimensionality=TWO_D_PLANAR` + `B21`/`B23`，不需要截面方向，自由度只有 u1、u2、ur3。
- **载荷**：均布载荷用 `LineLoad`（力/长度）；集中力加在顶点集合上；自重用 `Gravity`（需要密度）。
- **结果**：`RF` 是支座反力，`RM` 是支座反力矩（请求 RF 即有）。截面内力 `SF1` 轴力、`SF2`/`SF3` 剪力，`SM1` 绕局部 1 轴的弯矩、`SM2` 绕 2 轴、`SM3` 扭矩。`abq odb job.odb --vars U,RF,RM,SF,SM` 查看。梁的 `S11` 是截面积分点上的轴向应力，最大弯曲应力要取截面最外侧的积分点或用 M·c/I 手算核对。
- 桁架：`m.TrussSection(name='T', material='Steel', area=A)` + `T3D2`，每根杆只划 1 个单元，节点是铰。

## 壳（已验证的写法）

```python
s = m.ConstrainedSketch(name='plate', sheetSize=20.0)
s.rectangle(point1=(0.0, 0.0), point2=(2.0, 1.0))
pl = m.Part(name='Plate', dimensionality=THREE_D, type=DEFORMABLE_BODY)
pl.BaseShell(sketch=s)                                        # 中面；法向是 +z
m.HomogeneousShellSection(name='ShellSec', material='Steel', thickness=0.02, numIntPts=5)
pl.SectionAssignment(region=pl.Set(name='AllFaces', faces=pl.faces), sectionName='ShellSec')
pl.seedPart(size=0.1)
pl.setElementType(regions=(pl.faces,), elemTypes=(mesh.ElemType(elemCode=S4R, elemLibrary=STANDARD),))
pl.generateMesh()

edges = a.Set(name='PlateEdges', edges=pi.edges)
m.DisplacementBC(name='SimplySupported', createStepName='Initial', region=edges, u3=0.0)   # 简支：只约束挠度
top = a.Surface(name='PlateTop', side1Faces=pi.faces)        # side1 = 正法向一侧(SPOS)，side2 = 负法向一侧(SNEG)
m.Pressure(name='Q', createStepName='Load', region=top, magnitude=5.0)   # 正值压向该侧表面，即沿 -法向
```

要点：

- **简支板**：边上只约束法向位移（上例 u3），转动放开；再用最少的面内约束消除刚体位移（如一个角点约束 u1、u2，另一个角点约束 u2）。把边上的面内位移或转动也约束了就不是简支，挠度会偏小。**固支**：边上 6 个自由度全约束。
- **对称边界**用 `XsymmBC`/`YsymmBC`（它们同时约束了正确的转动分量），不要自己拼。
- 厚度在截面里给，几何只是中面；`numIntPts=5` 是厚度方向积分点数，材料非线性时取 5～9。
- **应力在厚度方向上变化**：壳的 `S` 在每个截面点上都有值，弯曲应力最大值在上下表面。`abq odb` 给出的是所有截面点里的最值；核对用 σ = 6M/t²。
- 小挠度理论只在挠度远小于板厚时成立；挠度接近板厚要开 `nlgeom=ON`（薄膜效应使板变硬）。对照经典薄板公式时保持 `nlgeom=OFF`。
- 网格：每边至少 16～20 个 `S4R` 才能把挠度算到 1%～2% 以内；弯矩/应力收敛更慢，再加密核对。
- 载荷方向与法向有关：建完用 `abq check` 和预览面板确认压强箭头方向。
- 壳与实体、梁与壳连接处转动自由度不连续，需要 `ShellToSolidCoupling` 或耦合约束，不能只共节点。

## 核对

- 反力合计 = 施加的总载荷（线载荷×长度、压强×面积）。
- 挠度与梁/板理论比较，偏差超过几个百分点先查：截面方向（a、b 是否反了）、边界约束是否过多或过少、单元类型（B31 与欧拉梁差一个剪切变形）、网格份数。
