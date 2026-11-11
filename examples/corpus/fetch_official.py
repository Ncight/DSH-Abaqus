# -*- coding: utf-8 -*-
"""Copy the official Abaqus sample inputs used by the preview tests from YOUR Abaqus installation into
examples/corpus/official/ (they are Dassault Systemes material and are not distributed with this repository).

    py fetch_official.py

Uses `abq fetch`, which reads the samples archive of the local installation (see ABQ_SAMPLES_ZIP in tools/abq/abq.py).
The tests in tools/preview/tests/ run without these files; with them, the parser is also checked on 37 official models.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ABQ = os.path.join(HERE, '..', '..', 'abq.cmd')
dest = os.path.join(HERE, 'official')
names = [l.strip() for l in open(os.path.join(HERE, 'official_files.txt')) if l.strip()]
missing = 0
for n in names:
    if os.path.isfile(os.path.join(dest, n)):
        continue
    p = subprocess.run(['cmd.exe', '/d', '/c', os.path.abspath(ABQ), 'fetch', n, '--to', dest], capture_output=True)
    ok = os.path.isfile(os.path.join(dest, n))
    missing += not ok
    print('%s %s' % ('ok  ' if ok else 'MISSING', n))
print('%d of %d files present' % (len(names) - missing, len(names)))
sys.exit(1 if missing else 0)
