# -*- coding: utf-8 -*-
"""Does the agent's final answer contain what a reviewer needs to check the result?  (a crude, deterministic proxy)

    py report_quality.py <tag> [<tag> ...]

Six yes/no items per run, looked for in out/final.txt with regular expressions:
  compare   a comparison with theory / hand estimate that states a deviation in percent
  units     the unit system or units of the results are stated
  mesh      element type and mesh size (element code plus a count or an element size)
  verify    a statement about solver completion / warnings / verification
  limits    assumptions or limitations are named
  plain     no number in scientific notation (the user's standing preference for reports)
The benchmark checkers only score the numbers; this table shows whether the delivery around the numbers differs between
configurations.  It measures presence of content, not its correctness.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(os.path.dirname(HERE), '.dsh-eval', 'runs')

ITEMS = [
    ('compare', r'(理论|解析|估算|手算|预估|公式|theor|analytic|hand)[^。\n]{0,120}?[-+−]?\d+(\.\d+)?\s*[%％]|'
                r'[-+−]?\d+(\.\d+)?\s*[%％][^。\n]{0,60}?(理论|解析|估算|手算|偏差|误差|差)|偏差[^。\n]{0,30}?\d+(\.\d+)?\s*[%％]'),
    ('units', r'单位制|单位[:：]|SI\b|m[–\-—]t[–\-—]s|mm[–\-—]t|N[–\-—]mm|kN|kPa|MPa\b|\bPa\b|\bHz\b'),
    ('mesh', r'\b(C3D\d+\w*|CP[SE]\d+\w*|CAX\d+\w*|B[23][123]\w*|S[348]R?\d*\w*|T[23]D[23]\w*|R[23]D\d|CIN\w+|SPRING\w*|DASHPOT\w*)\b'
             r'[\s\S]{0,400}?(\d[\d,， ]*\s*(个)?(单元|节点)|单元(尺寸|大小|边长)|网格)'),
    ('verify', r'核查|verify|PASS|警告|warning|收敛|能量比|COMPLETED|正常结束|成功完成|求解完成'),
    ('limits', r'局限|假设|注意|限制|未考虑|忽略|仅适用|caveat|limitation|assum'),
]
# the user's standing preference: numbers in reports are written out, never in scientific notation
SCI = re.compile(r'\d(\.\d+)?\s*[eE][-+]?\d{1,3}\b|[×x\*]\s*10\s*(\^|\*\*|<sup>)\s*[\{(]?[-+−]?\d|\\times\s*10\s*\^')


def score(text):
    s = dict((name, bool(re.search(pat, text, re.I))) for name, pat in ITEMS)
    body = re.sub(r'```[\s\S]*?```', '', text)             # quoted file contents (result.json) do not count
    s['plain'] = not SCI.search(body)
    return s


def main():
    tags = sys.argv[1:]
    if not tags:
        raise SystemExit(__doc__)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print('| tag | runs | ' + ' | '.join(n for n, _ in ITEMS) + ' | plain numbers | mean items of 6 | mean length (chars) |')
    print('|---|---|' + '---|' * (len(ITEMS) + 3))
    for tag in tags:
        base = os.path.join(RUNS, tag)
        rows = []
        for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            fp = os.path.join(base, d, 'out', 'final.txt')
            if not os.path.isfile(os.path.join(base, d, 'out', 'record.json')) or not os.path.isfile(fp):
                continue
            with open(fp, encoding='utf-8', errors='replace') as f:
                text = f.read()
            s = score(text)
            s['_len'] = len(text)
            rows.append(s)
        if not rows:
            print('| %s | 0 |' % tag)
            continue
        n = float(len(rows))
        names = [name for name, _ in ITEMS] + ['plain']
        print('| %s | %d | %s | %.2f | %.0f |' % (
            tag, len(rows), ' | '.join('%.0f%%' % (100.0 * sum(1 for r in rows if r[name]) / n) for name in names),
            sum(sum(1 for name in names if r[name]) for r in rows) / n, sum(r['_len'] for r in rows) / n))


if __name__ == '__main__':
    main()
