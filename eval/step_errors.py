# -*- coding: utf-8 -*-
"""Step-level failure mining: which exceptions do the agent's own scripts hit, and how often?

    py step_errors.py tag1 tag2 ... [--split tune|holdout|all]

Counts the last exception line of every failing CAE script (abq script / abaqus cae) and ODB script (abq python /
abaqus python) call in the runs of the given eval tags.  The default split is tune: what is learnt from the holdout
tasks must not flow back into the guidance.  This is the input for improving skills and the hint table when the tasks
themselves all pass (see eval/history/v3/CHANGES.md)."""
import collections
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dsh-eval', 'runs')
args = sys.argv[1:]
split = 'tune'
if '--split' in args:
    i = args.index('--split')
    split = args[i + 1]
    del args[i:i + 2]
EXC = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)): (.*)$')


def kind(cmd):
    c = cmd.lower()
    if re.search(r"abq\.cmd['\"]?\s+script", c) or re.search(r'\babaqus\b.*\bcae\b', c):
        return 'CAE'
    if re.search(r"abq\.cmd['\"]?\s+python", c) or re.search(r'\babaqus\b.*\bpython\b', c):
        return 'ODB'
    return None


def dec(s):
    # raw abaqus output captured by PowerShell is often GBK shown as mojibake; leave as is
    return s


cnt = {'CAE': collections.Counter(), 'ODB': collections.Counter()}
ex = collections.defaultdict(set)
for tag in args:
    for d in sorted(glob.glob(os.path.join(ROOT, tag, '*__r*'))):
        rp = os.path.join(d, 'out', 'record.json')
        if not os.path.isfile(rp):
            continue
        rec = json.load(open(rp, encoding='utf-8'))
        if split != 'all' and rec.get('split') != split:
            continue
        pend = {}
        with open(os.path.join(d, 'out', 'events.jsonl'), encoding='utf-8', errors='replace') as f:
            for line in f:
                if not line.startswith('{'):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('type') == 'tool_call' and ev.get('tool') == 'pwsh':
                    pend[ev.get('callId')] = str((ev.get('input') or {}).get('command', ''))
                elif ev.get('type') == 'tool_result' and ev.get('callId') in pend:
                    k = kind(pend.pop(ev['callId']))
                    if not k:
                        continue
                    last = None
                    for ln in str(ev.get('result') or '').splitlines():
                        m = EXC.match(ln)
                        if m and 'NativeCommandError' not in ln:
                            last = (m.group(1), m.group(2).strip())
                    if last:
                        msg = re.sub(r'\d+(\.\d+)?([eE][-+]?\d+)?', '#', last[1])[:140]
                        key = '%s: %s' % (last[0], msg)
                        cnt[k][key] += 1
                        ex[(k, key)].add(rec['task'][:3])
for k in ('ODB', 'CAE'):
    print('=== %s scripts: %d failing calls ===' % (k, sum(cnt[k].values())))
    for key, n in cnt[k].most_common(60):
        print('%3d  %s   [%s]' % (n, key, ','.join(sorted(ex[(k, key)]))))
