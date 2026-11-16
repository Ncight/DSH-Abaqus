# -*- coding: utf-8 -*-
"""Several workspaces in one panel, solver detection by content, and the review fingerprint (ANSYS plan, step 1).

    py test_multiroot.py [--server-dir DIR] [--port 8769]

Everything happens in temp folders: three workspaces A, B (both exist) and C (not created), with copies of
two small INPs from examples/, an Abaqus .dat print file, a MAPDL command stream saved as .inp and a short
CDB.  Checks, with urllib against real server processes (no browser):
  * solvers.py and fingerprint.py on their own;
  * one workspace: nothing new shows up (no `root`, no `solver` on Abaqus files) and a CDB is not listed
    as long as there is no CDB parser (mapdl_model.py);
  * three workspaces: `root` and `solver` on every file, `roots` in the answer, the same relative path in
    two workspaces kept apart, review decisions written to the workspace the file is in, bad workspace
    numbers and paths that leave a workspace refused;
  * with a stand-in mapdl_model.py next to a copy of the server: CDB files are listed as mapdl, the
    decision is bound to the CDB together with <job>.solve.dat, and the panel says which of the two changed.
Exit code 1 on failure.
"""
import argparse
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
BEAM = os.path.join(ROOT, 'examples', 'demo_beam', 'demo_beam.inp')
POISSON = os.path.join(ROOT, 'examples', 'corpus', 'checks', 'chk_material_poisson.inp')
FAILS = []

# the first lines of a CDB written by MAPDL 2025 R1 (CDWRITE,DB in step 0 of the ANSYS plan), cut short
CDB = """/COM,ANSYS RELEASE 2025 R1           BUILD 25.1      UP20241202       16:09:36
/PREP7
/NOPR
/TITLE,DSH step0 t09 cantilever model for result extraction
*IF,_CDRDOFF,EQ,1,THEN     !if solid model was read in
_CDRDOFF=             !reset flag, numoffs already performed
*ELSE              !offset database for the following FE model
NUMOFF,NODE,       11
NUMOFF,ELEM,       10
*ENDIF
*SET,_RETURN ,  0.000000000000
NBLOCK,6,SOLID,        11,        11
(3i9,6e21.13e3)
        1        0        0
        2        0        0 1.0000000000000E-01
N,R5.3,LOC,       -1,
/GO
FINISH
"""
APDL = """! a MAPDL command stream that happens to be called .inp
/PREP7
ET,1,BEAM188
MP,EX,1,2.0E11
FINISH
"""
STUB = '''# test stand-in for the CDB parser: same interface as inp_model.Model, no parsing
import json
import inp_model
TooLargeError = inp_model.TooLargeError
MemoryRefusedError = inp_model.MemoryRefusedError


class Model(object):
    def __init__(self, path, force=False, max_auto_mb=None):
        self.path = path

    def checks(self):
        return [{'level': 'warn', 'msg': 'stand-in parser'}]

    def view_json_iter(self, extra):
        d = {'solver': 'mapdl', 'stats': {'nodes': 2, 'elements': 1}, 'steps': [], 'checks': self.checks()}
        d.update(extra)
        yield json.dumps(d, ensure_ascii=False).encode('utf-8')

    def highlight_json(self, kind, name):
        return b'{"n":0,"pts":[],"tris":[],"segs":[]}'

    def symbols(self, step_index):
        return {'bc': [], 'cload': [], 'cmom': [], 'press': [], 'grav': []}
'''


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def write(path, text, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))


def copy(src, dst, mtime):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.utime(dst, (mtime, mtime))


class Client(object):
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        h = {'Host': '127.0.0.1:%d' % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw.decode('utf-8'))
        except ValueError:
            return r.status, raw

    def get(self, path):
        return self.request('GET', path)

    def post(self, obj):
        return self.request('POST', '/api/review', json.dumps(obj).encode('utf-8'), {'Content-Type': 'application/json'})


def start(server_dir, roots, port):
    cmd = [sys.executable, os.path.join(server_dir, 'preview_server.py'), '--port', str(port)]
    for r in roots:
        cmd += ['--root', r]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    c = Client(port)
    for _ in range(100):
        try:
            if c.get('/api/health')[0] == 200:
                return p, c
        except OSError:
            time.sleep(0.1)
    p.terminate()
    raise SystemExit('server did not start: %s' % p.stdout.read().decode('utf-8', 'replace'))


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
    return p.stdout.read().decode('utf-8', 'replace')


