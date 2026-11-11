# -*- coding: utf-8 -*-
"""Batch-evaluate DeepSeek Harness + Flash on the Abaqus benchmark.

    py run_eval.py --variant live --split tune --repeats 3 --parallel 4 --tag v1
    py run_eval.py --variant baseline --tasks t01_cantilever_static,t03_modal_cantilever --repeats 1 --tag smoke

A variant is what the agent finds in its working directory besides the task:
    baseline            nothing (bare harness: no AGENTS.md, no skills; the agent must drive `abaqus` itself)
    live                this repository's own AGENTS.md + .dsh/skills (what the interactive workspace uses)
    <dir>               any directory laid out like the repository root (AGENTS.md, .dsh/skills/...)
Each run happens in its own fresh directory with its own .git marker, so nothing leaks between variants or runs.
Results: .dsh-eval/runs/<tag>/results.json and report.md
"""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import dshrun  # noqa: E402

TASKS = os.path.join(HERE, 'tasks')
RUNS = os.path.join(ROOT, '.dsh-eval', 'runs')


SUITES = {'t': 'basic', 'h': 'basic', 'x': 'hard', 'd': 'domain'}     # default suite by the first letter of the task id


def load_tasks(names=None, split='all', suite='all'):
    """tasks by explicit id list, or by split (tune / holdout) and suite (basic / hard / domain).
    A task whose meta.json says "draft": true is only run when named explicitly."""
    out = []
    for d in sorted(os.listdir(TASKS)):
        mp = os.path.join(TASKS, d, 'meta.json')
        if not os.path.isfile(mp):
            continue
        with open(mp, encoding='utf-8') as f:
            meta = json.load(f)
        meta['dir'] = os.path.join(TASKS, d)
        meta.setdefault('id', d)
        meta.setdefault('suite', SUITES.get(meta['id'][:1], 'basic'))
        if names:
            if meta['id'] not in names:
                continue
        else:
            if meta.get('draft'):
                continue
            if split != 'all' and meta.get('split') != split:
                continue
            if suite != 'all' and meta['suite'] not in suite.split(','):
                continue
        out.append(meta)
    return out


def variant_dir(name):
    if name == 'baseline':
        return None
    if name == 'live':
        return ROOT
    p = os.path.abspath(name)
    if not os.path.isdir(p):
        p = os.path.join(HERE, 'variants', name)
    if not os.path.isdir(p):
        raise SystemExit('variant not found: %s' % name)
    return p


def materialize(vdir, work, task=None):
    os.makedirs(work, exist_ok=True)
    subprocess.run(['git', 'init', '-q'], cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.path.join(work, 'abaqus_v6.env'), 'w', encoding='ascii') as f:
        f.write('# machine policy for benchmark runs: cap solver memory (default is 90% of RAM)\n'
                'memory = "2 gb"\n')
    assets = os.path.join(task['dir'], 'assets') if task else ''
    if assets and os.path.isdir(assets):                      # input files the task hands to the agent (e.g. a ground motion)
        shutil.copytree(assets, work, dirs_exist_ok=True)
    if vdir is None:
        return
    for rel in ('AGENTS.md',):
        src = os.path.join(vdir, rel)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(work, rel))
    sk = os.path.join(vdir, '.dsh', 'skills')
    if os.path.isdir(sk):
        shutil.copytree(sk, os.path.join(work, '.dsh', 'skills'), dirs_exist_ok=True)


