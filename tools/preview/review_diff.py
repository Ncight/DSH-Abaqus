# -*- coding: utf-8 -*-
"""What changed since the last approval.

When a file is approved, a gzip copy of exactly what was approved is kept in <workspace>/.abq/review/approved/.
The gate (abq run) and the panel compare a file with
  * its own last approved copy (the file was changed after approval), or
  * the approved file in the same folder closest to it in size (a new load case built from an approved one, a
    restart branch next to the approved branches),
and list the lines that differ, so a human sees at once that, say, only the load line changed.
"""
import gzip
import math
import os
import re

MAX_COPY_MB = 300


def _abq_dir(wbase):
    return os.path.join(wbase, '.abq')


def copy_path(wbase, rel):
    safe = re.sub(r'[\\/:*?"<>|]+', '__', rel.replace('\\', '/'))
    return os.path.join(_abq_dir(wbase), 'review', 'approved', safe + '.gz')


def save_copy(wbase, rel, src):
    """keep what was just approved (skipped for very large files)"""
    try:
        if os.path.getsize(src) > MAX_COPY_MB * (1 << 20):
            return None
        dst = copy_path(wbase, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + '.tmp'
        with open(src, 'rb') as fi, gzip.open(tmp, 'wb', compresslevel=3) as fo:
            while True:
                b = fi.read(1 << 20)
                if not b:
                    break
                fo.write(b)
        os.replace(tmp, dst)
        return dst
    except OSError:
        return None


def _lines(path):
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rb') as f:
        return f.read().decode('latin-1').splitlines()


def diff_lines(old, new, show=30):
    """common head and tail removed; what is left on each side is the change"""
    n0, n1 = len(old), len(new)
    i = 0
    while i < n0 and i < n1 and old[i] == new[i]:
        i += 1
    j = 0
    while j < n0 - i and j < n1 - i and old[n0 - 1 - j] == new[n1 - 1 - j]:
        j += 1
    rem, add = old[i:n0 - j], new[i:n1 - j]
    return {'same': not rem and not add, 'line': i + 1, 'n_removed': len(rem), 'n_added': len(add),
            'removed': rem[:show], 'added': add[:show]}


def _size(wbase, rel, cp):
    """size of an approved file: the file itself if it is still there, else the gzip copy's stored length"""
    try:
        return os.path.getsize(os.path.join(wbase, rel.replace('/', os.sep)))
    except OSError:
        try:
            with open(cp, 'rb') as f:
                f.seek(-4, 2)
                return int.from_bytes(f.read(4), 'little')
        except OSError:
            return 0


def find_base(wbase, rel, reviews, size=None):
    """-> (label, copy path, decidedAt) or None.  reviews: the 'files' dict of review.json.
    Without an approved version of its own, a file is compared with the approved file in the same folder that is
    closest to it in size (a restart branch with the other branches, a load case with the other load cases), the
    latest approved among those of about the same size."""
    own = reviews.get(rel)
    if own and own.get('status') == 'approved' and os.path.isfile(copy_path(wbase, rel)):
        return ('上次批准的版本', copy_path(wbase, rel), own.get('decidedAt', ''))
    folder = os.path.dirname(rel.replace('\\', '/'))
    cands = []
    for r, e in reviews.items():
        if r == rel or e.get('status') != 'approved' or os.path.dirname(r.replace('\\', '/')) != folder:
            continue
        cp = copy_path(wbase, r)
        if os.path.isfile(cp):
            near = round(abs(math.log((_size(wbase, r, cp) + 1.0) / (size + 1.0))), 1) if size is not None else 0.0
            cands.append((near, e.get('decidedAt', ''), r, cp))
    if not cands:
        return None
    cands.sort(key=lambda c: c[1], reverse=True)            # latest approved first ...
    near, when, r, cp = min(cands, key=lambda c: c[0])      # ... and min() keeps the first of equals
    return ('已批准的 %s' % os.path.basename(r), cp, when)


def compare(wbase, rel, path, reviews, show=30):
    """-> dict(base, decidedAt, same, line, n_removed, n_added, removed, added) or None"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    b = find_base(wbase, rel, reviews, size)
    if not b:
        return None
    try:
        d = diff_lines(_lines(b[1]), _lines(path), show)
    except (OSError, EOFError):
        return None
    d['base'], d['decidedAt'] = b[0], b[2]
    return d


def solve_rel(rel_cdb):
    """a MAPDL job's solve segment, next to its CDB: <job>.cdb -> <job>.solve.dat"""
    return rel_cdb[:-4] + '.solve.dat' if rel_cdb.lower().endswith('.cdb') else rel_cdb + '.solve.dat'


def save_mapdl(wbase, rel_cdb, cdb_path, solve_path):
    """keep what was approved for a MAPDL job: the CDB and the solve segment"""
    save_copy(wbase, rel_cdb, cdb_path)
    if solve_path and os.path.isfile(solve_path):
        save_copy(wbase, solve_rel(rel_cdb), solve_path)


def compare_mapdl(wbase, rel_cdb, cdb_path, solve_path, reviews, show=30):
    """-> {'cdb': diff or None, 'solve': diff or None}.  The approval of a MAPDL job covers both files; the load cases
    of one model usually differ only in the solve segment."""
    out = {'cdb': compare(wbase, rel_cdb, cdb_path, reviews, show), 'solve': None}
    if solve_path and os.path.isfile(solve_path):
        by_solve = {solve_rel(r): e for r, e in reviews.items() if r.lower().endswith('.cdb')}
        out['solve'] = compare(wbase, solve_rel(rel_cdb), solve_path, by_solve, show)
    return out


def text(d, limit=12):
    """a few readable lines for the gate message"""
    if not d:
        return []
    if d['same']:
        return ['与%s（%s）内容完全相同。' % (d['base'], d['decidedAt'].replace('T', ' '))]
    out = ['与%s（%s）相比，从第 %d 行起有 %d 行被替换为 %d 行:' % (
        d['base'], d['decidedAt'].replace('T', ' '), d['line'], d['n_removed'], d['n_added'])]
    for s in d['removed'][:limit]:
        out.append('  - ' + s[:160])
    for s in d['added'][:limit]:
        out.append('  + ' + s[:160])
    if d['n_removed'] > limit or d['n_added'] > limit:
        out.append('  … （差异较多，只列前 %d 行）' % limit)
    return out
