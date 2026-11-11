# -*- coding: utf-8 -*-
# 模板：三维实体静力分析（Abaqus/CAE 2021，自带 Python 2.7）。运行：abq script <本文件>
# 改写时保持这个顺序：参数 → 部件 → 材料/截面 → 分区 → 网格 → 装配与集合 → 分析步/输出 → 边界/载荷 → 导出 INP
from abaqus import *
from abaqusConstants import *
from caeModules import *

# ---------------- 参数（本例单位制 m–t–s：力 kN，应力 kPa，密度 t/m3） ----------------
JOB = 'beam_demo'
L, B, H = 2.0, 0.3, 0.4            # 长(z)、宽(x)、高(y)  m
E, NU, RHO = 3.0e7, 0.2, 2.5       # kPa, -, t/m3
P_TOP = 50.0                       # 顶面压强 kPa
SEED = 0.05                        # 网格尺寸 m（高度方向 8 层）
TOL = 1e-6                         # 选几何用的容差

Mdb()                              # 从空数据库开始，避免上一次运行的残留
m = mdb.models['Model-1']

# 1) 部件：草图 → 拉伸（草图在 x-y 平面，沿 +z 拉伸）
s = m.ConstrainedSketch(name='profile', sheetSize=10.0)
s.rectangle(point1=(0.0, 0.0), point2=(B, H))
p = m.Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=L)

# 2) 材料与截面（截面指派给部件上的集合）
mat = m.Material(name='Concrete')
mat.Elastic(table=((E, NU),))
mat.Density(table=((RHO,),))       # 有重力、动力、模态时必须有密度
m.HomogeneousSolidSection(name='Sec', material='Concrete', thickness=None)
p.SectionAssignment(region=p.Set(name='All', cells=p.cells), sectionName='Sec')

# 3) 分区：让网格规整，并在需要加载/取结果的位置造出几何边界（这里在跨中切一刀）
p.PartitionCellByPlaneThreePoints(cells=p.cells, point1=(0.0, 0.0, L / 2), point2=(B, 0.0, L / 2),
                                  point3=(0.0, H, L / 2))

# 4) 网格：种子 → 网格控制 → 单元类型 → 生成（分区后要重新取 p.cells）
p.seedPart(size=SEED, deviationFactor=0.1, minSizeFactor=0.1)
p.setMeshControls(regions=p.cells, elemShape=HEX, technique=STRUCTURED)
p.setElementType(regions=(p.cells,), elemTypes=(mesh.ElemType(elemCode=C3D8I, elemLibrary=STANDARD),))
p.generateMesh()

# 5) 装配；用包围盒从实例上选面/边，建装配级的集合与表面
a = m.rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Beam-1', part=p, dependent=ON)
fix = a.Set(name='FixEnd', faces=inst.faces.getByBoundingBox(zMax=TOL))
top = a.Surface(name='Top', side1Faces=inst.faces.getByBoundingBox(yMin=H - TOL))
a.Set(name='MidBottom', edges=inst.edges.getByBoundingBox(zMin=L / 2 - TOL, zMax=L / 2 + TOL, yMax=TOL))

# 6) 分析步与输出请求
m.StaticStep(name='Load', previous='Initial', nlgeom=OFF, initialInc=1.0, maxInc=1.0)
m.fieldOutputRequests['F-Output-1'].setValues(variables=('S', 'E', 'U', 'RF', 'CF'))

# 7) 边界条件与载荷（region 用上面建好的集合/表面）
m.EncastreBC(name='Fix', createStepName='Initial', region=fix)
m.Pressure(name='TopPressure', createStepName='Load', region=top, magnitude=P_TOP)
m.Gravity(name='Gravity', createStepName='Load', comp2=-9.8)

# 8) 只导出 INP，不在脚本里提交；之后用 abq check / abq datacheck / abq run
mdb.Job(name=JOB, model='Model-1').writeInput(consistencyChecking=OFF)
print('INP written: %s.inp' % JOB)
