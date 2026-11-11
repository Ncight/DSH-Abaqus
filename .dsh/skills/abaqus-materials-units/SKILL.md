---
name: abaqus-materials-units
description: 定义材料或核对单位制时加载：弹性、密度、金属塑性(*Plastic 表)、混凝土损伤塑性(CDP)、摩尔-库仑、阻尼、热膨胀，以及 SI / m–t–s / mm–t–s 三套单位制下的数值换算和自检方法。结果量级离谱、频率或波速不对、塑性不屈服或一加载就屈服时也要读。
---

# 材料输入与单位制

## 单位制

Abaqus 不认识单位，输入必须自洽。运行 `abq units` 看完整对照表。要点：

| 单位制 | 长度 | 力 | 质量 | 应力 | 密度 | g |
|---|---|---|---|---|---|---|
| SI | m | N | kg | Pa | kg/m³ | 9.8 |
| m–t–s（本项目默认） | m | kN | t | kPa | t/m³ | 9.8 |
| mm–t–s | mm | N | t | MPa | t/mm³ | 9800 |

- 任务书指定单位制就用任务书的；整个模型（几何、E、密度、载荷、g）必须同一套，结果也在同一套里。
- 钢：SI 下 E=206000000000、ρ=7850；m–t–s 下 E=206000000、ρ=7.85；mm–t–s 下 E=206000、ρ=0.00000000785。
- 自检：√(E/ρ) 是波速。m 制下钢约 5100、混凝土约 3500、土 100～1000；mm 制下是它的 1000 倍。`abq check` 自动给出这个值。
- 压强/面力用应力单位，线载荷用 力/长度，集中力用力单位，体力/重力靠密度×加速度。
- 零件尺寸是毫米级、或需要亚毫米的单元时，用 mm–t–s 制建模（CAE 的几何精度约 0.000001 个模型单位，用米建模会失败）；结果按 1 MPa = 1000000 Pa、1 mm = 0.001 m 换算回去。
- 换算最容易错的是密度和重力加速度：m–t–s 里密度是 t/m³（混凝土 2.5，不是 2500），mm–t–s 里 g=9800。

## 弹性与密度

```python
mat = m.Material(name='Steel')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))        # 有重力、模态、动力时必须定义
```

ν 必须 < 0.5；接近 0.5 时用杂交单元。

## 金属塑性

```python
# 每行 (真应力, 塑性应变)。第一行塑性应变必须是 0（即屈服应力），应力不能下降，最后一行之后按理想塑性外延
mat.Plastic(table=((3.45e5, 0.0), (4.5e5, 0.05), (5.0e5, 0.15)))
```

- 表里填的是**真应力—对数塑性应变**，不是工程应力应变、也不是总应变。由工程值换算：σ_真 = σ_工(1+ε_工)，ε_塑 = ln(1+ε_工) − σ_真/E。小应变（< 2%）时两者差别可忽略。
- 线性强化（塑性模量 H = dσ/dε_塑）：两行即可，`((σy, 0.0), (σy + H·ε1, ε1))`，ε1 取得比预计的最大塑性应变大。注意 H 是对**塑性应变**的斜率；若给的是切线模量 Et（对总应变），H = E·Et/(E−Et)。
- 单轴应力状态下：总应变 ε = σ/E + ε_塑。核对时用它反算。
- 默认等向强化；循环加载需要随动强化时用 `hardening=KINEMATIC`。
- 屈服后要有足够的增量步（`initialInc` ≤ 0.05），大应变开 `nlgeom=ON`。
- 输出里看塑性：`PEEQ`（等效塑性应变）、`PE`。

## 混凝土损伤塑性（CDP）

```python
mc.ConcreteDamagedPlasticity(table=((30.0, 0.1, 1.16, 0.667, 0.0005),))    # 膨胀角, 偏心率, fb0/fc0, K, 粘性系数
mc.concreteDamagedPlasticity.ConcreteCompressionHardening(table=((fc0, 0.0), (fcu, eps_in1), ...))   # (压应力, 非弹性应变)
mc.concreteDamagedPlasticity.ConcreteTensionStiffening(table=((ft, 0.0), (ft2, eps_ck1), ...))       # (拉应力, 开裂应变)
mc.concreteDamagedPlasticity.ConcreteCompressionDamage(table=((0.0, 0.0), (dc1, eps_in1), ...))
mc.concreteDamagedPlasticity.ConcreteTensionDamage(table=((0.0, 0.0), (dt1, eps_ck1), ...))
```

- 压缩硬化表首行非弹性应变为 0；非弹性应变 = 总应变 − σ/E0。损伤因子在 0～1 之间且随应变不减。
- 粘性系数取小值（如 0.0005）帮助隐式收敛，过大会让结果偏硬；显式里取 0。
- 受拉软化对网格敏感，结果要说明网格尺寸。损伤输出：`DAMAGEC`、`DAMAGET`、`SDEG`。
- 关键字写法、受拉软化表在 Abaqus 里怎么换算和插值（手算核对时要用）：skill `abaqus-geotech`。

## 土体

```python
soil.MohrCoulombPlasticity(table=((phi_deg, psi_deg),))                    # 摩擦角, 剪胀角（度）
soil.mohrCoulombPlasticity.MohrCoulombHardening(table=((c, 0.0),))         # 粘聚力, 塑性应变
```

有自重的土体先做地应力平衡（`GeostaticStep` + 初始应力），否则一加重力就产生很大的假沉降。

对应的关键字是 `*Mohr Coulomb` 和 `*Mohr Coulomb Hardening`（不是接口名）。不排水强度、固结（孔压单元）、粘性边界：skill `abaqus-geotech`。

## 阻尼

瑞利阻尼 `mat.Damping(alpha=..., beta=...)`：阻尼比 ξ = α/(2ω) + βω/2。给定两个圆频率 ω1、ω2 处同为 ξ：α = 2ξω1ω2/(ω1+ω2)，β = 2ξ/(ω1+ω2)。显式分析里 β 会大幅减小稳定步长，尽量取 0。

## 其他

- 热膨胀 `mat.Expansion(table=((alpha,),))`，配合温度场 `m.Temperature(...)`。
- 超弹性、蠕变等其他属性：`abq api Material.` 列出 Material 对象的全部方法，再 `abq api 方法名` 看参数。
