# -*- coding: utf-8 -*-
"""Full input and result of chosen steps of one eval run (step numbers as printed by trace.py).

    py step.py <tag> <task__rN> 19 23 [--max 3000] [--think]"""
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dsh-eval', 'runs')
args = sys.argv[1:]
mx = 3000
if '--max' in args:
    i = args.index('--max')
    mx = int(args[i + 1])
    del args[i:i + 2]
tag, run = args[0], args[1]
want = set(int(x) for x in args[2:])
p = os.path.join(ROOT, tag, run, 'out', 'events.jsonl')
n = 0
ids = {}
with open(p, encoding='utf-8', errors='replace') as f:
    for line in f:
        if not line.startswith('{'):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'tool_call':
            n += 1
            if n in want:
                ids[ev.get('callId')] = n
                inp = ev.get('input') or {}
                print('=' * 20, 'step', n, ev.get('tool'))
                if ev.get('tool') == 'pwsh':
                    print(str(inp.get('command', ''))[:mx])
                else:
                    print(json.dumps(inp, ensure_ascii=False)[:mx])
        elif ev.get('type') == 'tool_result' and ev.get('callId') in ids:
            print('-' * 20, 'result of step', ids[ev['callId']], ev.get('status'))
            print(str(ev.get('result') or '')[:mx])
        elif ev.get('type') == 'thinking' and '--think' in sys.argv and (n + 1) in want:
            print('~' * 20, 'thinking before step', n + 1)
            print(str(ev.get('text') or '')[:mx])
