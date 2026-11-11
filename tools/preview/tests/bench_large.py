# -*- coding: utf-8 -*-
"""Time and peak memory of the INP preview parser on a synthetic model.

    py bench_large.py [--elements 100000] [--kind solid|shell] [--module-dir DIR] [--label NAME]
                      [--compare OLD_MODULE_DIR] [--keep] [--allow-large]

Writes a part-based INP (structured C3D8R block or S4R plate with sets, a surface, a tie-free
single instance, BC, pressure load, one static step) into a temporary folder OUTSIDE the workspace -
a preview server that watches the workspace must not pick a 30 MB file up by itself - parses it in
a fresh Python process and reports wall time and the peak working set / peak commit of that process
(GetProcessMemoryInfo).  With --compare the same file is also parsed with another copy of
inp_model.py (for example the previous version) so that the two can be put side by side.

Safety: a run above 50,000 elements needs 4 GB of free commit memory (GlobalMemoryStatusEx), a run
above 100,000 elements additionally needs --allow-large and 8 GB free; two parses never run at once.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_gate  # noqa: E402

CHILD = r'''
import ctypes, json, os, sys, time
sys.path.insert(0, sys.argv[1])
import inp_model
path = sys.argv[2]
mode = sys.argv[3]
t0 = time.time()
kw = {}
if 'force' in getattr(inp_model.Model.__init__, '__code__').co_varnames:
    kw['force'] = True
m = inp_model.Model(path, **kw)
t1 = time.time()
if mode == 'json' and hasattr(m, 'view_json'):
    body = m.view_json()                 # what the preview server sends
    nbytes = len(body)
    st = m.view(geom=False)['stats']
else:
    v = m.view()                         # the dict other tools get (and what the old server serialised)
    body = json.dumps(v, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    nbytes = len(body)
    st = v['stats']
t2 = time.time()


class PMC(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


pmc = PMC()
pmc.cb = ctypes.sizeof(pmc)
k32 = ctypes.windll.kernel32
k32.GetCurrentProcess.restype = ctypes.c_void_p
k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
print(json.dumps({'parse_s': round(t1 - t0, 2), 'view_s': round(t2 - t1, 2), 'json_mb': round(nbytes / 1048576.0, 2),
                  'peak_ws_mb': round(pmc.PeakWorkingSetSize / 1048576.0, 1),
                  'peak_commit_mb': round(pmc.PeakPagefileUsage / 1048576.0, 1),
                  'nodes': st['nodes'], 'elements': st['elements']}))
'''


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


def write_solid(path, n_elem):
    """one part: nx x ny x nz block of C3D8R, written the way CAE writes a part / instance file"""
    nx = max(2, int(round((n_elem / 4.0) ** (1.0 / 3.0))))
    ny = nx
    nz = max(2, int(round(n_elem / float(nx * ny))))
    h = 0.5
    with open(path, 'w', newline='\n') as f:
        w = f.write
        w('*Heading\n** synthetic %d x %d x %d block for the preview benchmark\n*Part, name=Soil\n*Node\n' % (nx, ny, nz))
        lab = 0
        for k in range(nz + 1):
            for j in range(ny + 1):
                for i in range(nx + 1):
                    lab += 1
                    w('%7d, %12.6g, %12.6g, %12.6g\n' % (lab, i * h, j * h, k * h))
        w('*Element, type=C3D8R\n')
        sx, sy = nx + 1, (nx + 1) * (ny + 1)
        el = 0
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    el += 1
                    a = 1 + i + j * sx + k * sy
                    w('%d, %d, %d, %d, %d, %d, %d, %d, %d\n' % (el, a, a + 1, a + 1 + sx, a + sx,
                                                               a + sy, a + 1 + sy, a + 1 + sx + sy, a + sx + sy))
        nn, ne = lab, el
        w('*Nset, nset=All, generate\n 1, %d, 1\n*Elset, elset=All, generate\n 1, %d, 1\n' % (nn, ne))
        w('*Nset, nset=Base, generate\n 1, %d, 1\n' % sy)
        w('*Elset, elset=TopLayer, generate\n %d, %d, 1\n' % (ne - nx * ny + 1, ne))
        # an explicitly listed set, 16 labels per line, as CAE writes picked sets
        picked = list(range(1, ne + 1, 3))
        w('*Elset, elset=Picked\n')
        for i in range(0, len(picked), 16):
            w(', '.join(str(v) for v in picked[i:i + 16]) + '\n')
        w('*Surface, type=ELEMENT, name=Top\nTopLayer, S2\n*Solid Section, elset=All, material=Soil\n,\n*End Part\n')
        w('*Assembly, name=Assembly\n*Instance, name=Soil-1, part=Soil\n 10., 0., -5.\n*End Instance\n*End Assembly\n')
        w('*Material, name=Soil\n*Density\n 1.9,\n*Elastic\n 5.e4, 0.3\n*Boundary\nSoil-1.Base, ENCASTRE\n')
        w('*Step, name=Load\n*Static\n1., 1., 1e-05, 1.\n*Dsload\nSoil-1.Top, P, 100.\n*End Step\n')
    return ne


def write_shell(path, n_elem):
    nx = max(2, int(round(n_elem ** 0.5)))
    ny = max(2, int(round(n_elem / float(nx))))
    h = 0.5
    with open(path, 'w', newline='\n') as f:
        w = f.write
        w('*Heading\n** synthetic %d x %d shell plate for the preview benchmark\n*Part, name=Plate\n*Node\n' % (nx, ny))
        lab = 0
        for j in range(ny + 1):
            for i in range(nx + 1):
                lab += 1
                w('%7d, %12.6g, %12.6g, %12.6g\n' % (lab, i * h, j * h, 0.0))
        w('*Element, type=S4R\n')
        sx = nx + 1
        el = 0
        for j in range(ny):
            for i in range(nx):
                el += 1
                a = 1 + i + j * sx
                w('%d, %d, %d, %d, %d\n' % (el, a, a + 1, a + 1 + sx, a + sx))
        w('*Nset, nset=Edge, generate\n 1, %d, 1\n*Elset, elset=All, generate\n 1, %d, 1\n' % (sx, el))
        w('*Surface, type=ELEMENT, name=Top\nAll, SPOS\n*Shell Section, elset=All, material=Steel\n 0.02, 5\n*End Part\n')
        w('*Assembly, name=Assembly\n*Instance, name=Plate-1, part=Plate\n*End Instance\n*End Assembly\n')
        w('*Material, name=Steel\n*Density\n 7.85,\n*Elastic\n 2.06e8, 0.3\n*Boundary\nPlate-1.Edge, ENCASTRE\n')
        w('*Step, name=Load\n*Static\n1., 1., 1e-05, 1.\n*Dsload\nPlate-1.Top, P, 1.\n*End Step\n')
    return el


def run(module_dir, path, mode):
    r = subprocess.run([sys.executable, '-c', CHILD, module_dir, path, mode], capture_output=True, text=True)
    if r.returncode:
        return {'error': (r.stderr or r.stdout).strip().splitlines()[-1:]}
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elements', type=int, default=100000)
    ap.add_argument('--allow-large', action='store_true', help='permit more than 100,000 elements')
    ap.add_argument('--kind', choices=('solid', 'shell'), default='solid')
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--label', default='current')
    ap.add_argument('--compare', default='', help='directory of another inp_model.py to measure on the same file')
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--min-free-gb', type=float, default=None,
                    help='free commit memory required before a parse (default 1.5 / 4 / 8 GB by model size); '
                         'a 100,000-element parse peaks below 0.2 GB even with the old parser')
    a = ap.parse_args()
    if a.elements > 100000 and not a.allow_large:
        print('more than 100,000 elements needs --allow-large (and a machine that is not busy)')
        return 2
    need = 8.0 if a.elements > 100000 else 4.0 if a.elements > 50000 else 1.5
    if a.min_free_gb is not None and a.elements <= 100000:
        need = max(1.5, a.min_free_gb)
    work = tempfile.mkdtemp(prefix='abq_preview_bench_')
    path = os.path.join(work, 'bench_%s_%d.inp' % (a.kind, a.elements))
    try:
        n = (write_solid if a.kind == 'solid' else write_shell)(path, a.elements)
        size = os.path.getsize(path) / 1048576.0
        print('%s model: %d elements, %.1f MB INP (%s)' % (a.kind, n, size, path))
        rows = [(a.label, a.module_dir, 'json')]
        if a.compare:
            rows.append(('compare', a.compare, 'dict'))
        for label, mdir, mode in rows:
            try:        # one heavy step at a time, only with memory to spare (waits up to 30 min)
                with heavy_gate.slot('parse of %d elements (%s)' % (n, label), need, 30):
                    res = run(mdir, path, mode)
            except TimeoutError as e:
                print('%-8s skipped: %s' % (label, e))
                continue
            if 'error' in res:
                print('%-8s FAILED %s' % (label, res['error']))
                continue
            print('%-8s parse %6.2f s   view+json %6.2f s   peak working set %7.1f MB   peak commit %7.1f MB   '
                  '(%.1f x file size)   json %.1f MB' % (label, res['parse_s'], res['view_s'], res['peak_ws_mb'],
                                                         res['peak_commit_mb'], res['peak_commit_mb'] / size,
                                                         res['json_mb']))
            time.sleep(0.5)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
