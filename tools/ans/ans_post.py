# -*- coding: utf-8 -*-
"""Results of an ANSYS job read by MAPDL itself (ans rst): /POST1 + *GET + *VWRITE into small text files.

The session reads only the results file (FILE,<job>,rst), not the CDB: the results file carries the mesh and the
components (dsh-ansys step 0: reading the CDB first adds a "may have been solved using a different model"
warning).  It runs in the run directory under the job name "post", so the job's own files stay as they are.

    text = post_input(job, ext, kind, comps, ecomps, nls, step)    # the input file
    res = parse(rundir, comps, nls)                                 # -> dict
Files: post_sets.txt (set, load step, substep, time, frequency), post_modes.txt (modal, buckling: the largest and
smallest UX, UY, UZ of each of the first MAX_MODES mode shapes, to tell which way a mode moves), post_main.txt
(load step, substep, time of the set the main summary reads), and for that set: post_u.txt (largest displacement,
its node and place), post_react.txt (PRRSOL), post_comp_<NAME>.txt (FSUM and the largest displacement over a
component), post_s.txt (largest von Mises stress, late because it is the item most likely to be missing for line
elements), post_cs_<NAME>.txt (static, transient: largest and smallest SX, SY, SZ, SEQV over a component's nodes,
last of all for the same reason); post_amp.txt (harmonic: largest amplitude per frequency).
Static and transient jobs with several load steps: the same files once more for the end of every load step,
post_step<N>.txt (load step, substep, time) and post_u_ls<N>.txt, post_react_ls<N>.txt, ... (user, 2026-11-15:
the numbers of an intermediate load step could not be had).
Stress linearization (static, transient; ans rst --lin, 2026-11-16 for pressure-vessel assessment): for each
classification line the nodes nearest to its two given points (post_lin<K>_nodes.txt), then PATH + PPATH by those
two nodes (PRSECT wants a path defined by nodes) + PRSECT on the set of the main summary -> post_lin<K>.txt, read by
read_prsect().  PRSECT prints four significant digits whatever /FORMAT says.
Standard library only.
"""
import os
import re

MAX_SETS = 2000
MAX_MODES = 50
MAX_STEPS = 50       # load steps read one by one (static, transient)
MAX_LINES = 10       # stress classification lines per call

STRESS_ITEMS = ('X', 'Y', 'Z', 'EQV')


def _block(sfx, kind, comps, sel):
    """what is read from the active set: largest displacement, reaction totals (static, transient), components,
    largest SEQV and component stresses (static, transient) -> post_u<sfx>.txt, post_react<sfx>.txt, ..."""
    L = ['ALLSEL,ALL', 'NSORT,U,SUM', '*GET,UMAX_,SORT,0,MAX', '*GET,UN_,SORT,0,IMAX', 'NUSORT',
         '*GET,UX_,NODE,UN_,U,X', '*GET,UY_,NODE,UN_,U,Y', '*GET,UZ_,NODE,UN_,U,Z',
         '*GET,X_,NODE,UN_,LOC,X', '*GET,Y_,NODE,UN_,LOC,Y', '*GET,Z_,NODE,UN_,LOC,Z',
         '*CFOPEN,post_u%s,txt' % sfx, '*VWRITE,UMAX_,UN_,UX_,UY_,UZ_,X_,Y_,Z_', '(E22.13,F12.0,6E22.13)', '*CFCLOS']
    if kind in ('static', 'transient'):
        L += ['/FORMAT,,E,24,14', '/OUTPUT,post_react%s,txt' % sfx, 'PRRSOL', '/OUTPUT', '/FORMAT,DEFA']
    for c in comps:
        L += sel(c) + ['*GET,NC_,NODE,0,COUNT', 'FSUM',
                       '*GET,CFX_,FSUM,0,ITEM,FX', '*GET,CFY_,FSUM,0,ITEM,FY', '*GET,CFZ_,FSUM,0,ITEM,FZ',
                       'NSORT,U,SUM', '*GET,CU_,SORT,0,MAX', '*GET,CN_,SORT,0,IMAX', 'NUSORT',
                       '*CFOPEN,post_comp_%s%s,txt' % (c, sfx), '*VWRITE,NC_,CFX_,CFY_,CFZ_,CU_,CN_',
                       '(F12.0,4E22.13,F12.0)', '*CFCLOS']
    L += ['ALLSEL,ALL', 'NSORT,S,EQV', '*GET,SMAX_,SORT,0,MAX', '*GET,SN_,SORT,0,IMAX', 'NUSORT',
          '*CFOPEN,post_s%s,txt' % sfx, '*VWRITE,SMAX_,SN_', '(E22.13,F12.0)', '*CFCLOS']
    # nodal stresses over each component (static, transient), last because line elements have none: largest and
    # smallest SX, SY, SZ (global axes, averaged at the nodes) and SEQV, with their nodes
    if kind in ('static', 'transient'):
        for c in comps:
            L += sel(c) + ['*CFOPEN,post_cs_%s%s,txt' % (c, sfx)]
            for it in STRESS_ITEMS:
                L += ['NSORT,S,%s' % it, '*GET,SA_,SORT,0,MAX', '*GET,NA_,SORT,0,IMAX', '*GET,SB_,SORT,0,MIN',
                      '*GET,NB_,SORT,0,IMIN', 'NUSORT', '*VWRITE,SA_,NA_,SB_,NB_', '(E22.13,F12.0,E22.13,F12.0)']
            L += ['*CFCLOS']
    return L


