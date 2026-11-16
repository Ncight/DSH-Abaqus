# -*- coding: utf-8 -*-
"""The solve segment of an ANSYS job (<job>.solve.dat, ANSYS plan v2.1, 4.2): whitelist check and load steps.

The solve segment is what MAPDL executes after CDREAD of the approved CDB, in a clean run directory.  It may
change the analysis settings and the loads from load step to load step, but nothing else, so every line must
be one whitelisted command with literal values, and every load must name a component of the CDB:

    seg = parse(text, nodes=..., elems=..., params=..., pretension=...)
    seg['violations']   [(line, message)]  - anything the toolbox must refuse to run
    seg['commands']     [{'line', 'cmd', 'name', 'args'}]  every command, canonical name in 'name'
    seg['steps']        [{'first', 'solve', 'commands'}]   commands up to and including each SOLVE
    seg['after']        commands after the last SOLVE (they do not take effect)
    seg['refs']         [(line, component, 'NODE' | 'ELEM' | 'ANY')]

Rules (each one was tried on MAPDL 2025 R1, see dsh-ansys docs\\第0步实测.md and docs\\第3步.md):
  * one command per line, `!` starts a comment; `$` (several commands on one line) is refused;
  * a command is recognised the way MAPDL does it: by its first four characters, case and leading blanks
    ignored (SOLVXYZ and " solve" both solve);
  * no parameter assignment (`=`), no %...% substitution, no *-commands, no /-commands but /SOLU;
  * values are literals: a field that takes a number must hold a number (MAPDL would substitute a parameter
    name there, and evaluate 2*3), a field that takes a label must hold a word or a number;
  * D, DDELE, F, FDELE, SF, SFDELE, BF, BFDELE act on a node component of the CDB (or ALL), EKILL and
    EALIVE on an element component (or ALL); SLOAD on a pretension section of the CDB; a component name
    that is also a parameter of the CDB (*SET) is refused.

Standard library only.
"""
import re

# canonical name -> argument kinds after the command name:
#   N node target (node component or ALL)    E element target (element component or ALL)
#   C optional component of either kind      S pretension section number
#   n number                                 l label (word or number)              - must stay empty
WHITELIST = {
    '/SOLU': '', 'FINISH': '', 'ALLSEL': 'll', 'SOLVE': 'l',
    # analysis type and options
    'ANTYPE': 'llnnl', 'NLGEOM': 'l', 'PSTRES': 'l', 'MODOPT': 'lnnnlllnlll', 'MXPAND': 'nnnlnlll',
    'BUCOPT': 'lnnnl', 'HROPT': 'lnnll', 'HARFRQ': 'nn-l-n', 'TRNOPT': 'lnlnlll-ll', 'TINTP': 'nnnnnn--nnn',
    'EQSLV': 'lnnl', 'TIMINT': 'll',
    # time steps and convergence
    'TIME': 'n', 'NSUBST': 'nnnl', 'DELTIM': 'nnnl', 'AUTOTS': 'l', 'KBC': 'n', 'NEQIT': 'nl',
    'CNVTOL': 'lnnnn', 'LNSRCH': 'l', 'NROPT': 'l-l',
    # damping
    'ALPHAD': 'n', 'BETAD': 'n', 'DMPRAT': 'n', 'DMPSTR': 'n',
    # output
    'OUTRES': 'llCnl', 'OUTPR': 'llC', 'RESCONTROL': 'llln-n',
    # loads
    'D': 'Nlnn--lllll', 'DDELE': 'Nl--l', 'F': 'Nlnn--n', 'FDELE': 'Nl--l', 'SF': 'Nlnnn', 'SFDELE': 'Nl',
    'BF': 'Nlnnnnnnn', 'BFDELE': 'Nl', 'ACEL': 'nnn', 'TUNIF': 'n', 'TREF': 'n', 'EKILL': 'E', 'EALIVE': 'E',
    'SLOAD': 'Slllnnn',
}
GROUPS = [('进出', '/SOLU FINISH ALLSEL'),
          ('分析类型和选项', 'ANTYPE NLGEOM PSTRES MODOPT MXPAND BUCOPT HROPT HARFRQ TRNOPT TINTP EQSLV TIMINT'),
          ('时间步和收敛', 'TIME NSUBST DELTIM AUTOTS KBC NEQIT CNVTOL LNSRCH NROPT'),
          ('阻尼', 'ALPHAD BETAD DMPRAT DMPSTR'), ('输出', 'OUTRES OUTPR RESCONTROL'),
          ('载荷', 'D DDELE F FDELE SF SFDELE BF BFDELE ACEL TUNIF TREF EKILL EALIVE SLOAD'), ('求解', 'SOLVE')]
