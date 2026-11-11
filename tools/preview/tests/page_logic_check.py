# -*- coding: utf-8 -*-
"""Runs the preview page's own script under Node against real model data - no browser needed.

    py page_logic_check.py [--html static/index.html] [--module-dir DIR] [--only substring]

What is real:   the page script (taken out of index.html), three.module.js r160 and OrbitControls.js as
                vendored, the model / symbol / highlight JSON produced by inp_model.py for corpus files.
What is faked:  the DOM (elements are plain objects), the 2-D canvas context, fetch() (answers from the
                prepared JSON, review endpoints included) and THREE.WebGLRenderer (draws nothing).

So this checks the logic and the scene that the page builds, not the pixels:
  * loading every model, the four colour modes, model tree, 体检 list, properties, review bar,
    标注 -> 退回 -> 复制修改指令, the "too large" notice;
  * every buffer the scene holds is finite and consistent (positions / colours / wide-line attributes,
    instanced symbol matrices), highlights and symbols lie on the model;
  * for each view button (前/后/顶/底/左/右/Iso/适应) the whole model is inside the camera frustum and
    fills a sensible part of it - computed with three.js' own camera maths.
It is light (one short Node process); the real rendering is covered by ui_check.py (headless Edge).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.abspath(os.path.join(HERE, '..', '..', '..', 'examples', 'corpus'))
FILES = ['cae/quad_solids_c3d10_c3d20r.inp', 'cae/rigid_analytical_punches.inp', 'cae/frame_b31_t3d2.inp',
         'cae/plate_hole_cps4r_cps3.inp', 'cae/axisym_cylinder_cax4r.inp', 'cae/shell_roof_s4r_s3.inp',
         'cae/general_contact_blocks.inp', 'cae/contact_pair_blocks.inp', 'cae/rotated_instances.inp',
         'cae/explicit_taylor_bar.inp', 'cae/flat_noparts_beam_pad.inp', 'cae/tie_gap_within_tolerance.inp',
         'official/flap.inp', 'official/knee_bolster_cpair.inp', 'official/hgo_2fiber_std_uniori.inp',
         'official/boltpipeflange_axi_gkax6.inp', 'official/xcoupling_beamtoshell_lin.inp',
         'official/mpc_rigelem.inp', 'official/pipewhip_s4rs.inp', 'official/beamgap.inp',
         'checks/chk_material_poisson.inp', 'checks/chk_tie_gap.inp', 'handwritten/hw_mesh_generation.inp']

# the official Abaqus samples are not part of the repository (examples/corpus/fetch_official.py copies them from your
# own installation); without them the test runs on the remaining files
FILES = [f for f in FILES if os.path.isfile(os.path.join(CORPUS, f.replace('/', os.sep)))]

HEAD = r'''
import * as THREE_REAL from 'three';
import { OrbitControls } from './OrbitControls.js';
import fs from 'node:fs';

const P = new Proxy(function () {}, {
  get: (t, k) => k === Symbol.toPrimitive ? () => 0 : k === Symbol.iterator ? function* () {} : k === 'then' ? undefined
    : k === 'length' ? 0 : P,
  apply: () => P, construct: () => P, set: () => true, has: () => true,
});
const els = {};
function el(id) {
  if (!els[id]) {
    const cls = new Set();
    els[id] = { id, style: {}, dataset: {}, value: id === 'stepSel' ? '0' : '', checked: id !== 'intChk', textContent: '', innerHTML: '', title: '',
      classList: { toggle: (c, on) => { (on === undefined ? !cls.has(c) : on) ? cls.add(c) : cls.delete(c); }, add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c) },
      appendChild() {}, removeChild() {}, insertBefore() {}, childNodes: [], firstChild: null, querySelector: () => null,
      querySelectorAll: () => [], addEventListener() {}, removeEventListener() {}, getContext: () => P, selectedOptions: [{ textContent: 'step' }],
      scrollIntoView() {}, focus() {}, select() {}, remove() {}, setPointerCapture() {}, releasePointerCapture() {},
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 800 }),
      clientWidth: 1200, clientHeight: 800, scrollTop: 0, scrollHeight: 0, width: 0, height: 0,
      set className(v) { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => cls.add(c)); }, get className() { return [...cls].join(' '); } };
  }
  return els[id];
}
globalThis.document = { getElementById: el, createElement: () => el('tmp' + Math.random()), body: el('body'), execCommand: () => true,
  documentElement: el('html') };
globalThis.window = globalThis;
globalThis.devicePixelRatio = 1;
globalThis.ResizeObserver = class { observe() {} };
globalThis.requestAnimationFrame = () => 0;
globalThis.setInterval = () => 0;
const store = {};
Object.defineProperty(globalThis, 'localStorage', { value: { getItem: k => (k in store ? store[k] : null), setItem(k, v) { store[k] = String(v); } }, configurable: true });
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true });

const DATA = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let current = 0;
const posted = [];
globalThis.fetch = async (url, opt) => {
  const d = DATA[current];
  let body;
  if (url.startsWith('/api/list')) body = { files: DATA.map((x, i) => ({ path: x.model.path, mtime: i === current ? x.model.mtime : 1, size: 1 })) };
  else if (url.startsWith('/api/model')) body = d.model;
  else if (url.startsWith('/api/symbols')) body = d.symbols;
  else if (url.startsWith('/api/highlight')) {
    const q = new URLSearchParams(url.split('?')[1]);
    body = d.highlights[q.get('kind') + '|' + q.get('name')] || { n: 0, pts: [], tris: [], segs: [] };
  } else if (url.startsWith('/api/review') && opt && opt.method === 'POST') {
    const b = JSON.parse(opt.body);
    posted.push(b);
    body = { ok: true, state: b.status, entry: { sha256: 'x', status: b.status, decidedAt: '2026-11-10T09:00:00', note: b.note, comments: b.comments }, markdown: '# md\n- 时间：now', mdPath: '.abq/review/x.md', dryRun: b.dryRun };
  } else if (url.startsWith('/api/review')) body = d.review;
  else body = { error: 'not found' };
  return { status: 200, json: async () => body };
};

// the real three.js, except that nothing is drawn
class FakeRenderer {
  constructor(o) { this.domElement = (o && o.canvas) || el('canvas' + Math.random()); this.frames = 0; }
  setPixelRatio() {} setSize() {} dispose() {}
  render(scene, camera) { this.frames++; scene.updateMatrixWorld(true); camera.updateMatrixWorld(true); }
  getContext() { return null; }
}
const THREE = Object.assign({}, THREE_REAL, { WebGLRenderer: FakeRenderer });
'''

DRIVER = r'''
(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  await wait(50);
  let fails = 0;
  const V3 = THREE_REAL.Vector3;
  const finite = (arr, what) => { for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) throw new Error('non-finite value in ' + what + ' at ' + i); };

  function checkBuffers(group, what, box, margin) {
    let n = 0;
    group.traverse(o => {
      const g = o.geometry;
      if (!g || o.isSprite) return;          // sprites share three.js' own interleaved quad
      n++;
      const cnt = g.attributes.position.count;
      for (const [name, at] of Object.entries(g.attributes)) {
        if (at.array.length !== at.count * at.itemSize) throw new Error(what + ': attribute ' + name + ' has a ragged array');
        if (at.count !== cnt && !o.isInstancedMesh) throw new Error(what + ': attribute ' + name + ' has ' + at.count + ' items, position has ' + cnt);
        finite(at.array, what + '.' + name);
      }
      if (o.isInstancedMesh) { finite(o.instanceMatrix.array, what + '.instanceMatrix'); return; }
      if (o.isSprite || o.type === 'ArrowHelper' || (o.parent && o.parent.type === 'ArrowHelper')) return;
      // drawn where the model is
      const p = g.attributes.position.array;
      for (let i = 0; i < p.length; i += 3) for (let k = 0; k < 3; k++)
        if (p[i + k] < box[0][k] - margin || p[i + k] > box[1][k] + margin)
          throw new Error(what + ': vertex ' + (i / 3) + ' lies outside the model box (' + [p[i], p[i + 1], p[i + 2]].map(v => v.toFixed(4)) + ')');
    });
    return n;
  }

  function checkViews(M, tag) {
    const b = M.stats.viewBbox || M.stats.bbox;
    const out = [];
    for (const v of ['front', 'back', 'top', 'bottom', 'left', 'right', 'iso', 'fit']) {
      setView(v);
      camera.updateMatrixWorld(true);
      let lo = [1e9, 1e9], hi = [-1e9, -1e9];
      for (let i = 0; i < 8; i++) {
        const p = new V3(b[i & 1][0], b[(i >> 1) & 1][1], b[(i >> 2) & 1][2]).project(camera);
        if (![p.x, p.y, p.z].every(Number.isFinite)) throw new Error(tag + ' view ' + v + ': projection is not finite');
        if (Math.abs(p.x) > 1.0005 || Math.abs(p.y) > 1.0005 || p.z < -1 || p.z > 1)
          throw new Error(tag + ' view ' + v + ': a corner of the model box is outside the view (' + [p.x, p.y, p.z].map(q => q.toFixed(3)) + ')');
        lo = [Math.min(lo[0], p.x), Math.min(lo[1], p.y)]; hi = [Math.max(hi[0], p.x), Math.max(hi[1], p.y)];
      }
      const cover = Math.max(hi[0] - lo[0], hi[1] - lo[1]) / 2;       // share of the view the box spans
      if (cover < 0.55) throw new Error(tag + ' view ' + v + ': the model only spans ' + (100 * cover).toFixed(0) + ' % of the view');
      out.push(v + ' ' + (100 * cover).toFixed(0) + '%');
    }
    return out.join(' ');
  }

  for (let i = 0; i < DATA.length; i++) {
    current = i;
    const p = DATA[i].model.path;
    try {
      await window.__preview.loadModel(p, false);
      const M = window.__preview.model;
      if (!M || window.__preview.path !== p) throw new Error('model not set');
      // what is drawn lies inside the box of all nodes (loose construction nodes included) and of the view
      const vb = M.stats.viewBbox || M.stats.bbox, ab = M.stats.bbox;
      const box = [[0, 1, 2].map(k => Math.min(vb[0][k], ab[0][k])), [0, 1, 2].map(k => Math.max(vb[1][k], ab[1][k]))];
      const d = Math.hypot(box[1][0] - box[0][0], box[1][1] - box[0][1], box[1][2] - box[0][2]) || 1;
      const nModel = checkBuffers(gModel, 'model', box, 1e-6 * d + 1e-9);
      if (!nModel) throw new Error('nothing was put into the scene');
      for (const m of ['inst', 'mat', 'sec', 'none']) { document.getElementById('colorSel').value = m; applyColors(); }
      if (faceMesh) { const c = faceMesh.geometry.attributes.color.array; finite(c, 'colours'); if (!c.some(x => x > 0)) throw new Error('all face colours are black'); }
      buildTree(); buildChecks(); showOverview(); renderReview();
      const dflt = M.stats.dim === '2d' || M.stats.dim === 'axi' ? 'front' : 'iso';
      const views = checkViews(M, p);
      setView(dflt);
      // symbols of the last step and a few real highlights
      await loadSymbols();
      const nSym = checkBuffers(gSym, 'symbols', box, 0.6 * d);
      let nHi = 0;
      for (const key of Object.keys(DATA[i].highlights)) {
        const [kind, name] = [key.slice(0, key.indexOf('|')), key.slice(key.indexOf('|') + 1)];
        await highlight([[kind, name, 0xff0000]]);
        const h = DATA[i].highlights[key];
        if ((h.tris || []).length + (h.segs || []).length + (h.pts || []).length && !gHi.children.length) throw new Error('highlight ' + key + ' drew nothing');
        nHi += checkBuffers(gHi, 'highlight ' + key, box, 1e-6 * d + 1e-9);
      }
      // review: comment -> reject -> copy
      const before = posted.length;
      window.__preview.addComment('材料 X / *Density', '1.0');
      if (window.__preview.pending.length !== 1) throw new Error('标注 was not recorded');
      await copyInstructions();
      await decide('rejected');
      if (window.__preview.review.state !== 'rejected') throw new Error('reject did not stick');
      const sent = posted.slice(before).filter(b => !b.dryRun);
      if (sent.length !== 1 || sent[0].path !== p || sent[0].status !== 'rejected' || sent[0].comments.length !== 1)
        throw new Error('unexpected POST /api/review: ' + JSON.stringify(sent));
      if (window.__preview.pending.length) throw new Error('pending comments were not cleared after the decision');
      await copyInstructions();
      if (!window.__preview.lastCopied) throw new Error('nothing was copied');
      console.log('ok   ' + p + '  scene objects ' + nModel + ', symbols ' + nSym + ', highlights ' + nHi + '; views ' + views);
    } catch (e) { fails++; console.log('FAIL ' + p + ': ' + (e && e.stack || e)); }
  }
  // approval of a model with errors needs the extra confirmation (the modal is answered by the test)
  try {
    const k = DATA.findIndex(x => x.model.checks.some(c => c.level === 'error'));
    if (k < 0) throw new Error('no model with a 体检 error in the test set');
    current = k;
    await window.__preview.loadModel(DATA[k].model.path, false);
    const before = posted.length;
    let asked = null;
    const pr = decide('approved');
    await wait(20);
    asked = document.getElementById('modal').classList.contains('open') ? document.getElementById('mdBody').innerHTML : null;
    if (!asked || !/仍然批准求解/.test(asked)) throw new Error('no extra confirmation before approving a model with errors');
    document.getElementById('mdCancel').onclick();
    await pr;
    if (posted.slice(before).some(b => !b.dryRun)) throw new Error('approval was sent although the confirmation was cancelled');
    const pr2 = decide('approved');
    await wait(20);
    document.getElementById('mdOk').onclick();
    await pr2;
    const sent = posted.slice(before).filter(b => !b.dryRun);
    if (sent.length !== 1 || sent[0].status !== 'approved') throw new Error('approval after confirmation was not sent: ' + JSON.stringify(sent));
    console.log('ok   approving ' + DATA[k].model.path + ' (体检 has errors) asks for confirmation; cancel sends nothing, confirm sends "approved"');
  } catch (e) { fails++; console.log('FAIL approve-with-errors: ' + (e && e.stack || e)); }
  // the "too large" answer
  DATA[0].model = { tooLarge: true, path: DATA[0].model.path, file: 'big.inp', sizeMB: 812.4, limitMB: 60, estimatedMemoryGB: 8.1, reserveGB: 8, availableCommitGB: 3.4, canForce: false };
  current = 0;
  try { await window.__preview.loadModel(DATA[0].model.path, false); if (!window.__preview.tooLarge) throw new Error('no notice');
    if (gModel.children.length) throw new Error('the previous model is still in the scene');
    console.log('ok   too-large notice: ' + document.getElementById('bigBtn').textContent + ' | ' + document.getElementById('bigWhy').textContent); }
  catch (e) { fails++; console.log('FAIL tooLarge: ' + (e && e.stack || e)); }
  console.log(fails ? fails + ' FAILED' : 'ALL OK');
  process.exit(fails ? 1 : 0);
})();
'''


def highlights_of(m, v):
    """a few real highlight payloads of a model: {'kind|name': payload}"""
    want = []
    for kind, key in (('nset', 'nsets'), ('elset', 'elsets'), ('surface', 'surfaces'), ('instance', 'instances'),
                      ('material', 'materials')):
        for it in v.get(key, [])[:2]:
            want.append((kind, it['name']))
    if v.get('sections'):
        want.append(('section', '0'))
    bcs = list(v.get('initialBcs', [])) + [b for s in v['steps'] for b in s['bcs']]
    for b in bcs[:2]:
        want.append(('bc', str(b['id'])))
    for l in [l for s in v['steps'] for l in s['loads']][:2]:
        want.append(('load', str(l['id'])))
    for c in v.get('constraints', [])[:2]:
        if c.get('id'):
            want.append(('constraint', str(c['id'])))
    out = {}
    for kind, name in want:
        out['%s|%s' % (kind, name)] = m.highlight(kind, name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--html', default=os.path.join(HERE, '..', 'static', 'index.html'))
    ap.add_argument('--module-dir', default=os.path.abspath(os.path.join(HERE, '..')))
    ap.add_argument('--only', default='')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, os.path.abspath(a.module_dir))
    import inp_model
    static = os.path.dirname(os.path.abspath(a.html))
    html = open(a.html, encoding='utf-8').read()
    script = re.search(r'<script type="module">(.*?)</script>', html, re.S).group(1)
    script = re.sub(r"^import .*?;\s*$", '', script, flags=re.M)
    data = []
    for rel in FILES:
        if a.only not in rel:
            continue
        m = inp_model.Model(os.path.join(CORPUS, rel.replace('/', os.sep)))
        v = json.loads(m.view_json({'path': rel, 'mtime': 123.0, 'size': 1, 'parseSeconds': 0.01}).decode('utf-8'))
        data.append({'model': v, 'symbols': json.loads(json.dumps(m.symbols(len(v['steps']) - 1))),
                     'highlights': json.loads(json.dumps(highlights_of(m, v))),
                     'review': {'path': rel, 'entry': None, 'current': False, 'sha256': 'x', 'state': 'none'}})
    work = tempfile.mkdtemp(prefix='page_logic_')
    try:
        pkg = os.path.join(work, 'node_modules', 'three')
        os.makedirs(pkg)
        shutil.copy2(os.path.join(static, 'three.module.js'), pkg)
        with open(os.path.join(pkg, 'package.json'), 'w') as f:
            json.dump({'name': 'three', 'type': 'module', 'main': 'three.module.js', 'exports': './three.module.js'}, f)
        shutil.copy2(os.path.join(static, 'OrbitControls.js'), work)
        with open(os.path.join(work, 'package.json'), 'w') as f:
            json.dump({'type': 'module'}, f)
        with open(os.path.join(work, 'data.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f)
        with open(os.path.join(work, 'page.mjs'), 'w', encoding='utf-8') as f:
            f.write(HEAD + '\n' + script + '\n' + DRIVER)
        try:
            r = subprocess.run(['node', os.path.join(work, 'page.mjs'), os.path.join(work, 'data.json')],
                               capture_output=True, text=True, encoding='utf-8', timeout=180, cwd=work)
        except subprocess.TimeoutExpired as e:
            print('TIMEOUT\n', (e.stdout or '')[-3000:])
            return 2
        print(r.stdout[-12000:])
        if r.stderr.strip():
            print('STDERR:', r.stderr[-3000:])
        return r.returncode
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
