# -*- coding: utf-8 -*-
"""Generic ODB summary for Abaqus (runs under `abaqus python`, i.e. Python 2.7 + odbAccess).

    abaqus python odb_extract.py <file.odb> <out.json> [--step NAME|INDEX] [--frame N]
                                 [--vars U,S,...] [--probe x,y,z ...] [--set NAME ...] [--series U,S,...] [--no-history]

Writes one JSON document:
  steps[]        name, procedure, domain, nFrames, timePeriod, totalTime, lastFrameValue
  frequencies[]  step, mode, frequency (cycles/time) for *Frequency steps
  eigenvalues[]  step, mode, value for *Buckle steps
  frame          the frame that `fields` describes (default: last frame of last non-eigen step)
  fields{}       per field: components max/min with location, magnitude, sums (RF, CF ...), invariants for tensors
  history{}      whole-model energies (last value, max abs) and derived ratios
  probes[]       nearest node/element to each requested point with a time series of the requested variables
  sets[]         every named node / element set (kind, holder, name, size)
  setResults[]   for each --set: per-field sum / mean / min / max over the set at the chosen frame, the node rows of small
                 node sets, and with --series the per-frame sum (RF, CF ...) or mean (other fields) over the set
All numbers are raw solver values in the model's own unit system.
"""
from __future__ import print_function

import json
import math
import re
import sys

import numpy as np
from abaqusConstants import (ELEMENT_NODAL, INTEGRATION_POINT, MAX_PRINCIPAL, MIN_PRINCIPAL, MISES, NODAL, PRESS,
                             TRESCA)
from odbAccess import openOdb

NODE_VECTORS = ('U', 'UR', 'RF', 'RM', 'CF', 'CM', 'V', 'A', 'VR', 'AR', 'TF')
TENSORS = ('S', 'E', 'LE', 'PE', 'EE', 'NE', 'PEEQ_TENSOR')
SCALARS = ('PEEQ', 'CPRESS', 'CSHEAR1', 'CSHEAR2', 'COPEN', 'CSLIP1', 'CSLIP2', 'NT11', 'TEMP', 'DAMAGEC', 'DAMAGET',
           'SDEG', 'STATUS', 'MISESMAX', 'PEMAG', 'CSTATUS', 'CNAREA', 'HFL', 'RFL11', 'POR', 'DMICRT', 'AC YIELD',
           'EVOL', 'STH', 'RD', 'PEEQT', 'ENER', 'ELSE', 'S_MISES')
SUM_FIELDS = ('RF', 'RM', 'CF', 'CM', 'TF', 'RFL11', 'CNAREA', 'EVOL')
DEFAULT_VARS = NODE_VECTORS + ('S', 'E', 'LE', 'PE') + SCALARS
ENERGY_KEYS = ('ALLIE', 'ALLKE', 'ALLSE', 'ALLPD', 'ALLAE', 'ALLVD', 'ALLWK', 'ALLFD', 'ALLCD', 'ALLSD', 'ALLDMD',
               'ALLCCE', 'ALLCCDW', 'ALLMW', 'ALLPW', 'ETOTAL')


