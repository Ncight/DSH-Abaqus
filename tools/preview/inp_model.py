# -*- coding: utf-8 -*-
"""Abaqus INP reader for the preview panel.

Reads what the solver will actually see (the INP), not the script that wrote it:
mesh, instances, sets, surfaces, materials, sections, constraints, steps, BCs, loads -
and runs a set of sanity checks ("体检") on the result.

Handles CAE-written files (parts / instances / assembly, flat "no parts" files) and the
hand-written style of the Abaqus example library: *INCLUDE, input= files, *PARAMETER,
*SYSTEM, *NODE system=, *NGEN, *NFILL, *NCOPY, *NMAP, *ELGEN, *ELCOPY, keyword and
parameter abbreviations, forward references between sets.

Memory: the file is read as a stream (node / element tables are never held as text) and
the mesh lives in flat arrays, so a model costs a few times its file size.  Files above
the automatic limit (60 MB, env ABQ_PREVIEW_MAX_AUTO_MB) are refused unless force=True,
and even then only when the machine has the estimated memory plus a reserve to spare.

Pure standard library, no side effects on import. Python 3.8+.

    m = Model(path)         # raises TooLargeError / MemoryRefusedError for oversized files
    v = m.view()            # JSON-able dict for the page (stats, geom, tree data, checks)
    m.highlight(kind, name) # geometry of one set / surface / BC / load ...
    m.symbols(step_index)   # BC and load symbols active in a step
"""
import ast
import math
import os
import re
from array import array
from bisect import bisect_left

MAX_INCLUDE_DEPTH = 8

# ---------------------------------------------------------------- memory guard
DEFAULT_MAX_AUTO_MB = 60.0      # files above this are not parsed without an explicit request
MEMORY_RESERVE_GB = 8.0         # commit memory that must stay free on top of the estimate
# peak memory of a parse: a fixed part for the interpreter plus a multiple of the INP size.  Measured with
# tests/bench_large.py on synthetic 20k / 50k / 100k-element models (peak commit of the whole process,
# view + JSON included): 100,079 C3D8R, 11.3 MB INP -> 35 MB (3.1 x the file); 99,856 S4R, 8.0 MB INP
# -> 56 MB (7.0 x, every shell element is drawn).  Larger models were not measured; the guard assumes more.
MEMORY_BASE_GB = 0.15
MEMORY_PER_FILE_GB = 10.0       # GB of peak memory per GB of INP assumed by the guard


class TooLargeError(Exception):
    """the INP is above the automatic size limit; parse it only on an explicit request (force=True)"""

    def __init__(self, path, size_bytes, limit_mb):
        self.path = path
        self.size_mb = size_bytes / 1048576.0
        self.limit_mb = limit_mb
        self.estimated_gb = estimate_memory_gb(size_bytes)
        Exception.__init__(self, '文件 %.1f MB 超过自动解析上限 %.0f MB, 预计解析需占用约 %.1f GB 内存' % (
            self.size_mb, limit_mb, self.estimated_gb))


class MemoryRefusedError(Exception):
    """not enough commit memory left on this machine to parse the file safely"""

    def __init__(self, path, size_bytes, available_gb):
        self.path = path
        self.size_mb = size_bytes / 1048576.0
        self.estimated_gb = estimate_memory_gb(size_bytes)
        self.available_gb = available_gb
        Exception.__init__(self, '拒绝解析: 预计需要约 %.1f GB 内存, 再留 %.0f GB 余量共 %.1f GB, 但本机可用提交内存只有 %.1f GB' % (
            self.estimated_gb, MEMORY_RESERVE_GB, self.estimated_gb + MEMORY_RESERVE_GB, available_gb))


def estimate_memory_gb(size_bytes):
    """upper estimate of the peak memory needed to parse an INP of this size"""
    return MEMORY_BASE_GB + MEMORY_PER_FILE_GB * size_bytes / 2.0 ** 30


def max_auto_mb():
    """size limit for parsing without an explicit request (env ABQ_PREVIEW_MAX_AUTO_MB, default 60)"""
    try:
        v = float(os.environ.get('ABQ_PREVIEW_MAX_AUTO_MB', ''))
        if v > 0:
            return v
    except ValueError:
        pass
    return DEFAULT_MAX_AUTO_MB


def available_commit_gb():
    """commit memory the system can still hand out (GB); None when it cannot be determined"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPageFile / 2.0 ** 30
    except Exception:
        return None


def check_memory(path, size_bytes, force=False, limit_mb=None):
    """raise TooLargeError / MemoryRefusedError when parsing a file of this size is not acceptable"""
    limit = limit_mb if limit_mb is not None else max_auto_mb()
    if size_bytes <= limit * 1048576.0:
        return
    if not force:
        raise TooLargeError(path, size_bytes, limit)
    avail = available_commit_gb()
    if avail is not None and avail < estimate_memory_gb(size_bytes) + MEMORY_RESERVE_GB:
        raise MemoryRefusedError(path, size_bytes, avail)


# ---------------------------------------------------------------- element topology
# Face node orders give OUTWARD normals (right-hand rule).
HEX_FACES = {'S1': (0, 3, 2, 1), 'S2': (4, 5, 6, 7), 'S3': (0, 1, 5, 4),
             'S4': (1, 2, 6, 5), 'S5': (2, 3, 7, 6), 'S6': (3, 0, 4, 7)}
TET_FACES = {'S1': (0, 2, 1), 'S2': (0, 1, 3), 'S3': (1, 2, 3), 'S4': (2, 0, 3)}
WEDGE_FACES = {'S1': (0, 2, 1), 'S2': (3, 4, 5), 'S3': (0, 1, 4, 3),
               'S4': (1, 2, 5, 4), 'S5': (2, 0, 3, 5)}
PYR_FACES = {'S1': (0, 3, 2, 1), 'S2': (0, 1, 4), 'S3': (1, 2, 4), 'S4': (2, 3, 4), 'S5': (3, 0, 4)}
SOLID_FACES = {'hex': HEX_FACES, 'tet': TET_FACES, 'wedge': WEDGE_FACES, 'pyr': PYR_FACES}
NCORNER = {'hex': 8, 'tet': 4, 'wedge': 6, 'pyr': 5, 'quad': 4, 'tri': 3, 'quad2d': 4, 'tri2d': 3}
SURFACE_FAMILIES = ('quad', 'tri')
PLANE_FAMILIES = ('quad2d', 'tri2d')

# face identifiers are stored as small codes (one byte per surface facet)
FID_CODE = {'': 0, 'S1': 1, 'S2': 2, 'S3': 3, 'S4': 4, 'S5': 5, 'S6': 6, 'SPOS': 7, 'SNEG': 8,
            'E1': 9, 'E2': 10, 'E3': 11, 'E4': 12, 'END1': 13, 'END2': 14}
FID_WHOLE = 15                  # the element itself (shell / plane element drawn as one polygon)
FID_NAME = {v: k for k, v in FID_CODE.items()}
_QUAD_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0))
_TRI_EDGES = ((0, 1), (1, 2), (2, 0))
# family -> {face code: local node indices}; 1 index = point, 2 = segment, more = polygon
FACE_TAB = {
    'hex': {FID_CODE[k]: v for k, v in HEX_FACES.items()},
    'tet': {FID_CODE[k]: v for k, v in TET_FACES.items()},
    'wedge': {FID_CODE[k]: v for k, v in WEDGE_FACES.items()},
    'pyr': {FID_CODE[k]: v for k, v in PYR_FACES.items()},
    'quad': dict([(0, (0, 1, 2, 3)), (7, (0, 1, 2, 3)), (8, (3, 2, 1, 0)), (FID_WHOLE, (0, 1, 2, 3))] +
                 [(9 + i, e) for i, e in enumerate(_QUAD_EDGES)]),
    'tri': dict([(0, (0, 1, 2)), (7, (0, 1, 2)), (8, (2, 1, 0)), (FID_WHOLE, (0, 1, 2))] +
                [(9 + i, e) for i, e in enumerate(_TRI_EDGES)]),
    'quad2d': dict([(FID_WHOLE, (0, 1, 2, 3))] + [(1 + i, e) for i, e in enumerate(_QUAD_EDGES)]),
    'tri2d': dict([(FID_WHOLE, (0, 1, 2))] + [(1 + i, e) for i, e in enumerate(_TRI_EDGES)]),
    'line': {0: (0, -1), 7: (0, -1), 8: (-1, 0), 13: (0,), 14: (-1,)},
}

_N3D = {4: 'tet', 10: 'tet', 8: 'hex', 20: 'hex', 27: 'hex', 6: 'wedge', 15: 'wedge', 18: 'wedge',
        5: 'pyr', 13: 'pyr'}
_N2D = {3: 'tri2d', 6: 'tri2d', 4: 'quad2d', 8: 'quad2d', 5: 'quad2d'}
_NSH = {3: 'tri', 6: 'tri', 65: 'tri', 4: 'quad', 8: 'quad', 9: 'quad'}
_RE_3D = re.compile(r'^(?:C3D|DC3D|DCC3D|AC3D|Q3D|EMC3D|CIN3D|COH3D|COD3D|EC3D|FC3D|CSS|SC)(\d+)')
_RE_GK3D = re.compile(r'^GK3D(\d+)')
_RE_CCL = re.compile(r'^CCL(\d+)')
_RE_MCL = re.compile(r'^(?:MCL|SFMCL)(\d+)')
_RE_2D = re.compile(r'^(?:CPEG|CPE|CPS|CGAX|CAX|DC2D|DCAX|DCC2D|DCCAX|AC2D|ACAX|CINPE|CINPS|CINAX|COH2D|'
                    r'COHAX|COD2D|CODAX|WARP2D|EMC2D)(\d+)')
_RE_GK2D = re.compile(r'^(?:GKPS|GKPE|GKAX)(\d+)')
_RE_SH = re.compile(r'^(?:STRI|SFM3D|M3D|R3D|DS|S|F3D|ACIN3D|ASI)(\d+)')
_RE_BEAM = re.compile(r'^(?:B|PIPE|ELBOW)(\d)(\d)')
_RE_TRUSS = re.compile(r'^T[23]D(\d)')
_LINE3 = ('SAX2', 'MAX2', 'MGAX2', 'SFMAX2', 'SFMGAX2', 'DC1D3', 'AC1D3', 'ACIN2D3', 'ACINAX3')
_LINE2 = ('CONN', 'SPRINGA', 'SPRING2', 'DASHPOTA', 'DASHPOT2', 'JOINTC', 'GAPUNI', 'GAPCYL', 'GAPSPHER',
          'DGAP', 'ITS', 'RB2D2', 'RB3D2', 'R2D2', 'RAX2', 'FRAME', 'GK2D2', 'GK3D2', 'GKAX2', 'F2D2', 'FAX2',
          'ACIN2D2', 'ACINAX2', 'DC1D2', 'DCC1D2', 'AC1D2', 'ASI2', 'SFMAX1', 'SFMGAX1', 'SAX1', 'MAX1',
          'MGAX1')
_POINT = ('MASS', 'ROTARYI', 'SPRING1', 'DASHPOT1', 'HEATCAP', 'DCOUP2D', 'DCOUP3D', 'ASI1', 'PC3D')
# element types that carry no section / property keyword of their own
_NO_SECTION = ('R3D', 'R2D', 'RAX', 'RB2D', 'RB3D')
_AXI_PREFIX = ('CAX', 'CGAX', 'SAX', 'MAX', 'MGAX', 'RAX', 'DCAX', 'DCCAX', 'ACAX', 'ACINAX', 'COHAX', 'CODAX',
               'GKAX', 'SFMAX', 'SFMGAX', 'CINAX', 'FAX')
_PLANE_PREFIX = ('CPS', 'CPE', 'DC2D', 'DCC2D', 'AC2D', 'ACIN2D', 'COH2D', 'COD2D', 'GKPS', 'GKPE', 'GK2D',
                 'CINPS', 'CINPE', 'WARP2D', 'EMC2D', 'B2', 'PIPE2', 'T2D', 'R2D', 'RB2D', 'CONN2D', 'FRAME2D',
                 'F2D', 'DCOUP2D')
_NEUTRAL_PREFIX = ('MASS', 'ROTARYI', 'SPRING', 'DASHPOT', 'HEATCAP', 'JOINTC', 'GAP', 'DGAP')

# Gasket elements list the nodes of the bottom face first and then those of the top face.  The preview
# wants the corner nodes first and in the usual continuum order, so these types are re-ordered when read
# (orders checked against the node coordinates of the Abaqus example problems).
_CORNER_PERM = (
    ('GK3D4L', (0, 1, 3, 2)), ('GK3D6L', (0, 2, 5, 3, 1, 4)),
    ('GKPS4', (0, 1, 3, 2)), ('GKPE4', (0, 1, 3, 2)), ('GKAX4', (0, 1, 3, 2)),
    ('GKPS6', (0, 2, 5, 3, 1, 4)), ('GKPE6', (0, 2, 5, 3, 1, 4)), ('GKAX6', (0, 2, 5, 3, 1, 4)),
    ('GK3D12M', (0, 1, 2, 6, 7, 8, 3, 4, 5, 9, 10, 11)),
    ('GK3D18', (0, 1, 2, 3, 8, 9, 10, 11, 4, 5, 6, 7, 12, 13, 14, 15, 16, 17)),
)

def corner_perm(etype):
    t = etype.upper()
    for prefix, perm in _CORNER_PERM:
        if t.startswith(prefix):
            return perm
    return None


def node_count(etype):
    """number of nodes an element of this type always has, 0 when it varies or is not known.
    (Used to tell a harmless trailing comma from a connectivity that continues on the next line.)"""
    t = etype.upper()
    if t.startswith('CONN'):
        return 0                    # one or two nodes, never continued
    if t.startswith('C3D15V'):
        return 18                   # variable node elements: the full line holds the largest count
    if t.startswith('C3D27'):
        return 27
    for rx in (_RE_3D, _RE_2D, _RE_GK3D, _RE_GK2D, _RE_CCL, _RE_MCL):
        m = rx.match(t)
        if m:
            return int(m.group(1))
    m = _RE_SH.match(t)
    if m:
        n = int(m.group(1))
        return 6 if n == 65 else n
    fam, n = classify(t)
    return n if fam == 'line' else 1 if fam == 'point' else 0


_family_cache = {}


def classify(etype):
    """-> (family, n_corner_nodes); family is None for element types the preview cannot draw."""
    t = etype.upper()
    hit = _family_cache.get(t)
    if hit is not None:
        return hit
    res = (None, 0)
    if t.startswith(('GK3D4L', 'GK3D6L')):
        res = ('quad', 4)                    # line gasket: a strip between its bottom and top line
    elif t.startswith(_POINT):
        res = ('point', 1)
    elif t.startswith(_LINE3):
        res = ('line', 3)
    elif t.startswith(_LINE2):
        res = ('line', 2)
    else:
        fam = None
        m = _RE_3D.match(t)
        if m:
            fam = _N3D.get(int(m.group(1)))
        if fam is None:
            m = _RE_GK3D.match(t)
            if m:
                fam = {8: 'hex', 6: 'wedge', 18: 'hex', 12: 'wedge'}.get(int(m.group(1)))
        if fam is None:
            m = _RE_CCL.match(t)
            if m:
                fam = {9: 'wedge', 18: 'wedge', 12: 'hex', 24: 'hex'}.get(int(m.group(1)))
        if fam is None:
            m = _RE_MCL.match(t)
            if m:
                fam = {6: 'quad', 9: 'quad'}.get(int(m.group(1)))
        if fam is None:
            m = _RE_2D.match(t)
            if m:
                fam = _N2D.get(int(m.group(1)))
        if fam is None:
            m = _RE_GK2D.match(t)
            if m:
                fam = {4: 'quad2d', 6: 'quad2d'}.get(int(m.group(1)))
        if fam is None:
            m = _RE_SH.match(t)
            if m:
                fam = _NSH.get(int(m.group(1)))
        if fam is not None:
            res = (fam, NCORNER[fam])
        else:
            m = _RE_BEAM.match(t)
            if m:
                res = ('line', 3 if m.group(2) == '2' else 2)
            else:
                m = _RE_TRUSS.match(t)
                if m and m.group(1) in '23':
                    res = ('line', int(m.group(1)))
    _family_cache[t] = res
    return res


# ---------------------------------------------------------------- small helpers
def _dec(raw):
    """bytes of one line -> str (UTF-8, else GBK as written by a Chinese-locale CAE)"""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', errors='replace')


def _norm(s):
    return ' '.join(s.upper().split())


def _squash(s):
    return ''.join(s.upper().split())


def _toks(line):
    return [t.replace('"', '').strip() for t in line.split(',')]


def _is_int(s):
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


_RE_FORTRAN = re.compile(r'^\s*([+-]?(?:\d+\.?\d*|\.\d+))(?:[eEdD]([+-]?\d+)?|([+-]\d+))?[eEdD+-]*\s*$')


def _fnum(s):
    """float or None.  Reads numbers the way Abaqus (Fortran) does: 1.5D3, 1.5+3, and a mantissa whose
    exponent got lost ('207.34E-') counts as the mantissa."""
    try:
        return float(s)
    except (ValueError, TypeError):
        m = _RE_FORTRAN.match(str(s))
        if not m:
            return None
        try:
            return float(m.group(1)) * 10.0 ** int(m.group(2) or m.group(3) or 0)
        except (ValueError, OverflowError):
            return None


def _floats(tokens):
    """leading run of numbers in a token list (blank = 0)"""
    out = []
    for t in tokens:
        if t == '':
            out.append(0.0)
            continue
        v = _fnum(t)
        if v is None:
            break
        out.append(v)
    return out


def pget(params, name, default=None):
    """Keyword parameter lookup. Abaqus ignores blanks inside names and accepts unique
    abbreviations (GEN for GENERATE, NEWSET for NEW SET, FREQ for FREQUENCY)."""
    v = params.get(name)
    if v is not None:
        return v
    want = name.replace(' ', '')
    for k, v in params.items():
        if k.replace(' ', '') == want:
            return v
    for k, v in params.items():
        ks = k.replace(' ', '')
        if len(ks) >= 3 and want.startswith(ks):
            return v
    return default


def pstr(params, name, default=''):
    v = pget(params, name)
    return default if v is None or v is True else str(v)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else None


def _rot(v, axis, ang):
    """Rodrigues rotation of vector v about unit axis by ang (radians)"""
    c, s = math.cos(ang), math.sin(ang)
    d = _dot(axis, v)
    cx = _cross(axis, v)
    return (v[0] * c + cx[0] * s + axis[0] * d * (1 - c),
            v[1] * c + cx[1] * s + axis[1] * d * (1 - c),
            v[2] * c + cx[2] * s + axis[2] * d * (1 - c))


def _frame(a, b=None, c=None):
    """local rectangular system from *SYSTEM / *NMAP style points: a = origin, b on local x,
    c in the local x-y plane. -> (origin, ex, ey, ez)"""
    ex, ey, ez = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if b is not None:
        u = _unit(_sub(b, a))
        if u is not None:
            if c is not None and _unit(_cross(u, _sub(c, a))) is not None:
                ex = u
                ez = _unit(_cross(ex, _sub(c, a)))
            else:
                # only the x' axis is given (Keywords Reference, *SYSTEM): "the Z-axis direction remains
                # unchanged, and the X1-axis is projected onto the (X, Y) plane".  x' along global Z has no
                # projection: the axes then stay as they are (pure shift).
                ex = _unit((u[0], u[1], 0.0)) or ex
            ey = _cross(ez, ex)
    return (a, ex, ey, ez)


def _to_global(fr, p):
    o, ex, ey, ez = fr
    return (o[0] + ex[0] * p[0] + ey[0] * p[1] + ez[0] * p[2],
            o[1] + ex[1] * p[0] + ey[1] * p[1] + ez[1] * p[2],
            o[2] + ex[2] * p[0] + ey[2] * p[1] + ez[2] * p[2])


def _vec_global(fr, v):
    return _to_global(((0.0, 0.0, 0.0), fr[1], fr[2], fr[3]), v)


def _curvilinear(kind, x, y, z):
    """*NODE system=C / S : (R, theta deg, Z) or (R, theta deg, phi deg) -> rectangular"""
    if kind == 'C':
        t = math.radians(y)
        return x * math.cos(t), x * math.sin(t), z
    if kind == 'S':
        t, p = math.radians(y), math.radians(z)
        return x * math.cos(p) * math.cos(t), x * math.cos(p) * math.sin(t), x * math.sin(p)
    return x, y, z


def _fmt(x, scale=None):
    """numbers in messages: never scientific notation; 4 decimals, or 4 significant digits when tiny.
    With `scale`, values that are only rounding dust next to it print as zero."""
    if x == 0 or (scale and abs(x) < 1e-9 * scale):
        return '0.0000'
    a = abs(x)
    if a >= 1e-4:
        return '%.4f' % x
    return '%.*f' % (min(20, 3 - int(math.floor(math.log10(a)))), x)


def _sorted_unique(a):
    """array('i') -> the same values ascending without duplicates (returns `a` itself when it already is)"""
    it = iter(a)
    try:
        prev = next(it)
    except StopIteration:
        return a
    for v in it:
        if v <= prev:
            return array('i', sorted(set(a)))
        prev = v
    return a


# ---------------------------------------------------------------- compact storage
class LabelMap(object):
    """node / element label -> index.

    Labels that arrive in increasing order (the normal case) are kept as one run: nothing at all when
    they are consecutive, 4 bytes each otherwise.  Only out-of-order labels cost a dict entry."""
    __slots__ = ('base', 'n', 'g0', 'labels', 'extra', 'shared')

    def __init__(self):
        self.base = 0           # first label of the run
        self.n = 0              # length of the run
        self.g0 = 0             # index of the first item of the run
        self.labels = None      # ascending labels of the run when they are not consecutive
        self.extra = {}
        self.shared = False     # labels array belongs to a part template (copy before writing)

    def __len__(self):
        return self.n + len(self.extra)

    def add(self, lab, g):
        n = self.n
        if n == 0 and not self.extra:
            self.base, self.g0, self.n = lab, g, 1
            return
        if n and g == self.g0 + n:
            labels = self.labels
            if labels is None:
                if lab == self.base + n:
                    self.n = n + 1
                    return
                if lab > self.base + n:
                    self.labels = labels = array('i', range(self.base, self.base + n))
                    labels.append(lab)
                    self.n = n + 1
                    return
            elif lab > labels[-1]:
                if self.shared:
                    self.labels = labels = array('i', labels)
                    self.shared = False
                labels.append(lab)
                self.n = n + 1
                return
        self.extra[lab] = g

    def get(self, lab, default=None):
        if self.extra:
            g = self.extra.get(lab)
            if g is not None:
                return g
        labels = self.labels
        if labels is None:
            i = lab - self.base
            return self.g0 + i if 0 <= i < self.n else default
        i = bisect_left(labels, lab)
        if i < self.n and labels[i] == lab:
            return self.g0 + i
        return default

    def __contains__(self, lab):
        return self.get(lab) is not None

    def top(self):
        """largest label so far when every label sits in the ordered run (a larger label is then
        certainly new); None when some labels are stored out of order"""
        if self.extra:
            return None
        if not self.n:
            return -1
        return self.labels[-1] if self.labels is not None else self.base + self.n - 1

    def dense(self):
        """(first label, last label, index offset) when every label maps by plain arithmetic, else None"""
        if self.labels is None and not self.extra and self.n:
            return self.base, self.base + self.n - 1, self.g0 - self.base
        return None

    def values(self):
        for i in range(self.n):
            yield self.g0 + i
        for g in self.extra.values():
            yield g

    def shifted(self, off):
        """the same labels for a copy of the items placed `off` further on"""
        m = LabelMap()
        m.base, m.n, m.g0, m.labels = self.base, self.n, self.g0 + off, self.labels
        m.shared = self.labels is not None
        m.extra = {k: v + off for k, v in self.extra.items()} if self.extra else {}
        return m


class Mesh(object):
    """nodes and elements in flat arrays (no Python object per node or element)"""
    __slots__ = ('xyz', 'n_label', 'n_inst', 'e_code', 'e_off', 'e_nodes', 'e_inst', 'e_sec', 'e_label')

    def __init__(self):
        self.xyz = array('d')           # x, y, z per node
        self.n_label = array('i')
        self.n_inst = array('i')        # context (instance) index per node
        self.e_code = array('H')        # element type code (index into Model.types)
        self.e_off = array('i', [0])    # connectivity of element e = e_nodes[e_off[e]:e_off[e + 1]]
        self.e_nodes = array('i')
        self.e_inst = array('i')
        self.e_sec = array('i')         # section index, -1 = none
        self.e_label = array('i')


class _CoordView(object):
    """coords[g] -> (x, y, z) on top of the flat array"""
    __slots__ = ('a',)

    def __init__(self, a):
        self.a = a

    def __len__(self):
        return len(self.a) // 3

    def __getitem__(self, g):
        a = self.a
        i = 3 * g
        return (a[i], a[i + 1], a[i + 2])

    def __setitem__(self, g, p):
        a = self.a
        i = 3 * g
        a[i], a[i + 1], a[i + 2] = p[0], p[1], p[2]

    def __iter__(self):
        a = self.a
        for i in range(0, len(a), 3):
            yield (a[i], a[i + 1], a[i + 2])


class _ConnView(object):
    """e_conn[e] -> tuple of node indices"""
    __slots__ = ('off', 'nodes')

    def __init__(self, mesh):
        self.off = mesh.e_off
        self.nodes = mesh.e_nodes

    def __len__(self):
        return len(self.off) - 1

    def __getitem__(self, e):
        return tuple(self.nodes[self.off[e]:self.off[e + 1]])

    def __iter__(self):
        off, nodes = self.off, self.nodes
        for e in range(len(off) - 1):
            yield tuple(nodes[off[e]:off[e + 1]])


class _TypeView(object):
    """e_type[e] -> element type name"""
    __slots__ = ('code', 'names')

    def __init__(self, mesh, names):
        self.code = mesh.e_code
        self.names = names

    def __len__(self):
        return len(self.code)

    def __getitem__(self, e):
        return self.names[self.code[e]]

    def __iter__(self):
        names = self.names
        for c in self.code:
            yield names[c]


# ---------------------------------------------------------------- keyword groups
MESH_KW = {'NODE', 'ELEMENT', 'NSET', 'ELSET', 'SURFACE', 'NGEN', 'NFILL', 'NCOPY', 'NMAP', 'ELGEN',
           'ELCOPY', 'SYSTEM'}
SECTION_KW = {'SOLID SECTION', 'SHELL SECTION', 'SHELL GENERAL SECTION', 'BEAM SECTION',
              'BEAM GENERAL SECTION', 'MEMBRANE SECTION', 'COHESIVE SECTION', 'SURFACE SECTION',
              'CONNECTOR SECTION', 'MASS', 'ROTARY INERTIA', 'SPRING', 'DASHPOT', 'GASKET SECTION',
              'EULERIAN SECTION', 'FRAME SECTION', 'PIPE-SOIL INTERACTION', 'GAP', 'INTERFACE', 'ITS',
              'JOINT', 'HEATCAP', 'UEL PROPERTY', 'FLUID PROPERTY', 'DISCRETE SECTION',
              'DISTRIBUTING COUPLING', 'DRAG CHAIN', 'LINE SPRING'}
PROC_KW = {'STATIC', 'DYNAMIC', 'FREQUENCY', 'BUCKLE', 'VISCO', 'GEOSTATIC', 'SOILS', 'HEAT TRANSFER',
           'COUPLED TEMPERATURE-DISPLACEMENT', 'STEADY STATE DYNAMICS', 'MODAL DYNAMIC',
           'RANDOM RESPONSE', 'RESPONSE SPECTRUM', 'COMPLEX FREQUENCY', 'ANNEAL', 'MASS DIFFUSION',
           'DYNAMIC TEMPERATURE-DISPLACEMENT', 'SUBSTRUCTURE GENERATE', 'COUPLED THERMAL-ELECTRICAL',
           'DIRECT CYCLIC', 'STEADY STATE TRANSPORT', 'MATRIX GENERATE', 'ELECTROMAGNETIC',
           'COUPLED THERMAL-ELECTRICAL-STRUCTURAL'}
LOAD_KW = {'CLOAD', 'DLOAD', 'DSLOAD', 'CFLUX', 'DFLUX', 'DSFLUX', 'FILM', 'SFILM', 'RADIATE',
           'SRADIATE', 'CONNECTOR LOAD', 'CONNECTOR MOTION', 'TEMPERATURE', 'BASE MOTION',
           'INERTIA RELIEF', 'FIELD', 'PRESSURE STRESS', 'CECHARGE', 'DECHARGE', 'DSECHARGE'}
OUTVAR_KW = {'NODE OUTPUT', 'ELEMENT OUTPUT', 'CONTACT OUTPUT', 'ENERGY OUTPUT', 'INTEGRATED OUTPUT',
             'INCREMENTATION OUTPUT', 'RADIATION OUTPUT'}
CONSTRAINT_KW = {'COUPLING', 'RIGID BODY', 'EMBEDDED ELEMENT', 'MPC', 'EQUATION', 'KINEMATIC',
                 'DISTRIBUTING', 'KINEMATIC COUPLING', 'SHELL TO SOLID COUPLING', 'PRE-TENSION SECTION',
                 'CYCLIC SYMMETRY MODEL', 'FASTENER', 'ADJUST', 'DISPLAY BODY'}
GCONTACT_KW = {'CONTACT INCLUSIONS', 'CONTACT EXCLUSIONS', 'CONTACT PROPERTY ASSIGNMENT',
               'SURFACE PROPERTY ASSIGNMENT', 'CONTACT FORMULATION', 'CONTACT STABILIZATION',
               'CONTACT INITIALIZATION ASSIGNMENT', 'CONTACT CONTROLS ASSIGNMENT', 'CONTACT MASS SCALING',
               'CONTACT INITIALIZATION DATA'}
SURFINT_KW = {'FRICTION', 'SURFACE BEHAVIOR', 'CONTACT DAMPING', 'GAP CONDUCTANCE', 'COHESIVE BEHAVIOR',
              'DAMAGE INITIATION', 'DAMAGE EVOLUTION', 'GAP HEAT GENERATION', 'GAP RADIATION',
              'GAP ELECTRICAL CONDUCTANCE', 'DAMAGE STABILIZATION', 'FRACTURE CRITERION'}
OTHER_KW = {'HEADING', 'PREPRINT', 'PART', 'END PART', 'ASSEMBLY', 'END ASSEMBLY', 'INSTANCE',
            'END INSTANCE', 'MATERIAL', 'STEP', 'END STEP', 'BOUNDARY', 'SURFACE INTERACTION', 'AMPLITUDE',
            'INITIAL CONDITIONS', 'CONTACT PAIR', 'CONTACT', 'TIE', 'RESTART', 'PHYSICAL CONSTANTS',
            'DISTRIBUTION', 'DISTRIBUTION TABLE', 'CONNECTOR BEHAVIOR', 'SECTION CONTROLS', 'TIME POINTS',
            'ORIENTATION', 'TRANSFORM', 'OUTPUT', 'FILTER', 'PARAMETER', 'FLUID BEHAVIOR', 'INCIDENT WAVE',
            'CLEARANCE', 'BULK VISCOSITY', 'FIXED MASS SCALING', 'VARIABLE MASS SCALING', 'NODAL THICKNESS',
            'ELEMENT PROPERTIES', 'CONTACT CONTROLS', 'INCLUDE', 'GASKET BEHAVIOR',
            'GASKET THICKNESS BEHAVIOR', 'GASKET ELASTICITY', 'GASKET CONTACT AREA', 'CONTACT INTERFERENCE',
            'NODE PRINT', 'EL PRINT', 'NODE FILE', 'EL FILE', 'CONTACT PRINT', 'CONTACT FILE', 'ENERGY PRINT',
            'ENERGY FILE', 'FILE OUTPUT', 'FILE FORMAT', 'MONITOR', 'PRINT', 'CONTROLS', 'DIAGNOSTICS',
            'IMPORT', 'SYMMETRIC MODEL GENERATION', 'SYMMETRIC RESULTS TRANSFER', 'MAP SOLUTION',
            'SUBMODEL', 'DESIGN PARAMETER', 'DESIGN RESPONSE', 'DSA CONTROLS', 'PARAMETER DEPENDENCE',
            'PARAMETER SHAPE VARIATION', 'REBAR LAYER', 'REBAR', 'NODAL ENERGY RATE', 'IMPERFECTION',
            'MODAL DAMPING', 'GLOBAL DAMPING', 'SELECT EIGENMODES', 'SUBSTRUCTURE PROPERTY',
            'ADAPTIVE MESH', 'ADAPTIVE MESH CONSTRAINT', 'ADAPTIVE MESH CONTROLS', 'MODEL CHANGE',
            'SOLUTION TECHNIQUE', 'SOLVER CONTROLS', 'STEP CONTROL', 'ELEMENT RESPONSE', 'NODE RESPONSE',
            'CONTACT RESPONSE', 'AXIAL', 'M1', 'M2', 'TORQUE', 'TRANSVERSE SHEAR STIFFNESS',
            'SECTION POINTS', 'CENTROID', 'SHEAR CENTER', 'RELEASE', 'CONTACT NODE SET', 'SLIDE LINE',
            'FLEXIBLE BODY', 'SUBSTRUCTURE LOAD CASE', 'RETAINED NODAL DOFS', 'SURFACE FLAW',
            'TRACER PARTICLE', 'EXTREME VALUE', 'EXTREME ELEMENT VALUE', 'EXTREME NODE VALUE',
            'CO-SIMULATION', 'CO-SIMULATION REGION', 'CO-SIMULATION CONTROLS', 'ACOUSTIC WAVE FORMULATION',
            'INCIDENT WAVE INTERACTION', 'INCIDENT WAVE INTERACTION PROPERTY', 'SIMPEDANCE', 'IMPEDANCE',
            'IMPEDANCE PROPERTY', 'BEAM ADDED INERTIA', 'BEAM FLUID INERTIA', 'NONSTRUCTURAL MASS',
            'MASS ADJUST', 'LOAD CASE', 'END LOAD CASE', 'ENRICHMENT', 'ENRICHMENT ACTIVATION',
            'CORRELATION', 'PSD-DEFINITION', 'SPECTRUM', 'CONTOUR INTEGRAL',
            'NORMAL', 'PERIODIC', 'CAVITY DEFINITION', 'VIEWFACTOR OUTPUT', 'RADIATION VIEWFACTOR',
            'EMISSIVITY', 'SURFACE PROPERTY', 'CONSTRAINT CONTROLS', 'DAMPING CONTROLS',
            'USER ELEMENT', 'MATRIX', 'MATRIX INPUT', 'MATRIX ASSEMBLE', 'SUBSTRUCTURE DIRECTORY',
            'SUBSTRUCTURE PATH', 'SUBSTRUCTURE COPY', 'SUBSTRUCTURE DELETE', 'RIGID SURFACE',
            'SURFACE SMOOTHING', 'DEBOND'}
# A *Material block ends at any of these; every other keyword after *Material is one of its sub-options.
# (*Damage ... are material options as well as contact property options.)
MATERIAL_END = (MESH_KW | SECTION_KW | PROC_KW | LOAD_KW | OUTVAR_KW | CONSTRAINT_KW | GCONTACT_KW |
                SURFINT_KW | OTHER_KW) - {'DAMAGE INITIATION', 'DAMAGE EVOLUTION', 'DAMAGE STABILIZATION'}

_KW_CANON = {}
for _group in (MESH_KW, SECTION_KW, PROC_KW, LOAD_KW, OUTVAR_KW, CONSTRAINT_KW, GCONTACT_KW, SURFINT_KW,
               OTHER_KW, {'ELASTIC', 'DENSITY', 'PLASTIC', 'USER MATERIAL', 'DEPVAR', 'HYPERELASTIC',
                          'EXPANSION', 'CONDUCTIVITY', 'SPECIFIC HEAT', 'DAMPING'}):
    for _k in _group:
        _KW_CANON[_squash(_k)] = _k
del _group, _k

# data lines of these keywords may live in a separate file (input=...)
INPUT_DATA_KW = {'NODE', 'ELEMENT', 'NSET', 'ELSET', 'AMPLITUDE', 'BOUNDARY', 'CLOAD', 'DLOAD', 'DSLOAD',
                 'INITIAL CONDITIONS', 'MPC', 'EQUATION'}
# keywords whose presence means the mesh is not (all) in this file
INCOMPLETE_KW = {
    'IMPORT': '*Import 从上一个分析导入网格',
    'SYMMETRIC MODEL GENERATION': '*Symmetric Model Generation 由轴对称/扇区模型生成三维网格',
    'SYMMETRIC RESULTS TRANSFER': '*Symmetric Results Transfer',
    'SUBSTRUCTURE PATH': '子结构',
    'SUBSTRUCTURE COPY': '子结构',
    'MAP SOLUTION': '*Map Solution 网格重划映射',
}

BC_TYPES = {'ENCASTRE': (1, 2, 3, 4, 5, 6), 'PINNED': (1, 2, 3), 'XSYMM': (1, 5, 6),
            'YSYMM': (2, 4, 6), 'ZSYMM': (3, 4, 5), 'XASYMM': (2, 3, 4), 'YASYMM': (1, 3, 5),
            'ZASYMM': (1, 2, 6)}


# parameters whose value is a label: Abaqus ignores blanks inside a label unless it is quoted
_NAME_PARAMS = {'NAME', 'PART', 'INSTANCE', 'ELSET', 'NSET', 'MATERIAL', 'SURFACE', 'REFNODE', 'OLDSET', 'NEWSET',
                'HOSTELSET', 'PINNSET', 'TIENSET', 'ANALYTICALSURFACE', 'INTERACTION', 'ORIENTATION',
                'CONSTRAINTNAME', 'BEHAVIOR', 'CONTROLS', 'AMPLITUDE', 'NODE'}
_kw_abbrev = {}


def _kw_name(raw):
    """canonical keyword name; blanks are insignificant and a unique abbreviation is accepted (*ROTARY)"""
    key = _squash(raw)
    name = _KW_CANON.get(key)
    if name is None:
        name = _kw_abbrev.get(key)
        if name is None:
            name = _norm(raw)
            if len(key) >= 5:
                hits = [v for k, v in _KW_CANON.items() if k.startswith(key)]
                if len(hits) == 1:
                    name = hits[0]
            _kw_abbrev[key] = name
    return name


def _parse_kw(line):
    """'*Solid Section, elset=A, material=B' -> ('SOLID SECTION', {'ELSET': 'A', 'MATERIAL': 'B'})"""
    parts = line[1:].split(',')
    name = _kw_name(parts[0])
    params = {}
    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        if '=' in p:
            k, v = p.split('=', 1)
            v = v.strip()
            if '"' in v:
                v = v.replace('"', '')
            elif ' ' in v and _squash(k) in _NAME_PARAMS:
                v = ''.join(v.split())
            params[_norm(k)] = v
        else:
            params[_norm(p)] = True
    return name, params


# ---------------------------------------------------------------- *PARAMETER (safe evaluation)
_PARAM_ENV = {'pi': math.pi, 'e': math.e, 'abs': abs, 'min': min, 'max': max, 'int': int, 'float': float,
              'round': round}
for _f in ('sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'atan2', 'sinh', 'cosh', 'tanh', 'sqrt', 'exp', 'log',
           'log10', 'pow', 'fabs', 'floor', 'ceil', 'fmod', 'hypot', 'radians', 'degrees'):
    _PARAM_ENV[_f] = getattr(math, _f)
del _f
_RE_PARAM_REF = re.compile(r'<\s*([A-Za-z_][A-Za-z0-9_]*)\s*>')


class ParamError(Exception):
    pass


def _py2_div(a, b):
    # Abaqus evaluates parameter expressions with Python 2: int / int truncates
    if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and not isinstance(b, bool):
        return a // b
    return a / b


_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.Div: _py2_div, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
_CMPOPS = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
           ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b}


def _param_lookup(env, name):
    if name in env:
        return env[name]
    low = name.lower()
    for k in env:
        if k.lower() == low:
            return env[k]
    if name in _PARAM_ENV:
        return _PARAM_ENV[name]
    raise ParamError('未定义的参数 %s' % name)


def _eval_expr(node, env):
    """evaluate a parameter expression without eval(): numbers, strings, arithmetic, math functions"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str)):
            return node.value
        raise ParamError('不支持的常量')
    if isinstance(node, ast.Name):
        return _param_lookup(env, node.id)
    if isinstance(node, ast.BinOp):
        a, b = _eval_expr(node.left, env), _eval_expr(node.right, env)
        if isinstance(node.op, ast.Pow):
            if isinstance(b, (int, float)) and abs(b) > 512:
                raise ParamError('指数过大')
            return a ** b
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ParamError('不支持的运算符')
        return op(a, b)
    if isinstance(node, ast.UnaryOp):
        v = _eval_expr(node.operand, env)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        if isinstance(node.op, ast.Not):
            return not v
        raise ParamError('不支持的运算符')
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise ParamError('不支持的函数调用')
        f = _PARAM_ENV.get(node.func.id)
        if not callable(f):
            raise ParamError('不支持的函数 %s' % node.func.id)
        return f(*[_eval_expr(a, env) for a in node.args])
    if isinstance(node, ast.IfExp):
        return _eval_expr(node.body if _eval_expr(node.test, env) else node.orelse, env)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op = _CMPOPS.get(type(node.ops[0]))
        if op is None:
            raise ParamError('不支持的比较')
        return op(_eval_expr(node.left, env), _eval_expr(node.comparators[0], env))
    if isinstance(node, ast.BoolOp):
        vals = [_eval_expr(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    raise ParamError('不支持的表达式')


def _param_str(v):
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, float):
        return repr(v)
    return str(v)


def eval_parameters(lines, env, errors):
    """data lines of one *PARAMETER block: `name = expression` statements"""
    for line in lines:
        src = line.split('#', 1)[0].strip()
        if not src:
            continue
        for stmt in src.split(';'):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                tree = ast.parse(stmt, mode='exec')
                if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
                    raise ParamError('不是赋值语句')
                node = tree.body[0]
                val = _eval_expr(node.value, env)
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Name):
                        raise ParamError('不支持的赋值目标')
                    env[tgt.id] = val
            except (ParamError, SyntaxError, ValueError, TypeError, ArithmeticError, RecursionError) as e:
                errors.append('%s  (%s)' % (stmt[:60], e))


