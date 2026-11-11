# -*- coding: utf-8 -*-
"""Self-improvement loop for the Abaqus guidance (AGENTS.md, skills, hint table).

    py optimize.py digest  --tag <eval tag>                       failures of the TUNING split -> failures.md
    py optimize.py propose --tag <eval tag> --cand <name>         strong model reads failures.md and edits a candidate copy
    py optimize.py compare --cur <tag> --cand <tag>               promotion decision from two finished evals
    py optimize.py promote --cand <name> --cur <tag> --candtag <tag>   copy the candidate over the live files + git commit
    py optimize.py cycle   --name <name> [--repeats 2 --parallel 2]    all of the above in sequence

What may change automatically: AGENTS.md, .dsh/skills/**, tools/abq/hints.json (text and data only).
What never changes automatically: tool code, the benchmark, the checkers. Holdout tasks are never shown to the proposer;
they only vote in the promotion gate, so a candidate that merely memorises the tuning tasks does not get promoted.
"""
import argparse
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

RUNS = os.path.join(ROOT, '.dsh-eval', 'runs')
VARIANTS = os.path.join(HERE, 'variants')
EDITABLE = ['AGENTS.md', os.path.join('.dsh', 'skills'), os.path.join('tools', 'abq', 'hints.json')]
PRO_PATCH = os.path.join(HERE, 'patches', 'model_pro.yml')


def load(tag):
    with open(os.path.join(RUNS, tag, 'results.json'), encoding='utf-8') as f:
        return json.load(f)


def rates(res):
    by = {}
    for r in res['records']:
        d = by.setdefault(r['task'], {'split': r.get('split'), 'n': 0, 'k': 0})
        d['n'] += 1
        d['k'] += 1 if r['pass'] else 0
    return by


def task_seconds(res):
    """mean wall time per task"""
    acc = {}
    for r in res['records']:
        acc.setdefault(r['task'], []).append(r['seconds'])
    return dict((t, sum(v) / len(v)) for t, v in acc.items())


def split_totals(by):
    tot = {}
    for t, d in by.items():
        s = tot.setdefault(d['split'], [0, 0])
        s[0] += d['k']
        s[1] += d['n']
    return tot


