# -*- coding: utf-8 -*-
"""Build guidance candidate v2 from the v1 copy in ./v2 (made with optimize.make_candidate('v2')).

What changes relative to v1, and why (evidence: eval tag v1_live):
  1. AGENTS.md: leaner, tiered workflow (v1 loaded 4-6 skills and ran every step even on trivial tasks: slower and
     2-4x the tokens of the bare harness on tasks both solve), plus an anti-thrashing rule and the small-geometry rule.
  2. abaqus-contact: verified rules for AXISYMMETRIC analytical rigid surfaces (the Hertz run burned its budget on
     "解析刚性表面特征失败") and the advice to model sub-millimetre contact zones in mm.
  3. abaqus-cae-scripting: closed-profile rule for BaseShell, small-geometry section.
  4. abaqus-materials-units: the mm system for small parts.
  5. hints.json: hints for the feature-creation / collapsed-element errors seen in that run.
    py build_v2.py
"""
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
V2 = os.path.join(HERE, 'v2')


def edit(rel, pairs):
    p = os.path.join(V2, rel)
    with open(p, encoding='utf-8') as f:
        t = f.read()
    for old, new in pairs:
        if new in t:
            continue                      # already applied
        assert old in t, 'anchor not found in %s: %s' % (rel, old[:40])
        t = t.replace(old, new, 1)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(t)


shutil.copy2(os.path.join(HERE, 'v2_AGENTS.md'), os.path.join(V2, 'AGENTS.md'))

# ---------------------------------------------------------------- contact
edit(os.path.join('.dsh', 'skills', 'abaqus-contact', 'SKILL.md'), [
    ("- 刚性平面：草图画一条直线即可；三维刚性面用离散刚体或 `AnalyticRigidSurfExtrude`/`AnalyticRigidSurfRevolve`（参数用 `abq api` 查）。",
     "- 刚性平面：草图画一条直线即可；三维刚性面用离散刚体或 `AnalyticRigidSurfExtrude`/`AnalyticRigidSurfRevolve`（参数用 `abq api` 查）。\n"
     "- **轴对称的解析刚性面**（已实测）：部件用 `dimensionality=AXISYMMETRIC, type=ANALYTIC_RIGID_SURFACE`，仍然调用 `AnalyticRigidSurf2DPlanar(sketch=s)`。四条规则，违反任何一条都报\"解析刚性表面特征失败\"：\n"
     "  1. 草图里先画对称轴构造线：`s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))`；\n"
     "  2. 轮廓是一条**不闭合**的线/弧链（不能画成矩形等闭合图形）；\n"
     "  3. 轮廓全部在 r ≥ 0 一侧，可以从轴线出发，不能跨过轴线；\n"
     "  4. 单段圆弧小于 180°。\n"
     "  轴对称刚性平面就是一条从轴线出发的水平线：`s.Line(point1=(0.0, 0.0), point2=(R_plane, 0.0))`，再 `rig.ReferencePoint(point=(0.0, 0.0, 0.0))`。"),
    ("4. **网格**：接触区内至少 8～10 个单元（先按解析解估接触半宽），接触面用线性单元，从面不比主面粗。",
     "4. **网格**：接触区内至少 8～10 个单元（先按解析解估接触半宽），接触面用线性单元，从面不比主面粗。接触半宽常常只有零点几毫米：按\"接触半宽 ÷ 10\"算出单元尺寸，若小于约 0.0001 个模型单位（用米建模时很常见），整个模型改用 N–mm–MPa 建，否则会出现特征创建失败和面积为零的单元；只在接触区附近分区加密，远处放粗。"),
])