# ---------------------------------------------------------------- reading the file
# keyword lines the pre-scan cares about.  The pattern starts with a literal newline so that the regex
# engine can skip from line to line (a multi-line '^' would be tried at every byte).
_RE_SCAN = re.compile(
    rb'\n[ \t]*\*[ \t]*(?:(parameter)[ \t]*(?:,[^\n]*)?\r?(?=\n|\Z)'
    rb'|(include)[ \t]*,([^\n]*)'
    rb'|(instance)[ \t]*,((?:[^\n]*,[ \t]*\r?\n)*[^\n]*)'
    rb'|(?:node|element|nset|elset|amplitude|boundary|cload|dload|dsload|mpc|equation|initial[ \t]*conditions)'
    rb'\b([^\n]*\binput[ \t]*=[^\n]*))', re.I)
_SCAN_CHUNK = 8 << 20
_RE_INPUT = re.compile(rb'\binput[ \t]*=[ \t]*"?([^",\r\n]+)', re.I)
_RE_PART = re.compile(rb'\bpart[ \t]*=[ \t]*"?([^",\r\n]+)', re.I)


def _locate(base, name):
    """an include / input= file next to the INP; Abaqus on Windows is not case sensitive"""
    if not name:
        return None
    p = name if os.path.isabs(name) else os.path.join(base, name)
    if os.path.isfile(p):
        return p
    low = os.path.basename(name).lower()
    try:
        for f in os.listdir(os.path.dirname(p) or '.'):
            if f.lower() == low:
                return os.path.join(os.path.dirname(p), f)
    except OSError:
        pass
    return None


def prescan(path, _depth=0, _res=None):
    """One cheap pass over the raw bytes (no line objects): total size including the files that are
    pulled in, whether *PARAMETER is used, and how often each part is instanced."""
    res = _res if _res is not None else {'bytes': 0, 'files': 0, 'param': False, 'instances': {},
                                         'missing': []}
    try:
        size = os.path.getsize(path)
        f = open(path, 'rb')
    except OSError:
        res['missing'].append(os.path.basename(path))
        return res
    res['bytes'] += size
    res['files'] += 1
    base = os.path.dirname(path)
    buf = b'\n'
    with f:
        while True:
            chunk = f.read(_SCAN_CHUNK)
            last = len(chunk) < _SCAN_CHUNK
            buf += chunk
            # matches that start in the last few lines are left for the next round, when the lines a
            # keyword may be continued on have been read as well
            limit = len(buf)
            if not last:
                for _ in range(6):
                    limit = buf.rfind(b'\n', 0, limit)
                    if limit <= 0:
                        break
                if limit <= 0:
                    continue
            for m in _RE_SCAN.finditer(buf):
                if m.start() >= limit:
                    break
                if m.group(1):
                    res['param'] = True
                elif m.group(2) or m.group(6):
                    mm = _RE_INPUT.search(m.group(3) or m.group(6))
                    if mm and _depth < MAX_INCLUDE_DEPTH:
                        p = _locate(base, _dec(mm.group(1)).strip())
                        if p is not None:
                            prescan(p, _depth + 1, res)
                elif m.group(4):
                    mm = _RE_PART.search(m.group(5))
                    if mm:
                        key = _dec(mm.group(1)).strip().upper()
                        res['instances'][key] = res['instances'].get(key, 0) + 1
            if last:
                break
            buf = buf[limit:]
    return res


class _Stream(object):
    """Pull reader over an INP and the files it includes.

    Keyword lines are parsed here.  Data lines are handed out lazily - straight from the open file for
    the big tables - so that the node and element tables never exist as a list of strings."""

    def __init__(self, path, incomplete, env=None):
        self.incomplete = incomplete
        self.env = env              # *PARAMETER values; None when the file has none
        self.stack = []             # [(file object, directory)]
        self.f = None
        self.dir = ''
        self.held = None            # a line starting with '*' that a data consumer ran into
        self.keep_blank = False     # rows(blanks=True) is collecting: blank data lines count
        self.nmissing = 0           # include / input= files that could not be opened
        self.nfiles = 0
        self.unresolved = {}
        self._open(path)

    def _open(self, path):
        try:
            f = open(path, 'rb', buffering=1 << 20)
        except OSError as e:
            self.incomplete.append('文件读取失败: %s (%s)' % (os.path.basename(path), e.strerror or e))
            self.nmissing += 1
            return False
        self.stack.append((f, os.path.dirname(path)))
        self.f, self.dir = f, os.path.dirname(path)
        self.nfiles += 1
        return True

    def _pop(self):
        f, _ = self.stack.pop()
        f.close()
        if self.stack:
            self.f, self.dir = self.stack[-1]
        else:
            self.f = None

    def close(self):
        while self.stack:
            self._pop()

    def _readline(self):
        while self.f is not None:
            raw = self.f.readline()
            if raw:
                return raw
            self._pop()
        return None

    def _subst(self, text):
        def repl(m):
            try:
                return _param_str(_param_lookup(self.env, m.group(1)))
            except ParamError:
                self.unresolved[m.group(1)] = self.unresolved.get(m.group(1), 0) + 1
                return m.group(0)
        return _RE_PARAM_REF.sub(repl, text)

    def _include(self, name, what='*Include'):
        p = _locate(self.dir, name)
        if p is None or len(self.stack) > MAX_INCLUDE_DEPTH:
            self.incomplete.append('%s 的文件未找到或嵌套过深: %s' % (what, name))
            self.nmissing += 1
            return
        self._open(p)

    def _kw_line(self, s):
        """s = stripped bytes of a keyword line (first physical line); reads its continuation lines"""
        while s.endswith(b','):
            nxt = self._readline()
            if nxt is None:
                break
            n2 = nxt.strip()
            if n2[:2] == b'**':
                continue
            s += n2
        line = _dec(s)
        if self.env is not None and '<' in line:
            line = self._subst(line)
        return _parse_kw(line)

    def next_kw(self):
        """advance to the next keyword -> (name, params), None at the end; unread data is skipped"""
        while True:
            raw = self.held
            if raw is not None:
                self.held = None
            else:
                raw = self._readline()
                if raw is None:
                    return None
            s = raw.strip()
            if s[:1] != b'*' or s[:2] == b'**':
                continue
            name, params = self._kw_line(s)
            if name == 'INCLUDE':
                self._include(pstr(params, 'INPUT'))
                continue
            if name in INPUT_DATA_KW:
                inc = pstr(params, 'INPUT')
                if inc:
                    self._include(inc, '*%s, input=' % name.title())
            return name, params

    def _collect(self):
        """all data lines of the current block as stripped bytes, parameters substituted"""
        out = []
        env = self.env
        while True:
            raw = self._readline()
            if raw is None:
                break
            s = raw.strip()
            if not s:
                if self.keep_blank:
                    out.append(b'')
                continue
            if s[:1] == b'*':
                if s[:2] == b'**':
                    continue
                if s[1:].lstrip()[:7].upper() == b'INCLUDE':
                    name, params = self._kw_line(s)
                    self._include(pstr(params, 'INPUT'))
                    continue
                self.held = raw
                break
            if env is not None and b'<' in s:
                s = self._subst(_dec(s)).encode('utf-8')
            out.append(s)
        return out

    def files(self):
        """For the big tables:   for f in stream.files():
                                     for raw in f:
                                         if raw[0] == 42:            # '*': keyword or comment
                                             stream.held = raw; break
                                         ...
        Each `f` yields raw lines (bytes); the generator moves on through includes and comments."""
        if self.env is not None:
            yield iter(self._collect())
            return
        while self.f is not None:
            depth = len(self.stack)
            yield self.f
            raw = self.held
            if raw is None:
                if len(self.stack) == depth and self.f is not None:
                    self._pop()                     # that file is exhausted: go on in the including file
                continue
            s = raw.strip()
            if s[:2] == b'**':
                self.held = None
                continue
            if s[1:].lstrip()[:7].upper() == b'INCLUDE':
                self.held = None
                name, params = self._kw_line(s)
                self._include(pstr(params, 'INPUT'))
                continue
            return

    def rows(self, limit=None, blanks=False):
        """data lines of the current block as text -> (list of str, total number of lines).
        Blank lines are dropped unless `blanks` (Abaqus reads a blank data line as a line of zeros, which
        matters where the position of a line has a meaning: *NCOPY, *NMAP)."""
        out = []
        n = 0
        self.keep_blank = blanks
        try:
            for f in self.files():
                for raw in f:
                    s = raw.strip()
                    if not s:
                        if not blanks:
                            continue
                    elif s[:1] == b'*':
                        self.held = raw
                        break
                    n += 1
                    if limit is None or n <= limit:
                        out.append(_dec(s))
        finally:
            self.keep_blank = False
        return out, n

    def text(self, blanks=False):
        return self.rows(None, blanks)[0]


def read_blocks(path, depth=0, warnings=None):
    """-> list of [name, params, data_lines] (whole file in memory: for small files and old callers)."""
    check_memory(path, prescan(path)['bytes'])
    st = _Stream(path, warnings if warnings is not None else [])
    blocks = []
    try:
        while True:
            kw = st.next_kw()
            if kw is None:
                break
            blocks.append([kw[0], kw[1], st.text()])
    finally:
        st.close()
    return blocks


def collect_parameters(path, incomplete):
    """evaluate every *PARAMETER block of the file tree (definitions may follow their use)"""
    env = {}
    errors = []
    st = _Stream(path, [])
    try:
        while True:
            kw = st.next_kw()
            if kw is None:
                break
            if kw[0] == 'PARAMETER':
                if kw[1]:
                    incomplete.append('*Parameter 的表格/相关参数 (%s) 预览不支持' %
                                      ', '.join(k.lower() for k in kw[1]))
                    continue
                eval_parameters(st.text(), env, errors)
    finally:
        st.close()
    if errors:
        incomplete.append('*Parameter 表达式未能求值: ' + '; '.join(errors[:4]) +
                          (' 等 %d 条' % len(errors) if len(errors) > 4 else ''))
    return env


# ---------------------------------------------------------------- contexts
class Ctx(object):
    """Where mesh keywords land: the root (flat INP / assembly level), one instance, or a part template
    (a part is read once into its own compact mesh and copied for every instance of it)."""

    def __init__(self, index, name, mesh, nsets, elsets, template=False):
        self.index = index
        self.name = name
        self.prefix = (name + '.') if (name and not template) else ''
        self.mesh = mesh
        self.nsets = nsets
        self.elsets = elsets
        self.template = template
        self.node_map = LabelMap()
        self.elem_map = LabelMap()
        self.system = None        # active *SYSTEM frame (origin, ex, ey, ez)
        self.transform = None     # instance placement, applied when the instance is complete
        self.deferred = []        # element rows that referenced nodes not defined yet
        self.deferred_gen = []    # *ELGEN rows whose master element did not exist yet
        self.surf_defs = []       # *Surface definitions, resolved when the context is complete
        self.sec_defs = []        # template only: sections / rigid bodies to hand to each instance
        self.rigid_defs = []
        self.display = 0          # position in the instance list (0 = not an instance)
        self.nrange = (0, 0)      # node index range of an instance


