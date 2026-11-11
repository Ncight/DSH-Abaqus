# -*- coding: utf-8 -*-
from abaqus import *
from abaqusConstants import *
from caeModules import *
out = open('snip_findat.txt', 'w')
Mdb()
m = mdb.models['Model-1']
s = m.ConstrainedSketch(name='a', sheetSize=2.0)
s.rectangle(point1=(0.0, 0.0), point2=(1.0, 1.0))
p = m.Part(name='P', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=1.0)
for label, fn in (('findAt((x,y,z))', lambda: p.faces.findAt((0.5, 0.5, 1.0))),
                  ('findAt(((x,y,z),))', lambda: p.faces.findAt(((0.5, 0.5, 1.0),))),
                  ('findAt(((p1),),((p2),))', lambda: p.faces.findAt(((0.5, 0.5, 1.0),), ((0.5, 0.5, 0.0),))),
                  ('findAt(x,y,z)', lambda: p.faces.findAt(0.5, 0.5, 1.0)),
                  ('findAt miss', lambda: p.faces.findAt((5.0, 5.0, 5.0))),
                  ('empty bbox [0]', lambda: p.faces.getByBoundingBox(xMin=5.0)[0])):
    try:
        r = fn()
        out.write('%-26s -> %s len=%s\n' % (label, type(r).__name__, len(r) if hasattr(r, '__len__') else '-'))
    except Exception as e:
        out.write('%-26s -> %s: %s\n' % (label, type(e).__name__, str(e)[:70]))
out.close()
