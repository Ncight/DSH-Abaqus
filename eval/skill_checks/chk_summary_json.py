# -*- coding: utf-8 -*-
"""The key paths of <job>.summary.json quoted in the skill abaqus-postprocess, checked on the fixture fx_cae
(system Python; run after `abq odb fx_cae.odb --set FixEnd --set Tip --series RF,U --probe 0.025,0.05,1`)."""
import json
import sys

d = json.load(open('fx_cae.summary.json', encoding='utf-8'))
problems = []


def expect(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        problems.append(what)


expect(len(d['frequencies']) == 3 and d['frequencies'][0]['frequency'] > 0, 'frequencies[i].frequency')
expect(abs(d['fields']['RF']['sum']['RF2'] - 1000.0) < 1.0, 'fields.RF.sum.RF2 = %.3f' % d['fields']['RF']['sum']['RF2'])
expect(d['fields']['U']['maxMagnitude']['value'] > 0, 'fields.U.maxMagnitude.value')
sr = d['setResults'][0]
rf = sr['fields']['RF']
expect(rf['columns'][1] == 'RF2' and abs(rf['sum'][1] - 1000.0) < 1.0, 'setResults[0].fields.RF.sum follows .columns (%s)' % rf['columns'])
rows = sr['series']['RF']['rows']
expect(len(rows) >= 2 and abs(rows[-1][2] - 1000.0) < 1.0 and sr['series']['RF']['columns'][2] == 'sum RF2',
       'setResults[0].series.RF.rows: last row %s' % rows[-1])
tip = d['setResults'][1]
expect(tip['series']['U']['columns'][2] == 'mean U2' and tip['series']['U']['rows'][-1][2] < 0, 'series of a displacement is the mean over the set')
expect(len(d['probes'][0]['series']['U']['rows']) == len(rows), 'probes[0].series.U.rows has one row per frame of the static step (mode shapes left out)')
expect(any(s['name'] == 'FIXEND' and s['kind'] == 'node' and s['holder'] == 'assembly' for s in d['sets']), 'sets[] lists FIXEND')
print('SUMMARY %s' % ('OK' if not problems else 'FAILED: ' + '; '.join(problems)))
sys.exit(1 if problems else 0)