def _make_transform(lines):
    """*Instance data lines -> (function, translation, rotation). Translation first, then the
    rotation about the axis a-b (as Abaqus applies them)."""
    t = [0.0, 0.0, 0.0]
    rot = None
    if lines:
        v = [_fnum(x) for x in _toks(lines[0]) if x != '']
        if len(v) >= 3 and None not in v[:3]:
            t = v[:3]
    if len(lines) > 1:
        v = [_fnum(x) for x in _toks(lines[1]) if x != '']
        if len(v) >= 7 and None not in v[:7]:
            rot = v[:7]
    if rot is None and t == [0.0, 0.0, 0.0]:
        return None, t, None
    if rot is None:
        tx, ty, tz = t
        return (lambda x, y, z: (x + tx, y + ty, z + tz)), t, None
    ax, ay, az, bx, by, bz, ang = rot
    ux, uy, uz = bx - ax, by - ay, bz - az
    n = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
    ux, uy, uz = ux / n, uy / n, uz / n
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    tx, ty, tz = t

    def tf(x, y, z):
        px, py, pz = x + tx - ax, y + ty - ay, z + tz - az
        d = ux * px + uy * py + uz * pz
        cx, cy, cz = uy * pz - uz * py, uz * px - ux * pz, ux * py - uy * px
        return (ax + px * c + cx * s + ux * d * (1 - c),
                ay + py * c + cy * s + uy * d * (1 - c),
                az + pz * c + cz * s + uz * d * (1 - c))
    return tf, t, rot


def _tessellate_profile(rows):
    """START / LINE / CIRCL / PARAB rows of an analytical rigid surface -> (polyline [(x, y)], n segments)"""
    pts = []
    nseg = 0
    for t in rows:
        if not t or not t[0]:
            continue
        op = t[0].upper()
        v = _floats(t[1:])
        if op.startswith('START') and len(v) >= 2:
            pts.append((v[0], v[1]))
        elif not pts:
            continue
        elif op.startswith('LINE') and len(v) >= 2:
            pts.append((v[0], v[1]))
            nseg += 1
        elif op.startswith('CIRCL') and len(v) >= 4:
            x0, y0 = pts[-1]
            xe, ye, xc, yc = v[:4]
            a0 = math.atan2(y0 - yc, x0 - xc)
            a1 = math.atan2(ye - yc, xe - xc)
            da = a1 - a0
            while da > math.pi:             # always the minor arc (segments must subtend < 180 deg)
                da -= 2 * math.pi
            while da < -math.pi:
                da += 2 * math.pi
            r0 = math.hypot(x0 - xc, y0 - yc)
            r1 = math.hypot(xe - xc, ye - yc)
            n = max(2, int(abs(da) / math.radians(7.5)) + 1)
            for k in range(1, n):
                f = k / float(n)
                r = r0 + (r1 - r0) * f
                pts.append((xc + r * math.cos(a0 + da * f), yc + r * math.sin(a0 + da * f)))
            pts.append((xe, ye))
            nseg += 1
        elif op.startswith('PARAB') and len(v) >= 4:
            x0, y0 = pts[-1]
            xm, ym, xe, ye = v[:4]
            for k in range(1, 9):
                s = k / 8.0
                w0, w1, w2 = (1 - s) * (1 - 2 * s), 4 * s * (1 - s), s * (2 * s - 1)
                pts.append((w0 * x0 + w1 * xm + w2 * xe, w0 * y0 + w1 * ym + w2 * ye))
            nseg += 1
    return pts, nseg


def _shift_array(a, off, chunk=65536):
    """array('i') with `off` added to every item (in chunks: no big temporary list)"""
    if not off:
        return array('i', a)
    out = array('i')
    for i in range(0, len(a), chunk):
        out.extend([v + off for v in a[i:i + chunk]])
    return out


