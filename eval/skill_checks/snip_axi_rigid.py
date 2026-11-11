# -*- coding: utf-8 -*-
# which sketches make a valid AXISYMMETRIC analytical rigid surface? try several variants and report
from abaqus import *
from abaqusConstants import *
from caeModules import *

out = open('snip_axi_rigid.txt', 'w')
Mdb()
m = mdb.models['Model-1']


def attempt(label, build, sheet=0.05):
    s = m.ConstrainedSketch(name='s_' + label, sheetSize=sheet)
    try:
        build(s)
        p = m.Part(name='P_' + label, dimensionality=AXISYMMETRIC, type=ANALYTIC_RIGID_SURFACE)
        p.AnalyticRigidSurf2DPlanar(sketch=s)
        rp = p.ReferencePoint(point=(0.0, 0.0, 0.0))
        out.write('%-34s OK  edges=%d\n' % (label, len(p.edges)))
    except Exception as e:
        out.write('%-34s FAILED: %s\n' % (label, str(e).split('\n')[0][:90]))


def axis(s):
    s.ConstructionLine(point1=(0.0, -1.0), point2=(0.0, 1.0))


attempt('line_from_axis_with_cl', lambda s: (axis(s), s.Line(point1=(0.0, 0.0), point2=(0.015, 0.0))))
attempt('line_from_axis_no_cl', lambda s: s.Line(point1=(0.0, 0.0), point2=(0.015, 0.0)))
attempt('line_off_axis_with_cl', lambda s: (axis(s), s.Line(point1=(0.001, 0.0), point2=(0.015, 0.0))))
attempt('line_reversed_with_cl', lambda s: (axis(s), s.Line(point1=(0.015, 0.0), point2=(0.0, 0.0))))
attempt('line_crossing_axis', lambda s: (axis(s), s.Line(point1=(-0.015, 0.0), point2=(0.015, 0.0))))
attempt('line_big_sheet', lambda s: (axis(s), s.Line(point1=(0.0, 0.0), point2=(0.015, 0.0))), sheet=10.0)
attempt('line_mm_scale', lambda s: (axis(s), s.Line(point1=(0.0, 0.0), point2=(15.0, 0.0))), sheet=100.0)
attempt('rectangle_closed', lambda s: (axis(s), s.rectangle(point1=(0.0, -0.001), point2=(0.015, 0.0))))
attempt('quarter_arc_with_cl', lambda s: (axis(s), s.ArcByCenterEnds(center=(0.0, 0.01), point1=(0.01, 0.01), point2=(0.0, 0.0), direction=CLOCKWISE)))
attempt('half_arc_with_cl', lambda s: (axis(s), s.ArcByCenterEnds(center=(0.0, 0.01), point1=(0.0, 0.02), point2=(0.0, 0.0), direction=CLOCKWISE)))
out.close()