def unit_tests(server_dir, tmp):
    sys.path.insert(0, server_dir)
    import fingerprint
    import solvers
    print('solvers.py')
    d = os.path.join(tmp, 'unit')
    cases = [('abaqus.inp', '** comment\n*Heading\n*Node\n1, 0., 0., 0.\n', 'abaqus', 'abaqus'),
             ('include_data_only.inp', '1, 0., 0., 0.\n2, 1., 0., 0.\n', None, 'abaqus'),
             ('empty.inp', '', None, 'abaqus'),
             ('apdl.inp', APDL, 'apdl', None),
             ('apdl_star.inp', '! only APDL star commands\n*SET,A,1\n*DIM,T,TABLE,3\n', 'apdl', None),
             ('job.cdb', CDB, 'mapdl', 'mapdl'),
             ('cdb_named.inp', CDB, 'mapdl', 'mapdl'),
             ('abaqus_named.cdb', '*Heading\n*Node\n1, 0., 0., 0.\n', 'abaqus', None),
             ('empty.cdb', '', None, None),
             ('blocks_only.cdb', 'ETBLOCK,1,1\n(2i9,19a9)\n', 'mapdl', 'mapdl')]
    for name, text, want_sniff, want_class in cases:
        p = os.path.join(d, name)
        write(p, text)
        got = (solvers.sniff(p), solvers.classify(p))
        check(got == (want_sniff, want_class), '%-22s sniff %-7s classify %s' % (name, got[0], got[1]))
    check(solvers.classify(os.path.join(d, 'x.dat')) is None, '.dat is never a model file')

    print('fingerprint.py')
    inp = os.path.join(d, 'abaqus.inp')
    check(fingerprint.fingerprint(inp, 'abaqus') == {'sha256': sha(inp), 'parts': None}, 'Abaqus: sha256 of the INP itself')
    cdb = os.path.join(d, 'job.cdb')
    solve = os.path.join(d, 'job.solve.dat')
    check(fingerprint.solve_path(cdb) == solve, 'job.cdb -> job.solve.dat')
    fp0 = fingerprint.fingerprint(cdb, 'mapdl')
    want = hashlib.sha256((sha(cdb) + '\nnone').encode('ascii')).hexdigest()
    check(fp0 == {'sha256': want, 'parts': {'cdb': sha(cdb), 'solve': None}}, 'MAPDL without a solve segment: sha256(cdb digest + "\\nnone")')
    write(solve, '/SOLU\nANTYPE,STATIC\nSOLVE\nFINISH\n')
    fp1 = fingerprint.fingerprint(cdb, 'mapdl')
    want = hashlib.sha256((sha(cdb) + '\n' + sha(solve)).encode('ascii')).hexdigest()
    check(fp1['sha256'] == want and fp1['parts'] == {'cdb': sha(cdb), 'solve': sha(solve)},
          'MAPDL: sha256(sha256(cdb) + "\\n" + sha256(solve.dat)), both digests kept')
    write(solve, '/SOLU\nANTYPE,STATIC\nNSUBST,5\nSOLVE\nFINISH\n')
    fp2 = fingerprint.fingerprint(cdb, 'mapdl')
    check(fp2['sha256'] != fp1['sha256'] and fp2['parts']['cdb'] == fp1['parts']['cdb'], 'a changed solve segment changes the fingerprint')
    write(cdb, CDB + '\n')
    fp3 = fingerprint.fingerprint(cdb, 'mapdl')
    check(fp3['sha256'] != fp2['sha256'] and fp3['parts']['solve'] == fp2['parts']['solve'], 'a changed CDB changes the fingerprint')


def make_workspaces(tmp):
    t0 = time.time() - 3600
    a, b, c = (os.path.join(tmp, n) for n in ('WS_A', 'WS_B', 'WS_C'))
    copy(BEAM, os.path.join(a, 'job', 'm.inp'), t0 + 10)            # same relative path in A and B,
    copy(POISSON, os.path.join(b, 'job', 'm.inp'), t0 + 20)         # different models
    copy(BEAM, os.path.join(b, 'only_b.inp'), t0 + 30)              # newest of all
    write(os.path.join(a, 'job', 'm.dat'), 'Abaqus print output\n', t0 + 40)
    write(os.path.join(a, 'script.inp'), APDL, t0 + 50)
    write(os.path.join(b, 'cant', 'cant.cdb'), CDB, t0 + 60)
    write(os.path.join(a, 'tools', 'skipped.inp'), '*Heading\n', t0 + 70)
    return a, b, c