def _lin_block(lins):
    """stress linearization on the active set: lins = [(name, (x1, y1, z1), (x2, y2, z2)), ...]"""
    L = []
    for k, (_, p1, p2) in enumerate(lins[:MAX_LINES], 1):
        L += ['ALLSEL,ALL', 'LNA_=NODE(%r,%r,%r)' % tuple(float(v) for v in p1),
              'LNB_=NODE(%r,%r,%r)' % tuple(float(v) for v in p2),
              '*CFOPEN,post_lin%d_nodes,txt' % k,
              '*VWRITE,LNA_,NX(LNA_),NY(LNA_),NZ(LNA_),LNB_,NX(LNB_),NY(LNB_),NZ(LNB_)',
              '(F12.0,3E24.15,F12.0,3E24.15)', '*CFCLOS',
              '*IF,LNA_,NE,LNB_,THEN', 'PATH,LIN%d,2,30,48' % k, 'PPATH,1,LNA_', 'PPATH,2,LNB_',
              '/OUTPUT,post_lin%d,txt' % k, 'PRSECT', '/OUTPUT', '*ENDIF']
    return L


def post_input(job, ext='rst', kind='static', comps=(), ecomps=(), nls=1, step=None, lins=()):
    """kind: static | transient | modal | buckle | harmonic (decides what is taken from which set)
    comps: components asked for with --comp; ecomps: those of them that are element components (their nodes are
    selected with NSLE and only their elements are averaged at them); nls: load steps of the solve segment;
    step: static, transient - the load step whose end the main summary reads (default: the last set);
    lins: static, transient - stress classification lines [(name, p1, p2)], linearized on that same set"""
    ecomps = {c.upper() for c in ecomps}

    def sel(c):
        return ['ALLSEL,ALL', 'CMSEL,S,%s' % c] + (['NSLE,S'] if c.upper() in ecomps else [])
    L = ['/PSEARCH,OFF', '/POST1', 'FILE,%s,%s' % (job, ext), '*GET,NSET_,ACTIVE,0,SET,NSET',
         '*IF,NSET_,GT,%d,THEN' % MAX_SETS, 'NSET_=%d' % MAX_SETS, '*ENDIF',
         '*CFOPEN,post_sets,txt', '*DO,K_,1,NSET_', 'SET,,,,,,,K_',
         '*GET,T_,ACTIVE,0,SET,TIME', '*GET,F_,ACTIVE,0,SET,FREQ', '*GET,LS_,ACTIVE,0,SET,LSTP',
         '*GET,SB_,ACTIVE,0,SET,SBST', '*VWRITE,K_,LS_,SB_,T_,F_', '(F8.0,F8.0,F8.0,E22.13,E22.13)', '*ENDDO', '*CFCLOS']
    if kind == 'harmonic':
        # complex results: a real and an imaginary set per frequency.  SET cannot take the amplitude (KIMG) together
        # with a set number (MAPDL 2025 R1: "The KIMG option is not supported with the NSET option"), so read the
        # load step and substep of every other set and ask for that one's amplitude
        L += ['*CFOPEN,post_amp,txt', '*DO,K_,1,NSET_,2', 'SET,,,,,,,K_', '*GET,LS_,ACTIVE,0,SET,LSTP',
              '*GET,SB_,ACTIVE,0,SET,SBST', 'SET,LS_,SB_,,AMPL', '*GET,F_,ACTIVE,0,SET,FREQ', 'ALLSEL,ALL',
              'NSORT,U,SUM', '*GET,A_,SORT,0,MAX', '*GET,AN_,SORT,0,IMAX', 'NUSORT', '*VWRITE,K_,F_,A_,AN_',
              '(F8.0,E22.13,E22.13,F12.0)', '*ENDDO', '*CFCLOS']
    if kind in ('modal', 'buckle'):
        # which way each mode moves: the largest and smallest UX, UY, UZ of the whole model, first MAX_MODES sets
        L += ['NM_=NSET_', '*IF,NM_,GT,%d,THEN' % MAX_MODES, 'NM_=%d' % MAX_MODES, '*ENDIF',
              '*CFOPEN,post_modes,txt', '*DO,K_,1,NM_', 'SET,,,,,,,K_', '*GET,F_,ACTIVE,0,SET,FREQ', 'ALLSEL,ALL']
        for c in 'XYZ':
            L += ['NSORT,U,%s' % c, '*GET,%sA_,SORT,0,MAX' % c, '*GET,%sB_,SORT,0,MIN' % c, 'NUSORT']
        L += ['*VWRITE,K_,F_,XA_,XB_,YA_,YB_,ZA_,ZB_', '(F8.0,7E22.13)', '*ENDDO', '*CFCLOS']
    # the set the main summary reads: the first mode (modal, buckling); the end of the asked load step or the last
    # set (static, transient, harmonic).  post_main.txt says which one it was
    if kind in ('modal', 'buckle'):
        L += ['SET,FIRST']
    elif step and kind in ('static', 'transient'):
        L += ['SET,%d,LAST' % int(step)]
    else:
        L += ['SET,LAST']
    L += ['*GET,MLS_,ACTIVE,0,SET,LSTP', '*GET,MSB_,ACTIVE,0,SET,SBST', '*GET,MT_,ACTIVE,0,SET,TIME',
          '*CFOPEN,post_main,txt', '*VWRITE,MLS_,MSB_,MT_', '(F8.0,F8.0,E22.13)', '*CFCLOS']
    L += _block('', kind, comps, sel)
    if lins and kind in ('static', 'transient'):
        L += _lin_block(lins)
    # static, transient with several load steps: the same for the end of every load step (post_step<N>.txt says which
    # set it was; a load step without results on the file is skipped)
    if kind in ('static', 'transient') and nls and int(nls) > 1:
        for n in range(1, min(int(nls), MAX_STEPS) + 1):
            L += ['SET,%d,LAST' % n, '*GET,LSC_,ACTIVE,0,SET,LSTP', '*GET,SBC_,ACTIVE,0,SET,SBST',
                  '*GET,TC_,ACTIVE,0,SET,TIME', '*IF,LSC_,EQ,%d,THEN' % n,
                  '*CFOPEN,post_step%d,txt' % n, '*VWRITE,LSC_,SBC_,TC_', '(F8.0,F8.0,E22.13)', '*CFCLOS']
            L += _block('_ls%d' % n, kind, comps, sel) + ['*ENDIF']
    L += ['FINISH', '/EXIT,NOSAVE']
    return '\n'.join(L) + '\n'


