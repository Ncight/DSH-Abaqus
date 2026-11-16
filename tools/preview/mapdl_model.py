# -*- coding: utf-8 -*-
"""MAPDL CDB reader for the preview panel (ANSYS plan v2.1, 4.5).

Reads the CDB that MAPDL writes with CDWRITE,DB (default blocked format) - mesh, element types and KEYOPTs,
materials, sections, real constants, components, coordinate systems, constraint equations, coupled sets,
contact pairs, constraints and loads of the one load state the file holds, analysis settings - into the
same internal structures as inp_model.Model, so that the page, the highlights, the symbols and part of the
checks work unchanged.  The view carries `solver: "mapdl"` and some MAPDL-only fields (see view()).

    m = Model(path)          # raises TooLargeError / MemoryRefusedError like inp_model.Model
    v = m.view()             # JSON-able dict for the page
    m.highlight(kind, name)  # geometry of one item of the model tree
    m.symbols(step_index)    # BC and load symbols

A CDB is not plain data: it is an APDL input that MAPDL executes.  Every command outside the data blocks is
checked against a whitelist of the commands CDWRITE writes (cdb_commands()); anything else, a "$" that would
put several commands on one line, a data row that does not fit its block format, or the unblocked format,
is reported as an error with its line number (view()['cdb']['violations']).

Formats were taken from CDB files written by MAPDL 2025 R1 (step 0 of the plan and the MAPDL verification
inputs), not from memory.  Standard library only; Python 3.8+.
"""
import math
import os
import re
from array import array

import inp_model
import solve_segment
from inp_model import (FACE_TAB, FID_WHOLE, PLANE_FAMILIES, SOLID_FACES, SURFACE_FAMILIES, MemoryRefusedError,
                       TooLargeError, _cross, _dot, _fmt, _mul, _sub, _unit, check_memory, estimate_memory_gb,
                       MEMORY_RESERVE_GB)

__all__ = ['Model', 'TooLargeError', 'MemoryRefusedError', 'estimate_memory_gb', 'MEMORY_RESERVE_GB',
           'element_name', 'CDB_WHITELIST']

# ---------------------------------------------------------------- element library
# number -> (name, class).  Classes:
#   S8  brick with corners I..P (8 or 20 nodes); prism, tetrahedron and pyramid as the documented degenerate forms
#   S4  tetrahedron, corners I J K L (4 or 10 nodes)
#   SH  3-D shell, corners I J K L (4 or 8 nodes; triangle when K = L)
#   P4  2-D solid, corners I J K L (4 or 8 nodes; triangle when K = L)
#   P3  2-D triangle, corners I J K (3 or 6 nodes)
#   L2  line I-J (further nodes are orientation / extra nodes)
#   L3  line I-J with the middle node K
#   PT  one node
#   CS  3-D contact / target / surface-effect element: drawn only when it is picked
#   CL  2-D (or line) contact / target / surface-effect element: drawn only when it is picked
#   --  not drawn
_ELEMENTS = """
1 LINK1 L2|3 BEAM3 L2|4 BEAM4 L2|5 SOLID5 S8|8 LINK8 L2|9 INFIN9 L2|10 LINK10 L2|11 LINK11 L2|12 CONTAC12 L2
13 PLANE13 P4|14 COMBIN14 L2|16 PIPE16 L2|17 PIPE17 L2|18 PIPE18 L2|20 PIPE20 L2|21 MASS21 PT|23 BEAM23 L2
24 BEAM24 L2|25 PLANE25 P4|26 CONTAC26 L2|27 MATRIX27 L2|28 SHELL28 SH|29 FLUID29 P4|30 FLUID30 S8|31 LINK31 L2
32 LINK32 L2|33 LINK33 L2|34 LINK34 L2|35 PLANE35 P3|36 SOURC36 --|37 COMBIN37 L2|38 FLUID38 L2|39 COMBIN39 L2
40 COMBIN40 L2|41 SHELL41 SH|42 PLANE42 P4|43 SHELL43 SH|44 BEAM44 L2|45 SOLID45 S8|46 SOLID46 S8|47 INFIN47 SH
48 CONTAC48 CL|49 CONTAC49 CS|50 MATRIX50 --|51 SHELL51 L2|52 CONTAC52 L2|53 PLANE53 P4|54 BEAM54 L2|55 PLANE55 P4
57 SHELL57 SH|59 PIPE59 L2|60 PIPE60 L2|61 SHELL61 L2|62 SOLID62 S8|63 SHELL63 SH|64 SOLID64 S8|65 SOLID65 S8
67 PLANE67 P4|68 LINK68 L2|69 SOLID69 S8|70 SOLID70 S8|71 MASS71 PT|72 SOLID72 S4|73 SOLID73 S8|75 PLANE75 P4
77 PLANE77 P4|78 PLANE78 P4|79 FLUID79 P4|80 FLUID80 S8|81 FLUID81 P4|82 PLANE82 P4|83 PLANE83 P4|87 SOLID87 S4
88 VISCO88 P4|89 VISCO89 S8|90 SOLID90 S8|91 SHELL91 SH|92 SOLID92 S4|93 SHELL93 SH|94 CIRCU94 --|95 SOLID95 S8
96 SOLID96 S8|97 SOLID97 S8|98 SOLID98 S4|99 SHELL99 SH|106 VISCO106 S8|107 VISCO107 S8|108 VISCO108 P4
110 INFIN110 P4|111 INFIN111 S8|115 INTER115 SH|116 FLUID116 L2|117 SOLID117 S8|119 HF119 S4|120 HF120 S8
121 PLANE121 P4|122 SOLID122 S8|123 SOLID123 S4|124 CIRCU124 --|125 CIRCU125 --|126 TRANS126 L2|129 FLUID129 L2
130 FLUID130 SH|131 SHELL131 SH|132 SHELL132 SH|136 FLUID136 SH|138 FLUID138 L2|139 FLUID139 L2|141 FLUID141 P4
142 FLUID142 S8|143 SHELL143 SH|144 ROM144 --|145 PLANE145 P4|146 PLANE146 P3|147 SOLID147 S8|148 SOLID148 S4
150 SHELL150 SH|151 SURF151 CL|152 SURF152 CS|153 SURF153 CL|154 SURF154 CS|155 SURF155 CL|156 SURF156 CL
157 SHELL157 SH|159 SURF159 CL|160 LINK160 L2|161 BEAM161 L2|162 PLANE162 P4|163 SHELL163 SH|164 SOLID164 S8
165 COMBI165 L2|166 MASS166 PT|167 LINK167 L2|168 SOLID168 S4|169 TARGE169 CL|170 TARGE170 CS|171 CONTA171 CL
172 CONTA172 CL|173 CONTA173 CS|174 CONTA174 CS|175 CONTA175 CS|176 CONTA176 CL|177 CONTA177 CL|178 CONTA178 L2
179 PRETS179 --|180 LINK180 L2|181 SHELL181 SH|182 PLANE182 P4|183 PLANE183 P4|184 MPC184 L2|185 SOLID185 S8
186 SOLID186 S8|187 SOLID187 S4|188 BEAM188 L2|189 BEAM189 L3|190 SOLSH190 S8|191 SOLID191 S8|192 INTER192 P4
193 INTER193 P4|194 INTER194 S8|195 INTER195 S8|200 MESH200 --|201 FOLLW201 PT|202 INTER202 P4|203 INTER203 P4
204 INTER204 S8|205 INTER205 S8|208 SHELL208 L2|209 SHELL209 L3|212 CPT212 P4|213 CPT213 P4|214 COMBI214 L2
215 CPT215 S8|216 CPT216 S8|217 CPT217 S4|218 FLUID218 SH|220 FLUID220 S8|221 FLUID221 S4|222 PLANE222 P4
223 PLANE223 P4|225 SOLID225 S8|226 SOLID226 S8|227 SOLID227 S4|230 PLANE230 P4|231 SOLID231 S8|232 SOLID232 S4
233 PLANE233 P4|236 SOLID236 S8|237 SOLID237 S4|238 PLANE238 P4|239 SOLID239 S8|240 SOLID240 S4|241 HSFLD241 CL
242 HSFLD242 CS|250 COMBI250 L2|251 SURF251 CL|257 INFIN257 --|252 SURF252 CS|263 REINF263 CL|264 REINF264 CL|265 REINF265 CS
272 SOLID272 --|273 SOLID273 --|278 SOLID278 S8|279 SOLID279 S8|280 CABLE280 L3|281 SHELL281 SH|285 SOLID285 S4
288 PIPE288 L2|289 PIPE289 L3|290 ELBOW290 L3|291 SOLID291 S4|292 PLANE292 P4|293 PLANE293 P4
"""
ELEMENTS = {}
for _row in _ELEMENTS.replace('\n', '|').split('|'):
    _t = _row.split()
    if len(_t) == 3:
        ELEMENTS[int(_t[0])] = (_t[1], _t[2])
OVERLAY = ('CS', 'CL')
# 2-D element families whose KEYOPT(3) = 1 means axisymmetric; elements that are always axisymmetric
_AXI_KEYOPT3 = {13, 25, 42, 53, 55, 67, 77, 82, 121, 162, 182, 183, 222, 223, 230, 233, 238, 292, 293, 29, 79, 81,
                110, 141, 145, 35}
_ALWAYS_AXI = {25, 51, 61, 75, 78, 83, 208, 209}
# element names that carry rotational degrees of freedom (beams, pipes, shells, rigid links, masses)
_ROT_PREFIX = ('BEAM', 'PIPE', 'ELBOW', 'SHELL', 'MPC184', 'MATRIX27', 'CABLE')
_STRUCT_LABELS = {'UX': 1, 'UY': 2, 'UZ': 3, 'ROTX': 4, 'ROTY': 5, 'ROTZ': 6, 'WARP': 7}
_FORCE_DIR = {'FX': 0, 'FY': 1, 'FZ': 2, 'MX': 0, 'MY': 1, 'MZ': 2}
ANTYPE_NAME = {0: 'Static', 1: 'Buckle', 2: 'Modal', 3: 'Harmonic', 4: 'Transient', 7: 'Substructure',
               8: 'Spectrum', 9: 'Soil'}
ANTYPE_WORD = {'STAT': 0, 'BUCK': 1, 'MODA': 2, 'HARM': 3, 'TRAN': 4, 'SUBS': 7, 'SPEC': 8, 'SOIL': 9}


def element_name(n):
    e = ELEMENTS.get(n)
    return e[0] if e else 'ETYPE%d' % n


# ANSYS face definitions (local corner indices, documented node letters) per class
_FACES_S8 = {1: (1, 0, 3, 2), 2: (0, 1, 5, 4), 3: (1, 2, 6, 5), 4: (2, 3, 7, 6), 5: (3, 0, 4, 7), 6: (4, 5, 6, 7)}
_FACES_S4 = {1: (1, 0, 2), 2: (0, 1, 3), 3: (1, 2, 3), 4: (2, 0, 3)}
_EDGES_P4 = {1: (1, 0), 2: (2, 1), 3: (3, 2), 4: (0, 3)}
_EDGES_P3 = {1: (1, 0), 2: (2, 1), 3: (0, 2)}
_EDGES_SH = {3: (1, 0), 4: (2, 1), 5: (3, 2), 6: (0, 3)}

# ---------------------------------------------------------------- CDB command whitelist
# Commands that CDWRITE,DB writes (blocked format), collected from CDB files written by MAPDL 2025 R1:
# step 0 of the plan and the MAPDL verification inputs (tools: dsh-ansys step2 vm corpus).
# Matched like MAPDL matches commands: by the first four characters.
CDB_WHITELIST = """
/COM /TITLE /PREP7 /NOPR /GO /CSYS /DTYPE FINISH *IF *ELSE *ENDIF *SET *DIM *PREAD NUMOFF DOF ETBLOCK ET KEYOPT
LOCAL CSCIR CSYS TYPE MAT REAL SECNUM ESYS NBLOCK EBLOCK CMBLOCK RLBLOCK MPTEMP MPDATA TB TBTEMP TBDATA TBPT TBFIELD
TBIN TBLE SECTYPE SECDATA SECOFFSET SECBLOCK SECCONTROL SECJOINT SECLOCK SECSTOP SECMODIF CP CE EXTOPT CECMOD
TREF IRLF BFUNIF ACEL OMEGA DOMEGA CGLOC CGOMEGA DCGOMG CMOMEGA CMDOMEGA ANTYPE NLGEOM NSUBST DELTIM KUSE TIME
ALPHAD BETAD DMPRAT DMPSTR CRPLIM NCNV NEQIT ERESX OUTRES OUTPR DBLOCK IC FBLOCK BFEBLOCK BFBLOCK SFEBLOCK SFBEAM
SFE BFE BF CNVTOL KBC AUTOTS LNSRCH NROPT PSTRES TIMINT TINTP TRNOPT MODOPT MXPAND EQSLV HROPT HROUT HARFRQ
BUCOPT SPOPT PSDFRQ PSDUNIT PSDVAL PSDRES PSDC SV SVTYPE FREQ CYCLIC CORIOLIS EMUNIT EMSYM MAGOPT SSTIF PRED
SFGRAD STEF SPCTEMP SLOAD SED SUBOPT EXPASS RESIDUAL NUMEXP HREXP OCTYPE OCDATA OCTABLE FJ DJ ADDAM VDDAM NRLS
HEMIOPT RADOPT VFOPT V2DOPT BSAX BSM1 BSM2 BSTQ BSS1 BSS2 BSMD MIDTOL LUMPM SRSS MMASS RIGRESP QRDOPT MODCONTROL
TOFFST CINT MODE INISTATE TBTEM TBDAT N EN E COVAL FC GRP GP EXPSOL LVSCAL CMROTATE PMLOPT SFCONTROL SE SPFREQ SPVAL SPUNIT RIGID
"""
_WL4 = {w[:4] for w in CDB_WHITELIST.split()}
# end of data blocks
_RE_FMT_INT = re.compile(r'(\d*)i(\d+)', re.I)
_BLOCKS = ('NBLOCK', 'EBLOCK', 'ETBLOCK', 'CMBLOCK', 'RLBLOCK', 'SECBLOCK', 'DBLOCK', 'FBLOCK', 'SFEBLOCK',
           'BFEBLOCK', 'BFBLOCK')


def _fmt_fields(spec):
    """'(3i9,6e21.13e3)' -> list of (kind, width); kind i = integer, f = real, a = text; width 0 = free"""
    s = spec.strip().lower().replace('p', '')
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1]
    out = []
    for part in re.split(r',(?![^(]*\))', s):
        part = part.strip()
        m = re.match(r'^(\d*)\((.*)\)$', part)
        if m:                                               # 6(pg20.9) / 2(pg16.9) / 6(a)
            n = int(m.group(1) or 1)
            inner = _fmt_fields('(' + m.group(2) + ')')
            out.extend(inner * n)
            continue
        m = re.match(r'^(\d*)([iegfa])(\d*)', part)
        if not m:
            continue
        n = int(m.group(1) or 1)
        kind = 'i' if m.group(2) == 'i' else 'a' if m.group(2) == 'a' else 'f'
        out.extend([(kind, int(m.group(3) or 0))] * n)
    return out


def _num(s):
    s = s.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        # Fortran without the E: 1.5-3, 2.0+10
        m = re.match(r'^([+-]?\d*\.?\d*)([+-]\d+)$', s)
        if m:
            return float(m.group(1)) * 10.0 ** int(m.group(2))
        raise


def _int(s):
    s = s.strip()
    return int(s) if s else 0


def _cmd_of(line):
    """command word of an input line (upper case), '' for comments / blank"""
    s = line.strip()
    if not s or s.startswith('!'):
        return ''
    return s.split(',')[0].split('!')[0].strip().upper()


class CdbError(ValueError):
    pass


_STAR_OK = {'*IF', '*ELSE', '*ENDIF', '*SET', '*DIM', '*PREAD'}


