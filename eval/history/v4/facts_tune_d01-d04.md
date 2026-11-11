# 专业组调优集（d01–d04）构建时实测到的 Abaqus 2021 事实

来源：构建和验证评测题 d01–d04 时逐条实跑得到（2026-11-10）。这些来自**调优集**，可以写进指引；写进 skill 之前仍要在 `eval/skill_checks/` 里补上对应的实跑脚本。是否写、写哪几条，等专业组基线评测的失败记录出来再定——没有失败的地方不必加内容。

## 波动输入和粘性边界

- 接地阻尼器 `a.engineeringFeatures.SpringDashpotToGround(..., dof=1, dashpotBehavior=ON, dashpotCoefficient=c)` 只有 Abaqus/Standard 有。Explicit 要用 `TwoPointSpringDashpot(regionPairs=((节点集, 固定参考点集),), axis=NODAL_LINE, ...)` 连到固定参考点。
- 系数是每个节点的值：阻尼为 ρ·Vs·从属面积，等效节点力为 2·ρ·Vs·从属面积·v(t)，端节点从属面积减半。
- 一维剪切柱的侧向条件有两种等价做法：全部节点约束竖向，或同高程左右节点两个自由度都用 `m.Equation` 绑定。只约束底部竖向时柱会弯曲，结果全错。
- Explicit 场输出用 `numIntervals` 时要加 `timeMarks=ON`，否则帧时刻会偏离整时刻最多一个稳定步长。
- 精度参考（4 Hz 子波，Vs = 200 m/s）：h = 1 m 配隐式 1 ms，峰值 +0.13%、残余 0.15%；显式残余 1.0%；h = 5 m 时 +0.27%、3.7%；h = 10 m 时 −25%。

## 固结

- 接口：`m.SoilsStep`、`mat.Permeability(specificWeight=γw, table=((k, e0),))`、`m.VoidsRatio(name, region, voidsRatio1=...)`、`m.PorePressureBC`。初始孔隙比必须给；不透水边界不用定义。
- 突加荷载要放在单独的只有一个增量的短步里，时长约 γw·h²/(6·M·k)（h 为单元尺寸），再接 UTOL 自动步长的固结步。在 UTOL 控制的步里突加荷载会不断折减，直到报 "TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM"；加载步太短会让孔压振荡。
- 只支持四边形的单元类型（CPE4P 等）要先 `setMeshControls(elemShape=QUAD, technique=STRUCTURED)` 再 `setElementType`，否则报 "select a type that supports triangular shapes"。
- CPE8RP、CPE4P、C3D8P 三者结果相差不到 0.2%。

## 不排水承载力

- 平面应变下 Tresca 强度 cu 对应 Mises 屈服应力 √3·cu，用 2cu 会高 18%。`MohrCoulombPlasticity` 取 φ = 0、c = cu 得到同样结果。
- 单元对比（0.125 m 网格，光滑基础，相对 Prandtl）：CPE8RH +0.4%，CPE6M +0.8%，CPE8R +2.5%，CPE8 +4.4%，CPE4R −4.9%，CPE4 和 CPE4H −6.3%，CPE3 +51%。0.5 m 的 CPE8R 是 +8.8%。
- 运动耦合面上的节点，在未耦合的自由度上再加边界条件会被忽略（.dat 警告 "DEGREE OF FREEDOM 1 IS NOT ACTIVE ON NODE … THIS BOUNDARY CONDITION IS IGNORED"）。粗糙基础要把 u1 也放进耦合，或直接在基础节点上给位移。
- 位移控制压入 B/10 足够到平台（E/cu = 300）。

## 混凝土损伤塑性

- 接口：`mat.ConcreteDamagedPlasticity(table=((36.0, 0.1, 1.16, 0.6667, 0.0),))`，其下的 `ConcreteCompressionHardening`、`ConcreteTensionStiffening(type=STRAIN)`、`ConcreteTensionDamage(type=STRAIN)`；输出量 DAMAGET、PEEQT。
- Abaqus 把开裂应变换算成塑性应变，ε_pl = ε_ck − d/(1−d)·σ/E0，应力和损伤都在塑性应变上线性插值；与在开裂应变上线性插值的手算相差约 1%。换算后的塑性应变必须递增。
- 卸载模量等于 (1−d)·E0。
- `type=DISPLACEMENT` 时表按位移解释，0.1 m 的单元上软化慢 10 倍。
- 粘性参数取 0.2（步长 1）时峰值高 68%。
- 同材料单元串联在位移控制下不局部化，5 单元串联与单单元结果相同。

## 更早一批（难题组 x01–x04）里可用的三条

- `abaqus cae noGUI` 里 `print` 不进标准输出，要用 `sys.__stdout__.write`。
- 全积分 CPS4 在长宽比 2 时弯曲偏刚 9.7%；CPS4R 每层一个单元偏柔 36%。
- 初始温度要在 Initial 步定义，再用 `setValuesInStep` 改到目标值；否则温差从 0 算起。
