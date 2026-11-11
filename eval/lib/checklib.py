# -*- coding: utf-8 -*-
"""Shared helpers for the DSH-Abaqus benchmark checkers (Python 3, standard library only).

A checker is a small script `eval/tasks/<id>/check.py`:

    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
    import checklib as cl

    def body(c):                      # c is a cl.Checker
        c.job_completed()             # check 1 (critical)
        ex = c.extract(vars='U,RF')   # extractor JSON as a dict, or None (the reason is kept in c.odb_error)
        c.check_rel('reaction', cl.vec_sum_mag(ex, 'RF'), 1000.0, 0.01)
        c.load_result({'reaction_force_N': 'number'})                 # check `result_json` (critical)
        c.report('reaction_force_N', [('|sum RF|', cl.vec_sum_mag(ex, 'RF'))], 0.03, critical=True)

    cl.main(__file__, body)

Contract (see the benchmark spec): `py check.py <workdir>` prints ONE JSON object
    {"task", "pass", "score", "checks": [{"name", "pass", "critical", "got", "expected", "tol", "note"}], "error"}
and exits 0 unless the checker itself crashed.  `pass` = every critical check passed, `score` = fraction of all
checks passed.  Nothing in here raises for bad agent output: a missing ODB, an extractor failure or a broken
result.json all turn into failed checks with a note.

All ODB numbers come from tools/abq/odb_extract.py (run with `abaqus python`); values written by the agent are
only ever compared against them, never trusted.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import traceback

LIB_DIR = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = os.path.abspath(os.path.join(LIB_DIR, '..'))
ROOT = os.path.abspath(os.path.join(EVAL_DIR, '..'))
ABAQUS_BAT = os.environ.get('ABAQUS_BAT') or r'D:\SIMULIA\Commands\abaqus.bat'
EXTRACTOR = os.environ.get('DSH_ODB_EXTRACT') or os.path.join(ROOT, 'tools', 'abq', 'odb_extract.py')
EXTRACT_TIMEOUT_S = int(os.environ.get('DSH_EXTRACT_TIMEOUT', '600'))
# Abaqus/Standard and Abaqus/Explicit 2021 both end a successful run with this line in <job>.sta (verified).
SUCCESS_LINE = 'THE ANALYSIS HAS COMPLETED SUCCESSFULLY'


# ----------------------------------------------------------------------------------------------- small utilities
def num(x):
    """float(x) if x is a finite real number (bool and strings are rejected), else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def read_text(path):
    """File content as text whatever the encoding (UTF-8 with/without BOM, UTF-16 from PowerShell, GBK from Abaqus)."""
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode('utf-8', 'replace')
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', 'replace')
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1')


def rel_err(got, expected):
    got, expected = num(got), num(expected)
    if got is None or expected is None or expected == 0.0:
        return None
    return (got - expected) / abs(expected)


def _clean(v):
    """make a value JSON-safe and compact"""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return float('%.6g' % v)
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v[:20]]
    if isinstance(v, dict):
        return dict((str(k)[:80], _clean(x)) for k, x in list(v.items())[:40])
    if isinstance(v, str) and len(v) > 300:
        return v[:300] + '...'
    return v


# --------------------------------------------------------------------------- accessors for the extractor's JSON
def _fields(ex, base):
    """all field summaries whose name is `base` (also 'CPRESS   <surface pair>' style names), without errors"""
    if not isinstance(ex, dict):
        return []
    out = []
    for name, f in (ex.get('fields') or {}).items():
        if isinstance(f, dict) and 'error' not in f and name.split(' ')[0] == base:
            out.append(f)
    return out


def has_field(ex, base):
    return bool(_fields(ex, base))


def available_fields(ex):
    if not isinstance(ex, dict):
        return []
    return list((ex.get('frame') or {}).get('available') or [])


def max_mag(ex, base='U'):
    """largest vector magnitude of a nodal vector field (invariant to the orientation of the model)"""
    vals = [num((f.get('maxMagnitude') or {}).get('value')) for f in _fields(ex, base)]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def comp_range(ex, base):
    """{component label: (max, min)} of a field"""
    out = {}
    for f in _fields(ex, base):
        for lab, c in (f.get('components') or {}).items():
            mx, mn = num(c.get('max')), num(c.get('min'))
            if mx is None or mn is None:
                continue
            if lab in out:
                mx, mn = max(mx, out[lab][0]), min(mn, out[lab][1])
            out[lab] = (mx, mn)
    return out


def comp_absmax(ex, base):
    """largest absolute value of any single component of a field"""
    r = comp_range(ex, base)
    if not r:
        return None
    return max(max(abs(mx), abs(mn)) for mx, mn in r.values())


def dominant_axis(ex, base='U'):
    """index (1, 2, 3) of the component with the largest absolute value, e.g. the pressing direction of a contact"""
    r = comp_range(ex, base)
    best = None
    for lab, (mx, mn) in r.items():
        m = re.search(r'(\d)$', lab)
        if not m:
            continue
        v = max(abs(mx), abs(mn))
        if best is None or v > best[0]:
            best = (v, int(m.group(1)))
    return best[1] if best else None


