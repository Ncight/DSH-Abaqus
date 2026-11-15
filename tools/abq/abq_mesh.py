# -*- coding: utf-8 -*-
"""网格质量体检（abq mesh 的计算部分）。读 INP 里求解器真正看到的网格，逐个单元算质量指标。

指标（与 Abaqus/CAE "Verify Mesh" 同名的按它的定义算）：
  aspect   长宽比 = 最长边 / 最短边
  angle    面内角（每个面的每个角）最小值、最大值
  shape    形状因子（仅三角形 / 四面体）= 面积或体积 / 同外接圆（球）半径的正三角形（正四面体）的面积（体积）
  sjac     缩放雅可比（最小角点值）：理想单元为 1，<=0 为翻转 / 退化，求解器会拒绝
  warp     翘曲角（仅四边形壳）：按两条对角线各分成两个三角形，两三角形法向夹角取较大者

两档阈值：
  ABAQUS  Abaqus/CAE 检查网格的默认值，超出 = Abaqus 自己会标黄的单元
  GOOD    常用工程推荐值，超出 = 能算但质量一般；达标率用来比较两套网格

需要 numpy。没有副作用，可单独 import。
"""
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'preview'))
import inp_model  # noqa: E402

# ---------------------------------------------------------------- 阈值
# 键: (指标, 族组) -> (方向, 值)；方向 'min' 表示指标不得小于该值，'max' 表示不得大于
ABAQUS = {
    ('aspect', 'all'): ('max', 10.0),
    ('angle_min', 'q'): ('min', 10.0), ('angle_max', 'q'): ('max', 160.0),
    ('angle_min', 't'): ('min', 5.0), ('angle_max', 't'): ('max', 170.0),
    ('shape', 't'): ('min', 1e-4),
}
GOOD = {
    ('aspect', 'all'): ('max', 5.0),
    ('angle_min', 'q'): ('min', 45.0), ('angle_max', 'q'): ('max', 135.0),
    ('angle_min', 't'): ('min', 20.0), ('angle_max', 't'): ('max', 120.0),
    ('sjac', 'q'): ('min', 0.5),
    ('sjac', 'tet'): ('min', 0.2),
    ('shape', 'tri'): ('min', 0.5),
    ('warp', 'quad'): ('max', 10.0),
}
# 族组：q = 四边形面为主的单元（hex/wedge/pyr/quad/quad2d），t = 三角形面单元（tet/tri/tri2d）
GROUP = {'hex': 'q', 'wedge': 'q', 'pyr': 'q', 'quad': 'q', 'quad2d': 'q', 'tet': 't', 'tri': 't', 'tri2d': 't'}
FAM_CN = {'hex': '六面体', 'wedge': '楔形', 'pyr': '金字塔', 'tet': '四面体', 'quad': '四边形壳', 'tri': '三角形壳',
          'quad2d': '四边形平面', 'tri2d': '三角形平面', 'line': '梁/杆', 'point': '点单元', 'other': '其他'}
METRIC_CN = {'aspect': '长宽比', 'angle_min': '最小角', 'angle_max': '最大角', 'shape': '形状因子',
             'sjac': '缩放雅可比', 'warp': '翘曲角'}
SKIP_PREFIX = ('COH', 'GK', 'R3D', 'R2D', 'RAX', 'IRS', 'ITSUNI', 'ITSCYL', 'DRAG', 'SPRING', 'DASHPOT', 'CONN',
               'MASS', 'ROTARYI', 'HEATCAP', 'SFM', 'M3D', 'MAX', 'MGAX')   # 界面/刚体/连接/面单元：不评质量