def _rows(path):
    out = []
    try:
        with open(path, encoding='latin-1') as f:
            for line in f:
                t = line.split()
                if not t:
                    continue
                try:
                    out.append([float(x) for x in t])
                except ValueError:
                    continue
    except OSError:
        return None
    return out


def read_totals(path):
    """PRRSOL listing -> {label: total} (the TOTAL VALUES lines)"""
    try:
        with open(path, encoding='latin-1') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    labels, tot = None, {}
    for i, line in enumerate(lines):
        s = line.split()
        if s and s[0] == 'NODE' and len(s) > 1:
            labels = s[1:]
        if line.strip().startswith('TOTAL VALUES'):
            vals = []
            for l in lines[i + 1:i + 4]:
                t = l.replace('VALUE', '').split()
                try:
                    vals.extend(float(x) for x in t)
                except ValueError:
                    break
            if labels:
                for lab, v in zip(labels, vals):
                    tot[lab] = tot.get(lab, 0.0) + v
    return tot


def _parse_block(rundir, sfx, comps):
    res = {}
    u = _rows(os.path.join(rundir, 'post_u%s.txt' % sfx))
    if u and len(u[0]) >= 8:
        r = u[0]
        res['umax'] = {'value': r[0], 'node': int(r[1]), 'u': r[2:5], 'xyz': r[5:8]}
    tot = read_totals(os.path.join(rundir, 'post_react%s.txt' % sfx))
    if tot:
        res['reaction'] = tot
    for c in comps:
        cr = _rows(os.path.join(rundir, 'post_comp_%s%s.txt' % (c, sfx)))
        if cr and len(cr[0]) >= 6:
            r = cr[0]
            res.setdefault('comps', {})[c] = {'nodes': int(r[0]), 'fsum': r[1:4], 'umax': r[4], 'node': int(r[5])}
        cs = _rows(os.path.join(rundir, 'post_cs_%s%s.txt' % (c, sfx)))
        if cs and len(cs) == len(STRESS_ITEMS) and all(len(r) >= 4 for r in cs) and c in res.get('comps', {}):
            if all(int(r[1]) == 0 and int(r[3]) == 0 for r in cs):
                # no node of the component carries a nodal stress (line elements: BEAM188, LINK180 report theirs
                # per element); MAPDL then answers 0 at node 0
                res['comps'][c]['stress'] = None
            else:
                res['comps'][c]['stress'] = {('S' + it): {'max': r[0], 'maxNode': int(r[1]), 'min': r[2],
                                                          'minNode': int(r[3])} for it, r in zip(STRESS_ITEMS, cs)}
    s = _rows(os.path.join(rundir, 'post_s%s.txt' % sfx))
    if s and len(s[0]) >= 2:
        res['seqv'] = {'value': s[0][0], 'node': int(s[0][1])}
    return res


