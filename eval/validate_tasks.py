# -*- coding: utf-8 -*-
"""Validate the benchmark: every reference solution must PASS its checker, every negative control must FAIL it
(and fail it for the intended reason).

    py validate_tasks.py [--task ID[,ID..]] [--variant NAME[,NAME..]] [--reuse] [--no-retry] [--timeout S] [--wide]

For each task `eval/tasks/<id>/reference/variants.json` lists the variants of `reference/solve.py`
(selected through the environment variable REF_VARIANT):

    {"variants": [{"name": "default", "expect": "pass", "what": "..."},
                  {"name": "coarse",  "expect": "fail", "fails": ["hoop_stress_inner"], "what": "..."}]}

  expect = pass : the checker must report pass=true AND every single check (also the non-critical ones) must pass,
                  unless the variant lists the non-critical checks it is allowed to miss in "may_miss".
  expect = fail : the checker must report pass=false, `job_completed` must still pass (the control has to be a
                  finished analysis, not a crash) and every check named in "fails" must be among the failed
                  critical checks; checks named in the optional "passes" list must NOT fail.

Two synthetic controls are added per task from the finished default run (no extra solve):
  (no_result)  copy of the default work directory without result.json  -> must FAIL on `result_json` only
  (empty_dir)  a work directory with nothing but the task assets       -> must FAIL on `job_completed`, exit code 0

Task assets: the files in `eval/tasks/<id>/assets/` (input data a task hands to the agent, e.g. a ground-motion
record) are copied into the root of every work directory before the reference script starts - exactly what the
evaluation runner does for the agent.

Machine safety (this workstation also runs long solver jobs of its owner):
  * Abaqus is run strictly one process tree at a time - there is deliberately no parallel option.
  * Before every Abaqus launch (CAE, solver, `abaqus python` of the checker) the free commit memory is checked;
    below MIN_FREE_COMMIT_GB (6.0 GB, override with DSH_MIN_FREE_COMMIT_GB) the run waits, re-checking every 10 s,
    and the headroom must hold on two readings a few seconds apart.  A reading below the low-water mark
    (2.5 GB, DSH_LOW_WATER_GB) stops all launches for two minutes.  A launch that never gets headroom is
    reported as NOTRUN (a mismatch) - nothing is started.
  * Every reference job is created with memory = 2 GB and one CPU (otherwise pre.exe reserves ~5 GB).
  * A run whose analysis did not complete (memory / licence hiccup) is repeated ONCE after a 120 s pause
    (the queue stands still meanwhile).
  * On a timeout only the process tree started here is terminated.

Work directories: <repo>/.dsh-eval/validate/<task>/<variant>/ ; a machine-readable copy of the table is written to
<repo>/.dsh-eval/validate/summary.json (summary_partial_<timestamp>.json when --task / --variant restrict the run).
Exit code 1 on any mismatch.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(EVAL_DIR, '..'))
TASKS_DIR = os.path.join(EVAL_DIR, 'tasks')
WORK_ROOT = os.path.join(ROOT, '.dsh-eval', 'validate')
ABAQUS_BAT = os.environ.get('ABAQUS_BAT') or r'D:\SIMULIA\Commands\abaqus.bat'
GENERIC = ('job_completed', 'result_json')
MIN_FREE_COMMIT_GB = float(os.environ.get('DSH_MIN_FREE_COMMIT_GB', '6.0'))
LOW_WATER_GB = float(os.environ.get('DSH_LOW_WATER_GB', '2.5'))
LOW_WATER_PAUSE_S = 120
HEADROOM_MAX_WAIT_S = int(os.environ.get('DSH_HEADROOM_MAX_WAIT_S', '1800'))
HEADROOM_CONFIRM_S = float(os.environ.get('DSH_HEADROOM_CONFIRM_S', '3'))
NOT_RUN = 'NOTRUN'
RETRY_PAUSE_S = 120


class _MemStatus(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def free_commit_gb():
    """free system commit (same number as Win32_OperatingSystem.FreeVirtualMemory); None if it cannot be read"""
    try:
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPageFile / 1024.0 ** 3
    except Exception:
        return None


def wait_headroom(what):
    """Block until there is commit headroom for one more Abaqus launch -> True, or False after the maximum wait.

    * launch only at >= MIN_FREE_COMMIT_GB, confirmed by a second reading HEADROOM_CONFIRM_S later (so that a short
      gap between two memory-hungry jobs of somebody else is not mistaken for free memory);
    * a reading below LOW_WATER_GB stops everything for LOW_WATER_PAUSE_S; two good readings are needed to resume.
    """
    t0 = time.time()
    while True:
        g = free_commit_gb()
        if g is None:
            return True
        if g < LOW_WATER_GB:
            sys.stderr.write('    free commit %.1f GB < %.1f GB (low-water mark): pausing %d s before %s\n' % (
                g, LOW_WATER_GB, LOW_WATER_PAUSE_S, what))
            sys.stderr.flush()
            time.sleep(LOW_WATER_PAUSE_S)
        elif g >= MIN_FREE_COMMIT_GB:
            time.sleep(HEADROOM_CONFIRM_S)
            g = free_commit_gb()
            if g is None or g >= MIN_FREE_COMMIT_GB:
                return True
        else:
            sys.stderr.write('    free commit %.1f GB < %.1f GB, waiting before %s\n' % (g, MIN_FREE_COMMIT_GB, what))
            sys.stderr.flush()
            time.sleep(10)
        if time.time() - t0 > HEADROOM_MAX_WAIT_S:
            return False


def kill_tree(pid):
    """terminate only the process tree this script started"""
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean_dir(path):
    if os.path.isdir(path):
        for _ in range(5):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.isdir(path):
                break
            time.sleep(1.0)
    os.makedirs(path, exist_ok=True)


def copy_assets(task_dir, workdir):
    """copy the task's input files (eval/tasks/<id>/assets/*) into the root of the work directory"""
    src = os.path.join(task_dir, 'assets')
    if not os.path.isdir(src):
        return
    for fn in sorted(os.listdir(src)):
        p = os.path.join(src, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(workdir, fn))
        elif os.path.isdir(p):
            shutil.copytree(p, os.path.join(workdir, fn), dirs_exist_ok=True)