def sums(ex, base='RF'):
    """{axis index: {'sum': net, 'pos': sum of positive nodal values, 'neg': sum of negative nodal values}}"""
    out = {}
    for f in _fields(ex, base):
        s, sp, sn = f.get('sum') or {}, f.get('sumPos') or {}, f.get('sumNeg') or {}
        for lab in s:
            m = re.search(r'(\d)$', lab)
            if not m:
                continue
            d = out.setdefault(int(m.group(1)), {'sum': 0.0, 'pos': 0.0, 'neg': 0.0})
            d['sum'] += num(s.get(lab)) or 0.0
            d['pos'] += num(sp.get(lab)) or 0.0
            d['neg'] += num(sn.get(lab)) or 0.0
    return out


def vec_sum_mag(ex, base='RF'):
    """|sum over all nodes of a nodal vector| = magnitude of the net reaction / net applied load"""
    s = sums(ex, base)
    if not s:
        return None
    return math.sqrt(sum(d['sum'] ** 2 for d in s.values()))


def invariant(ex, base, which, stat, nodal=True):
    """max or min (stat) of a tensor invariant (which = mises | maxPrincipal | minPrincipal | pressure | tresca).

    nodal=True  -> values extrapolated to the element nodes (what a contour plot shows on a surface);
                   falls back to the integration-point values when the extractor could not extrapolate.
    nodal=False -> integration-point values.
    """
    best = None
    for f in _fields(ex, base):
        v = None
        if nodal:
            v = num(((f.get('elementNodal') or {}).get(which) or {}).get(stat))
        if v is None:
            v = num(((f.get('invariants') or {}).get(which) or {}).get(stat))
        if v is None:
            continue
        if best is None or (stat == 'max' and v > best) or (stat == 'min' and v < best):
            best = v
    return best


def scalar(ex, base, stat='max'):
    """max / min of a scalar field (PEEQ, CPRESS ...) over all fields with that base name"""
    best = None
    for f in _fields(ex, base):
        v = num(f.get(stat))
        if v is None:
            continue
        if best is None or (stat == 'max' and v > best) or (stat == 'min' and v < best):
            best = v
    return best


def element_types(ex):
    if not isinstance(ex, dict):
        return {}
    return dict(ex.get('elementTypes') or {})


def steps(ex):
    if not isinstance(ex, dict):
        return []
    return list(ex.get('steps') or [])


def frequencies(ex):
    """natural frequencies (cycles/time) of the last *FREQUENCY step, ordered by mode number"""
    if not isinstance(ex, dict):
        return []
    rows = [r for r in (ex.get('frequencies') or []) if num(r.get('frequency')) is not None]
    if not rows:
        return []
    last = rows[-1].get('step')
    rows = sorted((r for r in rows if r.get('step') == last), key=lambda r: r.get('mode', 0))
    return [float(r['frequency']) for r in rows]


def distinct_frequencies(freqs, merge_rel=0.005, rigid_below=0.0):
    """ascending list without repeats: values within merge_rel of the previous one are merged (a 3-D model of a
    line-like structure gives every transverse mode twice), values <= rigid_below (rigid-body modes) are dropped"""
    out = []
    for f in sorted(freqs):
        if f <= rigid_below:
            continue
        if out and abs(f - out[-1]) <= merge_rel * out[-1]:
            continue
        out.append(f)
    return out


def frame_is_general_step(ex):
    """True when the frame the extractor summarised belongs to a general (non-eigenvalue) analysis step"""
    try:
        name = ex['frame']['step']
        for s in ex['steps']:
            if s.get('name') == name:
                proc = str(s.get('procedure', '')).upper()
                return not ('FREQUENCY' in proc or 'BUCKLE' in proc or 'COMPLEX' in proc)
    except Exception:
        pass
    return False


def eigenvalues(ex):
    """eigenvalues of the last *BUCKLE step, ordered by mode number"""
    if not isinstance(ex, dict):
        return []
    rows = [r for r in (ex.get('eigenvalues') or []) if num(r.get('value')) is not None]
    if not rows:
        return []
    last = rows[-1].get('step')
    rows = sorted((r for r in rows if r.get('step') == last), key=lambda r: r.get('mode', 0))
    return [float(r['value']) for r in rows]


def probe_series(ex, index, var, column):
    """time series [(t, value)] of `column` (e.g. 'S11') of variable `var` at probe number `index`; [] if absent"""
    try:
        s = ex['probes'][index]['series'][var]
        ci = s['columns'].index(column)
        out = []
        for row in s['rows']:
            t, v = num(row[0]), num(row[ci])
            if t is not None and v is not None:
                out.append((t, v))
        return out
    except Exception:
        return []


def probe_location(ex, index):
    """where the probe actually sampled: {'node': {...}, 'element': {...}} (subset that exists)"""
    try:
        p = ex['probes'][index]
        return dict((k, p[k]) for k in ('node', 'element') if k in p)
    except Exception:
        return {}