def single_root(server_dir, ws, port):
    print('one workspace (as before)')
    p, c = start(server_dir, [ws], port)
    try:
        st, d = c.get('/api/list')
        check(st == 200 and list(d) == ['root', 'files', 'maxAutoMB'], 'answer has exactly root / files / maxAutoMB: %s' % list(d))
        names = [f['path'] for f in d['files']]
        check(names == ['job/m.inp'], 'only the INP is listed (no .dat, no command stream, nothing under tools/): %s' % names)
        check(all(set(f) == {'path', 'mtime', 'size'} for f in d['files']), 'Abaqus items carry only path / mtime / size')
        st, d = c.get('/api/model?path=script.inp')
        check(st == 500 and '无效的文件路径' in d.get('error', ''), 'a command stream saved as .inp is not served')
        st, d = c.get('/api/model?path=job/m.inp&root=1')
        check(st == 500 and '无效的工作区序号' in d.get('error', ''), 'root=1 with one workspace is refused')
        st, d = c.get('/api/model?path=job/m.inp&root=0')
        check(st == 200 and d['stats']['elements'] == 1472, 'root=0 is the only workspace')
    finally:
        out = stop(p)
    check('root=%s' % ws in out, 'start-up line unchanged: %s' % out.strip())


def multi_root(server_dir, a, b, c, port):
    print('three workspaces (C does not exist yet)')
    p, cl = start(server_dir, [a, b, c, a], port)       # A given twice: counts once
    try:
        st, d = cl.get('/api/list')
        check(list(d) == ['root', 'roots', 'files', 'maxAutoMB'], 'answer has root / roots / files / maxAutoMB')
        check([(r['index'], r['name'], r['exists']) for r in d['roots']] == [(0, 'WS_A', True), (1, 'WS_B', True), (2, 'WS_C', False)],
              'roots: %s' % [(r['index'], r['name'], r['exists']) for r in d['roots']])
        got = [(f['root'], f['path'], f['solver']) for f in d['files']]
        want = [(1, 'only_b.inp', 'abaqus'), (1, 'job/m.inp', 'abaqus'), (0, 'job/m.inp', 'abaqus')]
        if os.path.isfile(os.path.join(server_dir, 'mapdl_model.py')):      # the CDB parser is there (step 2)
            want.insert(0, (1, 'cant/cant.cdb', 'mapdl'))
        check(got == want, 'files of A and B, newest first, each with root and solver: %s' % got)
        st, ma = cl.get('/api/model?path=job/m.inp&root=0')
        st2, mb = cl.get('/api/model?path=job/m.inp&root=1')
        check(ma['stats']['elements'] == 1472 and mb['stats']['elements'] != 1472,
              'same relative path, two workspaces, two models (%d / %d elements)' % (ma['stats']['elements'], mb['stats']['elements']))
        st, md = cl.get('/api/model?path=job/m.inp')
        check(md['stats']['elements'] == 1472, 'no root= means the first workspace')
        for bad in ('3', '-1', 'x', '1.5'):
            st, d = cl.get('/api/model?path=job/m.inp&root=' + bad)
            check(st == 500 and '无效的工作区序号' in d.get('error', ''), 'root=%s refused' % bad)
        st, d = cl.get('/api/model?path=../WS_B/only_b.inp&root=0')
        check(st == 500 and '无效的文件路径' in d.get('error', ''), 'a path that leaves its workspace is refused')
        st, h = cl.get('/api/highlight?path=job/m.inp&root=1&kind=material&name=x')
        check(st == 200 and 'n' in h, 'highlight with root=')
        st, s = cl.get('/api/symbols?path=job/m.inp&root=0&step=0')
        check(st == 200 and 'bc' in s, 'symbols with root=')
        st, hl = cl.get('/api/health')
        check(len(hl.get('roots', [])) == 3, 'health lists the workspaces')

        print('review decisions go to the workspace of the file')
        st, d = cl.post({'path': 'job/m.inp', 'root': 1, 'status': 'rejected', 'note': 'B 的密度', 'comments': []})
        check(st == 200 and d['state'] == 'rejected' and d['mdPath'] == '.abq/review/m.md', 'reject in B -> 200, %s' % d.get('mdPath'))
        check(os.path.isfile(os.path.join(b, '.abq', 'review.json')) and not os.path.exists(os.path.join(a, '.abq')),
              'written to B/.abq, nothing in A')
        e = json.load(open(os.path.join(b, '.abq', 'review.json'), encoding='utf-8'))['files']['job/m.inp']
        check(set(e) == {'sha256', 'status', 'decidedAt', 'note', 'comments'} and e['sha256'] == sha(os.path.join(b, 'job', 'm.inp')),
              'B entry: the five agreed keys, fingerprint of B\'s file')
        st, ra = cl.get('/api/review?path=job/m.inp&root=0')
        st, rb = cl.get('/api/review?path=job/m.inp&root=1')
        check(ra['state'] == 'none' and rb['state'] == 'rejected', 'A reads none, B reads rejected')
        st, d = cl.post({'path': 'job/m.inp', 'root': 7, 'status': 'approved', 'note': '', 'comments': []})
        check(st == 400 and '无效的工作区序号' in d.get('error', ''), 'POST with a bad workspace number -> 400')
        st, d = cl.post({'path': 'job/m.inp', 'status': 'approved', 'note': '', 'comments': []})
        check(st == 200 and os.path.isfile(os.path.join(a, '.abq', 'review.json')), 'POST without root -> first workspace (A)')
        os.makedirs(c)
        copy(BEAM, os.path.join(c, 'late.inp'), time.time())
        st, d = cl.get('/api/list')
        check(d['files'][0]['root'] == 2 and d['files'][0]['path'] == 'late.inp' and d['roots'][2]['exists'],
              'a workspace created while the panel runs shows up without a restart')
    finally:
        out = stop(p)
    check('roots=' in out and 'WS_C (not there yet)' in out, 'start-up line names the workspaces: %s' % out.strip())


