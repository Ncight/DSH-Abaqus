# -*- coding: utf-8 -*-
"""Aggregate finished runs of one or more eval tags (reads every out/record.json) and print a side-by-side table.

    py report.py v1_base v1_live          # also rewrites each tag's results.json / report.md from the records
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNS = os.path.join(ROOT, '.dsh-eval', 'runs')
sys.path.insert(0, HERE)


def collect(tag):
    base = os.path.join(RUNS, tag)
    recs = []
    if not os.path.isdir(base):
        return recs
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d, 'out', 'record.json')
        if os.path.isfile(p):
            with open(p, encoding='utf-8') as f:
                recs.append(json.load(f))
    return recs


def summarize(recs):
    by = {}
    for r in recs:
        d = by.setdefault(r['task'], {'split': r.get('split'), 'n': 0, 'k': 0, 'sec': 0.0, 'tok_in': 0, 'tok_out': 0,
                                      'steps': 0, 'low': 0, 'failed': {}})
        d['n'] += 1
        d['k'] += 1 if r['pass'] else 0
        d['sec'] += r['seconds']
        d['steps'] += r['steps']
        d['tok_in'] += r['usage']['inputTokens'] + r['usage']['cacheReadTokens']
        d['tok_out'] += r['usage']['outputTokens']
        d['low'] += 1 if r.get('lowMemory') else 0
        for c in r.get('failed', []):
            d['failed'][c] = d['failed'].get(c, 0) + 1
    return by


def main():
    tags = sys.argv[1:]
    if not tags:
        raise SystemExit(__doc__)
    data = {}
    for t in tags:
        recs = collect(t)
        data[t] = summarize(recs)
        base = os.path.join(RUNS, t)
        if recs:
            variant = None
            old = os.path.join(base, 'results.json')
            if os.path.isfile(old):
                try:
                    with open(old, encoding='utf-8') as f:
                        variant = json.load(f).get('variant')
                except ValueError:
                    pass
            with open(old, 'w', encoding='utf-8') as f:
                json.dump({'tag': t, 'variant': variant, 'records': recs}, f, ensure_ascii=False, indent=1)
            try:
                import run_eval
                with open(os.path.join(base, 'report.md'), 'w', encoding='utf-8') as f:
                    f.write(run_eval.report(recs, t, variant))
            except Exception:
                pass
    tasks = sorted(set(k for t in tags for k in data[t]))
    head = '| task | split | ' + ' | '.join('%s pass | %s s | %s tok(k)' % (t, t, t) for t in tags) + ' |'
    print(head)
    print('|' + '---|' * (2 + 3 * len(tags)))
    for task in tasks:
        cells = []
        split = ''
        for t in tags:
            d = data[t].get(task)
            if not d:
                cells += ['—', '—', '—']
                continue
            split = d['split']
            cells += ['%d/%d%s' % (d['k'], d['n'], ' (low-mem %d)' % d['low'] if d['low'] else ''),
                      '%.0f' % (d['sec'] / d['n']), '%.0f' % ((d['tok_in'] + d['tok_out']) / d['n'] / 1000.0)]
        print('| %s | %s | %s |' % (task, split, ' | '.join(cells)))
    for t in tags:
        tot = {}
        for d in data[t].values():
            s = tot.setdefault(d['split'], [0, 0])
            s[0] += d['k']
            s[1] += d['n']
        allk, alln = sum(v[0] for v in tot.values()), sum(v[1] for v in tot.values())
        nsec = sum(d['sec'] for d in data[t].values())
        print('%s: %s; all %d/%d; mean %.0f s per run' % (
            t, ', '.join('%s %d/%d' % (s, v[0], v[1]) for s, v in sorted(tot.items())), allk, alln, nsec / max(1, alln)))
        fails = [(task, d['failed']) for task, d in sorted(data[t].items()) if d['failed']]
        for task, f in fails:
            print('   failed checks in %s: %s' % (task, ', '.join('%s×%d' % kv for kv in sorted(f.items()))))


if __name__ == '__main__':
    main()