def run_checker(task, work, out):
    try:
        p = subprocess.run([sys.executable, os.path.join(task['dir'], 'check.py'), work], capture_output=True,
                           timeout=600, env=dict(os.environ, PYTHONUTF8='1'))
        text = p.stdout.decode('utf-8', errors='replace')
        start = text.find('{')
        res = json.loads(text[start:]) if start >= 0 else {'pass': False, 'score': 0, 'checks': [], 'error': 'no JSON from checker: ' + text[-300:] + p.stderr.decode('utf-8', errors='replace')[-300:]}
    except Exception as e:  # the checker itself broke: record it, never crash the batch
        res = {'pass': False, 'score': 0, 'checks': [], 'error': 'checker crashed: %s: %s' % (type(e).__name__, e)}
    with open(os.path.join(out, 'check.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def one(task, rep, vdir, tag, a):
    base = os.path.join(RUNS, tag, '%s__r%d' % (task['id'], rep))
    work, out = os.path.join(base, 'work'), os.path.join(base, 'out')
    if a.resume and os.path.isfile(os.path.join(out, 'record.json')):
        with open(os.path.join(out, 'record.json'), encoding='utf-8') as f:
            old = json.load(f)
        if not (a.redo_lowmem and old.get('lowMemory')):
            return old
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    materialize(vdir, work, task)
    with open(os.path.join(task['dir'], 'task.md'), encoding='utf-8') as f:
        prompt = f.read()
    timeout = int(task.get('timeout_s', 900) * a.timeout_scale)
    env = {'ABQ_GATE': 'off', 'ABQ_CASE_EXCLUDE': task['id']}
    if vdir and os.path.abspath(vdir) != ROOT and os.path.isfile(os.path.join(vdir, 'tools', 'abq', 'hints.json')):
        env['ABQ_HINTS_FILE'] = os.path.join(vdir, 'tools', 'abq', 'hints.json')
    info = dshrun.run(prompt, work, out, mode=a.mode, timeout=timeout, patches=a.patch, extra_env=env)
    chk = run_checker(task, work, out)
    rec = {'task': task['id'], 'split': task.get('split'), 'rep': rep, 'pass': bool(chk.get('pass')),
           'score': chk.get('score', 0), 'checkError': chk.get('error'),
           'failed': [c['name'] for c in chk.get('checks', []) if not c.get('pass')],
           'seconds': info['seconds'], 'timedOut': info['timedOut'], 'exitCode': info['exitCode'],
           'reason': (info.get('reason') or {}).get('kind') if isinstance(info.get('reason'), dict) else info.get('reason'),
           'steps': info['steps'], 'toolCalls': info['nToolCalls'], 'tools': info['tools'], 'abq': info['abq'],
           'rawAbaqus': info['rawAbaqus'], 'skills': info['skills'], 'usage': info['usage'], 'dir': base,
           'lowMemory': info.get('lowMemory', 0)}
    with open(os.path.join(out, 'record.json'), 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print('[%s] %s r%d  %s  score %.2f  %.0fs  steps %d  %s' % (
        time.strftime('%H:%M:%S'), task['id'], rep, 'PASS' if rec['pass'] else 'FAIL', rec['score'] or 0, rec['seconds'],
        rec['steps'], ('failed: ' + ','.join(rec['failed'])) if rec['failed'] else ''), flush=True)
    return rec


def report(records, tag, variant):
    by = {}
    for r in records:
        by.setdefault(r['task'], []).append(r)
    L = ['# Eval `%s` — variant `%s`' % (tag, variant), '',
         '| task | split | pass | mean score | mean s | mean steps | in tok (k) | out tok (k) | abq cmds | raw abaqus | failed checks |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    tot = {}
    for t in sorted(by):
        rs = by[t]
        n = len(rs)
        k = sum(1 for r in rs if r['pass'])
        sp = rs[0].get('split') or '?'
        tot.setdefault(sp, [0, 0])
        tot[sp][0] += k
        tot[sp][1] += n
        failed = {}
        for r in rs:
            for c in r['failed']:
                failed[c] = failed.get(c, 0) + 1
        L.append('| %s | %s | %d/%d | %.2f | %.0f | %.0f | %.0f | %.1f | %.1f | %.1f | %s |' % (
            t, sp, k, n, sum(r['score'] or 0 for r in rs) / n, sum(r['seconds'] for r in rs) / n,
            sum(r['steps'] for r in rs) / n, sum(r['usage']['inputTokens'] + r['usage']['cacheReadTokens'] for r in rs) / n / 1000.0,
            sum(r['usage']['outputTokens'] for r in rs) / n / 1000.0, sum(sum(r['abq'].values()) for r in rs) / n,
            sum(r['rawAbaqus'] for r in rs) / n, ', '.join('%s×%d' % kv for kv in sorted(failed.items()))))
    L.append('')
    allk = sum(v[0] for v in tot.values())
    alln = sum(v[1] for v in tot.values())
    for sp, (k, n) in sorted(tot.items()):
        L.append('- **%s**: %d/%d = %.0f%%' % (sp, k, n, 100.0 * k / n if n else 0))
    L.append('- **all**: %d/%d = %.0f%%' % (allk, alln, 100.0 * allk / alln if alln else 0))
    lm = [r for r in records if r.get('lowMemory')]
    if lm:
        L.append('- **%d run(s) hit the low-memory pause** (machine busy with other jobs) and are not fair samples: %s' % (
            len(lm), ', '.join('%s r%d' % (r['task'], r['rep']) for r in lm)))
    L.append('- timed out: %d, mean wall time %.0f s, total tokens in %.2f M / out %.2f M' % (
        sum(1 for r in records if r['timedOut']), sum(r['seconds'] for r in records) / max(1, len(records)),
        sum(r['usage']['inputTokens'] + r['usage']['cacheReadTokens'] for r in records) / 1e6,
        sum(r['usage']['outputTokens'] for r in records) / 1e6))
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--variant', default='live')
    ap.add_argument('--tasks', default='', help='comma-separated task ids (default: by --split)')
    ap.add_argument('--split', default='all', choices=['all', 'tune', 'holdout'])
    ap.add_argument('--suite', default='all', help='basic (t*, h*), hard (x*), domain (d*); comma-separated, default all')
    ap.add_argument('--repeats', type=int, default=1)
    ap.add_argument('--parallel', type=int, default=3)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--mode', default='workspace-write')
    ap.add_argument('--timeout-scale', type=float, default=1.0)
    ap.add_argument('--patch', action='append', default=[], help='extra DSH profile patch overlay (repeatable)')
    ap.add_argument('--resume', action='store_true', help='keep finished runs of this tag, only run the missing ones')
    ap.add_argument('--redo-lowmem', action='store_true', help='with --resume: redo runs that hit the low-memory pause')
    a = ap.parse_args()
    names = [x.strip() for x in a.tasks.split(',') if x.strip()]
    tasks = load_tasks(names or None, a.split, a.suite)
    if not tasks:
        raise SystemExit('no tasks selected')
    vdir = variant_dir(a.variant)
    jobs = [(t, r) for r in range(1, a.repeats + 1) for t in tasks]
    print('eval tag=%s variant=%s: %d tasks x %d repeats, parallel=%d' % (a.tag, a.variant, len(tasks), a.repeats, a.parallel), flush=True)
    records = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=a.parallel) as ex:
        futs = [ex.submit(one, t, r, vdir, a.tag, a) for t, r in jobs]
        for f in cf.as_completed(futs):
            try:
                records.append(f.result())
            except Exception as e:
                print('run crashed: %s: %s' % (type(e).__name__, e), flush=True)
    records.sort(key=lambda r: (r['task'], r['rep']))
    base = os.path.join(RUNS, a.tag)
    with open(os.path.join(base, 'results.json'), 'w', encoding='utf-8') as f:
        json.dump({'tag': a.tag, 'variant': a.variant, 'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
                   'wallSeconds': round(time.time() - t0, 1), 'records': records}, f, ensure_ascii=False, indent=1)
    text = report(records, a.tag, a.variant)
    with open(os.path.join(base, 'report.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    print(text, flush=True)


if __name__ == '__main__':
    main()