def run_solve(task_dir, variant, workdir, timeout):
    """abaqus cae noGUI=reference/solve.py with REF_VARIANT=<variant>, cwd = workdir -> (seconds, note)"""
    solve = os.path.join(task_dir, 'reference', 'solve.py')
    copy_assets(task_dir, workdir)
    env = dict(os.environ)
    env['REF_VARIANT'] = variant
    cmd = '%s /d /s /c ""%s" cae noGUI="%s""' % (os.environ.get('COMSPEC', 'cmd.exe'), ABAQUS_BAT, solve)
    if not wait_headroom('solving %s' % variant):
        return 0.0, 'not solved: free commit memory stayed below %.0f GB' % MIN_FREE_COMMIT_GB
    t0 = time.time()
    note = ''
    with open(os.path.join(workdir, '_solve.log'), 'wb') as log:
        p = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL)
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            note = 'solve timed out after %d s' % timeout
    return time.time() - t0, note


def run_check(task_dir, workdir):
    """-> (verdict dict or None, exit code, raw stdout, seconds); exit code NOT_RUN when there was no headroom"""
    has_odb = os.path.isdir(workdir) and any(f.lower().endswith('.odb') for f in os.listdir(workdir))
    if has_odb and not wait_headroom('checking'):       # the checker starts `abaqus python` only when an ODB exists
        return None, NOT_RUN, 'not checked: free commit memory stayed below %.0f GB' % MIN_FREE_COMMIT_GB, 0.0
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(task_dir, 'check.py'), workdir], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    secs = time.time() - t0
    raw = p.stdout.decode('utf-8', 'replace')
    try:
        return json.loads(raw), p.returncode, raw, secs
    except ValueError:
        return None, p.returncode, raw + p.stderr.decode('utf-8', 'replace'), secs


def fmt(x):
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, (int, float)):
        return '%.5g' % x
    if isinstance(x, list):
        return '[' + ', '.join(fmt(v) for v in x) + ']'
    if isinstance(x, dict):
        return '{' + ', '.join('%s: %s' % (k, fmt(v)) for k, v in x.items()) + '}'
    return str(x)


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def key_numbers(verdict, only_failed=False):
    """compact 'name=got (expected, error)' list of the physics checks"""
    parts = []
    for c in verdict.get('checks', []):
        generic = c['name'] in GENERIC or c['name'].startswith('report_')
        if only_failed:
            if c['pass']:
                continue
        elif generic:
            continue
        s = '%s=%s' % (c['name'], fmt(c['got']))
        exp = c.get('expected')
        if c.get('tol') == 'upper bound' and is_num(exp):
            s += ' (limit %s)' % fmt(exp)
        elif is_num(exp) and is_num(c['got']) and exp:
            if c['name'].startswith('report_'):
                err = (abs(c['got']) - abs(exp)) / abs(exp)
            else:
                err = (c['got'] - exp) / abs(exp)
            s += ' (exp %s, %+.2f%%)' % (fmt(exp), 100.0 * err)
        elif isinstance(exp, list) and exp and all(is_num(v) for v in exp):
            s += ' (%s %s)' % (c.get('tol') or 'exp', fmt(exp))
        if not c['pass']:
            s += ' FAIL'
        parts.append(s)
    return '; '.join(parts)


