# -*- coding: utf-8 -*-
"""Parse Abaqus job files (.sta / .msg / .dat / log) into a compact diagnosis. Python 3, stdlib only."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SUCCESS = 'THE ANALYSIS HAS COMPLETED SUCCESSFULLY'
NOT_DONE = 'THE ANALYSIS HAS NOT BEEN COMPLETED'


def read_text(path, limit=None):
    if not os.path.isfile(path):
        return ''
    with open(path, 'rb') as f:
        raw = f.read() if limit is None else f.read(limit)
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def messages(text):
    """-> list of (kind, text) for ***ERROR / ***WARNING / ***NOTE blocks (continuation lines joined)."""
    out = []
    cur = None
    for line in text.splitlines():
        m = re.match(r'^\s*\*\*\*\s*(ERROR|WARNING|NOTE)(?! CATEGORY)\s*:?\s*(.*)$', line, re.I)
        if m:
            cur = [m.group(1).upper(), [m.group(2).strip()]]
            out.append(cur)
        elif cur is not None:
            s = line.strip()
            if not s or s.startswith('*') or s.startswith('LINE IMAGE') or re.match(r'^-{5,}', s):
                cur = None          # blank line or input echo ends the message
            elif len(cur[1]) < 8:
                cur[1].append(s)
    return [(k, ' '.join(t).strip()) for k, t in out]


# warnings that carry no diagnostic value on their own (shown only as a count)
BENIGN = re.compile(r'ADJUSTED NODES WITH VERY SMALL ADJUSTMENTS WERE NOT PRINTED'
                    r'|OUTPUT REQUEST .* IS NOT AVAILABLE|IS NOT (A )?VALID .*OUTPUT|OUTPUT REQUEST \w+ HAS BEEN REMOVED'
                    r'|DEGREE OF FREEDOM \d+ (IS NOT ACTIVE|AND AT LEAST ONE OF THE ROTATIONAL)'
                    r'|THE REQUEST FOR \w+ OUTPUT WILL BE IGNORED|WILL BE IGNORED FOR ELEMENT'
                    r'|THE PARAMETER FREQUENCY IS DEPRECATED|THE PARAMETER HOURGLASS ON THE \*SECTION CONTROLS'
                    r'|THE OPTION \*BOUNDARY,TYPE=DISPLACEMENT HAS BEEN USED|There (is|are) \d+ warning messages? in the data'
                    r'|PATH BASED TRACKING CANNOT BE USED WITH ANALYTICAL RIGID|PATH BASED TRACKING IS DEFINED IN CONTACT PAIR'
                    r'|THERE IS ZERO (MOMENT|FORCE|FLUX) EVERYWHERE IN THE MODEL', re.I)


def split_benign(groups):
    keep = [g for g in groups if not BENIGN.search(g[1])]
    return keep, len(groups) - len(keep)


def group(msgs):
    """dedupe messages that differ only in numbers -> list of (count, first_text)"""
    seen = {}
    order = []
    for t in msgs:
        key = re.sub(r'[-+]?\d+\.?\d*(?:[eEdD][-+]?\d+)?', '#', t)[:160]
        if key not in seen:
            seen[key] = [0, t]
            order.append(key)
        seen[key][0] += 1
    return [(seen[k][0], seen[k][1]) for k in order]


def parse_sta(text):
    info = {'exists': bool(text), 'completed': SUCCESS in text, 'notCompleted': NOT_DONE in text, 'explicit': False,
            'increments': 0, 'cutbacks': 0, 'steps': 0, 'last': None, 'tail': []}
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    info['tail'] = lines[-10:]
    if 'STABLE' in text and 'CRITICAL' in text:
        info['explicit'] = True
        rows = [l for l in lines if re.match(r'^\s*\d+\s+[-+0-9.Ee]+\s+[-+0-9.Ee]+\s+\d\d:\d\d:\d\d', l)]
        info['increments'] = int(rows[-1].split()[0]) if rows else 0
        if rows:
            p = rows[-1].split()
            info['last'] = {'increment': int(p[0]), 'stepTime': p[1], 'totalTime': p[2], 'stableInc': p[4] if len(p) > 4 else ''}
        return info
    steps = set()
    for l in lines:
        m = re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)(U?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)', l)
        if not m:
            continue
        steps.add(m.group(1))
        if m.group(4) == 'U':
            info['cutbacks'] += 1
        else:
            info['increments'] += 1
        info['last'] = {'step': int(m.group(1)), 'inc': int(m.group(2)), 'att': int(m.group(3)),
                        'converged': m.group(4) != 'U', 'totalTime': m.group(8), 'stepTime': m.group(9),
                        'incTime': m.group(10)}
    info['steps'] = len(steps)
    return info


def last_residuals(msg_text, n=14):
    """the last block of convergence information in the .msg (where the solver is struggling)"""
    keys = ('LARGEST RESIDUAL', 'LARGEST INCREMENT', 'LARGEST CORRECTION', 'AVERAGE FORCE', 'AVERAGE MOMENT',
            'CONVERGENCE CHECKS', 'SEVERE DISCONTINUIT', 'INCREMENT ', 'OVERCLOSURE', 'CONTACT PRESSURE ERROR')
    hits = [l.strip() for l in msg_text.splitlines() if any(k in l for k in keys)]
    return hits[-n:]


def load_hints(extra_path=None):
    hints = []
    only = os.environ.get('ABQ_HINTS_FILE')          # a candidate hint table replaces the built-in one (used by the optimizer)
    paths = (only,) if only and os.path.isfile(only) else (os.path.join(HERE, 'hints.json'), extra_path, os.environ.get('ABQ_HINTS'))
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    hints.extend(json.load(f))
            except (ValueError, OSError):
                pass
    return hints


def match_hints(texts, hints, limit=6):
    out = []
    blob = '\n'.join(texts)
    for h in hints:
        try:
            if re.search(h['pattern'], blob, re.I | re.S):
                out.append(h['hint'])
        except re.error:
            continue
        if len(out) >= limit:
            break
    return out


def diagnose(job_dir, job, log_text=''):
    """-> dict describing the state of a job from its files."""
    base = os.path.join(job_dir, job)
    sta = parse_sta(read_text(base + '.sta'))
    dat = read_text(base + '.dat')
    msg = read_text(base + '.msg')
    log = log_text or read_text(base + '.log') or read_text(base + '.abqlog')
    errs, warns = [], []
    for src, text in (('dat', dat), ('msg', msg), ('log', log)):
        for kind, t in messages(text):
            if kind == 'ERROR':
                errs.append(t)
            elif kind == 'WARNING':
                warns.append(t)
    for l in log.splitlines():
        if re.search(r'Abaqus Error|exited with error|Licensing error|Error in job|unable to check ?out', l, re.I):
            errs.append(l.strip())
    d = {'job': job, 'dir': job_dir, 'sta': sta, 'errors': group(errs), 'warnings': group(warns),
         'odb': os.path.isfile(base + '.odb'), 'running': os.path.isfile(base + '.lck'),
         'residuals': last_residuals(msg) if (errs or sta['notCompleted'] or sta['cutbacks']) else []}
    if sta['completed'] and not errs:
        d['state'] = 'completed'
    elif errs or sta['notCompleted']:
        d['state'] = 'failed'
    elif d['running']:
        d['state'] = 'running'
    elif not sta['exists'] and os.path.isfile(base + '.dat'):
        d['state'] = 'checked'      # datacheck only
    else:
        d['state'] = 'unknown'
    return d
