/**
 * abq-gate — Host half of the Abaqus workspace plugin for DeepSeek Harness.
 *
 * In a workspace whose `.abq/config.json` says `"gate": "human"`, the shell tool may not launch an Abaqus solve
 * directly. Solves have to go through `abq run`, which checks that the analyst approved this exact INP in the
 * preview panel. The guard is monotonic (ctx.tools.guard): no other plugin can turn the denial back into permission.
 * Everywhere else, and for every other command, the guard stays out of the way.
 */
import { existsSync, readFileSync } from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'

export const name = 'abq-gate'
export const inject = ['tools', 'workingDirectory']

// an abaqus executable followed by something that starts a solver or a CAE kernel
const RAW = /(?:^|[\s;&|(`'"=])(?:[A-Za-z]:)?[\w\\/.-]*?abaqus(?:\.bat|\.exe)?["']?\s+[^\n;|&]*?\b(job\s*=|cae\b|standard\b|explicit\b)/i
// harmless uses of `abaqus job=...`
const HARMLESS = /\b(datacheck|syntaxcheck|information\s*=|fetch\b|findkeyword|whereami|terminate|suspend|resume)\b/i

function gatedRoot(start) {
  let dir = start
  for (let i = 0; i < 40; i++) {
    const cfg = join(dir, '.abq', 'config.json')
    if (existsSync(cfg)) {
      try {
        const gate = String(JSON.parse(readFileSync(cfg, 'utf8')).gate ?? 'off').toLowerCase()
        return gate === 'human' ? dir : undefined
      } catch {
        return undefined
      }
    }
    if (existsSync(join(dir, '.git'))) return undefined
    const up = dirname(dir)
    if (up === dir) return undefined
    dir = up
  }
  return undefined
}

export function denialFor(command, cwd) {
  if (typeof command !== 'string' || !RAW.test(command) || HARMLESS.test(command)) return undefined
  const root = gatedRoot(cwd)
  if (root === undefined) return undefined
  return '本工作区开启了人工审查闸门，不能直接调用 abaqus 启动求解或 CAE。' +
    '建模脚本用 E:\\DSH-Abaqus\\abq.cmd script <脚本.py>，求解用 E:\\DSH-Abaqus\\abq.cmd run <作业名>；' +
    'abq run 会核对用户是否已在预览面板批准这份 INP。若用户还没批准，请说明模型要点并停下等待，不要换别的方式绕过。'
}

export function apply(ctx) {
  ctx.tools.guard((exec) => {
    if (exec.name !== 'pwsh' && exec.name !== 'bash') return undefined
    const args = exec.arguments ?? {}
    let cwd
    try {
      cwd = ctx.workingDirectory.get(exec.agent.session)
    } catch {
      return undefined
    }
    if (typeof args.workdir === 'string' && args.workdir !== '') {
      cwd = isAbsolute(args.workdir) ? args.workdir : resolve(cwd, args.workdir)
    }
    return denialFor(args.command, cwd)
  })
}
