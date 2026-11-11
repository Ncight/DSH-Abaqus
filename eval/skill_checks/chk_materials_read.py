# -*- coding: utf-8 -*-
# Reads chk_cdp.odb and chk_mc.odb (see chk_materials.py) and compares with the hand calculation.
#   abq python chk_materials_read.py
from odbAccess import openOdb
import math
import sys

problems = []


def expect(name, got, want, tol):
    ok = abs(got - want) <= tol * abs(want)
    print('%s %-34s got %.6g, expected %.6g (%+.2f%%)' % ('ok  ' if ok else 'FAIL', name, got, want, 100.0 * (got / want - 1.0)))
    if not ok:
        problems.append(name)


def last(odb, key, comp=None):
    fr = odb.steps['Load'].frames[-1]
    v = fr.fieldOutputs[key].values[0]
    return float(v.data if comp is None else v.data[comp])


# CDP: softening branch between (1.5e6, 0.0002) and (0.3e6, 0.001); cracking strain = total strain - stress / E0
E0, eps = 3.0e10, 0.0006
slope = (0.3e6 - 1.5e6) / (0.001 - 0.0002)
sig = (1.5e6 + slope * (eps - 0.0002)) / (1.0 + slope / E0)
eck = eps - sig / E0
dt = 0.4 + (0.8 - 0.4) * (eck - 0.0002) / (0.001 - 0.0002)
odb = openOdb('chk_cdp.odb', readOnly=True)
peak = max(float(fr.fieldOutputs['S'].values[0].data[2]) for fr in odb.steps['Load'].frames)
expect('CDP peak tensile stress', peak, 3.0e6, 0.02)
expect('CDP stress at strain 0.0006', last(odb, 'S', 2), sig, 0.03)
expect('CDP tension damage DAMAGET', last(odb, 'DAMAGET'), dt, 0.03)
expect('CDP stiffness degradation SDEG', last(odb, 'SDEG'), dt, 0.03)
odb.close()

# Mohr-Coulomb: unconfined compression strength 2 c cos(phi) / (1 - sin(phi))
phi, c = math.radians(30.0), 20.0
odb = openOdb('chk_mc.odb', readOnly=True)
expect('MC unconfined compressive stress', last(odb, 'S', 2), -2.0 * c * math.cos(phi) / (1.0 - math.sin(phi)), 0.01)
odb.close()
print('MATERIALS %s' % ('OK' if not problems else 'FAILED: ' + ', '.join(problems)))
sys.exit(1 if problems else 0)