# ---------------------------------------------------------------- the model
class Model(inp_model.Model):
    """CDB -> the structures of inp_model.Model (see the module docstring)"""

    def __init__(self, path, force=False, max_auto_mb=None, solve_path=None):
        """solve_path: the solve segment; None = <job>.solve.dat next to the CDB when it exists, False = none"""
        self._init_state(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.size_bytes = os.path.getsize(path)
        self.n_files = 1
        check_memory(path, self.size_bytes, force, max_auto_mb)
        self.solver = 'mapdl'
        self.release = ''
        self.cmd_count = {}            # command -> count (outside data blocks)
        self.violations = []           # (line, text): what the whitelist / the format checks refuse
        self.notes = []                # (level, text) gathered while reading
        self.etypes = {}               # type number -> {'ename', 'name', 'cls', 'keyopt': [18]}
        self.reals = {}                # real constant set -> [values]
        self.mprops = {}               # material -> {label: [(temp, value)]}
        self.tbs = {}                  # material -> [{'lab', 'opt', 'rows', 'n'}]
        self.secs = {}                 # section id -> {'type', 'subtype', 'name', 'data', 'layers', ...}
        self.csys = {}                 # LOCAL id -> {'type', 'loc', 'ang', 'prm'}
        self.params = {}               # *SET scalars
        self.tables = {}               # *DIM name -> {'type', 'dims', 'vars', 'values'}
        self.settings = []             # (command, [args]) analysis settings in file order
        self.inertia = {}              # ACEL / OMEGA / ... -> [x, y, z]
        self.bfunif = None
        self.tref = None
        self.node_rot = {}             # node index -> (thxy, thyz, thzx) in degrees
        self._dl = []                  # DBLOCK rows: (node index, label, value, value2 or None, table name or '')
        self._fl = []                  # FBLOCK rows
        self._sfe = []                 # (element index, face, label, kval, [values], table)
        self._bfn = {}                 # BF label -> [(node index, value)]
        self._bfe = {}                 # BFE label -> [(element index, loc, value)]
        self._ics = []                 # (node index, label, v1, v2)
        self._sfbeam = []
        self._ce = []                  # {'id', 'const', 'terms': [(node index, label, coef)]}
        self._cp = {}                  # set id -> {'label', 'nodes': [labels]}
        self._ist = []                 # INISTATE files created in the CDB
        self._supers = []              # SE lines: (type, element number, file)
        self.e_attr = {}               # filled in _build: per element arrays
        self._econn = array('i')       # original connectivity (node indices, MAPDL order, with repeats)
        self._eoff = array('i', [0])
        self._ecls = []                # per type code: class
        self._overlay = set()          # type codes of overlay elements (not part of the drawn skin)
        self._unknown_names = {}
        self.dead = 0
        self.seg = None                # the solve segment (solve_segment.parse) or None
        self.seg_steps = []
        self.cm_names = {'NODE': [], 'ELEM': []}   # CMBLOCK components (the sets the preview makes are not here)
        self.solve_file = os.path.splitext(path)[0] + '.solve.dat'
        self._read()
        self._build()
        self._build_segment(solve_path)

    # ------------------------------------------------------------ reading
    def _bad(self, ln, text):
        if len(self.violations) < 200:
            self.violations.append((ln, text))

    def _read(self):
        with open(self.path, 'rb') as f:
            raw = f.read()
        text = raw.decode('latin-1')
        self._lines = text.split('\n')
        lines = self._lines
        n = len(lines)
        i = 0
        cur = {'TYPE': 1, 'MAT': 1, 'REAL': 1, 'SECN': 1, 'ESYS': 0}
        mptemp = {}
        tb = None
        sec = None
        ce = None
        in_if = 0
        unblocked = 0
        first = True
        while i < n:
            ln = i + 1
            line = lines[i].rstrip('\r')
            i += 1
            s = line.strip()
            if not s or s.startswith('!'):
                continue
            if first:
                first = False
                if s.upper().startswith('/COM,ANSYS RELEASE'):
                    self.release = ' '.join(s[5:].split()[:6])
            up = s.upper()
            cmd = _cmd_of(s)
            # a "$" outside a comment puts several commands on one line (MAPDL splits there)
            body = s.split('!')[0]
            if '$' in body and not up.startswith(('/COM', '/TITLE')):
                self._bad(ln, '一行里有 $（MAPDL 会在这里拆成几条命令）: %s' % s[:80])
            # ---- assignment: only the flag CDWRITE resets
            if '=' in cmd and not cmd.startswith('*'):
                name = cmd.split('=')[0].strip()
                if name != '_CDRDOFF':
                    self._bad(ln, '参数赋值不在白名单内: %s' % s[:80])
                self.cmd_count['_CDRDOFF='] = self.cmd_count.get('_CDRDOFF=', 0) + 1
                continue
            key = cmd[:4]
            self.cmd_count[cmd] = self.cmd_count.get(cmd, 0) + 1
            f = [x.strip() for x in body.split(',')]
            try:
                if cmd in _BLOCKS:
                    i = getattr(self, '_blk_' + cmd.lower())(i, f, ln)
                    continue
                if key == '*CRE':
                    i = self._create_block(i, f, ln)
                    continue
                if re.match(r'^(BF|BFE|SFE),END,LOC,\s*-1,?$', re.sub(r'\s+', '', up) and up.replace(' ', '')):
                    continue                                # a spare end line of a load block (MAPDL writes some)
                if cmd.startswith('*') and cmd not in _STAR_OK or not cmd.startswith('*') and key not in _WL4:
                    self._bad(ln, '命令 %s 不在 CDB 白名单内' % cmd)
                    continue
                if cmd == '*IF':
                    if len(f) < 2 or f[1].upper() != '_CDRDOFF':
                        self._bad(ln, '*IF 只允许 CDWRITE 写的那一种 (*IF,_CDRDOFF,...): %s' % s[:80])
                    in_if += 1
                elif cmd == '*ENDIF':
                    in_if -= 1
                elif key == '*SET':
                    self._set_param(f)
                elif key == '*DIM':
                    self._dim_cmd(f)
                elif key == '*PRE':
                    i = self._pread(i, f, ln)
                elif cmd == 'N' or cmd == 'EN' or cmd == 'E':
                    unblocked += 1
                    if unblocked == 1:
                        self._bad(ln, '这是非块格式的 CDB (逐条的 %s 命令), 预览只读默认的块格式: '
                                      '请用 CDWRITE,DB,...,BLOCKED 导出' % cmd)
                elif key == '/TIT':
                    self.heading = s.split(',', 1)[1].strip() if ',' in s else ''
                elif key in ('TYPE', 'MAT', 'REAL', 'SECN', 'ESYS'):
                    try:
                        cur[key] = int(float(f[1]))
                    except (ValueError, IndexError):
                        pass
                elif key == 'ET':
                    self._et(f)
                elif key == 'KEYO':
                    self._keyopt(f)
                elif key == 'LOCA':
                    self._local(f)
                elif key == 'MPTE':
                    mptemp = self._mptemp(f, mptemp)
                elif key == 'MPDA':
                    self._mpdata(f, mptemp)
                elif key == 'TB':
                    tb = self._tb(f)
                elif key in ('TBTE', 'TBDA', 'TBPT', 'TBFI', 'TBIN', 'TBLE'):
                    if tb is not None:
                        tb['n'] += 1
                        if len(tb['rows']) < 12:
                            tb['rows'].append([cmd] + [x for x in f[1:] if x != ''])
                elif key == 'SECT':
                    sec = self._sectype(f)
                elif key in ('SECD', 'SECO', 'SECC', 'SECJ', 'SECL', 'SECS', 'SECM'):
                    if sec is not None:
                        sec.setdefault('rows', []).append([cmd] + [x for x in f[1:]])
                        if key == 'SECD':
                            sec['data'].extend(_floats_loose(f[1:]))
                elif key == 'CP':
                    self._cp_line(f)
                elif key == 'CE':
                    ce = self._ce_line(f, ce)
                elif key == 'IC':
                    self._ic(f)
                elif key == 'SFBE':
                    i = self._sfbeam_line(i, f, ln)
                elif key in ('ACEL', 'OMEG', 'DOME', 'CGLO', 'CGOM', 'DCGO', 'CMOM', 'CMDO'):
                    part = f[2:5] if key in ('CMOM', 'CMDO') else f[1:4]
                    vec = (_floats_loose(part) + [0.0] * 3)[:3]
                    self.inertia[cmd if key not in ('CMOM', 'CMDO') else '%s %s' % (cmd, f[1])] = vec
                elif cmd == 'SE':
                    # SE,UNBL,file,,,toler,type,element: a superelement (MATRIX50) read from a .sub file
                    try:
                        self._supers.append((int(float(f[6])), int(float(f[7])), f[2]))
                    except (ValueError, IndexError):
                        self._bad(ln, 'SE 这一行读不懂: %s' % s[:80])
                elif key == 'BF' and cmd == 'BF':
                    g = self.root.node_map.get(int(float(f[1])))
                    if g is not None and len(f) > 3:
                        self._bfn.setdefault(f[2].upper(), []).append((g, _num(f[3]) if f[3] else 0.0))
                elif key == 'BFUN':
                    self.bfunif = (f[1] if len(f) > 1 else '', f[2] if len(f) > 2 else '')
                elif key == 'TREF':
                    self.tref = _floats_loose(f[1:2])[0] if _floats_loose(f[1:2]) else None
                elif key == 'INIS':
                    self.settings.append((cmd, f[1:]))
                elif key in ('/COM', '/PRE', '/NOP', '/GO', 'FINI', '*ELS', 'NUMO', 'DOF', 'EXTO', 'CECM',
                             'CSYS', '/CSY', 'CSCI', 'IRLF', '/DTY'):
                    pass
                else:
                    self.settings.append((cmd, f[1:]))
            except (ValueError, IndexError) as e:
                self._bad(ln, '%s 这一行读不懂 (%s): %s' % (cmd, type(e).__name__, s[:80]))
        if in_if != 0:
            self._bad(n, '*IF 和 *ENDIF 不配对')
        self._lines = None

    # -- blocks ---------------------------------------------------------
    def _format(self, i, ln, what):
        if i >= len(self._lines):
            raise CdbError('%s 缺格式行' % what)
        spec = self._lines[i].strip()
        if not spec.startswith('('):
            raise CdbError('%s 的格式行不对: %s' % (what, spec[:40]))
        return _fmt_fields(spec)

    def _rows(self, i, fields, what, end, ln, embedded=None):
        """data rows of a block until its end marker -> (next line index, list of (line number, values)).
        embedded: {command: handler(i, fields, line number) -> next i} for commands MAPDL writes inside the block"""
        lines = self._lines
        out = []
        n = len(lines)
        while i < n:
            raw = lines[i].rstrip('\r')
            s = raw.strip()
            i += 1
            if not s:
                continue
            if end(s):
                return i, out
            if embedded:
                c = _cmd_of(s)
                if c in embedded:
                    self.cmd_count[c] = self.cmd_count.get(c, 0) + 1
                    i = embedded[c](i, [x.strip() for x in s.split('!')[0].split(',')], i)
                    continue
            vals = self._row(raw, fields)
            if vals is None:
                self._bad(i, '%s 的数据行不符合格式（块里不能夹命令）: %s' % (what, s[:80]))
                continue
            out.append((i, vals))
        self._bad(ln, '%s 没有结束行' % what)
        return i, out

    @staticmethod
    def _row(raw, fields):
        """one fixed-format row -> list of values (int / float / str), None when it does not fit"""
        vals = []
        pos = 0
        free = any(w == 0 for _, w in fields)
        if free:
            toks = raw.split()
            for (kind, w), t in zip(fields, toks):
                if kind == 'i':
                    if not re.match(r'^[+-]?\d+$', t):
                        return None
                    vals.append(int(t))
                elif kind == 'f':
                    try:
                        vals.append(_num(t))
                    except ValueError:
                        if '%' in t:
                            vals.append(t)
                        else:
                            return None
                else:
                    vals.append(t)
            return vals
        n = len(raw)
        for kind, w in fields:
            if pos >= n:
                break
            piece = raw[pos:pos + w]
            pos += w
            if kind == 'i':
                t = piece.strip()
                if not t:
                    vals.append(0)
                    continue
                if not re.match(r'^[+-]?\d+$', t):
                    return None
                vals.append(int(t))
            elif kind == 'f':
                t = piece.strip()
                try:
                    vals.append(_num(t))
                except ValueError:
                    if '%' in t:
                        vals.append(t)
                    else:
                        return None
            else:
                vals.append(piece.strip())
        if pos < n and raw[pos:].strip():
            return None                        # more text than the format holds
        return vals

    def _blk_nblock(self, i, f, ln):
        fields = self._format(i, ln, 'NBLOCK')
        i += 1
        lines = self._lines
        nint = sum(1 for k, _ in fields if k == 'i')
        widths = [w for _, w in fields]
        wi = sum(widths[:nint])
        fw = widths[nint] if len(widths) > nint else 21
        root = self.root
        put = self._put_node
        rot = self.node_rot
        n = len(lines)
        while i < n:
            raw = lines[i].rstrip('\r')
            i += 1
            s = raw.strip()
            if not s:
                continue
            if s.upper().startswith('N,') or s == '-1':
                return i
            try:
                lab = int(raw[0:widths[0]])
                if lab < 0:
                    return i
                v = []
                p = wi
                while p < len(raw) and len(v) < 6:
                    t = raw[p:p + fw].strip()
                    v.append(_num(t) if t else 0.0)
                    p += fw
                if p < len(raw) and raw[p:].strip():
                    raise ValueError('extra text')
            except ValueError:
                self._bad(i, 'NBLOCK 的数据行不符合格式（块里不能夹命令）: %s' % s[:80])
                continue
            v.extend([0.0] * (6 - len(v)))
            g = put(root, lab, (v[0], v[1], v[2]))
            if v[3] or v[4] or v[5]:
                rot[g] = (v[3], v[4], v[5])
        self._bad(ln, 'NBLOCK 没有结束行')
        return i

    def _blk_eblock(self, i, f, ln):
        solid = len(f) > 2 and f[2].upper().startswith('SOLID')
        fields = self._format(i, ln, 'EBLOCK')
        i += 1
        lines = self._lines
        per = len(fields)
        w = fields[0][1] if fields else 10
        n = len(lines)
        rows = self._erows
        while i < n:
            raw = lines[i].rstrip('\r')
            i += 1
            s = raw.strip()
            if not s:
                continue
            t = s.split()
            if t[0] == '-1':
                return i
            try:
                v = [int(x) for x in t]
                if len(raw.rstrip()) > per * w:
                    raise ValueError
            except ValueError:
                self._bad(i, 'EBLOCK 的数据行不符合格式（块里不能夹命令）: %s' % s[:80])
                continue
            if not solid:
                self._bad(i, 'EBLOCK 不是 SOLID 格式, 预览暂不支持这种写法')
                return self._skip_to(i, lambda x: x.strip() == '-1')
            if len(v) < 11:
                self._bad(i, 'EBLOCK 数据行太短: %s' % s[:80])
                continue
            nn = v[8]
            nodes = v[11:]
            while len(nodes) < nn and i < n:
                raw2 = lines[i].rstrip('\r')
                t2 = raw2.split()
                try:
                    nodes.extend(int(x) for x in t2)
                except ValueError:
                    self._bad(i + 1, 'EBLOCK 的续行不符合格式: %s' % raw2.strip()[:80])
                    break
                i += 1
            rows.append((i, v[:11], nodes[:nn]))
        self._bad(ln, 'EBLOCK 没有结束行')
        return i

    def _skip_to(self, i, end):
        lines = self._lines
        while i < len(lines):
            s = lines[i].strip()
            i += 1
            if end(s):
                break
        return i

    def _blk_etblock(self, i, f, ln):
        fields = self._format(i, ln, 'ETBLOCK')
        i += 1
        i, rows = self._rows(i, [('i', 0)] * 21, 'ETBLOCK', lambda s: s == '-1', ln)
        for _, v in rows:
            if len(v) >= 2:
                ko = [int(x) for x in v[2:20]] + [0] * 18
                self._def_type(v[0], v[1], ko[:18])
        return i

    def _def_type(self, itype, ename, keyopt=None):
        name, cls = ELEMENTS.get(ename, ('ETYPE%d' % ename, '--'))
        if ename not in ELEMENTS:
            self._unknown_names[name] = 0
        self.etypes[itype] = {'ename': ename, 'name': name, 'cls': cls,
                              'keyopt': list(keyopt) if keyopt else [0] * 18}

    def _et(self, f):
        itype = int(float(f[1]))
        nm = f[2].upper()
        m = re.match(r'^[A-Z]*(\d+)$', nm)
        ename = int(m.group(1)) if m else 0
        self._def_type(itype, ename, [_int(x) if re.match(r'^[+-]?\d+$', x or '') else 0 for x in f[3:9]] + [0] * 12)

    def _keyopt(self, f):
        itype, k, v = int(float(f[1])), int(float(f[2])), int(float(f[3]))
        et = self.etypes.get(itype)
        if et is not None and 1 <= k <= 18:
            et['keyopt'][k - 1] = v

    def _blk_cmblock(self, i, f, ln):
        name = f[1].strip()
        kind = f[2].strip().upper()
        cnt = int(f[3].split('!')[0])
        fields = self._format(i, ln, 'CMBLOCK')
        i += 1
        per = len(fields) or 8
        w = fields[0][1] if fields else 10
        vals = []
        lines = self._lines
        while len(vals) < cnt and i < len(lines):
            raw = lines[i].rstrip('\r')
            i += 1
            for k in range(0, len(raw), w):
                t = raw[k:k + w].strip()
                if t:
                    try:
                        vals.append(int(t))
                    except ValueError:
                        self._bad(i, 'CMBLOCK %s 的数据行不符合格式: %s' % (name, raw.strip()[:80]))
                        return i
        self._comps.append((name, kind, vals, ln))
        return i

    def _blk_rlblock(self, i, f, ln):
        nsets = int(f[1])
        f1 = self._format(i, ln, 'RLBLOCK')
        f2 = self._format(i + 1, ln, 'RLBLOCK')
        i += 2
        lines = self._lines
        for _ in range(nsets):
            if i >= len(lines):
                break
            v = self._row(lines[i].rstrip('\r'), f1)
            i += 1
            if v is None or len(v) < 2:
                self._bad(i, 'RLBLOCK 的数据行不符合格式')
                continue
            rid, cnt = v[0], v[1]
            vals = [x for x in v[2:] if not isinstance(x, str)]
            while len(vals) < cnt and i < len(lines):
                w = self._row(lines[i].rstrip('\r'), f2)
                i += 1
                if w is None:
                    self._bad(i, 'RLBLOCK 的续行不符合格式')
                    break
                vals.extend(x for x in w if not isinstance(x, str))
            self.reals[rid] = vals[:cnt]
        return i

    def _blk_secblock(self, i, f, ln):
        cnt = int(float(f[1])) if len(f) > 1 and f[1] else 0
        sec = self._cur_sec
        lines = self._lines
        if sec is not None and sec['type'] == 'SHELL':
            for _ in range(cnt):
                if i >= len(lines):
                    break
                t = [x.strip() for x in lines[i].split(',')]
                i += 1
                try:
                    sec['layers'].append((_num(t[0]), int(float(t[1])), _num(t[2]) if len(t) > 2 else 0.0,
                                          int(float(t[3])) if len(t) > 3 and t[3] else 0))
                except (ValueError, IndexError):
                    self._bad(i, 'SECBLOCK 的数据行不符合格式: %s' % lines[i - 1].strip()[:80])
            return i
        # other section types: numeric rows until the next command
        while i < len(lines):
            s = lines[i].strip()
            if not s:
                i += 1
                continue
            if re.match(r'^[A-Za-z/*_]', s):
                break
            if not re.match(r'^[\s,+\-.0-9EeDd]+$', s):
                self._bad(i + 1, 'SECBLOCK 的数据行不符合格式: %s' % s[:80])
            i += 1
        return i

    def _load_rows(self, i, f, ln, what, end):
        fields = self._format(i, ln, what)
        i += 1
        return self._rows(i, fields, what, end, ln)

    def _blk_dblock(self, i, f, ln):
        i, rows = self._load_rows(i, f, ln, 'DBLOCK', lambda s: s == '-1')
        get = self.root.node_map.get
        for rl, v in rows:
            g = get(v[0])
            if g is None:
                self.notes.append(('warn', 'DBLOCK 第 %d 行: 约束加在不存在的节点 %d 上, 已忽略' % (rl, v[0])))
                continue
            lab = str(v[1]).upper()
            a = v[2] if len(v) > 2 else 0.0
            b = v[3] if len(v) > 3 else 0.0
            if isinstance(a, str):
                self._dl.append((g, lab, 0.0, 0.0, a.strip('%')))
            else:
                self._dl.append((g, lab, a, b if not isinstance(b, str) else 0.0, ''))
        return i

    def _blk_fblock(self, i, f, ln):
        i, rows = self._load_rows(i, f, ln, 'FBLOCK', lambda s: s == '-1')
        get = self.root.node_map.get
        for rl, v in rows:
            g = get(v[0])
            if g is None:
                self.notes.append(('warn', 'FBLOCK 第 %d 行: 力加在不存在的节点 %d 上, 已忽略' % (rl, v[0])))
                continue
            lab = str(v[1]).upper()
            a = v[2] if len(v) > 2 else 0.0
            if isinstance(a, str):
                self._fl.append((g, lab, 0.0, 0.0, a.strip('%')))
            else:
                b = v[3] if len(v) > 3 and not isinstance(v[3], str) else 0.0
                self._fl.append((g, lab, a, b, ''))
        return i

    def _blk_sfeblock(self, i, f, ln):
        lab = f[2].upper() if len(f) > 2 else ''
        fields = self._format(i, ln, 'SFEBLOCK')
        # MAPDL writes the SFBEAM loads of beam elements in the middle of this block (SFBEAM line + values line)
        i, rows = self._rows(i + 1, fields, 'SFEBLOCK', lambda s: s.upper().startswith('SFE,'), ln,
                             embedded={'SFBEAM': self._sfbeam_line})
        for rl, v in rows:
            if len(v) < 4:
                continue
            vals = v[3:]
            tab = ''
            if any(isinstance(x, str) and x for x in vals):
                tab = next(x for x in vals if isinstance(x, str) and x).strip('%')
                vals = []
            self._sfe.append((v[0], v[1], lab, v[2], [x for x in vals if not isinstance(x, str)], tab, rl))
        return i

    def _blk_bfeblock(self, i, f, ln):
        lab = f[2].upper() if len(f) > 2 else ''
        i, rows = self._load_rows(i, f, ln, 'BFEBLOCK', lambda s: s.upper().startswith('BFE,'))
        lst = self._bfe.setdefault(lab, [])
        for rl, v in rows:
            if len(v) >= 3:
                lst.append((v[0], v[1], v[2]))
        return i

    def _blk_bfblock(self, i, f, ln):
        lab = f[2].upper() if len(f) > 2 else ''
        i, rows = self._load_rows(i, f, ln, 'BFBLOCK', lambda s: s.upper().startswith('BF,'))
        get = self.root.node_map.get
        lst = self._bfn.setdefault(lab, [])
        for rl, v in rows:
            if len(v) >= 2:
                g = get(v[0])
                if g is not None:
                    lst.append((g, v[1]))
        return i

    def _create_block(self, i, f, ln):
        """*CREATE,file ... *END: CDWRITE writes initial-state data this way (INISTATE,READ,file)"""
        name = f[1].strip() if len(f) > 1 else ''
        if len(f) > 2 and f[2].strip():
            name = '%s.%s' % (name, f[2].strip())        # *CREATE,Fname,Ext
        if not name.upper().endswith('.IST'):
            self._bad(ln, '*CREATE 只允许写初始状态文件 (.IST): %s' % name)
        lines = self._lines
        while i < len(lines):
            s = lines[i].strip().upper()
            i += 1
            if s.startswith('*END'):
                break
        self._ist.append(name)
        return i

    def _pread(self, i, f, ln):
        """*PREAD rows: numbers of 20 characters each (they can touch: 0.29E-06-0.31E-06)"""
        name = f[1].strip().upper() if len(f) > 1 else ''
        vals = []
        lines = self._lines
        while i < len(lines):
            raw = lines[i].rstrip('\r')
            s = raw.strip()
            i += 1
            if s.upper().startswith('END PREAD'):
                break
            try:
                for k in range(0, len(raw), 20):
                    t = raw[k:k + 20].strip()
                    if t:
                        vals.append(_num(t))
            except ValueError:
                self._bad(i, '*PREAD 的数据行不符合格式: %s' % s[:80])
        t = self.tables.get(name)
        if t is not None:
            t['values'] = vals
        return i

    def _sfbeam_line(self, i, f, ln):
        lines = self._lines
        vals = []
        if len(f) > 4 and f[4].upper() == 'UNBL' and i < len(lines):
            s = lines[i].strip()
            i += 1
            try:
                vals = [_num(x) for x in s.split()]
            except ValueError:
                self._bad(i, 'SFBEAM 的数据行不符合格式: %s' % s[:80])
        self._sfbeam.append((f[1:4], vals))
        return i

    # -- single commands -----------------------------------------------
    def _set_param(self, f):
        name = f[1].split('(')[0].strip().upper()
        val = f[2].strip() if len(f) > 2 else ''
        if '(' in f[1]:
            return                                  # element of an array
        try:
            self.params[name] = float(val)
        except ValueError:
            self.params[name] = val.strip("'")

    def _dim_cmd(self, f):
        name = f[1].strip().upper()
        dims = []
        for x in f[3:6]:
            try:
                dims.append(int(float(x)))
            except ValueError:
                dims.append(1)
        self.tables[name] = {'type': f[2].strip().upper() if len(f) > 2 else '', 'dims': dims,
                             'vars': [x.strip() for x in f[6:9] if x.strip()], 'values': []}

    def _local(self, f):
        # LOCAL,UNBL,LOC|ANG|PRM,id,type,values
        if len(f) > 3 and f[1].upper() == 'UNBL':
            cid = int(float(f[3]))
            ent = self.csys.setdefault(cid, {'id': cid, 'type': 0, 'loc': [0.0] * 3, 'ang': [0.0] * 3, 'prm': [1.0, 1.0]})
            ent['type'] = int(float(f[4])) if f[4] else 0
            vals = _floats_loose(f[5:8])
            what = f[2].upper()
            if what == 'LOC':
                ent['loc'] = (vals + [0.0] * 3)[:3]
            elif what == 'ANG':
                ent['ang'] = (vals + [0.0] * 3)[:3]
            elif what == 'PRM':
                ent['prm'] = (vals + [1.0, 1.0])[:2]

    def _mptemp(self, f, cur):
        # MPTEMP,UNBL,start,count,t1,...   (or MPTEMP,start,t1,...)
        if len(f) > 1 and f[1].upper() == 'UNBL':
            start = int(float(f[2]))
            vals = _floats_loose(f[4:])
        else:
            start = int(float(f[1])) if len(f) > 1 and f[1] else 1
            vals = _floats_loose(f[2:])
        if start <= 1:
            cur = {}
        for k, v in enumerate(vals):
            cur[start + k] = v
        return cur

    def _mpdata(self, f, temps):
        # MPDATA,UNBL,?,LAB,MAT,LOC,v1,...   (or MPDATA,LAB,MAT,LOC,v1,...)
        if len(f) > 1 and f[1].upper() == 'UNBL':
            lab, mat, loc, vals = f[3].upper(), int(float(f[4])), int(float(f[5])), _floats_loose(f[6:])
        else:
            lab, mat, loc, vals = f[1].upper(), int(float(f[2])), int(float(f[3] or 1)), _floats_loose(f[4:])
        props = self.mprops.setdefault(mat, {})
        lst = props.setdefault(lab, [])
        for k, v in enumerate(vals):
            lst.append((temps.get(loc + k, 0.0), v))

    def _tb(self, f):
        lab = f[1].upper() if len(f) > 1 else ''
        mat = int(float(f[2])) if len(f) > 2 and f[2] else 1
        opt = f[5].upper() if len(f) > 5 else ''
        ent = {'lab': lab, 'opt': opt, 'rows': [], 'n': 0, 'args': [x for x in f[1:] if x]}
        self.tbs.setdefault(mat, []).append(ent)
        return ent

    def _sectype(self, f):
        sid = int(float(f[1]))
        ent = {'id': sid, 'type': f[2].upper() if len(f) > 2 else '', 'subtype': f[3].upper() if len(f) > 3 else '',
               'name': f[4] if len(f) > 4 else '', 'data': [], 'layers': [], 'rows': []}
        self.secs[sid] = ent
        self._cur_sec = ent
        return ent

    def _cp_line(self, f):
        # CP,UNBL,count,id,LAB,n1,n2,...
        if len(f) > 4 and f[1].upper() == 'UNBL':
            sid = int(float(f[3]))
            ent = self._cp.setdefault(sid, {'id': sid, 'label': f[4].upper(), 'count': int(float(f[2])), 'nodes': []})
            ent['nodes'].extend(int(float(x)) for x in f[5:] if x)

    def _ce_line(self, f, cur):
        # CE,UNBL,DEFI,nterms,id,const  /  CE,UNBL,NODE,n1,lab1,c1,n2,lab2,c2
        if len(f) > 2 and f[1].upper() == 'UNBL':
            if f[2].upper() == 'DEFI':
                cur = {'id': int(float(f[4])), 'nterms': int(float(f[3])), 'const': _num(f[5]) if len(f) > 5 else 0.0,
                       'terms': []}
                self._ce.append(cur)
            elif f[2].upper() == 'NODE' and cur is not None:
                t = f[3:]
                for k in range(0, len(t) - 2, 3):
                    if t[k]:
                        cur['terms'].append((int(float(t[k])), t[k + 1].upper(), _num(t[k + 2])))
        return cur

    def _ic(self, f):
        get = self.root.node_map.get
        g = get(int(float(f[1])))
        if g is not None:
            self._ics.append((g, f[2].upper(), _num(f[3]) if len(f) > 3 else 0.0, _num(f[4]) if len(f) > 4 else 0.0))

    # ------------------------------------------------------------ building the shared structures
    _elems_done = False
    _cur_sec = None

    @property
    def _erows(self):
        r = getattr(self, '_erows_list', None)
        if r is None:
            r = self._erows_list = []
        return r

    @property
    def _comps(self):
        r = getattr(self, '_comps_list', None)
        if r is None:
            r = self._comps_list = []
        return r

    def _acode(self, name, fam, ncorner, overlay):
        key = '%s#%s' % (name, fam)
        c = self._tcode.get(key)
        if c is None:
            c = self._tcode[key] = len(self.types)
            self.types.append(key)
            self.fams.append((fam, ncorner, tuple(FACE_TAB[fam].items()) if fam in SOLID_FACES else None))
            if overlay:
                self._overlay.add(c)
        return c

    def _shape(self, et, nodes):
        """-> (family, corner node labels, other node labels, note) for one element"""
        cls = et['cls']
        nn = len(nodes)
        if cls == 'S8' and nn >= 8:
            I, J, K, L, M, N, O, P = nodes[:8]
            if K == L and M == N == O == P:
                return 'tet', [I, J, K, M], nodes[8:], 'tet'
            if K == L and O == P:
                return 'wedge', [I, J, K, M, N, O], nodes[8:], 'wedge'
            if M == N == O == P:
                return 'pyr', [I, J, K, L, M], nodes[8:], 'pyr'
            if len(set(nodes[:8])) == 8:
                return 'hex', list(nodes[:8]), nodes[8:], 'hex'
            return 'hex', list(nodes[:8]), nodes[8:], 'odd'
        if cls == 'S4' and nn >= 4:
            return 'tet', list(nodes[:4]), nodes[4:], 'tet'
        if cls in ('SH', 'P4') and nn >= 3:
            q = 'quad' if cls == 'SH' else 'quad2d'
            t = 'tri' if cls == 'SH' else 'tri2d'
            if et['ename'] == 183 and et['keyopt'][0] == 1 or nn in (3, 6) and cls == 'P4':
                return t, list(nodes[:3]), nodes[3:], 'tri'
            if nn >= 4 and nodes[2] == nodes[3]:
                return t, list(nodes[:3]), nodes[4:], 'tri'
            if nn >= 4:
                return q, list(nodes[:4]), nodes[4:], 'quad'
            return t, list(nodes[:3]), [], 'tri'
        if cls == 'P3' and nn >= 3:
            return 'tri2d', list(nodes[:3]), nodes[3:], 'tri'
        if cls == 'L3' and nn >= 3 and nodes[2]:
            return 'line', [nodes[0], nodes[2], nodes[1]], nodes[3:], 'line3'
        if cls == 'L3' and nn >= 2:
            return 'line', [nodes[0], nodes[1]], nodes[2:], 'line'
        if cls == 'L2' and nn >= 2:
            if nodes[1] == 0:
                return 'point', [nodes[0]], [], 'point'
            return 'line', [nodes[0], nodes[1]], nodes[2:], 'line'
        if cls == 'PT' and nn >= 1:
            return 'point', [nodes[0]], nodes[1:], 'point'
        if cls == 'CS':
            nz = [x for x in nodes if x]
            if len(nz) >= 4 and len(set(nz[:4])) == 4:
                return 'quad', nz[:4], nz[4:], 'quad'
            if len(nz) >= 4 and nz[2] == nz[3]:
                return 'tri', nz[:3], nz[4:], 'tri'
            if len(nz) == 3 or len(nz) == 6 or len(nz) == 7:
                return 'tri', nz[:3], nz[3:], 'tri'
            if len(nz) == 2:
                return 'line', nz[:2], [], 'line'
            if len(nz) >= 1:
                return 'point', nz[:1], nz[1:], 'point'
        if cls == 'CL':
            nz = [x for x in nodes if x]
            if len(nz) >= 3 and et['ename'] in (172, 153, 151, 209) and nz[2] != nz[1]:
                return 'line', [nz[0], nz[2], nz[1]], nz[3:], 'line3'
            if len(nz) >= 2:
                return 'line', nz[:2], nz[2:], 'line'
            if len(nz) == 1:
                return 'point', nz[:1], [], 'point'
        return None, [], list(nodes), 'none'

    def _build(self):
        root = self.root
        mesh = self.mesh
        nmap = root.node_map
        add = self._add_element
        e_mat, e_typ, e_real, e_secn, e_esys, e_dead, e_shape = (array('i'), array('i'), array('i'), array('i'),
                                                                   array('i'), bytearray(), [])
        odd = 0
        missing_nodes = 0
        shapes = {}
        econn, eoff = self._econn, self._eoff
        for rl, a, nodes in self._erows:
            mat, typ, real, secn, esys, dead, _solid, _shape, nn, _x, lab = a[:11]
            et = self.etypes.get(typ)
            if et is None:
                self._def_type(typ, 0)
                et = self.etypes[typ]
                self.notes.append(('error', '单元 %d 用的单元类型 %d 没有定义' % (lab, typ)))
            g_all = []
            bad = False
            for x in nodes:
                if x == 0:
                    g_all.append(-1)
                    continue
                g = nmap.get(x)
                if g is None:
                    bad = True
                    break
                g_all.append(g)
            if bad:
                missing_nodes += 1
                continue
            fam, corners, rest, note = self._shape(et, [x for x in nodes])
            name = et['name']
            if fam is not None and not all(corners):
                fam, note = None, 'none'                   # a corner left empty: do not draw it
            if fam is None:
                code = self._acode(name, None, 0, False)
                conn = [nmap.get(x) for x in nodes if x]
            else:
                code = self._acode(name, fam, len(corners), et['cls'] in OVERLAY)
                conn = [nmap.get(x) for x in corners]      # the corners in the order of the drawn family
                cs = set(conn)
                for x in rest:                             # then every other node once (midside, extra nodes)
                    if x:
                        g = nmap.get(x)
                        if g not in cs:
                            cs.add(g)
                            conn.append(g)
            if note == 'odd':
                odd += 1
            e = add(root, lab, code, conn)
            e_mat.append(mat)
            e_typ.append(typ)
            e_real.append(real)
            e_secn.append(secn)
            e_esys.append(esys)
            e_dead.append(1 if dead else 0)
            e_shape.append(note)
            sk = (name, note)
            shapes[sk] = shapes.get(sk, 0) + 1
            econn.extend(g_all)
            eoff.append(len(econn))
        for typ, lab, fname in self._supers:          # superelements: counted, not drawn
            et = self.etypes.get(typ)
            if et is None:
                self._def_type(typ, 50)
                et = self.etypes[typ]
            add(root, lab, self._acode(et['name'], None, 0, False), [])
            for arr in (e_mat, e_typ, e_real, e_secn, e_esys):
                arr.append(typ if arr is e_typ else 0)
            e_dead.append(0)
            e_shape.append('super')
            shapes[(et['name'], 'super')] = shapes.get((et['name'], 'super'), 0) + 1
            eoff.append(len(econn))
        if self._supers:
            self.notes.append(('info', '%d 个超单元 (MATRIX50, SE 命令读入 %s) 预览不画' % (
                len(self._supers), ', '.join(sorted({x[2] for x in self._supers})[:3]))))
        self._erows_list = None
        self._elems_done = True
        self.e_attr = {'mat': e_mat, 'type': e_typ, 'real': e_real, 'secn': e_secn, 'esys': e_esys,
                       'dead': e_dead, 'shape': e_shape}
        self.dead = sum(e_dead)
        self._shapes = shapes
        if missing_nodes:
            self.notes.append(('error', '%d 个单元引用了不存在的节点, 预览中已跳过' % missing_nodes))
        if odd:
            self.notes.append(('info', '%d 个单元的退化形状不是常见的棱柱/四面体/金字塔写法, 按六面体画' % odd))
        self._build_components()
        self._build_materials()
        self._build_sections()
        self._build_contact()
        self._build_constraints()
        self._build_bcs_loads()
        self._build_counts()
        self._finalize_flat()

    def _build_components(self):
        root = self.root
        nget, eget = root.node_map.get, root.elem_map.get
        for name, kind, vals, ln in self._comps:
            ids = []
            k = 0
            miss = 0
            get = nget if kind == 'NODE' else eget
            while k < len(vals):
                a = vals[k]
                if k + 1 < len(vals) and vals[k + 1] < 0:
                    b = -vals[k + 1]
                    for lab in range(a, b + 1):
                        g = get(lab)
                        if g is None:
                            miss += 1
                        else:
                            ids.append(g)
                    k += 2
                else:
                    g = get(a)
                    if g is None:
                        miss += 1
                    else:
                        ids.append(g)
                    k += 1
            arr = array('i', sorted(set(ids)))
            table = self.nsets if kind == 'NODE' else self.elsets
            table[name.upper()] = {'name': name, 'ids': arr, 'internal': name.startswith('_'), 'pieces': None,
                                   'unsorted': False, 'nmiss': miss}
            if miss:
                self.notes.append(('warn', '组件 %s 中有 %d 个编号不存在, 已忽略' % (name, miss)))
            self.cm_names[kind].append(name)
        self._comps_list = None

    def _prop(self, mat, lab, default=None):
        lst = self.mprops.get(mat, {}).get(lab)
        if not lst:
            return default
        return lst[0][1]

    def _build_materials(self):
        used = set(self.e_attr['mat'])
        for m in sorted(set(self.mprops) | set(self.tbs)):
            props = self.mprops.get(m, {})
            mat = {'name': 'MAT%d' % m, 'id': m, 'options': []}
            e = self._prop(m, 'EX')
            nu = self._prop(m, 'PRXY', self._prop(m, 'NUXY'))
            rho = self._prop(m, 'DENS')
            if e is not None:
                mat['E'] = e
            if nu is not None:
                mat['nu'] = nu
            if rho is not None:
                mat['rho'] = rho
            rows = []
            for lab in sorted(props):
                vals = props[lab]
                if len(vals) == 1:
                    rows.append([lab, _g(vals[0][1])])
                else:
                    rows.append([lab] + ['%s@%s' % (_g(v), _g(t)) for t, v in vals[:6]])
            if rows:
                mat['options'].append({'kw': 'MPDATA', 'params': {}, 'rows': rows, 'nrows': len(rows)})
            for tb in self.tbs.get(m, ()):
                mat['options'].append({'kw': 'TB,' + tb['lab'], 'params': {'TBOPT': tb['opt']} if tb['opt'] else {},
                                       'rows': tb['rows'], 'nrows': tb['n']})
            mat['used'] = m in used
            self.materials.append(mat)
        self._mat_name = {mt['id']: mt['name'] for mt in self.materials}

    def _sec_area(self, sec):
        """cross-section area of a beam / pipe / link section, None when the subtype is not handled"""
        d = sec['data']
        t, st = sec['type'], sec['subtype']
        try:
            if t == 'LINK':
                return d[0]
            if t == 'PIPE':
                do, tw = d[0], d[1]
                return math.pi / 4.0 * (do * do - (do - 2 * tw) ** 2)
            if t == 'BEAM':
                if st == 'RECT':
                    return d[0] * d[1]
                if st == 'CSOL':
                    return math.pi * d[0] ** 2
                if st == 'CTUB':
                    return math.pi * (d[1] ** 2 - d[0] ** 2)
                if st == 'ASEC':
                    return d[0]
                if st == 'HREC':
                    w1, w2, t1, t2, t3, t4 = d[:6]
                    return w1 * w2 - (w1 - t1 - t2) * (w2 - t3 - t4)
                if st in ('I', 'CHAN', 'Z'):
                    w1, w2, w3, t1, t2, t3 = d[:6]
                    return w1 * t1 + w2 * t2 + (w3 - t1 - t2) * t3
                if st in ('L', 'T'):
                    w1, w2, t1, t2 = d[:4]
                    return w1 * t1 + (w2 - t1) * t2
                if st == 'HATS':
                    w1, w2, w3, w4, t1, t2, t3, t4, t5 = d[:9]
                    return w1 * t1 + w2 * t2 + w3 * t3 + w4 * t4 + w4 * t5
        except (IndexError, TypeError):
            return None
        return None

    def _build_sections(self):
        """one 'section' per combination of element type, material, real constant set and section that elements use"""
        at = self.e_attr
        e_sec = self.mesh.e_sec
        combo = {}
        for e in range(len(at['mat'])):
            k = (at['type'][e], at['mat'][e], at['real'][e], at['secn'][e])
            idx = combo.get(k)
            if idx is None:
                idx = combo[k] = len(self.sections)
                typ, mat, real, secn = k
                et = self.etypes.get(typ, {'name': '?', 'keyopt': [0] * 18, 'cls': '--', 'ename': 0})
                data = ['TYPE %d, %s' % (typ, et['name'])]
                ko = ['KEYOPT(%d)=%d' % (i + 1, v) for i, v in enumerate(et['keyopt']) if v]
                if ko:
                    data.append(', '.join(ko))
                if real and real in self.reals:
                    data.append('REAL %d: %s' % (real, ' '.join(_g(v) for v in self.reals[real][:8])))
                sec = self.secs.get(secn) if secn else None
                if sec is not None and self._uses_section(et):
                    desc = 'SECTYPE %d, %s%s' % (secn, sec['type'], (',' + sec['subtype']) if sec['subtype'] else '')
                    if sec['layers']:
                        desc += ': ' + ' / '.join('t=%s MAT%d' % (_g(l[0]), l[1]) for l in sec['layers'][:6])
                    elif sec['data']:
                        desc += ': ' + ' '.join(_g(v) for v in sec['data'][:8])
                    data.append(desc)
                ent = {'type': et['name'], 'elset': '', 'material': self._mat_name.get(mat, '') if mat else '',
                       'instance': '', 'data': data, 'count': 0, 'params': {}, 'etype': typ, 'mat': mat,
                       'real': real, 'secnum': secn if self._uses_section(et) else 0, 'cls': et['cls']}
                if mat and mat not in self._mat_name and self._needs_mat(et):
                    ent['materialMissing'] = mat
                if sec is not None and sec['type'] == 'SHELL' and sec['layers']:
                    ent['thickness'] = sum(l[0] for l in sec['layers'])
                self.sections.append(ent)
            e_sec[e] = idx
            self.sections[idx]['count'] += 1

    @staticmethod
    def _uses_section(et):
        nm = et['name']
        return nm.startswith(('BEAM18', 'PIPE28', 'ELBOW', 'LINK180', 'SHELL', 'SOLSH', 'MPC184', 'CABLE',
                              'REINF', 'PRETS', 'SOLID185', 'SOLID186', 'PLANE18', 'COMBI250', 'GENB'))

    @staticmethod
    def _needs_mat(et):
        if et['cls'] in ('S8', 'S4', 'SH', 'P4', 'P3', 'L3'):
            return not et['name'].startswith(('INTER', 'MESH', 'CPT', 'HSFLD'))
        return et['name'].startswith(('BEAM', 'LINK', 'PIPE', 'ELBOW', 'CABLE', 'SHELL'))

    # -- contact -------------------------------------------------------
    def _build_contact(self):
        at = self.e_attr
        codes = self.mesh.e_code
        conta, targe = {}, {}
        for e in range(len(at['type'])):
            et = self.etypes.get(at['type'][e])
            if et is None:
                continue
            nm = et['name']
            if nm.startswith('CONTA') and nm != 'CONTA178':
                conta.setdefault(at['real'][e], []).append(e)
            elif nm.startswith('TARGE'):
                targe.setdefault(at['real'][e], []).append(e)
        self._pairs = []
        for r in sorted(set(conta) | set(targe)):
            ce, te = conta.get(r, []), targe.get(r, [])
            et = self.etypes.get(at['type'][ce[0]]) if ce else None
            ko12 = et['keyopt'][11] if et else 0
            ko2 = et['keyopt'][1] if et else 0
            behavior = {0: '标准', 1: '粗糙', 2: '不分离', 3: '绑定', 4: '不分离(始终)', 5: '绑定(始终)',
                        6: '绑定(初始接触)'}.get(ko12, 'KEYOPT(12)=%d' % ko12)
            mu = self._prop(at['mat'][ce[0]], 'MU') if ce else None
            sname, mname = '_CONTA_R%d' % r, '_TARGE_R%d' % r
            for nm, els in ((sname, ce), (mname, te)):
                fe, ff = array('i'), array('B')
                nodes = set()
                for e in els:
                    fam = self.fams[codes[e]][0]
                    fe.append(e)
                    ff.append(FID_WHOLE if fam in ('quad', 'tri') else 0 if fam == 'line' else 0)
                    o = self.mesh.e_off
                    nodes.update(self.mesh.e_nodes[o[e]:o[e + 1]])
                self.surfaces[nm.upper()] = {'name': nm, 'type': 'ELEMENT', 'fe': fe, 'ff': ff,
                                             'nodes': array('i'), 'analytic': None, 'internal': True, 'inst': 0}
                self.nsets[(nm + '_N').upper()] = {'name': nm + '_N', 'ids': array('i', sorted(nodes)),
                                                   'internal': True, 'pieces': None, 'unsorted': False, 'nmiss': 0}
            bonded = ko12 in (3, 5, 6)
            p = {'real': r, 'nContact': len(ce), 'nTarget': len(te), 'behavior': behavior, 'bonded': bonded,
                 'mu': mu, 'keyopt2': ko2}
            self._pairs.append(p)
            params = {'实常数': str(r), '行为': behavior}
            if mu:
                params['摩擦系数'] = _g(mu)
            if ko2:
                params['算法 KEYOPT(2)'] = str(ko2)
            if ce and te:
                self.contact_pairs.append({'slave': sname, 'master': mname, 'interaction': 'REAL %d' % r,
                                           'params': params, 'step': '', 'real': r, 'behavior': behavior})
                if bonded:
                    self.ties.append({'name': 'REAL %d (%s)' % (r, behavior), 'slave': sname, 'master': mname,
                                      'params': params, 'tol': None})
            elif ce and not te:
                pilot = False
                self.notes.append(('error', '接触对 (实常数 %d) 有 %d 个接触单元, 却没有目标单元' % (r, len(ce))))
            elif te and not ce:
                # a lone pilot node (rigid target / remote point) needs no contact elements of its own
                if all(self.fams[codes[e]][0] == 'point' for e in te):
                    for e in te:
                        g = self.mesh.e_nodes[self.mesh.e_off[e]]
                        self.ref_nodes[g] = 'PILOT'
                else:
                    self.notes.append(('error', '接触对 (实常数 %d) 有 %d 个目标单元, 却没有接触单元' % (r, len(te))))

    # -- CE / CP / MPC184 ---------------------------------------------------
    def _build_constraints(self):
        get = self.root.node_map.get
        con_arr = self._con_nodes
        for ce in self._ce:
            nodes = array('i')
            rows = []
            for lab, dof, c in ce['terms']:
                g = get(lab)
                if g is None:
                    self.notes.append(('error', '约束方程 %d 引用了不存在的节点 %d' % (ce['id'], lab)))
                    continue
                nodes.append(g)
                if len(rows) < 4:
                    rows.append([str(lab), dof, _g(c)])
            con = self._new_con('CE', {}, rows, n=len(ce['terms']), name='CE %d' % ce['id'], const=ce['const'])
            con['nrows'] = len(ce['terms'])
            con['_groups'] = [nodes]
            con_arr[con['id']] = nodes
        for sid in sorted(self._cp):
            cp = self._cp[sid]
            nodes = array('i', [g for g in (get(x) for x in cp['nodes']) if g is not None])
            rows = [[cp['label']] + [str(x) for x in cp['nodes'][:6]]]
            con = self._new_con('CP', {}, rows, n=len(nodes), name='CP %d (%s)' % (sid, cp['label']), label=cp['label'])
            con['nrows'] = len(cp['nodes'])
            con['_groups'] = [nodes]
            con_arr[con['id']] = nodes
        # MPC184 rigid links / beams / joints: one entry per element type
        at = self.e_attr
        m = self.mesh
        groups = {}
        for e in range(len(at['type'])):
            et = self.etypes.get(at['type'][e])
            if et and et['name'] == 'MPC184':
                groups.setdefault(at['type'][e], []).append(e)
        kinds = {0: '刚性杆', 1: '刚性梁', 3: '滑移', 6: '转动铰', 7: '万向铰', 8: '槽', 9: '点面', 10: '平动铰',
                 11: '圆柱铰', 12: '平面铰', 13: '焊接', 14: '定向', 15: '球铰', 16: '一般铰', 17: '螺旋', 18: '轴承'}
        for typ, els in groups.items():
            et = self.etypes[typ]
            k = kinds.get(et['keyopt'][0], 'KEYOPT(1)=%d' % et['keyopt'][0])
            nodes = array('i')
            grp = []
            for e in els:
                a = array('i', m.e_nodes[m.e_off[e]:m.e_off[e + 1]])
                grp.append(a)
                nodes.extend(a)
            con = self._new_con('MPC184', {}, [['TYPE %d' % typ, k, '%d 个单元' % len(els)]], n=len(els),
                                name='MPC184 %s' % k)
            con['_groups'] = grp
            con_arr[con['id']] = nodes

    # -- BCs and loads -------------------------------------------------------
    def _rot_nodes(self):
        """bytearray: 1 for nodes that carry rotational DOFs (some element there has them)"""
        r = getattr(self, '_rotflag', None)
        if r is None:
            r = bytearray(len(self.n_label))
            at = self.e_attr
            m = self.mesh
            rot_types = {t for t, et in self.etypes.items() if et['name'].startswith(_ROT_PREFIX) or
                         (et['name'] == 'MASS21' and et['keyopt'][2] in (0, 3)) or
                         (et['name'] == 'COMBIN14' and et['keyopt'][2] in (1,))}
            for e in range(len(at['type'])):
                if at['type'][e] in rot_types:
                    for g in m.e_nodes[m.e_off[e]:m.e_off[e + 1]]:
                        r[g] = 1
            self._rotflag = r
        return r

    def _target_name(self, ids, table):
        """a component whose members are exactly these, else a short description"""
        key = None
        for ent in table.values():
            if ent['internal'] or len(ent['ids']) != len(ids):
                continue
            if key is None:
                key = array('i', sorted(ids))
            if ent['ids'] == key:
                return ent['name']
        if table is self.nsets:
            labels = [self.n_label[g] for g in ids[:3]]
            if len(ids) == 1:
                return str(labels[0])
            return '%d 个节点 (%s%s)' % (len(ids), ', '.join(str(x) for x in labels), ' …' if len(ids) > 3 else '')
        labels = [self.e_label[e] for e in ids[:3]]
        return '%d 个单元 (%s%s)' % (len(ids), ', '.join(str(x) for x in labels), ' …' if len(ids) > 3 else '')

    def _build_bcs_loads(self):
        step = {'name': 'CDB', 'params': {}, 'proc': '', 'procParams': {}, 'procData': [], 'bcs': [], 'loads': [],
                'outputs': [], 'other': []}
        antype = 0
        for cmd, args in self.settings:
            if cmd[:4] == 'ANTY':
                a = args[0].strip().upper() if args else '0'
                try:
                    antype = int(float(a))
                except ValueError:
                    antype = ANTYPE_WORD.get(a[:4], 0)
        self.antype = antype
        step['proc'] = ANTYPE_NAME.get(antype, 'ANTYPE %d' % antype)
        defaults = {'ALPHAD': ['0.00000000'], 'BETAD': ['0.00000000'], 'DMPRAT': ['0.00000000'],
                    'DMPSTR': ['0.00000000'], 'KUSE': ['0'], 'NEQIT': ['0'], 'ERESX': ['DEFA']}
        for cmd, args in self.settings:
            a = [x for x in args]
            while a and a[-1] == '':
                a.pop()
            if cmd[:4] in ('ANTY',):
                continue
            if cmd in ('OUTRES', 'OUTPR'):
                step['outputs'].append({'kw': cmd, 'params': {}, 'vars': [x for x in a if x]})
                continue
            vals = _floats_loose(a)
            if cmd in defaults and all(v == 0 for v in vals) or cmd in ('CRPLIM', 'NCNV') or \
                    cmd == 'ERESX' and a[:1] == ['DEFA']:
                continue
            if cmd == 'NLGEOM':
                step['procParams']['NLGEOM'] = 'ON' if vals and vals[0] else 'OFF'
                continue
            if cmd in ('TIME', 'NSUBST', 'DELTIM', 'AUTOTS', 'KBC', 'NROPT', 'TIMINT', 'TRNOPT', 'MODOPT', 'MXPAND',
                       'HROPT', 'HARFRQ', 'BUCOPT', 'EQSLV', 'PSTRES', 'CNVTOL', 'LNSRCH', 'TINTP', 'SPOPT'):
                if cmd not in step['procParams'] or cmd == 'CNVTOL':
                    step['procParams'][cmd] = ','.join(_short(x) for x in a)
                continue
            step['procData'].append([cmd] + [_short(x) for x in a][:8])
        self._step = step
        self.steps.append(step)
        sname = step['name']
        rotn = self._rot_nodes()
        # ---- constraints (DBLOCK), grouped by what they hold
        per = {}
        for g, lab, v, v2, tab in self._dl:
            if lab in ('ROTX', 'ROTY', 'ROTZ') and not rotn[g]:
                continue                     # D,ALL also writes rotations on solid nodes: not a real constraint
            per.setdefault(g, {})[lab] = (round(v, 12), tab)
        self._bc0 = per                      # the constraint state of the CDB, for the load steps of the solve segment
        groups = {}
        for g, d in per.items():
            sig = tuple(sorted(d.items()))
            groups.setdefault(sig, []).append(g)
        if len(groups) > 40:                 # many different values: group by the labels alone
            regroup = {}
            for sig, ns in groups.items():
                regroup.setdefault(tuple(l for l, _ in sig), []).extend(ns)
            groups = {tuple((l, (None, '')) for l in k): v for k, v in regroup.items()}
        bc_n = 0
        for sig, ns in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            bc_n += 1
            ns = array('i', sorted(ns))
            dofs, mags, labels = [], [], []
            for lab, (val, tab) in sig:
                d = _STRUCT_LABELS.get(lab, lab)
                dofs.append(d)
                labels.append(lab)
                if tab:
                    mags.append([d, d, '%' + tab + '%'])
                elif val is not None and val != 0:
                    mags.append([d, d, val])
            nrot = sum(1 for g in ns if g in self.node_rot)
            ent = {'id': 'bc%d' % bc_n, 'target': self._target_name(ns, self.nsets), 'dofs': dofs, 'type': '',
                   'mag': [m for m in mags if not isinstance(m[2], str)], 'params': {}, 'step': sname, 'n': len(ns),
                   'labels': labels, 'command': 'D'}
            if any(isinstance(m[2], str) for m in mags):
                ent['tables'] = sorted({m[2] for m in mags if isinstance(m[2], str)})
            if nrot:
                ent['rotatedNodes'] = nrot
            struct = [l for l in labels if l in _STRUCT_LABELS]
            if struct and not ent['mag'] and not ent.get('tables') and \
                    {'UX', 'UY', 'UZ'} <= set(struct) | ({'UZ'} if self.dimension() != '3d' else set()):
                ent['kind'] = 'fixed'
            step['bcs'].append(ent)
            self.bc_by_id[ent['id']] = (ent, ns)
        self._dl = None
        ld_n = [0]

        def new_load(kw, rows, nrows, **extra):
            ld_n[0] += 1
            ent = {'id': 'ld%d' % ld_n[0], 'kw': kw, 'params': {}, 'rows': rows, 'nrows': nrows, 'step': sname}
            ent.update(extra)
            step['loads'].append(ent)
            self.load_by_id[ent['id']] = ent
            return ent
        # ---- nodal loads (FBLOCK)
        fg = {}
        for g, lab, v, v2, tab in self._fl:
            fg.setdefault((lab, tab or round(v, 12)), []).append(g)
        if len(fg) > 40:
            regroup = {}
            for (lab, v), ns in fg.items():
                regroup.setdefault(lab, []).extend(ns)
            fg = {(lab, None): ns for lab, ns in regroup.items()}
        vals = {}
        for g, lab, v, v2, tab in self._fl:
            vals[(g, lab)] = (v, tab)
        self._fvals = vals
        for (lab, v), ns in sorted(fg.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
            ns = array('i', sorted(ns))
            tgt = self._target_name(ns, self.nsets)
            if isinstance(v, str):
                shown, total = '%' + v + '%', None
            elif v is None:
                shown, total = '(各节点不同)', sum(vals[(g, lab)][0] for g in ns)
            else:
                shown, total = _g(v), v * len(ns)
            ent = new_load('F', [[tgt, lab, shown]], len(ns), label=lab, n=len(ns), nodes=ns, command='F')
            if total is not None:
                ent['total'] = total
                if v is not None and len(ns) > 1:
                    ent['perNode'] = v
            nrot = sum(1 for g in ns if g in self.node_rot)
            if nrot:
                ent['rotatedNodes'] = nrot
            if isinstance(v, str):
                ent['table'] = v
            pret = self._pretension_nodes()
            if pret and all(g in pret for g in ns):
                ent['pretension'] = True       # SLOAD: the bolt load on the pretension node, internal to the model
        self._fl = None
        # ---- surface loads (SFEBLOCK) -> one internal surface per label and value
        sg = {}
        eget = self.root.elem_map.get
        skipped = 0
        for lab_e, face, lab, kval, vals_, tab, rl in self._sfe:
            if kval != 1:
                continue                     # KVAL = 2: imaginary part
            e = eget(lab_e)
            if e is None:
                skipped += 1
                continue
            code = self._face_code(e, face)
            if code is None:
                skipped += 1
                continue
            # the block always holds four values; only the ones of the face's corner nodes count
            # (an edge of a 2-D element has two: -100, -100, 0, 0 is a pressure of 100, not 50)
            vs = [v for v in vals_ if not isinstance(v, str)][:self._face_corner_count(e, face)]
            key = (lab, tab) if tab else (lab, round(sum(vs) / len(vs), 12) if vs else 0.0)
            sg.setdefault(key, []).append((e, code, face, vs))
        if skipped:
            self.notes.append(('info', '%d 个面载荷没有画出 (单元不存在或面号对不上)' % skipped))
        self._sf0 = {(e, code): (lab, v) for (lab, v), faces in sg.items() for e, code, _, _ in faces}
        k = 0
        for (lab, v), faces in sorted(sg.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
            k += 1
            nm = '_SFE_%s_%d' % (lab, k)
            fe, ff = array('i'), array('B')
            for e, code, face, vs in faces:
                fe.append(e)
                ff.append(code)
            self.surfaces[nm.upper()] = {'name': nm, 'type': 'ELEMENT', 'fe': fe, 'ff': ff, 'nodes': array('i'),
                                         'analytic': None, 'internal': True, 'inst': 0}
            shown = ('%' + v + '%') if isinstance(v, str) else _g(v)
            ent = new_load('SFE', [[nm, lab, shown]], len(faces), label=lab, n=len(faces), surface=nm,
                           command='SF')
            if isinstance(v, str):
                ent['table'] = v
            else:
                ent['value'] = v
            ent['_faces'] = faces
        self._sfe = None
        # ---- body loads
        for lab, lst in sorted(self._bfn.items()):
            if not lst:
                continue
            vs = [v for _, v in lst if not isinstance(v, str)]
            shown = _g(vs[0]) if vs and min(vs) == max(vs) else ('%s–%s' % (_g(min(vs)), _g(max(vs))) if vs else '')
            ns = array('i', sorted({g for g, _ in lst}))
            new_load('BF', [[self._target_name(ns, self.nsets), lab, shown]], len(lst), label=lab, n=len(ns),
                     nodes=ns, command='BF')
        for lab, lst in sorted(self._bfe.items()):
            if not lst:
                continue
            els = array('i', sorted({e for e in (eget(x) for x, _, _ in lst) if e is not None}))
            vs = [v for _, _, v in lst if not isinstance(v, str)]
            shown = _g(vs[0]) if vs and min(vs) == max(vs) else ('%s–%s' % (_g(min(vs)), _g(max(vs))) if vs else '')
            tgt = self._target_name(els, self.elsets) if len(els) else ''
            new_load('BFE', [[tgt, lab, shown]], len(lst), label=lab, n=len(els), elements=els, command='BFE')
        for (args, vals_) in self._sfbeam:
            pass
        if self._sfbeam:
            new_load('SFBEAM', [[a[0] for a, _ in self._sfbeam[:1]] + ['PRES']], len(self._sfbeam),
                     n=len(self._sfbeam), command='SFBEAM')
        # ---- inertia
        acel = self.inertia.get('ACEL')
        if acel and any(acel):
            new_load('ACEL', [['', 'ACEL'] + [_g(x) for x in acel]], 1, vector=acel, command='ACEL')
        for key in sorted(self.inertia):                   # OMEGA, DOMEGA, CGOMEGA, ..., CMOMEGA <component>
            v = self.inertia[key]
            if key != 'ACEL' and v and any(v):
                cmd, _, comp = key.partition(' ')
                new_load(cmd, [[comp, cmd] + [_g(x) for x in v]], 1, vector=v, command=cmd, component=comp)
        # ---- initial conditions
        ic = {}
        for g, lab, v1, v2 in self._ics:
            ic.setdefault(lab, []).append((g, v1, v2))
        for lab, lst in sorted(ic.items()):
            self.init_conds.append({'type': lab, 'n': len(lst),
                                    'rows': [[str(self.n_label[g]), lab, _g(v1)] for g, v1, _ in lst[:4]]})
        self._ics = None
        if self.tref:
            step['procData'].append(['TREF', _g(self.tref)])
        if self.bfunif and self.bfunif[1] and self.bfunif[1].upper() != '_TINY':
            step['procData'].append(['BFUNIF'] + list(self.bfunif))

    def _face_code(self, e, face):
        """MAPDL face number of element e -> face code of the preview family, None when there is none"""
        m = self.mesh
        code = m.e_code[e]
        fam = self.fams[code][0]
        if fam is None:
            return None
        cls = self._cls_of(e)
        orig = self._econn[self._eoff[e]:self._eoff[e + 1]]
        if cls == 'S8':
            loc = _FACES_S8.get(face)
        elif cls == 'S4':
            loc = _FACES_S4.get(face)
        elif cls == 'SH':
            if face == 1:
                return 8                      # bottom face: pressure along +N (like SNEG)
            if face == 2:
                return 7                      # top face: pressure along -N (like SPOS)
            loc = _EDGES_SH.get(face)
        elif cls == 'P4':
            et = self.etypes.get(self.e_attr['type'][e])
            true_tri = len(orig) in (3, 6) or (et and et['ename'] == 183 and et['keyopt'][0] == 1)
            loc = (_EDGES_P3 if true_tri else _EDGES_P4).get(face)
        elif cls == 'P3':
            loc = _EDGES_P3.get(face)
        elif cls in ('CS',):
            return 7 if fam in ('quad', 'tri') else None
        elif cls in ('CL', 'L2', 'L3'):
            return 7 if fam == 'line' else None
        else:
            return None
        if loc is None or max(loc) >= len(orig):
            return None
        want = {orig[i] for i in loc}
        if -1 in want or len(want) < (2 if len(loc) == 2 else 3):
            return None
        o = m.e_off[e]
        n = self.fams[code][1]
        corners = m.e_nodes[o:o + n]
        for c, f in FACE_TAB.get(fam, {}).items():
            if fam in ('quad', 'tri') and c < 9:
                continue
            if {corners[i] for i in f} == want and len(f) == len(want):
                return c
        return None

    def _cls_of(self, e):
        et = self.etypes.get(self.e_attr['type'][e])
        return et['cls'] if et else '--'

    def _face_corner_count(self, e, face):
        """how many of the values in an SFEBLOCK row belong to this face (corner nodes of the face)"""
        cls = self._cls_of(e)
        fam = self.fams[self.mesh.e_code[e]][0]
        if cls == 'S8':
            return 4
        if cls == 'S4':
            return 3
        if cls == 'SH':
            return (4 if fam == 'quad' else 3) if face in (1, 2) else 2
        if cls in ('P4', 'P3', 'L2', 'L3', 'CL'):
            return 2
        if cls == 'CS':
            return 4 if fam == 'quad' else 3 if fam == 'tri' else 2
        return 4

    def _pretension_nodes(self):
        """the pretension node (K) of every PRETS179 element: a force there is the bolt load (SLOAD)"""
        out = getattr(self, '_pret', None)
        if out is None:
            out = set()
            for e in range(len(self.e_label)):
                et = self.etypes.get(self.e_attr['type'][e])
                if et and et['name'] == 'PRETS179':
                    orig = self._econn[self._eoff[e]:self._eoff[e + 1]]
                    if len(orig) >= 3 and orig[2] >= 0:
                        out.add(orig[2])
            self._pret = out
        return out

    def _build_counts(self):
        counts = {}
        unknown = {}
        codes = self.mesh.e_code
        for e in range(len(codes)):
            et = self.etypes.get(self.e_attr['type'][e])
            nm = et['name'] if et else '?'
            counts[nm] = counts.get(nm, 0) + 1
            if self.fams[codes[e]][0] is None:
                unknown[nm] = unknown.get(nm, 0) + 1
        self.type_counts = counts
        self.unknown_types = unknown

    def _finalize_flat(self):
        """what inp_model.Model._finalize does that applies here"""
        self._final = True
        self.n_dead = 0                       # killed elements stay in the model (EKILL), see self.dead

    # ------------------------------------------------------------ the solve segment (plan 4.2)
    def _build_segment(self, solve_path):
        """read <job>.solve.dat: whitelist check (solve_segment.parse) and one analysis step per SOLVE, listed
        after the load state of the CDB.  A step lists what its commands change; symbols(k) draws the state
        after them (_seg_state)."""
        if solve_path is False:
            return
        sp = solve_path or self.solve_file
        if not os.path.isfile(sp):
            return
        self.solve_file = sp
        with open(sp, 'rb') as f:
            text = f.read().decode('latin-1')
        pret = [sid for sid, s in self.secs.items() if (s.get('type') or '').upper().startswith('PRET')]
        seg = solve_segment.parse(text, self.cm_names['NODE'], self.cm_names['ELEM'],
                                  list(self.params) + list(self.tables), pret)
        self.seg = seg
        antype = self.antype
        nlgeom = self._step['procParams'].get('NLGEOM') == 'ON'
        t_cdb = None
        for cmd, args in self.settings:
            if cmd == 'TIME' and args and _floats_loose(args[:1]) and _floats_loose(args[:1])[0] > 0:
                t_cdb = _floats_loose(args[:1])[0]
        t_end, first_in_analysis = 0.0, True
        self.seg_antypes = set()
        nbc = nld = 0
        for k, st in enumerate(seg['steps'], 1):
            name = '载荷步 %d' % k
            step = {'name': name, 'params': {}, 'proc': '', 'procParams': {}, 'procData': [], 'bcs': [], 'loads': [],
                    'outputs': [], 'other': [], 'lines': [st['first'], st['solve']], 'segment': True}
            t_given = None
            for c in st['commands']:
                if not c['ok']:
                    continue                 # refused lines are reported as violations, not shown as settings
                n, a, ln = c['name'], c['args'], c['line']
                tgt = a[0].upper() if a else ''
                if n == 'ANTYPE':
                    antype = solve_segment.antype_of(a[0] if a else '', antype)
                    if not (len(a) > 1 and a[1].upper().startswith('REST')):
                        t_end, first_in_analysis = 0.0, True
                    continue
                if n == 'NLGEOM':
                    nlgeom = bool(a) and a[0].upper() in ('ON', '1', 'YES')
                    step['procParams']['NLGEOM'] = 'ON' if nlgeom else 'OFF'
                    continue
                if n == 'TIME':
                    t_given = solve_segment.number(a[0]) if a else None
                if n in ('TIME', 'NSUBST', 'DELTIM', 'AUTOTS', 'KBC', 'NROPT', 'TIMINT', 'TRNOPT', 'MODOPT', 'MXPAND',
                         'HROPT', 'HARFRQ', 'BUCOPT', 'EQSLV', 'PSTRES', 'CNVTOL', 'LNSRCH', 'TINTP', 'NEQIT'):
                    step['procParams'][n] = ','.join(a)
                    continue
                if n in ('OUTRES', 'OUTPR'):
                    step['outputs'].append({'kw': n, 'params': {}, 'vars': [x for x in a if x]})
                    continue
                if n in ('ALPHAD', 'BETAD', 'DMPRAT', 'DMPSTR', 'TREF', 'RESCONTROL'):
                    step['procData'].append([n] + a)
                    continue
                if n in ('/SOLU', 'FINISH', 'ALLSEL', 'SOLVE'):
                    continue
                if n in ('D', 'DDELE'):
                    nbc += 1
                    ns = self._seg_nodes(tgt)
                    labs = [x.upper() for x in ([a[1]] if len(a) > 1 and a[1] else ['ALL']) +
                            ([x for x in a[6:11] if x] if n == 'D' else [])]
                    val = solve_segment.number(a[2]) if n == 'D' and len(a) > 2 and a[2] else 0.0
                    dofs = [_STRUCT_LABELS.get(l, l) for l in labs]
                    ent = {'id': 'sbc%d' % nbc, 'target': tgt, 'dofs': dofs, 'type': '' if n == 'D' else 'DDELE 删除约束',
                           'mag': [[d, d, val] for d in dofs] if n == 'D' and val else [], 'params': {}, 'step': name,
                           'n': len(ns), 'labels': labs, 'command': n, 'line': ln}
                    if n == 'D' and not val and ({'UX', 'UY', 'UZ'} <= set(labs) or 'ALL' in labs):
                        ent['kind'] = 'fixed'
                    step['bcs'].append(ent)
                    self.bc_by_id[ent['id']] = (ent, ns)
                    continue
                nld += 1
                ent = {'id': 'sld%d' % nld, 'kw': n, 'params': {}, 'rows': [a[:]], 'nrows': 1, 'step': name,
                       'command': n, 'line': ln}
                if n in ('F', 'FDELE', 'BF', 'BFDELE', 'SF', 'SFDELE'):
                    ns = self._seg_nodes(tgt)
                    lab = a[1].upper() if len(a) > 1 and a[1] else 'ALL'
                    ent.update(nodes=ns, n=len(ns), label=lab, target=tgt)
                    if n in ('F', 'BF', 'SF'):
                        v = solve_segment.number(a[2]) if len(a) > 2 and a[2] else 0.0
                        ent['value'] = v
                        if n == 'F':
                            ent['perNode'] = v
                            ent['total'] = v * len(ns)
                            ent['rows'] = [[tgt, lab, _g(v)] + (['每节点 × %d 节点 = %s' % (len(ns), _g(v * len(ns)))]
                                                                if len(ns) > 1 else [])]
                    if n in ('SF', 'SFDELE'):
                        faces = self._faces_on_nodes(ns)
                        nm = '_SF%d_%s' % (nld, tgt)
                        fe, ff = array('i'), array('B')
                        for e, code in faces:
                            fe.append(e)
                            ff.append(code)
                        self.surfaces[nm.upper()] = {'name': nm, 'type': 'ELEMENT', 'fe': fe, 'ff': ff,
                                                     'nodes': array('i'), 'analytic': None, 'internal': True, 'inst': 0}
                        ent.update(surface=nm, faces=len(faces))
                        if n == 'SF':
                            ent['rows'] = [[tgt, lab, _g(ent['value']), '%d 个单元面' % len(faces)]]
                elif n in ('EKILL', 'EALIVE'):
                    els = self._seg_elems(tgt)
                    ent.update(elements=els, n=len(els), target=tgt)
                    ent['rows'] = [[tgt, '%d 个单元' % len(els)]]
                elif n == 'ACEL':
                    vec = [solve_segment.number(x) or 0.0 if x else 0.0 for x in (a + ['', '', ''])[:3]]
                    ent['vector'] = vec
                    ent['rows'] = [['', 'ACEL'] + [_g(x) for x in vec]]
                elif n == 'TUNIF':
                    ent['rows'] = [['', 'TEMP', a[0] if a else '0']]
                elif n == 'SLOAD':
                    sid = int(solve_segment.number(a[0]))
                    pts = {t for t, et in self.etypes.items() if et['name'] == 'PRETS179'}
                    els = array('i', [e for e in range(len(self.e_label))
                                      if self.e_attr['secn'][e] == sid and self.e_attr['type'][e] in pts])
                    ent.update(elements=els, n=len(els), section=sid)
                step['loads'].append(ent)
                self.load_by_id[ent['id']] = ent
            if t_given:
                t_end = t_given
            elif first_in_analysis and t_cdb:
                t_end = t_cdb
            else:
                t_end = t_end + 1.0
            first_in_analysis = False
            step['proc'] = ANTYPE_NAME.get(antype, 'ANTYPE %d' % antype)
            if antype in (0, 4):
                step['params']['TIME'] = _g(t_end)
            step['antype'] = antype
            self.seg_antypes.add(antype)
            self.seg_steps.append(step)
            self.steps.append(step)
        self._seg_nlgeom = nlgeom

    def _seg_nodes(self, name):
        if name == 'ALL':
            return array('i', range(len(self.n_label)))
        s = self.nsets.get(name)
        return s['ids'] if s else array('i')

    def _seg_elems(self, name):
        if name == 'ALL':
            return array('i', range(len(self.e_label)))
        s = self.elsets.get(name)
        return s['ids'] if s else array('i')

    def _faces_on_nodes(self, ids):
        """element faces whose corner nodes all belong to the node set: what SF / SFDELE on a node component
        act on (MAPDL 2025 R1: on a shell only face 1, on a solid the faces of its outer side, on a 2-D
        element its edges) -> [(element, face code)]"""
        mark = bytearray(len(self.n_label))
        for g in ids:
            mark[g] = 1
        m = self.mesh
        out = []
        for e in range(len(m.e_code)):
            code = m.e_code[e]
            if code in self._overlay:
                continue
            fam, n, _ = self.fams[code]
            if fam is None:
                continue
            o = m.e_off[e]
            corners = m.e_nodes[o:o + n]
            if fam in SOLID_FACES:
                for c, f in FACE_TAB[fam].items():
                    if all(mark[corners[i]] for i in f):
                        out.append((e, c))
            elif fam in SURFACE_FAMILIES:
                if all(mark[g] for g in corners):
                    out.append((e, 8))                  # face 1 of a shell
            elif fam in PLANE_FAMILIES:
                for c, f in FACE_TAB[fam].items():
                    if c != FID_WHOLE and len(f) == 2 and mark[corners[f[0]]] and mark[corners[f[1]]]:
                        out.append((e, c))
        return out

    def _seg_state(self, k):
        """constraints, forces, surface loads, body loads, acceleration and killed elements in effect during
        load step k of the solve segment (k = 0: the CDB) - MAPDL keeps every load until it is deleted or
        overwritten (dsh-ansys step 3: SF on a node component overwrites the face pressures read from the CDB,
        SFDELE removes them)"""
        cache = self.__dict__.setdefault('_states', {})
        if k in cache:
            return cache[k]
        if k == 0:
            st = {'bc': {g: {l: (tab or v) for l, (v, tab) in d.items()} for g, d in self._bc0.items()},
                  'f': {key: (tab or v) for key, (v, tab) in self._fvals.items()},
                  'sf': dict(self._sf0),
                  'bf': {(g, lab): v for lab, lst in self._bfn.items() for g, v in lst},
                  'acel': list(self.inertia.get('ACEL') or [0.0, 0.0, 0.0]),
                  'dead': {e for e in range(len(self.e_label)) if self.e_attr['dead'][e]}}
        else:
            prev = self._seg_state(k - 1)
            st = {'bc': {g: dict(d) for g, d in prev['bc'].items()}, 'f': dict(prev['f']), 'sf': dict(prev['sf']),
                  'bf': dict(prev['bf']), 'acel': list(prev['acel']), 'dead': set(prev['dead'])}
            step = self.seg_steps[k - 1]
            rotn = self._rot_nodes()
            dim = self.dimension()
            trans = ['UX', 'UY', 'UZ'] if dim == '3d' else ['UX', 'UY']
            rots = ['ROTX', 'ROTY', 'ROTZ'] if dim == '3d' else ['ROTZ']
            for ent in sorted(step['bcs'] + step['loads'], key=lambda x: x['line']):
                cmd = ent['command']
                if cmd in ('D', 'DDELE'):
                    ns = self.bc_by_id[ent['id']][1]
                    val = ent['mag'][0][2] if ent['mag'] else 0.0
                    for g in ns:
                        labs = []
                        for l in ent['labels']:
                            labs.extend((trans + (rots if rotn[g] else [])) if l == 'ALL' else [l])
                        if cmd == 'D':
                            d = st['bc'].setdefault(g, {})
                            for l in labs:
                                d[l] = val
                        elif g in st['bc']:
                            if 'ALL' in ent['labels']:
                                del st['bc'][g]
                            else:
                                for l in labs:
                                    st['bc'][g].pop(l, None)
                elif cmd in ('F', 'FDELE', 'BF', 'BFDELE'):
                    tab = st['f'] if cmd in ('F', 'FDELE') else st['bf']
                    lab = ent['label']
                    if cmd in ('F', 'BF'):
                        for g in ent['nodes']:
                            tab[(g, lab)] = ent['value']
                    else:
                        ns = set(ent['nodes'])
                        for key in [x for x in tab if x[0] in ns and (lab == 'ALL' or x[1] == lab)]:
                            del tab[key]
                elif cmd in ('SF', 'SFDELE'):
                    s = self.surfaces[ent['surface'].upper()]
                    for e, code in zip(s['fe'], s['ff']):
                        if cmd == 'SF':
                            st['sf'][(e, code)] = (ent['label'], ent['value'])
                        elif ent['label'] == 'ALL' or st['sf'].get((e, code), ('',))[0] == ent['label']:
                            st['sf'].pop((e, code), None)
                elif cmd == 'ACEL':
                    st['acel'] = list(ent['vector'])
                elif cmd == 'EKILL':
                    st['dead'].update(ent['elements'])
                elif cmd == 'EALIVE':
                    st['dead'].difference_update(ent['elements'])
        cache[k] = st
        return st

    def _tags(self, st):
        """the objects of a load state the way ANSYS Mechanical labels them A, B, C ... in its viewport: one tag
        per group of nodes / faces / elements with the same kind and value, anchored on the node of the group
        nearest to its centroid -> [{'letter', 'kind', 'name', 'target', 'value', 'n', 'pos'}]"""
        coords = self.coords
        rotn = self._rot_nodes()
        out = []

        def anchor(ns):
            ns = list(ns)
            if not ns:
                return None
            step = max(1, len(ns) // 2000)
            pts = [coords[g] for g in ns[::step]]
            c = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
            best = min(pts, key=lambda p: (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2)
            return [best[0], best[1], best[2]]

        def target(ids, table):
            return self._target_name(array('i', sorted(ids)), table)
        dim = self.dimension()
        need = {'UX', 'UY', 'UZ'} if dim == '3d' else {'UX', 'UY'}
        groups = {}
        for g, d in st['bc'].items():
            labs = tuple(sorted(l for l in d if l in _STRUCT_LABELS and (rotn[g] or not l.startswith('ROT'))))
            if labs:
                vals = tuple(d[l] if isinstance(d[l], str) else round(d[l], 12) for l in labs)
                groups.setdefault((labs, vals), []).append(g)
        if len(groups) > 12:
            regroup = {}
            for (labs, vals), ns in groups.items():
                regroup.setdefault((labs, tuple('*' for _ in labs)), []).extend(ns)
            groups = regroup
        for (labs, vals), ns in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            moved = [(l, v) for l, v in zip(labs, vals) if v != 0]
            if not moved and need <= set(labs):
                kind, name = 'fixed', '固定支撑'
            elif moved:
                kind, name = 'disp', '位移'
            else:
                kind, name = 'support', '约束'
            out.append({'kind': kind, 'name': name, 'target': target(ns, self.nsets), 'n': len(ns),
                        'value': ', '.join('%s=%s' % (l, v if isinstance(v, str) else _g(v)) for l, v in zip(labs, vals)),
                        'pos': anchor(ns)})
        fg = {}
        for (g, lab), v in st['f'].items():
            if isinstance(v, str) or v:
                fg.setdefault((lab, v if isinstance(v, str) else round(v, 12)), []).append(g)
        for (lab, v), ns in sorted(fg.items(), key=lambda kv: (kv[0][0], -len(kv[1])))[:12]:
            kind = 'moment' if lab.startswith('M') else 'force'
            shown = ('%' + v + '%') if isinstance(v, str) else _g(v)
            if not isinstance(v, str) and len(ns) > 1:
                shown += ' 每节点 × %d = %s' % (len(ns), _g(v * len(ns)))
            out.append({'kind': kind, 'name': '力矩' if kind == 'moment' else '力', 'target': target(ns, self.nsets),
                        'n': len(ns), 'value': '%s %s' % (lab, shown), 'pos': anchor(ns)})
        pg = {}
        for (e, code), (lab, v) in st['sf'].items():
            pg.setdefault((lab, v if isinstance(v, str) else round(v, 12)), []).append((e, code))
        for (lab, v), faces in sorted(pg.items(), key=lambda kv: -len(kv[1]))[:12]:
            ns = set()
            for e, code in faces:
                f = self.face_nodes(e, code)
                if f:
                    ns.update(f)
            out.append({'kind': 'pressure' if lab == 'PRES' else 'surface', 'name': '压力' if lab == 'PRES' else '面载荷 ' + lab,
                        'target': target(ns, self.nsets) if ns else '', 'n': len(faces),
                        'value': ('%' + v + '%') if isinstance(v, str) else _g(v), 'pos': anchor(ns)})
        bg = {}
        for (g, lab), v in st['bf'].items():
            bg.setdefault((lab, v if isinstance(v, str) else round(v, 12)), []).append(g)
        for (lab, v), ns in sorted(bg.items(), key=lambda kv: -len(kv[1]))[:8]:
            out.append({'kind': 'temp' if lab == 'TEMP' else 'body', 'name': '温度' if lab == 'TEMP' else '体载荷 ' + lab,
                        'target': target(ns, self.nsets), 'n': len(ns),
                        'value': ('%' + v + '%') if isinstance(v, str) else _g(v), 'pos': anchor(ns)})
        eget = self.root.elem_map.get
        for lab, lst in sorted(self._bfe.items()):          # element body loads (BFE): the solve segment cannot change them
            vg = {}
            for x, _, v in lst:
                e = eget(x)
                if e is not None:
                    vg.setdefault(v if isinstance(v, str) else round(v, 12), set()).add(e)
            for v, els in sorted(vg.items(), key=lambda kv: -len(kv[1]))[:4]:
                ns = set()
                for e in sorted(els)[:5000]:
                    ns.update(g for g in self._econn[self._eoff[e]:self._eoff[e + 1]] if g >= 0)
                out.append({'kind': 'temp' if lab == 'TEMP' else 'body', 'name': ('温度' if lab == 'TEMP' else '体载荷 ' + lab) + ' (单元)',
                            'target': target(els, self.elsets), 'n': len(els),
                            'value': ('%' + v + '%') if isinstance(v, str) else _g(v), 'pos': anchor(ns)})
        a = st['acel']
        if any(a):
            out.append({'kind': 'accel', 'name': '加速度', 'target': '', 'n': 0,
                        'value': '(%s)' % ', '.join(_g(x) for x in a), 'pos': None})
        if st['dead']:
            dead = sorted(st['dead'])
            ns = set()
            for e in dead[:5000]:
                ns.update(g for g in self._econn[self._eoff[e]:self._eoff[e + 1]] if g >= 0)
            out.append({'kind': 'dead', 'name': '杀死的单元', 'target': target(dead, self.elsets), 'n': len(dead),
                        'value': '', 'pos': anchor(ns)})
        for i, t in enumerate(out):
            t['letter'] = chr(65 + i) if i < 26 else 'Z%d' % (i - 25)
        return out

    def symbols(self, step_index, cap=400):
        """BC / load symbols; for a load step of the solve segment, of the state in effect during that step.
        'tags' lists the objects of that state for the ANSYS look (_tags)."""
        if step_index < 1 or not self.seg_steps or step_index > len(self.seg_steps):
            out = inp_model.Model.symbols(self, step_index, cap)
            if step_index == 0:
                out['tags'] = self._tags(self._seg_state(0))
            return out
        st = self._seg_state(step_index)
        out = {'bc': [], 'cload': [], 'cmom': [], 'press': [], 'grav': []}
        rotn = self._rot_nodes()
        groups = {}
        for g, d in st['bc'].items():
            labs = tuple(sorted(l for l in d if l in _STRUCT_LABELS and (rotn[g] or not l.startswith('ROT'))))
            if labs:
                moving = any(not isinstance(d[l], str) and d[l] for l in labs) or any(isinstance(d[l], str) for l in labs)
                groups.setdefault((labs, moving), []).append(g)
        for (labs, moving), ns in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            out['bc'].append({'dofs': [_STRUCT_LABELS[l] for l in labs], 'pts': self._xyz(ns, cap).tolist(), 'n': len(ns),
                              'moving': moving})
        coords = self.coords
        items = [(g, lab, v) for (g, lab), v in st['f'].items() if lab in _FORCE_DIR and (isinstance(v, str) or v)]
        stride = max(1, len(items) // cap)
        for g, lab, v in items[::stride]:
            d = [0.0, 0.0, 0.0]
            d[_FORCE_DIR[lab]] = 1.0 if isinstance(v, str) or v >= 0 else -1.0
            r = self.node_rot.get(g)
            if r:
                d = list(_rotate(r, d))
            key = 'cload' if lab.startswith('F') else 'cmom'
            out[key].extend(coords[g])
            out[key].extend(d)
        faces = [(e, code, v) for (e, code), (lab, v) in st['sf'].items() if lab == 'PRES']
        stride = max(1, len(faces) // cap)
        for e, code, v in faces[::stride]:
            f = self.face_nodes(e, code)
            if f is None or len(f) < 2:
                continue
            p = [coords[g] for g in f]
            c = [sum(q[i] for q in p) / len(p) for i in range(3)]
            if len(p) == 2:
                nrm = _unit((p[1][1] - p[0][1], p[0][0] - p[1][0], 0.0))
            else:
                nrm = _unit(_cross(_sub(p[1], p[0]), _sub(p[2], p[0])))
            if nrm is None:
                continue
            d = _mul(nrm, -1.0 if isinstance(v, str) or v >= 0 else 1.0)
            out['press'].extend((c[0], c[1], c[2], d[0], d[1], d[2]))
        a = st['acel']
        mag = math.sqrt(sum(x * x for x in a))
        if mag > 0:
            out['grav'].append({'dir': [-a[0] / mag, -a[1] / mag, -a[2] / mag], 'mag': mag, 'kind': 'GRAV', 'target': ''})
        out['tags'] = self._tags(st)
        return out

    # ------------------------------------------------------------ overrides of inp_model.Model
    def skin(self, elems=None):
        """as inp_model.Model.skin, but contact / target / surface-effect elements are left out of the drawn
        model (they lie on the faces of the solids); a picked group of elements still shows them"""
        if elems is None:
            ov = self._overlay
            codes = self.mesh.e_code
            if ov:
                elems = [e for e in range(len(codes)) if codes[e] not in ov]
        return inp_model.Model.skin(self, elems)

    def dimension(self):
        d = getattr(self, '_dim', None)
        if d is None:
            fams = {self.fams[c][0] for c in set(self.mesh.e_code)}
            three = fams & {'hex', 'tet', 'wedge', 'pyr', 'quad', 'tri'}
            plane = fams & set(PLANE_FAMILIES)
            axi = False
            for t, et in self.etypes.items():
                if et['ename'] in _ALWAYS_AXI or (et['ename'] in _AXI_KEYOPT3 and et['keyopt'][2] == 1):
                    axi = True
            two_d_only = not three and not plane and self.etypes and all(
                et['ename'] in _ALWAYS_AXI for et in self.etypes.values())
            if three and not plane:
                d = '3d'
            elif plane or two_d_only:
                d = 'axi' if axi else '2d'
            else:
                d = '3d'
            self._dim = d
        return d

    def node_name(self, g):
        return str(self.n_label[g])

    def _load_symbols(self, ent, sym, hl=None, cap=400):
        kw = ent['kw']
        coords = self.coords
        if kw in ('DDELE', 'FDELE', 'BFDELE') and hl is not None:
            ns = ent.get('nodes') or (self.bc_by_id.get(ent['id'], (None, ()))[1])
            hl['pts'].extend(self._xyz(ns, 30000))
            hl['n'] += len(ns)
            return
        if kw in ('EKILL', 'EALIVE', 'SLOAD') and hl is not None:
            self._hl_elems(hl, ent.get('elements', ()))
            return
        if kw in ('SF', 'SFDELE'):
            if kw == 'SF' and ent.get('label') == 'PRES':
                v = ent.get('value', 1.0)
                tmp = {'kw': 'DSLOAD', 'rows': [[ent['surface'], 'P', repr(v if v else 1.0)]]}
                inp_model.Model._load_symbols(self, tmp, sym, hl, cap)
            elif hl is not None:
                s = self.find_surface(ent['surface'])
                if s is not None:
                    hl['n'] += self._faces_xyz(s['fe'], s['ff'], hl['tris'], hl['segs'], hl['pts'])
            return
        if kw == 'F':
            lab = ent.get('label', '')
            ns = ent.get('nodes', ())
            if hl is not None:
                hl['pts'].extend(self._xyz(ns, 30000))
                hl['n'] += len(ns)
            k = _FORCE_DIR.get(lab)
            if k is None:
                return
            key = 'cload' if lab.startswith('F') else 'cmom'
            vals = self._fvals
            stride = max(1, len(ns) // cap)
            for g in ns[::stride]:
                v = ent['value'] if 'value' in ent else vals.get((g, lab), (0.0, ''))[0]
                if not v and not ent.get('table'):
                    continue
                d = [0.0, 0.0, 0.0]
                d[k] = 1.0 if (v >= 0 or ent.get('table')) else -1.0
                r = self.node_rot.get(g)
                if r:
                    d = list(_rotate(r, d))
                sym[key].extend(coords[g])
                sym[key].extend(d)
        elif kw == 'SFE':
            if ent.get('label') != 'PRES':
                if hl is not None:
                    s = self.find_surface(ent['surface'])
                    if s is not None:
                        hl['n'] += self._faces_xyz(s['fe'], s['ff'], hl['tris'], hl['segs'], hl['pts'])
                return
            v = ent.get('value', 1.0)
            tmp = {'kw': 'DSLOAD', 'rows': [[ent['surface'], 'P', repr(v if v else 1.0)]]}
            inp_model.Model._load_symbols(self, tmp, sym, hl, cap)
        elif kw == 'ACEL':
            a = ent.get('vector') or [0.0, 0.0, 0.0]
            mag = math.sqrt(sum(x * x for x in a))
            if mag > 0:
                sym['grav'].append({'dir': [-a[0] / mag, -a[1] / mag, -a[2] / mag], 'mag': mag, 'kind': 'GRAV',
                                    'target': ''})
        elif kw == 'BF' and hl is not None:
            ns = ent.get('nodes', ())
            hl['pts'].extend(self._xyz(ns, 30000))
            hl['n'] += len(ns)
        elif kw == 'BFE' and hl is not None:
            self._hl_elems(hl, ent.get('elements', ()))

    # ------------------------------------------------------------ mass and load resultants
    def mass(self):
        """-> {'total', 'complete', 'byType': {name: mass}, 'missing': {name: count}}"""
        res = getattr(self, '_mass', None)
        if res is not None:
            return res
        at = self.e_attr
        xyz = self.mesh.xyz
        total = 0.0
        by, missing = {}, {}
        dim = self.dimension()
        for e in range(len(at['type'])):
            et = self.etypes.get(at['type'][e])
            if et is None:
                continue
            try:
                m = self._elem_mass(e, et, xyz, dim)
            except (ValueError, ZeroDivisionError, IndexError):
                m = None
            if m is None:
                missing[et['name']] = missing.get(et['name'], 0) + 1
                continue
            if m:
                total += m
                by[et['name']] = by.get(et['name'], 0.0) + m
        res = self._mass = {'total': total, 'complete': not missing, 'byType': by, 'missing': missing}
        return res

    def _xyz_of(self, nodes, xyz):
        return [(xyz[3 * g], xyz[3 * g + 1], xyz[3 * g + 2]) for g in nodes]

    def _elem_mass(self, e, et, xyz, dim):
        """mass of one element; 0 for elements without mass, None when it cannot be worked out here"""
        cls, name = et['cls'], et['name']
        at = self.e_attr
        mat = at['mat'][e]
        real = self.reals.get(at['real'][e], [])
        orig = self._econn[self._eoff[e]:self._eoff[e + 1]]
        if cls in OVERLAY or cls == '--' or name.startswith(('MPC', 'COMBIN14', 'COMBI214', 'CONTA', 'TARGE',
                                                              'PRETS', 'FOLLW', 'MESH', 'COMBIN39', 'COMBIN37',
                                                              'TRANS', 'CIRCU', 'SOURC')):
            return 0.0
        if name == 'MASS21':
            ko3 = et['keyopt'][2]
            return real[0] if real else None
        if name == 'MASS71' or name == 'MASS166':
            return real[0] if real else None
        if name == 'COMBIN40':
            return real[2] if len(real) > 2 else 0.0
        if name == 'MATRIX27':
            return None
        rho = self._prop(mat, 'DENS')
        if cls in ('S8', 'S4'):
            if rho is None:
                return 0.0
            return rho * _solid_volume(cls, self._xyz_of(orig, xyz))
        if cls == 'SH':
            secn = at['secn'][e]
            sec = self.secs.get(secn)
            area = _surface_area(self._xyz_of(orig[:8] if len(orig) >= 8 else orig[:4], xyz))
            if sec is not None and sec['type'] == 'SHELL' and sec['layers']:
                m = 0.0
                for t, lm, _th, _np in sec['layers']:
                    r = self._prop(lm if lm else mat, 'DENS')
                    if r is None:
                        r = rho
                    if r is None:
                        return 0.0
                    m += r * t
                return m * area
            if real:
                t = sum(real[:4]) / max(1, len([x for x in real[:4] if x])) if real[0] else None
                if t and rho is not None:
                    return rho * t * area
            return None if rho else 0.0
        if cls in ('P4', 'P3'):
            if rho is None:
                return 0.0
            pts = self._xyz_of(orig[:8] if len(orig) >= 8 else orig[:4] if cls == 'P4' else orig[:6], xyz)
            ko3 = et['keyopt'][2]
            if et['ename'] in _ALWAYS_AXI or (et['ename'] in _AXI_KEYOPT3 and ko3 == 1):
                return rho * _plane_area(pts, axi=True)
            if ko3 == 3:
                thk = real[0] if real else None
                sec = self.secs.get(at['secn'][e])
                if sec is not None and sec['type'] in ('PLANE', 'AXIS') and sec['data']:
                    thk = sec['data'][0]
                if thk is None:
                    return None
                return rho * thk * _plane_area(pts)
            return rho * _plane_area(pts)
        if cls in ('L2', 'L3'):
            if name.startswith(('BEAM', 'PIPE', 'ELBOW', 'LINK180', 'CABLE')):
                sec = self.secs.get(at['secn'][e])
                area = self._sec_area(sec) if sec else (real[0] if real and name.startswith('LINK') else None)
                if area is None:
                    return None
                if rho is None:
                    return 0.0
                pts = self._xyz_of(orig[:3] if cls == 'L3' else orig[:2], xyz)
                return rho * area * _line_length(pts, cls == 'L3')
            if name.startswith('LINK') and real:
                if rho is None:
                    return 0.0
                return rho * real[0] * _line_length(self._xyz_of(orig[:2], xyz), False)
            return 0.0
        if cls == 'PT':
            return 0.0
        return None

    def step_time(self):
        """TIME of the load state in the CDB (1.0 when the file does not set it)"""
        t = None
        for cmd, args in self.settings:
            if cmd == 'TIME' and args:
                try:
                    t = float(args[0])
                except ValueError:
                    pass
        return t if t else 1.0

    def table_value(self, name, t):
        """value of a one-dimensional *DIM table whose primary variable is TIME at time t (linear, clamped);
        None for any other kind of table"""
        tab = self.tables.get(name.upper())
        if tab is None or tab['type'] != 'TABLE' or not tab['vars'] or tab['vars'][0].upper() != 'TIME':
            return None
        n = tab['dims'][0] if tab['dims'] else 0
        vals = tab['values']
        if n < 1 or len(vals) < 2 * (n + 1):
            return None
        axis = vals[1:n + 1]
        data = vals[n + 2:2 * n + 2]
        if t <= axis[0]:
            return data[0]
        for i in range(1, n):
            if t <= axis[i]:
                a, b = axis[i - 1], axis[i]
                return data[i - 1] + (data[i] - data[i - 1]) * ((t - a) / (b - a) if b > a else 0.0)
        return data[-1]

    def load_resultant(self):
        """total force of the nodal forces and the pressures (global axes, consistent units)
        -> {'force': [x, y, z], 'fromF': [...], 'fromPressure': [...], 'complete': bool, 'notes': [...]}"""
        res = getattr(self, '_resultant', None)
        if res is not None:
            return res
        ff = [0.0, 0.0, 0.0]
        gross = 0.0
        notes = []
        pret = self._pretension_nodes()
        for (g, lab), (v, tab) in self._fvals.items():
            k = _FORCE_DIR.get(lab)
            if k is None or not lab.startswith('F'):
                continue
            if g in pret:
                continue                       # SLOAD on a pretension node: the bolt load, internal to the model
            if tab:
                v = self.table_value(tab, self.step_time())
                if v is None:
                    notes.append('表格载荷 %s 未计入' % tab)
                    continue
            d = [0.0, 0.0, 0.0]
            d[k] = v
            r = self.node_rot.get(g)
            if r:
                d = list(_rotate(r, d))
            for i in range(3):
                ff[i] += d[i]
            gross += abs(v)
        fp = [0.0, 0.0, 0.0]
        dim = self.dimension()
        complete = True
        for ent in self._step['loads']:
            if ent['kw'] != 'SFE' or ent.get('label') != 'PRES':
                continue
            if ent.get('table'):
                notes.append('表格压力 %s 未计入' % ent['table'])
                complete = False
                continue
            for e, code, face, vs in ent['_faces']:
                try:
                    f = self._pressure_force(e, code, face, vs, dim)
                except (ValueError, ZeroDivisionError, IndexError):
                    f = None
                if f is None:
                    complete = False
                    continue
                for i in range(3):
                    fp[i] += f[i]
                gross += math.sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2])
        res = self._resultant = {'force': [ff[i] + fp[i] for i in range(3)], 'fromF': ff, 'fromPressure': fp,
                                 'gross': gross, 'complete': complete and not notes, 'notes': sorted(set(notes))}
        return res

    def _pressure_force(self, e, code, face, vs, dim):
        """force of a pressure on one element face (vector, global)"""
        m = self.mesh
        fam = self.fams[m.e_code[e]][0]
        cls = self._cls_of(e)
        xyz = m.xyz
        orig = self._econn[self._eoff[e]:self._eoff[e + 1]]
        p = sum(vs) / len(vs) if vs else 0.0
        if cls in ('S8', 'S4'):
            loc = (_FACES_S8 if cls == 'S8' else _FACES_S4)[face]
            pts = [tuple(xyz[3 * orig[i]:3 * orig[i] + 3]) for i in loc]
            av = _area_vector(pts)
            c = _centroid(self._xyz_of([g for g in orig[:8 if cls == 'S8' else 4] if g >= 0], xyz))
            fc = _centroid(pts)
            sign = 1.0 if _dot(av, _sub(fc, c)) >= 0 else -1.0
            pv = (list(vs) + [p] * 4)[:len(loc)] if vs else [0.0] * len(loc)
            # a quadratic element with midside nodes off the straight edges: integrate over the curved face
            mids = _HEX20_MID if cls == 'S8' else _TET10_MID
            nfull = 20 if cls == 'S8' else 10
            if len(orig) >= nfull and all(g >= 0 for g in orig[:nfull]):
                mid_pts = [tuple(xyz[3 * orig[mids[frozenset((loc[k], loc[(k + 1) % len(loc)]))]]:
                                     3 * orig[mids[frozenset((loc[k], loc[(k + 1) % len(loc)]))]] + 3])
                           for k in range(len(loc))]
                curved = any(max(abs(q[j] - (pts[k][j] + pts[(k + 1) % len(loc)][j]) / 2) for j in range(3)) >
                             1e-9 * max(1e-30, max(abs(x) for x in pts[k] + pts[(k + 1) % len(loc)]))
                             for k, q in enumerate(mid_pts))
                if curved:
                    f = _quad_face_load(pts, mid_pts, pv)
                    return _mul(f, -sign)              # pressure pushes into the element
            if vs and max(vs) != min(vs) and len(loc) in (3, 4):
                f = _quad_face_load(pts, None, pv)
                return _mul(f, -sign)
            return _mul(av, -p * sign)                 # pressure pushes into the element
        if cls == 'SH' and face in (1, 2):
            o = m.e_off[e]
            n = self.fams[m.e_code[e]][1]
            pts = [tuple(xyz[3 * g:3 * g + 3]) for g in m.e_nodes[o:o + n]]
            av = _area_vector(pts)
            return _mul(av, p if face == 1 else -p)
        if cls in ('P4', 'P3'):
            o = m.e_off[e]
            n = self.fams[m.e_code[e]][1]
            corners = m.e_nodes[o:o + n]
            f = FACE_TAB[fam].get(code)
            if f is None or len(f) != 2:
                return None
            a, b = [tuple(xyz[3 * corners[i]:3 * corners[i] + 3]) for i in f]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L == 0:
                return [0.0, 0.0, 0.0]
            out = ((b[1] - a[1]) / L, (a[0] - b[0]) / L, 0.0)   # right of a -> b: outward for these edge orders
            c = _centroid([tuple(xyz[3 * g:3 * g + 3]) for g in corners])
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.0)
            if (mid[0] - c[0]) * out[0] + (mid[1] - c[1]) * out[1] < 0:
                out = (-out[0], -out[1], 0.0)
            et = self.etypes.get(self.e_attr['type'][e])
            ko3 = et['keyopt'][2] if et else 0
            scale = L
            if dim == 'axi':
                scale = L * 2 * math.pi * mid[0]
            elif ko3 == 3:
                real = self.reals.get(self.e_attr['real'][e], [])
                scale = L * (real[0] if real else 1.0)
            return [-p * scale * out[0], -p * scale * out[1], 0.0]
        return None

    # ------------------------------------------------------------ checks
    def checks(self, mats=None):
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
        for ln, text in self.violations[:30]:
            add('error', 'CDB 第 %d 行: %s' % (ln, text))
        if len(self.violations) > 30:
            add('error', '另有 %d 处 CDB 内容不在白名单内或格式不对' % (len(self.violations) - 30))
        for level, text in self.notes[:40]:
            add(level, text)
        self._check_segment(add)
        for w in self.incomplete[:12]:
            add('warn', '预览不完整: ' + w)
        self._check_elements(add)
        self._check_mapdl_materials(add, mats)
        stray = self.unused_nodes()
        if len(stray):
            add('info', '有 %d 个节点没有被任何单元使用, 也不在约束方程/耦合里 (如节点 %s)' % (
                len(stray), ', '.join(self.node_name(g) for g in stray[:3])))
        if not self._step['bcs']:
            add('warn', '没有任何位移约束 (D)')
        if self.node_rot:
            add('info', '%d 个节点的坐标轴转过角度 (NROTAT 等): 这些节点上的 D、F 按节点坐标系的方向' % len(self.node_rot))
        if self.dead:
            add('info', '%d 个单元在 CDB 里是杀死状态 (EKILL), 求解时不参与计算, 除非求解段里 EALIVE' % self.dead)
        tabs = sorted({l['table'] for l in self._step['loads'] if l.get('table')} |
                      {t for b in self._step['bcs'] for t in b.get('tables', ())})
        if tabs:
            add('info', '有随时间变化的载荷或约束 (表 %s)' % ', '.join(tabs[:6]))
        if self._ist:
            add('info', 'CDB 带初始状态 (INISTATE): %s' % ', '.join(self._ist[:3]))
        for fn in (self._check_gaps, self._check_restraint):
            try:
                fn(add)
            except Exception as e:      # a check must never take the preview down
                add('info', '检查 %s 未能完成: %s: %s' % (fn.__name__.replace('_check_', ''), type(e).__name__, e))
        if not any(c['level'] in ('error', 'warn') for c in out):
            out.insert(0, {'level': 'ok', 'msg': '未发现问题' if not out else '未发现错误或警告'})
        self._checks = out
        self._check_notes = [m.get('cNote') for m in mats]
        return [dict(c) for c in out]

    def _check_segment(self, add):
        seg = self.seg
        fn = os.path.basename(self.solve_file)
        if seg is None:
            add('info', '还没有求解段 (%s): 求解段写好前不能批准求解' % fn)
            return
        for ln, text in seg['violations'][:30]:
            add('error', '求解段 %s 第 %d 行: %s' % (fn, ln, text))
        if len(seg['violations']) > 30:
            add('error', '求解段另有 %d 处不在白名单内或写法不对' % (len(seg['violations']) - 30))
        if not seg['steps']:
            add('warn', '求解段里没有 SOLVE, 什么也不会算')
        if seg['commands'] and seg['commands'][0]['name'] != '/SOLU':
            add('error', '求解段第一条命令应是 /SOLU (第 %d 行是 %s): 读入 CDB 后还在前处理里, 不进 /SOLU 就不能求解'
                % (seg['commands'][0]['line'], seg['commands'][0]['cmd']))
        if seg['after']:
            add('warn', '求解段最后一次 SOLVE 之后还有 %d 条命令 (第 %s 行), 它们不起作用' % (
                len(seg['after']), ', '.join(str(c['line']) for c in seg['after'][:5])))
        for ln in solve_segment.needs_nropt_full(seg, self.antype, self._step['procParams'].get('NLGEOM') == 'ON'):
            add('error', '求解段第 %d 行: 线性静力里用 EKILL/EALIVE, 要在第一次 SOLVE 之前写 NROPT,FULL, '
                         '否则 MAPDL 报错停算' % ln)
        if seg['steps']:
            add('info', '求解段 %s: %d 个载荷步 (%s)' % (fn, len(seg['steps']), ', '.join(
                sorted({ANTYPE_NAME.get(t, str(t)) for t in self.seg_antypes}))))

    def _check_elements(self, add):
        aux = {k: n for k, n in self.unknown_types.items() if k in ('PRETS179', 'MESH200')}
        real = {k: n for k, n in self.unknown_types.items() if k not in aux}
        if real:
            add('warn', '以下单元类型预览不画: ' + ', '.join('%s × %d' % kv for kv in sorted(real.items())))
        if aux:
            add('info', '辅助单元不画: ' + ', '.join('%s × %d' % kv for kv in sorted(aux.items())) +
                ' (PRETS179 是螺栓预紧单元, MESH200 只用于划网格)')
        nomat, nosec, noreal = {}, {}, {}
        for s in self.sections:
            et = self.etypes.get(s['etype'])
            if et is None:
                continue
            nm = et['name']
            if self._needs_mat(et) and (not s['mat'] or s['mat'] not in self._mat_name):
                nomat[(nm, s['mat'])] = nomat.get((nm, s['mat']), 0) + s['count']
            needs_sec = nm.startswith(('BEAM18', 'PIPE28', 'ELBOW', 'CABLE')) or nm == 'SOLSH190'
            sec_or_real = nm in ('LINK180', 'SHELL181', 'SHELL281', 'SHELL208', 'SHELL209')
            has_sec = s['secnum'] and s['secnum'] in self.secs
            has_real = s['real'] and s['real'] in self.reals
            if needs_sec and not has_sec or sec_or_real and not has_sec and not has_real:
                nosec[nm] = nosec.get(nm, 0) + s['count']
            needs_real = nm in ('MASS21', 'COMBIN14', 'COMBIN40', 'COMBIN39', 'MATRIX27', 'COMBI214', 'MASS71')
            if needs_real and not has_real:
                noreal[nm] = noreal.get(nm, 0) + s['count']
        for (nm, mt), n in sorted(nomat.items()):
            if mt:
                add('error', '%d 个 %s 单元用的材料 %d 没有定义' % (n, nm, mt))
            else:
                add('error', '%d 个 %s 单元没有材料 (MAT=0)' % (n, nm))
        for nm, n in sorted(nosec.items()):
            add('error', '%d 个 %s 单元没有截面 (SECTYPE) %s' % (
                n, nm, '也没有实常数' if nm in ('LINK180', 'SHELL181', 'SHELL281', 'SHELL208', 'SHELL209') else ''))
        for nm, n in sorted(noreal.items()):
            add('error', '%d 个 %s 单元缺实常数 (R)' % (n, nm))

    def _check_mapdl_materials(self, add, mats):
        # analyses that need mass: modal, harmonic, transient, spectrum (eigenvalue buckling does not)
        types = {self.antype} | set(getattr(self, 'seg_antypes', ()))
        dyn_t = sorted(t for t in types if t in (2, 3, 4, 8))
        dyn = bool(dyn_t)
        acel = self.inertia.get('ACEL')
        grav = bool(acel and any(acel))
        used = {s['mat'] for s in self.sections if s['count'] and self._needs_mat(self.etypes.get(s['etype'], {'cls': '--', 'name': ''}))}
        kinds = set()
        for m in mats:
            nm, mid = m['name'], m['id']
            e, nu, rho = m.get('E'), m.get('nu'), m.get('rho')
            if mid not in used:
                continue
            if nu is not None and nu >= 0.5:
                add('error', '材料 %d 的泊松比 %s ≥ 0.5 (不可压缩), 线弹性材料不允许' % (mid, _fmt(nu)))
            elif nu is not None and nu < 0:
                add('warn', '材料 %d 的泊松比 %s 为负, 请核对' % (mid, _fmt(nu)))
            if e is not None and e <= 0:
                add('error', '材料 %d 的弹性模量 %s ≤ 0' % (mid, _fmt(e)))
            if rho is not None and rho <= 0:
                add('error', '材料 %d 的密度 %s ≤ 0' % (mid, _fmt(rho)))
            if (dyn or grav) and not rho:
                add('error', '材料 %d 没有密度, 但%s' % (mid, '分析类型是 %s' % ANTYPE_NAME.get(dyn_t[0], '?')
                                                        if dyn else '模型有加速度 (ACEL)'))
            c = m.get('c')
            if c is None:
                continue
            if 30.0 <= c <= 8000.0:
                kinds.add('m')
                m['cNote'] = '与 m 制自洽 (波速 m/s 量级)'
            elif 3.0e4 <= c <= 8.0e6:
                kinds.add('mm')
                m['cNote'] = '与 mm 制自洽 (波速 mm/s 量级)'
            else:
                m['cNote'] = '量级异常'
                add('warn', '材料 %d 的 √(E/ρ) = %s, 不像 m 制也不像 mm 制, 请核对 E 与密度的单位' % (mid, _fmt(c)))
        if 'm' in kinds and 'mm' in kinds:
            add('error', '不同材料的 √(E/ρ) 分属 m 制和 mm 制量级, 单位制可能混用')

    def _check_gaps(self, add):
        """solid meshes that face each other without a contact pair between them (inp_model's check, with the
        contact sides given as the node sets of the contact and target elements)"""
        saved = (self.ties, self.contact_pairs)
        self.ties = [{'name': t['name'], 'slave': t['slave'] + '_N', 'master': t['master'] + '_N', 'params': {},
                      'tol': None} for t in saved[0]]
        self.contact_pairs = [{'slave': c['slave'] + '_N', 'master': c['master'] + '_N', 'params': {},
                               'interaction': '', 'step': ''} for c in saved[1]]
        self.__dict__.pop('_links_cache', None)
        msgs = []
        try:
            inp_model.Model._check_facing_gaps(self, lambda level, msg: msgs.append((level, msg)))
        finally:
            self.ties, self.contact_pairs = saved
            self.__dict__.pop('_links_cache', None)
        for level, msg in msgs:
            add(level, msg.replace(' Tie 或接触', '接触对').replace('查一下 Tie 的面是不是漏了这一段',
                                                                    '查一下接触对 (CONTA/TARGE) 是不是漏了这一段'))

    def _check_restraint(self, add):
        """static analysis with loads, but a piece of the model can move as a rigid body in some direction"""
        if self.antype != 0:
            return
        loads = [l for l in self._step['loads'] if l['kw'] in ('F', 'SFE', 'ACEL') and
                 (l['kw'] != 'SFE' or l.get('label') == 'PRES')]
        if not loads:
            return
        dim = self.dimension()
        dirs = (1, 2, 3) if dim == '3d' else (2,) if dim == 'axi' else (1, 2)
        m = self.mesh
        n = len(m.n_label)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def join(ns):
            r0 = None
            for g in ns:
                r = find(g)
                if r0 is None:
                    r0 = r
                elif r != r0:
                    parent[r] = r0
        ov = self._overlay
        for e in range(len(m.e_code)):
            if m.e_code[e] not in ov:
                join(m.e_nodes[m.e_off[e]:m.e_off[e + 1]])
        for con in self.constraints:
            for grp in con.get('_groups') or ():
                join(grp)
        soft = []
        for c in self._pairs:
            a = self.nsets.get(('_CONTA_R%d_N' % c['real']).upper())
            b = self.nsets.get(('_TARGE_R%d_N' % c['real']).upper())
            if not a or not b:
                continue
            if c['bonded']:
                join(list(a['ids'][:1]) + list(b['ids'][:1]))
                join(a['ids'])
                join(b['ids'])
            else:
                soft.append((a['ids'], b['ids']))
        used = self._used_nodes()
        held = {}
        for b in self._step['bcs']:
            ns = self.bc_by_id[b['id']][1]
            d = {x for x in b['dofs'] if x in (1, 2, 3)}
            if not d:
                continue
            for g in ns:
                r = find(g)
                held.setdefault(r, set()).update((1, 2, 3) if g in self.node_rot else d)
        bodies = {}
        for g in range(n):
            if used[g]:
                bodies.setdefault(find(g), 0)
                bodies[find(g)] += 1
        soft_roots = set()
        for a, b in soft:
            for g in list(a[:1]) + list(b[:1]):
                soft_roots.add(find(g))
        free_bodies = []
        for r, cnt in bodies.items():
            free = [d for d in dirs if d not in held.get(r, ())]
            if free:
                free_bodies.append((r, cnt, free))
        if not free_bodies:
            return
        dname = {1: 'UX', 2: 'UY', 3: 'UZ'}
        if len(bodies) == 1:
            r, cnt, free = free_bodies[0]
            add('warn', '静力分析有载荷, 但整个模型在 %s 方向没有任何位移约束, 会出现刚体位移' %
                '、'.join(dname[d] for d in free))
            return
        for r, cnt, free in sorted(free_bodies, key=lambda x: -x[1])[:6]:
            if r in soft_roots:
                add('info', '静力分析有载荷, 一块网格 (%d 个节点) 在 %s 方向没有位移约束, 只能靠接触约束, '
                            '请确认不会出现刚体位移' % (cnt, '、'.join(dname[d] for d in free)))
            else:
                add('warn', '静力分析有载荷, 但一块网格 (%d 个节点) 在 %s 方向没有位移约束, 也没有绑定/约束方程/耦合连到'
                            '已约束的部分, 会出现刚体位移' % (cnt, '、'.join(dname[d] for d in free)))

    # ------------------------------------------------------------ view
    def view(self, geom=True):
        v = inp_model.Model.view(self, geom)
        v['solver'] = 'mapdl'
        st = v['stats']
        st['flat'] = True
        st['deadElements'] = self.dead
        st['rotatedNodes'] = len(self.node_rot)
        st['shapes'] = {'%s %s' % k: n for k, n in sorted(self._shapes.items())}
        try:
            ms = self.mass()
            st['mass'] = ms['total']
            st['massComplete'] = ms['complete']
            if ms['missing']:
                st['massMissing'] = ms['missing']
        except Exception:
            pass
        try:
            st['loadResultant'] = self.load_resultant()['force']
        except Exception:
            pass
        def clean(d):
            return {k: x for k, x in d.items() if not k.startswith('_') and not isinstance(x, array)}
        v['steps'] = [dict(s, bcs=[clean(b) for b in s['bcs']], loads=[clean(l) for l in s['loads']])
                      for s in self.steps]
        v['parameters'] = {k: (repr(x) if isinstance(x, float) else str(x)) for k, x in sorted(self.params.items())
                           if not k.startswith('_')} or None
        v['cdb'] = {'release': self.release, 'violations': [{'line': ln, 'msg': t} for ln, t in self.violations],
                    'commands': dict(sorted(self.cmd_count.items())), 'antype': self.antype,
                    'etypes': [{'type': t, 'ename': et['ename'], 'name': et['name'],
                                'keyopt': {str(i + 1): k for i, k in enumerate(et['keyopt']) if k}}
                               for t, et in sorted(self.etypes.items())],
                    'reals': {str(k): v_[:12] for k, v_ in sorted(self.reals.items())},
                    'sections': [{'id': s['id'], 'type': s['type'], 'subtype': s['subtype'], 'data': s['data'][:12],
                                  'layers': s['layers'][:20]} for s in sorted(self.secs.values(), key=lambda s: s['id'])],
                    'csys': [self.csys[k] for k in sorted(self.csys)],
                    'contacts': self._pairs, 'tables': sorted(self.tables)}
        seg = self.seg
        v['solve'] = {'file': os.path.basename(self.solve_file), 'present': seg is not None,
                      'violations': [{'line': ln, 'msg': t} for ln, t in seg['violations']] if seg else [],
                      'commands': len(seg['commands']) if seg else 0, 'steps': len(self.seg_steps),
                      'after': [c['line'] for c in seg['after']] if seg else [],
                      'whitelist': solve_segment.whitelist_text()}
        return v


# ---------------------------------------------------------------- numbers
def _g(v):
    if isinstance(v, str):
        return v
    if v != v or v in (float('inf'), float('-inf')):
        return str(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return '%.6g' % v


def _short(x):
    x = x.strip()
    try:
        return _g(float(x))
    except ValueError:
        return x


def _floats_loose(items):
    out = []
    for x in items:
        x = x.strip() if isinstance(x, str) else x
        if x == '' or x is None:
            continue
        try:
            out.append(_num(x) if isinstance(x, str) else float(x))
        except ValueError:
            continue
    return out


def _rotate(angles, v):
    """vector given in a nodal coordinate system rotated by (thxy, thyz, thzx) degrees -> global"""
    a, b, c = (math.radians(x) for x in angles)
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    # R = Rz(thxy) * Rx(thyz) * Ry(thzx)
    rz = ((ca, -sa, 0.0), (sa, ca, 0.0), (0.0, 0.0, 1.0))
    rx = ((1.0, 0.0, 0.0), (0.0, cb, -sb), (0.0, sb, cb))
    ry = ((cc, 0.0, sc), (0.0, 1.0, 0.0), (-sc, 0.0, cc))

    def mul(m, x):
        return tuple(m[i][0] * x[0] + m[i][1] * x[1] + m[i][2] * x[2] for i in range(3))
    return mul(rz, mul(rx, mul(ry, v)))


def _centroid(pts):
    n = float(len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n, sum(p[2] for p in pts) / n)


def _area_vector(pts):
    """Newell area vector of a polygon (half the cross products), repeated points allowed"""
    nx = ny = nz = 0.0
    k = len(pts)
    for i in range(k):
        a, b = pts[i], pts[(i + 1) % k]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx / 2.0, ny / 2.0, nz / 2.0)


_G2 = (-1.0 / math.sqrt(3.0), 1.0 / math.sqrt(3.0))
_G3 = ((-math.sqrt(0.6), 5.0 / 9.0), (0.0, 8.0 / 9.0), (math.sqrt(0.6), 5.0 / 9.0))
_HEX8 = ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))


def _det3(a, b, c):
    return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) +
            a[2] * (b[0] * c[1] - b[1] * c[0]))


def _hex8_volume(p):
    vol = 0.0
    for x in _G2:
        for y in _G2:
            for z in _G2:
                d = [[0.0] * 3 for _ in range(3)]
                for (xi, yi, zi), q in zip(_HEX8, p):
                    dx = 0.125 * xi * (1 + y * yi) * (1 + z * zi)
                    dy = 0.125 * yi * (1 + x * xi) * (1 + z * zi)
                    dz = 0.125 * zi * (1 + x * xi) * (1 + y * yi)
                    for k in range(3):
                        d[0][k] += dx * q[k]
                        d[1][k] += dy * q[k]
                        d[2][k] += dz * q[k]
                vol += _det3(d[0], d[1], d[2])
    return abs(vol)


def _hex20_volume(p):
    """20-node serendipity brick (MAPDL node order I..P, then Q..X on the edges I-J ... and Y..B on the
    vertical edges), 3x3x3 Gauss"""
    mids = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
    loc = list(_HEX8)
    for a, b in mids:
        loc.append(tuple((loc[a][k] + loc[b][k]) / 2.0 for k in range(3)))
    vol = 0.0
    for x, wx in _G3:
        for y, wy in _G3:
            for z, wz in _G3:
                d = [[0.0] * 3 for _ in range(3)]
                for i, ((xi, yi, zi), q) in enumerate(zip(loc, p)):
                    if i < 8:
                        s = x * xi + y * yi + z * zi - 2
                        dx = 0.125 * xi * (1 + y * yi) * (1 + z * zi) * s + 0.125 * (1 + x * xi) * (1 + y * yi) * (1 + z * zi) * xi
                        dy = 0.125 * yi * (1 + x * xi) * (1 + z * zi) * s + 0.125 * (1 + x * xi) * (1 + y * yi) * (1 + z * zi) * yi
                        dz = 0.125 * zi * (1 + x * xi) * (1 + y * yi) * s + 0.125 * (1 + x * xi) * (1 + y * yi) * (1 + z * zi) * zi
                    elif xi == 0:
                        dx = -0.5 * x * (1 + y * yi) * (1 + z * zi)
                        dy = 0.25 * (1 - x * x) * yi * (1 + z * zi)
                        dz = 0.25 * (1 - x * x) * (1 + y * yi) * zi
                    elif yi == 0:
                        dx = 0.25 * xi * (1 - y * y) * (1 + z * zi)
                        dy = -0.5 * y * (1 + x * xi) * (1 + z * zi)
                        dz = 0.25 * (1 + x * xi) * (1 - y * y) * zi
                    else:
                        dx = 0.25 * xi * (1 + y * yi) * (1 - z * z)
                        dy = 0.25 * (1 + x * xi) * yi * (1 - z * z)
                        dz = -0.5 * z * (1 + x * xi) * (1 + y * yi)
                    for k in range(3):
                        d[0][k] += dx * q[k]
                        d[1][k] += dy * q[k]
                        d[2][k] += dz * q[k]
                vol += wx * wy * wz * _det3(d[0], d[1], d[2])
    return abs(vol)


def _tet4_volume(p):
    return abs(_det3(_sub(p[1], p[0]), _sub(p[2], p[0]), _sub(p[3], p[0]))) / 6.0


def _tet10_volume(p):
    """10-node tetrahedron (MAPDL order I J K L, then M(I-J) N(J-K) O(K-I) P(I-L) Q(J-L) R(K-L)),
    degree-3 rule (5 points)"""
    pts = ((0.25, 0.25, 0.25, -0.8), (0.5, 1 / 6.0, 1 / 6.0, 0.45), (1 / 6.0, 0.5, 1 / 6.0, 0.45),
           (1 / 6.0, 1 / 6.0, 0.5, 0.45), (1 / 6.0, 1 / 6.0, 1 / 6.0, 0.45))
    vol = 0.0
    for r, s, t, w in pts:
        L = (1 - r - s - t, r, s, t)
        dL = ((-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1))
        edges = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
        dN = []
        for i in range(4):
            dN.append(tuple((4 * L[i] - 1) * dL[i][k] for k in range(3)))
        for a, b in edges:
            dN.append(tuple(4 * (L[a] * dL[b][k] + L[b] * dL[a][k]) for k in range(3)))
        d = [[0.0] * 3 for _ in range(3)]
        for (gr, gs, gt), q in zip(dN, p):
            for k in range(3):
                d[0][k] += gr * q[k]
                d[1][k] += gs * q[k]
                d[2][k] += gt * q[k]
        vol += w * _det3(d[0], d[1], d[2])
    return abs(vol) / 6.0


# midside node (index in the MAPDL node list) of each edge of a 20-node brick / 10-node tetrahedron
_HEX20_MID = {frozenset(e): 8 + k for k, e in enumerate(((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7),
                                                         (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)))}
_TET10_MID = {frozenset(e): 4 + k for k, e in enumerate(((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)))}
_TRI7 = ((1 / 3.0, 1 / 3.0, 0.225),
         (0.0597158717, 0.4701420641, 0.1323941527), (0.4701420641, 0.0597158717, 0.1323941527),
         (0.4701420641, 0.4701420641, 0.1323941527),
         (0.7974269853, 0.1012865073, 0.1259391805), (0.1012865073, 0.7974269853, 0.1259391805),
         (0.1012865073, 0.1012865073, 0.1259391805))


def _quad_face_load(c, mids, pv):
    """integral of p * (x_u x x_v) over a face given by its corners (3 or 4, in face order), the midside nodes
    of its edges (or None for straight edges) and the pressure at the corners (interpolated linearly)"""
    if len(c) == 4:
        if mids:
            loc = ((-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0))
            pts = list(c) + list(mids)
        else:
            loc = ((-1, -1), (1, -1), (1, 1), (-1, 1))
            pts = list(c)
        tot = [0.0, 0.0, 0.0]
        for x, wx in _G3:
            for y, wy in _G3:
                dx = [0.0, 0.0, 0.0]
                dy = [0.0, 0.0, 0.0]
                for i, ((xi, yi), q) in enumerate(zip(loc, pts)):
                    if len(loc) == 4:
                        Nx = 0.25 * xi * (1 + y * yi)
                        Ny = 0.25 * yi * (1 + x * xi)
                    elif i < 4:
                        Nx = 0.25 * xi * (1 + y * yi) * (2 * x * xi + y * yi)
                        Ny = 0.25 * yi * (1 + x * xi) * (x * xi + 2 * y * yi)
                    elif xi == 0:
                        Nx = -x * (1 + y * yi)
                        Ny = 0.5 * (1 - x * x) * yi
                    else:
                        Nx = 0.5 * xi * (1 - y * y)
                        Ny = -y * (1 + x * xi)
                    for k in range(3):
                        dx[k] += Nx * q[k]
                        dy[k] += Ny * q[k]
                p = sum(0.25 * (1 + x * xi) * (1 + y * yi) * pv[i] for i, (xi, yi) in enumerate(loc[:4]))
                n = _cross(dx, dy)
                for k in range(3):
                    tot[k] += wx * wy * p * n[k]
        return tot
    # triangle: 3 corners (+ 3 midside nodes), area coordinates
    pts = list(c) + (list(mids) if mids else [])
    tot = [0.0, 0.0, 0.0]
    for r, s, w in _TRI7:
        L = (1 - r - s, r, s)
        dLr, dLs = (-1, 1, 0), (-1, 0, 1)
        dr = [0.0, 0.0, 0.0]
        ds = [0.0, 0.0, 0.0]
        if mids:
            terms = [((4 * L[i] - 1) * dLr[i], (4 * L[i] - 1) * dLs[i]) for i in range(3)]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                terms.append((4 * (L[a] * dLr[b] + L[b] * dLr[a]), 4 * (L[a] * dLs[b] + L[b] * dLs[a])))
        else:
            terms = [(dLr[i], dLs[i]) for i in range(3)]
        for (nr, ns), q in zip(terms, pts):
            for k in range(3):
                dr[k] += nr * q[k]
                ds[k] += ns * q[k]
        p = sum(L[i] * pv[i] for i in range(3))
        n = _cross(dr, ds)
        for k in range(3):
            tot[k] += 0.5 * w * p * n[k]
    return tot


def _straight(p, n_corner, edges):
    """True when every midside node sits at the middle of its edge"""
    for k, (a, b) in enumerate(edges):
        q = p[n_corner + k]
        m = tuple((p[a][i] + p[b][i]) / 2.0 for i in range(3))
        size = max(abs(p[a][i] - p[b][i]) for i in range(3)) or 1.0
        if max(abs(q[i] - m[i]) for i in range(3)) > 1e-9 * size:
            return False
    return True


def _solid_volume(cls, p):
    if cls == 'S4':
        if len(p) >= 10 and not _straight(p, 4, ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))):
            return _tet10_volume(p[:10])
        return _tet4_volume(p[:4])
    if len(p) >= 20:
        edges = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
        if not _straight(p, 8, edges):
            return _hex20_volume(p[:20])
    return _hex8_volume(p[:8])


