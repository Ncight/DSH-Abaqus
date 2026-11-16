# -*- coding: utf-8 -*-
"""The gate of the ANSYS toolbox, end to end (ANSYS plan, section 5, 闸门).

    py test_ans_gate.py [--port 8777]

A temp workspace laid out like E:\\DSH-Ansys (gate on in .abq/config.json, ans.cmd at its root), the job beam
(tests/data/cant.build.mac + cant_static1.solve.dat) and the preview server of this repository on a test port.
Approval and rejection are sent to the panel exactly as the page sends them (POST /api/review).
  1. ans build -> CDB + build record; ans run refused: not reviewed
  2. approved in the panel -> ans run solves, ans verify passes
  3. the solve segment changed -> the panel says stale, ans run refused (names the solve segment)
  4. the solve segment as approved again -> ans run solves
  5. the CDB edited by hand -> ans run refused (not the CDB of the last build)
  6. a command outside the whitelist in the solve segment -> ans check and ans run refuse with its line
  7. SOLVE in the build commands -> ans build refuses before MAPDL starts; SOLVE hidden in a file read with
     /INPUT (--with) -> MAPDL builds, the cumulative load step count is 1, no CDB
  8. rejected in the panel with a note -> ans run refused and prints the note
  9. `ans gate off` from an agent (DSH_SESSION_ID set) -> refused, the gate stays on
 10. the plugin: node test_guard.mjs (direct ANSYS251.exe / mapdl / runwb2 calls, ans gate off, .abq records)
Needs MAPDL (one session at a time) and node.  Exit code 1 on failure.
"""
import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ANS = os.path.dirname(HERE)
TOOLS = os.path.dirname(ANS)
REPO = os.path.dirname(TOOLS)
DATA = os.path.join(HERE, 'data')
FAILS = []