def fnum(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def inst_name(block):
    try:
        return block.instance.name if block.instance is not None else ''
    except Exception:
        return ''


def node_coords(odb, iname, label):
    try:
        if iname:
            n = odb.rootAssembly.instances[iname].getNodeFromLabel(int(label))
        else:
            n = None
            for nd in odb.rootAssembly.nodes:
                if nd.label == int(label):
                    n = nd
                    break
        return [fnum(c) for c in n.coordinates] if n is not None else None
    except Exception:
        return None


def elem_centroid(odb, iname, label):
    try:
        inst = odb.rootAssembly.instances[iname]
        el = inst.getElementFromLabel(int(label))
        pts = [inst.getNodeFromLabel(n).coordinates for n in el.connectivity]
        return [fnum(sum(p[i] for p in pts) / len(pts)) for i in range(3)]
    except Exception:
        return None


def where(odb, block, i, deformed=None):
    """location dict for row i of a bulk block"""
    loc = {'instance': inst_name(block)}
    try:
        if block.nodeLabels is not None:
            loc['node'] = int(block.nodeLabels[i])
    except Exception:
        pass
    try:
        if block.elementLabels is not None:
            loc['element'] = int(block.elementLabels[i])
    except Exception:
        pass
    try:
        if block.integrationPoints is not None:
            loc['ip'] = int(block.integrationPoints[i])
    except Exception:
        pass
    if 'node' in loc:
        c = node_coords(odb, loc['instance'], loc['node'])
        if c:
            loc['coords'] = c
    elif 'element' in loc:
        c = elem_centroid(odb, loc['instance'], loc['element'])
        if c:
            loc['centroid'] = c
    return loc


def scan_scalar(odb, field):
    """max / min over all bulk blocks of a scalar-like field -> dict"""
    best_max = best_min = None
    n = 0
    total = 0.0
    for b in field.bulkDataBlocks:
        d = np.asarray(b.data, dtype=float)
        if d.size == 0:
            continue
        d = d.reshape(d.shape[0], -1)[:, 0]
        ok = np.isfinite(d)
        if not ok.any():
            continue
        n += int(ok.sum())
        total += float(d[ok].sum())
        dd = np.where(ok, d, -np.inf)
        i = int(dd.argmax())
        if best_max is None or dd[i] > best_max[0]:
            best_max = (float(dd[i]), b, i)
        dd = np.where(ok, d, np.inf)
        i = int(dd.argmin())
        if best_min is None or dd[i] < best_min[0]:
            best_min = (float(dd[i]), b, i)
    if best_max is None:
        return None
    return {'max': best_max[0], 'maxAt': where(odb, best_max[1], best_max[2]),
            'min': best_min[0], 'minAt': where(odb, best_min[1], best_min[2]), 'count': n, 'sum': total}


def scan_components(odb, field, want_sum=False):
    labels = list(field.componentLabels)
    out = {}
    ncomp = len(labels)
    stats = [None] * ncomp
    sums = np.zeros(ncomp)
    sums_pos = np.zeros(ncomp)
    sums_neg = np.zeros(ncomp)
    mag_best = None
    n = 0
    for b in field.bulkDataBlocks:
        d = np.asarray(b.data, dtype=float)
        if d.size == 0:
            continue
        d = d.reshape(d.shape[0], -1)
        n += d.shape[0]
        k = min(ncomp, d.shape[1])
        clean = np.where(np.isfinite(d), d, 0.0)
        sums[:k] += clean[:, :k].sum(axis=0)
        sums_pos[:k] += np.clip(clean[:, :k], 0.0, None).sum(axis=0)
        sums_neg[:k] += np.clip(clean[:, :k], None, 0.0).sum(axis=0)
        for c in range(k):
            col = clean[:, c]
            i1, i0 = int(col.argmax()), int(col.argmin())
            s = stats[c]
            if s is None:
                stats[c] = [float(col[i1]), (b, i1), float(col[i0]), (b, i0)]
            else:
                if col[i1] > s[0]:
                    s[0], s[1] = float(col[i1]), (b, i1)
                if col[i0] < s[2]:
                    s[2], s[3] = float(col[i0]), (b, i0)
        mag = np.sqrt((clean[:, :k] ** 2).sum(axis=1))
        im = int(mag.argmax())
        if mag_best is None or mag[im] > mag_best[0]:
            mag_best = (float(mag[im]), b, im)
    if n == 0:
        return None
    comps = {}
    for c, lab in enumerate(labels):
        s = stats[c]
        if s is None:
            continue
        comps[lab] = {'max': s[0], 'maxAt': where(odb, s[1][0], s[1][1]), 'min': s[2],
                      'minAt': where(odb, s[3][0], s[3][1])}
    out['components'] = comps
    out['count'] = n
    if mag_best is not None:
        out['maxMagnitude'] = {'value': mag_best[0], 'at': where(odb, mag_best[1], mag_best[2])}
    if want_sum:
        # sum = net total; sumPos / sumNeg = totals of the positive / negative nodal values separately
        # (when both ends of a body are restrained the net reaction is ~0 but sumPos = -sumNeg = the load)
        out['sum'] = dict((labels[c], float(sums[c])) for c in range(ncomp))
        out['sumPos'] = dict((labels[c], float(sums_pos[c])) for c in range(ncomp))
        out['sumNeg'] = dict((labels[c], float(sums_neg[c])) for c in range(ncomp))
    return out


def field_summary(odb, name, field):
    info = {'description': field.description, 'type': str(field.type)}
    try:
        info['positions'] = [str(l.position) for l in field.locations]
    except Exception:
        pass
    base = name.split(' ')[0]
    is_tensor = str(field.type).startswith('TENSOR')
    is_vector = str(field.type) == 'VECTOR'
    if is_tensor:
        info.update(scan_components(odb, field) or {})
        inv = {}
        for key, const in (('mises', MISES), ('maxPrincipal', MAX_PRINCIPAL), ('minPrincipal', MIN_PRINCIPAL),
                           ('pressure', PRESS), ('tresca', TRESCA)):
            try:
                r = scan_scalar(odb, field.getScalarField(invariant=const))
                if r:
                    r.pop('sum', None)
                    inv[key] = r
            except Exception:
                pass
        info['invariants'] = inv
        # values extrapolated to element nodes (what a contour plot shows at free surfaces / notches)
        try:
            sub = field.getSubset(position=ELEMENT_NODAL)
            en = {}
            for key, const in (('mises', MISES), ('maxPrincipal', MAX_PRINCIPAL), ('minPrincipal', MIN_PRINCIPAL)):
                try:
                    r = scan_scalar(odb, sub.getScalarField(invariant=const))
                except Exception:
                    # e.g. Mises is "Invalid invariant" for strain tensors (E, LE, PE): keep the principal values
                    continue
                if r:
                    r.pop('sum', None)
                    en[key] = r
            comp = scan_components(odb, sub)
            if comp:
                en['components'] = comp.get('components')
            info['elementNodal'] = en
        except Exception as e:
            info['elementNodalError'] = str(e)
    elif is_vector:
        info.update(scan_components(odb, field, want_sum=base in SUM_FIELDS) or {})
    else:
        r = scan_scalar(odb, field)
        if r:
            if base not in SUM_FIELDS:
                r.pop('sum', None)
            info.update(r)
    return info


def history_summary(odb, steps):
    out = {}
    series = {}
    for step in steps:
        for rname, region in step.historyRegions.items():
            if not rname.startswith('Assembly'):
                continue
            for key in ENERGY_KEYS:
                if key in region.historyOutputs.keys():
                    data = region.historyOutputs[key].data
                    if data:
                        series.setdefault(key, []).extend((float(t) + step.totalTime, float(v)) for t, v in data)
    for key, data in series.items():
        vals = [v for _, v in data]
        out[key] = {'last': vals[-1], 'maxAbs': max(abs(v) for v in vals), 'n': len(vals)}
    ie = out.get('ALLIE', {}).get('maxAbs')
    ratios = {}
    if ie:
        for key in ('ALLAE', 'ALLSD', 'ALLVD', 'ALLKE', 'ALLCD'):
            if key in out:
                ratios[key + '/ALLIE'] = out[key]['last'] / ie if ie else None
    if ratios:
        out['ratios'] = ratios
    return out


def list_history(odb, step, cap=60):
    items = []
    for rname, region in step.historyRegions.items():
        for vname, ho in region.historyOutputs.items():
            data = ho.data
            items.append({'region': rname, 'var': vname, 'n': len(data),
                          'last': fnum(data[-1][1]) if data else None,
                          'max': fnum(max(v for _, v in data)) if data else None,
                          'min': fnum(min(v for _, v in data)) if data else None})
            if len(items) >= cap:
                return items, True
    return items, False


def nearest_node(odb, pt):
    best = None
    p = np.array(pt, dtype=float)
    for iname, inst in odb.rootAssembly.instances.items():
        nodes = inst.nodes
        if len(nodes) == 0:
            continue
        xyz = np.array([n.coordinates for n in nodes], dtype=float)
        d = ((xyz - p) ** 2).sum(axis=1)
        i = int(d.argmin())
        if best is None or d[i] < best[0]:
            best = (float(d[i]), iname, int(nodes[i].label), [float(c) for c in xyz[i]])
    return best


def nearest_element(odb, pt):
    best = None
    p = np.array(pt, dtype=float)
    for iname, inst in odb.rootAssembly.instances.items():
        nodes = inst.nodes
        if len(nodes) == 0 or len(inst.elements) == 0:
            continue
        lab2xyz = dict((n.label, n.coordinates) for n in nodes)
        for el in inst.elements:
            try:
                pts = [lab2xyz[c] for c in el.connectivity]
            except KeyError:
                continue
            c = np.mean(np.array(pts, dtype=float), axis=0)
            d = float(((c - p) ** 2).sum())
            if best is None or d < best[0]:
                best = (d, iname, int(el.label), [float(v) for v in c])
    return best


def probe_series(odb, steps, pt, names, skip=()):
    """time series at the node / element nearest to pt; steps named in `skip` (eigenvalue steps: their frames are mode
    shapes, not a history) are left out"""
    steps = [s for s in steps if s.name not in skip]
    out = {'point': pt, 'series': {}}
    nn = nearest_node(odb, pt)
    ne = None
    if nn:
        out['node'] = {'instance': nn[1], 'label': nn[2], 'coords': nn[3], 'distance': math.sqrt(nn[0])}
    for name in names:
        rows = []
        labels = None
        for step in steps:
            for fr in step.frames:
                if name not in fr.fieldOutputs.keys():
                    continue
                fo = fr.fieldOutputs[name]
                nodal = any(str(l.position) == 'NODAL' for l in fo.locations)
                try:
                    if nodal and nn:
                        region = odb.rootAssembly.instances[nn[1]].getNodeFromLabel(nn[2])
                        vals = fo.getSubset(region=region).values
                    else:
                        if ne is None:
                            ne = nearest_element(odb, pt)
                            if ne:
                                out['element'] = {'instance': ne[1], 'label': ne[2], 'centroid': ne[3],
                                                  'distance': math.sqrt(ne[0])}
                        if not ne:
                            continue
                        region = odb.rootAssembly.instances[ne[1]].getElementFromLabel(ne[2])
                        vals = fo.getSubset(region=region, position=INTEGRATION_POINT).values
                except Exception:
                    continue
                if not vals:
                    continue
                labels = list(fo.componentLabels) or [name]
                arr = np.array([np.atleast_1d(v.data) for v in vals], dtype=float)
                mean = arr.mean(axis=0)
                row = [float(step.totalTime + fr.frameValue)] + [float(x) for x in mean]
                if str(fo.type).startswith('TENSOR'):
                    try:
                        row.append(float(np.mean([v.mises for v in vals])))
                    except Exception:
                        pass
                rows.append(row)
        if rows:
            cols = ['time'] + (labels or [])
            if len(rows[0]) > len(cols):
                cols.append('mises')
            out['series'][name] = {'columns': cols, 'rows': rows}
    return out


def set_size(obj, kind):
    """number of nodes / elements of a set (assembly-level sets hold one array per instance)"""
    try:
        items = obj.nodes if kind == 'node' else obj.elements
        if items is None:
            return 0
        if len(items) and not hasattr(items[0], 'label'):
            return int(sum(len(x) for x in items))
        return int(len(items))
    except Exception:
        return None


def all_sets(odb):
    """every named node / element set -> list of dicts (kind, holder, name, n) plus the set objects"""
    asm = odb.rootAssembly
    out = []
    holders = [('assembly', asm)] + [(k, asm.instances[k]) for k in asm.instances.keys()]
    for label, h in holders:
        for kind, repo in (('node', h.nodeSets), ('element', h.elementSets)):
            for name in repo.keys():
                if name.startswith(' '):                    # ' ALL NODES', ' ALL ELEMENTS'
                    continue
                out.append(({'kind': kind, 'holder': label, 'name': name, 'n': set_size(repo[name], kind)}, repo[name]))
    return out


def find_sets(sets, name):
    """sets called `name` (case-insensitive, with or without the instance prefix) -> {'node': (info, obj), 'element': ...};
    an assembly-level set wins over an instance-level one of the same name"""
    want = name.strip().upper()
    hits = {}
    for info, obj in sets:
        nm = info['name'].upper()
        if nm == want or nm.split('.')[-1] == want or (info['holder'].upper() + '.' + nm) == want:
            if info['kind'] not in hits:
                hits[info['kind']] = (info, obj)
    return hits


def subset_of(fo, hits):
    """values of a field output on a named set -> (values, kind of set used) or (None, None)"""
    nodal = any(str(l.position) == 'NODAL' for l in fo.locations)
    order = ('node', 'element') if nodal else ('element',)
    for kind in order:
        if kind in hits:
            try:
                vals = fo.getSubset(region=hits[kind][1]).values
            except Exception:
                continue
            if len(vals):
                return vals, kind
    return None, None


def set_summary(odb, fr, hits, want):
    """statistics of the requested fields on a set at one frame"""
    out = {}
    for name in fr.fieldOutputs.keys():
        base = name.split(' ')[0]
        if base not in want and name not in want:
            continue
        fo = fr.fieldOutputs[name]
        vals, kind = subset_of(fo, hits)
        if vals is None:
            continue
        arr = np.array([np.atleast_1d(v.data) for v in vals], dtype=float)
        labels = list(fo.componentLabels) or [name]
        k = min(len(labels), arr.shape[1])
        info = {'setKind': kind, 'n': int(arr.shape[0]), 'columns': labels[:k],
                'sum': [float(x) for x in arr[:, :k].sum(axis=0)], 'mean': [float(x) for x in arr[:, :k].mean(axis=0)],
                'min': [float(x) for x in arr[:, :k].min(axis=0)], 'max': [float(x) for x in arr[:, :k].max(axis=0)],
                'type': str(fo.type)}
        if str(fo.type) == 'VECTOR':
            mag = np.sqrt((arr[:, :k] ** 2).sum(axis=1))
            info['maxMagnitude'] = float(mag.max())
        if str(fo.type).startswith('TENSOR'):
            try:
                ms = np.array([v.mises for v in vals], dtype=float)
                info['mises'] = {'max': float(ms.max()), 'mean': float(ms.mean()), 'min': float(ms.min())}
            except Exception:
                pass
        if kind == 'node' and arr.shape[0] <= 12:
            rows = []
            for i, v in enumerate(vals):
                iname = v.instance.name if v.instance is not None else ''
                rows.append({'node': int(v.nodeLabel), 'instance': iname, 'coords': node_coords(odb, iname, v.nodeLabel),
                             'data': [float(x) for x in arr[i, :k]]})
            info['rows'] = rows
        out[name] = info
    return out


def set_series(steps, hits, names, skip=()):
    """per frame: sum (reaction-like fields) or mean (everything else) of each component over the set"""
    out = {}
    for name in names:
        rows = []
        cols = None
        for step in steps:
            if step.name in skip:
                continue
            for fr in step.frames:
                if name not in fr.fieldOutputs.keys():
                    continue
                fo = fr.fieldOutputs[name]
                vals, kind = subset_of(fo, hits)
                if vals is None:
                    continue
                arr = np.array([np.atleast_1d(v.data) for v in vals], dtype=float)
                labels = list(fo.componentLabels) or [name]
                k = min(len(labels), arr.shape[1])
                how = 'sum' if name.split(' ')[0] in SUM_FIELDS else 'mean'
                red = arr[:, :k].sum(axis=0) if how == 'sum' else arr[:, :k].mean(axis=0)
                cols = ['time'] + ['%s %s' % (how, l) for l in labels[:k]]
                rows.append([float(step.totalTime + fr.frameValue)] + [float(x) for x in red])
        if rows:
            out[name] = {'columns': cols, 'rows': rows}
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path, out_path = argv[0], argv[1]
    opt = {'step': None, 'frame': None, 'vars': None, 'probes': [], 'series': ['U'], 'history': True, 'sets': [],
           'seriesGiven': False}
    i = 2
    while i < len(argv):
        a = argv[i]
        if a == '--step':
            opt['step'] = argv[i + 1]
            i += 2
        elif a == '--frame':
            opt['frame'] = int(argv[i + 1])
            i += 2
        elif a == '--vars':
            opt['vars'] = [v.strip() for v in argv[i + 1].split(',') if v.strip()]
            i += 2
        elif a == '--probe':
            opt['probes'].append([float(v) for v in argv[i + 1].split(',')])
            i += 2
        elif a == '--series':
            opt['series'] = [v.strip() for v in argv[i + 1].split(',') if v.strip()]
            opt['seriesGiven'] = True
            i += 2
        elif a == '--set':
            opt['sets'].append(argv[i + 1])
            i += 2
        elif a == '--no-history':
            opt['history'] = False
            i += 1
        else:
            i += 1

    res = {'odb': path, 'error': None}
    odb = openOdb(path, readOnly=True)
    try:
        steps = list(odb.steps.values())
        res['steps'] = []
        res['frequencies'] = []
        res['eigenvalues'] = []
        eigen_steps = set()
        for s in steps:
            frames = s.frames
            info = {'name': s.name, 'procedure': s.procedure, 'domain': str(s.domain), 'nFrames': len(frames),
                    'timePeriod': fnum(s.timePeriod), 'totalTime': fnum(s.totalTime),
                    'lastFrameValue': fnum(frames[-1].frameValue) if len(frames) else None}
            res['steps'].append(info)
            for fr in frames:
                desc = fr.description or ''
                m = re.search(r'Mode\s+(\d+):\s*EigenValue\s*=\s*([-+0-9.eE]+)', desc)
                if m:
                    res['eigenvalues'].append({'step': s.name, 'mode': int(m.group(1)), 'value': float(m.group(2))})
                    eigen_steps.add(s.name)
                    continue
                m = re.search(r'Mode\s+(\d+):.*Freq\s*=\s*([-+0-9.eE]+)', desc)
                if m:
                    res['frequencies'].append({'step': s.name, 'mode': int(m.group(1)), 'frequency': float(m.group(2))})
                    eigen_steps.add(s.name)
        inst = {}
        types = {}
        lo = hi = None
        for iname, ins in odb.rootAssembly.instances.items():
            inst[iname] = {'nodes': len(ins.nodes), 'elements': len(ins.elements)}
            for el in ins.elements:
                types[el.type] = types.get(el.type, 0) + 1
            if len(ins.nodes):
                xyz = np.array([n.coordinates for n in ins.nodes], dtype=float)
                mn, mx = xyz.min(axis=0), xyz.max(axis=0)
                lo = mn if lo is None else np.minimum(lo, mn)
                hi = mx if hi is None else np.maximum(hi, mx)
        if lo is not None:
            res['bbox'] = [[float(v) for v in lo], [float(v) for v in hi]]
        res['instances'] = inst
        res['elementTypes'] = types

        # choose the frame
        step = None
        if opt['step'] is not None:
            if opt['step'] in odb.steps.keys():
                step = odb.steps[opt['step']]
            elif opt['step'].lstrip('-').isdigit():
                step = steps[int(opt['step'])]
        if step is None:
            cand = [s for s in steps if s.name not in eigen_steps and len(s.frames)]
            step = cand[-1] if cand else (steps[-1] if steps else None)
        res['fields'] = {}
        if step is not None and len(step.frames):
            fidx = opt['frame'] if opt['frame'] is not None else len(step.frames) - 1
            if not -len(step.frames) <= fidx < len(step.frames):
                raise IndexError('step %s has %d frames: valid frame numbers are 0 to %d (or -1 for the last one)' % (
                    step.name, len(step.frames), len(step.frames) - 1))
            fr = step.frames[fidx]
            res['frame'] = {'step': step.name, 'index': fidx if fidx >= 0 else len(step.frames) + fidx,
                            'frameValue': fnum(fr.frameValue), 'description': fr.description,
                            'available': list(fr.fieldOutputs.keys()),
                            # frames of an eigenvalue step hold normalised mode shapes, not physical displacements
                            'modeShape': step.name in eigen_steps}
            want = opt['vars'] or DEFAULT_VARS
            for name in fr.fieldOutputs.keys():
                base = name.split(' ')[0]
                if base not in want and name not in want:
                    continue
                try:
                    res['fields'][name] = field_summary(odb, name, fr.fieldOutputs[name])
                except Exception as e:
                    res['fields'][name] = {'error': '%s: %s' % (type(e).__name__, e)}
        if opt['history']:
            try:
                res['history'] = history_summary(odb, steps)
            except Exception as e:
                res['history'] = {'error': str(e)}
            if step is not None:
                try:
                    items, more = list_history(odb, step)
                    res['historyOutputs'] = {'step': step.name, 'items': items, 'truncated': more}
                except Exception as e:
                    res['historyOutputs'] = {'error': str(e)}
        res['probes'] = [probe_series(odb, steps, p, opt['series'], eigen_steps) for p in opt['probes']]
        # named sets: always listed; statistics (and, with --series, time histories) for the ones asked for
        try:
            sets = all_sets(odb)
            res['sets'] = [info for info, _ in sets]
            res['setResults'] = []
            for name in opt['sets']:
                hits = find_sets(sets, name)
                item = {'name': name, 'found': dict((k, v[0]) for k, v in hits.items())}
                if hits and step is not None and len(step.frames):
                    fidx = opt['frame'] if opt['frame'] is not None else len(step.frames) - 1
                    item['fields'] = set_summary(odb, step.frames[fidx], hits, opt['vars'] or DEFAULT_VARS)
                    if opt['seriesGiven']:
                        item['series'] = set_series(steps, hits, opt['series'], eigen_steps)
                res['setResults'].append(item)
        except Exception as e:
            res['setsError'] = '%s: %s' % (type(e).__name__, e)
    finally:
        odb.close()
    with open(out_path, 'w') as f:
        json.dump(res, f, indent=1)
    return 0


if __name__ == '__main__':
    try:
        code = main(sys.argv[1:])
    except Exception as exc:  # always leave a JSON behind so the caller can report the reason
        import traceback
        try:
            with open(sys.argv[2], 'w') as fh:
                json.dump({'error': '%s: %s' % (type(exc).__name__, exc), 'trace': traceback.format_exc()}, fh)
        except Exception:
            pass
        traceback.print_exc()
        code = 1
    sys.exit(code)