def crossing_time(series, level):
    """first time |value| reaches `level`, linearly interpolated between frames; None if it never does"""
    prev = None
    for t, v in series:
        a = abs(v)
        if a >= level:
            if prev is None or prev[1] >= level:
                return t
            t0, a0 = prev
            if a == a0:
                return t
            return t0 + (level - a0) * (t - t0) / (a - a0)
        prev = (t, a)
    return None


def closest(cands, expected):
    """(label, value) of the candidate [(label, value), ...] nearest to `expected`; ('', None) when there is none.

    For a quantity that can legitimately be read from the ODB in more than one way (reaction carried by one
    reference node, or spread over a face ...).  Only use it when every wrong model is far away from `expected`
    with ALL candidates."""
    best = None
    for lab, v in cands:
        v = num(v)
        if v is None:
            continue
        err = abs(v - expected) / abs(expected) if expected else abs(v)
        if best is None or err < best[0]:
            best = (err, lab, v)
    return (best[1], best[2]) if best else ('', None)


def step_start(ex, name=None):
    """total time at the start of step `name` (default: the step of the frame the extractor summarised, i.e. the last
    general step); None when it cannot be established.  Probe series carry total time, so this is what turns them
    into step time when an analysis has earlier steps."""
    try:
        name = name or ex['frame']['step']
        for s in ex['steps']:
            if s.get('name') == name:
                return float(s.get('totalTime') or 0.0)
    except Exception:
        pass
    return None


def _series_layout(ex, name=None):
    """Where the frames of step `name` (default: the summarised step) sit in a probe series of this extraction.

    A probe series has one row per frame of every step EXCEPT eigenvalue steps (frequency / buckle), whose frames
    are mode shapes; the extractor leaves those out.  -> (offset of the first row, number of rows, total time at the
    start of the step, number of rows of the whole series, total time at the start of the next general step or None)
    """
    name = name or ex['frame']['step']
    st = steps(ex)
    eigen = set(r.get('step') for key in ('frequencies', 'eigenvalues') for r in (ex.get(key) or []))
    general = [s for s in st if s.get('name') not in eigen]
    i = [s.get('name') for s in general].index(name)
    nxt = general[i + 1].get('totalTime') if i + 1 < len(general) else None
    return (sum(int(s['nFrames']) for s in general[:i]), int(general[i]['nFrames']),
            float(general[i].get('totalTime') or 0.0), sum(int(s['nFrames']) for s in general),
            float(nxt) if nxt is not None else None)


def step_rows(ex, series, name=None):
    """the rows of a probe series [(total time, value)] that belong to step `name` (default: the summarised step),
    with the time counted from the start of that step -> [(step time, value)]; [] when the step is unknown.
    Uses the frame counts of the steps when the series has one row per frame, otherwise the step's time window."""
    try:
        off, n, t0, total, t1 = _series_layout(ex, name)
    except Exception:
        return []
    if len(series) == total:
        return [(t - t0, v) for t, v in series[off:off + n]]
    return [(t - t0, v) for t, v in series if t >= t0 - 1e-9 and (t1 is None or t <= t1 + 1e-9)]


def frame_at_fraction(ex, series, frac):
    """Index, within the step the extractor summarised, of the frame whose step time is closest to `frac` of the
    time reached at the end of that step (first and last frame excluded) -> (index, note) or (None, reason).

    `series` is a probe series [(total time, value)] of the same extraction: it has one row per frame of every
    general step, which is where the frame times come from.  Without usable times the frames are assumed equally
    spaced."""
    try:
        name = ex['frame']['step']
        off, n, t0, total, _t1 = _series_layout(ex, name)
    except Exception:
        return None, 'the step of the summarised frame could not be identified'
    if n < 4:
        return None, 'step "%s" has only %d frames in the ODB - too few to show how the response develops' % (name, n)
    k, how = None, ''
    if len(series) == total:
        times = [t - t0 for t, _v in series[off:off + n]]
        if times[-1] > times[0]:
            target = frac * times[-1]
            k = min(range(1, n - 1), key=lambda j: abs(times[j] - target))
            how = 'frame %d of %d in step "%s", at %.3g of the final step time' % (k, n - 1, name, times[k] / times[-1])
    if k is None:
        k = min(max(int(round(frac * (n - 1))), 1), n - 2)
        how = 'frame %d of %d in step "%s" (frame times not available: by index)' % (k, n - 1, name)
    return k, how


