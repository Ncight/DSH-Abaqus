# -*- coding: utf-8 -*-
"""Which slave nodes does Abaqus still tie when no position tolerance is given?

    py tie_tolerance_probe.py

Takes checks/chk_tie_gap.inp (pad above a beam, both meshed with 0.025 m elements, tie without a
position tolerance, Abaqus/Standard, i.e. the surface-to-surface formulation), moves the pad, runs a
memory-capped `abaqus datacheck` for each position and counts the "WILL NOT BE TIED" warnings.

  gap series       pad lifted off the beam by a few millimetres: where is the default tolerance?
                   (documented: 5 % / 10 % of the typical main facet diagonal, here 0.035355 m, for the
                   node-to-surface / surface-to-surface formulation -> 0.00177 m / 0.00354 m)
  overhang series  pad in contact but hanging over the edge of the beam by 0.1 m, 0.0875 m, 0.09375 m:
                   the first row of overhanging slave faces lies 0 %, 50 %, 25 % over the beam - does the
                   surface-to-surface rule ("a significant portion of a secondary face within the
                   tolerance ties all its nodes") rescue the first overhanging row of 13 nodes?

The result calibrates the tie check of inp_model.py.  Results go to tie_tolerance_probe.json.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import odb_truth  # noqa: E402

DIAG = 0.0353553
# (label, pad instance translation)
CASES = [('gap_%.4f' % g, (0.0, 0.1 + g, 0.35)) for g in (0.0015, 0.0020, 0.0033, 0.0038)] + \
        [('overhang_%.5f' % o, (o, 0.1, 0.35)) for o in (0.1, 0.0875, 0.09375)]
SRC = os.path.join(os.path.dirname(HERE), 'checks', 'chk_tie_gap.inp')
OUT = os.path.join(HERE, 'tie_tolerance_probe.json')


def main():
    text = odb_truth.read_text(SRC)
    pat = re.compile(r'(\*Instance, name=Pad-1, part=Pad\s*\n)([^\n]*)\n')
    if not pat.search(text):
        raise SystemExit('instance line not found in ' + SRC)
    scratch = tempfile.mkdtemp(prefix='tie_tol_probe_')
    out = json.load(open(OUT)) if os.path.isfile(OUT) else {}
    try:
        for label, pos in CASES:
            if label in out and out[label].get('completed'):
                continue
            job = re.sub(r'[^A-Za-z0-9_]', '_', 'tie_' + label)
            work = os.path.join(scratch, job)
            os.makedirs(work)
            line = '%s, %s, %s' % tuple(repr(float(v)) for v in pos)
            with open(os.path.join(work, job + '.inp'), 'w', newline='\n') as f:
                f.write(pat.sub(lambda m: m.group(1) + line + '\n', text, count=1))
            with open(os.path.join(work, 'abaqus_v6.env'), 'w') as f:
                f.write('memory = "2 gb"' + os.linesep)
            with odb_truth.heavy_gate.slot('abaqus datacheck ' + job, odb_truth.MIN_FREE_GB, 90):
                r = subprocess.run(['cmd', '/c', odb_truth.ABQ, 'job=' + job, 'datacheck', 'interactive',
                                    'ask_delete=OFF', 'memory=2000'], cwd=work, capture_output=True, text=True,
                                   timeout=900)
            full = ''.join(odb_truth.read_text(os.path.join(work, job + ext)) for ext in ('.dat', '.msg')
                           if os.path.isfile(os.path.join(work, job + ext)))
            # the data file prints at most 11 of these warnings ("suppressed due to excessive reporting");
            # the real number is the size of the internal node set WarnNodeMissMasterIntersect in the odb
            warned = len(re.findall(r'WILL NOT BE TIED', full))
            ok = 'COMPLETED' in ((r.stdout or '') + (r.stderr or ''))
            sets = {}
            if os.path.isfile(os.path.join(work, job + '.odb')):
                with odb_truth.heavy_gate.slot('abaqus python (tie sets) ' + job, odb_truth.MIN_FREE_GB, 90):
                    subprocess.run(['cmd', '/c', odb_truth.ABQ, 'python', os.path.join(HERE, 'dump_tie_sets.py'),
                                    job + '.odb', job + '.tiesets.json'], cwd=work, capture_output=True, text=True,
                                   timeout=600)
                p = os.path.join(work, job + '.tiesets.json')
                if os.path.isfile(p):
                    sets = json.load(open(p))
            n = sum(v for k, v in sets.items() if 'WarnNodeMiss' in k) if sets else (warned if warned < 11 else None)
            out[label] = {'notTied': n, 'warningsPrinted': warned, 'completed': ok, 'position': list(pos),
                          'odbSets': sets}
            print('%-18s pad at %-28s %s of 117 slave nodes not tied (%d warnings printed)   %s' % (
                label, line, n, warned, 'ok' if ok else 'datacheck did not complete'))
            sys.stdout.flush()
            json.dump(out, open(OUT, 'w'), indent=1, sort_keys=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == '__main__':
    main()