def judge(spec, verdict, code):
    """-> (ok, actual, problem)"""
    if code == NOT_RUN:
        return False, 'NOTRUN', 'no Abaqus launch: free commit memory stayed below %.0f GB' % MIN_FREE_COMMIT_GB
    if verdict is None:
        return False, 'CRASH', 'checker printed no JSON (exit %s)' % code
    if code != 0:
        return False, 'CRASH', 'checker exit code %s: %s' % (code, verdict.get('error'))
    actual = 'pass' if verdict.get('pass') else 'fail'
    checks = verdict.get('checks', [])
    failed = [c['name'] for c in checks if not c['pass']]
    failed_crit = [c['name'] for c in checks if not c['pass'] and c['critical']]
    if spec['expect'] == 'pass':
        if actual != 'pass':
            return False, actual, 'expected PASS; failed critical checks: %s' % ', '.join(failed_crit)
        extra = [n for n in failed if n not in spec.get('may_miss', [])]
        if extra:
            return False, actual, 'reference misses non-critical checks: %s' % ', '.join(extra)
        return True, actual, ''
    if actual != 'fail':
        return False, actual, 'expected FAIL but the checker passed it'
    want = spec.get('fails', [])
    missing = [n for n in want if n not in failed_crit]
    if missing:
        return False, actual, 'failed, but not on the intended critical check(s) %s (failed: %s)' % (
            ', '.join(missing), ', '.join(failed_crit))
    if 'job_completed' in failed_crit and 'job_completed' not in want:
        return False, actual, 'the control did not even complete the analysis (job_completed failed)'
    wrongly = [n for n in spec.get('passes', []) if n in failed]
    if wrongly:
        return False, actual, 'check(s) that this control must still pass have failed: %s' % ', '.join(wrongly)
    return True, actual, ''


def row(task, spec, actual, ok, problem, solve_s, check_s, verdict, raw, only_failed=False):
    return {'task': task, 'variant': spec['name'], 'expect': spec['expect'], 'actual': actual, 'ok': ok,
            'problem': problem, 'solve_s': round(solve_s, 1), 'check_s': round(check_s, 1),
            'what': spec.get('what', ''),
            'numbers': key_numbers(verdict, only_failed) if verdict else raw[-300:],
            'failed': [c['name'] for c in verdict.get('checks', []) if not c['pass']] if verdict else [],
            'score': verdict.get('score') if verdict else None, 'verdict': verdict}


def do_variant(task, task_dir, spec, args):
    name = spec['name']
    workdir = os.path.join(WORK_ROOT, task, name)
    odb_there = any(f.lower().endswith('.odb') for f in os.listdir(workdir)) if os.path.isdir(workdir) else False
    secs, note, solved = 0.0, '', False
    for attempt in (1, 2):
        if not (args.reuse and odb_there and attempt == 1):
            clean_dir(workdir)
            secs, note = run_solve(task_dir, name, workdir, args.timeout)
            solved = True
            if note.startswith('not solved'):
                return row(task, spec, 'NOTRUN', False, note, 0.0, 0.0, None, note)
        verdict, code, raw, check_secs = run_check(task_dir, workdir)
        ok, actual, problem = judge(spec, verdict, code)
        # An Abaqus run that died for reasons unrelated to the model (memory / licence hiccup) is repeated once,
        # after a pause, before it is reported.
        died = verdict is not None and any(c['name'] == 'job_completed' and not c['pass'] for c in verdict['checks'])
        if (ok or not died or 'job_completed' in spec.get('fails', []) or attempt == 2 or 'timed out' in note
                or args.no_retry):
            break
        sys.stderr.write('    %s / %s: analysis did not complete, retrying once in %d s\n' % (task, name, RETRY_PAUSE_S))
        sys.stderr.flush()
        time.sleep(RETRY_PAUSE_S)
        note = ''
    if note:
        problem = (problem + '; ' if problem else '') + note
    if not solved:
        problem = (problem + '; ' if problem else '') + 'note: reused the existing ODB (--reuse)'
    return row(task, spec, actual, ok, problem, secs, check_secs, verdict, raw)


