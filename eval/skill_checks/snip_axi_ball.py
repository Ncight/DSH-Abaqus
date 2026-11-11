# -*- coding: utf-8 -*-
# verify the v2 skill statements: closed profile for an axisymmetric ball (BaseShell) and an axisymmetric rigid plane
from abaqus import *
from abaqusConstants import *
from caeModules import *

out = open('snip_axi_ball.txt', 'w')
Mdb()
m = mdb.models['Model-1']
R = 10.0   # mm


def attempt(label, closed):
    s = m.ConstrainedSketch(name='s_' + label, sheetSize=100.0)
    s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))
    s.ArcByCenterEnds(center=(0.0, R), point1=(0.0, 0.0), point2=(0.0, 2.0 * R), direction=COUNTERCLOCKWISE)
    if closed:
        s.Line(point1=(0.0, 2.0 * R), point2=(0.0, 0.0))
    try:
        p = m.Part(name='P_' + label, dimensionality=AXISYMMETRIC, type=DEFORMABLE_BODY)
        p.BaseShell(sketch=s)
        out.write('%-18s OK faces=%d edges=%d\n' % (label, len(p.faces), len(p.edges)))
    except Exception as e:
        out.write('%-18s FAILED: %s\n' % (label, str(e).split('\n')[0][:80]))


attempt('arc_plus_axis_line', True)
attempt('arc_only_open', False)
s = m.ConstrainedSketch(name='plane', sheetSize=100.0)
s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))
s.Line(point1=(0.0, 0.0), point2=(15.0, 0.0))
rig = m.Part(name='Plane', dimensionality=AXISYMMETRIC, type=ANALYTIC_RIGID_SURFACE)
rig.AnalyticRigidSurf2DPlanar(sketch=s)
rp = rig.ReferencePoint(point=(0.0, 0.0, 0.0))
out.write('rigid plane OK, reference point id %d\n' % rp.id)
out.close()
