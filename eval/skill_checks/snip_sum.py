# -*- coding: utf-8 -*-
from abaqus import *
from abaqusConstants import *
from caeModules import *
import math
out = open('snip_sum.txt', 'w')
out.write('sum is %r\n' % (sum,))
for label, fn in (('sum(list)', lambda: sum([1.0, 2.0, 3.0])), ('sum(generator)', lambda: sum(x for x in [1.0, 2.0, 3.0])),
                  ('sum(tuple)', lambda: sum((1.0, 2.0, 3.0))), ('math.fsum(generator)', lambda: math.fsum(x for x in [1.0, 2.0, 3.0])),
                  ('max(generator)', lambda: max(x for x in [1.0, 2.0, 3.0])), ('min(list)', lambda: min([1.0, 2.0])),
                  ('abs(-1)', lambda: abs(-1.0)), ('round(1.26,1)', lambda: round(1.26, 1)),
                  ('__builtins__ sum(generator)', lambda: __import__('__builtin__').sum(x for x in [1.0, 2.0, 3.0]))):
    try:
        out.write('%s -> %r\n' % (label, fn()))
    except Exception as e:
        out.write('%s -> %s: %s\n' % (label, type(e).__name__, e))
out.close()
