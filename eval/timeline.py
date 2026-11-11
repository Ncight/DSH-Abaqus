# -*- coding: utf-8 -*-
"""Where does the wall time of a benchmark run go?  (needs runs recorded with time stamps: out/events.times)

    py timeline.py <tag>                    all runs of the tag: share of model time and of each kind of tool call
    py timeline.py <tag> <task__rN>         one run: the same plus its longest steps
    py timeline.py <tagA> <tagB> ...        several tags side by side (totals only)

"model" is the time between a tool result and the next tool call (reading the result, reasoning, writing the call);
a tool call's time runs from the call to its result.  Resolution about half a second.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNS = os.path.join(ROOT, '.dsh-eval', 'runs')
sys.path.insert(0, HERE)
import dshrun  # noqa: E402


def kind_of(ev):
    tool = ev.get('tool') or ev.get('name') or '?'
    inp = ev.get('input') or {}
    if tool != 'pwsh' or not isinstance(inp, dict):
        return tool
    cmd = str(inp.get('command') or '')
    m = re.search(r'abq(?:\.cmd|\.py)?["\']?\s+([a-z][a-z-]*)', cmd, re.I)
    if m and 'abq' in cmd.lower():
        return 'abq ' + m.group(1).lower()
    if re.search(r'(^|[\s;&|(])abaqus(\.bat)?\s', cmd, re.I):
        m = re.search(r'abaqus(?:\.bat)?\s+(cae|python|job=)', cmd, re.I)
        return 'raw abaqus ' + (m.group(1).rstrip('=').lower() if m else 'other')
    return 'pwsh (other)'


def analyse(out):
    """-> (dict kind -> [n, seconds], model seconds, total seconds, steps[(seconds, kind, text)]) or None"""
    evs = dshrun.event_times(out)
    if not evs:
        return None
    acc = {}
    steps = []
    open_calls = {}
    model = 0.0
    last = evs[0][0]
    for t, ev in evs:
        typ = ev.get('type')
        if typ == 'tool_call':
            if not open_calls:
                model += max(0.0, t - last)
            inp = ev.get('input') or {}
            text = str(inp.get('command') or inp.get('path') or inp.get('file_path') or inp.get('name') or '') if isinstance(inp, dict) else ''
            open_calls[ev.get('callId')] = (t, kind_of(ev), text.replace('\n', ' ')[:110])
        elif typ == 'tool_result' and ev.get('callId') in open_calls:
            t0, kind, text = open_calls.pop(ev['callId'])
            a = acc.setdefault(kind, [0, 0.0])
            a[0] += 1
            a[1] += t - t0
            steps.append((t - t0, kind, text))
            last = t
        elif typ == 'final':
            model += max(0.0, t - last)
            last = t
    total = evs[-1][0] - evs[0][0]
    return acc, model, total, steps


def show(title, acc, model, total, nruns=1):
    tool = sum(v[1] for v in acc.values())
    print('%s: %.0f s per run — model %.0f s (%.0f%%), tools %.0f s (%.0f%%)' % (
        title, total / nruns, model / nruns, 100.0 * model / total if total else 0, tool / nruns, 100.0 * tool / total if total else 0))
    for k, (n, s) in sorted(acc.items(), key=lambda kv: -kv[1][1])[:12]:
        print('    %-20s %5.1f calls  %6.1f s per run  (%4.1f%% of wall, %5.1f s per call)' % (
            k, n / float(nruns), s / nruns, 100.0 * s / total if total else 0, s / n if n else 0))


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if len(args) == 2 and os.path.isdir(os.path.join(RUNS, args[0], args[1])):
        r = analyse(os.path.join(RUNS, args[0], args[1], 'out'))
        if r is None:
            raise SystemExit('this run has no time stamps (recorded before events.times was kept)')
        acc, model, total, steps = r
        show('%s/%s' % (args[0], args[1]), acc, model, total)
        print('  longest steps:')
        for sec, kind, text in sorted(steps, reverse=True)[:10]:
            print('    %6.1f s  %-16s %s' % (sec, kind, text))
        return
    for tag in args:
        base = os.path.join(RUNS, tag)
        tot_acc, tot_model, tot_total, n, skipped = {}, 0.0, 0.0, 0, 0
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            out = os.path.join(base, d, 'out')
            if not os.path.isfile(os.path.join(out, 'record.json')):
                continue
            r = analyse(out)
            if r is None:
                skipped += 1
                continue
            acc, model, total, _ = r
            n += 1
            tot_model += model
            tot_total += total
            for k, v in acc.items():
                a = tot_acc.setdefault(k, [0, 0.0])
                a[0] += v[0]
                a[1] += v[1]
        if n:
            show('%s (%d runs%s)' % (tag, n, ', %d without time stamps' % skipped if skipped else ''), tot_acc, tot_model, tot_total, n)
        else:
            print('%s: no runs with time stamps' % tag)


if __name__ == '__main__':
    main()