def _solve_linear(a, b):
    """solve the small dense system a x = b by Gaussian elimination with partial pivoting; None if singular"""
    n = len(b)
    m = [list(a[i]) + [b[i]] for i in range(n)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(m[i][k]))
        if abs(m[p][k]) < 1e-300:
            return None
        m[k], m[p] = m[p], m[k]
        for i in range(k + 1, n):
            f = m[i][k] / m[k][k]
            if f:
                for j in range(k, n + 1):
                    m[i][j] -= f * m[k][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def free_decay_fit(series):
    """Free decay that follows the largest |value| of a time series [(t, v)] (static deflection, then release).

    The samples from the release on are normalised by the largest value and fitted, in the least-squares sense, with
        y = exp(-a tau) (A cos(w tau) + B sin(w tau)),   tau = time since the release
    (Gauss-Newton started from the zero crossings and the half-cycle peaks; every sample is used, so the result does
    not depend on how well the peaks are sampled and a small ripple of higher modes averages out).

    -> dict: ok, reason (when not ok), peak (largest |v|), sign, t_release, duration, max_interval, n_samples,
             n_crossings, and when ok: freq_d, freq_n (Hz), zeta, decay_rate (a), log_dec, rms (residual / signal),
             fitted (False when only the starting estimates could be returned).
    """
    out = {'ok': False, 'reason': '', 'peak': None}
    pts = []
    for t, v in sorted((float(t), float(v)) for t, v in series):
        if pts and abs(t - pts[-1][0]) <= 1e-12 * max(1.0, abs(t)):
            pts[-1] = (t, v)                      # two frames at the same total time (step boundary): keep the later
        else:
            pts.append((t, v))
    if len(pts) < 8:
        out['reason'] = 'only %d samples in the time series' % len(pts)
        return out
    k_peak = max(range(len(pts)), key=lambda i: abs(pts[i][1]))
    peak = abs(pts[k_peak][1])
    out['peak'] = peak
    if peak <= 0.0:
        out['reason'] = 'the series is identically zero'
        return out
    sgn = 1.0 if pts[k_peak][1] > 0 else -1.0
    y_all = [sgn * v / peak for _t, v in pts]
    k0 = next(i for i, y in enumerate(y_all) if y >= 0.999)      # the first time the largest deflection is reached
    while k0 + 1 < len(pts) and y_all[k0 + 1] >= 0.999:          # ... and the end of the plateau, if it is held
        k0 += 1
    t0 = pts[k0][0]
    tau = [t - t0 for t, _v in pts[k0:]]
    y = y_all[k0:]
    n = len(y)
    out.update({'sign': sgn, 't_release': t0, 'duration': tau[-1], 'n_samples': n,
                'max_interval': max(tau[i + 1] - tau[i] for i in range(n - 1)) if n > 1 else None})
    if n < 8:
        out['reason'] = ('the value stays at its largest magnitude until (almost) the end of the series - only %d '
                         'sample(s) follow: the load was never removed, or nothing was computed after it' % (n - 1))
        return out
    cross = []
    for i in range(n - 1):
        if y[i] == 0.0 or y[i] * y[i + 1] < 0.0:
            cross.append((tau[i] + (tau[i + 1] - tau[i]) * y[i] / (y[i] - y[i + 1]), i))
    out['n_crossings'] = len(cross)
    if len(cross) < 3:
        out['reason'] = ('no oscillation about zero after the largest deflection (%d zero crossings in %.4g time '
                         'units; last value %.3g of the largest)' % (len(cross), tau[-1], y[-1]))
        return out
    w = math.pi * (len(cross) - 1) / (cross[-1][0] - cross[0][0])
    peaks = []                                    # (tau, |y|) of the extreme sample of every half cycle
    bounds = [0] + [i + 1 for _tz, i in cross] + [n]
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        if hi > lo:
            j = max(range(lo, hi), key=lambda i: abs(y[i]))
            if abs(y[j]) > 0.0:
                peaks.append((tau[j], abs(y[j])))
    a = 0.0
    if len(peaks) >= 3:
        xs = [p[0] for p in peaks[:-1]]           # the last half cycle is usually cut off
        ys = [math.log(p[1]) for p in peaks[:-1]]
        xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - xm) ** 2 for x in xs)
        if sxx > 0.0:
            a = -sum((x - xm) * (v - ym) for x, v in zip(xs, ys)) / sxx

    def residual(par):
        A, B, aa, ww = par
        tot = 0.0
        for ti, yi in zip(tau, y):
            tot += (yi - math.exp(-aa * ti) * (A * math.cos(ww * ti) + B * math.sin(ww * ti))) ** 2
        return tot

    par = [1.0, a / w, a, w]
    fitted = True
    try:
        best = residual(par)
        for _it in range(60):
            A, B, aa, ww = par
            jtj = [[0.0] * 4 for _ in range(4)]
            jtr = [0.0] * 4
            for ti, yi in zip(tau, y):
                e = math.exp(-aa * ti)
                cs, sn = math.cos(ww * ti), math.sin(ww * ti)
                f = e * (A * cs + B * sn)
                g = (e * cs, e * sn, -ti * f, ti * e * (-A * sn + B * cs))
                r = yi - f
                for p in range(4):
                    jtr[p] += g[p] * r
                    for q in range(p, 4):
                        jtj[p][q] += g[p] * g[q]
            for p in range(4):
                for q in range(p):
                    jtj[p][q] = jtj[q][p]
            step = _solve_linear(jtj, jtr)
            if step is None:
                break
            lam, improved = 1.0, False
            while lam > 1e-4:
                trial = [par[p] + lam * step[p] for p in range(4)]
                if abs(trial[3] - w) < 0.2 * w:                  # stay on the main lobe of the starting frequency
                    rt = residual(trial)
                    if rt < best:
                        improved = True
                        break
                lam *= 0.5
            if not improved:
                break
            change = max(abs(lam * step[2]) / max(abs(trial[2]), 1e-3 * trial[3]), abs(lam * step[3]) / trial[3])
            par, best = trial, rt
            if change < 1e-10:
                break
        if not all(math.isfinite(v) for v in par) or par[3] <= 0.0:
            raise ValueError('fit diverged')
    except Exception:
        par, fitted = [1.0, a / w, a, w], False
        best = residual(par)
    A, B, a, w = par
    wn = math.sqrt(a * a + w * w)
    zeta = a / wn
    out.update({'ok': True, 'fitted': fitted, 'freq_d': w / (2 * math.pi), 'freq_n': wn / (2 * math.pi),
                'zeta': zeta, 'decay_rate': a,
                'log_dec': 2 * math.pi * zeta / math.sqrt(1 - zeta * zeta) if abs(zeta) < 1 else None,
                'rms': math.sqrt(best / max(sum(v * v for v in y), 1e-300))})
    return out


