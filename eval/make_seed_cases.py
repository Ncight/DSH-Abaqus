# -*- coding: utf-8 -*-
"""Create the seed entries of the verified case library from scripts that were run and checked by hand.
    py make_seed_cases.py
Each case = cases/<id>/case.json + the script(s). Seeds are deliberately different problems from the benchmark tasks.
"""
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNIPS = os.path.join(ROOT, '.dsh-eval', 'runs', '_smoke', 'snips')
SKILLS = os.path.join(ROOT, '.dsh', 'skills')
CASES = os.path.join(ROOT, 'cases')

SEEDS = [
    {'id': 'c01_solid_cantilever_pressure_gravity', 'src': [(os.path.join(SKILLS, 'abaqus-cae-scripting', 'template_solid_static.py'), 'model.py'),
                                                             (os.path.join(SKILLS, 'abaqus-postprocess', 'read_odb_example.py'), 'post.py')],
     'title': '三维实体悬臂梁：顶面压强 + 自重（静力）',
     'tags': ['静力', '三维实体', 'C3D8I', '重力', '压强', '分区', '结构化网格', 'm-t-s'],
     'keywords': ['cantilever', 'solid', 'static', 'gravity', 'pressure', 'partition', 'beam'],
     'problem': '2 m × 0.3 m × 0.4 m 混凝土悬臂梁，一端固定，顶面 50 kPa 压强加自重，求端部挠度和支座反力。',
     'approach': ['草图拉伸建部件，跨中分区，C3D8I 结构化六面体网格（高度方向 8 层）',
                  '用 getByBoundingBox 在实例上选面建装配级集合/表面', '脚本只导出 INP，abq run 求解，post.py 用 abaqus python 读 ODB'],
     'lessons': ['有重力必须定义密度；m–t–s 制下混凝土密度是 2.5 不是 2500', 'C3D8I 在规则网格下弯曲很准，不需要很多层',
                 '短粗梁(L/H=5)挠度比欧拉梁公式大约 4%，是剪切变形，不是错误'],
     'verified': {'tip_deflection_m': 0.000772, 'theory_euler_plus_shear_m': 0.000776, 'reaction_kN': 35.88, 'theory_reaction_kN': 35.88}},
    {'id': 'c02_punch_on_block_frictional_contact', 'src': [(os.path.join(SNIPS, 'snip_2d_contact.py'), 'model.py')],
     'title': '平面应变：刚度较大的压块压在软基座上（带摩擦的面-面接触，两个分析步）',
     'tags': ['接触', '摩擦', '平面应变', 'CPE4R', '面-面接触', '位移控制', '局部加密', 'PartitionFaceBySketch'],
     'keywords': ['contact', 'friction', 'punch', 'plane strain', 'surface-to-surface', 'two steps', 'refine'],
     'problem': '0.06 m 宽的钢压块压在 0.20 m × 0.10 m 的软基座上，摩擦系数 0.3，压块顶面下压 0.5 mm。',
     'approach': ['基座用 PartitionFaceBySketch 切出压块下方的加密区，seedEdgeBySize 局部加密', '主面取较硬的压块底面，从面取较软且网格更细的基座顶面',
                  '第 1 步用 0.01 mm 的小位移建立接触，第 2 步用 setValuesInStep 加到 0.5 mm', '输出 CSTRESS、CDISP'],
     'lessons': ['先小位移建立接触再加载，12 个增量步无折减收敛', '两端都有约束时反力净合计为 0，传递的力看"正值合计"',
                 '压块角点下方是应力奇异点，峰值随网格加密而增大，不能当作结果'],
     'verified': {'increments': 12, 'cutbacks': 0, 'transmitted_force_kN_per_m': 579.97}},
    {'id': 'c03_rigid_cylinder_on_block_plane_strain', 'src': [(os.path.join(SNIPS, 'snip_rigid_contact.py'), 'model.py')],
     'title': '平面应变：解析刚性圆柱压头（带参考点）压弹性块，与 Hertz 线接触解对比',
     'tags': ['接触', '解析刚性面', '参考点', '平面应变', 'Hertz', '线接触', 'CPE4R', '位移控制'],
     'keywords': ['contact', 'analytical rigid', 'reference point', 'hertz', 'line contact', 'cylinder', 'indenter', 'CPRESS'],
     'problem': '半径 0.05 m 的刚性圆柱压入 0.20 m × 0.10 m 的弹性块（E=1000000 kPa, ν=0.3）2 mm，无摩擦，求接触压力峰值。',
     'approach': ['压头用 ANALYTIC_RIGID_SURFACE 部件，半圆画成两段四分之一圆弧', '参考点建在压头部件上，位移和转角全部给定在参考点',
                  '刚性面取朝向弹性块的一侧（本例草图方向下是 side2Edges）', '弹性块顶面作从面，网格 4 mm（接触半宽内约 4 个单元，再密更准）'],
     'lessons': ['解析刚性面的单段圆弧必须小于 180°，否则特征创建失败', '刚性面朝向选反时不报错，只有警告 FACING IN THE WRONG DIRECTION，结果是反力和 CPRESS 全为 0',
                 '位移控制下力是结果量：从参考点反力读出每单位厚度的力，再代入 Hertz 公式核对', '线接触 Hertz：a=√(4PR/(πE*))，p0=2P/(πa)，E*=E/(1−ν²)'],
     'verified': {'force_kN_per_m': 1046.65, 'cpress_max_kPa': 84970.6, 'hertz_p0_kPa': 85567.0, 'error_percent': -0.7}},
    {'id': 'c04_axisym_ring_external_pressure', 'src': [(os.path.join(SNIPS, 'snip_axisym.py'), 'model.py')],
     'title': '轴对称：厚壁圆环受外压（轴向不伸缩），偏置网格',
     'tags': ['轴对称', 'CAX4R', '压强', '偏置种子', '结构化网格', 'Lame'],
     'keywords': ['axisymmetric', 'ring', 'pressure', 'bias', 'seedEdgeByBias', 'lame', 'hoop stress'],
     'problem': '内半径 0.5 m、外半径 0.8 m、高 0.2 m 的钢环，外表面受 1000 kPa 压强，两端面轴向位移为 0。',
     'approach': ['AXISYMMETRIC 部件，草图 x=r、y=轴向，先画对称轴构造线', '径向边用 seedEdgeByBias 向内壁加密，轴向边 seedEdgeByNumber',
                  '两端面 u2=0 模拟无轴向应变；外表面 Pressure'],
     'lessons': ['轴对称模型里 S11 径向、S22 轴向、S33 环向', '线性减缩单元的应力在单元中心，最靠近内壁的积分点比内壁理论值略小，要更准就加密或用 CAX8R',
                 '偏置方向由边的方向决定，生成后要检查小单元是否在该密的一端'],
     'verified': {'hoop_at_innermost_ip_kPa': -3256.3, 'lame_hoop_at_bore_kPa': -3282.1, 'radial_at_outer_kPa': -993.8}},
    {'id': 'c05_portal_frame_and_ss_plate', 'src': [(os.path.join(SNIPS, 'snip_beam_shell.py'), 'model.py')],
     'title': '梁单元门式框架（均布线载荷）+ 壳单元简支板（均布压强）',
     'tags': ['梁单元', 'B31', '壳单元', 'S4R', '截面方向', '线载荷', '简支板', '框架'],
     'keywords': ['beam', 'frame', 'shell', 'plate', 'profile', 'orientation', 'LineLoad', 'simply supported'],
     'problem': '4 m 跨、3 m 高的矩形截面钢框架，横梁受 20 kN/m 均布载荷，柱脚固定；另有 2 m × 1 m × 20 mm 的四边简支钢板受 5 kPa 压强。',
     'approach': ['BaseWire 建线框，RectangularProfile + BeamSection，assignBeamSectionOrientation 指定 n1', 'LineLoad 加均布线载荷；柱脚顶点集合 EncastreBC',
                  '板用 BaseShell + HomogeneousShellSection；四边只约束 u3，两个角点补面内约束', '场输出请求 SF（同时得到 SM）'],
     'lessons': ['请求输出 SM 会报"无效变量"，只请求 SF 即可', '同一模型里互不相连的部件会有 UNCONNECTED REGIONS 警告，这里是有意为之',
                 '梁截面 a 沿局部 1 轴、b 沿局部 2 轴；框架在 x-y 平面、n1=(0,0,-1) 时面内弯曲的截面高度是 b'],
     'verified': {'completed': True, 'increments': 1}},
    {'id': 'c06_cantilever_beam_elements_orientation', 'src': [(os.path.join(SNIPS, 'snip_beam_orient.py'), 'model.py')],
     'title': '梁单元悬臂梁端部集中力：验证截面尺寸与弯曲方向的对应关系',
     'tags': ['梁单元', 'B33', '悬臂梁', '截面方向', '集中力', '反力矩'],
     'keywords': ['beam', 'cantilever', 'B33', 'orientation', 'RectangularProfile', 'RM', 'tip load'],
     'problem': '4 m 长悬臂梁，矩形截面 a=0.2 m、b=0.4 m，端部 10 kN 向下，E=206000000 kPa。',
     'approach': ['线框沿 x 轴，n1=(0,0,-1)，B33 欧拉梁单元 20 个', '根部顶点 EncastreBC，端部顶点 ConcentratedForce'],
     'lessons': ['结果 0.000970874 m 与 PL³/(3EI)、I=a·b³/12 完全一致：此时受弯高度是 b', 'B33 不含剪切变形，适合对照欧拉梁公式；B31 会多出剪切挠度',
                 '请求 RF 即可同时得到支座反力矩 RM（这里 RM3=40 kN·m = P·L）'],
     'verified': {'tip_deflection_m': 0.000970874, 'theory_m': 0.000970874, 'root_moment_kNm': 40.0}},
    {'id': 'c07_block_modes_and_explicit_push', 'src': [(os.path.join(SNIPS, 'snip_dynamics.py'), 'model.py')],
     'title': '同一块体的模态分析与显式准静态压缩（弹塑性，平滑幅值）',
     'tags': ['模态', '频率', '显式', 'Explicit', '塑性', '平滑幅值', '能量输出', 'C3D8R', '沙漏控制'],
     'keywords': ['frequency', 'modal', 'explicit', 'plastic', 'smooth step', 'amplitude', 'energy', 'history output'],
     'problem': '0.1 m × 0.1 m × 0.5 m 钢块底面固定：(1) 求前 6 阶固有频率；(2) 用 Abaqus/Explicit 在 0.01 s 内把顶面平滑压下 10 mm。',
     'approach': ['一个脚本里建两个模型，各导出一个 INP', '模态：FrequencyStep(numEigen=6)，C3D8I', '显式：C3D8R + ENHANCED 沙漏控制，SmoothStepAmplitude，场输出 20 帧，能量历程 100 点，双精度'],
     'lessons': ['方形截面的弯曲模态成对出现（1、2 阶频率相同），属正常', '显式作业 2995 个增量步，稳定步长约 0.000002785 s，与"最小单元尺寸/波速"的估计一致',
                 '显式里 *Plastic 表与隐式相同：真应力—塑性应变，首行塑性应变为 0'],
     'verified': {'f1_Hz': 324.63, 'f3_Hz': 1415.1, 'explicit_increments': 2995}},
]


def main():
    os.makedirs(CASES, exist_ok=True)
    for c in SEEDS:
        d = os.path.join(CASES, c['id'])
        os.makedirs(d, exist_ok=True)
        files = []
        for src, name in c['src']:
            shutil.copy2(src, os.path.join(d, name))
            files.append(name)
        meta = {k: v for k, v in c.items() if k != 'src'}
        meta['files'] = files
        meta['source'] = 'seed'
        with open(os.path.join(d, 'case.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        print('case', c['id'], files)


if __name__ == '__main__':
    main()