# ---------------------------------------------------------------- scripting
edit(os.path.join('.dsh', 'skills', 'abaqus-cae-scripting', 'SKILL.md'), [
    ("轴对称：`dimensionality=AXISYMMETRIC`，草图里 x 是半径 r（必须 ≥ 0）、y 是轴向，",
     "`BaseShell` 的草图必须是一个**闭合**轮廓。例如轴对称的球：半圆弧加一条沿轴线的直线把它封闭（`s.ArcByCenterEnds(...)` + `s.Line(point1=(0.0, 0.0), point2=(0.0, 2*R))`）；轮廓不闭合会报\"创建壳的…特征失败\"。\n\n"
     "轴对称：`dimensionality=AXISYMMETRIC`，草图里 x 是半径 r（必须 ≥ 0）、y 是轴向，"),
    ("## 网格\n",
     "## 尺寸很小的模型：换单位制\n\n"
     "CAE 的几何精度约为 0.000001 个模型单位。需要的最小单元或最小几何特征小于约 0.0001 个模型单位时（例如用米建模，接触区单元只有十几微米），会出现草图/分区特征创建失败，自由网格里出现面积为零的塌缩单元（数据检查报 `area … zero, small, or negative`）。不要在原单位制里反复调网格：把整个模型改成 mm 制（长度 mm、力 N、应力 MPa、密度 t/mm³，钢 E=206000、ρ=0.00000000785）重建，结果再换算回任务书要求的单位。判断方法：先估出关心区域的尺寸（接触半宽、圆角半径、孔径），除以 10 得到单元尺寸，再看它相对模型单位是不是小于 0.0001。\n\n"
     "## 网格\n"),
    ("| 特征创建失败 / findAt 找不到 | 坐标没落在几何上，改用包围盒 |",
     "| findAt 找不到 | 坐标没落在几何上，改用包围盒 |\n"
     "| 特征创建失败（草图、拉伸、分区） | 草图不闭合或自相交；分区线没有把面完全切开；尺寸相对几何精度太小（见\"尺寸很小的模型\"） |\n"
     "| 解析刚性表面特征失败 | 见 skill `abaqus-contact` 里的四条规则（轴对称要对称轴构造线、轮廓不闭合、不跨轴、单段弧小于 180°） |"),
])

# ---------------------------------------------------------------- materials / units
edit(os.path.join('.dsh', 'skills', 'abaqus-materials-units', 'SKILL.md'), [
    ("- 换算最容易错的是密度和重力加速度：",
     "- 零件尺寸是毫米级、或需要亚毫米的单元时，用 mm–t–s 制建模（CAE 的几何精度约 0.000001 个模型单位，用米建模会失败）；结果按 1 MPa = 1000000 Pa、1 mm = 0.001 m 换算回去。\n"
     "- 换算最容易错的是密度和重力加速度："),
])

# ---------------------------------------------------------------- hints
hp = os.path.join(V2, 'tools', 'abq', 'hints.json')
with open(hp, encoding='utf-8') as f:
    hints = json.load(f)
new = [
    {"pattern": "解析刚性表面.{0,12}特征失败|Analytic(al)? rigid surface.{0,30}fail",
     "hint": "解析刚性面创建失败，逐条核对：轴对称部件的草图里要先画对称轴构造线；轮廓必须是不闭合的线/弧链；轮廓不能跨过对称轴；单段圆弧要小于 180°（半圆拆成两段）。详见 skill abaqus-contact。"},
    {"pattern": "创建壳的.{0,8}特征失败|Shell.{0,20}feature.{0,10}fail",
     "hint": "BaseShell 需要一个闭合的草图轮廓：检查各线段首尾是否相接成封闭图形（例如半圆弧要加一条沿轴线的直线封口），有没有重叠或自相交的线。"},
    {"pattern": "特征创建失败|面必须包含一些实体|Feature creation failed",
     "hint": "特征创建失败的常见原因：草图不闭合或自相交；分区用的线没有把面/体完全切开；选取到的几何为空；尺寸相对 CAE 几何精度(约 0.000001 个模型单位)太小——最小特征小于约 0.0001 个模型单位时改用 mm 制建模。同一种写法试两三次不行就换做法，不要反复微调。"},
    {"pattern": "area of \\d+ elements? is zero, small, or negative|ErrElemAreaSmallNegZero",
     "hint": "网格里有面积为零的塌缩单元。尺寸很小的模型（单元尺寸接近 CAE 几何精度）用自由网格时常见：把模型改成 mm 制重建，或对该区域分区后用结构化网格。"},
]
have = set(h['pattern'] for h in hints)
for h in new:
    if h['pattern'] not in have:
        hints.insert(4, h)
for h in hints:
    re.compile(h['pattern'])
with open(hp, 'w', encoding='utf-8') as f:
    json.dump(hints, f, ensure_ascii=False, indent=1)
print('v2 built: %d hints' % len(hints))
