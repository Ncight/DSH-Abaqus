# -*- coding: utf-8 -*-
"""Same question as cpu_bench.py for Abaqus/Explicit: wall time with cpus=1 and cpus=4 for small and mid-size models.
Run from an empty scratch folder:  py cpu_bench_explicit.py"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'preview', 'tests'))
import bench_large  # noqa: E402

HERE = os.getcwd()
for n in (2000, 12000, 40000):
    base = 'xpl%d' % n
    path = os.path.join(HERE, base + '.inp')
    ne = bench_large.write_solid(path, n)
    text = open(path).read()
    # about 4000 stable increments: element 0.5 m, wave speed about 188 m/s -> dt about 0.0025 s
    text = text.replace('*Static\n1., 1., 1e-05, 1.', '*Dynamic, Explicit\n, 10.')
    open(path, 'w').write(text)
    for cpus in (1, 4):
        job = '%s_c%d' % (base, cpus)
        t0 = time.time()
        p = subprocess.run(['cmd.exe', '/d', '/c', 'abaqus', 'job=' + job, 'input=' + base + '.inp', 'cpus=%d' % cpus,
                            'interactive', 'ask_delete=OFF'], cwd=HERE, capture_output=True)
        sec = time.time() - t0
        incs = None
        sta = os.path.join(HERE, job + '.sta')
        if os.path.isfile(sta):
            nums = re.findall(r'(?m)^\s*(\d+)\s+[\d.E+-]+\s+[\d.E+-]+\s+\d\d:\d\d:\d\d', open(sta, errors='replace').read())
            incs = int(nums[-1]) if nums else None
        ok = b'COMPLETED' in p.stdout
        print('%7d elements  cpus=%d  total %6.1f s  increments %s  %s' % (ne, cpus, sec, incs, 'ok' if ok else 'FAILED: ' + p.stdout.decode('gbk', 'replace')[-200:]), flush=True)
        for ext in ('.odb', '.sim', '.stt', '.mdl', '.prt', '.res', '.com', '.msg', '.abq', '.pac', '.sel', '.023'):
            f = os.path.join(HERE, job + ext)
            if os.path.isfile(f):
                os.remove(f)