def synthetic(task, task_dir):
    """controls that need no solve: (no_result) and (empty_dir)"""
    rows = []
    src = os.path.join(WORK_ROOT, task, 'default')
    for name, want in (('(no_result)', 'result_json'), ('(empty_dir)', 'job_completed')):
        workdir = os.path.join(WORK_ROOT, task, name.strip('()') + '_synthetic')
        clean_dir(workdir)
        copy_assets(task_dir, workdir)
        if name == '(no_result)':
            if not os.path.isfile(os.path.join(src, 'result.json')):
                continue
            for fn in os.listdir(src):
                p = os.path.join(src, fn)
                if os.path.isfile(p) and fn.lower() != 'result.json' and not fn.lower().endswith('.lck'):
                    shutil.copy2(p, os.path.join(workdir, fn))
        spec = {'name': name, 'expect': 'fail', 'fails': [want], 'what': 'synthetic control'}
        verdict, code, raw, check_secs = run_check(task_dir, workdir)
        ok, actual, problem = judge(spec, verdict, code)
        if ok and name == '(no_result)':
            others = [c['name'] for c in verdict['checks'] if not c['pass'] and c['name'] != 'result_json'
                      and not c['name'].startswith('report_')]
            if others:
                ok, problem = False, 'checks unrelated to result.json failed: %s' % ', '.join(others)
        rows.append(row(task, spec, actual, ok, problem, 0.0, check_secs, verdict, raw, only_failed=True))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--task', help='comma separated task ids (prefix match), default: all')
    ap.add_argument('--variant', help='comma separated variant names, default: all')
    ap.add_argument('--reuse', action='store_true', help='do not re-solve when the work directory already has an ODB')
    ap.add_argument('--timeout', type=int, default=900, help='per-solve timeout in seconds')
    ap.add_argument('--wide', action='store_true', help='print the full key-number column')
    ap.add_argument('--no-retry', action='store_true',
                    help='do not repeat a run whose analysis did not complete (useful while debugging a reference)')
    args = ap.parse_args()

    tasks = sorted(d for d in os.listdir(TASKS_DIR) if os.path.isfile(os.path.join(TASKS_DIR, d, 'check.py')))
    if args.task:
        want = [t.strip() for t in args.task.split(',') if t.strip()]
        tasks = [t for t in tasks if any(t == w or t.startswith(w) for w in want)]
    only = [v.strip() for v in args.variant.split(',')] if args.variant else None

    work = []
    for t in tasks:
        task_dir = os.path.join(TASKS_DIR, t)
        with open(os.path.join(task_dir, 'reference', 'variants.json'), encoding='utf-8') as f:
            specs = json.load(f)['variants']
        for spec in specs:
            if only and spec['name'] not in only:
                continue
            work.append((t, task_dir, spec))

    t_start = time.time()
    rows = []
    for i, (t, d, s) in enumerate(work):          # strictly sequential: one Abaqus process tree at a time
        r = do_variant(t, d, s, args)
        rows.append(r)
        sys.stderr.write('[%d/%d] %s / %s -> %s (%s) %.0f s\n' % (
            i + 1, len(work), r['task'], r['variant'], r['actual'], 'ok' if r['ok'] else 'MISMATCH',
            r['solve_s'] + r['check_s']))
        sys.stderr.flush()
    if not only:
        for t in tasks:
            rows.extend(synthetic(t, os.path.join(TASKS_DIR, t)))
    rows.sort(key=lambda r: tasks.index(r['task']))      # stable: variants in file order, synthetic controls last

    width = 10 ** 6 if args.wide else 150
    head = '%-22s %-18s %-6s %-6s %-3s %7s  %s' % ('task', 'variant', 'expect', 'actual', 'ok', 'time_s', 'key numbers')
    print(head)
    print('-' * 170)
    last = None
    for r in rows:
        if last is not None and r['task'] != last:
            print()
        last = r['task']
        nums = r['numbers'] if r['expect'] == 'pass' else ('failed: ' + ', '.join(r['failed']) + ' | ' + r['numbers'])
        if len(nums) > width:
            nums = nums[:width - 3] + '...'
        print('%-22s %-18s %-6s %-6s %-3s %7.1f  %s' % (r['task'], r['variant'], r['expect'].upper(),
                                                       r['actual'].upper(), 'ok' if r['ok'] else '!!',
                                                       r['solve_s'] + r['check_s'], nums))
        if r['problem']:
            print('%-22s   -> %s' % ('', r['problem']))
    bad = [r for r in rows if not r['ok']]
    print()
    print('%d rows, %d mismatches, wall time %.0f s' % (len(rows), len(bad), time.time() - t_start))
    try:
        os.makedirs(WORK_ROOT, exist_ok=True)
        name = 'summary.json' if not (args.task or args.variant) else 'summary_partial_%s.json' % time.strftime(
            '%Y%m%d_%H%M%S')
        with open(os.path.join(WORK_ROOT, name), 'w', encoding='utf-8') as f:
            json.dump({'rows': rows, 'mismatches': len(bad)}, f, ensure_ascii=False, indent=1)
    except Exception as e:
        sys.stderr.write('could not write the summary: %s\n' % e)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