# ------------------------------------------------------------------------- reading loads back from the job's INP
def inp_rayleigh(inp_path):
    """Rayleigh coefficients given on the *DAMPING keywords of an input file -> ([(alpha, beta), ...], note)"""
    if not os.path.isfile(inp_path):
        return [], '%s not found' % os.path.basename(inp_path)
    try:
        text = read_text(inp_path)
    except Exception as e:
        return [], 'cannot read %s: %s' % (os.path.basename(inp_path), e)
    found = []
    for kind, a, b in _inp_records(text):
        if kind == 'kw' and a == 'DAMPING':
            try:
                pair = (float((b.get('ALPHA') or '0').upper().replace('D', 'E')),
                        float((b.get('BETA') or '0').upper().replace('D', 'E')))
            except ValueError:
                continue
            if pair not in found:
                found.append(pair)
    return found, ('%d *DAMPING definition(s) in %s' % (len(found), os.path.basename(inp_path)) if found
                   else 'no *DAMPING keyword with ALPHA/BETA in %s' % os.path.basename(inp_path))


def _inp_records(text):
    """yield ('kw', NAME, {param: value}) and ('data', [fields]) records of an Abaqus input file"""
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('**'):
            continue
        if pending is not None:
            line = pending + line
            pending = None
        if line.startswith('*'):
            if line.endswith(','):
                pending = line
                continue
            parts = [x.strip() for x in line[1:].split(',')]
            name = re.sub(r'\s+', ' ', parts[0]).upper()
            params = {}
            for item in parts[1:]:
                if not item:
                    continue
                k, _, v = item.partition('=')
                params[k.strip().upper().replace(' ', '')] = v.strip().strip('"')
            yield 'kw', name, params
        else:
            yield 'data', [x.strip().strip('"') for x in line.rstrip(',').split(',')], None


def _is_int(tok):
    return bool(re.match(r'^[+-]?\d+$', tok))