def _quad_area(p, axi=False, plane=False):
    """bilinear quad (4 points) or serendipity quad (8 points): area, or 2*pi*integral(x dA) when axi"""
    if len(p) >= 8:
        loc = ((-1, -1), (1, -1), (1, 1), (-1, 1), (0, -1), (1, 0), (0, 1), (-1, 0))
        rule = _G3
    else:
        loc = ((-1, -1), (1, -1), (1, 1), (-1, 1))
        rule = tuple((g, 1.0) for g in _G2)
    tot = 0.0
    for x, wx in rule:
        for y, wy in rule:
            dx = [0.0, 0.0, 0.0]
            dy = [0.0, 0.0, 0.0]
            pos = [0.0, 0.0, 0.0]
            for i, ((xi, yi), q) in enumerate(zip(loc, p)):
                if len(loc) == 4:
                    N = 0.25 * (1 + x * xi) * (1 + y * yi)
                    Nx = 0.25 * xi * (1 + y * yi)
                    Ny = 0.25 * yi * (1 + x * xi)
                elif i < 4:
                    N = 0.25 * (1 + x * xi) * (1 + y * yi) * (x * xi + y * yi - 1)
                    Nx = 0.25 * xi * (1 + y * yi) * (2 * x * xi + y * yi)
                    Ny = 0.25 * yi * (1 + x * xi) * (x * xi + 2 * y * yi)
                elif xi == 0:
                    N = 0.5 * (1 - x * x) * (1 + y * yi)
                    Nx = -x * (1 + y * yi)
                    Ny = 0.5 * (1 - x * x) * yi
                else:
                    N = 0.5 * (1 + x * xi) * (1 - y * y)
                    Nx = 0.5 * xi * (1 - y * y)
                    Ny = -y * (1 + x * xi)
                for k in range(3):
                    dx[k] += Nx * q[k]
                    dy[k] += Ny * q[k]
                    pos[k] += N * q[k]
            c = _cross(dx, dy)
            a = math.sqrt(c[0] ** 2 + c[1] ** 2 + c[2] ** 2) if not plane else abs(c[2])
            tot += wx * wy * a * (2 * math.pi * pos[0] if axi else 1.0)
    return tot


