# -*- coding: utf-8 -*-
"""Corpus test for the INP preview parser.

    py run_corpus.py [--only substring] [--module-dir DIR] [--quiet] [--table]

Parses every stand-alone INP under examples/corpus/ and checks, per file:
  * no exception, the view is JSON-serialisable and has the keys other tools rely on;
  * node / element counts, bounding box and a sample of node coordinates against what Abaqus itself
    built (examples/corpus/_gen/odb_truth.json, from `abaqus datacheck` + the output database) and,
    for the CAE-generated models, against what CAE reported when it wrote the file
    (examples/corpus/_gen/cae_expected.json);
  * the 体检 messages each "check" model must produce, and that clean models stay clean.
Prints one block per file and a summary table.  Exit code 1 when anything fails.
Needs no Abaqus: the reference data were produced once by examples/corpus/_gen/odb_truth.py.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
CORPUS = os.path.join(ROOT, 'examples', 'corpus')
GEN = os.path.join(CORPUS, '_gen')
INC = re.compile(r'(?im)^\*\s*(?:INCLUDE|NODE|ELEMENT|NSET|ELSET)\b[^\n]*?\bINPUT\s*=\s*"?([^\s",]+)')
KEYS = ('stats', 'checks', 'materials', 'sections', 'instances', 'steps', 'initialBcs', 'ties',
        'contactPairs', 'nsets', 'elsets', 'surfaces', 'geom')

# What each model must report.  ('warn', 'text') = a message of that level containing the text.
# 'clean': no error / warning at all.  Files not listed are only required to parse.
EXPECT = {
    'cae/shell_roof_s4r_s3.inp': {'clean': True},
    'cae/frame_b31_t3d2.inp': {'clean': True},
    'cae/plate_hole_cps4r_cps3.inp': {'clean': True},
    'cae/axisym_cylinder_cax4r.inp': {'clean': True},
    'cae/quad_solids_c3d10_c3d20r.inp': {'clean': True},
    'cae/contact_pair_blocks.inp': {'clean': True, 'must': [('info', '初始间隙')]},
    'cae/general_contact_blocks.inp': {'clean': True},
    'cae/rigid_analytical_punches.inp': {'clean': True, 'must': [('info', '初始间隙')]},
    'cae/explicit_taylor_bar.inp': {'clean': True},
    'cae/flat_noparts_beam_pad.inp': {'clean': True},
    'cae/rotated_instances.inp': {'clean': True},
    'cae/tie_gap_within_tolerance.inp': {'clean': True},
    'checks/chk_tie_gap.inp': {'must': [('warn', '不会被绑定')]},
    'checks/chk_tie_overhang.inp': {'must': [('warn', '不会被绑定')]},
    'checks/chk_adjacent_untied.inp': {'must': [('warn', '相邻')]},
    'checks/chk_contact_far.inp': {'must': [('warn', '相距很远')]},
    'checks/chk_contact_empty_surface.inp': {'must': [('error', '为空')]},
    'checks/chk_unused_nodes.inp': {'must': [('info', '没有被任何单元使用')], 'no': ['warn', 'error']},
    'checks/chk_material_poisson.inp': {'must': [('error', '泊松比')]},
    'checks/chk_material_negative.inp': {'must': [('error', '弹性模量'), ('error', '密度')]},
    'checks/chk_material_steel_density.inp': {'must': [('warn', '按名称应是钢')]},
    'checks/chk_material_concrete_units.inp': {'must': [('warn', '按名称应是混凝土')]},
    'checks/chk_material_near_incompressible.inp': {'must': [('error', '0.495')]},
    'checks/chk_name_non_ascii.inp': {'must': [('error', '非 ASCII'), ('warn', '按名称应是钢'),
                                               ('warn', '按名称应是混凝土')]},
    'handwritten/hw_system_x_along_z.inp': {'must': [('error', 'x 轴与 z 轴平行')]},
    'handwritten/hw_mesh_generation.inp': {'no': ['warn', 'error']},
    'handwritten/hw_nmap_transforms.inp': {'no': ['warn', 'error']},
    'handwritten/hw_nmap_shift_only.inp': {'no': ['warn', 'error']},
    'checks/chk_rigid_body_motion.inp': {'must': [('warn', '刚体位移')]},
    'checks/chk_rigid_body_motion_contact.inp': {'must': [('info', '只能靠接触')], 'no': ['warn', 'error']},
    'checks/chk_unrestrained_instance.inp': {'must': [('warn', '刚体位移')]},
}


def read_text(p):
    raw = open(p, 'rb').read()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', 'replace')


def standalone_inps():
    out = []
    for d, dirs, files in os.walk(CORPUS):
        dirs[:] = sorted(x for x in dirs if not x.startswith('_'))
        inps = [f for f in files if f.lower().endswith('.inp')]
        included = set()
        for f in inps:
            for m in INC.finditer(read_text(os.path.join(d, f))):
                included.add(os.path.basename(m.group(1)).lower())
        for f in sorted(inps):
            if f.lower() not in included:
                out.append(os.path.relpath(os.path.join(d, f), CORPUS).replace('\\', '/'))
    return out


def load(name):
    p = os.path.join(GEN, name)
    return json.load(open(p, encoding='utf-8')) if os.path.isfile(p) else {}


def close(a, b, tol):
    return abs(a - b) <= tol


def node_coords(mdl, inst, label):
    """coordinates of node `label` of an ODB / CAE instance name in the parsed model, or None"""
    key = inst.upper()
    ctx = mdl.inst_by_key.get(key)
    if ctx is None:
        if key in ('', 'ASSEMBLY', 'PART-1-1') or not mdl.instances:
            ctx = mdl.root
        else:
            return None
    g = ctx.node_map.get(label)
    return None if g is None else mdl.coords[g]


# The output database names a few things differently from the input file.
TYPE_ALIAS = {'S3': 'S3R'}                       # S3 and S3R are the same element
INTERNAL_TYPES = ('IDCOUP',)                     # elements Abaqus generates itself (distributing couplings)
# Abaqus moves the slave nodes of these ties onto the master surface (adjust=yes): allow that distance.
ADJUSTED = {'cae/tie_gap_within_tolerance.inp': 0.0041}


def norm_types(types):
    out = {}
    for k, v in types.items():
        k = k.upper()
        if k.startswith(INTERNAL_TYPES):
            continue
        k = TYPE_ALIAS.get(k, k)
        out[k] = out.get(k, 0) + v
    return out


def compare(mdl, view, ref, what, problems, notes, slack=0.0):
    """ref: {'nodes', 'elements', 'types', 'bbox', 'sample', 'instances'} from Abaqus / CAE"""
    st = view['stats']
    lo, hi = st['meshBbox']
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3))) or 1.0
    tol = 2e-6 * diag + 1e-9 + slack
    unused = set(mdl.unused_nodes())
    auto = set(mdl.auto_nodes)
    if ref.get('types'):
        mine, theirs = norm_types(st['types']), norm_types(ref['types'])
        if mine != theirs:
            problems.append('%s: element types %s, preview %s' % (what, theirs, mine))
    elif ref.get('elements') is not None and ref['elements'] != st['elements']:
        problems.append('%s: %d elements, preview has %d' % (what, ref['elements'], st['elements']))
    by_inst = ref.get('instances') if mdl.instances else None
    if by_inst:
        # compare instance by instance; the output database repeats at assembly level the nodes that
        # assembly-level elements (connectors) use, so the totals are not comparable
        for name, rec in by_inst.items():
            ctx = mdl.inst_by_key.get(name.upper())
            if ctx is None:
                if name and rec.get('nodes'):
                    problems.append('%s: instance %s is missing in the preview' % (what, name))
                continue
            n0, n1 = ctx.nrange
            mine = (n1 - n0) - sum(1 for g in unused if n0 <= g < n1)
            want = rec['nodes'] + rec.get('refPoints', 0)
            if want not in (mine, n1 - n0):
                problems.append('%s: instance %s has %d nodes, preview %d (%d with unused ones)' % (
                    what, name, want, mine, n1 - n0))
    elif ref.get('nodes') is not None and ref['nodes'] != st['nodes']:
        # the output database only keeps nodes that take part in the analysis
        if ref['nodes'] == st['nodes'] - st['unusedNodes']:
            notes.append('%s 不含 %d 个未使用节点' % (what, st['unusedNodes']))
        else:
            problems.append('%s: %d nodes, preview has %d (%d unused)' % (
                what, ref['nodes'], st['nodes'], st['unusedNodes']))
    if ref.get('bbox') and not auto:
        for k in range(3):
            if not close(ref['bbox'][0][k], lo[k], tol) or not close(ref['bbox'][1][k], hi[k], tol):
                box = st['bbox']        # the reference may include nodes without elements
                if close(ref['bbox'][0][k], box[0][k], tol) and close(ref['bbox'][1][k], box[1][k], tol):
                    continue
                problems.append('%s: bbox %s, preview %s' % (what, ref['bbox'], [lo, hi]))
                break
    bad = miss = n = 0
    worst = 0.0
    for inst, label, x, y, z in ref.get('sample', []):
        if mdl.instances and inst == '':
            continue                    # assembly-level copies in the output database (see above)
        key = inst.upper()
        ctx = mdl.inst_by_key.get(key) or mdl.root
        g = ctx.node_map.get(label)
        if g is None:
            miss += 1
            continue
        if g in auto:
            continue                    # created by Abaqus itself; the preview only approximates its place
        n += 1
        p = mdl.coords[g]
        d = max(abs(p[0] - x), abs(p[1] - y), abs(p[2] - z))
        worst = max(worst, d)
        if d > tol:
            bad += 1
    if bad or miss:
        problems.append('%s: %d of %d sampled nodes are off (max %.3g), %d not found' % (what, bad, n, worst, miss))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--quiet', action='store_true', help='do not print the 体检 messages')
    ap.add_argument('--table', action='store_true', help='print the summary as a Markdown table')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, a.module_dir)
    import inp_model
    print('inp_model from', os.path.dirname(os.path.abspath(inp_model.__file__)))
    truth = load('odb_truth.json')
    truth.update(load('odb_truth_official.json'))          # present only where the official samples were fetched
    cae = load('cae_expected.json')
    rows = []
    failed = 0
    for rel in standalone_inps():
        if a.only not in rel:
            continue
        path = os.path.join(CORPUS, rel.replace('/', os.sep))
        problems, notes = [], []
        t0 = time.time()
        try:
            mdl = inp_model.Model(path)
            view = mdl.view()
            json.dumps(view)
            for k in KEYS:
                if k not in view:
                    problems.append('view lacks key %r' % k)
            for c in view['checks']:
                if c.get('level') not in ('error', 'warn', 'info', 'ok') or not isinstance(c.get('msg'), str):
                    problems.append('malformed check %r' % (c,))
                if re.search(r'\d[eE][-+]?\d', c.get('msg', '')):
                    problems.append('scientific notation in message: %s' % c['msg'])
            for s in view['surfaces'][:40]:
                mdl.highlight('surface', s['name'])
            for i in range(-1, len(view['steps'])):
                mdl.symbols(i)
        except Exception:
            failed += 1
            print('\n== %s\n   EXCEPTION\n%s' % (rel, traceback.format_exc()))
            rows.append((rel, 'EXCEPTION', '', '', '', '', '', '', 'FAIL'))
            continue
        dt = time.time() - t0
        st = view['stats']
        nsamp = 0
        ref = truth.get(rel)
        if ref and ref.get('status') == 'ok':
            nsamp += compare(mdl, view, ref, 'Abaqus', problems, notes, ADJUSTED.get(rel, 0.0))
        elif ref:
            notes.append('Abaqus datacheck: %s' % ref.get('status'))
        else:
            notes.append('无 Abaqus 对照数据')
        cref = cae.get(os.path.basename(rel))
        if cref:
            exp = {'elements': cref['elements'], 'nodes': cref['meshNodes'] + cref['refPoints'],
                   'types': cref['types'], 'bbox': cref['bbox'],
                   'instances': None if cref.get('flat') else cref['instances'],
                   'sample': [] if cref.get('flat') else cref['sample']}
            nsamp += compare(mdl, view, exp, 'CAE', problems, notes)
        exp = EXPECT.get(rel, {})
        levels = [c['level'] for c in view['checks']]
        for level, text in exp.get('must', []):
            if not any(c['level'] == level and text in c['msg'] for c in view['checks']):
                problems.append('expected a %s message containing "%s"' % (level, text))
        forbidden = ['error', 'warn'] if exp.get('clean') else exp.get('no', [])
        for c in view['checks']:
            if c['level'] in forbidden:
                problems.append('unexpected %s: %s' % (c['level'], c['msg']))
        nbc = len(view['initialBcs']) + sum(len(s['bcs']) for s in view['steps'])
        nld = sum(len(s['loads']) for s in view['steps'])
        nint = len(view['ties']) + len(view['contactPairs']) + len(view['constraints']) + \
            (1 if view['generalContact'] else 0)
        types = ', '.join('%s×%d' % kv for kv in sorted(st['types'].items()))
        unknown = ', '.join('%s×%d' % kv for kv in sorted(st['unknownTypes'].items())) or '-'
        cnt = '%dE %dW %dI' % (levels.count('error'), levels.count('warn'), levels.count('info'))
        ok = not problems
        failed += 0 if ok else 1
        rows.append((rel, st['nodes'], st['elements'], types, unknown,
                     '%d/%d/%d' % (len(view['nsets']), len(view['elsets']), len(view['surfaces'])),
                     '%d/%d/%d' % (nbc, nld, nint), cnt, 'ok' if ok else 'FAIL'))
        print('\n== %s   [%s]  %.2fs' % (rel, 'ok' if ok else 'FAIL', dt))
        print('   nodes %d  elements %d  types: %s  unknown: %s' % (st['nodes'], st['elements'], types, unknown))
        print('   sets n/el/surf %d/%d/%d   BC %d  loads %d  interactions %d   verified nodes: %d%s' % (
            len(view['nsets']), len(view['elsets']), len(view['surfaces']), nbc, nld, nint, nsamp,
            ('   (' + '; '.join(notes) + ')') if notes else ''))
        if not a.quiet:
            for c in view['checks']:
                print('     [%s] %s' % (c['level'], c['msg']))
        for p in problems:
            print('   !! ' + p)
    print('\n' + '=' * 100)
    head = ('file', 'nodes', 'elems', 'element types', 'unknown', 'sets n/el/surf', 'BC/load/inter', '体检', 'result')
    if a.table:
        print('| ' + ' | '.join(head) + ' |')
        print('|' + '---|' * len(head))
        for r in rows:
            print('| ' + ' | '.join(str(x) for x in r) + ' |')
    else:
        for r in rows:
            print('%-46s %6s %6s  %-9s %-12s %-12s %s' % (r[0], r[1], r[2], r[5], r[6], r[7], r[8]))
    print('%d files, %d failed' % (len(rows), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
