---
name: abaqus-dynamics
description: 做模态（固有频率/振型）、屈曲特征值、隐式动力、Abaqus/Explicit 显式动力（冲击、波传播、准静态成形）时加载。给出各类分析步的已验证写法、显式稳定步长与计算量估算、平滑加载与输出设置、用能量判断结果是否可信的标准，以及动力分析里最常见的错误。
---

# 动力与特征值分析

## 共同前提

- 所有参与的材料都要有**密度**，且单位自洽（`abq units`）。密度差 1000 倍，频率差约 31.6 倍、波速差 31.6 倍——用 `abq check` 给出的 √(E/ρ) 核对。
- 频率输出单位是 Hz（周/秒）；时间单位是 s。

## 模态分析

```python
m.FrequencyStep(name='Modes', previous='Initial', numEigen=6)     # 要几阶写几阶，多要一两阶无妨
m.EncastreBC(name='Fix', createStepName='Initial', region=base)   # 边界条件建在 Initial 步
```

- `abq odb job.odb` 直接列出各阶频率；看某一阶振型：`abq render job.odb --var U --frame 阶次`。
- 出现接近 0 的频率 = 有未约束的刚体模态（自由模态分析除外）。
- 对称截面会出现成对的重频（两个方向的弯曲），属正常；报告"前 n 阶"时按求解器给出的顺序，并说明各阶是什么振型（哪个方向的弯曲、扭转、轴向）。
- 细长构件和欧拉梁公式对比时，实体单元结果略低 1%～3%（含剪切变形和转动惯量）是正常的；高出很多说明单元自锁或网格太粗。
- 单元与网格：`C3D8I`、`C3D20R`、`S4R`、`B31` 都可；`C3D8R` 层数少时沙漏会带来假的低频。每个半波至少 6 个线性单元。

## 线性屈曲

```python
m.BuckleStep(name='Buckle', previous='Initial', numEigen=3, vectors=10, maxIterations=300)
m.ConcentratedForce(name='Pref', createStepName='Buckle', region=top, cf2=-1.0)   # 参考载荷
```

临界载荷 = 特征值 × 参考载荷（参考载荷取 1 时特征值就是临界载荷）。只取**正的**最小特征值；负特征值表示反向加载才屈曲。边界条件决定计算长度，铰接端要放开转动。梁单元建柱时沿长度至少 10 个单元。

## 隐式动力

```python
m.ImplicitDynamicsStep(name='Dyn', previous='Initial', timePeriod=1.0, initialInc=0.001, maxInc=0.01, maxNumInc=100000)
```

时间步 ≤ 关心的最高频率周期的 1/10～1/20。适合时间较长、频率不高的响应；高速冲击和波传播用显式。

## 显式动力（Abaqus/Explicit）

```python
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8R, elemLibrary=EXPLICIT,
                                                              hourglassControl=ENHANCED),))
T = 0.01
m.ExplicitDynamicsStep(name='Push', previous='Initial', timePeriod=T, nlgeom=ON)
m.SmoothStepAmplitude(name='Ramp', timeSpan=STEP, data=((0.0, 0.0), (T, 1.0)))         # 准静态/渐加载用平滑幅值
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'U', 'V', 'PEEQ', 'RF'), numIntervals=20)
m.historyOutputRequests['H-Output-1'].setValues(variables=('ALLIE', 'ALLKE', 'ALLAE', 'ALLWK', 'ALLPD', 'ETOTAL'),
                                                 numIntervals=100)
m.DisplacementBC(name='Push', createStepName='Push', region=top, u3=-0.01, amplitude='Ramp')
m.VelocityBC(name='Hit', createStepName='Push', region=face, v1=1.0)                    # 不给幅值 = 一开始就是全值（突加）
mdb.Job(name=JOB, model='Model-1', explicitPrecision=DOUBLE_PLUS_PACK).writeInput(consistencyChecking=OFF)
```

要点：

- **稳定步长** Δt ≈ 最小单元尺寸 / 膨胀波速，波速 c = √((λ+2μ)/ρ)（ν=0 时就是 √(E/ρ)）。增量步数 ≈ 总时长 / Δt。动手前先估：步数超过几百万就要重新考虑网格或时长。一个特别小的单元会拖慢整个模型。
- **波传播/冲击**：按真实时间算，不要质量缩放；单元尺寸 ≤ 最短波长的 1/10，沿传播方向均匀；场输出帧数要够（`numIntervals` ≥ 50），才能看到波前到达时刻；应力波幅值 σ = ρ·c·v，可用来核对。波前附近有数值振荡属正常，取波前过后平台段的平均值。
- **突加还是渐加**：研究冲击/波传播用突加（不给幅值）；想得到准静态解用 `SmoothStepAmplitude` 渐加，并检查动能/内能 < 5%～10%。
- **准静态加速**：可以缩短加载时间或做质量缩放，但必须用能量比验证，且不改变关心的结果。
- **阻尼**：刚度比例阻尼 β 会把稳定步长压得很小；显式里尽量 β=0，要阻尼用质量比例 α 或材料自身耗能。
- **精度**：增量步数很多（> 30 万）时用双精度（上面的 `explicitPrecision=DOUBLE_PLUS_PACK`，或 `abq run job --double`）。
- **输出**：显式默认只存 20 帧左右，要时程就加大 `numIntervals`，或对指定节点集建历程输出 `m.HistoryOutputRequest(name='H2', createStepName='Push', variables=('U1', 'V1'), region=a.sets['Monitor'], numIntervals=500)`。
- 取某点的时程：`abq odb job.odb --probe x,y,z --series S,U,V`。

## 用能量判断可不可信（`abq verify` 会自动检查）

| 指标 | 要求 | 不满足说明 |
|---|---|---|
| ALLAE / ALLIE（伪应变能/内能） | < 5% | 沙漏严重：加密网格或加强沙漏控制 |
| ALLKE / ALLIE（准静态时） | < 5%～10% | 加载太快，结果含惯性效应 |
| ETOTAL（总能量） | 基本不变 | 漂移大说明不稳定或接触穿透 |
| ALLVD、ALLSD | 远小于 ALLIE | 阻尼/稳定在改变结果 |