def with_cdb_parser(server_dir, a, b, port, tmp):
    print('with a stand-in CDB parser (copy of the server + test-only mapdl_model.py)')
    sd = os.path.join(tmp, 'server_copy')
    shutil.copytree(server_dir, sd, ignore=shutil.ignore_patterns('tests', '__pycache__', 'mapdl_model.py'))
    with open(os.path.join(sd, 'mapdl_model.py'), 'w', encoding='utf-8') as f:
        f.write(STUB)
    p, cl = start(sd, [a, b], port)
    try:
        st, d = cl.get('/api/list')
        got = [(f['root'], f['path'], f['solver']) for f in d['files'] if f['solver'] == 'mapdl']
        check(got == [(1, 'cant/cant.cdb', 'mapdl')], 'the CDB is listed as mapdl; the command stream is still not: %s' % got)
        st, m = cl.get('/api/model?path=cant/cant.cdb&root=1')
        check(st == 200 and m.get('solver') == 'mapdl' and m['path'] == 'cant/cant.cdb', 'the CDB is served by mapdl_model')
        cdb = os.path.join(b, 'cant', 'cant.cdb')
        solve = os.path.join(b, 'cant', 'cant.solve.dat')
        st, r = cl.get('/api/review?path=cant/cant.cdb&root=1')
        check(r['state'] == 'none' and r['parts'] == {'cdb': sha(cdb), 'solve': None}, 'review state carries both digests (no solve segment yet)')
        write(solve, '/SOLU\nANTYPE,STATIC\nSOLVE\nFINISH\n')
        st, d = cl.post({'path': 'cant/cant.cdb', 'root': 1, 'status': 'approved', 'note': '', 'comments': []})
        want = hashlib.sha256((sha(cdb) + '\n' + sha(solve)).encode('ascii')).hexdigest()
        e = d.get('entry', {})
        check(st == 200 and e.get('sha256') == want and e.get('parts') == {'cdb': sha(cdb), 'solve': sha(solve)},
              'approval is bound to sha256(cdb digest + "\\n" + solve digest) and keeps both digests')
        check('CDB 和求解段' in d.get('markdown', '') and 'cant.solve.dat' in d.get('markdown', ''), 'the 审查意见单 names the CDB and the solve segment')
        st, r = cl.get('/api/review?path=cant/cant.cdb&root=1')
        check(r['state'] == 'approved' and r['current'] is True, 'reads back as approved')
        write(solve, '/SOLU\nANTYPE,STATIC\nNSUBST,10\nSOLVE\nFINISH\n')
        st, r = cl.get('/api/review?path=cant/cant.cdb&root=1')
        check(r['state'] == 'stale' and r.get('changed') == ['solve'], 'solve segment edited -> stale, changed = %s' % r.get('changed'))
        write(cdb, CDB + '\n')
        st, r = cl.get('/api/review?path=cant/cant.cdb&root=1')
        check(r['state'] == 'stale' and r.get('changed') == ['cdb', 'solve'], 'CDB edited too -> changed = %s' % r.get('changed'))
        st, d = cl.post({'path': 'cant/cant.cdb', 'root': 1, 'status': 'rejected', 'note': '改求解段', 'comments': []})
        check('ans build' in d.get('markdown', ''), 'a rejection tells to rebuild the CDB with ans build or edit the solve segment')
    finally:
        stop(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--port', type=int, default=8769)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    server_dir = os.path.abspath(a.server_dir)
    tmp = tempfile.mkdtemp(prefix='abq_preview_multi_')
    try:
        unit_tests(server_dir, tmp)
        wa, wb, wc = make_workspaces(tmp)
        single_root(server_dir, wa, a.port)
        multi_root(server_dir, wa, wb, wc, a.port)
        with_cdb_parser(server_dir, wa, wb, a.port, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