BY4 = {name[:4]: name for name in WHITELIST}
NODE_CMDS = ('D', 'DDELE', 'F', 'FDELE', 'SF', 'SFDELE', 'BF', 'BFDELE')
ELEM_CMDS = ('EKILL', 'EALIVE')
_NUM = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$')
_WORD = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
ANTYPE_WORD = {'STAT': 0, 'BUCK': 1, 'MODA': 2, 'HARM': 3, 'TRAN': 4, 'SUBS': 7, 'SPEC': 8, 'SOIL': 9}


def number(s):
    """literal number of a field (Fortran D exponent allowed) or None"""
    s = s.strip()
    if not _NUM.match(s):
        return None
    return float(s.replace('d', 'e').replace('D', 'e'))


def antype_of(arg, current):
    """ANTYPE argument (word or number) -> analysis type number"""
    a = (arg or '').strip().upper()
    if not a:
        return current
    v = number(a)
    if v is not None:
        return int(v)
    return ANTYPE_WORD.get(a[:4], current)


def parse(text, nodes=(), elems=(), params=(), pretension=()):
    """text of a solve segment -> dict (see the module docstring).

    nodes / elems: names of the node / element components of the CDB (any case); params: names of its *SET
    parameters; pretension: numbers of its pretension sections."""
    ncomp = {x.upper() for x in nodes}
    ecomp = {x.upper() for x in elems}
    pars = {x.upper() for x in params}
    psec = set(pretension)
    viol, cmds, refs = [], [], []

    def bad(ln, msg):
        viol.append((ln, msg))

    for ln, raw in enumerate(text.split('\n'), 1):
        line = raw.rstrip('\r')
        code = line.split('!', 1)[0]
        if '$' in code:
            bad(ln, '一行里有 $ (MAPDL 会把它拆成几条命令), 求解段每行只能写一条命令')
            continue
        s = code.strip()
        if not s:
            continue
        if '=' in s:
            bad(ln, '参数赋值 (=) 不允许, 数值只能写字面量: %s' % s[:60])
            continue
        if '%' in s:
            bad(ln, '%参数% 替换不允许, 数值只能写字面量: ' + s[:60])
            continue
        fields = s.split(',')
        word = fields[0].strip().upper()
        if not word or re.search(r'\s', word):
            bad(ln, '看不出命令名: %s' % s[:60])
            continue
        name = BY4.get(word[:4])
        if name is None:
            if word.startswith('*'):
                bad(ln, '%s 不允许 (求解段里不能有 *DO、*IF、*USE、*SET 这类 APDL 语句)' % word)
            elif word.startswith('/'):
                bad(ln, '%s 不允许 (斜杠命令只放行 /SOLU)' % word)
            else:
                bad(ln, '命令 %s 不在求解段白名单内' % word)
            continue
        args = [f.strip() for f in fields[1:]]
        while args and args[-1] == '':
            args.pop()
        spec = WHITELIST[name]
        if len(args) > len(spec):
            bad(ln, '%s 的参数多于 %d 个: %s' % (name, len(spec), s[:80]))
            continue
        ok = True
        for i, a in enumerate(args):
            kind = spec[i]
            if not a:
                if kind in 'NES':
                    bad(ln, '%s 没写作用对象 (第 %d 个参数)' % (name, i + 1))
                    ok = False
                continue
            if _WORD.match(a) and a.upper() in pars:
                bad(ln, '%s 的第 %d 个参数 %s 和 CDB 里的参数 (*SET) 同名, MAPDL 可能当成参数取值' % (name, i + 1, a))
                ok = False
                continue
            if kind == '-':
                bad(ln, '%s 的第 %d 个参数必须空着 (按组件加载时用不到): %s' % (name, i + 1, a))
                ok = False
            elif kind == 'n':
                if number(a) is None:
                    bad(ln, '%s 的第 %d 个参数要写数字字面量: %s' % (name, i + 1, a))
                    ok = False
            elif kind == 'l':
                if number(a) is None and not _WORD.match(a):
                    bad(ln, '%s 的第 %d 个参数要写一个词或数字: %s' % (name, i + 1, a))
                    ok = False
            elif kind in 'NE':
                up = a.upper()
                if up == 'ALL':
                    continue
                if number(a) is not None:
                    bad(ln, '%s 只能对组件名或 ALL 加, 不能写编号: %s' % (name, a))
                    ok = False
                elif not _WORD.match(a):
                    bad(ln, '%s 的作用对象不是组件名: %s' % (name, a))
                    ok = False
                elif kind == 'N' and up not in ncomp:
                    bad(ln, ('%s 要节点组件, %s 是单元组件' % (name, a)) if up in ecomp else
                        '组件 %s 在 CDB 里没有 (%s 要节点组件)' % (a, name))
                    ok = False
                elif kind == 'E' and up not in ecomp:
                    bad(ln, ('%s 要单元组件, %s 是节点组件' % (name, a)) if up in ncomp else
                        '组件 %s 在 CDB 里没有 (%s 要单元组件)' % (a, name))
                    ok = False
                else:
                    refs.append((ln, up, 'NODE' if kind == 'N' else 'ELEM'))
            elif kind == 'C':
                up = a.upper()
                if up not in ncomp and up not in ecomp:
                    bad(ln, '组件 %s 在 CDB 里没有 (%s 的组件参数)' % (a, name))
                    ok = False
                else:
                    refs.append((ln, up, 'ANY'))
            elif kind == 'S':
                v = number(a)
                if v is None or v != int(v) or int(v) not in psec:
                    bad(ln, '%s 的第一个参数要写 CDB 里预紧截面 (SECTYPE ...,PRETENSION) 的编号, %s 不是' % (name, a))
                    ok = False
        cmds.append({'line': ln, 'cmd': word, 'name': name, 'args': args, 'ok': ok})
    steps, cur = [], []
    for c in cmds:
        cur.append(c)
        if c['name'] == 'SOLVE':
            steps.append({'first': cur[0]['line'], 'solve': c['line'], 'commands': cur})
            cur = []
    after = [c for c in cur if c['name'] != 'FINISH']
    return {'violations': viol, 'commands': cmds, 'steps': steps, 'after': after, 'refs': refs}


def needs_nropt_full(seg, antype0=0, nlgeom0=False):
    """lines of EKILL / EALIVE that MAPDL refuses: linear static analysis (NLGEOM off) without NROPT,FULL
    before the first SOLVE ("The birth and death capability requires that the NROPT,FULL command be
    specified before the first SOLVE command and before the first EKILL command", MAPDL 2025 R1)"""
    antype, nlgeom, full, solved = antype0, nlgeom0, False, False
    out = []
    for c in seg['commands']:
        n, a = c['name'], c['args']
        if n == 'ANTYPE':
            antype = antype_of(a[0] if a else '', antype)
        elif n == 'NLGEOM':
            nlgeom = bool(a) and a[0].upper() in ('ON', '1', 'YES')
        elif n == 'NROPT':
            if not solved and a and a[0].upper().startswith('FULL'):
                full = True
        elif n == 'SOLVE':
            solved = True
        elif n in ELEM_CMDS and antype == 0 and not nlgeom and not full:
            out.append(c['line'])
    return out


def whitelist_text():
    """the whitelist grouped as in the plan, one group per line"""
    return '\n'.join('%s: %s' % (g, ' '.join(names.split())) for g, names in GROUPS)