_COMP_HDR = ['SX', 'SY', 'SZ', 'SXY', 'SYZ', 'SXZ']
_PRIN_HDR = ['S1', 'S2', 'S3', 'SINT', 'SEQV']
_SECTIONS = (('** MEMBRANE PLUS BENDING', 'mb'), ('** MEMBRANE', 'membrane'), ('** BENDING', 'bending'),
             ('** PEAK', 'peak'), ('** TOTAL', 'total'))


def read_prsect(path):
    """PRSECT listing -> {'inside': node, 'outside': node, 'membrane': {SX.. SXZ, S1, S2, S3, SINT, SEQV},
    'bending' / 'mb' / 'peak' / 'total': {'I': {...}, 'C': {...}, 'O': {...}}}; None if there is no listing"""
    try:
        with open(path, encoding='latin-1') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    res, sec, hdr = {}, None, None
    for line in lines:
        s = line.strip()
        m = re.search(r'INSIDE NODE\s*=\s*(\d+)\s+OUTSIDE NODE\s*=\s*(\d+)', s)
        if m:
            res['inside'], res['outside'] = int(m.group(1)), int(m.group(2))
            continue
        hit = next((key for tag, key in _SECTIONS if s.startswith(tag)), None)
        if hit:
            sec, hdr = hit, None
            continue
        t = s.split()
        if not t or sec is None:
            continue
        if t[:6] == _COMP_HDR:
            hdr = _COMP_HDR
            continue
        if t[:5] == _PRIN_HDR:
            hdr = _PRIN_HDR
            continue
        if hdr is None:
            continue
        where = None
        if t[0] in ('I', 'C', 'O'):
            where, t = t[0], t[1:]
        try:
            vals = [float(x) for x in t[:len(hdr)]]
        except ValueError:
            continue
        if len(vals) < len(hdr):
            continue
        d = dict(zip(hdr, vals))
        if sec == 'membrane':
            res.setdefault('membrane', {}).update(d)
        elif where:
            res.setdefault(sec, {}).setdefault(where, {}).update(d)
    return res if 'membrane' in res else None


