# -*- coding: utf-8 -*-
# Reads chk_tie.odb / chk_tie_gap.odb (see chk_tie.py): the tied stack shortens by p (h1 + h2) / E.
from odbAccess import openOdb
import sys

problems = []
want = -1000.0 * 0.3 / 2.0e8
for job in ('chk_tie', 'chk_tie_gap'):
    odb = openOdb(job + '.odb', readOnly=True)
    fr = odb.steps['Load'].frames[-1]
    top = fr.fieldOutputs['U'].getSubset(region=odb.rootAssembly.nodeSets['PADTOPNODES'])
    u2 = [float(v.data[1]) for v in top.values]
    mean = sum(u2) / len(u2)
    ok = abs(mean - want) <= 0.01 * abs(want)
    print('%s %s: mean top displacement %.6g, expected %.6g (%+.2f%%)' % ('ok  ' if ok else 'FAIL', job, mean, want,
                                                                         100.0 * (mean / want - 1.0)))
    if not ok:
        problems.append(job)
    odb.close()
print('TIE %s' % ('OK' if not problems else 'FAILED: ' + ', '.join(problems)))
sys.exit(1 if problems else 0)