def check(cond, what):
    print('  %s %s' % ('ok  ' if cond else 'FAIL', what), flush=True)
    if not cond:
        FAILS.append(what)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8777)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ws = tempfile.mkdtemp(prefix='ans_gate_')
    os.makedirs(os.path.join(ws, '.abq'))
    with open(os.path.join(ws, '.abq', 'config.json'), 'w') as f:
        f.write('{"gate": "human"}')
    with open(os.path.join(ws, 'ans.cmd'), 'w') as f:
        f.write('@py "%s" %%*\n' % os.path.join(ANS, 'ans.py'))
    job = os.path.join(ws, 'beam')
    os.makedirs(job)
    shutil.copy2(os.path.join(DATA, 'cant.build.mac'), os.path.join(job, 'beam.build.mac'))
    shutil.copy2(os.path.join(DATA, 'cant_static1.solve.dat'), os.path.join(job, 'beam.solve.dat'))
    clean = dict(os.environ)
    for k in ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL', 'ABQ_GATE'):
        clean.pop(k, None)

    def ans(*args, env=None):
        p = subprocess.run([sys.executable, '-B', os.path.join(ANS, 'ans.py')] + list(args), cwd=job, env=env or clean,
                           capture_output=True, timeout=3600)
        return p.returncode, p.stdout.decode('utf-8', 'replace')

    srv = subprocess.Popen([sys.executable, os.path.join(TOOLS, 'preview', 'preview_server.py'), '--root', ws,
                            '--port', str(a.port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def req(method, path, body=None):
        for _ in range(100):
            try:
                cn = http.client.HTTPConnection('127.0.0.1', a.port, timeout=120)
                hdr = {'Host': '127.0.0.1:%d' % a.port}
                data = None
                if body is not None:
                    data = json.dumps(body).encode('utf-8')
                    hdr['Content-Type'] = 'application/json'
                cn.request(method, path, body=data, headers=hdr)
                r = cn.getresponse()
                return r.status, json.loads(r.read().decode('utf-8'))
            except (ConnectionError, OSError):
                time.sleep(0.1)
        return None, None

    def decide(status, note=''):
        return req('POST', '/api/review', {'path': 'beam/beam.cdb', 'status': status, 'note': note, 'comments': []})
    try:
        print('1. build, run before any review')
        rc, out = ans('build', 'beam')
        check(rc == 0 and os.path.isfile(os.path.join(job, 'beam.cdb')), 'ans build -> beam.cdb (%d)' % rc)
        rec = json.load(open(os.path.join(ws, '.abq', 'builds.json'), encoding='utf-8'))
        check('beam/beam.cdb' in rec['files'], 'build record in .abq/builds.json')
        rc, out = ans('run', 'beam')
        check(rc == 3 and '还没有被审查过' in out, 'ans run refused: not reviewed (%d)' % rc)
        _, lst = req('GET', '/api/list')
        check([f['path'] for f in lst['files']] == ['beam/beam.cdb'], 'the panel lists beam/beam.cdb only (.ans is skipped): %s'
              % [f['path'] for f in lst['files']])

        print('2. approved in the panel')
        st, r = decide('approved')
        check(st == 200 and r.get('state') == 'approved', 'POST /api/review approved')
        rc, out = ans('run', 'beam', '--comp', 'NFIX')
        check(rc == 0 and '✓ 求解完成' in out and 'FZ 100' in out, 'ans run solves; reaction FZ 100 (%d)' % rc)
        rc, out = ans('verify', 'beam')
        check(rc == 0 and '已批准，就是求解的这一份' in out, 'ans verify passes')

        print('3. the solve segment changed after the approval')
        sp = os.path.join(job, 'beam.solve.dat')
        orig = open(sp, 'rb').read()
        with open(sp, 'ab') as f:
            f.write(b'! one more comment\n')
        _, rv = req('GET', '/api/review?path=beam/beam.cdb')
        check(rv['state'] == 'stale' and rv.get('changed') == ['solve'], 'panel: stale, changed %s' % rv.get('changed'))
        rc, out = ans('run', 'beam')
        check(rc == 3 and '求解段改过了' in out, 'ans run refused, names the solve segment (%d)' % rc)
        rc, out = ans('verify', 'beam')
        check(rc == 1 and '结果已过期' in out, 'ans verify: the results no longer match the files')

        print('4. the solve segment as approved again')
        with open(sp, 'wb') as f:
            f.write(orig)
        rc, out = ans('run', 'beam')
        check(rc == 0, 'ans run solves again (%d)' % rc)

        print('5. the CDB edited by hand')
        cp = os.path.join(job, 'beam.cdb')
        cdb = open(cp, 'rb').read()
        i = cdb.index(b'MPDATA')
        with open(cp, 'wb') as f:
            f.write(cdb[:i] + b'/COM, edited\n' + cdb[i:])
        rc, out = ans('run', 'beam')
        check(rc == 2 and '不是最近一次 ans build 产出的那一份' in out, 'ans run refused: not the CDB of the last build (%d)' % rc)
        with open(cp, 'wb') as f:
            f.write(cdb)

        print('6. a command outside the whitelist in the solve segment')
        with open(sp, 'wb') as f:
            f.write(orig.replace(b'SOLVE', b'/SYS,calc.exe\r\nSOLVE', 1) if b'\r\n' in orig else orig.replace(b'SOLVE', b'/SYS,calc.exe\nSOLVE', 1))
        rc, out = ans('check', 'beam')
        check(rc == 1 and '第 5 行: /SYS 不允许' in out, 'ans check: line 5 /SYS refused (%d)' % rc)
        rc, out = ans('run', 'beam')
        check(rc == 2 and '求解段第 5 行: /SYS 不允许' in out, 'ans run refused before MAPDL starts (%d)' % rc)
        with open(sp, 'wb') as f:
            f.write(orig)

        print('7. SOLVE in the build commands')
        mp = os.path.join(job, 'beam.build.mac')
        mac = open(mp, 'rb').read()
        with open(mp, 'wb') as f:
            f.write(mac.rstrip() + b'\n/SOLU\nsolve\nFINISH\n')
        rc, out = ans('build', 'beam')
        check(rc == 3 and '有求解命令' in out and not os.path.isfile(cp), 'plain SOLVE: refused before MAPDL, old CDB removed (%d)' % rc)
        with open(os.path.join(job, 'hid.inp'), 'w') as f:
            f.write('/SOLU\nSOLVE\nFINISH\n')
        with open(mp, 'wb') as f:
            f.write(mac.rstrip() + b'\nFINISH\n/INPUT,hid,inp\n')
        rc, out = ans('build', 'beam', '--with', 'hid.inp')
        check(rc == 3 and '建模过程中求解过（累计载荷步数 1' in out and not os.path.isfile(cp),
              'SOLVE hidden in a file read with /INPUT: found after the build (load steps 1), no CDB (%d)' % rc)
        with open(mp, 'wb') as f:
            f.write(mac)
        rc, out = ans('build', 'beam')
        check(rc == 0 and os.path.isfile(cp), 'rebuilt (%d)' % rc)

        print('8. rejected in the panel')
        st, r = decide('rejected', '载荷改成每节点 -8 N')
        rc, out = ans('run', 'beam')
        check(rc == 3 and '已退回' in out and '载荷改成每节点 -8 N' in out, 'ans run refused and shows the note (%d)' % rc)

        print('9. the agent cannot switch the gate off')
        env = dict(clean, DSH_SESSION_ID='test')
        rc, out = ans('gate', 'off', env=env)
        check(rc == 3, 'ans gate off from an agent: refused (%d)' % rc)
        cfg = json.load(open(os.path.join(ws, '.abq', 'config.json'), encoding='utf-8'))
        check(cfg.get('gate') == 'human', 'the gate is still on')
        env = dict(clean, DSH_SESSION_ID='test', ABQ_GATE='off')
        rc, out = ans('run', 'beam', env=env)
        check(rc == 3, 'ABQ_GATE=off in an agent command changes nothing (%d)' % rc)

        print('10. the plugin')
        p = subprocess.run(['node', 'test_guard.mjs'], cwd=os.path.join(REPO, 'dsh-plugin', 'abq-gate'), capture_output=True)
        txt = p.stdout.decode('utf-8', 'replace').strip()
        check(p.returncode == 0, 'node test_guard.mjs: %s' % txt)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(ws, ignore_errors=True)
    print('\n%s' % ('ALL OK' if not FAILS else '%d FAILED: %s' % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