# ---------------------------------------------------------------- 单元拓扑（角点局部编号）
HEX_CORNER = ((1, 3, 4), (2, 0, 5), (3, 1, 6), (0, 2, 7), (7, 5, 0), (4, 6, 1), (5, 7, 2), (6, 4, 3))
WEDGE_CORNER = ((1, 2, 3), (2, 0, 4), (0, 1, 5), (5, 4, 0), (3, 5, 1), (4, 3, 2))
PYR_CORNER = ((1, 3, 4), (2, 0, 4), (3, 1, 4), (0, 2, 4))
TET_CORNER = ((1, 2, 3), (2, 0, 3), (0, 1, 3), (0, 2, 1))
EDGES = {
    'hex': ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)),
    'wedge': ((0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)),
    'pyr': ((0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)),
    'tet': ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)),
    'quad': ((0, 1), (1, 2), (2, 3), (3, 0)), 'quad2d': ((0, 1), (1, 2), (2, 3), (3, 0)),
    'tri': ((0, 1), (1, 2), (2, 0)), 'tri2d': ((0, 1), (1, 2), (2, 0)),
}
FACES = {
    'hex': ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)),
    'wedge': ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
    'pyr': ((0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
    'tet': ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)),
    'quad': ((0, 1, 2, 3),), 'quad2d': ((0, 1, 2, 3),), 'tri': ((0, 1, 2),), 'tri2d': ((0, 1, 2),),
}
SOLID = ('hex', 'wedge', 'pyr', 'tet')


def _norm(v):
    return np.sqrt(np.einsum('...i,...i->...', v, v))


def _det(a, b, c):
    return np.einsum('...i,...i->...', a, np.cross(b, c))


def _face_angles(X, faces):
    """所有面所有角的内角（度）-> (min, max) 每单元"""
    amin = np.full(X.shape[0], 180.0)
    amax = np.zeros(X.shape[0])
    for f in faces:
        n = len(f)
        fn = None
        if n == 4:      # 四边形面：用对角线叉积作面法向，凹角（>180°）按 360° 减去计，与 Abaqus 一致
            fn = np.cross(X[:, f[2]] - X[:, f[0]], X[:, f[3]] - X[:, f[1]])
        for i in range(n):
            p, a, b = X[:, f[i]], X[:, f[i - 1]], X[:, f[(i + 1) % n]]
            u, v = a - p, b - p
            c = np.einsum('ij,ij->i', u, v) / np.maximum(_norm(u) * _norm(v), 1e-300)
            ang = np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))
            if fn is not None:
                reflex = np.einsum('ij,ij->i', np.cross(v, u), fn) < 0
                ang = np.where(reflex, 360.0 - ang, ang)
            amin = np.minimum(amin, ang)
            amax = np.maximum(amax, ang)
    return amin, amax


def _edge_lengths(X, edges):
    L = np.stack([_norm(X[:, b] - X[:, a]) for a, b in edges], axis=1)
    return L


def _corner_sjac(X, table, norm=1.0):
    vals = []
    dets = []
    for i, (a, b, c) in enumerate(table):
        e1, e2, e3 = X[:, a] - X[:, i], X[:, b] - X[:, i], X[:, c] - X[:, i]
        d = _det(e1, e2, e3)
        dets.append(d)
        vals.append(d / np.maximum(_norm(e1) * _norm(e2) * _norm(e3), 1e-300) * norm)
    return np.min(np.stack(vals, axis=1), axis=1), np.stack(dets, axis=1)


HEX_NAT = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)


def _hex_jdet(X, xi, eta, zeta):
    """八节点六面体在自然坐标 (xi, eta, zeta) 处的雅可比行列式"""
    s = HEX_NAT
    dN = np.stack([s[:, 0] * (1 + s[:, 1] * eta) * (1 + s[:, 2] * zeta),
                   s[:, 1] * (1 + s[:, 0] * xi) * (1 + s[:, 2] * zeta),
                   s[:, 2] * (1 + s[:, 0] * xi) * (1 + s[:, 1] * eta)], 0) / 8.0     # (3, 8)
    J = np.einsum('ka,nad->nkd', dN, X)                                                 # (n, 3, 3)
    return np.linalg.det(J)