# ---------------------------------------------------------------- digest
def tool_trace(events_path, limit=60):
    """compact list of what the agent did: shell commands (first 200 chars) and the first lines of failed results"""
    out = []
    pending = {}
    if not os.path.isfile(events_path):
        return out
    with open(events_path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if not line.startswith('{'):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get('type') == 'tool_call':
                inp = ev.get('input') or {}
                if ev.get('tool') == 'pwsh' and isinstance(inp, dict):
                    desc = 'pwsh: ' + str(inp.get('command', ''))[:220].replace('\n', ' ')
                elif ev.get('tool') in ('write', 'edit', 'read') and isinstance(inp, dict):
                    desc = '%s: %s' % (ev['tool'], str(inp.get('path') or inp.get('file_path') or '')[-80:])
                elif ev.get('tool') == 'skill' and isinstance(inp, dict):
                    desc = 'skill: %s' % (inp.get('name') or inp.get('skill') or inp)
                else:
                    desc = str(ev.get('tool'))
                pending[ev.get('callId')] = len(out)
                out.append(desc)
            elif ev.get('type') == 'tool_result':
                i = pending.pop(ev.get('callId'), None)
                res = str(ev.get('result') or '')
                if i is not None and ('✗' in res or 'Error' in res or '错误' in res or 'FAIL' in res or '⛔' in res):
                    first = [l.strip() for l in res.splitlines() if l.strip()][:3]
                    out[i] += '\n      -> ' + ' | '.join(first)[:300]
    if len(out) > limit:
        out = out[:limit // 2] + ['… (%d steps omitted) …' % (len(out) - limit)] + out[-limit // 2:]
    return out


def digest(tag, split='tune'):
    res = load(tag)
    L = ['# Failure digest — eval `%s` (variant `%s`), split `%s`' % (tag, res.get('variant'), split), '']
    by = rates(res)
    L.append('Pass rates: ' + ', '.join('%s %d/%d' % (t, d['k'], d['n']) for t, d in sorted(by.items()) if d['split'] == split))
    L.append('')
    n = 0
    for r in res['records']:
        if r.get('split') != split or r['pass']:
            continue
        n += 1
        base = r['dir']
        tdir = os.path.join(HERE, 'tasks', r['task'])
        with open(os.path.join(tdir, 'task.md'), encoding='utf-8') as f:
            task = f.read().strip()
        chk = {}
        cp = os.path.join(base, 'out', 'check.json')
        if os.path.isfile(cp):
            with open(cp, encoding='utf-8') as f:
                chk = json.load(f)
        final = ''
        fp = os.path.join(base, 'out', 'final.txt')
        if os.path.isfile(fp):
            with open(fp, encoding='utf-8') as f:
                final = f.read().strip()
        L.append('## %s (repeat %d) — FAILED' % (r['task'], r['rep']))
        L.append('- outcome: %s, %.0f s, %d model steps, timed out: %s' % (r.get('reason'), r['seconds'], r['steps'], r['timedOut']))
        L.append('- abq commands used: %s; raw `abaqus` calls: %d; skills loaded: %s' % (
            json.dumps(r.get('abq', {}), ensure_ascii=False), r.get('rawAbaqus', 0), ', '.join(r.get('skills', [])) or 'none'))
        L.append('- checks:')
        for c in chk.get('checks', []):
            L.append('  - [%s] %s: got %s, expected %s (tol %s) %s' % ('ok' if c.get('pass') else 'FAIL', c.get('name'), c.get('got'),
                                                               c.get('expected'), c.get('tol'), c.get('note') or ''))
        if chk.get('error'):
            L.append('  - checker error: %s' % chk['error'])
        L.append('')
        L.append('### Task given to the agent')
        L.append(task)
        L.append('')
        L.append('### What the agent did')
        L.extend('%d. %s' % (i + 1, s) for i, s in enumerate(tool_trace(os.path.join(base, 'out', 'events.jsonl'))))
        L.append('')
        L.append('### Its final answer')
        L.append(final[:2500] + (' …' if len(final) > 2500 else ''))
        L.append('')
    if n == 0:
        L.append('No failures in this split.')
    out = os.path.join(RUNS, tag, 'failures_%s.md' % split)
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    return out, n


# ---------------------------------------------------------------- candidate
def make_candidate(name):
    cand = os.path.join(VARIANTS, name)
    if os.path.isdir(cand):
        shutil.rmtree(cand)
    os.makedirs(cand)
    for rel in EDITABLE:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(cand, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    subprocess.run(['git', 'init', '-q'], cwd=cand)
    subprocess.run(['git', 'add', '-A'], cwd=cand, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['git', '-c', 'user.name=optimizer', '-c', 'user.email=optimizer@local', 'commit', '-q', '-m', 'baseline copy'],
                   cwd=cand, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cand


PROPOSER_PROMPT = """你是一名资深 Abaqus 分析师兼提示工程师。这个目录是一套给"能力较弱的模型"用的 Abaqus 工作指引的副本：
- AGENTS.md：每次任务都会完整读到的工作区说明（流程、硬性规则、工具清单）
- .dsh/skills/<名字>/SKILL.md：按需加载的专题知识（目录里的 .py 是配套模板）
- tools/abq/hints.json：工具在求解器报错时自动给出的提示表（正则 pattern → hint 文本）

同目录下的 FAILURES.md 是那个弱模型最近一次评测里**没通过**的任务记录：任务书、它实际执行的命令、检查器的判定、它的最终回答。

你的工作：
1. 逐条读 FAILURES.md，给每个失败判定根因，归入五类之一：缺知识（指引里没有或说得不够醒目）、缺工具/工具提示不足、缺检查（它没发现自己错了）、需求理解错、模型能力上限（指引帮不上）。
2. 针对前三类，修改 AGENTS.md、SKILL.md 或 hints.json，让同类错误在**别的题目**上也不再发生。
3. 写 CHANGES.md：每个失败的根因分类与证据；你做的每处修改、对应哪类失败、预期效果；你认为指引帮不上的失败。

必须遵守：
- 只改通用的工程知识与工作方法。不许写入任何一道具体题目的答案、数值、尺寸或"某某题应这样做"；修改必须对没见过的题同样有用。
- 写进去的 Abaqus 知识必须是验证过的，弱模型会原样照抄。凡是代码片段、接口用法、关键字写法、报错原因，都要先实际验证再写：接口签名用 E:\\DSH-Abaqus\\abq.cmd api <方法名>；写法和行为在本目录的 scratch\\ 子目录里写一个最小脚本，用 abq.cmd script（CAE 脚本）或 abq.cmd python（读 ODB）跑通；需要现成的结果库可以用 E:\\DSH-Abaqus\\examples 下的算例自己求解一个小模型（一次只跑一个，模型要小）。验证不了的不要写，并在 CHANGES.md 里注明哪些内容经过了运行验证、哪些只是工作方法上的建议。
- 保持精炼：AGENTS.md 不超过 90 行；每个 SKILL.md 只增改必要的段落，不要整篇重写；优先把最容易被忽略的要点提到更醒目的位置，而不是堆更多文字。
- SKILL.md 开头的 frontmatter（name、description）格式不能破坏，name 不能改。hints.json 必须保持合法 JSON。
- 只能修改本目录内的这三类文件，外加新建 CHANGES.md。不要动别的目录。
完成后用三五句话总结你改了什么。"""


def propose(tag, name, timeout=2400):
    path, n = digest(tag, 'tune')
    cand = make_candidate(name)
    shutil.copy2(path, os.path.join(cand, 'FAILURES.md'))
    if n == 0:
        print('no tuning failures: nothing to propose')
        return cand, False
    out = os.path.join(VARIANTS, name + '_proposer_out')
    info = dshrun.run(PROPOSER_PROMPT, cand, out, mode='workspace-write', timeout=timeout, patches=[PRO_PATCH],
                      extra_env={'ABQ_GATE': 'off'})
    diff = subprocess.run(['git', 'status', '--porcelain'], cwd=cand, capture_output=True).stdout.decode('utf-8', 'replace')
    print('proposer finished: exit %s, %.0f s, %d tool calls' % (info['exitCode'], info['seconds'], info['nToolCalls']))
    print('changed files:\n' + (diff or '  (none)'))
    ok = validate_candidate(cand)
    return cand, bool(diff.strip()) and ok


def validate_candidate(cand):
    ok = True
    hp = os.path.join(cand, 'tools', 'abq', 'hints.json')
    try:
        with open(hp, encoding='utf-8') as f:
            hints = json.load(f)
        import re
        for h in hints:
            re.compile(h['pattern'])
            assert isinstance(h['hint'], str)
    except Exception as e:
        print('INVALID hints.json: %s' % e)
        ok = False
    sk = os.path.join(cand, '.dsh', 'skills')
    for d in sorted(os.listdir(sk)):
        p = os.path.join(sk, d, 'SKILL.md')
        if not os.path.isfile(p):
            continue
        with open(p, encoding='utf-8') as f:
            head = f.read(1500)
        if not head.startswith('---') or ('name: %s' % d) not in head or 'description:' not in head:
            print('INVALID frontmatter: %s' % p)
            ok = False
    ap = os.path.join(cand, 'AGENTS.md')
    with open(ap, encoding='utf-8') as f:
        nl = len(f.read().splitlines())
    if nl > 110:
        print('AGENTS.md too long: %d lines' % nl)
        ok = False
    return ok


# ---------------------------------------------------------------- gate
def compare(cur_tag, cand_tag, verbose=True):
    cur, cand = rates(load(cur_tag)), rates(load(cand_tag))
    tc, td = split_totals(cur), split_totals(cand)
    rows = []
    regress = []
    for t in sorted(set(cur) | set(cand)):
        a, b = cur.get(t), cand.get(t)
        rows.append((t, (a or b)['split'], '%d/%d' % (a['k'], a['n']) if a else '—', '%d/%d' % (b['k'], b['n']) if b else '—'))
        if a and b and a['n'] and b['n'] and a['k'] == a['n'] and b['k'] == 0:
            regress.append(t)

    def rate(tot, s):
        k, n = tot.get(s, [0, 0])
        return (k / float(n)) if n else 0.0, k, n

    tune_c, tune_d = rate(tc, 'tune'), rate(td, 'tune')
    hold_c, hold_d = rate(tc, 'holdout'), rate(td, 'holdout')
    need = max(2.0 / max(1, tune_d[2]), 0.10)                 # at least two extra passes and at least +10 points
    # Safety conditions that always apply
    blockers = []
    if hold_d[2] and hold_c[2] and hold_d[0] < hold_c[0] - 1.0 / hold_d[2] - 1e-9:
        blockers.append('holdout pass rate dropped (%.0f%% -> %.0f%%)' % (100 * hold_c[0], 100 * hold_d[0]))
    if regress:
        blockers.append('tasks went from always passing to always failing: ' + ', '.join(regress))
    # Route A: clearly more accurate on the tuning split
    more_accurate = tune_d[0] - tune_c[0] >= need - 1e-9
    # Route B: not less accurate, and clearly cheaper (mean wall time per task over the tasks both evals ran)
    sec_c, sec_d = task_seconds(load(cur_tag)), task_seconds(load(cand_tag))
    common = sorted(set(sec_c) & set(sec_d))
    mean_c = sum(sec_c[t] for t in common) / len(common) if common else 0.0
    mean_d = sum(sec_d[t] for t in common) / len(common) if common else 0.0
    cheaper = bool(common) and mean_d <= 0.85 * mean_c
    not_less_accurate = tune_d[0] >= tune_c[0] - 1e-9
    reasons = list(blockers)
    route = ''
    if not blockers and more_accurate:
        route = 'tuning pass rate %.0f%% -> %.0f%%' % (100 * tune_c[0], 100 * tune_d[0])
    elif not blockers and not_less_accurate and cheaper:
        route = 'no loss of accuracy and mean time per task %.0f s -> %.0f s (%.0f%%)' % (mean_c, mean_d, 100.0 * (mean_d / mean_c - 1.0))
    else:
        if not more_accurate:
            reasons.append('tuning pass rate did not improve enough (%.0f%% -> %.0f%%, need +%.0f points)' % (
                100 * tune_c[0], 100 * tune_d[0], 100 * need))
        if not cheaper:
            reasons.append('not clearly cheaper either (mean time per task %.0f s -> %.0f s, need -15%%)' % (mean_c, mean_d))
        if not not_less_accurate:
            reasons.append('tuning pass rate dropped')
    decision = bool(route)
    if verbose:
        print('| task | split | current `%s` | candidate `%s` |' % (cur_tag, cand_tag))
        print('|---|---|---|---|')
        for r in rows:
            print('| %s | %s | %s | %s |' % r)
        print('tune: %d/%d -> %d/%d;  holdout: %d/%d -> %d/%d' % (tune_c[1], tune_c[2], tune_d[1], tune_d[2], hold_c[1], hold_c[2], hold_d[1], hold_d[2]))
        print('mean time per task over the %d common tasks: %.0f s -> %.0f s' % (len(common), mean_c, mean_d))
        print('DECISION: ' + ('PROMOTE — ' + route if decision else 'REJECT — ' + '; '.join(reasons)))
    return decision, ([route] if decision else reasons)


def archive(name, cur_tag, cand_tag, promoted, reasons, forced=False):
    """keep what a candidate proposed, promoted or not: eval/history/<name>/ gets its CHANGES.md, the failure digest it was
    built from and the exact diff against the guidance it started from; one line goes to eval/history.jsonl.
    (The candidate directory itself is a throw-away working copy with its own git repository and is not versioned here.)"""
    cand = os.path.join(VARIANTS, name)
    dst = os.path.join(HERE, 'history', name)
    os.makedirs(dst, exist_ok=True)
    for fn in ('CHANGES.md', 'FAILURES.md'):
        if os.path.isfile(os.path.join(cand, fn)):
            shutil.copy2(os.path.join(cand, fn), os.path.join(dst, fn))
    if os.path.isdir(os.path.join(cand, '.git')):
        subprocess.run(['git', 'add', '-A', '--'] + [r.replace(os.sep, '/') for r in EDITABLE], cwd=cand,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        d = subprocess.run(['git', 'diff', '--cached', '--no-color', 'HEAD', '--'] + [r.replace(os.sep, '/') for r in EDITABLE],
                           cwd=cand, capture_output=True).stdout
        with open(os.path.join(dst, 'candidate.diff'), 'wb') as f:
            f.write(d)
    with open(os.path.join(HERE, 'history.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'candidate': name, 'cur': cur_tag, 'cand': cand_tag,
                            'promoted': bool(promoted), 'forced': bool(forced), 'reasons': reasons}, ensure_ascii=False) + '\n')
    return dst


def promote(name, cur_tag, cand_tag, force=False):
    ok, reasons = compare(cur_tag, cand_tag)
    if not ok and not force:
        archive(name, cur_tag, cand_tag, False, reasons)
        subprocess.run(['git', 'add', '-A', 'eval/history.jsonl', 'eval/history'], cwd=ROOT)
        subprocess.run(['git', '-c', 'user.name=optimizer', '-c', 'user.email=optimizer@local', 'commit', '-q', '-m',
                        'guidance: candidate %s rejected (%s vs %s)' % (name, cur_tag, cand_tag)], cwd=ROOT)
        print('candidate %s rejected; its proposal is kept in eval/history/%s/' % (name, name))
        return False
    cand = os.path.join(VARIANTS, name)
    for rel in EDITABLE:
        src, dst = os.path.join(cand, rel), os.path.join(ROOT, rel)
        if os.path.isdir(src):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
    archive(name, cur_tag, cand_tag, True, reasons, forced=bool(force and not ok))
    subprocess.run(['git', 'add', '-A', 'AGENTS.md', '.dsh', 'tools/abq/hints.json', 'eval/history.jsonl', 'eval/history'], cwd=ROOT)
    subprocess.run(['git', '-c', 'user.name=optimizer', '-c', 'user.email=optimizer@local', 'commit', '-q', '-m',
                    'guidance: promote candidate %s (%s -> %s)' % (name, cur_tag, cand_tag)], cwd=ROOT)
    print('promoted %s into the live workspace and committed' % name)
    return True


def run_eval(variant, tag, repeats, parallel, split='all', suite='all'):
    cmd = [sys.executable, os.path.join(HERE, 'run_eval.py'), '--variant', variant, '--tag', tag, '--repeats', str(repeats),
           '--parallel', str(parallel), '--split', split, '--suite', suite, '--resume']
    subprocess.run(cmd, check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('digest')
    s.add_argument('--tag', required=True)
    s.add_argument('--split', default='tune')
    s = sub.add_parser('propose')
    s.add_argument('--tag', required=True)
    s.add_argument('--cand', required=True)
    s = sub.add_parser('compare')
    s.add_argument('--cur', required=True)
    s.add_argument('--cand', required=True)
    s = sub.add_parser('promote')
    s.add_argument('--cand', required=True)
    s.add_argument('--cur', required=True)
    s.add_argument('--candtag', required=True)
    s.add_argument('--force', action='store_true')
    s = sub.add_parser('cycle')
    s.add_argument('--name', required=True)
    s.add_argument('--repeats', type=int, default=2)
    s.add_argument('--parallel', type=int, default=2)
    s.add_argument('--cur-tag', help='reuse an existing eval of the live variant instead of running a new one')
    s.add_argument('--suite', default='all', help='task suites to evaluate on: basic, hard, domain (comma-separated)')
    a = ap.parse_args()
    if a.cmd == 'digest':
        path, n = digest(a.tag, a.split)
        print('%d failures -> %s' % (n, path))
    elif a.cmd == 'propose':
        cand, changed = propose(a.tag, a.cand)
        print('candidate: %s (usable changes: %s)' % (cand, changed))
    elif a.cmd == 'compare':
        compare(a.cur, a.cand)
    elif a.cmd == 'promote':
        promote(a.cand, a.cur, a.candtag, a.force)
    elif a.cmd == 'cycle':
        cur = a.cur_tag or (a.name + '_cur')
        if not a.cur_tag:
            run_eval('live', cur, a.repeats, a.parallel, suite=a.suite)
        cand, changed = propose(cur, a.name)
        if not changed:
            print('cycle stops: no usable candidate changes')
            return
        run_eval(cand, a.name + '_cand', a.repeats, a.parallel, suite=a.suite)
        promote(a.name, cur, a.name + '_cand')


if __name__ == '__main__':
    main()