class Model(object):
    def __init__(self, path, force=False, max_auto_mb=None):
        """path: the INP.  Files above the automatic size limit raise TooLargeError unless force=True;
        with force=True they still raise MemoryRefusedError when the machine lacks the memory."""
        self.path = path
        self.warnings = []
        self.incomplete = []       # reasons why the preview may not show the whole model
        self.external = False      # (part of) the mesh comes from another analysis: references may dangle
        self.assembly_name = ''
        self.missing = []          # unresolved references
        self._missing_seen = set()
        self.input_errors = []     # input that Abaqus itself rejects (found by running Abaqus on test inputs)
        self.files_missing = False  # an *Include / input= file was not found: part of the model is unknown
        self.nonhybrid_warning = False
        self.heading = ''
        self.mesh = Mesh()
        self.types = ['']          # element type names; code 0 = a replaced (dead) element
        self._tcode = {'': 0}
        self.fams = [(None, 0, None)]
        m = self.mesh
        self.coords = _CoordView(m.xyz)
        self.e_conn = _ConnView(m)
        self.e_type = _TypeView(m, self.types)
        self.n_label, self.n_inst = m.n_label, m.n_inst
        self.e_inst, self.e_sec, self.e_label = m.e_inst, m.e_sec, m.e_label
        self.nsets = {}            # upper -> {'name','ids','internal','pieces'}
        self.elsets = {}
        self.surfaces = {}         # upper -> {'name','type','fe','ff','nodes','analytic'}
        self.ctxs = [Ctx(0, '', m, self.nsets, self.elsets)]
        self.root = self.ctxs[0]
        self.inst_by_key = {}
        self.parts = {}            # upper -> {'name', 'tmpl', 'nodes'}
        self.part_order = []
        self.instances = []        # dict per instance
        self.materials = []
        self.sections = []
        self.ties = []
        self.contact_pairs = []
        self.general_contact = False
        self.gcontact = {'allExterior': False, 'inclusions': [], 'exclusions': [], 'assignments': [],
                         'step': '', 'other': []}
        self.surf_ints = []
        self.constraints = []      # couplings, rigid bodies, embedded, mpc, equation ... (one entry each)
        self.amplitudes = []
        self.init_conds = []
        self.initial_bcs = []
        self.steps = []
        self.bc_by_id = {}
        self.load_by_id = {}
        self.con_by_id = {}
        self.unknown_types = {}
        self.features = {}         # mesh generation / parametric keywords seen -> count
        self.parameters = None
        self.transform_sets = []   # node sets of *TRANSFORM
        self.ref_nodes = {}        # node -> short label (reference / control nodes of constraints)
        self.auto_nodes = []       # reference nodes that Abaqus creates itself
        self.rigid_elems = set()
        self.display_ctx = set()   # instances that are display bodies (drawn only, no part in the analysis)
        self.user_nodes = {}       # *USER ELEMENT type -> number of nodes
        self._con_nodes = {}       # constraint id -> array of the nodes it holds
        self._sec_defs = []
        self._rigid_defs = []
        self._final = False
        self._skin_all = None
        self.n_dead = 0
        self.type_counts = {}
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        scan = prescan(path)
        self.size_bytes = scan['bytes']
        self.n_files = max(1, scan['files'])
        check_memory(path, scan['bytes'], force, max_auto_mb)
        self._inst_left = dict(scan['instances'])
        self._parse(scan)

    # ------------------------------------------------------------ bookkeeping
    def _miss(self, msg):
        if msg not in self._missing_seen:
            self._missing_seen.add(msg)
            self.missing.append(msg)

    def _feature(self, name):
        self.features[name] = self.features.get(name, 0) + 1

    def _code(self, etype):
        c = self._tcode.get(etype)
        if c is None:
            c = self._tcode[etype] = len(self.types)
            self.types.append(etype)
            fam, n = classify(etype)
            self.fams.append((fam, n, tuple(FACE_TAB[fam].items()) if fam in SOLID_FACES else None))
        return c

    def _set(self, table, name, internal=False):
        key = name.upper()
        ent = table.get(key)
        if ent is None:
            ent = table[key] = {'name': name, 'ids': None, 'internal': bool(internal), 'pieces': [],
                                'unsorted': False, 'nmiss': 0}
        return ent

    def _find_set(self, is_n, ctx, name):
        table = ctx.nsets if is_n else ctx.elsets
        if ctx.prefix:
            ent = table.get((ctx.prefix + name).upper())
            if ent is not None:
                return ent
        ent = table.get(name.upper())
        if ent is None and self.assembly_name and name.upper().startswith(self.assembly_name.upper() + '.'):
            ent = table.get(name[len(self.assembly_name) + 1:].upper())      # Assembly-1.Part-1-1.Set-1
        return ent

    def find_surface(self, name, ctx=None):
        """surface by name as written in the input (instance- or assembly-qualified) -> entry or None"""
        key = name.upper()
        ent = self.surfaces.get((ctx.prefix + name).upper()) if (ctx is not None and ctx.prefix) else None
        if ent is None:
            ent = self.surfaces.get(key)
        if ent is None and self.assembly_name and key.startswith(self.assembly_name.upper() + '.'):
            ent = self.surfaces.get(key[len(self.assembly_name) + 1:])
        return ent

    def _token(self, ctx, tok, is_n=True):
        """single node / element reference ('12' or 'Instance.12') -> index or None"""
        try:
            return (ctx.node_map if is_n else ctx.elem_map).get(int(tok))
        except ValueError:
            pass
        if '.' in tok:
            a, b = tok.rsplit('.', 1)
            inst = self.inst_by_key.get(a.upper())
            if inst is None and self.assembly_name and a.upper().startswith(self.assembly_name.upper() + '.'):
                inst = self.inst_by_key.get(a[len(self.assembly_name) + 1:].upper())
            if inst is not None and _is_int(b):
                return (inst.node_map if is_n else inst.elem_map).get(int(b))
        return None

    def _node_token(self, ctx, tok):
        return self._token(ctx, tok, True)

    def _eval_set(self, is_n, ent, stack):
        """sets are stored as recipes and evaluated late, so that definition order does not matter
        -> array('i') of indices, ascending and unique (in the given order for unsorted sets)"""
        key = id(ent)
        if key in stack:
            return array('i')
        stack = stack + (key,)
        out = array('i')
        miss = 0
        pieces = ent['pieces']
        clean = len(pieces) == 1          # a single piece that is known to be ascending needs no sorting
        for p in pieces:
            k = p[0]
            if k == 0:
                out.extend(p[1])
                clean = clean and p[2]
            elif k == 1:
                m = p[1].node_map if is_n else p[1].elem_map
                d = m.dense()
                if d is not None:
                    lo, hi, off = d
                    lab = p[2]
                    for i in range(0, len(lab), 65536):
                        part = lab[i:i + 65536]
                        good = [v + off for v in part if lo <= v <= hi]
                        miss += len(part) - len(good)
                        out.extend(good)
                else:
                    get = m.get
                    for lab in p[2]:
                        g = get(lab)
                        if g is None:
                            miss += 1
                        else:
                            out.append(g)
                clean = False
            elif k == 2:
                m = p[1].node_map if is_n else p[1].elem_map
                d = m.dense()
                a, b, inc = p[2], p[3], p[4]
                if d is not None:
                    lo, hi, off = d
                    if a < lo:
                        a += ((lo - a + inc - 1) // inc) * inc
                    b = min(b, hi)
                    if b >= a:
                        out.extend(range(a + off, b + off + 1, inc))
                else:
                    get = m.get
                    for lab in range(a, b + 1, inc):
                        g = get(lab)
                        if g is not None:
                            out.append(g)
                    clean = False
            elif k == 3:
                sub = self._find_set(is_n, p[1], p[2])
                if sub is ent:
                    continue                    # a set that lists itself: nothing to add
                if sub is None:
                    g = self._token(p[1], p[2], is_n)
                    if g is not None:
                        out.append(g)
                    elif self._final:
                        self._miss('*%s %s 引用了未定义的 %s' % ('Nset' if is_n else 'Elset', ent['name'], p[2]))
                else:
                    out.extend(sub['ids'] if sub['ids'] is not None else self._eval_set(is_n, sub, stack))
                clean = False
            elif k == 4:
                mesh = p[1].mesh
                off, nodes = mesh.e_off, mesh.e_nodes
                for e in (self.elems_of(p[2], p[1]) or ()):
                    out.extend(nodes[off[e]:off[e + 1]])
                clean = False
        ent['nmiss'] = miss
        if ent['unsorted']:
            seen = set()
            res = array('i')
            for g in out:
                if g not in seen:
                    seen.add(g)
                    res.append(g)
            return res
        return out if clean else _sorted_unique(out)

    def _ids(self, is_n, ent):
        ids = ent['ids']
        if ids is None:
            ids = self._eval_set(is_n, ent, ())
            if self._final:
                ent['ids'] = ids
        return ids

    def nodes_of(self, tok, ctx=None):
        """node or node set reference -> sequence of node indices (None if unresolved)"""
        ctx = ctx or self.root
        g = self._token(ctx, tok, True)
        if g is not None:
            return array('i', [g])
        ent = self._find_set(True, ctx, tok)
        if ent is not None:
            return self._ids(True, ent)
        return None

    def elems_of(self, tok, ctx=None):
        ctx = ctx or self.root
        g = self._token(ctx, tok, False)
        if g is not None:
            return array('i', [g])
        ent = self._find_set(False, ctx, tok)
        if ent is not None:
            return self._ids(False, ent)
        return None

    def _put_node(self, ctx, lab, xyz):
        g = ctx.node_map.get(lab)
        mesh = ctx.mesh
        if g is None:
            g = len(mesh.n_label)
            ctx.node_map.add(lab, g)
            mesh.xyz.extend(xyz)
            mesh.n_label.append(lab)
            mesh.n_inst.append(ctx.index)
        else:
            i = 3 * g
            mesh.xyz[i], mesh.xyz[i + 1], mesh.xyz[i + 2] = xyz[0], xyz[1], xyz[2]
        return g

    def node_name(self, g):
        i = self.n_inst[g]
        return ('%s.%d' % (self.ctxs[i].name, self.n_label[g])) if i else str(self.n_label[g])

    # ------------------------------------------------------------ mesh keywords
    def _mesh_kw(self, ctx, name, params, st):
        if name == 'NODE':
            self._kw_node(ctx, params, st)
        elif name == 'ELEMENT':
            self._kw_element(ctx, params, st)
        elif name == 'NSET':
            self._kw_set(ctx, True, params, st)
        elif name == 'ELSET':
            self._kw_set(ctx, False, params, st)
        elif name == 'SURFACE':
            self._kw_surface(ctx, params, st.text())
        elif name == 'SYSTEM':
            self._kw_system(ctx, st.text())
        else:
            self._feature(name)
            # *NCOPY / *NMAP: the meaning of a data line depends on its position, blank lines count
            getattr(self, '_kw_' + name.lower())(ctx, params, st.text(name in ('NCOPY', 'NMAP')))

    def _kw_node(self, ctx, params, st):
        kind = pstr(params, 'SYSTEM', 'R').upper()[:1]
        fr = ctx.system
        plain = kind not in ('C', 'S') and fr is None
        mesh = ctx.mesh
        xyz, nlab, ninst = mesh.xyz, mesh.n_label, mesh.n_inst
        nmap = ctx.node_map
        get, add = nmap.get, nmap.add
        idx = ctx.index
        nset = pstr(params, 'NSET')
        ids = array('i') if nset else None
        ordered = True
        g = len(nlab)
        top = nmap.top()
        for f in st.files():
            for raw in f:
                t = raw.split(b',')
                try:
                    lab = int(t[0])
                    n = len(t)
                    x = float(t[1]) if n > 1 else 0.0
                    y = float(t[2]) if n > 2 else 0.0
                    z = float(t[3]) if n > 3 else 0.0
                except ValueError:
                    s = raw.strip()
                    if not s:
                        continue
                    if s[:1] == b'*':
                        st.held = raw
                        break
                    v = [(_fnum(_dec(q).strip()) if q.strip() else 0.0) for q in t[1:4]]
                    if not _is_int(t[0].strip()) or None in v:
                        continue
                    lab = int(t[0])
                    x, y, z = (v + [0.0, 0.0, 0.0])[:3]
                if not plain:
                    x, y, z = _curvilinear(kind, x, y, z)
                    if fr is not None:
                        x, y, z = _to_global(fr, (x, y, z))
                if top is not None and lab > top:
                    top = lab
                    old = None
                else:
                    old = get(lab)
                if old is None:
                    add(lab, g)
                    xyz.append(x)
                    xyz.append(y)
                    xyz.append(z)
                    nlab.append(lab)
                    ninst.append(idx)
                    if ids is not None:
                        ids.append(g)
                    g += 1
                else:                               # defined again: the last definition wins
                    i = 3 * old
                    xyz[i], xyz[i + 1], xyz[i + 2] = x, y, z
                    if ids is not None:
                        ids.append(old)
                        ordered = False
        if nset:
            self._set(ctx.nsets, ctx.prefix + nset)['pieces'].append((0, ids, ordered))

    def _add_element(self, ctx, lab, code, conn):
        """append one element (or replace the element that already has this label) -> index"""
        mesh = ctx.mesh
        emap = ctx.elem_map
        e = emap.get(lab)
        if e is not None:
            o = mesh.e_off[e]
            if mesh.e_off[e + 1] - o == len(conn):      # redefinition: the last one wins
                mesh.e_code[e] = code
                for i, v in enumerate(conn):
                    mesh.e_nodes[o + i] = v
                return e
            mesh.e_code[e] = 0                          # different size: retire the old entry
            emap.extra[lab] = e = len(mesh.e_code)
        else:
            e = len(mesh.e_code)
            emap.add(lab, e)
        mesh.e_code.append(code)
        mesh.e_nodes.extend(conn)
        mesh.e_off.append(len(mesh.e_nodes))
        mesh.e_inst.append(ctx.index)
        mesh.e_sec.append(-1)
        mesh.e_label.append(lab)
        return e

    def _element_tokens(self, ctx, toks):
        """connectivity tokens (text) -> list of node indices, None when a node is not defined (yet)"""
        conn = []
        for tok in toks:
            if not tok:
                continue
            g = self._token(ctx, tok, True)
            if g is None:
                return None
            conn.append(g)
        return conn

    def _kw_element(self, ctx, params, st):
        etype = pstr(params, 'TYPE', '?').upper()
        code = self._code(etype)
        elset = pstr(params, 'ELSET')
        mesh = ctx.mesh
        e_code, e_off, e_nodes = mesh.e_code, mesh.e_off, mesh.e_nodes
        e_inst, e_sec, e_label = mesh.e_inst, mesh.e_sec, mesh.e_label
        nmap, emap = ctx.node_map, ctx.elem_map
        nget = nmap.get
        idx = ctx.index
        ids = array('i')
        ordered = True
        dense = nmap.dense()
        lo, hi, off = dense if dense is not None else (0, -1, 0)
        top = emap.top()
        eget = emap.get
        nfull = self.user_nodes.get(etype) or node_count(etype)   # nodes per element when the type fixes it
        perm = corner_perm(etype)
        e = len(e_code)
        buf = None
        pending = []                                   # logical lines that need the careful path
        for f in st.files():
            for raw in f:
                if raw[0] == 42:                       # '*': a keyword or a comment
                    st.held = raw
                    break
                s = raw.rstrip()
                if s[-1:] == b',':                     # connectivity may continue on the next line
                    s = s if buf is None else buf + s
                    if not nfull or s.count(b',') <= nfull:
                        buf = s
                        continue
                    s = s[:-1]                         # all nodes are there: just a trailing comma
                    buf = None
                elif buf is not None:
                    s = buf + s
                    buf = None
                try:
                    vals = [int(x) for x in s.split(b',')]
                    lab = vals[0]
                    conn = vals[1:]
                    if not conn:
                        continue
                    if dense is not None:
                        if min(conn) < lo or max(conn) > hi:
                            raise ValueError
                        if off:
                            conn = [v + off for v in conn]
                    else:
                        conn = [nget(v) for v in conn]
                        if None in conn:
                            raise ValueError
                    if top is not None and lab > top:
                        top = lab
                    elif eget(lab) is not None:
                        raise ValueError               # defined again: handled below
                    if perm is not None and len(conn) == len(perm):
                        conn = [conn[i] for i in perm]
                except ValueError:
                    if not s.strip():
                        continue
                    if s.lstrip()[:1] == b'*':
                        st.held = raw
                        break
                    pending.append(s)
                    continue
                emap.add(lab, e)
                e_code.append(code)
                e_nodes.extend(conn)
                e_off.append(len(e_nodes))
                e_inst.append(idx)
                e_sec.append(-1)
                e_label.append(lab)
                ids.append(e)
                e += 1
        if buf is not None:
            pending.append(buf)
        for s in pending:                              # blanks, qualified names, redefinitions, late nodes
            t = [x.replace('"', '').strip() for x in _dec(s).split(',')]
            if not _is_int(t[0]):
                continue
            conn = self._element_tokens(ctx, t[1:])
            if conn is None:
                ctx.deferred.append((int(t[0]), code, t[1:], elset, perm))
            elif conn:
                if perm is not None and len(conn) == len(perm):
                    conn = [conn[i] for i in perm]
                ids.append(self._add_element(ctx, int(t[0]), code, conn))
                ordered = False
        if elset:
            self._set(ctx.elsets, ctx.prefix + elset)['pieces'].append((0, ids, ordered))

    def _kw_set(self, ctx, is_n, params, st):
        kw = 'NSET' if is_n else 'ELSET'
        sname = pstr(params, kw)
        if not sname:
            return
        tctx = ctx
        inst = pstr(params, 'INSTANCE')
        if inst:
            tctx = self.inst_by_key.get(inst.upper())
            if tctx is None:
                self._miss('*%s %s 引用了不存在的实例 %s' % (kw.title(), sname, inst))
                return
        ent = self._set(ctx.nsets if is_n else ctx.elsets, ctx.prefix + sname, pget(params, 'INTERNAL'))
        if pget(params, 'UNSORTED'):
            ent['unsorted'] = True
        pieces = ent['pieces']
        if is_n and pget(params, 'ELSET') is not None:
            pieces.append((4, tctx, pstr(params, 'ELSET')))
        elif pget(params, 'GENERATE') is not None:
            for line in st.text():
                t = [x for x in _toks(line) if x != '']
                if len(t) >= 2 and _is_int(t[0]) and _is_int(t[1]):
                    inc = int(t[2]) if len(t) > 2 and _is_int(t[2]) and int(t[2]) > 0 else 1
                    pieces.append((2, tctx, int(t[0]), int(t[1]), inc))
        else:
            labels = array('i')
            for f in st.files():
                for raw in f:
                    if raw[0] == 42:
                        st.held = raw
                        break
                    try:
                        labels.extend([int(x) for x in raw.split(b',')])
                    except ValueError:
                        if raw.lstrip()[:1] == b'*':
                            st.held = raw
                            break
                        for tok in raw.split(b','):
                            tok = tok.strip()
                            if not tok:
                                continue
                            try:
                                labels.append(int(tok))
                            except ValueError:
                                if len(labels):
                                    pieces.append((1, tctx, labels))
                                    labels = array('i')
                                pieces.append((3, tctx, _dec(tok).replace('"', '').strip()))
            if len(labels):
                pieces.append((1, tctx, labels))

    def _new_surface(self, ctx, sname, stype, internal):
        key = (ctx.prefix + sname).upper()
        ent = self.surfaces.get(key)
        if ent is None:
            ent = self.surfaces[key] = {'name': ctx.prefix + sname, 'type': stype, 'fe': array('i'),
                                        'ff': array('B'), 'nodes': array('i'), 'internal': bool(internal),
                                        'analytic': None, 'inst': ctx.index}
        return ent

    def _kw_surface(self, ctx, params, data):
        sname = pstr(params, 'NAME')
        if not sname:
            return
        stype = _norm(pstr(params, 'TYPE', 'ELEMENT'))
        hits = [full for full in ('ELEMENT', 'NODE', 'SEGMENTS', 'CYLINDER', 'REVOLUTION', 'CUTTING SURFACE',
                                  'EULERIAN MATERIAL', 'USER') if _squash(full).startswith(_squash(stype))]
        if len(hits) == 1:
            stype = hits[0]
        ent = None if ctx.template else self._new_surface(ctx, sname, stype, pget(params, 'INTERNAL'))
        ctx.surf_defs.append((ent, sname, params, data, stype))

    def _kw_system(self, ctx, data):
        rows = [_floats(_toks(line)) for line in data]
        if not rows or len(rows[0]) < 3:
            ctx.system = None
            return
        self._feature('SYSTEM')
        v = rows[0]
        a = tuple(v[:3])
        b = tuple(v[3:6]) if len(v) >= 6 else None
        c = tuple(rows[1][:3]) if len(rows) > 1 and len(rows[1]) >= 3 else None
        if b is not None and _unit(_sub(b, a)) is None:
            b = None
        if b is not None:
            u = _unit(_sub(b, a))
            if c is None and math.hypot(u[0], u[1]) < 1e-6:
                # Abaqus 2021: "The x-axis and the z-axis of the local coordinate system are parallel"
                self.input_errors.append('*System 只给了原点和局部 x 轴上的一点, 而这条 x 轴与全局 Z 轴平行: '
                                         'Abaqus 会报错 (x 轴与 z 轴平行), 需要再给第三点')
            elif c is not None and _unit(_cross(u, _sub(c, a))) is None:
                self.input_errors.append('*System 的三个定义点共线, 局部坐标系无法确定')
        ctx.system = None if (b is None and a == (0.0, 0.0, 0.0)) else _frame(a, b, c)

    @staticmethod
    def _p(mesh, g):
        a = mesh.xyz
        i = 3 * g
        return (a[i], a[i + 1], a[i + 2])

    def _by_label(self, ctx, ids):
        lab = ctx.mesh.n_label
        return sorted(ids, key=lambda g: lab[g])

    # ---- *NGEN: nodes along a line / arc / parabola between two existing nodes
    def _kw_ngen(self, ctx, params, data):
        mode = pstr(params, 'LINE').upper()[:1]
        esys = pstr(params, 'SYSTEM', 'RC').upper()
        esys = 'C' if esys.startswith('C') else 'S' if esys.startswith('S') else 'R'
        fr = ctx.system
        mesh = ctx.mesh
        nmap = ctx.node_map
        ids = array('i')
        for row in data:
            t = _toks(row)
            if len(t) < 2 or not _is_int(t[0]) or not _is_int(t[1]):
                continue
            n1, n2 = int(t[0]), int(t[1])
            inc = abs(int(t[2])) if len(t) > 2 and _is_int(t[2]) and int(t[2]) != 0 else 1
            g1, g2 = nmap.get(n1), nmap.get(n2)
            if g1 is None or g2 is None:
                self._miss('*Ngen 的端节点 %d 或 %d 未定义' % (n1, n2))
                continue
            if n2 < n1:
                inc = -inc
            nint = (n2 - n1) // inc if inc else 0
            if nint < 1 or (n2 - n1) % inc:
                self._miss('*Ngen %d-%d 的节点号增量 %d 不能整除' % (n1, n2, abs(inc)))
                continue
            p1, p2 = self._p(mesh, g1), self._p(mesh, g2)
            extra = None
            if mode in ('C', 'P'):
                if len(t) > 3 and _is_int(t[3]) and int(t[3]) != 0:
                    ge = nmap.get(int(t[3]))
                    if ge is not None:
                        extra = self._p(mesh, ge)
                if extra is None:               # by coordinates; blank fields are zero (default: the origin)
                    v = (_floats(t[4:7]) + [0.0, 0.0, 0.0])[:3]
                    extra = _curvilinear(esys, v[0], v[1], v[2])
                    if fr is not None:
                        extra = _to_global(fr, extra)
            pts = []
            if mode == 'C':
                v1, v2 = _sub(p1, extra), _sub(p2, extra)
                r1, r2 = _len(v1), _len(v2)
                nv = (_floats(t[7:10]) + [0.0, 0.0, 0.0])[:3]
                normal = None
                if nv[0] or nv[1] or nv[2]:
                    normal = tuple(nv[:3])
                    if fr is not None:
                        normal = _vec_global(fr, normal)
                    normal = _unit(normal)
                if r1 <= 0 or r2 <= 0:
                    self._miss('*Ngen, line=C 的端点与圆心重合 (节点 %d-%d)' % (n1, n2))
                    continue
                if normal is not None:
                    ang = math.atan2(_dot(normal, _cross(v1, v2)), _dot(v1, v2))
                    if ang <= 1e-12:
                        ang += 2 * math.pi
                else:
                    normal = _unit(_cross(v1, v2))
                    if normal is None:
                        self._miss('*Ngen, line=C 的两端点与圆心共线, 需给出法向 (节点 %d-%d)' % (n1, n2))
                        continue
                    ang = math.acos(max(-1.0, min(1.0, _dot(v1, v2) / (r1 * r2))))
                u1 = _mul(v1, 1.0 / r1)
                for k in range(1, nint):
                    f = k / float(nint)
                    pts.append(_add(extra, _mul(_rot(u1, normal, ang * f), r1 + (r2 - r1) * f)))
            elif mode == 'P':
                for k in range(1, nint):
                    s = k / float(nint)
                    w0, w1, w2 = (1 - s) * (1 - 2 * s), 4 * s * (1 - s), s * (2 * s - 1)
                    pts.append(tuple(w0 * p1[i] + w1 * extra[i] + w2 * p2[i] for i in range(3)))
            else:
                for k in range(1, nint):
                    f = k / float(nint)
                    pts.append((p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f,
                                p1[2] + (p2[2] - p1[2]) * f))
            ids.append(g1)
            for k, p in enumerate(pts):
                ids.append(self._put_node(ctx, n1 + (k + 1) * inc, p))
            ids.append(g2)
        nset = pstr(params, 'NSET')
        if nset:
            self._set(ctx.nsets, ctx.prefix + nset)['pieces'].append((0, ids, False))

    # ---- *NFILL: nodes between two node sets
    def _kw_nfill(self, ctx, params, data):
        bias = _fnum(pstr(params, 'BIAS', '1'))
        if bias is None or bias <= 0:
            bias = 1.0
        two = pget(params, 'TWO STEP') is not None
        if pget(params, 'SINGULAR') is not None:
            self.incomplete.append('*Nfill, singular (裂尖四分点节点) 预览按均匀分布处理')
        mesh = ctx.mesh
        nlab = mesh.n_label
        ids = array('i')
        for row in data:
            t = _toks(row)
            if len(t) < 2 or not t[0] or not t[1]:
                continue
            a = self.nodes_of(t[0], ctx)
            b = self.nodes_of(t[1], ctx)
            if a is None or b is None:
                self._miss('*Nfill 引用了未定义的节点集 %s' % (t[0] if a is None else t[1]))
                continue
            a, b = self._by_label(ctx, a), self._by_label(ctx, b)
            nint = int(t[2]) if len(t) > 2 and _is_int(t[2]) and int(t[2]) > 0 else 1
            inc = int(t[3]) if len(t) > 3 and _is_int(t[3]) and int(t[3]) != 0 else 1
            if len(a) != len(b):
                self._miss('*Nfill 的两个节点集 %s (%d) 与 %s (%d) 节点数不同' % (t[0], len(a), t[1], len(b)))
            # fractions along the line; bias = ratio of adjacent intervals going from the first set to the second
            nb = nint // 2 if (two and nint % 2 == 0) else nint
            if abs(bias - 1.0) < 1e-12:
                base = [k / float(nb) for k in range(nb + 1)]
            else:
                r = 1.0 / bias
                base = [(r ** k - 1.0) / (r ** nb - 1.0) for k in range(nb + 1)]
            if nb != nint:
                frac = []
                for k in range(nb):
                    frac.append(base[k])
                    frac.append(0.5 * (base[k] + base[k + 1]))
                frac.append(1.0)
            else:
                frac = base
            ids.extend(a)
            ids.extend(b)
            for ga, gb in zip(a, b):
                pa, pb = self._p(mesh, ga), self._p(mesh, gb)
                la = nlab[ga]
                for k in range(1, nint):
                    f = frac[k]
                    ids.append(self._put_node(ctx, la + k * inc, (pa[0] + (pb[0] - pa[0]) * f,
                                                                 pa[1] + (pb[1] - pa[1]) * f,
                                                                 pa[2] + (pb[2] - pa[2]) * f)))
        nset = pstr(params, 'NSET')
        if nset:
            self._set(ctx.nsets, ctx.prefix + nset)['pieces'].append((0, ids, False))

    # ---- *NCOPY: copy a node set by shift / rotation / reflection
    def _kw_ncopy(self, ctx, params, data):
        old = pstr(params, 'OLD SET')
        chg = pstr(params, 'CHANGE NUMBER')
        src = self.nodes_of(old, ctx) if old else None
        if src is None or not _is_int(chg):
            self._miss('*Ncopy 的 old set=%s 未定义或缺少 change number' % old)
            return
        chg = int(chg)
        rows = [_floats(_toks(line)) for line in data]
        reflect = pstr(params, 'REFLECT').upper()
        mult = pstr(params, 'MULTIPLE', '1')
        mult = max(1, int(mult)) if _is_int(mult) else 1
        fn = first = None
        if pget(params, 'POLE') is not None:
            t0 = _toks(data[0]) if data else []
            pole = None
            if t0 and _is_int(t0[0]) and int(t0[0]) != 0:
                gp = ctx.node_map.get(int(t0[0]))
                if gp is not None:
                    pole = self._p(ctx.mesh, gp)
            if pole is None:
                pole = tuple((_floats(t0[1:4]) + [0.0, 0.0, 0.0])[:3])

            def from_pole(p):
                return (2 * p[0] - pole[0], 2 * p[1] - pole[1], 2 * p[2] - pole[2])
            fn = from_pole
        elif reflect:
            r0 = (rows[0] if rows else []) + [0.0] * 6
            a, b = tuple(r0[:3]), tuple(r0[3:6])
            u = _unit(_sub(b, a))
            c = tuple(((rows[1] if len(rows) > 1 else []) + [0.0] * 3)[:3])
            n = _unit(_cross(_sub(b, a), _sub(c, a)))

            def through_point(p):
                return (2 * a[0] - p[0], 2 * a[1] - p[1], 2 * a[2] - p[2])

            def through_line(p):
                q = _add(a, _mul(u, _dot(_sub(p, a), u)))
                return (2 * q[0] - p[0], 2 * q[1] - p[1], 2 * q[2] - p[2])

            def through_plane(p):
                d = 2 * _dot(_sub(p, a), n)
                return (p[0] - d * n[0], p[1] - d * n[1], p[2] - d * n[2])
            if reflect.startswith('P'):
                fn = through_point
            elif reflect.startswith('L'):
                fn = through_line if u is not None else None
            else:
                fn = through_plane if n is not None else None
            if fn is None:
                self._miss('*Ncopy, reflect 的定义点退化')
                return
        else:
            # SHIFT (Keywords Reference): "the translation is applied once before the rotation"; MULTIPLE is
            # "the number of times the rotation should be applied" - copy k is the shifted node turned k times
            tr = tuple(((rows[0] if rows else []) + [0.0] * 3)[:3])
            r1 = (rows[1] if len(rows) > 1 else []) + [0.0] * 7
            a, b, ang = tuple(r1[:3]), tuple(r1[3:6]), math.radians(r1[6])
            u = _unit(_sub(b, a))
            first = lambda p: _add(p, tr)                                   # noqa: E731

            def turn(p):
                if u is not None and ang:
                    return _add(a, _rot(_sub(p, a), u, ang))
                return p
            fn = turn
        if first is None:
            mult = 1                                    # MULTIPLE only goes with SHIFT
        mesh = ctx.mesh
        nlab = mesh.n_label
        ids = array('i')
        for g in self._by_label(ctx, src):
            p = self._p(mesh, g)
            if first is not None:
                p = first(p)
            lab = nlab[g]
            for k in range(1, mult + 1):
                p = fn(p)
                ids.append(self._put_node(ctx, lab + k * chg, p))
        new = pstr(params, 'NEW SET')
        if new:
            self._set(ctx.nsets, ctx.prefix + new)['pieces'].append((0, ids, False))

    # ---- *NMAP: map a node set through a coordinate system
    def _kw_nmap(self, ctx, params, data):
        kind = _squash(pstr(params, 'TYPE', 'RECTANGULAR'))
        hits = [t for t in NMAP_TYPES if t.startswith(kind)] if kind else []
        if len(hits) == 1:
            kind = hits[0]
        nset = pstr(params, 'NSET')
        ids = self.nodes_of(nset, ctx) if nset else None
        if ids is None:
            self._miss('*Nmap 的节点集 %s 未定义' % nset)
            return
        by_node = _squash(pstr(params, 'DEFINITION', 'COORDINATES')).startswith('N')
        toks = [_toks(line) for line in data]
        mesh = ctx.mesh

        def points(row, n):
            """n points of a data line: three coordinates each, or node numbers (definition=nodes).
            A point that is not given comes back as None."""
            t = toks[row] if row < len(toks) else []
            out = []
            if not any(t):
                return [None] * n                      # blank line: nothing given
            if by_node:
                for k in range(n):
                    g = ctx.node_map.get(int(t[k])) if k < len(t) and _is_int(t[k]) and int(t[k]) != 0 else None
                    out.append(self._p(mesh, g) if g is not None else None)
            else:
                v = _floats(t)
                if len(v) % 3:
                    v += [0.0] * (3 - len(v) % 3)      # trailing zeros left off
                for k in range(n):
                    out.append(tuple(v[3 * k:3 * k + 3]) if len(v) >= 3 * k + 3 else None)
            return out

        def numbers(row, n, blank):
            v = (_floats(toks[row]) if row < len(toks) else []) + [0.0] * n
            return [(x if x else blank) for x in v[:n]]

        a, b = points(0, 2)
        fn = None
        if a is None:
            pass
        elif kind == 'SCALE':
            sc = numbers(1, 3, 1.0)

            def fn(p):
                return (a[0] + sc[0] * (p[0] - a[0]), a[1] + sc[1] * (p[1] - a[1]), a[2] + sc[2] * (p[2] - a[2]))
        elif kind == 'TRANSLATION':
            u = _unit(_sub(b, a)) if b is not None else None
            mag = numbers(1, 1, 0.0)[0]
            if u is not None:
                def fn(p):
                    return (p[0] + mag * u[0], p[1] + mag * u[1], p[2] + mag * u[2])
        elif kind == 'ROTATION':
            u = _unit(_sub(b, a)) if b is not None else None
            c = points(1, 1)[0] or a
            ang = math.radians(numbers(2, 1, 0.0)[0])
            if u is not None:
                def fn(p):
                    return _add(c, _rot(_sub(p, c), u, ang))
        elif kind in ('RECTANGULAR', 'CYLINDRICAL', 'SPHERICAL', 'DIAMOND'):
            c, d = points(1, 2)
            sc = numbers(2, 3, 1.0)
            code = kind[0]
            fr = None
            if kind == 'RECTANGULAR':
                # only point a: "the coordinates of the nodes in the set are simply shifted"
                fr = _frame(a, b if (b is not None and _unit(_sub(b, a)) is not None) else None, c)
            elif kind == 'DIAMOND':
                axes = [_unit(_sub(q, a)) if q is not None else None for q in (b, c, d)]
                if None not in axes:
                    fr = (a, axes[0], axes[1], axes[2])       # skewed axes through a-b, a-c, a-d
            elif b is not None and _unit(_sub(b, a)) is not None:
                # cylindrical / spherical: a-b is the axis (local z), c lies in the plane theta = 0
                ez = _unit(_sub(b, a))
                ex = None
                if c is not None:
                    v = _sub(c, a)
                    ex = _unit(_sub(v, _mul(ez, _dot(v, ez))))
                if ex is None:
                    ex = _unit(_cross((0.0, 1.0, 0.0), ez)) or (1.0, 0.0, 0.0)
                fr = (a, ex, _cross(ez, ex), ez)
            if fr is not None:
                def fn(p):
                    return _to_global(fr, _curvilinear(code, p[0] * sc[0], p[1] * sc[1], p[2] * sc[2]))
        else:
            self.incomplete.append('*Nmap, type=%s 的坐标映射预览不支持, 节点集 %s 的 %d 个节点显示在映射前的位置'
                                   % (kind.title(), nset, len(ids)))
            return
        if fn is None:
            self._miss('*Nmap, type=%s (节点集 %s) 的定义点缺失或重合, 节点未移动' % (kind.title(), nset))
            return
        if kind not in NMAP_VERIFIED:
            self.warnings.append('*Nmap, type=%s 的映射约定未用 Abaqus 结果核对过, 节点集 %s 的位置请自行确认'
                                 % (kind.title(), nset))
        xyz = mesh.xyz
        for g in ids:
            i = 3 * g
            xyz[i], xyz[i + 1], xyz[i + 2] = fn((xyz[i], xyz[i + 1], xyz[i + 2]))

    # ---- *ELGEN: rows / layers of elements from a master element
    def _kw_elgen(self, ctx, params, data, final=False):
        emap = ctx.elem_map
        nget = ctx.node_map.get
        mesh = ctx.mesh
        nlab = mesh.n_label
        elset = pstr(params, 'ELSET')
        ids = array('i')
        bad = 0
        for row in data:
            t = _toks(row)
            if not t or not _is_int(t[0]):
                continue

            def num(i, default):
                return int(t[i]) if len(t) > i and _is_int(t[i]) and int(t[i]) != 0 else default
            master = int(t[0])
            e0 = emap.get(master)
            if e0 is None:
                if final:
                    self._miss('*Elgen 的主单元 %d 未定义' % master)
                else:                               # its nodes may be defined further down: try again later
                    ctx.deferred_gen.append((params, row))
                continue
            n1, ninc1, einc1 = num(1, 1), num(2, 1), num(3, 1)
            n2, ninc2, einc2 = num(4, 1), num(5, 1), num(6, 1)
            n3, ninc3, einc3 = num(7, 1), num(8, 1), num(9, 1)
            code = mesh.e_code[e0]
            base = [nlab[g] for g in mesh.e_nodes[mesh.e_off[e0]:mesh.e_off[e0 + 1]]]
            ids.append(e0)
            for l in range(n3):
                for r in range(n2):
                    for i in range(n1):
                        if not (i or r or l):
                            continue
                        dn = i * ninc1 + r * ninc2 + l * ninc3
                        lab_e = master + i * einc1 + r * einc2 + l * einc3
                        conn = [nget(lab + dn) for lab in base]
                        if None in conn:
                            if final:
                                bad += 1
                            else:
                                ctx.deferred.append((lab_e, code, [str(lab + dn) for lab in base], elset, None))
                            continue
                        ids.append(self._add_element(ctx, lab_e, code, conn))
        if bad:
            self._miss('*Elgen 生成的 %d 个单元引用了未定义的节点, 已跳过' % bad)
        if elset and len(ids):
            self._set(ctx.elsets, ctx.prefix + elset)['pieces'].append((0, ids, False))

    # ---- *ELCOPY
    def _kw_elcopy(self, ctx, params, data):
        old = pstr(params, 'OLD SET')
        src = self.elems_of(old, ctx) if old else None
        es, ns = pstr(params, 'ELEMENT SHIFT'), pstr(params, 'SHIFT NODES')
        if src is None or not _is_int(es) or not _is_int(ns):
            self._miss('*Elcopy 的 old set=%s 未定义或缺少 element shift / shift nodes' % old)
            return
        es, ns = int(es), int(ns)
        nget = ctx.node_map.get
        mesh = ctx.mesh
        nlab, elab = mesh.n_label, mesh.e_label
        ids = array('i')
        bad = 0
        for e in sorted(src, key=lambda q: elab[q]):
            conn = [nget(nlab[g] + ns) for g in mesh.e_nodes[mesh.e_off[e]:mesh.e_off[e + 1]]]
            if None in conn:
                bad += 1
                continue
            ids.append(self._add_element(ctx, elab[e] + es, mesh.e_code[e], conn))
        if bad:
            self._miss('*Elcopy 生成的 %d 个单元引用了未定义的节点, 已跳过' % bad)
        new = pstr(params, 'NEW SET')
        if new:
            self._set(ctx.elsets, ctx.prefix + new)['pieces'].append((0, ids, False))

    # ------------------------------------------------------------ parts and instances
    def _instantiate(self, tmpl, inst):
        """copy a part template into the model as instance `inst` (coordinates still in the part frame)"""
        gm, tm = self.mesh, tmpl.mesh
        n0, e0 = len(gm.n_label), len(gm.e_code)
        nn, ne = len(tm.n_label), len(tm.e_code)
        gm.xyz.extend(tm.xyz)
        gm.n_label.extend(tm.n_label)
        gm.n_inst.extend(array('i', [inst.index]) * nn)
        base = len(gm.e_nodes)
        src = tm.e_nodes
        if n0:
            for i in range(0, len(src), 65536):
                gm.e_nodes.extend([v + n0 for v in src[i:i + 65536]])
        else:
            gm.e_nodes.extend(src)
        src = tm.e_off
        if base:
            for i in range(1, len(src), 65536):
                gm.e_off.extend([v + base for v in src[i:i + 65536]])
        else:
            gm.e_off.extend(src[1:])
        gm.e_code.extend(tm.e_code)
        gm.e_label.extend(tm.e_label)
        gm.e_inst.extend(array('i', [inst.index]) * ne)
        gm.e_sec.extend(array('i', [-1]) * ne)
        inst.node_map = tmpl.node_map.shifted(n0)
        inst.elem_map = tmpl.elem_map.shifted(e0)
        for is_n, src_table, dst_table, off in ((True, tmpl.nsets, self.nsets, n0),
                                                (False, tmpl.elsets, self.elsets, e0)):
            for ent in src_table.values():
                new = self._set(dst_table, inst.prefix + ent['name'], ent['internal'])
                new['unsorted'] = ent['unsorted']
                for p in ent['pieces']:
                    if p[0] == 0:
                        new['pieces'].append((0, _shift_array(p[1], off), p[2]))
                    else:
                        new['pieces'].append((p[0], inst) + p[2:])
        for ent, sname, params, data, stype in tmpl.surf_defs:
            ent = self._new_surface(inst, sname, stype, pget(params, 'INTERNAL'))
            inst.surf_defs.append((ent, sname, params, data, stype))
        for name, params, data in tmpl.sec_defs:
            self._sec_defs.append((inst, name, params, data))
        for params in tmpl.rigid_defs:
            self._rigid_defs.append((inst, params))
        inst.deferred.extend(tmpl.deferred)
        inst.deferred_gen.extend(tmpl.deferred_gen)

    # ------------------------------------------------------------ closing a context
    def _close_ctx(self, ctx):
        """everything of this context has been read: late element rows, then its surfaces"""
        if ctx.deferred and not ctx.template:
            bad = []
            by_set = {}
            for lab, code, toks, elset, perm in ctx.deferred:
                conn = self._element_tokens(ctx, toks)
                if not conn:
                    bad.append(lab)
                    continue
                if perm is not None and len(conn) == len(perm):
                    conn = [conn[i] for i in perm]
                e = self._add_element(ctx, lab, code, conn)
                if elset:
                    by_set.setdefault(elset, array('i')).append(e)
            for elset, ids in by_set.items():
                self._set(ctx.elsets, ctx.prefix + elset)['pieces'].append((0, ids, False))
            if bad:
                self._miss('%s%d 个单元引用了未定义的节点 (如单元 %s), 预览中已跳过' %
                           (('实例 %s: ' % ctx.name) if ctx.name else '', len(bad),
                            ', '.join(str(x) for x in bad[:3])))
            ctx.deferred = []
        if ctx.deferred_gen and not ctx.template:
            gens, ctx.deferred_gen = ctx.deferred_gen, []
            for params, row in gens:
                self._kw_elgen(ctx, params, [row], final=True)
        if not ctx.template:
            for ent, sname, params, data, stype in ctx.surf_defs:
                self._resolve_surface(ctx, ent, params, data, stype)
            ctx.surf_defs = []

    # ------------------------------------------------------------ surfaces
    def face_nodes(self, e, code):
        """node indices of one face of an element: 1 = point, 2 = segment, more = polygon; None if n/a"""
        m = self.mesh
        fam, n, _ = self.fams[m.e_code[e]]
        if fam is None:
            return None
        o = m.e_off[e]
        if m.e_off[e + 1] - o < n:
            return None
        f = FACE_TAB.get(fam)
        f = f.get(code) if f is not None else None
        if f is None:
            return None
        nodes = m.e_nodes
        if fam == 'line':
            return tuple([nodes[o + (n - 1 if i < 0 else i)] for i in f])
        return tuple([nodes[o + i] for i in f])

    def surface_face(self, eidx, fid):
        """-> ('poly', nodes) | ('seg', (a, b)) | ('pt', node) | None;  fid = face name or code"""
        r = self.face_nodes(eidx, FID_CODE.get(fid, -1) if isinstance(fid, str) else fid)
        if r is None:
            return None
        return ('pt', r[0]) if len(r) == 1 else ('seg', r) if len(r) == 2 else ('poly', r)

    def _free_faces(self, els, fe, ff):
        """append the exterior faces of a group of elements to (fe, ff)"""
        m = self.mesh
        codes, off, nodes, fams = m.e_code, m.e_off, m.e_nodes, self.fams
        big = len(m.n_label) + 1
        seen = {}
        twosided = False
        for e in els:
            fam, n, ftab = fams[codes[e]]
            o = off[e]
            if fam is None or off[e + 1] - o < n:
                continue
            if ftab is not None:
                c = nodes[o:o + n]
                for code, f in ftab:
                    s = sorted([c[i] for i in f])
                    k = (s[0] * big + s[1]) * big + s[2]
                    if seen.pop(k, None) is None:
                        seen[k] = e * 16 + code
            elif fam in PLANE_FAMILIES:
                c = nodes[o:o + n]
                for i in range(n):
                    a, b = c[i], c[(i + 1) % n]
                    k = (a * big + b) if a < b else (b * big + a)
                    if seen.pop(k, None) is None:
                        seen[k] = e * 16 + 1 + i
            elif fam in SURFACE_FAMILIES:
                fe.append(e)
                ff.append(7)
                twosided = True
            elif fam == 'line':
                fe.append(e)
                ff.append(0)
        for v in seen.values():
            fe.append(v >> 4)
            ff.append(v & 15)
        return twosided

    def _free_edges(self, els, fe, ff):
        m = self.mesh
        codes, off, nodes, fams = m.e_code, m.e_off, m.e_nodes, self.fams
        big = len(m.n_label) + 1
        seen = {}
        for e in els:
            fam, n, ftab = fams[codes[e]]
            o = off[e]
            if fam not in SURFACE_FAMILIES or off[e + 1] - o < n:
                continue
            c = nodes[o:o + n]
            for i in range(n):
                a, b = c[i], c[(i + 1) % n]
                k = (a * big + b) if a < b else (b * big + a)
                if seen.pop(k, None) is None:
                    seen[k] = e * 16 + 9 + i
        for v in seen.values():
            fe.append(v >> 4)
            ff.append(v & 15)

    def _resolve_surface(self, ctx, ent, params, data, stype):
        rows = [_toks(line) for line in data]
        combine = pstr(params, 'COMBINE').upper()
        fe, ff = ent['fe'], ent['ff']
        if combine or pget(params, 'CROP') is not None:
            names = [t for t in (rows[0] if rows else []) if t]
            subs = []
            for nm in names:
                s = self.surfaces.get((ctx.prefix + nm).upper()) or self.surfaces.get(nm.upper())
                if s is None or s is ent:
                    ent['unresolved'] = True
                    self._miss('*Surface %s 引用了未定义的表面 %s' % (ent['name'], nm))
                else:
                    subs.append(s)
            if not subs:
                return
            faces = [e * 16 + c for e, c in zip(subs[0]['fe'], subs[0]['ff'])]
            nodes = list(subs[0]['nodes'])
            if combine:
                for s in subs[1:]:
                    fs = set(e * 16 + c for e, c in zip(s['fe'], s['ff']))
                    ns = set(s['nodes'])
                    if combine.startswith('U'):
                        have, haven = set(faces), set(nodes)
                        faces.extend(sorted(fs - have))
                        nodes.extend(sorted(ns - haven))
                    elif combine.startswith('I'):
                        faces = [f for f in faces if f in fs]
                        nodes = [g for g in nodes if g in ns]
                    else:
                        faces = [f for f in faces if f not in fs]
                        nodes = [g for g in nodes if g not in ns]
            else:
                box = _floats(rows[1]) if len(rows) > 1 else []
                if len(box) >= 6:
                    lo, hi = box[:3], box[3:6]
                    tol = 1e-9 * (max(abs(v) for v in box[:6]) or 1.0)
                    coords = self.coords

                    def inside(g):
                        p = coords[g]
                        return all(lo[i] - tol <= p[i] <= hi[i] + tol for i in range(3))
                    keep = []
                    for k in faces:
                        r = self.face_nodes(k >> 4, k & 15)
                        if r is not None and all(inside(g) for g in r):
                            keep.append(k)
                    faces = keep
                    nodes = [g for g in nodes if inside(g)]
            fe.extend([k >> 4 for k in faces])
            ff.extend([k & 15 for k in faces])
            ent['nodes'].extend(nodes)
            ent['type'] = subs[0]['type']
            return
        if stype == 'NODE':
            for t in rows:
                if t and t[0]:
                    ns = self.nodes_of(t[0], ctx)
                    if ns is None:
                        ent['unresolved'] = True
                        self._miss('*Surface %s 引用了未定义的节点集 %s' % (ent['name'], t[0]))
                    else:
                        ent['nodes'].extend(ns)
        elif stype == 'ELEMENT':
            for t in rows:
                if not t or not t[0]:
                    continue
                els = self.elems_of(t[0], ctx)
                if els is None:
                    ent['unresolved'] = True
                    self._miss('*Surface %s 引用了未定义的单元集 %s' % (ent['name'], t[0]))
                    continue
                fid = t[1].upper() if len(t) > 1 and t[1] else ''
                if fid == '':
                    if self._free_faces(els, fe, ff):
                        ent['twosided'] = True
                elif fid == 'EDGE':
                    self._free_edges(els, fe, ff)
                elif fid in FID_CODE:
                    fe.extend(els)
                    ff.frombytes(bytes([FID_CODE[fid]]) * len(els))
                else:
                    self.warnings.append('表面 %s 的面标识 %s 预览不认识, 这一行未显示' % (ent['name'], fid))
        elif stype in ('SEGMENTS', 'CYLINDER', 'REVOLUTION'):
            fr = None
            k = 0
            if stype != 'SEGMENTS' and rows and _fnum(rows[0][0]) is not None:
                # flat input: the local system is given first.  CYLINDER: a = origin, b on local x,
                # c on the generator (local z).  REVOLUTION: a = origin, b on the axis of revolution.
                v = _floats(rows[0]) + [0.0] * 6
                a, b = tuple(v[:3]), tuple(v[3:6])
                k = 1
                if stype == 'CYLINDER':
                    c = (0.0, 0.0, -1.0)
                    if len(rows) > 1 and _fnum(rows[1][0]) is not None:
                        c = tuple((_floats(rows[1]) + [0.0] * 3)[:3])
                        k = 2
                    ex = _unit(_sub(b, a)) or (1.0, 0.0, 0.0)
                    gen = _unit(_sub(c, a)) or (0.0, 0.0, -1.0)
                    ez = _mul(gen, -1.0)                       # c lies on the negative local z axis
                    ey = _unit(_cross(ez, ex)) or (0.0, 1.0, 0.0)
                    fr = (a, ex, ey, _cross(ex, ey))
                else:
                    ez = _unit(_sub(b, a)) or (0.0, 1.0, 0.0)
                    ex = _unit(_cross((0.0, 0.0, 1.0), ez)) or _unit(_cross((1.0, 0.0, 0.0), ez))
                    fr = (a, ex, _cross(ez, ex), ez)
            elif stype == 'REVOLUTION':
                # CAE writes the profile in the instance's own x-y plane, revolved about its y axis
                fr = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))
            pts, nseg = _tessellate_profile(rows[k:])
            if len(pts) < 2:
                self._miss('解析刚性面 %s 没有可用的线段定义' % ent['name'])
            ent['analytic'] = {'kind': stype, 'profile': pts, 'frame': fr, 'tf': ctx.transform, 'nseg': nseg,
                               'fillet': _fnum(pstr(params, 'FILLET RADIUS')) or 0.0}
        else:
            ent['other'] = True
            if stype != 'CUTTING SURFACE':          # a cutting surface is an output section, not geometry
                self.incomplete.append('表面 %s 的类型 type=%s 预览不支持, 没有显示' % (ent['name'], stype.title()))

    # ------------------------------------------------------------ main pass
    def _new_con(self, kw, params, rows, **extra):
        con = {'id': 'c%d' % (len(self.constraints) + 1), 'kw': kw, 'params': dict(params),
               'rows': rows[:4], 'nrows': len(rows)}
        con.update(extra)
        self.constraints.append(con)
        self.con_by_id[con['id']] = con
        return con

    def _ctx_block(self, ctx, name, params, st):
        """one keyword inside a part / instance"""
        if name in MESH_KW:
            self._mesh_kw(ctx, name, params, st)
        elif name in SECTION_KW:
            data = st.rows(4)[0]
            if ctx.template:
                ctx.sec_defs.append((name, params, data))
            else:
                self._sec_defs.append((ctx, name, params, data))
        elif name == 'RIGID BODY':
            if ctx.template:
                ctx.rigid_defs.append(params)
            else:
                self._rigid_defs.append((ctx, params))
        elif name in INCOMPLETE_KW:
            self.external = True
            self.incomplete.append('%s: 这部分网格不在本文件中' % INCOMPLETE_KW[name])

    def _parse(self, scan):
        env = None
        if scan['param']:
            env = collect_parameters(self.path, self.incomplete)
            self.parameters = env
            self._feature('PARAMETER')
        for nm in scan['missing']:
            self.incomplete.append('文件未找到: %s' % nm)
        if scan['missing']:
            self.files_missing = True
        st = _Stream(self.path, self.incomplete, env)
        try:
            self._read(st)
        finally:
            st.close()
            if st.nmissing:
                self.files_missing = True
        if st.unresolved:
            self.incomplete.append('参数引用 <%s> 没有对应的 *Parameter 定义, 相关数据按缺失处理' %
                                   '>, <'.join(sorted(st.unresolved)[:6]))
        if self.n_files > 1:
            self.features['INCLUDE'] = self.n_files - 1
        for p in self.part_order:
            p['tmpl'] = None                       # part templates are no longer needed
        self._finalize()

    def _read(self, st):
        part = None            # template context of the part being read
        inst = None            # [ctx, part key, translation, rotation, first node, first element]
        step = None
        mat = None
        surfint = None
        coupling = None
        bc_n = [0]
        ld_n = [0]
        self._bc_defs = []
        self._mpc_rows = []
        self._eq_rows = []
        root = self.root
        mesh = self.mesh

        def new_bc(params, data, step_name):
            out = []
            merged = {}
            for line in data:
                t = _toks(line)
                if not t or not t[0]:
                    continue
                tgt = t[0]
                ent = merged.get(tgt.upper())
                if ent is None:
                    bc_n[0] += 1
                    ent = {'id': 'bc%d' % bc_n[0], 'target': tgt, 'dofs': [], 'type': '', 'mag': [],
                           'params': dict(params), 'step': step_name, 'n': 0}
                    merged[tgt.upper()] = ent
                    out.append(ent)
                a = t[1].upper() if len(t) > 1 else ''
                if a in BC_TYPES:
                    ent['type'] = a
                    for d in BC_TYPES[a]:
                        if d not in ent['dofs']:
                            ent['dofs'].append(d)
                elif _is_int(a):
                    first = int(a)
                    last = int(t[2]) if len(t) > 2 and _is_int(t[2]) else first
                    mag = _fnum(t[3]) if len(t) > 3 and t[3] else None
                    for d in range(first, min(last, first + 40) + 1):
                        if d not in ent['dofs']:
                            ent['dofs'].append(d)
                    if mag is not None and mag != 0.0:
                        ent['mag'].append([first, last, mag])
            # a block that restrains many single nodes one by one: show them as groups, not as hundreds of BCs
            singles = [e for e in out if _is_int(e['target'])]
            if len(singles) > 12:
                groups = {}
                keep = []
                for e in out:
                    if not _is_int(e['target']):
                        keep.append(e)
                        continue
                    sig = (tuple(e['dofs']), e['type'], tuple(tuple(m) for m in e['mag']))
                    grp = groups.get(sig)
                    if grp is None:
                        grp = groups[sig] = e
                        e['_labels'] = [e['target']]
                        keep.append(e)
                    else:
                        grp['_labels'].append(e['target'])
                for e in keep:
                    labs = e.get('_labels')
                    if labs and len(labs) > 1:
                        e['target'] = '%d 个节点 (%s%s)' % (len(labs), ', '.join(labs[:3]),
                                                         ' …' if len(labs) > 3 else '')
                    elif labs:
                        del e['_labels']
                out = keep
            self._bc_defs.extend(out)
            return out

        def new_load(name, params, rows, nrows, step_name):
            ld_n[0] += 1
            ent = {'id': 'ld%d' % ld_n[0], 'kw': name, 'params': dict(params),
                   'rows': [_toks(l) for l in rows], 'nrows': nrows, 'step': step_name}
            self.load_by_id[ent['id']] = ent
            return ent

        while True:
            kw = st.next_kw()
            if kw is None:
                break
            name, params = kw
            # ---- material sub-options
            if mat is not None and name not in MATERIAL_END:
                rows, nrows = st.rows(8)
                opt = {'kw': name.title(), 'params': dict(params), 'rows': [_toks(l) for l in rows],
                       'nrows': nrows}
                mat['options'].append(opt)
                if name == 'ELASTIC' and rows and pstr(params, 'TYPE', 'ISOTROPIC').upper().startswith('ISO'):
                    t = _toks(rows[0])
                    v = _fnum(t[0])
                    if v is not None:
                        mat['E'] = v
                    if len(t) > 1 and _fnum(t[1]) is not None:
                        mat['nu'] = _fnum(t[1])
                elif name == 'DENSITY' and rows:
                    v = _fnum(_toks(rows[0])[0])
                    if v is not None:
                        mat['rho'] = v
                continue
            mat = None
            if surfint is not None:
                if name == 'FRICTION':
                    surfint['friction'] = [_toks(l) for l in st.rows(3)[0]]
                    surfint['frictionParams'] = dict(params)
                    continue
                if name == 'SURFACE BEHAVIOR':
                    surfint['behavior'] = dict(params)
                    continue
                if name in SURFINT_KW:
                    surfint.setdefault('extra', []).append(name.title())
                    continue
                surfint = None
            if coupling is not None:
                if name in ('KINEMATIC', 'DISTRIBUTING'):
                    coupling['kind'] = name.title()
                    coupling['dofs'] = [_toks(l) for l in st.rows(6)[0]]
                    continue
                coupling = None

            # ---- part / instance containers
            if name == 'PART':
                pname = pstr(params, 'NAME', '?')
                part = Ctx(-1, pname, Mesh(), {}, {}, template=True)
                rec = {'name': pname, 'tmpl': part, 'nodes': 0}
                self.parts[pname.upper()] = rec
                self.part_order.append(rec)
                continue
            if name == 'END PART':
                if part is not None:
                    self.parts[part.name.upper()]['nodes'] = len(part.mesh.n_label)
                part = None
                continue
            if part is not None:
                self._ctx_block(part, name, params, st)
                continue
            if name == 'INSTANCE':
                iname = pstr(params, 'NAME', '?')
                pkey = pstr(params, 'PART')
                tf, tr, rot = _make_transform(st.text())
                ctx = Ctx(len(self.ctxs), iname, mesh, self.nsets, self.elsets)
                ctx.transform = tf
                self.ctxs.append(ctx)
                self.inst_by_key[iname.upper()] = ctx
                inst = [ctx, pkey, tr, rot, len(mesh.n_label), len(mesh.e_code)]
                if pget(params, 'LIBRARY') is not None or pget(params, 'INSTANCE') is not None:
                    self.external = True
                    self.incomplete.append('实例 %s 的网格由 *Instance, library= 从其它结果库导入, 不在本文件中' % iname)
                rec = self.parts.get(pkey.upper()) if pkey else None
                if pkey and rec is None:
                    self._miss('实例 %s 引用了未定义的部件 %s' % (iname, pkey))
                elif rec is not None:
                    if rec['tmpl'] is None:
                        self._miss('实例 %s: 部件 %s 的网格已释放, 未能复制 (实例计数与预扫描不一致)' % (iname, pkey))
                    else:
                        self._instantiate(rec['tmpl'], ctx)
                        left = self._inst_left.get(pkey.upper(), 0) - 1
                        self._inst_left[pkey.upper()] = left
                        if left == 0:
                            rec['tmpl'] = None      # last instance of this part: drop the template
                continue
            if name == 'END INSTANCE':
                if inst is None:
                    continue
                ctx, pkey, tr, rot, n0, e0 = inst
                self._close_ctx(ctx)
                tf = ctx.transform
                if tf is not None:
                    xyz = mesh.xyz
                    if rot is None:
                        tx, ty, tz = tr
                        for i in range(3 * n0, len(xyz), 3):
                            xyz[i] += tx
                            xyz[i + 1] += ty
                            xyz[i + 2] += tz
                    else:
                        for i in range(3 * n0, len(xyz), 3):
                            xyz[i], xyz[i + 1], xyz[i + 2] = tf(xyz[i], xyz[i + 1], xyz[i + 2])
                self.instances.append({'name': ctx.name, 'part': pkey, 'nodes': len(mesh.n_label) - n0,
                                       'elements': len(mesh.e_code) - e0, 'translate': tr, 'rotate': rot,
                                       'index': len(self.instances) + 1})
                ctx.display = len(self.instances)
                ctx.nrange = (n0, len(mesh.n_label))
                inst = None
                continue
            if inst is not None:
                self._ctx_block(inst[0], name, params, st)
                continue

            # ---- root / assembly / step level
            if name in MESH_KW:
                self._mesh_kw(root, name, params, st)
            elif name in SECTION_KW:
                self._sec_defs.append((root, name, params, st.rows(4)[0]))
            elif name == 'HEADING':
                self.heading = ' '.join(st.rows(3)[0])
            elif name == 'PARAMETER':
                pass
            elif name == 'MATERIAL':
                mat = {'name': pstr(params, 'NAME', '?'), 'options': []}
                self.materials.append(mat)
            elif name == 'SURFACE INTERACTION':
                surfint = {'name': pstr(params, 'NAME', '?')}
                self.surf_ints.append(surfint)
            elif name == 'AMPLITUDE':
                ts, vs = [None, None], [None, None]
                k = 0
                for line in st.text():
                    for x in line.split(','):
                        v = _fnum(x.strip())
                        if v is None:
                            continue
                        r = vs if k % 2 else ts
                        k += 1
                        if r[0] is None or v < r[0]:
                            r[0] = v
                        if r[1] is None or v > r[1]:
                            r[1] = v
                self.amplitudes.append({'name': pstr(params, 'NAME', '?'), 'params': dict(params),
                                        'n': k // 2, 't': ts if ts[0] is not None else None,
                                        'v': vs if vs[0] is not None else None})
            elif name == 'INITIAL CONDITIONS':
                rows, nrows = st.rows(4)
                self.init_conds.append({'type': pstr(params, 'TYPE', '?'), 'n': nrows,
                                        'rows': [_toks(l) for l in rows]})
            elif name == 'DIAGNOSTICS':
                if pstr(params, 'NONHYBRID').upper().startswith('W'):
                    self.nonhybrid_warning = True       # the author accepted nu > 0.495 on ordinary elements
            elif name == 'TIE':
                tol = _fnum(pstr(params, 'POSITION TOLERANCE'))
                for line in st.text():
                    t = _toks(line)
                    if len(t) >= 2 and t[0] and t[1]:
                        self.ties.append({'name': pstr(params, 'NAME', '?'), 'slave': t[0], 'master': t[1],
                                          'params': dict(params), 'tol': tol})
            elif name == 'CONTACT PAIR':
                for line in st.text():
                    t = _toks(line)
                    if t and t[0]:
                        self.contact_pairs.append({'slave': t[0], 'master': t[1] if len(t) > 1 else '',
                                                   'interaction': pstr(params, 'INTERACTION'),
                                                   'params': dict(params), 'step': step['name'] if step else ''})
            elif name == 'CONTACT':
                self.general_contact = True
                self.gcontact['step'] = step['name'] if step else ''
            elif name in GCONTACT_KW:
                gc = self.gcontact
                rows = [_toks(l) for l in st.rows(500)[0]]
                if name == 'CONTACT INCLUSIONS':
                    if pget(params, 'ALL EXTERIOR') is not None or pget(params, 'ALL ELEMENT BASED') is not None:
                        gc['allExterior'] = True
                    gc['inclusions'].extend(rows)
                elif name == 'CONTACT EXCLUSIONS':
                    gc['exclusions'].extend(rows)
                elif name == 'CONTACT PROPERTY ASSIGNMENT':
                    gc['assignments'].extend(rows)
                elif name.title() not in gc['other']:
                    gc['other'].append(name.title())
            elif name == 'COUPLING':
                coupling = self._new_con('Coupling', params, [], name=pstr(params, 'CONSTRAINT NAME'),
                                         refNode=pstr(params, 'REF NODE'), surface=pstr(params, 'SURFACE'),
                                         kind='')
            elif name == 'RIGID BODY':
                self._rigid_defs.append((root, params))
            elif name == 'DISPLAY BODY':
                rows = [_toks(l) for l in st.rows(4)[0]]
                self._new_con('Display Body', params, rows, instance=pstr(params, 'INSTANCE'), _rows=rows)
            elif name == 'MPC':
                rows = [_toks(l) for l in st.text()]
                self._new_con('Mpc', params, rows)
                self._mpc_rows.append((self.constraints[-1]['id'], rows))
            elif name == 'EQUATION':
                rows = [_toks(l) for l in st.text()]
                self._new_con('Equation', params, rows)
                self._eq_rows.append((self.constraints[-1]['id'], rows))
            elif name in ('EMBEDDED ELEMENT', 'KINEMATIC COUPLING', 'SHELL TO SOLID COUPLING',
                          'PRE-TENSION SECTION', 'KINEMATIC', 'DISTRIBUTING', 'CYCLIC SYMMETRY MODEL',
                          'FASTENER', 'ADJUST'):
                rows = [_toks(l) for l in st.rows(5000)[0]]
                self._new_con(name.title(), params, rows, _rows=rows)
            elif name == 'TRANSFORM':
                self.transform_sets.append(pstr(params, 'NSET'))
            elif name == 'USER ELEMENT':
                n = pstr(params, 'NODES')
                if _is_int(n) and pstr(params, 'TYPE'):
                    self.user_nodes[pstr(params, 'TYPE').upper()] = int(n)
            elif name in INCOMPLETE_KW:
                self.external = True
                self.incomplete.append('%s: 这部分网格不在本文件中' % INCOMPLETE_KW[name])
            elif name == 'ASSEMBLY':
                self.assembly_name = pstr(params, 'NAME')
            elif name == 'RESTART' and pget(params, 'READ') is not None:
                self.external = True
                self.incomplete.append('*Restart, read: 网格在上一次分析的重启动文件里, 不在本文件中')
            elif name == 'STEP':
                step = {'name': pstr(params, 'NAME', 'Step-%d' % (len(self.steps) + 1)), 'params': dict(params),
                        'proc': '', 'procParams': {}, 'procData': [], 'bcs': [], 'loads': [], 'outputs': [],
                        'other': []}
                self.steps.append(step)
            elif name == 'END STEP':
                step = None
            elif name == 'BOUNDARY':
                ents = new_bc(params, st.text(), step['name'] if step else '')
                if step is not None:
                    step['bcs'].extend(ents)
                    if not ents and pstr(params, 'OP').upper() == 'NEW':
                        step['bcsCleared'] = True
                else:
                    self.initial_bcs.extend(ents)
            elif step is not None:
                if name in PROC_KW and not step['proc']:
                    step['proc'] = name.title()
                    step['procParams'] = dict(params)
                    step['procData'] = [_toks(l) for l in st.rows(2)[0]]
                elif name in LOAD_KW:
                    rows, nrows = st.rows(200)
                    step['loads'].append(new_load(name, params, rows, nrows, step['name']))
                elif name == 'OUTPUT':
                    step['outputs'].append({'kw': 'Output', 'params': dict(params), 'vars': []})
                elif name in OUTVAR_KW:
                    v = []
                    for line in st.rows(40)[0]:
                        v.extend(x for x in _toks(line) if x)
                    step['outputs'].append({'kw': name.title(), 'params': dict(params), 'vars': v})
                elif name.title() not in step['other']:
                    step['other'].append(name.title())
        if inst is not None:
            self._miss('实例 %s 缺少 *End Instance' % inst[0].name)

    # ------------------------------------------------------------ after the last keyword
    def surface_nodes(self, name, ctx=None):
        """all nodes of a surface (or node set) -> sorted array, None if it does not exist"""
        ctx = ctx or self.root
        ent = self.find_surface(name, ctx)
        if ent is None:
            return self.nodes_of(name, ctx)
        seen = set(ent['nodes'])
        face_nodes = self.face_nodes
        for e, c in zip(ent['fe'], ent['ff']):
            r = face_nodes(e, c)
            if r is not None:
                seen.update(r)
        return array('i', sorted(seen))

    def _finalize(self):
        root = self.root
        mesh = self.mesh
        self._close_ctx(root)
        e_off, e_nodes = mesh.e_off, mesh.e_nodes
        coords = self.coords
        con_arr = self._con_nodes

        # rigid bodies; a reference node that was never defined is created by Abaqus itself
        for ctx, params in self._rigid_defs:
            ref, elset = pstr(params, 'REF NODE'), pstr(params, 'ELSET')
            asurf = pstr(params, 'ANALYTICAL SURFACE')
            con = self._new_con('Rigid Body', params, [], instance=ctx.name, refNode=ref,
                                elset=(ctx.prefix + elset) if elset else '',
                                analyticalSurface=(ctx.prefix + asurf) if asurf else '')
            nodes = array('i')
            els = ()
            if elset:
                els = self.elems_of(elset, ctx)
                if els is None:
                    self._miss('*Rigid Body 引用了未定义的单元集 %s' % elset)
                    els = ()
                self.rigid_elems.update(els)
                for e in els:
                    nodes.extend(e_nodes[e_off[e]:e_off[e + 1]])
            for key in ('PIN NSET', 'TIE NSET'):
                nm = pstr(params, key)
                if nm:
                    ns = self.nodes_of(nm, ctx)
                    if ns is None:
                        self._miss('*Rigid Body 引用了未定义的节点集 %s' % nm)
                    else:
                        nodes.extend(ns)
            if asurf and (ctx.prefix + asurf).upper() not in self.surfaces and asurf.upper() not in self.surfaces:
                self._miss('*Rigid Body 引用了未定义的解析刚性面 %s' % asurf)
            rn = self.nodes_of(ref, ctx) if ref else None
            if rn is None and ref and _is_int(ref):
                # Abaqus creates the node itself: at the origin, or at the centre of mass when asked to
                # (the preview uses the centroid of the body's nodes for that)
                uniq = set(nodes) if pstr(params, 'POSITION').upper().startswith('CENTER') else ()
                if uniq:
                    c = [0.0, 0.0, 0.0]
                    for g in uniq:
                        p = coords[g]
                        c[0] += p[0]
                        c[1] += p[1]
                        c[2] += p[2]
                    c = (c[0] / len(uniq), c[1] / len(uniq), c[2] / len(uniq))
                else:
                    c = (0.0, 0.0, 0.0)
                g = self._put_node(ctx, int(ref), c)
                self.auto_nodes.append(g)
                rn = array('i', [g])
            elif rn is None and ref:
                self._miss('*Rigid Body 的参考节点 %s 未定义' % ref)
            for g in (rn or ()):
                self.ref_nodes[g] = 'RP'
            if rn:
                nodes.extend(rn)
            con['n'] = len(els)
            con_arr[con['id']] = nodes

        # freeze the sets
        self._final = True
        for is_n, table in ((True, self.nsets), (False, self.elsets)):
            for ent in table.values():
                ent['ids'] = self._eval_set(is_n, ent, ())
                if ent['nmiss'] and self.external:
                    self._miss('%s %s 中有 %d 个编号不在本文件中' % ('节点集' if is_n else '单元集', ent['name'],
                                                             ent['nmiss']))
                elif ent['nmiss']:
                    self.warnings.append('%s %s 中有 %d 个编号不存在, 已忽略' %
                                         ('节点集' if is_n else '单元集', ent['name'], ent['nmiss']))
                ent['pieces'] = None

        # sections, in file order (a later assignment overrides an earlier one)
        e_sec = mesh.e_sec
        for ctx, name, params, data in self._sec_defs:
            elset, matname = pstr(params, 'ELSET'), pstr(params, 'MATERIAL')
            sec = {'type': name.title(), 'elset': (ctx.prefix + elset) if elset else '', 'material': matname,
                   'instance': ctx.name, 'data': data[:4], 'count': 0,
                   'params': {k: v for k, v in params.items() if _squash(k) not in ('ELSET', 'MATERIAL')}}
            if name in ('SHELL SECTION', 'MEMBRANE SECTION', 'SHELL GENERAL SECTION') and data and \
                    pget(params, 'COMPOSITE') is None:
                th = _fnum(_toks(data[0])[0])
                if th is not None and th > 0:
                    sec['thickness'] = th
            idx = len(self.sections)
            self.sections.append(sec)
            if elset:
                els = self.elems_of(elset, ctx)
                if els is None:
                    # Abaqus only warns here and ignores the section
                    sec['undefined'] = True
                    self.warnings.append('%s 的单元集 %s 未定义, 这个截面不会生效' % (sec['type'], sec['elset']))
                else:
                    sec['count'] = len(els)
                    for e in els:
                        e_sec[e] = idx
        self._sec_defs = []

        # constraints: which nodes do they hold
        for con in self.constraints:
            kw = con['kw']
            nodes = con_arr.setdefault(con['id'], array('i'))
            rows = con.pop('_rows', ())
            if kw == 'Coupling':
                rn = self.nodes_of(con['refNode']) if con['refNode'] else None
                if rn is None:
                    self._miss('*Coupling %s 的参考节点 %s 未定义' % (con['name'], con['refNode']))
                sn = self.surface_nodes(con['surface']) if con['surface'] else None
                if sn is None:
                    self._miss('*Coupling %s 的表面 %s 未定义' % (con['name'], con['surface']))
                for g in (rn or ()):
                    self.ref_nodes[g] = 'RP'
                con['n'] = len(sn) if sn is not None else 0
                nodes.extend(rn or ())
                nodes.extend(sn or ())
            elif kw == 'Kinematic Coupling':
                rn = self.nodes_of(pstr(con['params'], 'REF NODE')) or ()
                for g in rn:
                    self.ref_nodes[g] = 'RP'
                nodes.extend(rn)
                for r in rows:
                    if r and r[0]:
                        nodes.extend(self.nodes_of(r[0]) or ())
            elif kw == 'Pre-Tension Section':
                rn = self.nodes_of(pstr(con['params'], 'NODE')) or ()
                for g in rn:
                    self.ref_nodes[g] = 'PT'
                nodes.extend(rn)
                sname = pstr(con['params'], 'SURFACE')
                if sname:
                    nodes.extend(self.surface_nodes(sname) or ())
            elif kw == 'Embedded Element':
                host = pstr(con['params'], 'HOST ELSET')
                n_emb = 0
                for r in rows:
                    for tok in r:
                        if tok:
                            for e in (self.elems_of(tok) or ()):
                                n_emb += 1
                                nodes.extend(e_nodes[e_off[e]:e_off[e + 1]])
                hs = self.elems_of(host) if host else None
                con['embedded'] = n_emb
                con['_hostAll'] = hs is None
                if hs is not None:
                    for e in hs[::max(1, len(hs) // 20000)]:
                        nodes.extend(e_nodes[e_off[e]:e_off[e + 1]])
            elif kw == 'Shell To Solid Coupling':
                for r in rows:
                    for tok in r[:2]:
                        if tok:
                            nodes.extend(self.surface_nodes(tok) or ())
            elif kw == 'Display Body':
                ctx = self.inst_by_key.get(con['instance'].upper())
                if ctx is None:
                    self._miss('*Display Body 引用了不存在的实例 %s' % con['instance'])
                else:
                    self.display_ctx.add(ctx.index)
                    con['n'] = ctx.nrange[1] - ctx.nrange[0]
                for r in rows:
                    for tok in r:
                        if tok:
                            rn = self.nodes_of(tok) or ()
                            for g in rn:
                                self.ref_nodes[g] = 'RP'
                            nodes.extend(rn)
        for cid, rows in self._mpc_rows:
            nodes = con_arr.setdefault(cid, array('i'))
            groups = self.con_by_id[cid].setdefault('_groups', [])
            for r in rows:
                grp = array('i')
                for tok in r[1:]:
                    if tok:
                        ns = self.nodes_of(tok)
                        if ns is None:
                            self._miss('*Mpc 引用了未定义的节点/节点集 %s' % tok)
                        else:
                            grp.extend(ns)
                nodes.extend(grp)
                groups.append(grp)
        for cid, rows in self._eq_rows:
            nodes = con_arr.setdefault(cid, array('i'))
            groups = self.con_by_id[cid].setdefault('_groups', [])
            left = 0
            grp = array('i')
            for r in rows:
                t = [x for x in r if x != '']
                if left <= 0 and len(t) == 1 and _is_int(t[0]):
                    left = int(t[0])
                    grp = array('i')
                    groups.append(grp)
                    continue
                for i in range(0, len(t) - 2, 3):
                    ns = self.nodes_of(t[i])
                    if ns is None:
                        self._miss('*Equation 引用了未定义的节点/节点集 %s' % t[i])
                    else:
                        grp.extend(ns)
                        nodes.extend(ns)
                    left -= 1
        self._mpc_rows = self._eq_rows = None

        # boundary conditions
        for ent in self._bc_defs:
            labs = ent.pop('_labels', None)
            if labs is not None:
                ns = array('i')
                for lab in labs:
                    g = self._token(root, lab, True)
                    if g is None:
                        self._miss('*Boundary 引用了未定义的节点 %s' % lab)
                    else:
                        ns.append(g)
            else:
                ns = self.nodes_of(ent['target'])
                if ns is None:
                    self._miss('*Boundary 引用了未定义的节点/节点集 %s' % ent['target'])
                    ns = array('i')
            ent['n'] = len(ns)
            self.bc_by_id[ent['id']] = (ent, ns)
        self._bc_defs = None

        # element types the preview cannot draw
        counts = [0] * len(self.types)
        for c in mesh.e_code:
            counts[c] += 1
        self.type_counts = {self.types[c]: n for c, n in enumerate(counts) if n and c}
        self.unknown_types = {t: n for t, n in self.type_counts.items() if classify(t)[0] is None}
        if any(t.startswith(('CAXA', 'SAXA')) for t in self.type_counts):
            self.external = True
            self.incomplete.append('CAXA / SAXA 单元在其它子午面上的节点由 Abaqus 自动生成, 预览没有这些节点和单元')
        self.n_dead = counts[0]

    # ------------------------------------------------------------ geometry helpers
    def skin(self, elems=None):
        """boundary of a group of elements (all when None)
        -> (pe, pc, le, qe): polygon faces as element index + face code, line elements, point elements"""
        m = self.mesh
        codes, off, nodes, fams = m.e_code, m.e_off, m.e_nodes, self.fams
        big = len(m.n_label) + 1
        seen = {}
        pe, pc, le, qe = array('i'), array('B'), array('i'), array('i')
        for e in (range(len(codes)) if elems is None else elems):
            fam, n, ftab = fams[codes[e]]
            if fam is None:
                continue
            o = off[e]
            if off[e + 1] - o < n:
                continue
            if ftab is not None:
                c = nodes[o:o + n]
                for code, f in ftab:
                    s = sorted([c[i] for i in f])
                    k = (s[0] * big + s[1]) * big + s[2]
                    # a face seen twice is interior: drop it again, so the table only holds the open front
                    if seen.pop(k, None) is None:
                        seen[k] = e * 8 + code
            elif fam == 'line':
                le.append(e)
            elif fam == 'point':
                qe.append(e)
            else:
                pe.append(e)
                pc.append(FID_WHOLE)
        for v in seen.values():
            pe.append(v >> 3)
            pc.append(v & 7)
        return pe, pc, le, qe

    def _skin(self):
        if self._skin_all is None:
            self._skin_all = self.skin()
        return self._skin_all

    def line_segments(self, e):
        """node pairs drawn for a line element (two pieces for a 3-node element)"""
        m = self.mesh
        o = m.e_off[e]
        n = self.fams[m.e_code[e]][1]
        nd = m.e_nodes
        if n == 3:
            return ((nd[o], nd[o + 1]), (nd[o + 1], nd[o + 2]))
        return ((nd[o], nd[o + 1]),)

    def _used_nodes(self):
        """bytearray flag per node: 1 if some element uses it"""
        u = getattr(self, '_used', None)
        if u is None:
            u = bytearray(len(self.n_label))
            m = self.mesh
            nodes, codes, off = m.e_nodes, m.e_code, m.e_off
            if self.n_dead:
                for e in range(len(codes)):
                    if codes[e]:
                        for g in nodes[off[e]:off[e + 1]]:
                            u[g] = 1
            else:
                for i in range(0, len(nodes), 65536):
                    for g in set(nodes[i:i + 65536]):
                        u[g] = 1
            self._used = u
        return u

    def mesh_bbox(self):
        """bounding box of the nodes that carry elements (reference points and stray nodes excluded)"""
        bb = getattr(self, '_mesh_bb', None)
        if bb is None:
            used = self._used_nodes()
            xyz = self.mesh.xyz
            lo = [1e300, 1e300, 1e300]
            hi = [-1e300, -1e300, -1e300]
            every = not any(used)
            n = len(used)
            for k in range(3):
                col = xyz[k::3]
                if every or all(used):
                    if n:
                        lo[k], hi[k] = min(col), max(col)
                else:
                    vals = [v for v, u in zip(col, used) if u]
                    lo[k], hi[k] = min(vals), max(vals)
            if lo[0] > hi[0]:
                lo, hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            bb = self._mesh_bb = [lo, hi]
        return bb

    def analytic_geom(self, ent):
        """analytical rigid surface -> (triangles xyz flat, segments xyz flat) in global coordinates"""
        a = ent.get('analytic')
        if not a or len(a['profile']) < 2:
            return [], []
        if 'geom' in a:
            return a['geom']
        pts, fr, tf, kind = a['profile'], a['frame'], a['tf'], a['kind']

        def place(p):
            q = _to_global(fr, p) if fr is not None else p
            return tf(q[0], q[1], q[2]) if tf is not None else q
        tris, segs = [], []
        if kind == 'SEGMENTS':
            for i in range(len(pts) - 1):
                segs.extend(place((pts[i][0], pts[i][1], 0.0)))
                segs.extend(place((pts[i + 1][0], pts[i + 1][1], 0.0)))
        else:
            size = max(max(p[0] for p in pts) - min(p[0] for p in pts),
                       max(p[1] for p in pts) - min(p[1] for p in pts)) or 1.0
            if kind == 'CYLINDER':
                # infinite along the generator: draw a strip long enough to cover the meshed model
                g0, g1 = place((0.0, 0.0, 0.0)), place((0.0, 0.0, 1.0))
                d = _sub(g1, g0)
                lo, hi = self.mesh_bbox()
                ss = [_dot(_sub((x, y, z), g0), d) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
                      for z in (lo[2], hi[2])]
                zc, half = 0.5 * (min(ss) + max(ss)), 0.5 * (max(ss) - min(ss)) * 1.05
                if not len(self.e_label) or half < 0.25 * size:
                    half = max(half, 0.5 * size)
                rows = [[place((x, y, zc - half)), place((x, y, zc + half))] for x, y in pts]
            else:
                n = 36
                rows = [[place((r * math.cos(2 * math.pi * j / n), r * math.sin(2 * math.pi * j / n), z))
                         for j in range(n + 1)] for r, z in pts]
            m = len(rows[0])
            for i in range(len(rows) - 1):
                for j in range(m - 1):
                    p00, p01, p10, p11 = rows[i][j], rows[i][j + 1], rows[i + 1][j], rows[i + 1][j + 1]
                    for q in (p00, p10, p11, p00, p11, p01):
                        tris.extend(q)
            for j in ((0, m - 1) if kind == 'CYLINDER' else range(0, m - 1, 9)):       # profile curves
                for i in range(len(rows) - 1):
                    segs.extend(rows[i][j])
                    segs.extend(rows[i + 1][j])
            for i in ((0, len(rows) - 1) if kind == 'CYLINDER' else range(len(rows))):  # generators / rings
                if kind != 'CYLINDER' and 0 < i < len(rows) - 1 and i % 6:
                    continue
                for j in range(m - 1):
                    segs.extend(rows[i][j])
                    segs.extend(rows[i][j + 1])
        a['geom'] = (tris, segs)
        return a['geom']

    # ------------------------------------------------------------ outputs
    def _mat_index(self):
        return {m['name'].upper(): i for i, m in enumerate(self.materials)}

    def dimension(self):
        """'3d', '2d' (plane) or 'axi' judged from the element types"""
        d = getattr(self, '_dim', None)
        if d is None:
            axi = plane = solid3 = False
            for t in self.type_counts:
                if t.startswith(_AXI_PREFIX):
                    axi = True
                elif t.startswith(_PLANE_PREFIX):
                    plane = True
                elif not t.startswith(_NEUTRAL_PREFIX):
                    solid3 = True
            d = '3d' if (solid3 or not (axi or plane)) else 'axi' if axi else '2d'
            self._dim = d
        return d

    def geometry(self):
        """what the page draws, as compact arrays (see view() for the JSON-able form)"""
        m = self.mesh
        xyz = m.xyz
        e_sec, e_inst, e_code = m.e_sec, m.e_inst, m.e_code
        types = self.types
        mat_idx = self._mat_index()
        sec_mat = [mat_idx.get(s['material'].upper(), -1) if s['material'] else -1 for s in self.sections]
        disp = [getattr(c, 'display', 0) for c in self.ctxs]       # context -> position in the instance list
        pe, pc, le, qe = self._skin()
        remap = array('i', [-1]) * len(m.n_label)
        pos = array('d')
        lo = [1e300, 1e300, 1e300]
        hi = [-1e300, -1e300, -1e300]
        rigid = self.rigid_elems
        norigid = tuple(i for i, t in enumerate(types) if t.startswith(_NO_SECTION))

        def rid(g):
            i = remap[g]
            if i < 0:
                i = remap[g] = len(pos) // 3
                k = 3 * g
                pos.extend(xyz[k:k + 3])
            return i

        shown = self.display_ctx

        def keys(e):
            sec = e_sec[e]
            if sec >= 0:
                return sec_mat[sec], sec
            if e in rigid or e_code[e] in norigid or e_inst[e] in shown:
                return -2, -2                       # rigid body / display body: needs no section
            return -1, -1

        tris, t_inst, t_mat, t_sec = array('i'), array('i'), array('i'), array('i')
        edges = array('i')
        dedupe = len(pe) <= 150000                  # beyond that a set of edges costs more than drawing twice
        seen = set()
        big = len(m.n_label) + 1
        face_nodes = self.face_nodes
        for e, code in zip(pe, pc):
            f = face_nodes(e, code)
            if f is None:
                continue
            ids = [rid(g) for g in f]
            mt, sec = keys(e)
            di = disp[e_inst[e]]
            n = len(ids)
            for k in range(1, n - 1):
                tris.append(ids[0])
                tris.append(ids[k])
                tris.append(ids[k + 1])
                t_inst.append(di)
                t_mat.append(mt)
                t_sec.append(sec)
            for k in range(n):
                a, b = ids[k], ids[(k + 1) % n]
                if dedupe:
                    key = a * big + b if a < b else b * big + a
                    if key in seen:
                        continue
                    seen.add(key)
                edges.append(a)
                edges.append(b)
        del seen
        lines, l_inst, l_mat, l_sec = array('i'), array('i'), array('i'), array('i')
        for e in le:
            mt, sec = keys(e)
            di = disp[e_inst[e]]
            for a, b in self.line_segments(e):
                lines.append(rid(a))
                lines.append(rid(b))
                l_inst.append(di)
                l_mat.append(mt)
                l_sec.append(sec)
        points = array('i', [rid(m.e_nodes[m.e_off[e]]) for e in qe])
        for k in range(3):
            if len(pos):
                col = pos[k::3]
                lo[k], hi[k] = min(col), max(col)

        def grow(p):
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]

        # analytical rigid surfaces are geometry, not mesh: append their tessellation
        n_analytic = 0
        for ent in self.surfaces.values():
            if not ent.get('analytic'):
                continue
            n_analytic += 1
            a_tris, a_segs = self.analytic_geom(ent)
            di = disp[ent.get('inst', 0)]
            cache = {}

            def vid(k, src):
                p = (src[k], src[k + 1], src[k + 2])
                i = cache.get(p)
                if i is None:
                    i = cache[p] = len(pos) // 3
                    pos.extend(p)
                    grow(p)
                return i
            for k in range(0, len(a_tris), 9):
                tris.extend((vid(k, a_tris), vid(k + 3, a_tris), vid(k + 6, a_tris)))
                t_inst.append(di)
                t_mat.append(-2)
                t_sec.append(-2)
            if a_tris:
                for k in range(0, len(a_segs), 6):
                    edges.extend((vid(k, a_segs), vid(k + 3, a_segs)))
            else:
                for k in range(0, len(a_segs), 6):
                    lines.extend((vid(k, a_segs), vid(k + 3, a_segs)))
                    l_inst.append(di)
                    l_mat.append(-2)
                    l_sec.append(-2)

        # reference points and stray nodes
        coords = self.coords
        ref_pts = []
        for g, lab in sorted(self.ref_nodes.items())[:2000]:
            p = coords[g]
            grow(p)
            ref_pts.append({'p': list(p), 'label': lab, 'node': self.node_name(g)})
        orphans = array('d')
        for g in self.unused_nodes()[:5000]:
            orphans.extend(coords[g])
        if lo[0] > hi[0]:
            lo, hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        return {'pos': pos, 'tris': tris, 'triInst': t_inst, 'triMat': t_mat, 'triSec': t_sec, 'edges': edges,
                'lines': lines, 'lineInst': l_inst, 'lineMat': l_mat, 'lineSec': l_sec, 'points': points,
                'refPoints': ref_pts, 'orphans': orphans, 'bbox': [lo, hi], 'analytic': n_analytic}

    def view(self, geom=True):
        """JSON-able description of the model.  geom=False leaves the (large) drawing arrays out;
        the preview server then adds them itself with view_json()."""
        m = self.mesh
        nn, ne = len(m.n_label), len(m.e_code) - self.n_dead
        xyz = m.xyz
        if nn:
            bbox = [[min(xyz[k::3]) for k in range(3)], [max(xyz[k::3]) for k in range(3)]]
        else:
            bbox = [[0, 0, 0], [0, 0, 0]]

        def set_list(table):
            return [{'name': v['name'], 'n': len(v['ids']), 'internal': v['internal']} for v in table.values()]

        mats = []
        for mt in self.materials:
            d = dict(mt)
            if mt.get('E') and mt.get('rho') and mt['E'] > 0 and mt['rho'] > 0:
                d['c'] = math.sqrt(mt['E'] / mt['rho'])
            mats.append(d)
        checks = self.checks(mats)
        g = None
        if geom:
            ga = self.geometry()
            g = {}
            for k, a in ga.items():
                if k == 'pos':
                    g[k] = [float('%.7g' % v) for v in a]
                elif isinstance(a, array):
                    g[k] = a.tolist()
                else:
                    g[k] = a
            self._view_bbox = g.pop('bbox')
            self._n_analytic = g.pop('analytic')
        return {
            'file': os.path.basename(self.path), 'heading': self.heading,
            'stats': {'nodes': nn, 'elements': ne, 'types': dict(self.type_counts), 'bbox': bbox,
                      'flat': not self.instances, 'meshBbox': self.mesh_bbox(),
                      'viewBbox': getattr(self, '_view_bbox', None) or self.mesh_bbox(),
                      'dim': self.dimension(), 'unusedNodes': len(self.unused_nodes()),
                      'files': self.n_files, 'sizeMB': round(self.size_bytes / 1048576.0, 2),
                      'features': self.features, 'analyticSurfaces': getattr(self, '_n_analytic', 0),
                      'unknownTypes': self.unknown_types},
            'geom': g,
            'parts': [{'name': p['name'], 'nodes': p['nodes']} for p in self.part_order],
            'instances': self.instances, 'materials': mats, 'sections': self.sections,
            'nsets': set_list(self.nsets), 'elsets': set_list(self.elsets),
            'surfaces': [{'name': v['name'], 'type': v['type'],
                          'n': len(v['fe']) or len(v['nodes']) or (v['analytic'] or {}).get('nseg', 0),
                          'internal': v['internal']} for v in self.surfaces.values()],
            'ties': self.ties, 'contactPairs': self.contact_pairs, 'generalContact': self.general_contact,
            'generalContactDef': dict(self.gcontact), 'surfInts': self.surf_ints,
            'constraints': [{k: v for k, v in c.items() if k[0] != '_'} for c in self.constraints],
            'amplitudes': self.amplitudes, 'initialConditions': self.init_conds,
            'initialBcs': self.initial_bcs, 'steps': self.steps, 'incomplete': self.incomplete,
            'parameters': ({k: _param_str(v) for k, v in self.parameters.items()}
                           if self.parameters is not None else None),
            'checks': checks,
        }

    def view_json_iter(self, extra=None):
        """the whole view as UTF-8 JSON, yielded in pieces of bytes: the geometry is formatted straight
        from the arrays, so neither Python lists of it nor the complete text need to exist at once"""
        import json
        ga = self.geometry()
        self._view_bbox = ga.pop('bbox')
        self._n_analytic = ga.pop('analytic')
        v = self.view(geom=False)
        del v['geom']
        if extra:
            v.update(extra)
        yield (json.dumps(v, ensure_ascii=False, separators=(',', ':'))[:-1] + ',"geom":{').encode('utf-8')
        for i, k in enumerate(list(ga)):
            a = ga.pop(k)
            yield ('%s"%s":' % (',' if i else '', k)).encode('ascii')
            if isinstance(a, array):
                for piece in json_array_iter(a):
                    yield piece
            else:
                yield json.dumps(a, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        yield b'}}'

    def view_json(self, extra=None):
        return b''.join(self.view_json_iter(extra))

    # ------------------------------------------------------------ highlight
    def _xyz(self, gids, cap=None):
        """coordinates of the given nodes as a flat array (evenly thinned to `cap` nodes)"""
        if cap and len(gids) > cap:
            step = len(gids) / float(cap)
            gids = [gids[int(i * step)] for i in range(cap)]
        out = array('d')
        xyz = self.mesh.xyz
        for g in gids:
            k = 3 * g
            out.extend(xyz[k:k + 3])
        return out

    def _faces_xyz(self, fe, ff, tris, segs, pts):
        """append the geometry of surface facets (element, face code) to flat coordinate arrays"""
        xyz = self.mesh.xyz
        face_nodes = self.face_nodes
        n = 0
        for e, c in zip(fe, ff):
            f = face_nodes(e, c)
            if f is None:
                continue
            n += 1
            p = [xyz[3 * g:3 * g + 3] for g in f]
            if len(p) == 1:
                pts.extend(p[0])
            elif len(p) == 2:
                segs.extend(p[0])
                segs.extend(p[1])
            else:
                for k in range(1, len(p) - 1):
                    tris.extend(p[0])
                    tris.extend(p[k])
                    tris.extend(p[k + 1])
                for k in range(len(p)):
                    segs.extend(p[k])
                    segs.extend(p[(k + 1) % len(p)])
        return n

    def _hl_surface(self, res, name):
        ent = self.find_surface(name)
        if ent is None:
            ns = self._find_set(True, self.root, name)
            if ns is not None:
                res['n'] += len(ns['ids'])
                res['pts'].extend(self._xyz(ns['ids'], 30000))
            return
        res['n'] += self._faces_xyz(ent['fe'], ent['ff'], res['tris'], res['segs'], res['pts'])
        res['n'] += len(ent['nodes'])
        res['pts'].extend(self._xyz(ent['nodes'], 30000))
        a_tris, a_segs = self.analytic_geom(ent)
        res['tris'].extend(a_tris)
        res['segs'].extend(a_segs)

    def _hl_elems(self, res, els):
        res['n'] += len(els)
        pe, pc, le, qe = self.skin(els)
        self._faces_xyz(pe, pc, res['tris'], res['segs'], res['pts'])
        xyz = self.mesh.xyz
        for e in le:
            for a, b in self.line_segments(e):
                res['segs'].extend(xyz[3 * a:3 * a + 3])
                res['segs'].extend(xyz[3 * b:3 * b + 3])
        m = self.mesh
        res['pts'].extend(self._xyz([m.e_nodes[m.e_off[e]] for e in qe], 30000))

    def _highlight(self, kind, name):
        res = {'pts': array('d'), 'tris': array('d'), 'segs': array('d'), 'n': 0}
        m = self.mesh
        if kind == 'nset':
            ent = self.nsets.get(name.upper())
            if ent:
                res['n'] = len(ent['ids'])
                res['pts'] = self._xyz(ent['ids'], 30000)
        elif kind in ('elset', 'instance', 'material', 'section'):
            if kind == 'elset':
                ent = self.elsets.get(name.upper())
                els = ent['ids'] if ent else ()
            elif kind == 'instance':
                ctx = self.inst_by_key.get(name.upper())
                els = [e for e, i in enumerate(m.e_inst) if i == ctx.index] if ctx else ()
                if ctx:
                    res['pts'].extend(self._xyz([g for g in self.ref_nodes if self.n_inst[g] == ctx.index], 1000))
                if ctx:
                    for s in self.surfaces.values():
                        if s.get('analytic') and s.get('inst') == ctx.index:
                            t, sg = self.analytic_geom(s)
                            res['tris'].extend(t)
                            res['segs'].extend(sg)
            elif kind == 'section':
                idx = int(name) if _is_int(name) else -9
                els = [e for e, s in enumerate(m.e_sec) if s == idx]
            else:
                ids = {i for i, s in enumerate(self.sections) if s['material'].upper() == name.upper()}
                els = [e for e, s in enumerate(m.e_sec) if s in ids]
            self._hl_elems(res, els)
        elif kind == 'surface':
            self._hl_surface(res, name)
        elif kind == 'bc':
            hit = self.bc_by_id.get(name)
            if hit:
                res['n'] = len(hit[1])
                res['pts'] = self._xyz(hit[1], 30000)
        elif kind == 'load':
            ent = self.load_by_id.get(name)
            if ent:
                sym = {'cload': [], 'cmom': [], 'press': [], 'grav': []}
                self._load_symbols(ent, sym, res)
        elif kind == 'constraint':
            con = self.con_by_id.get(name)
            if con:
                if con['kw'] == 'Coupling' and con.get('surface'):
                    self._hl_surface(res, con['surface'])
                    res['pts'].extend(self._xyz(self.nodes_of(con['refNode']) or (), 1000))
                elif con['kw'] == 'Rigid Body':
                    if con.get('elset'):
                        ent = self.elsets.get(con['elset'].upper())
                        self._hl_elems(res, ent['ids'] if ent else ())
                    if con.get('analyticalSurface'):
                        self._hl_surface(res, con['analyticalSurface'])
                    refs = [g for g in self._con_nodes.get(name, ()) if g in self.ref_nodes]
                    res['pts'].extend(self._xyz(refs, 1000))
                elif con['kw'] == 'Display Body':
                    ctx = self.inst_by_key.get(con.get('instance', '').upper())
                    if ctx:
                        self._hl_elems(res, [e for e, i in enumerate(m.e_inst) if i == ctx.index])
                    res['pts'].extend(self._xyz(self._con_nodes.get(name, ()), 1000))
                else:
                    nodes = sorted(set(self._con_nodes.get(name, ())))
                    res['n'] = len(nodes)
                    res['pts'] = self._xyz(nodes, 30000)
        return res

    def highlight(self, kind, name):
        """geometry of one item of the model tree -> {'pts': [...], 'tris': [...], 'segs': [...], 'n': count}"""
        r = self._highlight(kind, name)
        return {'pts': r['pts'].tolist(), 'tris': r['tris'].tolist(), 'segs': r['segs'].tolist(), 'n': r['n']}

    def highlight_json(self, kind, name):
        r = self._highlight(kind, name)
        out = [('{"n":%d' % r['n']).encode('ascii')]
        for k in ('pts', 'tris', 'segs'):
            out.append((',"%s":' % k).encode('ascii'))
            out.extend(json_array_iter(r[k]))
        out.append(b'}')
        return b''.join(out)

    # ------------------------------------------------------------ BC / load symbols
    def _load_symbols(self, ent, sym, hl=None, cap=400):
        kw = ent['kw']
        coords = self.coords
        if kw == 'CLOAD':
            for r in ent['rows']:
                if len(r) < 3 or not _is_int(r[1]):
                    continue
                dof, mag = int(r[1]), _fnum(r[2])
                ns = self.nodes_of(r[0])
                if ns is None or mag is None or mag == 0.0:
                    continue
                if hl is not None:
                    hl['pts'].extend(self._xyz(ns, 30000))
                    hl['n'] += len(ns)
                if 1 <= dof <= 6:
                    d = [0.0, 0.0, 0.0]
                    d[(dof - 1) % 3] = 1.0 if mag > 0 else -1.0
                    stride = max(1, len(ns) // cap)
                    key = 'cload' if dof <= 3 else 'cmom'
                    for g in ns[::stride]:
                        sym[key].extend(coords[g])
                        sym[key].extend(d)
        elif kw in ('DSLOAD', 'DLOAD'):
            for r in ent['rows']:
                if len(r) < 3:
                    continue
                typ, mag = r[1].upper(), _fnum(r[2])
                if mag is None:
                    continue
                fe, ff = array('i'), array('B')
                along = None                      # fixed direction (traction vector) instead of the normal
                sign = -1.0 if mag >= 0 else 1.0  # pressure pushes against the outward normal
                if kw == 'DSLOAD' and typ in ('P', 'PNU', 'HP', 'TRVEC'):
                    s = self.find_surface(r[0])
                    if s is not None:
                        fe, ff = s['fe'], s['ff']
                    if typ == 'TRVEC':
                        along = _unit(tuple((_floats(r[3:6]) + [0.0] * 3)[:3]))
                        if along is None:
                            continue
                        if mag < 0:
                            along = _mul(along, -1.0)
                elif kw == 'DLOAD' and typ in ('GRAV', 'BX', 'BY', 'BZ'):
                    if typ == 'GRAV':
                        d = _unit(tuple((_floats(r[3:6]) + [0.0] * 3)[:3]))
                    else:
                        d = {'BX': (1.0, 0.0, 0.0), 'BY': (0.0, 1.0, 0.0), 'BZ': (0.0, 0.0, 1.0)}[typ]
                    if d is not None and mag != 0.0:
                        if mag < 0:
                            d = _mul(d, -1.0)
                        sym['grav'].append({'dir': list(d), 'mag': abs(mag), 'kind': typ, 'target': r[0]})
                    continue
                elif kw == 'DLOAD' and re.match(r'^P\d?(NU)?$', typ):
                    digit = int(typ[1]) if len(typ) > 1 and typ[1].isdigit() else 0
                    codes, fams = self.mesh.e_code, self.fams
                    for e in (self.elems_of(r[0]) or ()):
                        fam = fams[codes[e]][0]
                        if not digit and fam in SURFACE_FAMILIES:
                            fe.append(e)
                            ff.append(8)                 # P on a shell acts along its positive normal
                        elif digit and fam in SOLID_FACES or digit and fam in PLANE_FAMILIES:
                            fe.append(e)
                            ff.append(digit)
                if not len(fe):
                    continue
                if hl is not None:
                    hl['n'] += self._faces_xyz(fe, ff, hl['tris'], hl['segs'], hl['pts'])
                stride = max(1, len(fe) // cap)
                face_nodes = self.face_nodes
                for i in range(0, len(fe), stride):
                    f = face_nodes(fe[i], ff[i])
                    if f is None or len(f) < 2:
                        continue
                    p = [coords[g] for g in f]
                    c = [sum(q[k] for q in p) / len(p) for k in range(3)]
                    if along is not None:
                        d = along
                    else:
                        if len(p) == 2:                  # edge of a plane element: outward = right of a -> b
                            n = _unit((p[1][1] - p[0][1], p[0][0] - p[1][0], 0.0))
                        else:
                            n = _unit(_cross(_sub(p[1], p[0]), _sub(p[2], p[0])))
                        if n is None:
                            continue
                        d = _mul(n, sign)
                    sym['press'].extend((c[0], c[1], c[2], d[0], d[1], d[2]))

    def active(self, step_index):
        """-> (bcs, loads) in effect during a step (-1 = initial); op=NEW clears earlier ones"""
        bcs = list(self.initial_bcs)
        loads = []
        for s in self.steps[:max(0, step_index + 1)]:
            if s.get('bcsCleared') or any(pstr(b['params'], 'OP').upper() == 'NEW' for b in s['bcs']):
                bcs = []
            bcs.extend(s['bcs'])
            for kw in {l['kw'] for l in s['loads'] if pstr(l['params'], 'OP').upper() == 'NEW'}:
                loads = [l for l in loads if l['kw'] != kw]
            loads.extend(s['loads'])
        return bcs, loads

    def symbols(self, step_index, cap=400):
        """BC / load symbols for one step"""
        bcs, loads = self.active(step_index)
        out = {'bc': [], 'cload': [], 'cmom': [], 'press': [], 'grav': []}
        for b in bcs:
            ns = self.bc_by_id[b['id']][1]
            out['bc'].append({'dofs': b['dofs'], 'pts': self._xyz(ns, cap).tolist(), 'n': len(ns),
                              'moving': bool(b['mag'])})
        for l in loads:
            self._load_symbols(l, out, None, cap)
        return out

    # ------------------------------------------------------------ checks ("体检")
    def unused_nodes(self):
        """nodes that no element uses and that no constraint / node-based surface refers to"""
        res = getattr(self, '_unused', None)
        if res is None:
            used = self._used_nodes()
            if all(used):
                res = array('i')
            else:
                keep = bytearray(len(used))
                for arr in self._con_nodes.values():
                    for g in arr:
                        keep[g] = 1
                for s in self.surfaces.values():
                    for g in s['nodes']:
                        keep[g] = 1
                for g in self.ref_nodes:
                    keep[g] = 1
                res = array('i', [g for g in range(len(used)) if not used[g] and not keep[g]])
            self._unused = res
        return res

    def _facets(self, name):
        """facets of a surface (or node set) for distance queries.  Yields
        (0, a, b, c) triangles, (1, a, b, normal2d or None) segments, (2, a) points - coordinates only."""
        coords = self.coords
        ent = self.find_surface(name)
        if ent is None:
            ns = self._find_set(True, self.root, name)
            for g in (ns['ids'] if ns is not None else ()):
                yield (2, coords[g])
            return
        codes, fams = self.mesh.e_code, self.fams
        face_nodes = self.face_nodes
        any_face = False
        for e, c in zip(ent['fe'], ent['ff']):
            f = face_nodes(e, c)
            if f is None:
                continue
            any_face = True
            if len(f) > 2:
                p = [coords[g] for g in f]
                yield (0, p[0], p[1], p[2])
                if len(p) == 4:
                    yield (0, p[0], p[2], p[3])
            elif len(f) == 2:
                a, b = coords[f[0]], coords[f[1]]
                n2 = None
                if fams[codes[e]][0] in PLANE_FAMILIES:
                    n2 = _unit((b[1] - a[1], a[0] - b[0], 0.0))     # outward for a counter-clockwise element
                yield (1, a, b, n2)
            else:
                yield (2, coords[f[0]])
        a_tris, a_segs = self.analytic_geom(ent)
        for k in range(0, len(a_tris), 9):
            any_face = True
            yield (0, tuple(a_tris[k:k + 3]), tuple(a_tris[k + 3:k + 6]), tuple(a_tris[k + 6:k + 9]))
        if not a_tris:
            for k in range(0, len(a_segs), 6):
                any_face = True
                yield (1, tuple(a_segs[k:k + 3]), tuple(a_segs[k + 3:k + 6]), None)
        if not any_face:
            for g in ent['nodes']:
                yield (2, coords[g])

    def _surf_info(self, name):
        """size / kind of a surface without holding its geometry; None when it is not defined"""
        cache = self.__dict__.setdefault('_surf_cache', {})
        key = name.upper()
        if key in cache:
            return cache[key]
        ent = self.find_surface(name)
        info = None
        if ent is None:
            ns = self._find_set(True, self.root, name)
            if ns is not None:
                info = {'kind': 'nset', 'n': len(ns['ids']), 'size': 0.0, 'thick': 0.0, 'oriented': False,
                        'far': None, 'empty': not len(ns['ids']) and self._mesh_complete()}
        else:
            nf = len(ent['fe'])
            coords = self.coords
            m = self.mesh
            secs = self.sections
            sizes = []
            thick = 0.0
            oriented = nf > 0
            step = max(1, nf // 4000)
            for i in range(0, nf, step):
                e, c = ent['fe'][i], ent['ff'][i]
                f = self.face_nodes(e, c)
                if f is None or len(f) < 2:
                    continue
                p = [coords[g] for g in f]
                if len(p) == 4:
                    sizes.append(_len(_sub(p[2], p[0])))
                elif len(p) == 3:
                    sizes.append(max(_len(_sub(p[1], p[0])), _len(_sub(p[2], p[1])), _len(_sub(p[0], p[2]))))
                else:
                    sizes.append(_len(_sub(p[1], p[0])))
                fam = self.fams[m.e_code[e]][0]
                if len(p) == 2 and fam not in PLANE_FAMILIES:
                    oriented = False
                if fam in SURFACE_FAMILIES and m.e_sec[e] >= 0:
                    thick = max(thick, secs[m.e_sec[e]].get('thickness', 0.0))
            analytic = bool(ent.get('analytic'))
            if analytic or ent.get('twosided'):
                oriented = False
            n = nf or (ent['analytic'] or {}).get('nseg', 0) or len(ent['nodes'])
            # "empty" only when the preview knows it: every line of the definition was understood and the
            # whole mesh is in the file.  (Abaqus: NO SURFACE FACETS ARE GENERATED FOR SURFACE ...)
            known = not (ent.get('other') or ent.get('unresolved') or analytic) and self._mesh_complete()
            info = {'kind': 'other' if ent.get('other') else 'analytic' if analytic else 'element' if nf else 'node',
                    'n': n, 'size': (sum(sizes) / len(sizes)) if sizes else 0.0, 'thick': thick,
                    'oriented': oriented, 'far': None, 'empty': n == 0 and known}
        cache[key] = info
        return info

    def _mesh_complete(self):
        return not (self.external or self.files_missing)      # the whole mesh is in the files that were read

    def _far_distance(self, p, name, info):
        """rough distance from p to a surface that is not nearby: nearest of a sample of its points"""
        samp = info['far']
        if samp is None:
            pts = []
            for f in self._facets(name):
                pts.append(f[1])
                if len(pts) > 200000:
                    break
            step = max(1, len(pts) // 600)
            samp = info['far'] = pts[::step]
        if not samp:
            return None
        best = 1e300
        px, py, pz = p
        for q in samp:
            d = (q[0] - px) ** 2 + (q[1] - py) ** 2 + (q[2] - pz) ** 2
            if d < best:
                best = d
        return math.sqrt(best)

    def _gap_stats(self, slave, master, minfo, reach, cap):
        """distances from the nodes of surface `slave` to surface `master`
        -> {'n': nodes examined, 'total': slave nodes, 'near': [(distance, side, node)], 'far': [(distance, node)]}"""
        coords = self.coords
        nodes = self.surface_nodes(slave) or ()
        total = len(nodes)
        if total > cap:
            step = total / float(cap)
            nodes = [nodes[int(i * step)] for i in range(cap)]
        if not total:
            return {'n': 0, 'total': 0, 'near': [], 'far': []}
        pts = [coords[g] for g in nodes]
        lo = [min(p[i] for p in pts) - reach for i in range(3)]
        hi = [max(p[i] for p in pts) + reach for i in range(3)]
        idx = _FacetIndex(self._facets(master), lo, hi)
        near, far = [], []
        for g, p in zip(nodes, pts):
            r = idx.query(p, reach)
            if r is None:
                far.append((self._far_distance(p, master, minfo), g))
            else:
                near.append((r[0], r[1], g))
        return {'n': len(nodes), 'total': total, 'near': near, 'far': far, 'index': idx}

    def _tie_face_rule(self, slave, flagged, idx, limit, reach):
        """Surface-to-surface ties also take every node of a slave face that lies over the master with a
        "significant portion" of its area (Analysis Guide, Mesh tie constraints).  Abaqus 2021 on a pad that
        overhangs a beam: the first overhanging row of faces is tied when half of each face is over the
        beam and is not when a quarter is (examples/corpus/_gen/tie_tolerance_probe.json) - the face
        centre lying within the tolerance reproduces both.  -> the nodes of `flagged` that are tied after all"""
        ent = self.find_surface(slave)
        saved = set()
        if ent is None or not flagged or idx is None:
            return saved
        coords = self.coords
        face_nodes = self.face_nodes
        for e, c in zip(ent['fe'], ent['ff']):
            f = face_nodes(e, c)
            if f is None or len(f) < 2:
                continue
            hit = [g for g in f if g in flagged]
            if not hit:
                continue
            n = float(len(f))
            cx = cy = cz = 0.0
            for g in f:
                p = coords[g]
                cx += p[0]
                cy += p[1]
                cz += p[2]
            r = idx.query((cx / n, cy / n, cz / n), reach)
            if r is not None and r[0] <= limit:
                saved.update(hit)
        return saved

    def _non_hybrid_types(self, material):
        """continuum element types (3-D, plane strain, axisymmetric) that use a material and are not hybrid"""
        secs = set(i for i, s in enumerate(self.sections) if (s['material'] or '').upper() == material.upper())
        if not secs:
            return []
        mesh = self.mesh
        codes = set()
        e_sec, e_code = mesh.e_sec, mesh.e_code
        for e in range(len(e_sec)):
            if e_sec[e] in secs:
                codes.add(e_code[e])
        out = []
        for c in sorted(codes):
            t = self.types[c].upper()
            if t.startswith(('C3D', 'CPE', 'CAX', 'CGAX')) and not re.match(r'^[A-Z]+[0-9]+[A-Z]*H', t):
                out.append(self.types[c])
        return out

    def _check_names(self, add):
        """names outside ASCII: Abaqus 2021 answers a UTF-8 input with
        'Invalid value "混凝土C30" for parameter "name"' (examples/corpus/checks/chk_name_non_ascii.inp)"""
        def odd(s):
            return any(ord(ch) > 127 for ch in s)

        def some(names):
            names = sorted(set(names))
            return ', '.join(names[:3]) + (' 等 %d 个' % len(names) if len(names) > 3 else '')
        mats = [m['name'] for m in self.materials if odd(m['name'])]
        if mats:
            add('error', '材料名含非 ASCII 字符 (%s): Abaqus 读入时会报 Invalid value, 请改用英文名' % some(mats))
        other = []
        for kind, names in (('部件', [p['name'] for p in self.part_order]),
                            ('实例', [i['name'] for i in self.instances]),
                            ('节点集', [v['name'] for v in self.nsets.values()]),
                            ('单元集', [v['name'] for v in self.elsets.values()]),
                            ('表面', [v['name'] for v in self.surfaces.values()]),
                            ('分析步', [s['name'] for s in self.steps])):
            hit = [n for n in names if odd(n)]
            if hit:
                other.append('%s %s' % (kind, some(hit)))
        if other:
            add('warn', '名称含非 ASCII 字符 (%s): Abaqus 的名称只接受 ASCII 字符, 读入时很可能报错' % '; '.join(other[:4]))

    def _is_explicit(self):
        for st in self.steps:
            if st['proc'].upper().startswith('DYNAMIC') and pget(st['procParams'], 'EXPLICIT') is not None:
                return True
        return False

    def _tie_node_to_surface(self, tie, s, m):
        """which tie formulation Abaqus uses: surface-to-surface is the default in Abaqus/Standard,
        node-to-surface in Abaqus/Explicit and whenever one of the surfaces is node-based"""
        kind = _squash(pstr(tie['params'], 'TYPE'))
        n2s = kind.startswith('N') if kind else self._is_explicit()
        return n2s or s['kind'] in ('node', 'nset') or m['kind'] in ('node', 'nset')

    def _check_ties(self, add):
        for tie in self.ties[:200]:
            if pget(tie['params'], 'CYCLIC SYMMETRY') is not None:
                continue                                   # the two surfaces are one sector apart by design
            s, m = self._surf_info(tie['slave']), self._surf_info(tie['master'])
            if s is None or m is None:
                continue                                   # reported as undefined elsewhere
            label = 'Tie %s' % tie['name']
            if not m['n'] or not s['n'] or 'other' in (m['kind'], s['kind']):
                if m.get('empty'):
                    add('error', '%s: 主面 %s 为空 (没有面), Abaqus 会报错' % (label, tie['master']))
                if s.get('empty'):
                    add('error', '%s: 从面 %s 为空 (没有节点), Abaqus 会报错' % (label, tie['slave']))
                continue                                   # nothing to measure (the reason is reported elsewhere)
            size = m['size'] or self._model_diag() * 0.01
            given = tie.get('tol')
            if given is not None and given > 0:
                tol, how = given, '位置容差'
            else:
                # Abaqus default (Analysis Guide, Mesh tie constraints): 5 % (node-to-surface) or 10 %
                # (surface-to-surface) of the typical main facet diagonal; for an analytical rigid main
                # surface the secondary facets count; for a node-based main surface it follows the node
                # spacing by a rule that is not documented - no check then.
                if m['kind'] in ('node', 'nset'):
                    continue
                ref, whose = size, '主面'
                if m['kind'] == 'analytic':
                    if not s['size']:
                        continue
                    ref, whose = s['size'], '从面'
                frac = TIE_DEFAULT_TOL_N2S if self._tie_node_to_surface(tie, s, m) else TIE_DEFAULT_TOL_S2S
                tol, how = frac * ref, '默认位置容差 (%s网格对角线的 %d%%)' % (whose, round(frac * 100))
            allow = 0.0
            if pget(tie['params'], 'NO THICKNESS') is None:
                allow = 0.5 * (s['thick'] + m['thick'])    # shell surfaces: the tie works from the shell faces
            reach = max(2.0 * (tol + allow), 1.5 * size)
            st = self._gap_stats(tie['slave'], tie['master'], m, reach, TIE_NODE_CAP)
            if not st['n']:
                continue                                   # faces of an element type the preview cannot place
            near = [(max(0.0, d - allow), g) for d, sign, g in st['near'] if d - allow > tol]
            far = st['far']
            if (near or far) and s['kind'] == 'element' and not self._tie_node_to_surface(tie, s, m):
                saved = self._tie_face_rule(tie['slave'], set(g for d, g in near) | set(g for d, g in far),
                                            st['index'], tol + allow, reach)
                if saved:
                    near = [q for q in near if q[1] not in saved]
                    far = [q for q in far if q[1] not in saved]
            out = len(near) + len(far)
            worst, worst_g = 0.0, None
            for gap, g in near:
                if gap > worst:
                    worst, worst_g = gap, g
            rough = False
            if far:
                # the far nodes only have a rough distance: measure the worst few exactly
                cand = sorted(far, key=lambda q: -(q[0] or 0.0))[:3]
                exact = self._exact_distance([g for d, g in cand], tie['master']) if m['n'] <= 120000 else None
                for k, (d, g) in enumerate(cand):
                    if exact is not None:
                        d = exact[k]
                    else:
                        rough = True
                    if d is not None and d - allow > worst:
                        worst, worst_g = d - allow, g
            if out:
                pct = 100.0 * out / st['n']
                msg = '%s: 从面 %s 的 %d 个节点中有 %d 个 (%d%%) 到主面 %s 的距离超过%s %s, 不会被绑定; 最大间隙%s %s' % (
                    label, tie['slave'], st['n'], out, round(pct), tie['master'], how, _fmt(tol),
                    '约' if rough else '', _fmt(worst))
                if worst_g is not None:
                    msg += ' (节点 %s)' % self.node_name(worst_g)
                if st['n'] < st['total']:
                    msg += '; 从面共 %d 个节点, 抽查了 %d 个' % (st['total'], st['n'])
                add('warn', msg)
        if len(self.ties) > 200:
            add('info', 'Tie 共 %d 个, 间隙检查只做了前 200 个' % len(self.ties))

    def _exact_distance(self, nodes, name):
        """distance from a few nodes to every facet of a surface (brute force) -> list"""
        coords = self.coords
        pts = [coords[g] for g in nodes]
        best = [1e300] * len(pts)
        dist = _FacetIndex._dist
        for f in self._facets(name):
            for k, p in enumerate(pts):
                d2 = dist(f, p[0], p[1], p[2])[0]
                if d2 < best[k]:
                    best[k] = d2
        return [math.sqrt(b) if b < 1e299 else None for b in best]

    def _check_contact_pairs(self, add):
        diag = self._model_diag()
        infos = []
        for cp in self.contact_pairs[:60]:
            if not cp['master']:
                continue                                   # self contact
            s, m = self._surf_info(cp['slave']), self._surf_info(cp['master'])
            if s is None or m is None:
                continue
            label = '接触对 %s / %s' % (cp['slave'], cp['master'])
            empty = sorted(set(nm for nm, q in ((cp['slave'], s), (cp['master'], m)) if q.get('empty')))
            if empty:
                # Abaqus: "NO SURFACE FACETS ARE GENERATED FOR SURFACE ..." / "THE SLAVE SURFACE ... DOES NOT EXIST"
                add('error', '%s: 表面 %s 为空 (没有面), Abaqus 会报错, 这对接触不起作用' % (label, ', '.join(empty)))
            if not s['n'] or not m['n'] or 'other' in (s['kind'], m['kind']):
                continue                                   # nothing to measure (the reason is reported elsewhere)
            size = m['size'] or s['size'] or diag * 0.01
            reach = max(3.0 * size, 0.02 * diag)
            st = self._gap_stats(cp['slave'], cp['master'], m, reach, CONTACT_NODE_CAP)
            allow = 0.5 * (s['thick'] + m['thick'])        # shell surfaces sit half a thickness off the nodes
            vals = []
            for d, sign, g in st['near']:
                # behind the master face and close to it = initial overclosure
                vals.append(-d if (m['oriented'] and sign is not None and sign < 0 and d <= size) else d)
            fars = [d for d, g in st['far'] if d is not None]
            if not vals and not fars:
                continue
            lo = min(vals + fars)
            hi = max(vals + fars)
            note = '按从面 %d 个节点' % st['n'] if st['n'] == st['total'] else \
                '从面共 %d 个节点, 抽查 %d 个' % (st['total'], st['n'])
            if allow:
                note += ', 按壳中面量取, 两侧壳厚之半合计 %s' % _fmt(allow)
            if not m['oriented']:
                note += ', 主面无法向 (解析面/双面壳), 未区分过盈'
            approx = '约 ' if (fars and not vals) else ''
            if lo - allow > max(0.1 * diag, 3.0 * size):
                add('warn', '%s: 两个表面相距很远, 最近处%s %s (模型尺寸 %s), 请确认接触对选对了面' % (
                    label, ' 约' if approx else '', _fmt(lo), _fmt(diag)))
            else:
                msg = '%s: 初始间隙 %s%s – %s (%s)' % (label, approx, _fmt(lo, diag), _fmt(hi, diag), note)
                if lo < -1e-9 * diag:
                    msg += '; 最小值为负, 即存在初始过盈 %s, 请确认是否有意' % _fmt(-lo)
                infos.append((lo, hi, msg))
        for lo, hi, msg in infos[:6]:
            add('info', msg)
        if len(infos) > 6:
            rest = infos[6:]
            add('info', '另有 %d 个接触对的初始间隙在 %s – %s 之间' % (
                len(rest), _fmt(min(q[0] for q in rest), diag), _fmt(max(q[1] for q in rest), diag)))
        if len(self.contact_pairs) > 60:
            add('info', '接触对共 %d 个, 初始间隙只检查了前 60 个' % len(self.contact_pairs))

    def _model_diag(self):
        lo, hi = self.mesh_bbox()
        return math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3))) or 1.0

    def _entity_links(self):
        """which bodies are joined by a constraint.  Bodies are instance (context) indices and, for the
        nodes that live at assembly level (reference points), the single node ('n', index).
        -> (hard pairs, soft pairs, union-find `find` over hard links, general contact covers everything)"""
        hit = getattr(self, '_links_cache', None)
        if hit is not None:
            return hit
        n_inst = self.n_inst
        flat = not self.instances

        def ents_of(nodes):
            insts = set()
            singles = []
            for g in nodes:
                i = n_inst[g]
                if i or flat:
                    insts.add(i)
                else:
                    singles.append(('n', g))
            return insts, singles
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        hard, soft = set(), set()

        def link(nodes, target=hard, join=True, more=()):
            insts, singles = ents_of(nodes)
            insts.update(more)
            ents = sorted(insts)
            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    target.add((ents[i], ents[j]))
            if join:
                allents = ents + singles
                for b in allents[1:]:
                    ra, rb = find(allents[0]), find(b)
                    if ra != rb:
                        parent[rb] = ra

        def owner(name):
            """instance that owns an analytical surface (it has no nodes of its own)"""
            s = self.find_surface(name) if name else None
            return [s['inst']] if (s is not None and s.get('analytic') and s.get('inst')) else []
        for tie in self.ties:
            a, b = self.surface_nodes(tie['slave']), self.surface_nodes(tie['master'])
            link(list(a or ()) + list(b or ()), more=owner(tie['slave']) + owner(tie['master']))
        embed_all = False
        for con in self.constraints:
            groups = con.get('_groups')
            if groups is not None:
                for grp in groups:
                    link(grp)
            else:
                more = owner(con.get('analyticalSurface', '')) if con['kw'] == 'Rigid Body' else ()
                link(self._con_nodes.get(con['id'], ()), more=more)
            if con.get('_hostAll'):
                embed_all = True
        soft_types = ('SPRING', 'DASHPOT', 'CONN', 'GAP', 'JOINTC', 'ITS', 'DGAP')
        if not flat:
            m = self.mesh
            soft_codes = {c for c, t in enumerate(self.types) if t.startswith(soft_types)}
            for e in self.root.elem_map.values():
                is_soft = m.e_code[e] in soft_codes
                link(m.e_nodes[m.e_off[e]:m.e_off[e + 1]], soft if is_soft else hard, not is_soft)
        for cp in self.contact_pairs:
            a = self.surface_nodes(cp['slave']) or ()
            b = (self.surface_nodes(cp['master']) or ()) if cp['master'] else ()
            link(list(a) + list(b), soft, False, owner(cp['slave']) + owner(cp['master']))
        gc_all = False
        if self.general_contact:
            gc = self.gcontact
            rows = [r for r in gc['inclusions'] if any(r)]
            if gc['allExterior'] or not rows:
                gc_all = True
            for r in rows:
                nodes = []
                more = []
                for nm in r[:2]:
                    if nm:
                        nodes.extend(self.surface_nodes(nm) or ())
                        more.extend(owner(nm))
                    else:
                        gc_all = True
                link(nodes, soft, False, more)
        self._links_cache = (hard, soft, find, gc_all, embed_all)
        return self._links_cache

    def _check_adjacency(self, add):
        """instances whose meshes touch although nothing connects them"""
        if len(self.instances) < 2:
            return
        hard, soft, find, gc_all, embed_all = self._entity_links()
        if gc_all or embed_all:
            return
        coords = self.coords
        xyz = self.mesh.xyz
        e_inst = self.mesh.e_inst
        pe, pc, le, qe = self._skin()
        dim3 = self.dimension() == '3d'
        diag = self._model_diag()
        face_nodes = self.face_nodes
        per = {}                                    # instance -> faces, lines, bbox, typical size
        shown = self.display_ctx
        for k in range(len(pe)):
            i = e_inst[pe[k]]
            if i and i not in shown:
                d = per.get(i)
                if d is None:
                    d = per[i] = {'faces': array('i'), 'lines': array('i')}
                d['faces'].append(k)
        for e in le:
            i = e_inst[e]
            if i and i not in shown:
                d = per.get(i)
                if d is None:
                    d = per[i] = {'faces': array('i'), 'lines': array('i')}
                d['lines'].append(e)
        if len(per) < 2:
            return
        for i, d in per.items():
            n0, n1 = self.ctxs[i].nrange
            d['lo'] = [min(xyz[3 * n0 + k:3 * n1:3]) for k in range(3)]
            d['hi'] = [max(xyz[3 * n0 + k:3 * n1:3]) for k in range(3)]
            sizes = []
            nf = len(d['faces'])
            for q in range(0, nf, max(1, nf // 500)):
                f = face_nodes(pe[d['faces'][q]], pc[d['faces'][q]])
                if f is not None:
                    sizes.append(_len(_sub(coords[f[2 if (dim3 and len(f) > 2) else 1]], coords[f[0]])))
            nl = len(d['lines'])
            for q in range(0, nl, max(1, nl // 500)):
                (a, b) = self.line_segments(d['lines'][q])[0]
                sizes.append(_len(_sub(coords[b], coords[a])))
            sizes.sort()
            d['h'] = sizes[len(sizes) // 2] if sizes else diag * 0.01
            d['nodes'] = None

        def boundary(i):
            d = per[i]
            if d['nodes'] is None:
                s = set()
                for q in d['faces']:
                    f = face_nodes(pe[q], pc[q])
                    if f is not None:
                        s.update(f)
                for e in d['lines']:
                    for a, b in self.line_segments(e):
                        s.add(a)
                        s.add(b)
                d['nodes'] = s
            return d['nodes']

        def facets(i, lo, hi):
            d = per[i]
            for q in d['faces']:
                f = face_nodes(pe[q], pc[q])
                if f is None:
                    continue
                p = [coords[g] for g in f]
                if min(v[0] for v in p) > hi[0] or max(v[0] for v in p) < lo[0] or \
                        min(v[1] for v in p) > hi[1] or max(v[1] for v in p) < lo[1] or \
                        min(v[2] for v in p) > hi[2] or max(v[2] for v in p) < lo[2]:
                    continue
                if dim3:
                    yield (0, p[0], p[1], p[2])
                    if len(p) == 4:
                        yield (0, p[0], p[2], p[3])
                else:
                    for k in range(len(p)):
                        yield (1, p[k], p[(k + 1) % len(p)], None)
            for e in d['lines']:
                for a, b in self.line_segments(e):
                    yield (1, coords[a], coords[b], None)
        ids = sorted(per)
        name = {c.index: c.name for c in self.ctxs}
        budget = ADJ_BUDGET
        found = []
        skipped = 0
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                i, j = ids[x], ids[y]
                a, b = per[i], per[j]
                tol = max(1e-6 * diag, ADJ_TOL * min(a['h'], b['h']))
                lo = [max(a['lo'][k], b['lo'][k]) - tol for k in range(3)]
                hi = [min(a['hi'][k], b['hi'][k]) + tol for k in range(3)]
                if lo[0] > hi[0] or lo[1] > hi[1] or lo[2] > hi[2]:
                    continue
                if (i, j) in hard or (i, j) in soft or find(i) == find(j):
                    continue
                if budget <= 0:
                    skipped += 1
                    continue
                near = 0
                for src, dst in ((i, j), (j, i)):
                    nodes = boundary(src)
                    budget -= len(nodes) + len(per[dst]['faces']) + len(per[dst]['lines'])
                    cand = []
                    for g in nodes:
                        p = coords[g]
                        if lo[0] <= p[0] <= hi[0] and lo[1] <= p[1] <= hi[1] and lo[2] <= p[2] <= hi[2]:
                            cand.append(p)
                    if not cand:
                        continue
                    if len(cand) > 3000:
                        step = len(cand) / 3000.0
                        cand = [cand[int(q * step)] for q in range(3000)]
                    idx = _FacetIndex(facets(dst, lo, hi), lo, hi)
                    if not idx.n:
                        continue
                    hits = 0
                    for p in cand:
                        r = idx.query(p, tol)
                        if r is not None and r[0] <= tol:
                            hits += 1
                    near = max(near, hits)
                    if near >= 3:
                        break
                need = 3 if (dim3 and len(a['faces']) and len(b['faces'])) else 1
                if near >= need:
                    found.append((name[i], name[j], near))
        for a, b, n in found[:12]:
            add('warn', '实例 %s 与 %s 相邻 (%d 个边界节点贴合) 但没有任何连接/接触定义 '
                        '(Tie、接触、耦合、MPC 都没有)' % (a, b, n))
        if len(found) > 12:
            add('warn', '另有 %d 对相邻实例同样没有连接/接触定义' % (len(found) - 12))
        if skipped:
            add('info', '模型较大, 实例相邻检查有 %d 对实例未检查' % skipped)

    def _check_materials(self, add, mats):
        dyn = any(s['proc'].upper() in ('DYNAMIC', 'FREQUENCY', 'MODAL DYNAMIC', 'STEADY STATE DYNAMICS',
                                        'COMPLEX FREQUENCY', 'RANDOM RESPONSE', 'RESPONSE SPECTRUM')
                  for s in self.steps)
        used = {s['material'].upper() for s in self.sections if s['material']}
        # materials whose elements all sit in rigid bodies: their mass may come from elsewhere
        e_sec = self.mesh.e_sec
        rigid_count = {}
        for el in self.rigid_elems:
            rigid_count[e_sec[el]] = rigid_count.get(e_sec[el], 0) + 1
        deformable = {s['material'].upper() for i, s in enumerate(self.sections)
                      if s['material'] and s['count'] > rigid_count.get(i, 0)}
        kinds = set()
        for m in mats:
            nm = m['name']
            e, nu, rho = m.get('E'), m.get('nu'), m.get('rho')
            if nu is not None and nu >= 0.5:
                add('error', '材料 %s 的泊松比 %s ≥ 0.5 (不可压缩), 线弹性材料不允许' % (nm, _fmt(nu)))
            elif nu is not None and nu < 0:
                add('error', '材料 %s 的泊松比 %s 为负, 请核对' % (nm, _fmt(nu)))
            elif nu is not None and nu > 0.495 and not self._is_explicit():
                # Abaqus/Standard 2021 stops with "THE POISSON RATIO OF 0.49900 EXCEEDS 0.495 ... HOWEVER, A
                # HYBRID TYPE ELEMENT IS NOT USED" unless *DIAGNOSTICS, NONHYBRID=WARNING is given
                # (examples/corpus/checks/chk_material_near_incompressible.inp)
                plain = self._non_hybrid_types(nm)
                if plain and self.nonhybrid_warning:
                    add('warn', '材料 %s 的泊松比 %s > 0.495 (近不可压缩), 单元 %s 不是杂交单元; 已用 *Diagnostics, '
                                'nonhybrid=warning 放行, 注意体积锁死' % (nm, _fmt(nu), ', '.join(plain[:3])))
                elif plain:
                    add('error', '材料 %s 的泊松比 %s > 0.495 (近不可压缩), 而单元 %s 不是杂交单元: Abaqus/Standard '
                                 '会报错, 请改用 H 型单元 (如 C3D8H)' % (nm, _fmt(nu), ', '.join(plain[:3])))
            if e is not None and e <= 0:
                add('error', '材料 %s 的弹性模量 %s ≤ 0' % (nm, _fmt(e)))
            if rho is not None and rho <= 0:
                add('error', '材料 %s 的密度 %s ≤ 0' % (nm, _fmt(rho)))
            other_density = any(o['kw'] == 'Acoustic Medium' and pget(o['params'], 'COMPLEX DENSITY') is not None
                                for o in m.get('options', ()))
            if dyn and not rho and not other_density and (nm.upper() in deformable or not used):
                add('error', '材料 %s 没有密度, 但模型含动力/模态分析步' % nm)
            c = m.get('c')
            if c is None or (used and nm.upper() not in used):
                continue
            expect = None
            for label, rx, band in MATERIAL_BANDS:
                if rx.search(nm):
                    expect = (label, band)
                    break
            if expect is not None:
                label, (c0, c1) = expect
                unit = None
                for uname, f in (('m', 1.0), ('mm', 1000.0), ('in', 39.3701)):
                    if c0 * f <= c <= c1 * f:
                        unit = uname
                if unit is not None:
                    if unit != 'in':
                        kinds.add(unit)
                    m['cNote'] = '与 %s 制的%s相符' % (unit, label)
                else:
                    mid = math.sqrt(c0 * c1)
                    best = min((('m', 1.0), ('mm', 1000.0)), key=lambda u: abs(math.log10(c / (mid * u[1]))))
                    ratio = (c / (mid * best[1])) ** 2
                    p = int(round(math.log10(ratio)))
                    how = ''
                    if p and abs(math.log10(ratio) - p) < 0.35:
                        how = '; E/ρ 约为 %s 制正常值的 %s, 多半是密度或模量的单位差了 %s 倍' % (
                            best[0], ('1' + '0' * p + ' 倍') if p > 0 else ('1/1' + '0' * (-p)), '1' + '0' * abs(p))
                    m['cNote'] = '与名称不符 (%s)' % label
                    add('warn', '材料 %s 按名称应是%s, 但 √(E/ρ) = %s, 不在%s的合理范围 (m 制 %d–%d, mm 制 %d–%d)%s' % (
                        nm, label, _fmt(c), label, c0, c1, c0 * 1000, c1 * 1000, how))
                continue
            if 30.0 <= c <= 8000.0:
                kinds.add('m')
                m['cNote'] = '与 m 制自洽 (波速 m/s 量级)'
            elif 3.0e4 <= c <= 8.0e6:
                kinds.add('mm')
                m['cNote'] = '与 mm 制自洽 (波速 mm/s 量级)'
            else:
                m['cNote'] = '量级异常'
                add('warn', '材料 %s 的 √(E/ρ) = %s, 不像 m 制也不像 mm 制, 请核对 E 与密度的单位' % (nm, _fmt(c)))
        if 'm' in kinds and 'mm' in kinds:
            add('error', '不同材料的 √(E/ρ) 分属 m 制和 mm 制量级, 单位制可能混用')

    def _check_static_restraint(self, add):
        """static steps that carry load although some body has no restraint in a translational direction"""
        dim = self.dimension()
        dirs = (1, 2, 3) if dim == '3d' else (2,) if dim == 'axi' else (1, 2)
        dname = {1: 'U1', 2: 'U2', 3: 'U3'}
        statics = [i for i, s in enumerate(self.steps) if s['proc'].upper() == 'STATIC']
        m = self.mesh
        if not statics or not len(m.e_code):
            return
        hard, soft, find, gc_all, embed_all = self._entity_links()
        flat = not self.instances
        n_inst = self.n_inst
        tf_nodes = set()
        for nm in self.transform_sets:
            tf_nodes.update(self.nodes_of(nm) or ())
        bodies = sorted(i for i in set(m.e_inst) if (i or flat) and i not in self.display_ctx)
        if not bodies:
            return
        soft_any = bool(self.contact_pairs or self.general_contact)
        ground = set()                                    # groups held by a grounded spring / dashpot
        gcodes = {c for c, t in enumerate(self.types) if t.startswith(('SPRING1', 'DASHPOT1'))}
        if gcodes:
            for e, c in enumerate(m.e_code):
                if c in gcodes:
                    g = m.e_nodes[m.e_off[e]]
                    ground.add(find(n_inst[g] if (n_inst[g] or flat) else ('n', g)))
        name = {c.index: c.name for c in self.ctxs}
        reported = {}
        for si in statics:
            bcs, loads = self.active(si)
            real = [l for l in loads if l['kw'] in ('CLOAD', 'DLOAD', 'DSLOAD') and l['nrows']]
            if not real or any(l['kw'] == 'INERTIA RELIEF' for l in loads):
                continue
            held = {}                                     # group -> set of restrained directions
            for b in bcs:
                ns = self.bc_by_id[b['id']][1]
                d = {x for x in b['dofs'] if x in (1, 2, 3)}
                if not d or not len(ns):
                    continue
                ents = set()
                skew = False
                for g in ns:
                    i = n_inst[g]
                    ents.add(i if (i or flat) else ('n', g))
                    if tf_nodes and g in tf_nodes:
                        skew = True
                for en in ents:
                    held.setdefault(find(en), set()).update((1, 2, 3) if skew else d)
            groups = {}
            for i in bodies:
                groups.setdefault(find(i), []).append(i)
            for r, members in sorted(groups.items(), key=lambda kv: kv[1]):
                free = [d for d in dirs if d not in held.get(r, ())]
                if not free:
                    continue
                if flat:
                    contact = soft_any or bool(self.ties)
                else:
                    contact = soft_any and (gc_all or any(
                        (min(i, j), max(i, j)) in soft for i in members for j in bodies if j not in members))
                key = (tuple(members), tuple(free), contact, r in ground)
                reported.setdefault(key, []).append(self.steps[si]['name'])
        for (members, free, contact, grounded), steps in reported.items():
            if flat or len(members) == len(bodies):
                who = '整个模型在'
            else:
                who = '实例 ' + ', '.join(name[i] for i in members[:4]) + \
                      (' 等 %d 个' % len(members) if len(members) > 4 else '') + ' 在'
            what = '%s %s 方向没有任何位移约束' % (who, '、'.join(dname[d] for d in free))
            where = '静力步 %s%s有载荷' % (steps[0], (' 等 %d 步' % len(steps)) if len(steps) > 1 else ' ')
            if contact or grounded:
                add('info', '%s, %s, 只能靠%s约束, 请确认不会出现刚体位移' % (
                    where, what, '接触' if contact else '接地弹簧'))
            else:
                add('warn', '%s, 但%s, 也没有绑定/接触连到已约束的部分, 会出现刚体位移' % (where, what))

    def checks(self, mats=None):
        """the sanity checks -> list of {'level': error | warn | info | ok, 'msg': text}"""
        cached = getattr(self, '_checks', None)
        if cached is not None:
            if mats is not None:
                for m, n in zip(mats, self._check_notes):
                    if n:
                        m['cNote'] = n
            return [dict(c) for c in cached]
        if mats is None:
            mats = []
            for m in self.materials:
                d = dict(m)
                if m.get('E') and m.get('rho') and m['E'] > 0 and m['rho'] > 0:
                    d['c'] = math.sqrt(m['E'] / m['rho'])
                mats.append(d)
        out = []

        def add(level, msg):
            out.append({'level': level, 'msg': msg})

        if self.external:
            if self.missing:
                add('warn', '网格来自其它分析, 因此有 %d 处引用在本文件内找不到 (如: %s)' % (
                    len(self.missing), self.missing[0]))
        else:
            for m in self.missing[:40]:
                add('error', m)
            if len(self.missing) > 40:
                add('error', '另有 %d 条未解析的引用未列出' % (len(self.missing) - 40))
        for w in self.input_errors[:20]:
            add('error', w)
        self._check_names(add)
        for w in self.incomplete[:12]:
            add('warn', '预览不完整: ' + w)
        for w in self.warnings[:20]:
            add('warn', w)
        mesh = self.mesh
        if len(mesh.e_sec) and min(mesh.e_sec) < 0:
            nosec = {}
            fams, types = self.fams, self.types
            skip = {c for c, t in enumerate(types) if not c or fams[c][0] is None or t.startswith(_NO_SECTION)}
            shown = self.display_ctx
            e_inst = mesh.e_inst
            for e, s in enumerate(mesh.e_sec):
                if s < 0:
                    c = mesh.e_code[e]
                    if c not in skip and e_inst[e] not in shown:
                        nosec[types[c]] = nosec.get(types[c], 0) + 1
            if nosec:
                add('error', '有单元未赋截面: ' + ', '.join('%s × %d' % kv for kv in sorted(nosec.items())))
        mat_keys = {m['name'].upper() for m in self.materials}
        for s in self.sections:
            if s['material'] and s['material'].upper() not in mat_keys and not self.external:
                add('error', '截面 (%s, 单元集 %s) 引用了未定义的材料 %s' % (s['type'], s['elset'], s['material']))
            if s['elset'] and s['count'] == 0 and not s.get('undefined'):
                add('warn', '截面 (%s) 的单元集 %s 为空' % (s['type'], s['elset']))
        used = {s['material'].upper() for s in self.sections if s['material']}
        for m in self.materials:
            if m['name'].upper() not in used:
                add('info', '材料 %s 已定义但没有截面使用' % m['name'])
        if self.unknown_types:
            add('warn', '以下单元类型预览未识别(不显示): ' +
                ', '.join('%s × %d' % kv for kv in sorted(self.unknown_types.items())))
        gen = [k for k in ('NGEN', 'NFILL', 'NCOPY', 'NMAP', 'ELGEN', 'ELCOPY') if k in self.features]
        if gen:
            add('info', '网格由 %s 生成, 预览按同样的规则展开 (共 %d 节点, %d 单元)' % (
                ', '.join('*%s × %d' % (k.title(), self.features[k]) for k in gen), len(mesh.n_label),
                len(mesh.e_code) - self.n_dead))
        if self.auto_nodes:
            add('info', '刚体参考节点 %s 没有用 *Node 定义, Abaqus 会自动创建 (在原点; position=center of mass '
                        '时在质心, 预览用刚体节点的形心近似)' % ', '.join(self.node_name(g) for g in self.auto_nodes[:5]))
        if not self.steps:
            add('warn', '没有分析步')
        if not self.initial_bcs and not any(s['bcs'] for s in self.steps):
            add('warn', '没有任何边界条件')
        for ent, ns in self.bc_by_id.values():
            if not len(ns) and not self.external:
                add('error', '边界条件目标 %s 没有节点' % ent['target'])
        def known(nm):
            return self.find_surface(nm) is not None or self._find_set(True, self.root, nm) is not None
        for key, t in (('slave', 'Tie 从面'), ('master', 'Tie 主面')):
            for tie in self.ties:
                if not known(tie[key]) and not self.external:
                    add('error', '%s %s 未定义 (Tie %s)' % (t, tie[key], tie['name']))
        for cp in self.contact_pairs:
            for key in ('slave', 'master'):
                if cp[key] and not known(cp[key]) and not self.external:
                    add('error', '接触对引用了未定义的表面 %s' % cp[key])
        self._check_materials(add, mats)
        stray = self.unused_nodes()
        if len(stray):
            add('info', '有 %d 个节点没有被任何单元使用, 也不是参考点/刚体/耦合/MPC 节点 (如节点 %s)' % (
                len(stray), ', '.join(self.node_name(g) for g in stray[:3])))
        for fn in (self._check_ties, self._check_contact_pairs, self._check_adjacency,
                   self._check_static_restraint):
            try:
                fn(add)
            except Exception as e:      # a check must never take the preview down
                add('info', '检查 %s 未能完成: %s: %s' % (fn.__name__.replace('_check_', ''), type(e).__name__, e))
        if not any(c['level'] in ('error', 'warn') for c in out):
            out.insert(0, {'level': 'ok', 'msg': '未发现问题' if not out else '未发现错误或警告'})
        self._checks = out
        self._check_notes = [m.get('cNote') for m in mats]
        return [dict(c) for c in out]


# ---------------------------------------------------------------- check parameters
# Abaqus default tie position tolerance as a fraction of the typical main facet diagonal
TIE_DEFAULT_TOL_N2S = 0.05  # node-to-surface formulation (Abaqus/Explicit default)
TIE_DEFAULT_TOL_S2S = 0.10  # surface-to-surface formulation (Abaqus/Standard default)
TIE_DEFAULT_TOL = TIE_DEFAULT_TOL_N2S   # old name, kept for callers
TIE_NODE_CAP = 20000        # slave nodes examined per tie
CONTACT_NODE_CAP = 4000     # slave nodes examined per contact pair
ADJ_TOL = 0.02              # "touching" = closer than this fraction of the local element size
ADJ_BUDGET = 4000000        # node + facet visits allowed for the adjacency check
NMAP_TYPES = ('ROTATION', 'TRANSLATION', 'SCALE', 'RECTANGULAR', 'CYLINDRICAL', 'DIAMOND', 'SPHERICAL',
              'TOROIDAL', 'BLENDED')
# *NMAP types whose convention was checked node by node against Abaqus 2021 (examples/corpus:
# official mesh-generation samples and handwritten/hw_mesh_generation.inp, hw_nmap_*.inp)
NMAP_VERIFIED = ('CYLINDRICAL', 'RECTANGULAR', 'SPHERICAL', 'ROTATION', 'TRANSLATION', 'SCALE', 'DIAMOND')
MATERIAL_BANDS = (
    # (label, name pattern, plausible sqrt(E/rho) in m/s)
    ('混凝土', re.compile(r'混凝土|砼|concrete|(?<![A-Za-z0-9])C(?:15|20|25|30|35|40|45|50|55|60|65|70|75|80)'
                       r'(?![0-9A-Za-z])', re.I), (2400.0, 4800.0)),
    ('钢', re.compile(r'钢|steel|(?<![A-Za-z0-9])(?:Q(?:195|215|235|275|345|355|390|420|460|500|550|620|690)|'
                     r'HRB\d{3}|HPB\d{3})(?![0-9])', re.I), (4500.0, 5700.0)),
    ('铝', re.compile(r'铝|alumin', re.I), (4500.0, 5600.0)),
)


def json_array_iter(a, chunk=20000):
    """array -> JSON text as pieces of bytes, so that no list of all its numbers is ever built"""
    yield b'['
    real = a.typecode in ('d', 'f')
    for i in range(0, len(a), chunk):
        part = a[i:i + chunk]
        text = ','.join(['%.7g' % v for v in part]) if real else ','.join(map(str, part))
        yield ((',' if i else '') + text).encode('ascii')
    yield b']'


def json_array(a):
    return b''.join(json_array_iter(a)).decode('ascii')


class _FacetIndex(object):
    """uniform grid over triangles / segments / points for closest-point queries.
    `facets` yields (0, a, b, c) | (1, a, b, normal2d) | (2, a); only those touching the box lo..hi are kept."""

    def __init__(self, facets, lo=None, hi=None):
        items = []
        boxes = []
        ext = []
        for it in facets:
            kind = it[0]
            if kind == 0:
                a, b, c = it[1], it[2], it[3]
                bl = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]), min(a[2], b[2], c[2]))
                bh = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]), max(a[2], b[2], c[2]))
            elif kind == 1:
                a, b = it[1], it[2]
                bl = (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]))
                bh = (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]))
            else:
                bl = bh = it[1]
            if lo is not None and (bl[0] > hi[0] or bl[1] > hi[1] or bl[2] > hi[2] or
                                   bh[0] < lo[0] or bh[1] < lo[1] or bh[2] < lo[2]):
                continue
            items.append(it)
            boxes.append((bl, bh))
            if kind != 2:
                ext.append(max(bh[0] - bl[0], bh[1] - bl[1], bh[2] - bl[2]))
        self.items = items
        self.n = len(items)
        self.cells = {}
        self.big = []
        if not items:
            self.h = 1.0
            return
        h = 0.0
        if ext:
            se = sorted(ext)
            h = se[len(se) // 2] or se[-1]
        if h <= 0:                                  # points only: aim at a handful of points per cell
            span = max(max(b[0][i] for b in boxes) - min(b[0][i] for b in boxes) for i in range(3))
            h = (span / max(1.0, len(items) ** (1.0 / 3.0))) or 1.0
        self.h = h
        cells = self.cells
        fl = math.floor
        for idx, (bl, bh) in enumerate(boxes):
            i0, i1 = int(fl(bl[0] / h)), int(fl(bh[0] / h))
            j0, j1 = int(fl(bl[1] / h)), int(fl(bh[1] / h))
            k0, k1 = int(fl(bl[2] / h)), int(fl(bh[2] / h))
            if (i1 - i0 + 1) * (j1 - j0 + 1) * (k1 - k0 + 1) > 512:
                self.big.append(idx)
                continue
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    for k in range(k0, k1 + 1):
                        key = (i, j, k)
                        c = cells.get(key)
                        if c is None:
                            cells[key] = [idx]
                        else:
                            c.append(idx)

    @staticmethod
    def _dist(it, px, py, pz):
        """-> (squared distance, side) ; side = +1 in front of the facet normal, -1 behind, None unknown"""
        kind = it[0]
        if kind == 0:
            a, b, c = it[1], it[2], it[3]
            abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            acx, acy, acz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            apx, apy, apz = px - a[0], py - a[1], pz - a[2]
            d1 = abx * apx + aby * apy + abz * apz
            d2 = acx * apx + acy * apy + acz * apz
            if d1 <= 0 and d2 <= 0:
                qx, qy, qz = a
            else:
                bpx, bpy, bpz = px - b[0], py - b[1], pz - b[2]
                d3 = abx * bpx + aby * bpy + abz * bpz
                d4 = acx * bpx + acy * bpy + acz * bpz
                if d3 >= 0 and d4 <= d3:
                    qx, qy, qz = b
                else:
                    vc = d1 * d4 - d3 * d2
                    if vc <= 0 and d1 >= 0 and d3 <= 0:
                        v = d1 / (d1 - d3) if d1 != d3 else 0.0
                        qx, qy, qz = a[0] + v * abx, a[1] + v * aby, a[2] + v * abz
                    else:
                        cpx, cpy, cpz = px - c[0], py - c[1], pz - c[2]
                        d5 = abx * cpx + aby * cpy + abz * cpz
                        d6 = acx * cpx + acy * cpy + acz * cpz
                        if d6 >= 0 and d5 <= d6:
                            qx, qy, qz = c
                        else:
                            vb = d5 * d2 - d1 * d6
                            if vb <= 0 and d2 >= 0 and d6 <= 0:
                                w = d2 / (d2 - d6) if d2 != d6 else 0.0
                                qx, qy, qz = a[0] + w * acx, a[1] + w * acy, a[2] + w * acz
                            else:
                                va = d3 * d6 - d5 * d4
                                if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
                                    den = (d4 - d3) + (d5 - d6)
                                    w = (d4 - d3) / den if den else 0.0
                                    qx = b[0] + w * (c[0] - b[0])
                                    qy = b[1] + w * (c[1] - b[1])
                                    qz = b[2] + w * (c[2] - b[2])
                                else:
                                    den = va + vb + vc
                                    if den == 0:
                                        qx, qy, qz = a
                                    else:
                                        v, w = vb / den, vc / den
                                        qx = a[0] + abx * v + acx * w
                                        qy = a[1] + aby * v + acy * w
                                        qz = a[2] + abz * v + acz * w
            dx, dy, dz = px - qx, py - qy, pz - qz
            nx, ny, nz = aby * acz - abz * acy, abz * acx - abx * acz, abx * acy - aby * acx
            s = dx * nx + dy * ny + dz * nz
            return dx * dx + dy * dy + dz * dz, (1 if s > 0 else -1 if s < 0 else 0)
        if kind == 1:
            a, b, n2 = it[1], it[2], it[3]
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            L2 = ux * ux + uy * uy + uz * uz
            t = ((px - a[0]) * ux + (py - a[1]) * uy + (pz - a[2]) * uz) / L2 if L2 else 0.0
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            dx, dy, dz = px - (a[0] + t * ux), py - (a[1] + t * uy), pz - (a[2] + t * uz)
            side = None
            if n2 is not None:
                s = dx * n2[0] + dy * n2[1]
                side = 1 if s > 0 else -1 if s < 0 else 0
            return dx * dx + dy * dy + dz * dz, side
        a = it[1]
        return (px - a[0]) ** 2 + (py - a[1]) ** 2 + (pz - a[2]) ** 2, None

    def query(self, p, reach):
        """closest facet within `reach` of p -> (distance, side) or None"""
        if not self.n:
            return None
        h = self.h
        px, py, pz = p
        best = reach * reach * (1.0 + 1e-12) + 1e-300
        side = None
        hit = False
        items = self.items
        dist = self._dist
        for idx in self.big:
            d2, s = dist(items[idx], px, py, pz)
            if d2 < best:
                best, side, hit = d2, s, True
        ci, cj, ck = int(math.floor(px / h)), int(math.floor(py / h)), int(math.floor(pz / h))
        rings = min(int(reach / h) + 1, 6)
        cells = self.cells
        seen = set()
        for r in range(rings + 1):
            for i in range(ci - r, ci + r + 1):
                di = abs(i - ci) == r
                for j in range(cj - r, cj + r + 1):
                    dj = di or abs(j - cj) == r
                    for k in ((ck - r, ck + r) if (r and not dj) else range(ck - r, ck + r + 1)):
                        c = cells.get((i, j, k))
                        if c is None:
                            continue
                        for idx in c:
                            if idx in seen:
                                continue
                            seen.add(idx)
                            d2, s = dist(items[idx], px, py, pz)
                            if d2 < best or (d2 == best and s is not None and side is not None and s > side):
                                best, side, hit = d2, s, True
            if hit and best <= (r * h) ** 2:
                break
        return (math.sqrt(best), side) if hit else None


if __name__ == '__main__':
    import json
    import sys
    import time
    t0 = time.time()
    mdl = Model(sys.argv[1], force='--force' in sys.argv)
    v = mdl.view()
    g = v.pop('geom')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({k: v[k] for k in ('file', 'stats', 'instances', 'materials', 'sections', 'ties',
                                        'initialBcs', 'steps', 'checks')}, ensure_ascii=False, indent=1))
    print('nsets', [(s['name'], s['n']) for s in v['nsets']])
    print('elsets', [(s['name'], s['n']) for s in v['elsets']])
    print('surfaces', [(s['name'], s['n']) for s in v['surfaces']])
    print('tris %d edges %d lines %d  %.2f s' % (len(g['tris']) // 3, len(g['edges']) // 2,
                                                 len(g['lines']) // 2, time.time() - t0))