def metrics(fam, X):
    """X: (n, ncorner, 3) 角点坐标 -> dict 指标名 -> (n,) 数组；另含 size（特征尺寸）与 measure（体积或面积）"""
    out = {}
    L = _edge_lengths(X, EDGES[fam])
    out['aspect'] = L.max(axis=1) / np.maximum(L.min(axis=1), 1e-300)
    out['size'] = L.mean(axis=1)
    out['angle_min'], out['angle_max'] = _face_angles(X, FACES[fam])
    if fam == 'hex':
        out['sjac'], d = _corner_sjac(X, HEX_CORNER)
        out['measure'] = d.mean(axis=1)                       # 平行六面体时精确
        g = 1.0 / math.sqrt(3.0)
        out['jc'] = _hex_jdet(X, 0.0, 0.0, 0.0)
        out['jg'] = np.min(np.stack([_hex_jdet(X, a * g, b * g, c * g) for a in (-1, 1) for b in (-1, 1)
                                     for c in (-1, 1)], 1), 1)
    elif fam == 'wedge':
        out['sjac'], d = _corner_sjac(X, WEDGE_CORNER, 2.0 / math.sqrt(3.0))
        out['measure'] = d.mean(axis=1) / 2.0
        out['jc'] = _det((X[:, 1] - X[:, 0] + X[:, 4] - X[:, 3]) / 2.0, (X[:, 2] - X[:, 0] + X[:, 5] - X[:, 3]) / 2.0,
                         (X[:, 3] - X[:, 0] + X[:, 4] - X[:, 1] + X[:, 5] - X[:, 2]) / 3.0)
    elif fam == 'pyr':
        out['sjac'], d = _corner_sjac(X, PYR_CORNER, math.sqrt(2.0))
        out['measure'] = d.mean(axis=1) / 3.0
    elif fam == 'tet':
        a, b, c = X[:, 1] - X[:, 0], X[:, 2] - X[:, 0], X[:, 3] - X[:, 0]
        det = _det(a, b, c)
        corner = []                                           # 每个角点三条边长的乘积
        for i, (p, q, r) in enumerate(TET_CORNER):
            corner.append(_norm(X[:, p] - X[:, i]) * _norm(X[:, q] - X[:, i]) * _norm(X[:, r] - X[:, i]))
        out['sjac'] = det * math.sqrt(2.0) / np.maximum(np.max(np.stack(corner, 1), 1), 1e-300)
        num = (np.einsum('ij,ij->i', a, a)[:, None] * np.cross(b, c) + np.einsum('ij,ij->i', b, b)[:, None] *
               np.cross(c, a) + np.einsum('ij,ij->i', c, c)[:, None] * np.cross(a, b))
        R = _norm(num) / np.maximum(2.0 * np.abs(det), 1e-300)
        V = det / 6.0
        Videal = (4.0 * R / math.sqrt(6.0)) ** 3 / (6.0 * math.sqrt(2.0))
        out['shape'] = V / np.maximum(Videal, 1e-300)
        out['measure'] = V
    elif fam in ('quad', 'quad2d'):
        d1, d2 = X[:, 2] - X[:, 0], X[:, 3] - X[:, 1]
        nrm = np.cross(d1, d2)
        area2 = _norm(nrm)
        nrm = nrm / np.maximum(area2, 1e-300)[:, None]
        if fam == 'quad2d':
            nrm = np.tile(np.array([0.0, 0.0, 1.0]), (X.shape[0], 1)) * np.sign(nrm[:, 2:3] + 1e-300)
        vals = []
        for i in range(4):
            e1, e2 = X[:, (i + 1) % 4] - X[:, i], X[:, (i - 1) % 4] - X[:, i]
            vals.append(np.einsum('ij,ij->i', np.cross(e1, e2), nrm) / np.maximum(_norm(e1) * _norm(e2), 1e-300))
        out['sjac'] = np.min(np.stack(vals, 1), 1)
        out['measure'] = area2 / 2.0
        if fam == 'quad':
            w = np.zeros(X.shape[0])
            for (p, q, r), (s, t, u) in (((0, 1, 2), (0, 2, 3)), ((0, 1, 3), (1, 2, 3))):
                n1 = np.cross(X[:, q] - X[:, p], X[:, r] - X[:, p])
                n2 = np.cross(X[:, t] - X[:, s], X[:, u] - X[:, s])
                c = np.einsum('ij,ij->i', n1, n2) / np.maximum(_norm(n1) * _norm(n2), 1e-300)
                w = np.maximum(w, np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))
            out['warp'] = w
    elif fam in ('tri', 'tri2d'):
        a, b = X[:, 1] - X[:, 0], X[:, 2] - X[:, 0]
        A = _norm(np.cross(a, b)) / 2.0
        if fam == 'tri2d':
            A = np.cross(a, b)[:, 2] / 2.0                    # 带符号：负值为翻转
        la, lb, lc = _norm(a), _norm(b), _norm(X[:, 2] - X[:, 1])
        R = la * lb * lc / np.maximum(4.0 * np.abs(A), 1e-300)
        Aideal = math.sqrt(3.0) / 4.0 * 3.0 * R ** 2
        out['shape'] = A / np.maximum(Aideal, 1e-300)
        out['sjac'] = out['shape']                            # 三角形：两者都按正三角形 = 1 归一，<=0 退化
        out['measure'] = A
    return out


