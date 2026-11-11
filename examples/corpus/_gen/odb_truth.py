# -*- coding: utf-8 -*-
"""Ground truth for the preview corpus, taken from Abaqus itself.

    py odb_truth.py [--only substring] [--jobs 1] [--force]

For every stand-alone INP under examples/corpus/ this runs `abaqus datacheck` in a scratch folder and
dumps the resulting output database with dump_odb.py: node / element counts, element types, bounding
box and a sample of node coordinates.  Everything is stored in odb_truth.json next to this script, so
the test runner (tools/preview/tests/run_corpus.py) can compare the preview parser against what the
solver really built - without needing Abaqus at test time.

Each Abaqus run is memory-capped (memory=2000 MB plus an abaqus_v6.env in the work folder) and is only
started while at least MIN_FREE_GB of commit memory is free; runs are sequential by default.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.dirname(HERE)
ABQ = r'D:\SIMULIA\Commands\abaqus.bat'
TRUTH = os.path.join(HERE, 'odb_truth.json')
TRUTH_OFFICIAL = os.path.join(HERE, 'odb_truth_official.json')     # official samples: local only, not in the repository
INC = re.compile(r'(?im)^\*\s*(?:INCLUDE|NODE|ELEMENT|NSET|ELSET)\b[^\n]*?\bINPUT\s*=\s*"?([^\s",]+)')
MSG = re.compile(r'^\s*\*\*\*(WARNING|ERROR|NOTE)')
MIN_FREE_GB = 4.0          # commit memory that must be free before an Abaqus run is started
sys.path.insert(0, os.path.join(CORPUS, '..', '..', 'tools', 'preview', 'tests'))
import heavy_gate  # noqa: E402   (one heavy process at a time, only with memory to spare)


def read_text(p):
    raw = open(p, 'rb').read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', 'replace')


def standalone_inps():
    """INPs that are not merely included by a neighbour."""
    out = []
    for d, dirs, files in os.walk(CORPUS):
        dirs[:] = [x for x in dirs if not x.startswith('_')]
        inps = [f for f in files if f.lower().endswith('.inp')]
        included = set()
        for f in inps:
            for m in INC.finditer(read_text(os.path.join(d, f))):
                included.add(os.path.basename(m.group(1)).lower())
        for f in sorted(inps):
            if f.lower() not in included:
                out.append(os.path.relpath(os.path.join(d, f), CORPUS).replace('\\', '/'))
    return out


def messages(work, job):
    out = []
    for ext in ('.dat', '.msg', '.pre'):
        p = os.path.join(work, job + ext)
        if not os.path.isfile(p):
            continue
        lines = read_text(p).splitlines()
        i = 0
        while i < len(lines) and len(out) < 60:
            if MSG.match(lines[i]):
                para = [lines[i].strip()]
                j = i + 1
                while j < len(lines) and lines[j].strip() and not MSG.match(lines[j]) and len(para) < 6:
                    para.append(lines[j].strip())
                    j += 1
                out.append(' '.join(para))
                i = j
            else:
                i += 1
    return out


def free_commit_gb():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    st = MS()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPageFile / 2.0 ** 30


def wait_for_memory(need=MIN_FREE_GB, tries=30):
    """do not start Abaqus while the machine is short of commit memory (other solves may be running)"""
    for _ in range(tries):
        free = free_commit_gb()
        if free >= need:
            return
        print('   free commit memory %.1f GB < %.1f GB, waiting ...' % (free, need))
        sys.stdout.flush()
        time.sleep(60)
    raise RuntimeError('not enough free commit memory to start Abaqus (%.1f GB)' % free_commit_gb())


def run_one(rel, scratch):
    src = os.path.join(CORPUS, rel)
    job = os.path.splitext(os.path.basename(rel))[0]
    work = os.path.join(scratch, re.sub(r'[^A-Za-z0-9_.-]', '_', rel))
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    for f in os.listdir(os.path.dirname(src)):          # the INP plus anything it may include
        p = os.path.join(os.path.dirname(src), f)
        if os.path.isfile(p) and os.path.getsize(p) < 20e6:
            shutil.copy2(p, work)
    res = {'status': 'failed'}
    try:
        # memory cap: without it Abaqus/pre reserves several GB even for a tiny job
        with open(os.path.join(work, 'abaqus_v6.env'), 'w') as f:
            f.write('memory = "2 gb"' + os.linesep)
        with heavy_gate.slot('abaqus datacheck ' + job, MIN_FREE_GB):
            r = subprocess.run(['cmd', '/c', ABQ, 'job=' + job, 'datacheck', 'interactive', 'ask_delete=OFF',
                                'memory=2000'], cwd=work, capture_output=True, text=True, timeout=900)
        log = (r.stdout or '') + (r.stderr or '')
        res['log'] = [l for l in log.splitlines() if l.strip()][-4:]
        odb = os.path.join(work, job + '.odb')
        res['messages'] = messages(work, job)
        # the tie / connectivity diagnostics are counted in full (the message list itself is capped)
        full = ''.join(read_text(os.path.join(work, job + ext)) for ext in ('.dat', '.msg')
                       if os.path.isfile(os.path.join(work, job + ext)))
        res['notTied'] = len(re.findall(r'WILL NOT BE TIED', full))
        m = re.search(r'THERE ARE (\d+) UNCONNECTED REGIONS', full)
        res['unconnectedRegions'] = int(m.group(1)) if m else 0
        if os.path.isfile(odb):
            out = os.path.join(work, job + '.truth.json')
            with heavy_gate.slot('abaqus python (odb dump) ' + job, MIN_FREE_GB):
                subprocess.run(['cmd', '/c', ABQ, 'python', os.path.join(HERE, 'dump_odb.py'), job + '.odb',
                                job + '.truth.json'], cwd=work, capture_output=True, text=True, timeout=600)
            if os.path.isfile(out):
                res.update(json.load(open(out)))
                res['status'] = 'ok' if 'COMPLETED' in log else 'odb-but-errors'
    except Exception as e:  # keep going, record why
        res['error'] = '%s: %s' % (type(e).__name__, e)
    return rel, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--jobs', type=int, default=1,
                    help='Abaqus runs in parallel; keep 1 on a machine that is busy with other solves')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--scratch', default=os.path.join(tempfile.gettempdir(), 'preview_corpus_dc'))
    a = ap.parse_args()
    truth = json.load(open(TRUTH)) if os.path.isfile(TRUTH) else {}
    if os.path.isfile(TRUTH_OFFICIAL):
        truth.update(json.load(open(TRUTH_OFFICIAL)))
    todo = [r for r in standalone_inps() if a.only in r and (a.force or r not in truth)]
    print('%d input files to check, scratch = %s' % (len(todo), a.scratch))
    os.makedirs(a.scratch, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for rel, res in ex.map(lambda r: run_one(r, a.scratch), todo):
            truth[rel] = res
            print('%-58s %-14s nodes=%s elements=%s' % (rel, res['status'], res.get('nodes'), res.get('elements')))
            sys.stdout.flush()
            json.dump({k: v for k, v in truth.items() if not k.startswith('official/')}, open(TRUTH, 'w'),
                      indent=1, sort_keys=True)
            json.dump({k: v for k, v in truth.items() if k.startswith('official/')}, open(TRUTH_OFFICIAL, 'w'),
                      indent=1, sort_keys=True)


if __name__ == '__main__':
    main()
