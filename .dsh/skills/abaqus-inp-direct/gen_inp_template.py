# -*- coding: utf-8 -*-
"""模板：不经过 CAE，用 Python 直接写出 Abaqus 输入文件（INP）。运行：py gen_inp_template.py  （系统 Python 3 即可）

适用：几何规则、想精确控制每个节点位置的模型（结构化网格、向某处渐密的网格、尺寸很小的模型）。
本例：平面应变土柱侧限压缩。宽 1 m、高 2 m，两侧和底面法向约束，顶面压强 100 kPa（m–t–s 单位制）。
解析解：竖向应力 -100 kPa，顶面沉降 p·H/E_oed = 0.004952 m，水平应力 -42.86 kPa。
之后：abq check block.inp → abq run block
"""
JOB = 'block'
W, H = 1.0, 2.0                 # m
NX, NY = 8, 16                  # 单元数
E, NU, RHO = 3.0e4, 0.3, 1.8    # kPa, -, t/m3
P_TOP = 100.0                   # kPa
BIAS = 1.0                      # >1 时单元沿高度向顶面渐密（相邻单元尺寸之比）


def graded(n, total, ratio):
    """n 个单元的节点坐标 0…total；ratio 为相邻单元尺寸之比（1 = 均匀，>1 时越靠后越小）"""
    if abs(ratio - 1.0) < 1e-12:
        return [total * i / n for i in range(n + 1)]
    first = total * (1.0 - 1.0 / ratio) / (1.0 - (1.0 / ratio) ** n)
    xs, x, h = [0.0], 0.0, first
    for _ in range(n):
        x += h
        xs.append(x)
        h /= ratio
    xs[-1] = total
    return xs


xs = graded(NX, W, 1.0)
ys = graded(NY, H, BIAS)


def nid(i, j):                  # 节点号：i 沿 x (0…NX)，j 沿 y (0…NY)，从 1 开始
    return j * (NX + 1) + i + 1


def eid(i, j):                  # 单元号：i 沿 x (0…NX-1)，j 沿 y (0…NY-1)
    return j * NX + i + 1


L = ['*Heading', 'Confined compression of a soil column, plane strain, units m-t-s (kN, kPa)',
     '*Node']
for j in range(NY + 1):
    for i in range(NX + 1):
        L.append('%d, %.9g, %.9g' % (nid(i, j), xs[i], ys[j]))

# 四边形单元的节点必须按逆时针顺序：左下、右下、右上、左上。
# 这样单元的面编号是 S1=下边、S2=右边、S3=上边、S4=左边。
L.append('*Element, type=CPE4, elset=SOIL')
for j in range(NY):
    for i in range(NX):
        L.append('%d, %d, %d, %d, %d' % (eid(i, j), nid(i, j), nid(i + 1, j), nid(i + 1, j + 1), nid(i, j + 1)))

# 节点集：generate 的三项是 起始号, 终止号, 步长
L += ['*Nset, nset=BOTTOM, generate', '%d, %d, 1' % (nid(0, 0), nid(NX, 0)),
      '*Nset, nset=TOP, generate', '%d, %d, 1' % (nid(0, NY), nid(NX, NY)),
      '*Nset, nset=LEFT, generate', '%d, %d, %d' % (nid(0, 0), nid(0, NY), NX + 1),
      '*Nset, nset=RIGHT, generate', '%d, %d, %d' % (nid(NX, 0), nid(NX, NY), NX + 1),
      '*Elset, elset=TOPROW, generate', '%d, %d, 1' % (eid(0, NY - 1), eid(NX - 1, NY - 1)),
      '*Surface, type=ELEMENT, name=TOPSURF', 'TOPROW, S3',          # 顶排单元的上边
      '*Solid Section, elset=SOIL, material=SOILMAT', '1.0,',        # 平面单元的厚度
      '*Material, name=SOILMAT', '*Elastic', '%.9g, %.9g' % (E, NU), '*Density', '%.9g,' % RHO,
      '*Boundary', 'BOTTOM, 2, 2', 'LEFT, 1, 1', 'RIGHT, 1, 1',      # 节点集, 起始自由度, 终止自由度
      '*Step, name=Load, nlgeom=NO', '*Static', '1., 1., 1e-05, 1.',
      '*Dsload', 'TOPSURF, P, %.9g' % P_TOP,                         # 正的压强压向表面
      '*Output, field', '*Node Output', 'U, RF', '*Element Output', 'S, E',
      '*Output, history, variable=PRESELECT',
      '*End Step']

with open(JOB + '.inp', 'w') as f:
    f.write('\n'.join(L) + '\n')
print('written %s.inp: %d nodes, %d elements' % (JOB, (NX + 1) * (NY + 1), NX * NY))