def parse_lins(rundir, lins):
    """the linearization of each line asked for: given points, nodes taken, their places and distances, PRSECT"""
    out = []
    for k, (name, p1, p2) in enumerate(lins[:MAX_LINES], 1):
        d = {'name': name, 'given': [list(map(float, p1)), list(map(float, p2))]}
        nd = _rows(os.path.join(rundir, 'post_lin%d_nodes.txt' % k))
        if nd and len(nd[0]) >= 8:
            r = nd[0]
            d['nodes'] = [int(r[0]), int(r[4])]
            d['xyz'] = [r[1:4], r[5:8]]
            d['snap'] = [sum((a - b) ** 2 for a, b in zip(d['xyz'][i], d['given'][i])) ** 0.5 for i in (0, 1)]
            d['length'] = sum((a - b) ** 2 for a, b in zip(d['xyz'][0], d['xyz'][1])) ** 0.5
            d['givenLength'] = sum((a - b) ** 2 for a, b in zip(p1, p2)) ** 0.5
        ps = read_prsect(os.path.join(rundir, 'post_lin%d.txt' % k))
        if ps:
            d.update(ps)
        out.append(d)
    return out


def parse(rundir, comps=(), nls=1, lins=()):
    res = {}
    sets = _rows(os.path.join(rundir, 'post_sets.txt'))
    if sets is not None:
        res['sets'] = [{'set': int(r[0]), 'step': int(r[1]), 'substep': int(r[2]), 'time': r[3], 'freq': r[4]}
                       for r in sets if len(r) >= 5]
    m = _rows(os.path.join(rundir, 'post_main.txt'))
    if m and len(m[0]) >= 3:
        res['main'] = {'step': int(m[0][0]), 'substep': int(m[0][1]), 'time': m[0][2]}
    md = _rows(os.path.join(rundir, 'post_modes.txt'))
    if md:
        res['modes'] = []
        for r in md:
            if len(r) < 8:
                continue
            um = [max(abs(r[i]), abs(r[i + 1])) for i in (2, 4, 6)]
            res['modes'].append({'mode': int(r[0]), 'freq': r[1], 'umax': um, 'dominant': 'XYZ'[um.index(max(um))]})
    amp = _rows(os.path.join(rundir, 'post_amp.txt'))
    if amp:
        res['amplitude'] = [{'set': int(r[0]), 'freq': r[1], 'umax': r[2], 'node': int(r[3])} for r in amp if len(r) >= 4]
    res.update(_parse_block(rundir, '', comps))
    if lins:
        res['lin'] = parse_lins(rundir, lins)
    steps = []
    for n in range(1, min(int(nls or 1), MAX_STEPS) + 1):
        st = _rows(os.path.join(rundir, 'post_step%d.txt' % n))
        if not st or len(st[0]) < 3:
            continue
        d = {'step': n, 'substep': int(st[0][1]), 'time': st[0][2]}
        d.update(_parse_block(rundir, '_ls%d' % n, comps))
        steps.append(d)
    if steps:
        res['steps'] = steps
    return res


def kind_of(antypes):
    """last analysis type of the solve segment -> post kind"""
    last = antypes[-1] if antypes else 0
    return {0: 'static', 1: 'buckle', 2: 'modal', 3: 'harmonic', 4: 'transient'}.get(last, 'static')