def _tri_area(p, axi=False):
    c = _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))
    a = 0.5 * math.sqrt(c[0] ** 2 + c[1] ** 2 + c[2] ** 2)
    if axi:
        return a * 2 * math.pi * (p[0][0] + p[1][0] + p[2][0]) / 3.0
    return a


def _surface_area(p):
    if len(p) >= 8:
        if p[2] == p[3]:                       # degenerate 8-node quad (triangle): corners only
            return _tri_area(p[:3])
        return _quad_area(p[:8])
    if len(p) >= 4 and p[2] != p[3]:
        return _quad_area(p[:4])
    return _tri_area(p[:3])


def _plane_area(p, axi=False):
    if len(p) >= 8 and p[2] != p[3]:
        return _quad_area(p[:8], axi, plane=True)
    if len(p) >= 4 and p[2] != p[3] and len(p) != 6:
        return _quad_area(p[:4], axi, plane=True)
    return _tri_area(p[:3], axi)


def _line_length(p, quad):
    if quad and len(p) >= 3:
        # MAPDL order I, J, K(middle)
        a, b, c = p[0], p[1], p[2]
        tot = 0.0
        for x, w in _G3:
            d = [(-0.5 + x) * a[k] + (0.5 + x) * b[k] - 2 * x * c[k] for k in range(3)]
            tot += w * math.sqrt(sum(v * v for v in d))
        return tot
    return math.sqrt(sum((p[1][k] - p[0][k]) ** 2 for k in range(3)))