def _full_integration(tname):
    """完全积分的实体单元（C3D8、C3D20、C3D8H、C3D8I …），减缩积分带 R（C3D8R、C3D20RH）"""
    m = re.match(r'^[A-Z]*?3D\d+([A-Z]*)', tname.upper())
    return bool(m) and 'R' not in m.group(1)


def _limits(table, metric, fam):
    for key in ((metric, fam), (metric, GROUP.get(fam, '')), (metric, 'all')):
        if key in table:
            return table[key]
    return None


def _fails(table, metric, fam, v):
    lim = _limits(table, metric, fam)
    if lim is None:
        return None
    sense, val = lim
    return (v < val) if sense == 'min' else (v > val)


def corners_of(model, e, fam, ncorner):
    perm = inp_model.corner_perm(model.types[model.mesh.e_code[e]])
    conn = model.e_conn[e]
    if perm:
        return [conn[i] for i in perm[:ncorner]]
    return list(conn[:ncorner])


def analyze(path, force=False, worst=10, progress=None):
    """-> 结果 dict（可直接写 JSON）。force: 超过预览面板自动上限的大文件也读。"""
    model = inp_model.Model(path, force=True, max_auto_mb=None) if force else inp_model.Model(path)
    mesh = model.mesh
    xyz = np.frombuffer(mesh.xyz, dtype=np.float64).reshape(-1, 3) if len(mesh.xyz) else np.zeros((0, 3))
    e_code = np.frombuffer(mesh.e_code, dtype=np.uint16) if len(mesh.e_code) else np.zeros(0, np.uint16)
    e_off = np.frombuffer(mesh.e_off, dtype=np.int32)
    e_nodes = np.frombuffer(mesh.e_nodes, dtype=np.int32) if len(mesh.e_nodes) else np.zeros(0, np.int32)
    e_inst = np.frombuffer(mesh.e_inst, dtype=np.int32) if len(mesh.e_inst) else np.zeros(0, np.int32)
    rigid = model.rigid_elems
    display = model.display_ctx
    inst_name = {c.index: (c.name or '(装配/无部件)') for c in model.ctxs}

    # 按单元类型分组
    by_type = {}
    for code, tname in enumerate(model.types):
        if code == 0:
            continue
        idx = np.nonzero(e_code == code)[0]
        if len(idx):
            by_type[tname] = (code, idx)

    fam_count = {}
    type_count = {}
    skipped = {}
    order = {'first': 0, 'second': 0}
    groups = []          # (fam, element indices, corner node index array)
    for tname, (code, idx) in sorted(by_type.items()):
        if display:
            idx = idx[~np.isin(e_inst[idx], list(display))]
        if rigid:
            idx = idx[~np.isin(idx, np.fromiter(rigid, dtype=np.int64, count=len(rigid)))]
        if not len(idx):
            continue
        fam, nc = inp_model.classify(tname)
        type_count[tname] = int(len(idx))
        t = tname.upper()
        if fam is None or t.startswith(SKIP_PREFIX):
            if fam in ('line', 'point'):
                fam_count[fam] = fam_count.get(fam, 0) + len(idx)
            else:
                skipped[tname] = int(len(idx))
            continue
        if fam in ('line', 'point'):
            fam_count[fam] = fam_count.get(fam, 0) + len(idx)
            continue
        nn = int(e_off[idx[0] + 1] - e_off[idx[0]])
        order['second' if nn > nc else 'first'] += len(idx)
        perm = inp_model.corner_perm(tname)
        sel = list(perm[:nc]) if perm else list(range(nc))
        starts = e_off[idx]
        lens = e_off[idx + 1] - starts
        ok = lens >= max(sel) + 1
        idx, starts = idx[ok], starts[ok]
        conn = np.stack([e_nodes[starts + k] for k in sel], axis=1)
        fam_count[fam] = fam_count.get(fam, 0) + len(idx)
        groups.append((fam, idx, conn, _full_integration(tname)))

    # 合并同族
    merged = {}
    full_of = np.zeros(len(e_code), dtype=bool)
    for fam, idx, conn, full in groups:
        full_of[idx] = full
        if fam in merged:
            merged[fam] = (np.concatenate([merged[fam][0], idx]), np.concatenate([merged[fam][1], conn]))
        else:
            merged[fam] = (idx, conn)

    n_elem_q = sum(len(v[0]) for v in merged.values())
    res = {'file': os.path.abspath(path), 'nodes': int(len(xyz)), 'elements': int(len(e_code)),
           'evaluated': int(n_elem_q), 'families': {}, 'types': type_count, 'skipped': skipped,
           'order': order, 'metrics': {}, 'instances': {}, 'worst': [], 'size': {}, 'verdict': ''}
    for fam, n in fam_count.items():
        res['families'][fam] = int(n)

    all_size = np.zeros(len(e_code))
    has_size = np.zeros(len(e_code), dtype=bool)
    bad_abq = np.zeros(len(e_code), dtype=bool)
    bad_good = np.zeros(len(e_code), dtype=bool)
    inverted = np.zeros(len(e_code), dtype=bool)
    score = np.full(len(e_code), np.inf)       # 越小越差，用于挑最差单元
    why = {}
    measure = {}
    for fam, (idx, conn) in merged.items():
        if progress:
            progress('%s %d' % (fam, len(idx)))
        X = xyz[conn]
        m = metrics(fam, X)
        all_size[idx] = m['size']
        has_size[idx] = True
        measure[fam] = float(np.abs(m['measure']).sum())
        # 翻转 = Abaqus 分析检查判为错误的单元：六面体看 2x2x2 积分点处的雅可比（减缩积分单元也一样，
        # 与 Abaqus/CAE 的检查结果逐个核对过），其余看角点 / 形心
        if fam == 'hex':
            inv = m['jg'] <= 0
        elif fam == 'wedge':
            inv = m['jc'] <= 0
        elif fam in ('tet', 'pyr', 'quad2d'):
            inv = m['sjac'] <= 0
        elif fam == 'tri2d':
            inv = m['measure'] <= 0
        else:                                   # 壳：法向随单元走，只有面积退化为 0 才算
            inv = np.abs(m['measure']) <= 1e-12 * np.maximum(m['size'], 1e-300) ** 2
        inverted[idx] |= inv
        cneg = (m['sjac'] <= 0) & ~inv          # 角点雅可比为负：严重畸变（凹角/折叠），求解器一般给警告
        bad_abq[idx[cneg]] = True
        famres = {}
        for key in ('aspect', 'angle_min', 'angle_max', 'shape', 'sjac', 'warp'):
            if key not in m:
                continue
            v = m[key]
            fa = _fails(ABAQUS, key, fam, v)
            fg = _fails(GOOD, key, fam, v)
            worst_v = float(v.min() if key in ('angle_min', 'shape', 'sjac') else v.max())
            q = 1 if key in ('angle_min', 'shape', 'sjac') else 99
            r = {'worst': worst_v, 'p%d' % q: float(np.percentile(v, q)), 'median': float(np.median(v))}
            if fa is not None:
                r['abaqus_limit'] = _limits(ABAQUS, key, fam)[1]
                r['abaqus_fail'] = int(fa.sum())
                bad_abq[idx[fa]] = True
            if fg is not None:
                r['good_limit'] = _limits(GOOD, key, fam)[1]
                r['good_fail'] = int(fg.sum())
                bad_good[idx[fg]] = True
                lim = _limits(GOOD, key, fam)
                # 归一化“离达标有多远”：<1 表示不达标，越小越差
                rel = (v / lim[1]) if lim[0] == 'min' else (lim[1] / np.maximum(v, 1e-300))
                better = rel < score[idx]
                score[idx[better]] = rel[better]
                why.setdefault(fam, []).append((key, idx[better], v[better]))
            famres[key] = r
        famres['inverted'] = int(inv.sum())
        famres['corner_negative'] = int(cneg.sum())
        res['metrics'][fam] = famres

    # 最差单元：score 最小的 N 个；原因 = 让它最差的那项指标
    cand = np.nonzero(np.isfinite(score) | inverted)[0]
    if len(cand):
        sc = np.where(inverted[cand], -1.0, score[cand])
        pick = cand[np.argsort(sc, kind='stable')[:worst]]
        reason = {}
        want = set(pick.tolist())
        for fam, lst in why.items():
            for key, wk, vals in lst:          # 按 score 更新顺序，后写覆盖先写
                hit = np.isin(wk, pick)
                for e, val in zip(wk[hit].tolist(), vals[hit].tolist()):
                    if e in want:
                        reason[e] = (key, val)
        for e in pick.tolist():
            metric, val = reason.get(e, ('sjac', float('nan')))
            conn = model.e_conn[e]
            cen = xyz[list(conn)].mean(axis=0)
            res['worst'].append({'instance': inst_name.get(int(e_inst[e]), ''), 'label': int(mesh.e_label[e]),
                                 'type': model.types[mesh.e_code[e]], 'metric': metric, 'value': float(val),
                                 'inverted': bool(inverted[e]),
                                 'centroid': [float(c) for c in cen]})

    # 尺寸与相邻单元尺寸突变（节点处相连单元的最大/最小特征尺寸之比）
    ev = np.nonzero(has_size)[0]
    if len(ev):
        s = all_size[ev]
        res['size'] = {'min': float(s.min()), 'median': float(np.median(s)), 'max': float(s.max())}
        hmin = np.full(len(xyz), np.inf)
        hmax = np.zeros(len(xyz))
        for fam, (idx, conn) in merged.items():
            h = np.repeat(all_size[idx], conn.shape[1])
            nodes = conn.ravel()
            np.minimum.at(hmin, nodes, h)
            np.maximum.at(hmax, nodes, h)
        used = np.isfinite(hmin) & (hmin > 0)
        ratio = hmax[used] / hmin[used]
        res['size']['jump_max'] = float(ratio.max()) if len(ratio) else 1.0
        res['size']['jump_gt2_pct'] = float((ratio > 2.0).mean() * 100) if len(ratio) else 0.0
        res['size']['jump_gt3_pct'] = float((ratio > 3.0).mean() * 100) if len(ratio) else 0.0

    # 壳的自由边（只属于一个单元的边）：真实开口（对称面、进出口）之外的自由边就是没连上的缝
    shell_edges = []
    for fam in ('quad', 'tri'):
        if fam in merged:
            idx, conn = merged[fam]
            n = conn.shape[1]
            for k in range(n):
                shell_edges.append(np.stack([conn[:, k], conn[:, (k + 1) % n]], 1))
    if shell_edges:
        E = np.sort(np.concatenate(shell_edges), axis=1).astype(np.int64)
        key = E[:, 0] * (len(xyz) + 1) + E[:, 1]
        uk, inv_k, cnt = np.unique(key, return_inverse=True, return_counts=True)
        first = np.zeros(len(uk), dtype=np.int64)
        first[inv_k[::-1]] = np.arange(len(key))[::-1]
        ue = E[first]
        fe = ue[cnt == 1]
        res['shell_edges'] = {'edges': int(len(uk)), 'free': int(len(fe)), 'nonmanifold': int((cnt > 2).sum())}
        if len(fe):
            seg_len = _norm(xyz[fe[:, 1]] - xyz[fe[:, 0]])
            res['shell_edges']['free_length'] = float(seg_len.sum())
            # 连成条：并查集
            parent = {}

            def find(a):
                while parent.get(a, a) != a:
                    parent[a] = parent.get(parent[a], parent[a])
                    a = parent[a]
                return a
            for a, b in fe.tolist():
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
            roots = np.array([find(a) for a in fe[:, 0].tolist()])
            loops = []
            for r in np.unique(roots):
                m = roots == r
                pts = xyz[np.unique(fe[m].ravel())]
                loops.append({'segments': int(m.sum()), 'length': float(seg_len[m].sum()),
                              'lo': [float(v) for v in pts.min(0)], 'hi': [float(v) for v in pts.max(0)]})
            loops.sort(key=lambda d: -d['length'])
            res['shell_edges']['loops'] = len(loops)
            res['shell_edges']['loop_list'] = loops[:50]
            # 自由边上重合却没合并的节点：两块网格各自划、没连上的直接证据
            fn = np.unique(fe.ravel())
            P = xyz[fn]
            span = float(np.max(P.max(0) - P.min(0))) if len(P) else 1.0
            tol = max(span * 1e-6, 1e-9)
            q = np.round(P / tol).astype(np.int64)
            _, ci_, cc_ = np.unique(q, axis=0, return_inverse=True, return_counts=True)
            res['shell_edges']['coincident_nodes'] = int((cc_[ci_.ravel()] > 1).sum())

    # 按实例
    for ci in np.unique(e_inst[ev]) if len(ev) else []:
        sel = ev[e_inst[ev] == ci]
        fams = {}
        for fam, (idx, conn) in merged.items():
            k = int(np.isin(idx, sel).sum())
            if k:
                fams[fam] = k
        res['instances'][inst_name.get(int(ci), str(ci))] = {
            'elements': int(len(sel)), 'families': fams,
            'abaqus_fail': int(bad_abq[sel].sum()), 'good_fail': int(bad_good[sel].sum()),
            'inverted': int(inverted[sel].sum())}

    n_inv = int(inverted.sum())
    n_abq = int(bad_abq.sum())
    n_good = int(bad_good.sum())
    res['inverted'] = n_inv
    res['abaqus_fail'] = n_abq
    res['good_fail'] = n_good
    res['good_pct'] = float(100.0 * (1 - n_good / n_elem_q)) if n_elem_q else 0.0
    solid_n = sum(fam_count.get(f, 0) for f in SOLID)
    if solid_n:
        res['hex_pct'] = float(100.0 * fam_count.get('hex', 0) / solid_n)
        vs = sum(measure.get(f, 0.0) for f in SOLID)
        res['hex_vol_pct'] = float(100.0 * measure.get('hex', 0.0) / vs) if vs else 0.0
    shell_n = fam_count.get('quad', 0) + fam_count.get('tri', 0)
    if shell_n:
        res['quad_pct'] = float(100.0 * fam_count.get('quad', 0) / shell_n)
    if n_inv:
        res['verdict'] = 'FAIL'
    elif n_abq:
        res['verdict'] = 'WARN'
    else:
        res['verdict'] = 'PASS'
    res['criteria'] = {'abaqus': {'%s/%s' % k: v for k, v in ABAQUS.items()},
                       'good': {'%s/%s' % k: v for k, v in GOOD.items()}}
    return res