def buckle_reference_load(inp_path):
    """Resultant of the concentrated loads (*CLOAD) applied in the *BUCKLE step of an input file.

    -> (value, note).  value is None when the load cannot be established from the file with certainty
    (file missing, distributed loads, unresolved node sets, a preload in an earlier step, *INCLUDE ...);
    the note says why.  The value is max over the three translational DOF of |sum(magnitude x nodes)|.
    """
    if not os.path.isfile(inp_path):
        return None, '%s not found' % os.path.basename(inp_path)
    try:
        text = read_text(inp_path)
    except Exception as e:
        return None, 'cannot read %s: %s' % (os.path.basename(inp_path), e)
    sets = {}                  # (scope, NAME) -> node count, or None when it cannot be counted
    inst_part = {}             # INSTANCE -> PART
    part = None                # current *PART
    state = None               # what the following data lines mean
    cur = None
    in_step = False
    step_is_buckle = False
    step_loads = []            # [(token, dof, magnitude)] of the current step
    step_other = []            # other load keywords in the current step
    earlier_loads = False
    result = None
    reasons = []

    def scope():
        return ('part', part) if part else ('asm', None)

    def add_to_set(key, n):
        if n is None or sets.get(key, 0) is None:
            sets[key] = None
        else:
            sets[key] = sets.get(key, 0) + n

    def count(tok):
        tok = tok.strip()
        if _is_int(tok):
            return 1
        if '.' in tok:
            inst, _, rest = tok.partition('.')
            if _is_int(rest):
                return 1
            p = inst_part.get(inst.upper())
            return sets.get((('part', p), rest.upper())) if p else None
        return sets.get((('asm', None), tok.upper()))

    def close_step():
        nonlocal result, earlier_loads
        if step_is_buckle:
            if step_other:
                reasons.append('the buckle step has distributed loads (%s)' % ', '.join(sorted(set(step_other))))
                return
            if not step_loads:
                reasons.append('no *CLOAD in the buckle step')
                return
            if earlier_loads:
                reasons.append('loads in a step before the buckle step (preloaded base state)')
                return
            tot = {1: 0.0, 2: 0.0, 3: 0.0}
            for tok, dof, mag in step_loads:
                n = count(tok)
                if n is None:
                    reasons.append('node set "%s" of a *CLOAD could not be resolved' % tok)
                    return
                if dof in tot:
                    tot[dof] += mag * n
            result = max(abs(v) for v in tot.values())
        elif step_loads or step_other:
            earlier_loads = True

    for kind, a, b in _inp_records(text):
        if kind == 'kw':
            name, params = a, b
            state = None
            if name == 'INCLUDE':
                reasons.append('the input file uses *INCLUDE')
            elif name == 'PART':
                part = (params.get('NAME') or '').upper()
            elif name == 'END PART':
                part = None
            elif name == 'INSTANCE':
                inst_part[(params.get('NAME') or '').upper()] = (params.get('PART') or '').upper()
            elif name == 'NSET':
                cur = (scope(), (params.get('NSET') or '').upper())
                state = 'nset_gen' if 'GENERATE' in params else 'nset'
                sets.setdefault(cur, 0)
            elif name == 'NODE' and params.get('NSET'):
                cur = (scope(), params['NSET'].upper())
                state = 'node'
                sets.setdefault(cur, 0)
            elif name == 'STEP':
                in_step, step_is_buckle, step_loads, step_other = True, False, [], []
            elif name == 'END STEP':
                close_step()
                in_step = False
            elif in_step and name == 'BUCKLE':
                step_is_buckle = True
            elif in_step and name == 'CLOAD':
                state = 'cload'
            elif in_step and name in ('DSLOAD', 'DLOAD'):
                step_other.append('*' + name)
        else:
            f = a
            try:
                if state == 'nset':
                    for tok in f:
                        if not tok:
                            continue
                        if _is_int(tok):
                            add_to_set(cur, 1)
                        else:
                            add_to_set(cur, sets.get((cur[0], tok.upper())))
                elif state == 'nset_gen':
                    first, last = int(f[0]), int(f[1])
                    inc = int(f[2]) if len(f) > 2 and f[2] else 1
                    add_to_set(cur, (last - first) // inc + 1)
                elif state == 'node':
                    add_to_set(cur, 1)
                elif state == 'cload' and len(f) >= 3:
                    step_loads.append((f[0], int(f[1]), float(f[2].upper().replace('D', 'E'))))
            except Exception:
                if state in ('nset', 'nset_gen', 'node'):
                    sets[cur] = None
                elif state == 'cload':
                    reasons.append('unreadable *CLOAD line: %s' % ','.join(f))
    if result is None:
        return None, '; '.join(reasons) or 'no *BUCKLE step found in the input file'
    if reasons:
        return None, '; '.join(reasons)
    return result, 'sum of the *CLOAD magnitudes in the *BUCKLE step of %s' % os.path.basename(inp_path)


# ------------------------------------------------------------------------------------------------- the checker
class Checker(object):
    def __init__(self, check_file, workdir):
        self.task_dir = os.path.dirname(os.path.abspath(check_file))
        with open(os.path.join(self.task_dir, 'meta.json'), encoding='utf-8') as f:
            self.meta = json.load(f)
        self.id = self.meta['id']
        self.job = self.meta['job']
        self.exp = self.meta.get('expected', {})
        self.tol = self.meta.get('tolerances', {})
        self.workdir = os.path.abspath(workdir)
        self.checks = []
        self.error = None
        self.odb_error = None        # why ODB numbers are unavailable (None = they are available)
        self.result = {}             # parsed result.json (only the valid required keys)
        self.result_raw = None
        self._n_extract = 0

    # ---- paths
    def path(self, name):
        return os.path.join(self.workdir, name)

    @property
    def odb_path(self):
        return self.path(self.job + '.odb')

    # ---- recording
    def add(self, name, ok, critical=True, got=None, expected=None, tol=None, note=''):
        self.checks.append({'name': name, 'pass': bool(ok), 'critical': bool(critical), 'got': _clean(got),
                            'expected': _clean(expected), 'tol': _clean(tol), 'note': note})
        return bool(ok)

    def _missing_note(self, note):
        why = self.odb_error or 'value not available in the ODB'
        return (note + ' | ' if note else '') + 'NOT EVALUATED: ' + why

    def check_rel(self, name, got, expected, tol, critical=True, note='', use_abs=False):
        """pass if |got - expected| <= tol * |expected|  (use_abs: compare magnitudes, ignoring the sign)"""
        g = num(got)
        if g is None:
            return self.add(name, False, critical, None, expected, tol, self._missing_note(note))
        a, b = (abs(g), abs(expected)) if use_abs else (g, expected)
        err = (a - b) / abs(b) if b else float('inf')
        return self.add(name, abs(err) <= tol, critical, g, expected, tol,
                        (note + ' | ' if note else '') + 'error %+.2f%%' % (100.0 * err))

    def check_range(self, name, got, lo, hi, critical=True, note=''):
        g = num(got)
        if g is None:
            return self.add(name, False, critical, None, [lo, hi], 'range', self._missing_note(note))
        return self.add(name, lo <= g <= hi, critical, g, [lo, hi], 'range', note)

    def check_max(self, name, got, limit, critical=True, note=''):
        """pass if got <= limit"""
        g = num(got)
        if g is None:
            return self.add(name, False, critical, None, limit, 'upper bound', self._missing_note(note))
        return self.add(name, g <= limit, critical, g, limit, 'upper bound', note)

    def check_min(self, name, got, limit, critical=True, note=''):
        """pass if got >= limit"""
        g = num(got)
        if g is None:
            return self.add(name, False, critical, None, limit, 'lower bound', self._missing_note(note))
        return self.add(name, g >= limit, critical, g, limit, 'lower bound', note)

    # ---- check 1: the job ran to completion
    def job_completed(self):
        odb_ok = os.path.isfile(self.odb_path) and os.path.getsize(self.odb_path) > 0
        sta = self.path(self.job + '.sta')
        sta_ok = False
        sta_state = 'missing'
        if os.path.isfile(sta):
            try:
                sta_ok = SUCCESS_LINE in read_text(sta).upper()
                sta_state = 'completed' if sta_ok else 'not completed'
            except Exception as e:
                sta_state = 'unreadable (%s)' % e
        note = ''
        if not odb_ok:
            self.odb_error = '%s.odb not found in the work directory' % self.job
            others = []
            try:
                for base, _dirs, files in os.walk(self.workdir):
                    for fn in files:
                        if fn.lower().endswith('.odb'):
                            others.append(os.path.relpath(os.path.join(base, fn), self.workdir))
                    if len(others) > 8:
                        break
            except Exception:
                pass
            note = '%s.odb must be in the work directory itself' % self.job
            if others:
                note += '; other ODB files present: ' + ', '.join(others[:8])
        elif not sta_ok:
            note = '%s.sta does not contain "%s"' % (self.job, SUCCESS_LINE)
        return self.add('job_completed', odb_ok and sta_ok, True,
                        'odb=%s, sta=%s' % ('present' if odb_ok else 'missing', sta_state),
                        'odb present, sta completed', None, note)

    # ---- ODB numbers through the shared extractor
    def extract(self, vars=None, probes=(), series=None, step=None, frame=None, history=False):
        """Run tools/abq/odb_extract.py on <job>.odb -> dict, or None (reason in self.odb_error)."""
        if not os.path.isfile(self.odb_path):
            self.odb_error = '%s.odb not found in the work directory' % self.job
            return None
        if not os.path.isfile(EXTRACTOR):
            self.odb_error = 'extractor not found: %s' % EXTRACTOR
            return None
        self._n_extract += 1
        try:
            out_dir = self.path('.check')
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = tempfile.mkdtemp(prefix='dshcheck_')
        out_json = os.path.join(out_dir, 'extract_%d.json' % self._n_extract)
        try:
            if os.path.isfile(out_json):
                os.remove(out_json)
        except Exception:
            pass
        args = []
        if step is not None:
            args += ['--step', '"%s"' % step]
        if frame is not None:
            args += ['--frame', str(int(frame))]
        if vars:
            args += ['--vars', '"%s"' % (vars if isinstance(vars, str) else ','.join(vars))]
        for p in probes:
            args += ['--probe', '"%s"' % ','.join(repr(float(x)) for x in p)]
        if series:
            args += ['--series', '"%s"' % (series if isinstance(series, str) else ','.join(series))]
        if not history:
            args += ['--no-history']
        # /s + one pair of outer quotes: cmd strips exactly those two and leaves the inner quoting alone
        cmd = '%s /d /s /c ""%s" python "%s" "%s" "%s" %s"' % (
            os.environ.get('COMSPEC', 'cmd.exe'), ABAQUS_BAT, EXTRACTOR, self.odb_path, out_json, ' '.join(args))
        log_path = os.path.join(out_dir, 'extract_%d.log' % self._n_extract)
        try:
            with open(log_path, 'wb') as log:
                p = subprocess.Popen(cmd, cwd=out_dir, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)
                try:
                    p.wait(timeout=EXTRACT_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    # stop only the process tree started here (cmd -> abaqus launcher -> python)
                    subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    self.odb_error = 'ODB extractor timed out after %d s' % EXTRACT_TIMEOUT_S
                    return None
            with open(log_path, 'rb') as log:
                tail = log.read().decode('gbk', 'replace')[-600:].strip()
        except Exception as e:
            self.odb_error = 'could not run the ODB extractor: %s' % e
            return None
        if not os.path.isfile(out_json):
            self.odb_error = 'ODB extractor wrote no output (exit %s): %s' % (p.returncode, tail)
            return None
        try:
            with open(out_json, encoding='utf-8', errors='replace') as f:
                ex = json.load(f)
        except Exception as e:
            self.odb_error = 'ODB extractor output is not valid JSON: %s' % e
            return None
        if not isinstance(ex, dict) or ex.get('error'):
            self.odb_error = 'ODB extractor failed: %s' % (ex.get('error') if isinstance(ex, dict) else 'bad output')
            return None
        self.odb_error = None
        return ex

    # ---- check 3: result.json
    def load_result(self, spec):
        """spec: {key: 'number' | ('list', n)}.  Adds the critical check `result_json`; valid keys land in self.result."""
        path = self.path('result.json')
        self.result = {}
        want = ', '.join(sorted(spec))
        if not os.path.isfile(path):
            return self.add('result_json', False, True, 'missing', 'keys: ' + want, None,
                            'result.json not found in the work directory')
        try:
            data = json.loads(read_text(path))
        except Exception as e:
            return self.add('result_json', False, True, 'unparseable', 'keys: ' + want, None,
                            'result.json is not valid JSON: %s' % e)
        if not isinstance(data, dict):
            return self.add('result_json', False, True, type(data).__name__, 'keys: ' + want, None,
                            'result.json must be one JSON object')
        self.result_raw = data
        bad = []
        for key, kind in spec.items():
            if key not in data:
                bad.append('%s: missing' % key)
                continue
            v = data[key]
            if kind == 'number':
                x = num(v)
                if x is None:
                    bad.append('%s: not a finite JSON number (%r)' % (key, v))
                else:
                    self.result[key] = x
            else:
                n = kind[1]
                xs = [num(x) for x in v] if isinstance(v, list) else None
                if xs is None or len(xs) != n or any(x is None for x in xs):
                    bad.append('%s: must be a list of %d finite JSON numbers (%r)' % (key, n, v))
                else:
                    self.result[key] = xs
        got = dict((k, data.get(k)) for k in spec if k in data)
        return self.add('result_json', not bad, True, got, 'keys: ' + want, None, '; '.join(bad))

    # ---- check 4: a reported number agrees with what the ODB says
    def report(self, key, candidates, tol, critical=False, signed=False, name=None, note=''):
        """result.json[key] must agree (relative tolerance `tol`) with one of the ODB-derived candidates
        [(label, value), ...].  Magnitudes are compared unless signed=True."""
        name = name or 'report_' + key
        rep = num(self.result.get(key)) if not isinstance(self.result.get(key), list) else None
        cands = [(lab, num(v)) for lab, v in candidates]
        cands = [(lab, v) for lab, v in cands if v is not None]
        if rep is None:
            return self.add(name, False, critical, None, cands[0][1] if cands else None, tol,
                            (note + ' | ' if note else '') + 'no valid value for "%s" in result.json' % key)
        if not cands:
            return self.add(name, False, critical, rep, None, tol, self._missing_note(note))
        best = None
        for lab, v in cands:
            a, b = (rep, v) if signed else (abs(rep), abs(v))
            err = (a - b) / abs(b) if b else (0.0 if a == 0.0 else float('inf'))
            if best is None or abs(err) < abs(best[0]):
                best = (err, lab, v)
        err, lab, v = best
        return self.add(name, abs(err) <= tol, critical, rep, v, tol,
                        (note + ' | ' if note else '') + 'reported vs ODB %s: %+.2f%%%s' % (
                            lab, 100.0 * err, '' if signed else ' (magnitudes)'))

    # ---- output
    def summary(self):
        n = len(self.checks)
        npass = sum(1 for c in self.checks if c['pass'])
        ok = n > 0 and all(c['pass'] for c in self.checks if c['critical']) and self.error is None
        return {'task': self.id, 'pass': bool(ok), 'score': round(float(npass) / n, 4) if n else 0.0,
                'checks': self.checks, 'error': self.error}


def main(check_file, body):
    """Entry point of every check.py: run `body(checker)`, print the verdict as one JSON object, exit."""
    code = 0
    task = os.path.basename(os.path.dirname(os.path.abspath(check_file)))
    out = {'task': task, 'pass': False, 'score': 0.0, 'checks': [], 'error': None}
    c = None
    try:
        if len(sys.argv) < 2:
            raise SystemExit('usage: py check.py <workdir>')
        workdir = sys.argv[1]
        c = Checker(check_file, workdir)
        if not os.path.isdir(c.workdir):
            c.odb_error = 'work directory does not exist: %s' % c.workdir
        body(c)
        out = c.summary()
    except SystemExit as e:
        out['error'] = str(e)
        code = 2
    except Exception:
        # a bug in the checker itself: report it, keep whatever checks were already recorded
        if c is not None:
            out = c.summary()
            out['pass'] = False
        out['error'] = 'checker crashed: ' + traceback.format_exc()
        code = 1
    sys.stdout.write(json.dumps(out, ensure_ascii=True, indent=1) + '\n')
    sys.stdout.flush()
    sys.exit(code)
