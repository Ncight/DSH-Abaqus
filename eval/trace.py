# -*- coding: utf-8 -*-
"""Compact trace of one eval run: every tool call in order, with the first lines of results that look like errors.

    py trace.py <tag> <task__rN> [line width] [--full]

--full shows the first lines of every result.  For the full text of single steps use step.py."""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dsh-eval', 'runs')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
tag, run = sys.argv[1], sys.argv[2]
maxlen = int(sys.argv[3]) if len(sys.argv) > 3 else 170
p = os.path.join(ROOT, tag, run, 'out', 'events.jsonl')
t0 = None
calls = {}
n = 0
with open(p, encoding='utf-8', errors='replace') as f:
    for line in f:
        if not line.startswith('{'):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        ts = ev.get('ts') or ev.get('time') or ev.get('timestamp')
        typ = ev.get('type')
        if typ == 'tool_call':
            n += 1
            inp = ev.get('input') or {}
            tool = ev.get('tool')
            if tool == 'pwsh':
                d = str(inp.get('command', '')).replace('\n', ' ⏎ ')
            elif tool in ('write', 'edit', 'read'):
                d = str(inp.get('path') or inp.get('file_path') or '')
                if tool == 'write':
                    d += '  (%d chars)' % len(str(inp.get('content', '')))
            elif tool == 'skill':
                d = str(inp.get('name') or inp.get('skill') or inp)
            else:
                d = json.dumps(inp, ensure_ascii=False)[:maxlen]
            calls[ev.get('callId')] = n
            print('%3d %-5s %s' % (n, tool, d[:maxlen]))
        elif typ == 'tool_result':
            res = str(ev.get('result') or '')
            flag = ''
            for key in ('✗', 'Error', '错误', 'FAIL', '⛔', 'Traceback', 'error'):
                if key in res:
                    flag = key
                    break
            if flag or '--full' in sys.argv:
                lines = [l.strip() for l in res.splitlines() if l.strip()]
                pick = [l for l in lines if flag and flag in l][:2] or lines[:2]
                print('        -> [%s] %s' % (flag, ' | '.join(pick)[:maxlen + 60]))
print('total tool calls: %d' % n)
