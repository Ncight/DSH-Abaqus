# -*- coding: utf-8 -*-
"""The tie gap check must count the slave nodes Abaqus does not tie exactly as Abaqus does.

    py tie_rule_check.py [--module-dir DIR]

examples/corpus/_gen/tie_tolerance_probe.py moved the pad of checks/chk_tie_gap.inp to seven positions,
ran `abaqus datacheck` for each (Abaqus/Standard, no position tolerance given) and stored how many of the
117 slave nodes Abaqus left untied (size of the node set WarnNodeMissMasterIntersect in the output
database).  This test rebuilds the same seven inputs in a temporary folder and compares the number in
the 体检 message with those counts.  No Abaqus needed here.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'examples', 'corpus'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, os.path.abspath(a.module_dir))
    import inp_model
    probe = json.load(open(os.path.join(CORPUS, '_gen', 'tie_tolerance_probe.json')))
    # latin-1 keeps every byte as it is (CAE writes comments in the local code page)
    text = open(os.path.join(CORPUS, 'checks', 'chk_tie_gap.inp'), encoding='latin-1', newline='').read()
    pat = re.compile(r'(\*Instance, name=Pad-1, part=Pad\s*\n)([^\r\n]*)(\r?\n)')
    work = tempfile.mkdtemp(prefix='tie_rule_')
    bad = 0
    try:
        for label in sorted(probe):
            case = probe[label]
            if not case.get('completed') or case.get('notTied') is None:
                print('skip %-18s (no Abaqus count)' % label)
                continue
            line = ', '.join(repr(float(v)) for v in case['position'])
            p = os.path.join(work, label.replace('.', '_') + '.inp')
            with open(p, 'w', encoding='latin-1', newline='') as f:
                f.write(pat.sub(lambda m: m.group(1) + line + m.group(3), text, count=1))
            msgs = [c['msg'] for c in inp_model.Model(p).checks() if c['msg'].startswith('Tie ')]
            m = re.search(r'中有 (\d+) 个', msgs[0]) if msgs else None
            mine = int(m.group(1)) if m else 0
            ok = mine == case['notTied']
            bad += not ok
            print('%s %-18s pad at (%s): Abaqus leaves %3d of 117 slave nodes untied, preview says %3d' % (
                'ok  ' if ok else 'FAIL', label, line, case['notTied'], mine))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ALL OK' if not bad else '%d FAILED' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
