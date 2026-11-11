# -*- coding: utf-8 -*-
"""Run one DeepSeek Harness headless task in an isolated DSH home and capture everything.

    py dshrun.py --cwd <workdir> --out <outdir> [--mode workspace-write] [--timeout 900] --task-file task.md
    py dshrun.py --cwd <workdir> --out <outdir> "say hi"

The API key is read at launch from the user's own ~/.dsh/.credentials.yaml and passed to the child
process through its environment only; it is never written anywhere.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DSH_EXE_DIR = r'D:\DSH'
DSH_CMD = os.path.join(DSH_EXE_DIR, r'resources\runtime\cli\bin\dsh.cmd')
EVAL_HOME = os.environ.get('DSH_EVAL_HOME') or os.path.join(ROOT, '.dsh-eval', 'home')
# user-level skills (~/.agents/skills) are kept out of benchmark runs; point DSH_EVAL_AGENTS_HOME at a copy of the user's
# folder to test with that catalogue present
AGENTS_HOME = os.environ.get('DSH_EVAL_AGENTS_HOME') or os.path.join(ROOT, '.dsh-eval', 'agents-home')


def load_key():
    key = os.environ.get('DEEPSEEK_API_KEY')
    if key:
        return key
    p = os.path.join(os.path.expanduser('~'), '.dsh', '.credentials.yaml')
    with open(p, encoding='utf-8') as f:
        m = re.search(r'^\s*DEEPSEEK_API_KEY:\s*["\']?([^\s"\']+)', f.read(), re.M)
    if not m:
        raise SystemExit('DEEPSEEK_API_KEY not found in %s' % p)
    return m.group(1)


def child_env(extra=None):
    env = dict(os.environ)
    env['DSH_HOME'] = EVAL_HOME
    env['DSH_AGENTS_HOME'] = AGENTS_HOME
    env['DEEPSEEK_API_KEY'] = load_key()
    env['DSH_TELEMETRY_MODE'] = 'DISABLED'
    env['PYTHONUTF8'] = '1'
    env['ABQ_LOW_PRIORITY'] = '1'
    env.pop('ELECTRON_RUN_AS_NODE', None)
    if extra:
        env.update(extra)
    return env


def headroom_gb():
    """available commit charge in GB (None if unreadable)"""
    try:
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
        return st.ullAvailPageFile / float(1 << 30)
    except Exception:
        return None


def wait_for_headroom(need=None, max_wait=1800):
    """Benchmark runs yield to real work: do not start a run while the machine is short of commit memory."""
    need = float(os.environ.get('EVAL_MIN_FREE_GB', '3.5')) if need is None else need
    t0 = time.time()
    while True:
        free = headroom_gb()
        if free is None or free >= need:
            return free
        if time.time() - t0 > max_wait:
            raise RuntimeError('only %.1f GB of commit memory available (need %.0f): not starting a benchmark run' % (free, need))
        time.sleep(20)


def kill_tree(pid):
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def summarize(events_path):
    """-> dict with final text, reason, tool-call counts, session id."""
    info = {'sessionId': None, 'final': '', 'reason': None, 'tools': {}, 'nToolCalls': 0, 'nEvents': 0,
            'toolErrors': 0, 'error': None, 'steps': 0, 'abq': {}, 'rawAbaqus': 0, 'skills': [], 'lowMemory': 0,
            'usage': {'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0}}
    if not os.path.isfile(events_path):
        return info
    with open(events_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            info['nEvents'] += 1
            t = ev.get('type')
            if t == 'session':
                info['sessionId'] = ev.get('sessionId')
            elif t == 'status':
                if ev.get('phase') == 'step_end':
                    info['steps'] += 1
                    u = ev.get('usage') or {}
                    for k in info['usage']:
                        info['usage'][k] += int(u.get(k) or 0)
            elif t == 'tool_call':
                name = ev.get('name') or ev.get('tool') or '?'
                info['tools'][name] = info['tools'].get(name, 0) + 1
                info['nToolCalls'] += 1
                inp = ev.get('input') or {}
                cmd = str(inp.get('command') or '') if isinstance(inp, dict) else ''
                m = re.search(r'abq(?:\.cmd|\.py)?["\']?\s+([a-z][a-z-]*)', cmd, re.I)
                if m and 'abq' in cmd.lower():
                    sub = m.group(1).lower()
                    info['abq'][sub] = info['abq'].get(sub, 0) + 1
                elif re.search(r'(^|[\s;&|(])abaqus(\.bat)?\s', cmd, re.I):
                    info['rawAbaqus'] += 1
                if name == 'skill' and isinstance(inp, dict):
                    info['skills'].append(str(inp.get('name') or inp.get('skill') or inp)[:80])
            elif t == 'tool_result':
                if ev.get('error') or ev.get('status') not in (None, 'completed'):
                    info['toolErrors'] += 1
                if '\u23f8' in str(ev.get('result') or ''):
                    info['lowMemory'] += 1      # the abq tool paused for lack of memory: not a fair sample
            elif t == 'error':
                info['error'] = ev.get('message')
            elif t == 'final':
                info['final'] = ev.get('text') or ev.get('answer') or ev.get('message') or ''
                info['reason'] = ev.get('reason')
                info['sessionId'] = ev.get('sessionId') or info['sessionId']
    return info


def event_times(out):
    """-> list of (seconds since start, event dict) for a finished run, from events.jsonl + events.times
    (empty when the run was recorded before time stamps were kept). Resolution: about half a second."""
    tp, ep = os.path.join(out, 'events.times'), os.path.join(out, 'events.jsonl')
    if not (os.path.isfile(tp) and os.path.isfile(ep)):
        return []
    marks = []
    with open(tp, encoding='ascii', errors='replace') as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2:
                marks.append((float(parts[0]), int(parts[1])))
    res = []
    pos = 0
    i = 0
    with open(ep, 'rb') as f:
        for raw in f:
            pos += len(raw)                      # the event is complete once the file has reached this size
            while i < len(marks) - 1 and marks[i][1] < pos:
                i += 1
            line = raw.decode('utf-8', errors='replace').strip()
            if not line.startswith('{'):
                continue
            try:
                res.append((marks[i][0] if marks else 0.0, json.loads(line)))
            except ValueError:
                continue
    return res


def run(task, cwd, out, mode='workspace-write', timeout=900, patches=(), add_dirs=(), session_id=None, extra_env=None):
    os.makedirs(out, exist_ok=True)
    wait_for_headroom()
    os.makedirs(EVAL_HOME, exist_ok=True)
    os.makedirs(AGENTS_HOME, exist_ok=True)
    args = ['cmd.exe', '/d', '/c', DSH_CMD, 'headless']
    for p in patches:
        args += ['--patch', p]
    args += ['--json', '--permission-mode', mode, '--approval-policy', 'never']
    for d in add_dirs:
        args += ['--add-dir', d]
    if session_id:
        args += ['--session-id', session_id]
    args.append('-')
    ev_path = os.path.join(out, 'events.jsonl')
    err_path = os.path.join(out, 'stderr.txt')
    t0 = time.time()
    timed_out = False
    with open(ev_path, 'wb') as fo, open(err_path, 'wb') as fe:
        # BELOW_NORMAL priority is inherited by the whole tree (harness, shells, Abaqus): benchmark runs must not
        # compete with whatever real solver jobs the machine is busy with
        p = subprocess.Popen(args, cwd=cwd, env=child_env(extra_env), stdin=subprocess.PIPE, stdout=fo, stderr=fe,
                             creationflags=0x00004000)
        try:
            p.stdin.write(task.encode('utf-8'))
            p.stdin.close()
        except OSError:
            pass
        fired = threading.Event()

        def on_timeout():
            fired.set()                 # set before killing: p.wait() returns as soon as the process dies
            kill_tree(p.pid)

        timer = threading.Timer(timeout, on_timeout)
        timer.start()
        done = threading.Event()

        def clock():
            # the harness prints events without time stamps: note when the event file grows (seconds since start,
            # file size), so that step durations can be worked out afterwards (see event_times)
            last = -1
            with open(os.path.join(out, 'events.times'), 'w', encoding='ascii') as ft:
                while True:
                    try:
                        sz = os.path.getsize(ev_path)
                    except OSError:
                        sz = last
                    if sz != last:
                        ft.write('%.1f %d\n' % (time.time() - t0, sz))
                        ft.flush()
                        last = sz
                    if done.wait(0.5):
                        break

        tc = threading.Thread(target=clock, daemon=True)
        tc.start()
        try:
            p.wait()
        finally:
            timer.cancel()
            timed_out = fired.is_set()
            done.set()
            tc.join(timeout=3)
    info = summarize(ev_path)
    info.update({'exitCode': p.returncode, 'seconds': round(time.time() - t0, 1), 'timedOut': timed_out,
                 'mode': mode, 'cwd': cwd})
    with open(os.path.join(out, 'final.txt'), 'w', encoding='utf-8') as f:
        f.write(info['final'] or '')
    with open(os.path.join(out, 'run.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('task', nargs='*')
    ap.add_argument('--task-file')
    ap.add_argument('--cwd', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', default='workspace-write')
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--patch', action='append', default=[])
    ap.add_argument('--add-dir', action='append', default=[])
    ap.add_argument('--session-id')
    a = ap.parse_args()
    task = open(a.task_file, encoding='utf-8').read() if a.task_file else ' '.join(a.task)
    if not task.strip():
        raise SystemExit('empty task')
    info = run(task, os.path.abspath(a.cwd), os.path.abspath(a.out), a.mode, a.timeout, a.patch, a.add_dir, a.session_id)
    short = dict(info)
    short['final'] = (info['final'] or '')[:1500]
    print(json.dumps(short, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
