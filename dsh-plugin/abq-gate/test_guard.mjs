// node test_guard.mjs   — unit test of the command classifier (no Harness needed)
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { denialFor, fileDenial } from './index.js'

const root = mkdtempSync(join(tmpdir(), 'abqgate-'))
const gated = join(root, 'gated'), open = join(root, 'open'), off = join(root, 'off'), ans = join(root, 'answs')
for (const d of [gated, open, off, ans]) mkdirSync(join(d, 'models', 'm1'), { recursive: true })
mkdirSync(join(gated, '.abq')); writeFileSync(join(gated, '.abq', 'config.json'), '{"gate": "human"}')
mkdirSync(join(off, '.abq')); writeFileSync(join(off, '.abq', 'config.json'), '{"gate": "off"}')
mkdirSync(join(ans, '.abq')); writeFileSync(join(ans, '.abq', 'config.json'), '{"gate": "human"}')
writeFileSync(join(ans, 'ans.cmd'), '@py E:\\DSH-Abaqus\\tools\\ans\\ans.py %*')

const sub = join(gated, 'models', 'm1'), asub = join(ans, 'models', 'm1')
const EXE = '"D:\\Program Files\\ANSYS Inc\\v251\\ansys\\bin\\winx64\\ANSYS251.exe"'
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
  // MAPDL / ANSYS / Workbench started directly in a gated workspace
  [EXE + ' -b -i beam.inp -o beam.out', asub, true],
  ['& ' + EXE + ' -b -s noread -np 1 -i run.inp -o out.out', asub, true],
  ['ansys251 -b -i x.inp -o x.out', asub, true],
  ['ANSYS251.exe -b -i x.inp', asub, true],
  ['mapdl -b -i a.inp -o a.out', asub, true],
  ['& "D:\\Program Files\\ANSYS Inc\\v251\\ansys\\bin\\winx64\\MAPDL.exe" -b -np 4 -i a.inp', asub, true],
  ['cmd /c "ansys251 -b -i a.inp"', asub, true],
  ['Start-Process -FilePath ' + EXE + ' -ArgumentList "-b -i a.inp"', asub, true],
  ['runwb2 -B -R run.wbjn', asub, true],
  ['& "D:\\Program Files\\ANSYS Inc\\v251\\Framework\\bin\\Win64\\RunWB2.exe"', asub, true],
  ['cd models; ansys251 -b -i a.inp', ans, true],
  [EXE + ' -b -i beam.inp', sub, true],
  // the toolbox and ordinary commands are fine
  ['E:\\DSH-Ansys\\ans.cmd build beam', asub, false],
  ['E:\\DSH-Ansys\\ans.cmd run beam --cpus 2 --comp NTIP', asub, false],
  ['py E:\\DSH-Abaqus\\tools\\ans\\ans.py check beam', asub, false],
  ['py E:\\DSH-Abaqus\\tools\\preview\\mapdl_model.py beam.cdb', asub, false],
  ['Get-Content .ans\\beam\\run\\output.out -Tail 30', asub, false],
  ['Select-String -Path .ans\\beam\\run\\output.out -Pattern "ERROR"', asub, false],
  ['E:\\DSH-Ansys\\ans.cmd gate status', asub, false],
  ['echo "ans run checks the approval"', asub, false],
  ['ansys251 -b -i x.inp -o x.out', join(open, 'models', 'm1'), false],
  ['ansys251 -b -i x.inp -o x.out', join(off, 'models', 'm1'), false],
  // the gate itself
  ['E:\\DSH-Ansys\\ans.cmd gate off', asub, true],
  ['py E:\\DSH-Abaqus\\tools\\ans\\ans.py gate off', asub, true],
  ['$env:ABQ_GATE="off"; E:\\DSH-Ansys\\ans.cmd run beam', asub, true],
  ["Set-Content ..\\..\\.abq\\builds.json '{}'", asub, true],
  ["Set-Content ..\\..\\.abq\\review.json '{}'", asub, true],
]
let bad = 0
const files = [
  [join(gated, '.abq', 'config.json'), sub, true],
  ['..\\..\\.abq\\review.json', sub, true],
  [join(sub, 'model.py'), sub, false],
  [join(off, '.abq', 'config.json'), join(off, 'models', 'm1'), false],
  [join(ans, '.abq', 'builds.json'), asub, true],
  ['..\\..\\.abq\\review.json', asub, true],
  [join(asub, 'beam.build.mac'), asub, false],
  [join(asub, '.ans', 'beam', 'run', 'output.out'), asub, false],
]
for (const [p, cwd, deny] of files) {
  const got = fileDenial(p, cwd) !== undefined
  if (got !== deny) { bad++; console.log('MISMATCH file', deny ? 'should deny' : 'should allow', '::', p) }
}
// the wording follows the workspace: Abaqus messages stay as they were, the ANSYS workspace gets the ans ones
const msg = (cmd, cwd) => denialFor(cmd, cwd) || ''
const words = [
  [msg('E:\\DSH-Abaqus\\abq.cmd gate off', sub), 'abq gate off'],
  [msg('E:\\DSH-Ansys\\ans.cmd gate off', asub), 'ans gate off'],
  [msg('ansys251 -b -i x.inp', asub), 'ans.cmd run'],
  [fileDenial(join(ans, '.abq', 'review.json'), asub) || '', 'ans check'],
  [fileDenial(join(gated, '.abq', 'review.json'), sub) || '', 'abq datacheck'],
]
for (const [m, w] of words) {
  if (!m.includes(w)) { bad++; console.log('MISMATCH message should mention', w, '::', m.slice(0, 80)) }
}

for (const [cmd, cwd, deny] of cases) {
  const got = denialFor(cmd, cwd) !== undefined
  if (got !== deny) { bad++; console.log('MISMATCH', deny ? 'should deny' : 'should allow', '::', cmd) }
}
rmSync(root, { recursive: true, force: true })
console.log(bad === 0 ? `all ${cases.length} cases OK` : `${bad} mismatches`)
process.exit(bad === 0 ? 0 : 1)
