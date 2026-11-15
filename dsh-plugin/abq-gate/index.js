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
// switching the gate off, overriding it through the environment, or touching its config / approval records
const TAMPER = /\babq(?:\.cmd|\.py)?["']?\s+gate\s+off\b|\bABQ_GATE\b|[\\/]\.abq[\\/](?:config|review)\.json/i
const GATE_DIR = /[\\/]\.abq[\\/]/

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

const TAMPER_MSG = '人工审查闸门由用户掌管，智能体不能关闭或绕过它（不能运行 abq gate off、不能设 ABQ_GATE、不能改 .abq 下的配置和批准记录）。' +
  '需要求解时：停下来向用户说明模型要点，请用户在预览面板里批准；只做数据检查用 abq datacheck。'

export function tamperDenial(command, cwd) {
  if (typeof command !== 'string' || !TAMPER.test(command)) return undefined
  return gatedRoot(cwd) === undefined ? undefined : TAMPER_MSG
}

// file tools (write / edit ...) aimed at the gate's own records
export function fileDenial(path, cwd) {
  if (typeof path !== 'string' || path === '') return undefined
  const full = isAbsolute(path) ? path : resolve(cwd, path)
  return GATE_DIR.test(full) && gatedRoot(cwd) !== undefined ? TAMPER_MSG : undefined
}

export function denialFor(command, cwd) {
  const tamper = tamperDenial(command, cwd)
  if (tamper !== undefined) return tamper
  if (typeof command !== 'string' || !RAW.test(command) || HARMLESS.test(command)) return undefined
  const root = gatedRoot(cwd)
  if (root === undefined) return undefined
  return '本工作区开启了人工审查闸门，不能直接调用 abaqus 启动求解或 CAE。' +
    '建模脚本用 E:\\DSH-Abaqus\\abq.cmd script <脚本.py>，求解用 E:\\DSH-Abaqus\\abq.cmd run <作业名>；' +
    'abq run 会核对用户是否已在预览面板批准这份 INP。若用户还没批准，请说明模型要点并停下等待，不要换别的方式绕过。'
}

export function apply(ctx) {
  ctx.tools.guard((exec) => {
    const args = exec.arguments ?? {}
    let cwd
    try {
      cwd = ctx.workingDirectory.get(exec.agent.session)
    } catch {
      return undefined
    }
    if (exec.name !== 'pwsh' && exec.name !== 'bash') {
      for (const key of ['path', 'file_path', 'filePath', 'target']) {
        const deny = fileDenial(args[key], cwd)
        if (deny !== undefined) return deny
      }
      return undefined
    }
    if (typeof args.workdir === 'string' && args.workdir !== '') {
      cwd = isAbsolute(args.workdir) ? args.workdir : resolve(cwd, args.workdir)
    }
    return denialFor(args.command, cwd)
  })
}
