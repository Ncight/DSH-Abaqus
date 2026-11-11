# -*- coding: utf-8 -*-
"""How does wall time depend on cpus= for small and mid-size Abaqus/Standard models on this machine?
Synthetic C3D8R block (same writer as the preview benchmark), one static increment and a 20-increment variant.
Run from an empty scratch folder (job files are written to the current directory):  py cpu_bench.py
The result decides SMALL_MODEL_NODES in tools/abq/abq.py; repeat after a hardware or Abaqus change.

Measured 2026-11-10 (Ryzen 9 9950X, Abaqus 2021), total job time cpus=1 / cpus=4:
      2,000 elements   1 increment     4.5 s / 13.5 s        20 increments    4.4 s / 15.9 s
     10,000 elements   1 increment     6.6 s / 15.5 s        20 increments   10.2 s / 17.5 s
     40,000 elements   1 increment    14.7 s / 17.8 s        20 increments   32.5 s / 27.5 s
    120,000 elements   1 increment    56.7 s / 33.8 s        20 increments  126.8 s / 94.3 s
and a 100-element beam model with 1,210 increments: 30 s on one core, 174 s on two."""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'preview', 'tests'))
import bench_large  # noqa: E402

HERE = os.getcwd()
rows = []
for n in (2000, 10000, 40000, 120000):
    base = 'blk%d' % n
    path = os.path.join(HERE, base + '.inp')
    ne = bench_large.write_solid(path, n)
    text = open(path).read()
    # 20 fixed increments instead of one, so that per-increment overhead shows
    multi = text.replace('*Static\n1., 1., 1e-05, 1.', '*Static, direct\n0.05, 1.')
    open(os.path.join(HERE, base + 'm.inp'), 'w').write(multi)
    for job_base, label in ((base, '1 inc'), (base + 'm', '20 inc')):
        for cpus in (1, 4):
            job = '%s_c%d' % (job_base, cpus)
            t0 = time.time()
            p = subprocess.run(['cmd.exe', '/d', '/c', 'abaqus', 'job=' + job, 'input=' + job_base + '.inp', 'cpus=%d' % cpus,
                                'interactive', 'ask_delete=OFF'], cwd=HERE, capture_output=True)
            sec = time.time() - t0
            wall = None
            dat = os.path.join(HERE, job + '.dat')
            if os.path.isfile(dat):
                m = re.findall(r'WALLCLOCK TIME \(SEC\) =\s+(\d+)', open(dat, errors='replace').read())
                wall = int(m[-1]) if m else None
            ok = b'COMPLETED' in p.stdout
            rows.append((ne, label, cpus, sec, wall, ok))
            print('%7d elements  %-6s cpus=%d  total %6.1f s  solver wallclock %s s  %s' % (ne, label, cpus, sec, wall, 'ok' if ok else 'FAILED'), flush=True)
            for ext in ('.odb', '.sim', '.stt', '.mdl', '.prt', '.res', '.com', '.msg', '.sta', '.023'):
                f = os.path.join(HERE, job + ext)
                if os.path.isfile(f):
                    os.remove(f)
