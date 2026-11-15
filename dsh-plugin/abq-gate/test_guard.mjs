// node test_guard.mjs   — unit test of the command classifier (no Harness needed)
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { denialFor, fileDenial } from './index.js'

const root = mkdtempSync(join(tmpdir(), 'abqgate-'))
const gated = join(root, 'gated'), open = join(root, 'open'), off = join(root, 'off')
for (const d of [gated, open, off]) mkdirSync(join(d, 'models', 'm1'), { recursive: true })
mkdirSync(join(gated, '.abq')); writeFileSync(join(gated, '.abq', 'config.json'), '{"gate": "human"}')
mkdirSync(join(off, '.abq')); writeFileSync(join(off, '.abq', 'config.json'), '{"gate": "off"}')

const sub = join(gated, 'models', 'm1')
const cases = [
  // [command, cwd, shouldDeny]
  ['abaqus job=beam interactive', sub, true],
  ['abaqus job=beam cpus=4 int', gated, true],
  ['D:\\SIMULIA\\Commands\\abaqus.bat job=beam interactive ask_delete=OFF', sub, true],
  ['& "D:\\SIMULIA\\Commands\\abaqus.bat" cae noGUI=model.py', sub, true],
  ['cmd /c "abaqus cae noGUI=model.py"', sub, true],
  ['cd models\\m1; abaqus job=x', gated, true],
  ['abaqus job=beam datacheck interactive', sub, false],
  ['abaqus information=release', sub, false],
  ['abaqus fetch job=beamgap', sub, false],
  ['E:\\DSH-Abaqus\\abq.cmd run beam --cpus 4', sub, false],
  ['E:\\DSH-Abaqus\\abq.cmd script model.py', sub, false],
  ['py E:\\DSH-Abaqus\\tools\\abq\\abq.py run beam', sub, false],
  ['Get-Content beam.sta -Tail 5', sub, false],
  ['Select-String -Path job.msg -Pattern "abaqus job"', sub, false],
  ['echo "see the abaqus manual"', sub, false],
  ['abaqus job=beam interactive', join(open, 'models', 'm1'), false],
  ['abaqus job=beam interactive', join(off, 'models', 'm1'), false],
  // the gate itself is off limits to the agent
  ['E:\\DSH-Abaqus\\abq.cmd gate off', sub, true],
  ['E:\\DSH-Abaqus\\abq.cmd gate off; E:\\DSH-Abaqus\\abq.cmd run x', gated, true],
  ['py E:\\DSH-Abaqus\\tools\\abq\\abq.py gate off', sub, true],
  ['$env:ABQ_GATE="off"; E:\\DSH-Abaqus\\abq.cmd run x', sub, true],
  ["Set-Content E:\\DSH-Abaqus\\.abq\\config.json '{}'", sub, true],
  ['E:\\DSH-Abaqus\\abq.cmd gate status', sub, false],
  ['E:\\DSH-Abaqus\\abq.cmd gate on', sub, false],
  ['E:\\DSH-Abaqus\\abq.cmd gate off', join(off, 'models', 'm1'), false],
]
let bad = 0
const files = [
  [join(gated, '.abq', 'config.json'), sub, true],
  ['..\\..\\.abq\\review.json', sub, true],
  [join(sub, 'model.py'), sub, false],
  [join(off, '.abq', 'config.json'), join(off, 'models', 'm1'), false],
]
for (const [p, cwd, deny] of files) {
  const got = fileDenial(p, cwd) !== undefined
  if (got !== deny) { bad++; console.log('MISMATCH file', deny ? 'should deny' : 'should allow', '::', p) }
}

for (const [cmd, cwd, deny] of cases) {
  const got = denialFor(cmd, cwd) !== undefined
  if (got !== deny) { bad++; console.log('MISMATCH', deny ? 'should deny' : 'should allow', '::', cmd) }
}
rmSync(root, { recursive: true, force: true })
console.log(bad === 0 ? `all ${cases.length} cases OK` : `${bad} mismatches`)
process.exit(bad === 0 ? 0 : 1)
