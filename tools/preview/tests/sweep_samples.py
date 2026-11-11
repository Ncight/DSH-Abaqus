# -*- coding: utf-8 -*-
"""Robustness sweep: parse a sample of the official Abaqus example inputs straight from samples.zip.

    py sweep_samples.py [--count 400] [--max-kb 300] [--module-dir DIR] [--zip PATH] [--show 15]

Each input is extracted (with the files it includes, when they are in the archive) into a temporary
folder outside the workspace, parsed, turned into a view and thrown away again.  Reports exceptions
(there must be none), how many files came out complete, and the most common reasons for an incomplete
preview or an unknown element type.  No Abaqus needed; memory use stays small (one file at a time).
"""
import argparse
import collections
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = r'D:\SIMULIA\EstProducts\2021\win_b64\SMA\samples\job_archive\samples.zip'
INC = re.compile(rb'(?im)^\*\s*(?:INCLUDE|NODE|ELEMENT|NSET|ELSET)\b[^\n]*?\bINPUT\s*=\s*"?([^\s",]+)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--count', type=int, default=400)
    ap.add_argument('--max-kb', type=int, default=300)
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--zip', default=ZIP)
    ap.add_argument('--show', type=int, default=15)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, a.module_dir)
    import inp_model
    z = zipfile.ZipFile(a.zip)
    by_name = {i.filename.lower(): i for i in z.infolist()}
    inps = sorted(i.filename for i in z.infolist()
                  if i.filename.lower().endswith('.inp') and 400 < i.file_size <= a.max_kb * 1024)
    step = max(1, len(inps) // a.count)
    picked = inps[::step][:a.count]
    work = tempfile.mkdtemp(prefix='abq_preview_sweep_')
    stats = collections.Counter()
    why = collections.Counter()
    unknown = collections.Counter()
    errors = []
    slow = []
    t_all = time.time()
    try:
        for name in picked:
            d = os.path.join(work, 'f')
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
            todo, seen = [name], set()
            while todo and len(seen) < 12:
                n = todo.pop()
                info = by_name.get(os.path.basename(n).lower())
                if info is None or info.filename in seen or info.file_size > 30e6:
                    continue
                seen.add(info.filename)
                raw = z.read(info)
                with open(os.path.join(d, os.path.basename(info.filename)), 'wb') as f:
                    f.write(raw)
                for m in INC.finditer(raw):
                    todo.append(m.group(1).decode('latin-1'))
            path = os.path.join(d, os.path.basename(name))
            t0 = time.time()
            try:
                mdl = inp_model.Model(path)
                v = mdl.view()
                json.dumps(v)
                for i in range(-1, min(len(v['steps']), 3)):
                    mdl.symbols(i)
                for s in v['surfaces'][:5]:
                    mdl.highlight('surface', s['name'])
            except Exception:
                stats['exception'] += 1
                errors.append((name, traceback.format_exc().strip().splitlines()[-1], traceback.format_exc()))
                continue
            dt = time.time() - t0
            if dt > 2.0:
                slow.append((round(dt, 1), name))
            stats['parsed'] += 1
            st = v['stats']
            if not st['elements']:
                stats['no elements'] += 1
            if v['incomplete']:
                stats['incomplete'] += 1
                for w in v['incomplete']:
                    why[re.sub(r'[:：].*', '', w)[:40]] += 1
            for t, n in st['unknownTypes'].items():
                unknown[re.sub(r'\d+$', '#', t)] += 1
            levels = collections.Counter(c['level'] for c in v['checks'])
            stats['with errors'] += 1 if levels['error'] else 0
            stats['with warnings'] += 1 if levels['warn'] else 0
            stats['generated mesh'] += 1 if any(k in st['features'] for k in ('NGEN', 'NFILL', 'NCOPY', 'ELGEN')) else 0
            stats['parameters'] += 1 if 'PARAMETER' in st['features'] else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('%d of %d sampled inputs (<= %d KB) from %s in %.1f s' % (len(picked), len(inps), a.max_kb,
                                                                 os.path.basename(a.zip), time.time() - t_all))
    for k in ('parsed', 'exception', 'no elements', 'incomplete', 'generated mesh', 'parameters', 'with errors',
              'with warnings'):
        print('  %-16s %d' % (k, stats[k]))
    if why:
        print('reasons for an incomplete preview:')
        for k, n in why.most_common(a.show):
            print('  %4d  %s' % (n, k))
    if unknown:
        print('element types the preview does not draw (files):')
        print('  ' + ', '.join('%s×%d' % kv for kv in unknown.most_common(40)))
    if slow:
        print('slow files:', sorted(slow, reverse=True)[:5])
    for name, last, full in errors[:a.show]:
        print('\nEXCEPTION in %s: %s' % (name, last))
        print('    ' + '\n    '.join(full.splitlines()[-7:-1]))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
