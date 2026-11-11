# -*- coding: utf-8 -*-
"""Index the Abaqus example archive (samples.zip) so `abq examples` can search it offline.

For every .inp: the set of keywords it uses (with a distinguishing first parameter such as EXPLICIT), element types, title.
For every .py: the CamelCase method names it calls.      py build_samples_index.py
"""
import gzip
import json
import os
import re
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.environ.get('ABQ_SAMPLES_ZIP', r'D:\SIMULIA\EstProducts\2021\win_b64\SMA\samples\job_archive\samples.zip')
MAX_BYTES = 40 << 20
KEEP_PARAM = {'*DYNAMIC': ('EXPLICIT', 'SUBSPACE'), '*CONTACT': (), '*SURFACE': ('TYPE=',), '*STEADY STATE DYNAMICS': ('DIRECT',),
              '*STATIC': ('RIKS', 'STABILIZE'), '*COUPLING': (), '*SOLID SECTION': ('COMPOSITE',), '*PLASTIC': ('HARDENING=',)}


def index_inp(raw):
    kws, elems, title = set(), set(), ''
    want_title = False
    for line in raw.split(b'\n'):
        if want_title:
            s = line.decode('latin-1').strip()
            if s and not s.startswith('*'):
                title = s[:120]
            want_title = False
        if not line.startswith(b'*') or line.startswith(b'**'):
            continue
        s = line.decode('latin-1').strip()
        parts = [p.strip() for p in s.split(',')]
        kw = ' '.join(parts[0].upper().split())
        kws.add(kw)
        up = [p.upper().replace(' ', '') for p in parts[1:]]
        for flag in KEEP_PARAM.get(kw, ()):
            for u in up:
                if u.startswith(flag):
                    kws.add('%s, %s' % (kw, u))
        if kw == '*ELEMENT':
            for u in up:
                if u.startswith('TYPE='):
                    elems.add(u[5:])
        if kw == '*HEADING' and not title:
            want_title = True
    return sorted(kws), sorted(elems), title


def index_py(raw):
    text = raw.decode('latin-1')
    apis = set(re.findall(r'\.([A-Z][A-Za-z0-9]{2,})\(', text))
    title = ''
    m = re.search(r'^\s*(?:#+|"""|\'\'\')\s*(\S.{5,110})', text, re.M)
    if m:
        title = m.group(1).strip().strip('"\'#').strip()
    return sorted(apis), title


def main():
    t0 = time.time()
    files = []
    with zipfile.ZipFile(ZIP) as z:
        infos = z.infolist()
        for k, info in enumerate(infos):
            name = os.path.basename(info.filename)
            if not name:
                continue
            ext = os.path.splitext(name)[1].lower()
            e = {'n': name, 's': info.file_size}
            if ext in ('.inp', '.py') and info.file_size <= MAX_BYTES:
                try:
                    raw = z.read(info)
                except Exception:
                    files.append(e)
                    continue
                if ext == '.inp':
                    e['k'], e['e'], e['t'] = index_inp(raw)
                else:
                    e['a'], e['t'] = index_py(raw)
            elif ext not in ('.inp', '.py', '.f', '.for', '.f90', '.c', '.cpp', '.h', '.txt', '.env', '.psf', '.par'):
                continue
            files.append(e)
            if k % 2000 == 0:
                print('%d / %d  %.0f s' % (k, len(infos), time.time() - t0), flush=True)
    os.makedirs(os.path.join(HERE, 'data'), exist_ok=True)
    out = os.path.join(HERE, 'data', 'samples_index.json.gz')
    with gzip.open(out, 'wt', encoding='utf-8') as f:
        json.dump({'zip': ZIP, 'built': time.strftime('%Y-%m-%d'), 'files': files}, f, separators=(',', ':'))
    print('indexed %d files (%d inp, %d py) in %.0f s -> %s (%.1f MB)' % (
        len(files), sum(1 for e in files if 'k' in e), sum(1 for e in files if 'a' in e), time.time() - t0, out,
        os.path.getsize(out) / 1e6))


if __name__ == '__main__':
    sys.exit(main())
