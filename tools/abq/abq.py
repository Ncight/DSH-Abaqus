#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""abq — Abaqus 工具箱（给智能体和人用的同一套命令）。

    abq check <file.inp>           静态体检 + 模型摘要（不调用 Abaqus）
    abq script <file.py>           运行 CAE 无界面脚本，报告错误与新生成的文件
    abq datacheck <job|file.inp>   求解器数据检查
    abq run <job|file.inp>         求解：自动体检、求解诊断、结果摘要、核查结论
    abq status <job>               查看作业状态（不启动求解）
    abq odb <file.odb>             结果摘要（最大值及位置、反力合计、能量、频率 …）
    abq python <file.py>           用 abaqus python 运行自己写的后处理脚本（读 ODB）
    abq verify <job>               收工前核查
    abq sweep <file.py> --set SEED=0.02,0.01   同一脚本多组参数依次求解并对比（网格收敛、方案比选）
    abq render <file.odb|file.inp> 出图 (PNG)
    abq examples / fetch           检索、取出 Abaqus 官方算例
    abq api <名字>                 查本机 Abaqus 脚本接口的真实签名
    abq rpy                        查看 CAE 回放文件里的脚本命令
    abq cases [关键词]             检索已验证案例库
    abq units                      单位制对照表
    abq gate status|on|off         人工审查闸门
每个子命令都支持 -h。Python 3，仅用标准库。
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import zipfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOLS)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TOOLS, 'preview'))
import abq_diag as diag  # noqa: E402

ABAQUS = os.environ.get('ABAQUS_BAT', r'D:\SIMULIA\Commands\abaqus.bat')
SAMPLES_ZIP = os.environ.get('ABQ_SAMPLES_ZIP',
                             r'D:\SIMULIA\EstProducts\2021\win_b64\SMA\samples\job_archive\samples.zip')
DATA = os.path.join(HERE, 'data')
CASES = os.environ.get('ABQ_CASES_DIR', os.path.join(PROJECT, 'cases'))
PREVIEW_PORT = int(os.environ.get('ABQ_PREVIEW_PORT', '8765'))
SELF = 'abq'
DOF = {1: 'U1', 2: 'U2', 3: 'U3', 4: 'UR1', 5: 'UR2', 6: 'UR3', 11: 'NT11'}

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass


def P(*lines):
    for l in lines:
        print(l)


def fnum(x, sig=6):
    """number -> plain decimal text, never scientific notation"""
    if x is None:
        return '—'
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if x == 0:
        return '0'
    s = '%.*g' % (sig, x)
    if 'e' in s or 'E' in s:
        s = format(Decimal(s), 'f')
    return s


def ftok(t):
    """INP data token -> plain number text when it is a float"""
    if re.match(r'^[-+]?(\d+\.?\d*|\.\d+)([eEdD][-+]?\d+)?$', t or '') and re.search(r'[.eEdD]', t):
        return fnum(float(re.sub('[dD]', 'e', t)), 8)
    return t


def row_text(row):
    return ', '.join(ftok(t) for t in row if t != '')


def params_text(p, skip=()):
    return ', '.join(k.lower() if v is True else '%s=%s' % (k.lower(), v) for k, v in (p or {}).items() if k not in skip)


def workspace_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, '.abq')) or os.path.exists(os.path.join(d, '.git')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start)
        d = up


def kill_tree(pid):
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


MIN_FREE_GB = 3.0          # never start Abaqus with less available commit memory than this
                           # (a memory-capped Abaqus process tree commits about 0.75 GB on this machine)


class LowMemory(Exception):
    pass


def commit_headroom_gb():
    """available commit charge (RAM + page file still unreserved), GB; None if it cannot be read"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPageFile / float(1 << 30)
    except Exception:
        return None


def wait_for_memory():
    """Other solvers may be running on this machine. Do not start Abaqus when the system is close to running out
    of commit memory: that can take the other jobs down with it. Wait a while, then give up with a clear message."""
    # The floor is fixed in code on purpose: an environment variable may only make the limit stricter, never looser.
    try:
        need = max(MIN_FREE_GB, float(os.environ.get('ABQ_MIN_FREE_GB', '0')))
    except ValueError:
        need = MIN_FREE_GB
    max_wait = 90.0
    t0 = time.time()
    told = False
    while True:
        free = commit_headroom_gb()
        if free is None or free >= need:
            return
        if not told:
            print('⏸ 系统可用内存只有 %s GB（安全下限 %s GB），本机有其他任务在占用内存；先等待最多 %d 秒…' % (
                fnum(free, 3), fnum(need, 3), int(max_wait)), flush=True)
            told = True
        if time.time() - t0 >= max_wait:
            raise LowMemory('系统可用内存仍只有 %s GB（安全下限 %s GB），没有启动 Abaqus。\n'
                            '这是保护本机其他正在运行的求解任务的安全限制，不是模型或命令的问题。\n'
                            '正确做法：向用户如实说明"本机内存被其他任务占用，Abaqus 暂时无法启动"，过几分钟再重试同一条命令；'
                            '已经完成的步骤不用重做。\n'
                            '不要尝试绕过：不要修改环境变量或工具设置，不要结束任何不是你自己启动的进程，不要改用直接调用 abaqus。'
                            % (fnum(free, 3), fnum(need, 3)))
        time.sleep(10)


def run_abaqus(args, cwd, log_path, timeout, env=None):
    """run abaqus with output to a file (no pipes: works inside restricted sandboxes) -> (code, timed_out, sec, log)"""
    wait_for_memory()
    flags = 0x00004000 if os.environ.get('ABQ_LOW_PRIORITY') else 0      # BELOW_NORMAL_PRIORITY_CLASS
    t0 = time.time()
    timed_out = False
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    with open(log_path, 'wb') as lf:
        p = subprocess.Popen(['cmd.exe', '/d', '/c', ABAQUS] + list(args), cwd=cwd, stdout=lf,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=full_env, creationflags=flags)
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            timed_out = True
            code = None
    return code, timed_out, time.time() - t0, diag.read_text(log_path)


def memory_arg(requested=None):
    """`memory=` for the solver. Abaqus defaults to 90% of physical RAM, which it may fail to obtain when other
    solvers are running on the machine. Unless the user asks for more, cap it to a share of what is actually free."""
    if requested:
        return 'memory=' + str(requested).replace(' ', '')
    free = commit_headroom_gb()
    if free is None:
        return None
    gb = max(1.5, min(8.0, free * 0.4))
    return 'memory=%d' % int(gb * 1024)


def run_solver(args, cwd, log_path, timeout):
    """run_abaqus for analysis jobs, retrying once when the failure was only a transient lack of memory"""
    res = run_abaqus(args, cwd, log_path, timeout)
    if re.search(r'failed to obtain memory|unable to allocate', res[3], re.I) and not res[1]:
        time.sleep(30)
        res = run_abaqus(args, cwd, log_path, timeout)
    return res


def preview_alive():
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', PREVIEW_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def preview_line():
    if preview_alive():
        return '预览面板: http://127.0.0.1:%d/ （自动显示工作区内最新的 .inp）' % PREVIEW_PORT
    return '预览面板未启动（人工查看几何/边界/接触用）: 运行 %s' % os.path.join(TOOLS, 'preview', 'start_preview.cmd')


# ---------------------------------------------------------------------------- gate
def gate_mode(root):
    env = os.environ.get('ABQ_GATE')
    if env:
        return env.lower()
    cfg = os.path.join(root, '.abq', 'config.json')
    if os.path.isfile(cfg):
        try:
            with open(cfg, encoding='utf-8') as f:
                return str(json.load(f).get('gate', 'off')).lower()
        except (ValueError, OSError):
            return 'off'
    return 'off'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def review_entry(root, inp):
    """-> (entry or None, is_current)"""
    state = os.path.join(root, '.abq', 'review.json')
    if not os.path.isfile(state):
        return None, False
    try:
        with open(state, encoding='utf-8') as f:
            files = json.load(f).get('files', {})
    except (ValueError, OSError):
        return None, False
    rel = os.path.relpath(os.path.abspath(inp), root).replace('\\', '/')
    ent = files.get(rel)
    if ent is None:
        low = dict((k.lower(), v) for k, v in files.items())
        ent = low.get(rel.lower())
    if ent is None:
        return None, False
    return ent, ent.get('sha256') == sha256_file(inp)


def review_text(root, inp):
    md = os.path.join(root, '.abq', 'review', os.path.splitext(os.path.basename(inp))[0] + '.md')
    return diag.read_text(md).strip()


def gate_check(inp):
    """-> (allowed, message_lines)"""
    root = workspace_root(os.path.dirname(os.path.abspath(inp)))
    if gate_mode(root) != 'human':
        return True, []
    ent, current = review_entry(root, inp)
    name = os.path.basename(inp)
    if ent and current and ent.get('status') == 'approved':
        return True, ['人工审查: 已批准 (%s)' % ent.get('decidedAt', '')]
    lines = ['⛔ 人工审查闸门: %s 尚未获准求解。' % name]
    if ent and ent.get('status') == 'rejected' and current:
        lines.append('审查结论: 已退回。请按下面的审查意见修改模型，重新导出 INP 后再请用户审查:')
        txt = review_text(root, inp)
        if txt:
            lines.append(txt)
        else:
            if ent.get('note'):
                lines.append('  总体意见: %s' % ent['note'])
            for i, c in enumerate(ent.get('comments') or [], 1):
                lines.append('  %d. %s — 当前: %s — 要求: %s' % (i, c.get('target', ''), c.get('current', ''), c.get('note', '')))
    elif ent and not current:
        lines.append('该 INP 在上次审查（%s）之后有改动，需要重新审查。' % ent.get('status'))
    else:
        lines.append('该 INP 还没有被审查过。')
    lines.append('请用户在预览面板里检查几何、边界、载荷、接触后点「批准求解」或「退回修改」: http://127.0.0.1:%d/' % PREVIEW_PORT)
    lines.append('此时应停下来向用户说明模型要点并等待审查结论；不要绕过闸门直接调用 abaqus。数据检查 (abq datacheck) 不受闸门限制。')
    return False, lines


# ---------------------------------------------------------------------------- check
LAST_STATS = {}         # stats of the input file inp_summary looked at last (node count decides the CPU count in abq run)


def inp_summary(path, brief=False):
    """-> (lines, n_error, n_warn)"""
    import inp_model
    m = inp_model.Model(path)
    v = m.view()
    st = v['stats']
    LAST_STATS.clear()
    LAST_STATS.update({'path': os.path.abspath(path), 'nodes': st['nodes'], 'elements': st['elements']})
    L = []
    b = st['bbox']
    L.append('文件: %s' % path)
    L.append('规模: %d 节点, %d 单元 (%s); %s' % (
        st['nodes'], st['elements'], ', '.join('%s×%d' % kv for kv in sorted(st['types'].items())),
        '平铺式 INP' if st.get('flat') else '%d 个实例' % len(v['instances'])))
    L.append('范围: X %s ~ %s, Y %s ~ %s, Z %s ~ %s' % (fnum(b[0][0]), fnum(b[1][0]), fnum(b[0][1]), fnum(b[1][1]),
                                                       fnum(b[0][2]), fnum(b[1][2])))
    if not brief:
        if v['instances']:
            L.append('实例: ' + '; '.join('%s(部件 %s, %d 单元)' % (i['name'], i['part'], i['elements']) for i in v['instances']))
        L.append('材料:')
        for mt in v['materials']:
            parts = []
            for o in mt['options']:
                head = o['kw'] + (('[' + params_text(o['params']) + ']') if o['params'] else '')
                rows = '; '.join(row_text(r) for r in o['rows'][:3])
                more = ' …共%d行' % o['nrows'] if o['nrows'] > 3 else ''
                parts.append('%s: %s%s' % (head, rows, more))
            note = ''
            if mt.get('c') is not None:
                note = '  [√(E/ρ)=%s, %s]' % (fnum(mt['c']), mt.get('cNote', ''))
            L.append('  - %s | %s%s' % (mt['name'], ' | '.join(parts) or '(无属性)', note))
        if not v['materials']:
            L.append('  (无)')
        L.append('截面:')
        for s in v['sections']:
            L.append('  - %s: 材料 %s, 单元集 %s, %d 单元%s' % (s['type'], s['material'] or '—', s['elset'] or '—', s['count'],
                                                         (' [' + params_text(s['params']) + ']') if s['params'] else ''))
        if not v['sections']:
            L.append('  (无)')
        L.append('边界条件:')
        bcs = list(v['initialBcs']) + [bc for s in v['steps'] for bc in s['bcs']]
        for bc in bcs:
            mag = ''.join(' 给定值 %s=%s' % (DOF.get(a, a), fnum(mg)) for a, z, mg in bc['mag'])
            L.append('  - [%s] %s: %s (%d 节点)%s%s' % (bc['step'] or 'Initial', bc['target'],
                                                    bc['type'] or ','.join(DOF.get(d, str(d)) for d in bc['dofs']), bc['n'], mag,
                                                    (' [' + params_text(bc['params']) + ']') if bc['params'] else ''))
        if not bcs:
            L.append('  (无)')
        L.append('载荷:')
        loads = [l for s in v['steps'] for l in s['loads']]
        for l in loads:
            rows = '; '.join(row_text(r) for r in l['rows'][:3])
            L.append('  - [%s] *%s%s: %s%s' % (l['step'], l['kw'], (', ' + params_text(l['params'])) if l['params'] else '',
                                              rows, ' …共%d行' % l['nrows'] if l['nrows'] > 3 else ''))
        if not loads:
            L.append('  (无)')
        inter = []
        for t in v['ties']:
            inter.append('Tie %s: 从面 %s ← 主面 %s' % (t['name'], t['slave'], t['master']))
        for c in v['contactPairs']:
            inter.append('接触对: 从面 %s / 主面 %s, 属性 %s' % (c['slave'], c['master'], c['interaction'] or '—'))
        if v.get('generalContact'):
            inter.append('通用接触 (*Contact)')
        for c in v.get('constraints', []):
            inter.append('*%s %s' % (c['kw'], params_text(c.get('params'))))
        L.append('相互作用/约束: ' + ('; '.join(inter) if inter else '(无)'))
        L.append('分析步:')
        for s in v['steps']:
            pd = '; '.join(row_text(r) for r in s['procData'][:1])
            outs = []
            for o in s['outputs']:
                if o['vars']:
                    outs.append('%s: %s' % (o['kw'], ','.join(o['vars'][:12])))
                elif o['params']:
                    outs.append(params_text(o['params']))
            L.append('  - %s: *%s%s%s | 时间数据: %s | 输出: %s' % (
                s['name'], s['proc'] or '?', (', ' + params_text(s['procParams'])) if s['procParams'] else '',
                (' [' + params_text(s['params'], ('NAME',)) + ']') if params_text(s['params'], ('NAME',)) else '',
                pd or '—', ' / '.join(outs) or '默认'))
        if not v['steps']:
            L.append('  (无)')
        vis = [s for s in v['nsets'] if not s['internal'] and not s['name'].split('.')[-1].startswith('_')]
        L.append('节点集: ' + (', '.join('%s(%d)' % (s['name'], s['n']) for s in vis[:30]) or '(无)'))
        vis = [s for s in v['surfaces'] if not s['internal'] and not s['name'].split('.')[-1].startswith('_')]
        L.append('表面: ' + (', '.join('%s(%d)' % (s['name'], s['n']) for s in vis[:30]) or '(无)'))
    tag = {'error': '错误', 'warn': '警告', 'info': '提示', 'ok': '通过'}
    ne = sum(1 for c in v['checks'] if c['level'] == 'error')
    nw = sum(1 for c in v['checks'] if c['level'] == 'warn')
    L.append('体检: %d 错误, %d 警告' % (ne, nw))
    for c in v['checks']:
        L.append('  [%s] %s' % (tag.get(c['level'], c['level']), c['msg']))
    return L, ne, nw


def cmd_check(a):
    path = os.path.abspath(a.inp)
    if not os.path.isfile(path):
        P('找不到文件: %s' % path)
        return 2
    try:
        lines, ne, nw = inp_summary(path, brief=a.brief)
    except Exception as e:
        P('INP 解析失败: %s: %s' % (type(e).__name__, e), '（解析器的问题不代表 INP 有错；可直接用 abq datacheck 让求解器检查）')
        return 2
    P(*lines)
    root = workspace_root(os.path.dirname(path))
    if gate_mode(root) == 'human':
        ent, cur = review_entry(root, path)
        P('人工审查: ' + ('已批准' if ent and cur and ent.get('status') == 'approved' else
                      '已退回' if ent and cur and ent.get('status') == 'rejected' else
                      '文件已变更，需重新审查' if ent else '尚未审查'))
    P(preview_line())
    if ne:
        P('结论: 有错误，先修正再做数据检查。')
    else:
        P('下一步: abq datacheck %s' % os.path.basename(path))
    return 1 if ne else 0


# ---------------------------------------------------------------------------- script
def snapshot(d):
    out = {}
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            st = os.stat(p)
            out[f] = (st.st_mtime, st.st_size)
    return out


def to_utf8(path, comments_only=False, max_mb=64):
    """An Abaqus installed with Chinese messages writes GBK text (boundary-condition type names in the comments of an
    input file, the month in the date line of .dat/.msg/.sta); tools that expect UTF-8 then refuse the whole file.
    Rewrite such a file as UTF-8.  comments_only: leave the file alone unless all non-ASCII text is in ** comment lines.
    -> True when the file was rewritten"""
    try:
        if os.path.getsize(path) > max_mb * 1048576:
            return False
        with open(path, 'rb') as f:
            raw = f.read()
        raw.decode('utf-8')
        return False
    except UnicodeDecodeError:
        pass
    except OSError:
        return False
    text = raw.decode('gbk', errors='replace')
    if comments_only and any(not l.startswith('**') and any(ord(c) > 127 for c in l) for l in text.splitlines()):
        return False
    try:
        with open(path, 'wb') as f:
            f.write(text.encode('utf-8'))
    except OSError:
        return False
    return True


def job_text_to_utf8(cwd, job):
    for ext in ('.dat', '.msg', '.sta'):
        to_utf8(os.path.join(cwd, job + ext))


def localize(path):
    """A template that lives in a skills folder is run from a copy in the current directory: Abaqus writes its files next
    to the script, and job files must not end up among the skills."""
    if '/skills/' not in path.replace('\\', '/').lower() or os.path.dirname(path) == os.getcwd():
        return path
    dst = os.path.join(os.getcwd(), os.path.basename(path))
    if os.path.isfile(dst):
        P('（当前目录已有同名文件 %s，运行的是当前目录这一份）' % os.path.basename(dst))
    else:
        with open(path, 'rb') as fi, open(dst, 'wb') as fo:
            fo.write(fi.read())
        P('（模板已复制到当前目录 %s，之后改写和运行都用这一份）' % os.path.basename(dst))
    return dst


def cmd_script(a):
    path = os.path.abspath(a.script)
    if not os.path.isfile(path):
        P('找不到脚本: %s' % path)
        return 2
    path = localize(path)
    cwd, name = os.path.dirname(path), os.path.basename(path)
    if ' ' in name:
        P('脚本文件名不能含空格: %s' % name)
        return 2
    src = diag.read_text(path)
    pre = []
    code_only = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
    if re.search(r'\.submit\s*\(', code_only) and gate_mode(workspace_root(cwd)) == 'human':
        P('⛔ 人工审查闸门已开启：建模脚本里不能提交作业（发现 .submit( 调用）。',
          '把脚本改成只导出 INP：mdb.Job(name=..., model=...).writeInput(consistencyChecking=OFF)，',
          '然后 abq check → abq datacheck → 请用户在预览面板审查 → abq run。')
        return 3
    if 'caeModules' not in src and re.search(r'\.(Tie|ContactProperty|\w+Step|\w+BC|\w+Section|Pressure|ConcentratedForce)\(', src):
        pre.append('提醒: 脚本没有 from caeModules import *，创建分析步/载荷/相互作用时很可能报 has no attribute。')
    before = snapshot(cwd)
    log = os.path.join(cwd, os.path.splitext(name)[0] + '.cae.log')
    args = ['cae', 'noGUI=' + name]
    if a.args:
        args += ['--'] + a.args
    code, to, sec, text = run_abaqus(args, cwd, log, a.timeout)
    after = snapshot(cwd)
    changed = sorted(f for f in after if after[f] != before.get(f) and f != os.path.basename(log))
    tb = [l for l in text.splitlines() if l.strip()]
    failed = to or bool(re.search(r'(?m)^(\w*Error|\w*Exception|Traceback|SyntaxError)|Abaqus Error|exited with an error', text))
    P(*pre)
    P('脚本 %s — %s (用时 %s s)' % (name, '超时已终止 (%d s)' % a.timeout if to else '失败 ✗' if failed else '完成 ✓', fnum(sec, 3)))
    shown = [l for l in tb if not re.search(r'License Manager|licenses remain|from Flexnet server|^"cae"', l)]
    if failed:
        P('输出（末尾 %d 行）:' % min(len(shown), 30), *['  ' + l for l in shown[-30:]])
        hints = diag.match_hints([text], diag.load_hints())
        if hints:
            P('提示:', *['  - ' + h for h in hints])
        P('  - 查方法签名: abq api <方法名>；查官方脚本怎么写: abq examples --api <方法名>')
    elif shown:
        P('输出:', *['  ' + l for l in shown[-15:]])
    keep = [f for f in changed if not f.endswith(('.rpy', '.rec', '.lck')) and not re.search(r'\.rpy\.\d+$', f)]
    for f in keep:
        if f.lower().endswith('.inp'):
            to_utf8(os.path.join(cwd, f), comments_only=True)
    if keep:
        P('新生成/更新的文件: ' + ', '.join(keep[:40]))
    rc = 1 if failed else 0
    for f in keep:
        if f.lower().endswith('.inp') and not failed:
            P('', '— 自动体检 %s —' % f)
            try:
                lines, ne, nw = inp_summary(os.path.join(cwd, f), brief=True)
                P(*lines)
                if ne:
                    rc = 1
            except Exception as e:
                P('INP 解析失败: %s: %s' % (type(e).__name__, e))
    if not failed and any(f.lower().endswith('.inp') for f in keep):
        P(preview_line(), '下一步: abq check <inp>（看完整摘要）→ abq datacheck <inp> → abq run <inp>')
    return rc


def cmd_python(a):
    path = os.path.abspath(a.script)
    if not os.path.isfile(path):
        P('找不到脚本: %s' % path)
        return 2
    path = localize(path)
    cwd, name = os.path.dirname(path), os.path.basename(path)
    log = os.path.join(cwd, os.path.splitext(name)[0] + '.py.log')
    code, to, sec, text = run_abaqus(['python', name] + list(a.args), cwd, log, a.timeout)
    lines = [l for l in text.splitlines() if l.strip()]
    failed = to or bool(re.search(r'(?m)^(Traceback|\w*Error:|\w*Exception:)|Abaqus Error', text))
    P('abaqus python %s — %s (用时 %s s)' % (name, '超时已终止' if to else '失败 ✗' if failed else '完成 ✓', fnum(sec, 3)))
    if lines:
        P('输出%s:' % ('（末尾 60 行）' if len(lines) > 60 else ''), *['  ' + l for l in lines[-60:]])
    if failed:
        hints = diag.match_hints([text], diag.load_hints())
        if hints:
            P('提示:', *['  - ' + h for h in hints])
        P('  - 这是 Abaqus 自带的 Python 2.7（print 语句、无 f-string）；读 ODB 的对象方法可用 abq api <名字> 查。')
    return 1 if failed else 0


# ---------------------------------------------------------------------------- datacheck / run / status
def resolve_job(arg):
    p = os.path.abspath(arg)
    if p.lower().endswith('.inp'):
        p = p[:-4]
    return os.path.dirname(p), os.path.basename(p)


def print_diag(d, verbose=True):
    sta = d['sta']
    if sta['exists']:
        if sta['explicit']:
            last = sta['last'] or {}
            P('显式求解: %d 个增量; 末行 step time %s, total time %s, 稳定步长 %s' % (
                sta['increments'], last.get('stepTime', '—'), last.get('totalTime', '—'), last.get('stableInc', '—')))
        else:
            last = sta['last'] or {}
            P('增量步: %d 个分析步, %d 个收敛增量, 折减 %d 次; 末行 step %s inc %s, step time %s, total time %s' % (
                sta['steps'], sta['increments'], sta['cutbacks'], last.get('step', '—'), last.get('inc', '—'),
                last.get('stepTime', '—'), last.get('totalTime', '—')))
    if d['errors']:
        P('错误 (%d 类):' % len(d['errors']))
        for n, t in d['errors'][:8]:
            P('  ×%d  %s' % (n, t[:600]))
    warns, benign = diag.split_benign(d['warnings'])
    if warns:
        P('警告 (%d 类%s):' % (len(warns), '，只列前 8 类' if len(warns) > 8 else ''))
        for n, t in warns[:8]:
            P('  ×%d  %s' % (n, t[:400]))
    if benign:
        P('（另有 %d 类无诊断意义的常见提示已省略，原文见 .dat/.msg）' % benign)
    if d['residuals'] and verbose:
        P('.msg 末尾的收敛信息（残差/修正最大的位置就是出问题的地方）:', *['  ' + l for l in d['residuals']])
    texts = [t for _, t in d['errors']] + [t for _, t in d['warnings']]
    hints = diag.match_hints(texts, diag.load_hints())
    if hints:
        P('提示:', *['  - ' + h for h in hints])


def precheck(inp, force):
    try:
        lines, ne, nw = inp_summary(inp, brief=True)
    except Exception as e:
        P('（静态体检跳过: %s）' % e)
        return True
    if ne and not force:
        P('静态体检有错误，未提交求解器:', *lines[-(ne + nw + 2):], '修正后重试；若确认是体检误报，加 --force。')
        return False
    if ne or nw:
        P(*lines[-(ne + nw + 1):])
    return True


def cmd_datacheck(a):
    cwd, job = resolve_job(a.job)
    inp = os.path.join(cwd, job + '.inp')
    if not os.path.isfile(inp):
        P('找不到 %s' % inp)
        return 2
    if not precheck(inp, a.force):
        return 1
    log = os.path.join(cwd, job + '.abqlog')
    dargs = ['job=' + job, 'datacheck', 'interactive', 'ask_delete=OFF']
    if memory_arg():
        dargs.append(memory_arg())
    code, to, sec, text = run_solver(dargs, cwd, log, a.timeout)
    job_text_to_utf8(cwd, job)
    d = diag.diagnose(cwd, job, text)
    ok = not d['errors'] and not to and 'COMPLETED' in text
    P('数据检查 %s — %s (用时 %s s)' % (job, '超时' if to else '通过 ✓' if ok else '未通过 ✗', fnum(sec, 3)))
    print_diag(d, verbose=False)
    if ok:
        P('下一步: abq run %s' % job)
    return 0 if ok else 1


SMALL_MODEL_NODES = 20000


def node_count(inp):
    """number of nodes of an input file (from the last static check when it was this file, else a fresh parse)"""
    if LAST_STATS.get('path') == os.path.abspath(inp):
        return LAST_STATS.get('nodes')
    try:
        import inp_model
        return inp_model.Model(inp).view(geom=False)['stats']['nodes']
    except Exception:
        return None


def effective_cpus(requested, inp, forced=False):
    """-> (cpus to use, note).  Small models run on one core whatever was asked for.  Measured on this machine
    (Abaqus 2021, eval/skill_checks/cpu_bench.py): a parallel job starts about 9 s later and pays 0.05-0.1 s per increment
    (100 beam elements, 1210 increments: 174 s on 2 cores, 30 s on 1; 2,000 solid elements, one increment: 13.5 s on 4
    cores, 4.5 s on 1); four cores only win from about 40,000 solid elements up (120,000 elements: 34 s against 57 s)."""
    requested = max(1, int(requested or 1))
    if requested == 1 or forced:
        return requested, ''
    n = node_count(inp)
    if n is not None and n < SMALL_MODEL_NODES:
        return 1, '模型只有 %d 个节点，这种规模多核并行反而更慢，已改用 1 核（坚持多核: --force-cpus）' % n
    return requested, ''


def cmd_run(a):
    cwd, job = resolve_job(a.job)
    inp = os.path.join(cwd, job + '.inp')
    if not os.path.isfile(inp):
        P('找不到 %s （先用脚本 writeInput 导出 INP，或给出正确的作业名）' % inp)
        return 2
    if not precheck(inp, a.force):
        return 1
    allowed, lines = gate_check(inp)
    P(*lines)
    if not allowed:
        return 3
    args = ['job=' + job, 'interactive', 'ask_delete=OFF']
    a.cpus, cpu_note = effective_cpus(a.cpus, inp, getattr(a, 'force_cpus', False))
    if cpu_note:
        P(cpu_note)
    if a.cpus and a.cpus > 1:
        args.append('cpus=%d' % a.cpus)
    if a.user:
        args.append('user=' + a.user)
    if a.double:
        args.append('double=both')
    mem = memory_arg(a.memory)
    if mem:
        args.append(mem)
    log = os.path.join(cwd, job + '.abqlog')
    code, to, sec, text = run_solver(args, cwd, log, a.timeout)
    job_text_to_utf8(cwd, job)
    write_runinfo(cwd, job)
    d = diag.diagnose(cwd, job, text)
    state = '超时已终止 (%d s)，可加大 --timeout 或改用后台运行' % a.timeout if to else \
        '完成 ✓' if d['state'] == 'completed' else '失败 ✗'
    P('作业 %s — %s (用时 %s s%s)' % (job, state, fnum(sec, 3), ', cpus=%d' % a.cpus if a.cpus > 1 else ''))
    print_diag(d)
    if d['state'] == 'completed' and not to:
        if a.no_post:
            P('下一步: abq odb %s.odb （结果摘要）→ abq verify %s （收工前核查）' % (job, job))
            return 0
        data, err = extract(os.path.join(cwd, job + '.odb'))
        if data is None:
            P('结果库读取失败: %s' % err)
        else:
            P('—— 结果摘要（模型自身单位制下的原始值）——', *odb_digest(data))
        verdict, _, _ = verify_job(cwd, job, d, data)
        P('更多细节: abq odb %s.odb（--step 名字 --frame N 指定帧；--probe x,y,z --series S,U 取某点时程；'
          '--set 集合名 --series RF 取集合上的合计/平均及其时程；--sets 列出集合）。以上数值的全精度版本在 %s，'
          '写结果文件时可用系统 Python 直接读它（如 frequencies[0].frequency、fields.RF.sum.RF2、fields.U.maxMagnitude.value）。' % (
            job, os.path.basename((data or {}).get('_json', '') or job + '.summary.json')))
        return 0 if verdict != 'FAIL' else 1
    if not d['errors'] and not to:
        tail = [l for l in text.splitlines() if l.strip()][-12:]
        P('求解器输出末尾:', *['  ' + l for l in tail])
    return 1


def cmd_status(a):
    cwd, job = resolve_job(a.job)
    d = diag.diagnose(cwd, job)
    names = {'completed': '已完成 ✓', 'failed': '失败 ✗', 'running': '运行中', 'checked': '仅做过数据检查', 'unknown': '未知/未运行'}
    P('作业 %s: %s' % (job, names[d['state']]))
    print_diag(d)
    if d['sta']['tail']:
        P('.sta 末尾:', *['  ' + l for l in d['sta']['tail'][-6:]])
    return 0 if d['state'] in ('completed', 'running', 'checked') else 1


# ---------------------------------------------------------------------------- odb
def extract(odb, extra=(), timeout=900):
    """run the extractor -> (dict or None, error text)"""
    cwd = os.path.dirname(os.path.abspath(odb))
    out = os.path.splitext(os.path.abspath(odb))[0] + '.summary.json'
    log = os.path.splitext(os.path.abspath(odb))[0] + '.summary.log'
    if os.path.isfile(out):
        os.remove(out)
    args = ['python', os.path.join(HERE, 'odb_extract.py'), os.path.abspath(odb), out] + list(extra)
    code, to, sec, text = run_abaqus(args, cwd, log, timeout)
    if not os.path.isfile(out):
        return None, ('提取超时' if to else '提取失败') + ': ' + ' | '.join(text.splitlines()[-6:])
    with open(out, encoding='utf-8') as f:
        d = json.load(f)
    if d.get('error'):
        return None, d['error']
    d['_json'] = out
    return d, ''


def loc_text(at):
    if not at:
        return ''
    s = []
    if at.get('instance'):
        s.append(at['instance'])
    if 'node' in at:
        s.append('节点 %d' % at['node'])
    if 'element' in at:
        s.append('单元 %d' % at['element'])
    c = at.get('coords') or at.get('centroid')
    if c:
        s.append('(%s)' % ', '.join(fnum(v, 5) for v in c))
    return ' @ ' + ' '.join(s)


def zf(v, ref):
    """format v, showing numerical noise (relative to ref) as 0"""
    if v is None:
        return '—'
    if ref and abs(v) < 1e-9 * abs(ref):
        return '0'
    return fnum(v)


def odb_digest(d, full=False, focus=False):
    """text lines of an extractor result; focus=True (a probe or a set was asked for) leaves the whole-model lines out"""
    L = []
    for s in d.get('steps', []):
        L.append('分析步 %s: %s, %d 帧, 时长 %s' % (s['name'], s['procedure'], s['nFrames'], fnum(s['timePeriod'])))
    if d.get('frequencies'):
        L.append('固有频率 (Hz): ' + ', '.join('%d阶 %s' % (f['mode'], fnum(f['frequency'])) for f in d['frequencies'][:20]))
    if d.get('eigenvalues'):
        L.append('屈曲特征值: ' + ', '.join('%d阶 %s' % (f['mode'], fnum(f['value'])) for f in d['eigenvalues'][:20]))
    fr = d.get('frame')
    F = d.get('fields', {})
    if fr and fr.get('modeShape'):
        L.append('（特征值分析步的帧是归一化的振型/屈曲模态，位移和应力数值没有物理意义，这里不列；'
                 '看某一阶的形状: abq render <odb> --var U --step %s --frame 阶次）' % fr['step'])
        F = {}
    elif fr:
        L.append('以下场量取自: 分析步 %s 第 %d 帧 (frame value %s)' % (fr['step'], fr['index'], fnum(fr['frameValue'])))
    order = ['U', 'UR', 'RF', 'RM', 'CF', 'CM', 'S', 'E', 'LE', 'PE', 'PEEQ', 'CPRESS', 'COPEN', 'NT11', 'V', 'A']
    names = [n for n in order if n in F] + sorted(n for n in F if n not in order)

    # rotations / moments that are pure round-off: judged against the translational field and the size of the model
    def peak(n):
        f = F.get(n) or {}
        return (f.get('maxMagnitude') or {}).get('value') or 0.0

    bb = d.get('bbox')
    size = max([abs(bb[1][i] - bb[0][i]) for i in range(3)]) if bb else 0.0
    noise_ref = {'UR': peak('U') / size if size else 0.0, 'VR': peak('V') / size if size else 0.0,
                 'AR': peak('A') / size if size else 0.0, 'RM': peak('RF') * size, 'CM': peak('CF') * size}
    noise = set(n for n in names if n in noise_ref and noise_ref[n] and 'components' in F[n]
                and peak(n) < 1e-9 * noise_ref[n])
    for name in ([] if focus else names):
        f = F[name]
        if 'error' in f:
            L.append('%s: 提取失败 %s' % (name, f['error']))
            continue
        if name in noise:
            L.append('%s: 全场为 0（只有舍入误差量级的数值）' % name)
            continue
        if 'invariants' in f:
            inv, en = f.get('invariants', {}), f.get('elementNodal', {})
            if name == 'S' or full:
                for key, label in (('mises', 'Mises'), ('maxPrincipal', '最大主值'), ('minPrincipal', '最小主值')):
                    if key in inv:
                        v = inv[key]
                        extra = ''
                        if key in en:
                            extra = ' | 外推到单元节点: max %s, min %s' % (fnum(en[key]['max']), fnum(en[key]['min']))
                        L.append('%s %s (积分点): max %s%s, min %s%s' % (name, label, fnum(v['max']), loc_text(v.get('maxAt')),
                                                                       fnum(v['min']), extra))
                comp = f.get('components', {})
                if comp:
                    cref = max([abs(c['max']) for c in comp.values()] + [abs(c['min']) for c in comp.values()] + [0.0])
                    L.append('%s 分量 (积分点): ' % name + '; '.join('%s %s ~ %s' % (k, zf(c['min'], cref), zf(c['max'], cref))
                                                                  for k, c in sorted(comp.items())))
            else:
                v = inv.get('maxPrincipal') or inv.get('mises')
                if v:
                    L.append('%s: 最大主值 max %s, 最小主值 min %s' % (name, fnum(v['max']),
                                                               fnum(inv.get('minPrincipal', v)['min'])))
        elif 'components' in f:
            mm = f.get('maxMagnitude')
            ref = mm['value'] if mm else max([abs(c['max']) for c in f['components'].values()] +
                                             [abs(c['min']) for c in f['components'].values()] + [0.0])
            line = '%s: ' % name
            if mm:
                line += '最大合量 %s%s; ' % (fnum(mm['value']), loc_text(mm.get('at')))
            line += '分量范围 ' + '; '.join('%s %s ~ %s' % (k, zf(c['min'], ref), zf(c['max'], ref)) for k, c in sorted(f['components'].items()))
            L.append(line)
            if 'sum' in f:
                sref = max([abs(v) for v in f['sumPos'].values()] + [abs(v) for v in f['sumNeg'].values()] + [0.0])
                L.append('%s 合计: %s  （正值合计 %s; 负值合计 %s）' % (
                    name, ', '.join('%s=%s' % (k, zf(v, sref)) for k, v in sorted(f['sum'].items())),
                    ', '.join(zf(v, sref) for k, v in sorted(f['sumPos'].items())),
                    ', '.join(zf(v, sref) for k, v in sorted(f['sumNeg'].items()))))
        elif 'max' in f:
            L.append('%s: max %s%s, min %s' % (name, fnum(f['max']), loc_text(f.get('maxAt')), fnum(f['min'])))
    h = d.get('history') or {}
    en = [(k, v) for k, v in h.items() if isinstance(v, dict) and 'last' in v and v.get('maxAbs')]
    if en and not focus:
        L.append('能量(末值): ' + ', '.join('%s=%s' % (k, fnum(v['last'])) for k, v in sorted(en)))
    rt = [(k, v) for k, v in sorted((h.get('ratios') or {}).items()) if v]
    if rt and not focus:
        L.append('能量比: ' + ', '.join('%s=%s' % (k, fnum(v, 4)) for k, v in rt))

    def gref(name):                      # scale of the whole field: what counts as round-off on a point or a set
        f = F.get(name) or {}
        if f.get('maxMagnitude'):
            return f['maxMagnitude'].get('value') or 0.0
        comp = f.get('components') or {}
        return max([abs(c['max']) for c in comp.values()] + [abs(c['min']) for c in comp.values()] + [0.0])

    for p in d.get('probes', []):
        where = ''
        if p.get('node'):
            where = '最近节点 %s.%d' % (p['node']['instance'], p['node']['label'])
            c = p['node'].get('coords')
            if c:
                where += ' (%s)，距探针 %s' % (', '.join(fnum(v, 5) for v in c), fnum(p['node'].get('distance') or 0.0, 3))
        L.append('探针 (%s) %s:' % (', '.join(fnum(v) for v in p['point']), where))
        for var, s in p.get('series', {}).items():
            L.extend(series_lines(var, s, floor=gref(var)))
    for sr in d.get('setResults', []):
        L.extend(set_lines(sr, d.get('sets', []), gref, noise))
    if fr and full:
        L.append('该帧可用场量: ' + ', '.join(fr.get('available', [])))
    return L


def series_lines(var, s, indent='  ', floor=0.0):
    """a time series as a short table: head and tail rows, round-off shown as 0, and where each column peaks"""
    rows = s['rows']
    ref = max([abs(x) for r in rows for x in r[1:]] + [floor or 0.0])
    show = rows if len(rows) <= 8 else rows[:2] + rows[-5:]
    L = ['%s%s 列: %s; 共 %d 帧%s' % (indent, var, ', '.join(s['columns']), len(rows),
                                    '（只列首 2 行和末 5 行）' if len(rows) > 8 else '')]
    for r in show:
        L.append(indent + '  ' + ', '.join([fnum(r[0])] + [zf(x, ref) for x in r[1:]]))
    if len(rows) > 8:
        ext = []
        for c in range(1, len(s['columns'])):
            col = [r[c] for r in rows]
            i1, i0 = col.index(max(col)), col.index(min(col))
            if ref and max(abs(col[i1]), abs(col[i0])) < 1e-9 * ref:
                continue
            ext.append('%s 最大 %s (t=%s), 最小 %s (t=%s)' % (s['columns'][c], zf(col[i1], ref), fnum(rows[i1][0]),
                                                           zf(col[i0], ref), fnum(rows[i0][0])))
        if ext:
            L.append(indent + '  全程极值: ' + '; '.join(ext))
    return L


def set_lines(sr, all_sets, gref=lambda name: 0.0, noise=()):
    """results on a named set (abq odb --set NAME)"""
    L = []
    found = sr.get('found') or {}
    if not found:
        names = sorted(set(s['name'] for s in all_sets))
        L.append('集合 %s: 没有这个名字的集合。结果库里的集合: %s' % (sr['name'], ', '.join(names[:40]) or '（无）'))
        return L
    desc = '; '.join('%s集 %s（%s，%s 个%s）' % ('节点' if k == 'node' else '单元', v['name'],
                                           '装配级' if v['holder'] == 'assembly' else '实例 ' + v['holder'],
                                           v['n'], '节点' if k == 'node' else '单元') for k, v in sorted(found.items()))
    L.append('集合 %s —— %s:' % (sr['name'], desc))
    order = ['U', 'UR', 'RF', 'RM', 'CF', 'CM', 'S', 'E', 'LE', 'PE', 'PEEQ']
    F = sr.get('fields') or {}
    for name in [n for n in order if n in F] + sorted(n for n in F if n not in order):
        f = F[name]
        if name in noise:
            L.append('  %s: 全为 0' % name)
            continue
        cols = f['columns']
        ref = max([abs(x) for x in f['max']] + [abs(x) for x in f['min']] + [gref(name) or 0.0])
        sref = max([abs(x) for x in f['sum']] + [ref])
        base = name.split(' ')[0]
        parts = []
        if base in ('RF', 'RM', 'CF', 'CM', 'TF'):
            parts.append('合计 ' + ', '.join('%s=%s' % (c, zf(v, sref)) for c, v in zip(cols, f['sum'])))
        else:
            parts.append('平均 ' + ', '.join('%s=%s' % (c, zf(v, ref)) for c, v in zip(cols, f['mean'])))
        parts.append('范围 ' + '; '.join('%s %s ~ %s' % (c, zf(a, ref), zf(b, ref)) for c, a, b in zip(cols, f['min'], f['max'])))
        if 'maxMagnitude' in f:
            parts.append('最大合量 %s' % zf(f['maxMagnitude'], ref))
        if 'mises' in f:
            parts.append('Mises max %s, 平均 %s' % (fnum(f['mises']['max']), fnum(f['mises']['mean'])))
        L.append('  %s（%d 个%s值）: %s' % (name, f['n'], '节点' if f['setKind'] == 'node' else '积分点', ' | '.join(parts)))
        for r in f.get('rows', []):
            c = r.get('coords')
            L.append('    节点 %d%s: %s' % (r['node'], (' (%s)' % ', '.join(fnum(v, 5) for v in c)) if c else '',
                                         ', '.join('%s=%s' % (k, zf(v, ref)) for k, v in zip(cols, r['data']))))
    if not F:
        L.append('  该帧没有可在这个集合上取到的场量（单元集取不到节点量时，换成同位置的节点集）')
    for var, s in (sr.get('series') or {}).items():
        L.extend(series_lines(var, s, floor=gref(var)))
    return L


def cmd_odb(a):
    path = os.path.abspath(a.odb if a.odb.lower().endswith('.odb') else a.odb + '.odb')
    if not os.path.isfile(path):
        P('找不到 %s' % path)
        return 2
    extra = []
    if a.step is not None:
        extra += ['--step', str(a.step)]
    if a.frame is not None:
        extra += ['--frame', str(a.frame)]
    if a.vars:
        extra += ['--vars', a.vars]
    for p in a.probe or []:
        extra += ['--probe', p]
    if a.series:
        extra += ['--series', a.series]
    for s in a.set or []:
        extra += ['--set', s]
    d, err = extract(path, extra)
    if d is None:
        m = re.search(r'step (\S+) has (\d+) frames', err or '')
        if m:
            P('分析步 %s 只有 %s 帧：--frame 可取 0 到 %d，或 -1 表示最后一帧。' % (m.group(1), m.group(2), int(m.group(2)) - 1))
        else:
            P('ODB 提取失败: %s' % err, '（作业还在运行或没有结果帧时会失败；先看 abq status）')
        return 1
    if a.sets:
        P('结果库 %s 里的集合（名字一律大写；取某个集合上的结果: abq odb <odb> --set 名字 [--vars U,RF] [--series RF]）:'
          % os.path.basename(path))
        for s in d.get('sets', []):
            P('  %s集 %-28s %s 个  %s' % ('节点' if s['kind'] == 'node' else '单元', s['name'], s['n'],
                                       '装配级' if s['holder'] == 'assembly' else '实例 ' + s['holder']))
        if not d.get('sets'):
            P('  （没有命名集合；建模时给关心的位置建集合，或用 --probe x,y,z 按坐标取）')
        return 0
    focus = bool(a.probe or a.set) and not a.full
    P('结果库 %s' % os.path.basename(path), *odb_digest(d, a.full, focus=focus))
    P('数值为模型自身单位制下的原始值。%s完整 JSON: %s' % ('全场最值/合计: 不带 --probe/--set 再运行一次。' if focus else '', d['_json']))
    where = []
    if a.series and d.get('probes'):
        where.append('probes[i].series.<量>.rows（列名在 .columns）')
    if a.series and d.get('setResults'):
        where.append('setResults[i].series.<量>.rows（列名在 .columns）')
    if where:
        P('本次的全部时程在该 JSON 的 %s，i 按本次命令行里 --probe / --set 的先后。每次运行 abq odb 都会重写这个文件：'
          '要同时用几个点或集合的时程，就在一条命令里把它们都列上。' % '、'.join(where))
    return 0


# ---------------------------------------------------------------------------- verify
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_runinfo(cwd, job):
    """remember which input file a result database was computed from (verify compares it later)"""
    inp = os.path.join(cwd, job + '.inp')
    try:
        with open(os.path.join(cwd, job + '.runinfo.json'), 'w', encoding='utf-8') as f:
            json.dump({'job': job, 'inpSha256': file_sha256(inp), 'solvedAt': time.strftime('%Y-%m-%dT%H:%M:%S')}, f)
    except OSError:
        pass


def read_runinfo(cwd, job):
    try:
        with open(os.path.join(cwd, job + '.runinfo.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def check_result_file(cwd, path, odb_path, add):
    """a result file the task asked for: valid JSON, numbers (not text, not NaN), written after the solve"""
    full = path if os.path.isabs(path) else os.path.join(cwd, path)
    name = os.path.basename(full)
    if not os.path.isfile(full):
        add('FAIL', '结果文件 %s 不存在' % name)
        return
    try:
        with open(full, encoding='utf-8-sig') as f:
            data = json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        add('FAIL', '结果文件 %s 不是合法的 JSON: %s' % (name, str(e)[:120]))
        return
    if not isinstance(data, dict):
        add('WARN', '结果文件 %s 的顶层不是 JSON 对象（键值对）' % name)
        return
    bad = []

    def walk(key, v):
        if isinstance(v, bool) or v is None or isinstance(v, str):
            bad.append('%s 的值是%s，不是数字' % (key, '文本' if isinstance(v, str) else '空值/布尔值'))
        elif isinstance(v, (int, float)):
            if v != v or v in (float('inf'), float('-inf')):
                bad.append('%s 不是有限的数' % key)
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                walk('%s[%d]' % (key, i), x)
        elif isinstance(v, dict):
            for k, x in v.items():
                walk('%s.%s' % (key, k), x)

    for k, v in data.items():
        walk(k, v)
    if bad:
        add('FAIL', '结果文件 %s: %s' % (name, '；'.join(bad[:6])))
    else:
        add('OK', '结果文件 %s: 合法 JSON，%d 个键（%s），值都是数字' % (name, len(data), ', '.join(list(data.keys())[:8])))
    if os.path.isfile(odb_path) and os.path.getmtime(full) < os.path.getmtime(odb_path) - 2.0:
        add('WARN', '结果文件 %s 比结果库旧：最后一次求解之后没有重新取数' % name)


def verify_job(cwd, job, d=None, data=None, results=None):
    """checks of a finished job -> (verdict, items, extractor data); prints the report, writes <job>.verify.json"""
    if d is None:
        d = diag.diagnose(cwd, job)
    R = []     # (level, text)  level: FAIL / WARN / OK / INFO

    def add(level, text):
        R.append((level, text))

    if d['state'] == 'completed':
        add('OK', '求解完成 (THE ANALYSIS HAS COMPLETED SUCCESSFULLY)')
    else:
        add('FAIL', '求解未成功完成（状态: %s）' % d['state'])
    for n, t in d['errors'][:5]:
        add('FAIL', '求解器错误 ×%d: %s' % (n, t[:300]))
    wtext = ' || '.join(t for _, t in d['warnings'])
    for pat, msg in ((r'ZERO PIVOT|NUMERICAL SINGULARITY', '出现零主元/数值奇异：有未约束的自由度，结果不可信，需修正约束'),
                     (r'NEGATIVE EIGENVALUE', '出现负特征值：确认不是刚体位移或失稳，并在报告里说明'),
                     (r'DISTORT', '有单元畸变警告'),
                     (r'NOT.{0,20}TIED|WILL NOT BE TIED', 'Tie 有节点未被绑定'),
                     (r'UNCONNECTED', '存在互不相连的区域'),
                     (r'OVERCLOS', '接触面有初始过盈警告')):
        if re.search(pat, wtext, re.I):
            add('WARN', msg)
    if d['sta']['cutbacks']:
        add('INFO', '增量步折减 %d 次（收敛过程不顺，结果可用但值得关注）' % d['sta']['cutbacks'])
    odb = os.path.join(cwd, job + '.odb')
    if data is None and os.path.isfile(odb) and d['state'] == 'completed':
        data, err = extract(odb)
        if data is None:
            add('WARN', '结果库读取失败: %s' % err)
    if data:
        F = {} if (data.get('frame') or {}).get('modeShape') else data.get('fields', {})
        h = data.get('history') or {}
        ratios = h.get('ratios', {})
        explicit = any('EXPLICIT' in (s.get('procedure') or '').upper() for s in data.get('steps', []))
        dynamic = any('DYNAMIC' in (s.get('procedure') or '').upper() for s in data.get('steps', []))
        for key, limit, msg in (('ALLAE/ALLIE', 0.05, '伪应变能(沙漏)占内能 %s，超过 5%%：网格太粗或单元类型不合适'),
                                ('ALLSD/ALLIE', 0.05, '静力稳定耗能占内能 %s，超过 5%%：稳定/阻尼在改变结果'),
                                ('ALLVD/ALLIE', 0.05, '粘性耗能占内能 %s，超过 5%%')):
            v = ratios.get(key)
            if v is not None and abs(v) > limit:
                if key == 'ALLVD/ALLIE' and dynamic:
                    # material / Rayleigh damping, dashpots and bulk viscosity all end up in ALLVD: expected in a dynamic step
                    add('INFO', '粘性耗能/内能 = %s：动力分析里材料阻尼、阻尼器、体积粘性的耗能都计入这一项，有意设置了阻尼时属正常；'
                                '没有设置阻尼却出现则要查原因' % fnum(v, 3))
                else:
                    add('WARN', msg % fnum(v, 3))
        if explicit and ratios.get('ALLKE/ALLIE') is not None and abs(ratios['ALLKE/ALLIE']) > 0.1:
            add('INFO', '动能/内能 = %s：若本意是准静态加载，这个比例偏高（应低于 5%%~10%%）' % fnum(ratios['ALLKE/ALLIE'], 3))
        cp = [f for n, f in F.items() if n.split(' ')[0] == 'CPRESS' and 'max' in f]
        if cp and all((f['max'] or 0) <= 0 for f in cp):
            add('WARN', '定义了接触但接触压力处处为 0：接触没有建立（表面朝向反了、两面没接触上，或加载方向不对）')
        rf, cf = F.get('RF', {}).get('sum'), F.get('CF', {}).get('sum')
        if rf:
            sref = max([abs(v) for v in F['RF'].get('sumPos', {}).values()] + [abs(v) for v in F['RF'].get('sumNeg', {}).values()] + [0.0])
            add('INFO', '反力合计: ' + ', '.join('%s=%s' % (k, zf(v, sref)) for k, v in sorted(rf.items())) +
                ' —— 请与施加的总载荷核对（集中力合计: %s；分布载荷/重力需按面积、体积自行核算）' % (
                    ', '.join('%s=%s' % (k, fnum(v)) for k, v in sorted(cf.items())) if cf else '无'))
            if cf and all(abs(v) < 1e-12 * max(1.0, max(abs(x) for x in cf.values())) for v in rf.values()) and any(abs(x) > 0 for x in cf.values()):
                add('WARN', '有集中力但反力合计为零，平衡关系可疑')
        u = F.get('U', {}).get('maxMagnitude', {}).get('value')
        bb = data.get('bbox')
        if u is not None:
            add('INFO', '最大位移合量 %s' % fnum(u))
            if bb:
                diagl = sum((bb[1][i] - bb[0][i]) ** 2 for i in range(3)) ** 0.5
                nl = None
                inp = os.path.join(cwd, job + '.inp')
                if os.path.isfile(inp):
                    txt = diag.read_text(inp, 4 << 20)
                    nl = bool(re.search(r'(?im)^\*step[^\n]*nlgeom\s*=\s*yes', txt)) or bool(re.search(r'(?im)^\*dynamic\s*,\s*explicit', txt))
                if diagl > 0 and u / diagl > 0.05 and nl is False:
                    add('WARN', '最大位移已达模型尺寸的 %s%%，但没有开 NLGEOM：先核对载荷与单位量级，确属大变形则应开几何非线性' % fnum(100 * u / diagl, 3))
        if data.get('frequencies'):
            add('INFO', '前几阶频率 (Hz): ' + ', '.join(fnum(f['frequency']) for f in data['frequencies'][:6]))
            low = [f for f in data['frequencies'] if abs(f['frequency']) < 1e-3]
            if low:
                add('WARN', '有 %d 阶频率接近 0：存在未约束的刚体模态（除非本来就是自由模态分析）' % len(low))
    # does the result database belong to the input file that is there now?
    inp, odbp = os.path.join(cwd, job + '.inp'), os.path.join(cwd, job + '.odb')
    if os.path.isfile(inp) and os.path.isfile(odbp):
        info = read_runinfo(cwd, job)
        if info.get('inpSha256'):
            if info['inpSha256'] != file_sha256(inp):
                add('WARN', '%s.inp 的内容在求解之后改过：现在的结果库不是这份输入文件的结果，重新求解后再取数' % job)
        elif os.path.getmtime(odbp) < os.path.getmtime(inp) - 2.0:
            add('INFO', '%s.inp 比结果库新（求解后又写过输入文件）；如果内容有改动，需要重新求解' % job)
    for rf_path in (results or []):
        check_result_file(cwd, rf_path, odbp, add)
    verdict = 'FAIL' if any(l == 'FAIL' for l, _ in R) else 'PASS(有警告)' if any(l == 'WARN' for l, _ in R) else 'PASS'
    P('核查 %s — %s' % (job, verdict))
    for l, t in R:
        P('  [%s] %s' % (l, t))
    P('说明: 核查只能发现常见的数值/建模问题，不能证明结果符合物理预期；请再用手算或量级估计对关键结果做一次对照。')
    with open(os.path.join(cwd, job + '.verify.json'), 'w', encoding='utf-8') as f:
        json.dump({'job': job, 'verdict': verdict, 'items': [{'level': l, 'text': t} for l, t in R],
                   'time': time.strftime('%Y-%m-%dT%H:%M:%S')}, f, ensure_ascii=False, indent=1)
    return verdict, R, data


def cmd_verify(a):
    cwd, job = resolve_job(a.job)
    verdict, _, _ = verify_job(cwd, job, results=a.result)
    return 0 if verdict != 'FAIL' else 1


# ---------------------------------------------------------------------------- sweep
def sweep_metrics(d):
    """key numbers of one solved variant -> ordered list of (label, value)"""
    F = d.get('fields', {})
    out = [('单元数', sum(v.get('elements', 0) for v in (d.get('instances') or {}).values()))]
    u = F.get('U', {}).get('maxMagnitude', {}).get('value')
    if u is not None:
        out.append(('最大位移', u))
    s = F.get('S', {})
    if 'invariants' in s:
        mi = s['invariants'].get('mises', {}).get('max')
        if mi is not None:
            out.append(('Mises(积分点)', mi))
        en = s.get('elementNodal', {})
        if en.get('maxPrincipal'):
            out.append(('最大主应力(节点外推)', en['maxPrincipal']['max']))
        if en.get('minPrincipal'):
            out.append(('最小主应力(节点外推)', en['minPrincipal']['min']))
    rf = F.get('RF', {})
    if rf.get('sumPos'):
        out.append(('传递力(RF正值合计最大分量)', max(abs(v) for v in list(rf['sumPos'].values()) + list(rf['sumNeg'].values()))))
    cp = [f['max'] for n, f in F.items() if n.split(' ')[0] == 'CPRESS' and 'max' in f]
    if cp:
        out.append(('最大接触压力', max(cp)))
    pe = F.get('PEEQ', {}).get('max')
    if pe:
        out.append(('最大PEEQ', pe))
    for f in (d.get('frequencies') or [])[:3]:
        out.append(('%d阶频率' % f['mode'], f['frequency']))
    for f in (d.get('eigenvalues') or [])[:1]:
        out.append(('1阶屈曲特征值', f['value']))
    r = (d.get('history') or {}).get('ratios', {}).get('ALLAE/ALLIE')
    if r:
        out.append(('ALLAE/ALLIE', r))
    return out


def cmd_sweep(a):
    """Run the same modelling script with several parameter values, solve each and tabulate objective results."""
    path = os.path.abspath(a.script)
    if not os.path.isfile(path):
        P('找不到脚本: %s' % path)
        return 2
    cwd, name = os.path.dirname(path), os.path.basename(path)
    src = diag.read_text(path)
    if 'ABQ_PARAMS' not in src:
        P('脚本没有读取 ABQ_PARAMS，无法做参数扫描。在脚本参数区加上（Python 2.7）:',
          "  import os, json",
          "  PARAMS = json.loads(os.environ.get('ABQ_PARAMS', '{}'))",
          "  JOB = str(PARAMS.get('JOB', '原来的作业名'))        # 扫描时由 abq 指定作业名",
          "  SEED = float(PARAMS.get('SEED', 0.05))             # 要扫描的参数，默认值就是原来的值",
          '然后: abq sweep %s --set SEED=0.05,0.025,0.0125' % name)
        return 2
    keys, lists = [], []
    for item in a.set or []:
        if '=' not in item:
            P('--set 的格式是 名字=值1,值2,…: %s' % item)
            return 2
        k, v = item.split('=', 1)
        keys.append(k.strip())
        lists.append([x.strip() for x in v.split(',') if x.strip() != ''])
    if not keys:
        P('至少给一个 --set 名字=值1,值2,…')
        return 2
    if len(keys) > 1 and not a.zip:
        combos = [[]]
        for lst in lists:
            combos = [c + [x] for c in combos for x in lst]
    else:
        n = min(len(l) for l in lists)
        combos = [[l[i] for l in lists] for i in range(n)]
    if len(combos) > a.max_runs:
        P('组合数 %d 超过上限 %d（--max-runs）。减少取值，或用 --zip 让各参数按位置配对。' % (len(combos), a.max_runs))
        return 2
    root = workspace_root(cwd)
    if gate_mode(root) == 'human':
        ok = False
        if a.base:
            ent, cur = review_entry(root, os.path.join(cwd, a.base + '.inp')) if os.path.isfile(os.path.join(cwd, a.base + '.inp')) else (None, False)
            ok = bool(ent and cur and ent.get('status') == 'approved')
        if not ok:
            P('⛔ 人工审查闸门已开启：参数扫描会连续求解多个变体，需要基准模型先获批准。',
              '先按正常流程生成基准 INP 并请用户在预览面板批准，然后: abq sweep %s --base <已批准的作业名> --set …' % name)
            return 3
    prefix = a.prefix or os.path.splitext(name)[0]
    rows = []
    for i, combo in enumerate(combos):
        job = '%s_v%d' % (prefix, i + 1)
        params = dict(zip(keys, combo))
        params['JOB'] = job
        label = ', '.join('%s=%s' % kv for kv in zip(keys, combo))
        inp = os.path.join(cwd, job + '.inp')
        if os.path.isfile(inp):
            os.remove(inp)
        code, to, sec, text = run_abaqus(['cae', 'noGUI=' + name], cwd, os.path.join(cwd, job + '.cae.log'), a.timeout,
                                         env={'ABQ_PARAMS': json.dumps(params)})
        if not os.path.isfile(inp):
            tail = [l for l in text.splitlines() if l.strip() and 'icense' not in l][-4:]
            rows.append((label, job, '脚本失败', [], ' | '.join(tail)[:300] or '没有生成 %s.inp（脚本是否用 PARAMS["JOB"] 作为作业名？）' % job))
            continue
        ncpu = effective_cpus(a.cpus, inp)[0]
        args = ['job=' + job, 'interactive', 'ask_delete=OFF'] + (['cpus=%d' % ncpu] if ncpu > 1 else [])
        if memory_arg():
            args.append(memory_arg())
        code, to, sec, text = run_solver(args, cwd, os.path.join(cwd, job + '.abqlog'), a.timeout)
        to_utf8(inp, comments_only=True)
        job_text_to_utf8(cwd, job)
        d = diag.diagnose(cwd, job, text)
        if d['state'] != 'completed' or to:
            first = d['errors'][0][1][:200] if d['errors'] else ('超时' if to else '未完成')
            rows.append((label, job, '求解失败', [('耗时s', sec)], first))
            continue
        data, err = extract(os.path.join(cwd, job + '.odb'))
        if data is None:
            rows.append((label, job, '读结果失败', [('耗时s', sec)], err[:200]))
            continue
        rows.append((label, job, '完成', sweep_metrics(data) + [('求解耗时s', sec)], ''))
    P('参数扫描 %s：%d 个变体（顺序求解）' % (name, len(rows)))
    prev = {}
    for label, job, state, metrics, note in rows:
        P('— %s  [%s] %s%s' % (label, job, state, ('：' + note) if note else ''))
        for k, v in metrics:
            change = ''
            if state == '完成' and k in prev and prev[k] not in (0, None) and isinstance(v, (int, float)) and k not in ('单元数', '求解耗时s'):
                change = '   （相对上一个变体 %+.2f%%）' % (100.0 * (v - prev[k]) / abs(prev[k]))
            P('    %s: %s%s' % (k, fnum(v), change))
        if state == '完成':
            prev = dict(metrics)
    done = [r for r in rows if r[2] == '完成']
    if len(done) >= 2:
        P('判读: 关心的量在最后两个变体之间变化小于 2%~5% 才算网格/参数收敛；还在明显变化就继续加密或查原因。',
          '各变体的完整结果: abq odb <作业名>.odb；最终交付时，把选定的变体按任务书要求的作业名重新生成并求解。')
    return 0 if done else 1


# ---------------------------------------------------------------------------- render
def cmd_render(a):
    path = os.path.abspath(a.target)
    if not os.path.isfile(path) and os.path.isfile(path + '.odb'):
        path += '.odb'
    if not os.path.isfile(path):
        P('找不到 %s' % path)
        return 2
    kind = 'odb' if path.lower().endswith('.odb') else 'inp'
    cwd = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.abspath(a.out) if a.out else os.path.join(cwd, '%s_%s%s_%s.png' % (
        stem, (a.var or ('model' if kind == 'inp' else 'S')), ('_' + (a.comp or a.inv)) if (a.comp or a.inv) else '', a.view))
    params = {'kind': kind, 'path': path, 'out': os.path.splitext(out)[0], 'var': a.var, 'inv': a.inv, 'comp': a.comp,
              'deformed': not a.undeformed, 'view': a.view, 'step': a.step, 'frame': a.frame, 'width': a.width,
              'height': a.height, 'scale': a.scale}
    pf = os.path.join(cwd, '_render_params.json')
    with open(pf, 'w', encoding='utf-8') as f:
        json.dump(params, f)
    log = os.path.join(cwd, '_render.log')
    code, to, sec, text = run_abaqus(['cae', 'noGUI=' + os.path.join(HERE, 'cae_render.py'), '--', pf], cwd, log, a.timeout,
                                     env={'ABAQUS_USE_LOCALIZATION': '0'})   # English labels: CJK glyphs garble off-screen
    ok = os.path.isfile(os.path.splitext(out)[0] + '.png') and os.path.getmtime(os.path.splitext(out)[0] + '.png') > time.time() - sec - 5
    for junk in (pf,):
        try:
            os.remove(junk)
        except OSError:
            pass
    if not ok:
        tail = [l for l in text.splitlines() if l.strip() and 'icense' not in l][-12:]
        P('出图失败:', *['  ' + l for l in tail])
        return 1
    info = ''
    m = re.search(r'RENDER_INFO (.*)', text)
    if m:
        info = ' | ' + m.group(1).strip()
    P('图片已生成: %s%s' % (os.path.splitext(out)[0] + '.png', info), '用 read_image 查看这张图；把它的路径写进给用户的报告。')
    return 0


# ---------------------------------------------------------------------------- examples / fetch
def load_gz(name):
    p = os.path.join(DATA, name)
    if not os.path.isfile(p):
        return None
    with gzip.open(p, 'rt', encoding='utf-8') as f:
        return json.load(f)


def norm_kw(s):
    s = s.strip().upper()
    if not s.startswith('*'):
        s = '*' + s
    return re.sub(r'\s*,\s*', ', ', ' '.join(s.split()))


def cmd_examples(a):
    idx = load_gz('samples_index.json.gz')
    if idx is None:
        P('官方算例索引尚未建立: 运行 py %s' % os.path.join(HERE, 'build_samples_index.py'))
        return 2
    kws = [norm_kw(k) for k in (a.kw or [])]
    apis = [x.lower() for x in (a.api or [])]
    elems = [x.upper() for x in (a.elem or [])]
    names = [x.lower() for x in (a.name or [])]
    hits = []
    for e in idx['files']:
        n = e['n'].lower()
        if names and not all(x in n or x in (e.get('t') or '').lower() for x in names):
            continue
        if kws:
            have = set(e.get('k', []))
            if not all(any(h == k or h.startswith(k + ',') or (', ' in k and h.startswith(k)) for h in have) for k in kws):
                continue
        if elems and not all(x in e.get('e', []) for x in elems):
            continue
        if apis:
            have = set(x.lower() for x in e.get('a', []))
            if not all(x in have for x in apis):
                continue
        if (kws or elems) and not n.endswith('.inp'):
            continue
        if apis and not n.endswith('.py'):
            continue
        if a.ext and not n.endswith('.' + a.ext.lower().lstrip('.')):
            continue
        hits.append(e)

    def rank(e):
        n = e['n'].lower()
        stem = os.path.splitext(n)[0]
        # complete, self-contained, small-to-medium files first; restart / import / continuation fragments last
        frag = 1 if (e['s'] < 1500 or re.search(r'(_res|_restart|_imp|_import|_cont|_post|_xpl\d*|_rst)$', stem)) else 0
        has_mesh = 0 if (n.endswith('.py') or '*NODE' in e.get('k', []) or '*INCLUDE' in e.get('k', [])) else 1
        name_hit = 0 if (names and all(x in stem for x in names)) else 1
        import math
        return (frag, has_mesh, name_hit, abs(math.log(max(e['s'], 1) / 12000.0)), n)

    hits.sort(key=rank)
    P('官方算例匹配 %d 个（完整且体量小的排在前面）:' % len(hits))
    for e in hits[:a.max]:
        extra = ''
        if e.get('e'):
            extra = ' | 单元 ' + ','.join(e['e'][:5])
        P('  %-34s %7.1f KB  %s%s' % (e['n'], e['s'] / 1024.0, (e.get('t') or '')[:70], extra))
    if hits:
        P('取出: abq fetch <文件名>  （放到当前目录的 refs\\ 下，只读参考，不要直接改它交差）')
    else:
        P('没有匹配。放宽条件再试: --kw 是 INP 关键字(如 "*CONTACT PAIR"、"*DYNAMIC, EXPLICIT")，--api 是脚本方法名(如 SurfaceToSurfaceContactStd)，--name 是文件名/标题里的词，--elem 是单元类型。')
    return 0


def cmd_fetch(a):
    if not os.path.isfile(SAMPLES_ZIP):
        P('找不到官方算例包: %s' % SAMPLES_ZIP)
        return 2
    dest = os.path.abspath(a.to)
    os.makedirs(dest, exist_ok=True)
    want = a.name.lower()
    got = []
    with zipfile.ZipFile(SAMPLES_ZIP) as z:
        for info in z.infolist():
            base = os.path.basename(info.filename).lower()
            if not base:
                continue
            if base == want or (os.path.splitext(want)[1] == '' and os.path.splitext(base)[0] == want):
                target = os.path.join(dest, os.path.basename(info.filename))
                with z.open(info) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                got.append(target)
    if not got:
        P('算例包里没有名为 %s 的文件；用 abq examples --name %s 找准确文件名。' % (a.name, os.path.splitext(a.name)[0]))
        return 1
    P('已取出:', *['  ' + g for g in got])
    for g in got:
        if g.lower().endswith('.inp'):
            txt = diag.read_text(g, 200000)
            inc = re.findall(r'(?im)^\*include\s*,\s*input\s*=\s*(\S+)', txt)
            if inc:
                P('  %s 引用了 *INCLUDE: %s （需要时同样用 abq fetch 取）' % (os.path.basename(g), ', '.join(sorted(set(inc))[:8])))
    return 0


# ---------------------------------------------------------------------------- api
def cmd_api(a):
    idx = load_gz('api_index.json.gz')
    if idx is None:
        P('接口索引尚未建立: 运行 py %s build-api' % os.path.abspath(__file__))
        return 2
    q = a.query.strip()
    ql = q.lower()
    if q.endswith('.'):
        cname = q.rstrip('.')
        match = [c for c in idx['classes'] if c.lower() == cname.lower()]
        if not match:
            P('没有名为 %s 的对象类型。' % cname)
            return 1
        c = idx['classes'][match[0]]
        P('%s 对象（访问路径示例: %s）' % (match[0], c.get('path', '')),
          '成员: ' + ', '.join(c.get('members', [])),
          '方法: ' + ', '.join(sorted(c.get('methods', {}))))
        P('看某个方法的签名: abq api %s.<方法名>' % match[0])
        return 0
    hits = []
    undocumented = []
    for cname, c in idx['classes'].items():
        for mname, doc in c.get('methods', {}).items():
            if not doc or 'no details available' in doc:
                if ql.split('.')[-1] in mname.lower():
                    undocumented.append('%s.%s' % (cname, mname))
                continue
            full = '%s.%s' % (cname, mname)
            fl = full.lower()
            if '.' in ql:
                score = 0 if fl == ql else 1 if fl.endswith(ql) or ql in fl else None
            else:
                ml = mname.lower()
                score = 0 if ml == ql else 1 if ml.startswith(ql) else 2 if ql in ml else None
            if score is not None:
                hits.append((score, len(full), full, doc or ''))
    hits.sort()
    if not hits and undocumented:
        P('这些方法存在，但 Abaqus 没有给出签名: ' + ', '.join(sorted(undocumented)[:12]),
          '用 abq examples --api %s 找官方脚本里的实际写法。' % min(undocumented, key=len).split('.')[-1])
        return 0
    if not hits:
        P('没有找到名字含 "%s" 的方法。换个词，或用 abq api <对象类型>. 列出某类对象的全部方法（如 abq api Model.）。' % q)
        return 1
    P('本机 Abaqus 脚本接口中匹配 "%s" 的方法 %d 个%s（<> 内为可选参数）:' % (q, len(hits), '，显示前 %d 个' % a.max if len(hits) > a.max else ''))
    seen = set()
    n = 0
    for score, _, full, doc in hits:
        sig = doc.split('->')[0].strip() if doc else full + '(?)'
        desc = doc.split('->', 1)[1].strip() if '->' in doc else ''
        key = re.sub(r'^\w+\.', '', sig)
        if key in seen and score > 0:
            continue
        seen.add(key)
        P('  ' + sig + ('\n      ' + desc[:160] if desc and n < 6 else ''))
        n += 1
        if n >= a.max:
            break
    same = [c for c in idx['classes'] if c.lower() == ql]
    if same:
        P('另有同名对象类型 %s；列出它的成员和方法: abq api %s.' % (same[0], same[0]))
    P('参数含义不确定时，用 abq examples --api <方法名> 找官方脚本里的实际用法。')
    return 0


def cmd_build_api(a):
    os.makedirs(DATA, exist_ok=True)
    work = os.path.join(DATA, '_api_build')
    os.makedirs(work, exist_ok=True)
    log = os.path.join(work, 'crawl.log')
    code, to, sec, text = run_abaqus(['cae', 'noGUI=' + os.path.join(HERE, 'cae_api_crawl.py')], work, log, 1200)
    src = os.path.join(work, 'api_index.json')
    if not os.path.isfile(src):
        P('接口抓取失败:', *text.splitlines()[-15:])
        return 1
    with open(src, encoding='utf-8') as f:
        d = json.load(f)
    with gzip.open(os.path.join(DATA, 'api_index.json.gz'), 'wt', encoding='utf-8') as f:
        json.dump(d, f)
    P('接口索引已建立: %d 类对象, %d 个方法 (用时 %s s)' % (len(d['classes']), sum(len(c['methods']) for c in d['classes'].values()), fnum(sec, 3)))
    return 0


# ---------------------------------------------------------------------------- rpy / cases / units / gate / preview
def cmd_rpy(a):
    path = os.path.abspath(a.file)
    if not os.path.isfile(path):
        P('找不到 %s（CAE 在其工作目录里写 abaqus.rpy，记录界面上每一步操作对应的脚本命令）' % path)
        return 2
    lines = [l.rstrip() for l in diag.read_text(path).splitlines()]
    keep = [l for l in lines if l.strip() and not l.startswith('#') and not re.match(r'^(from |import |session\.viewports\[.*\]\.(view|makeCurrent|maximize)|executeOnCaeStartup|session\.journalOptions)', l)]
    P('%s 中的脚本命令（末尾 %d 条，已去掉视图操作）:' % (path, min(len(keep), a.tail)), *keep[-a.tail:])
    return 0


def load_cases():
    out = []
    if not os.path.isdir(CASES):
        return out
    excl = set(x.strip().lower() for x in os.environ.get('ABQ_CASE_EXCLUDE', '').split(',') if x.strip())
    for name in sorted(os.listdir(CASES)):
        p = os.path.join(CASES, name, 'case.json')
        if os.path.isfile(p):
            try:
                with open(p, encoding='utf-8') as f:
                    c = json.load(f)
            except (ValueError, OSError):
                continue
            c['_dir'] = os.path.join(CASES, name)
            c.setdefault('id', name)
            if c['id'].lower() in excl or str(c.get('source', '')).lower() in excl:
                continue
            out.append(c)
    return out


def cmd_cases(a):
    cases = load_cases()
    if a.show:
        for c in cases:
            if c['id'].lower() == a.show.lower():
                P('案例 %s — %s' % (c['id'], c.get('title', '')), '标签: ' + ', '.join(c.get('tags', [])),
                  '问题: ' + c.get('problem', ''), '做法要点:', *['  - ' + x for x in c.get('approach', [])],
                  '踩过的坑:', *['  - ' + x for x in c.get('lessons', [])],
                  '已验证结果: ' + json.dumps(c.get('verified', {}), ensure_ascii=False),
                  '文件（只读参考，可照着改写成自己的脚本）:', *['  ' + os.path.join(c['_dir'], f) for f in c.get('files', [])])
                return 0
        P('没有这个案例: %s' % a.show)
        return 1
    if not cases:
        P('案例库为空: %s' % CASES)
        return 0
    q = [w.lower() for w in a.query]
    scored = []
    for c in cases:
        blob = ' '.join([c.get('title', ''), ' '.join(c.get('tags', [])), c.get('problem', ''),
                         ' '.join(c.get('keywords', []))]).lower()
        score = sum(3 if w in ' '.join(c.get('tags', [])).lower() else 1 for w in q if w in blob)
        if score or not q:
            scored.append((-score, c['id'], c))
    scored.sort(key=lambda x: (x[0], x[1]))
    P('已验证案例库: %d 个%s' % (len(cases), '，匹配 %d 个' % len(scored) if q else ''))
    for _, _, c in scored[:a.max]:
        P('  %-28s %s  [%s]' % (c['id'], c.get('title', ''), ', '.join(c.get('tags', [])[:8])))
    P('查看某个案例: abq cases --show <id>')
    return 0


UNITS = r"""Abaqus 没有内置单位，所有输入必须自洽。本项目默认 m–t–s 制（力 kN，应力 kPa）。
┌────────────┬────────┬────────┬────────┬──────────┬────────────┬──────────┬───────────────┐
│ 单位制     │ 长度   │ 力     │ 质量   │ 应力/模量│ 密度       │ 重力加速度│ 能量          │
├────────────┼────────┼────────┼────────┼──────────┼────────────┼──────────┼───────────────┤
│ SI         │ m      │ N      │ kg     │ Pa       │ kg/m³      │ 9.8      │ J             │
│ m–t–s(默认)│ m      │ kN     │ t      │ kPa      │ t/m³       │ 9.8      │ kJ            │
│ mm–t–s     │ mm     │ N      │ t      │ MPa      │ t/mm³      │ 9800     │ mJ            │
└────────────┴────────┴────────┴────────┴──────────┴────────────┴──────────┴───────────────┘
典型材料在三种单位制下的数值（E / 密度）:
  钢      SI 206000000000 / 7850     m–t–s 206000000 / 7.85     mm–t–s 206000 / 0.00000000785
  C30 砼  SI 30000000000 / 2500      m–t–s 30000000 / 2.5       mm–t–s 30000 / 0.0000000025
  铝      SI 70000000000 / 2700      m–t–s 70000000 / 2.7       mm–t–s 70000 / 0.0000000027
自检: √(E/密度) 是波速。m 制下钢约 5100、混凝土约 3500、土 100~1000 (m/s)；mm 制下数值是它的 1000 倍。
      数值落不进这些范围，多半是 E 或密度差了 1000 倍。abq check 会自动算这个值。
压强、面载荷用应力单位；集中力用力单位；时间一律用 s；频率输出为 Hz（周/秒）。
任务书若指定了别的单位制，以任务书为准，并在报告里写明所用单位制。"""


def cmd_units(a):
    P(UNITS)
    return 0


def cmd_gate(a):
    root = workspace_root(os.getcwd())
    cfg = os.path.join(root, '.abq', 'config.json')
    if a.action in ('on', 'off'):
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        data = {}
        if os.path.isfile(cfg):
            try:
                with open(cfg, encoding='utf-8') as f:
                    data = json.load(f)
            except (ValueError, OSError):
                data = {}
        data['gate'] = 'human' if a.action == 'on' else 'off'
        with open(cfg, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    mode = gate_mode(root)
    P('工作区: %s' % root, '人工审查闸门: %s' % ('开（abq run 需要用户先在预览面板里批准该 INP）' if mode == 'human' else '关（abq run 不需要批准）'))
    if os.environ.get('ABQ_GATE'):
        P('（当前由环境变量 ABQ_GATE=%s 决定）' % os.environ['ABQ_GATE'])
    return 0


def cmd_preview(a):
    P(preview_line())
    return 0


# ---------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog=SELF, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    s = sub.add_parser('check', help='静态体检 + 模型摘要')
    s.add_argument('inp')
    s.add_argument('--brief', action='store_true', help='只输出规模和体检结果')
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser('script', help='运行 CAE 无界面脚本')
    s.add_argument('script')
    s.add_argument('--timeout', type=int, default=1800)
    s.add_argument('args', nargs='*', help='传给脚本的参数')
    s.set_defaults(fn=cmd_script)

    s = sub.add_parser('python', help='用 abaqus python 运行后处理脚本（读 ODB）')
    s.add_argument('script')
    s.add_argument('--timeout', type=int, default=900)
    s.add_argument('args', nargs='*', help='传给脚本的参数')
    s.set_defaults(fn=cmd_python)

    s = sub.add_parser('datacheck', help='求解器数据检查')
    s.add_argument('job')
    s.add_argument('--timeout', type=int, default=900)
    s.add_argument('--force', action='store_true')
    s.set_defaults(fn=cmd_datacheck)

    s = sub.add_parser('run', help='完整求解')
    s.add_argument('job')
    s.add_argument('--cpus', type=int, default=1, help='核数；少于 20000 个节点的模型自动用 1 核（多核反而慢）')
    s.add_argument('--force-cpus', action='store_true', help='小模型也按 --cpus 给的核数运行')
    s.add_argument('--timeout', type=int, default=3600)
    s.add_argument('--user', help='用户子程序源文件')
    s.add_argument('--double', action='store_true', help='显式双精度')
    s.add_argument('--memory', help='求解器内存上限，如 32gb 或 16000（MB）。默认按当前空闲内存自动取 1.5~8 GB，大模型需要显式加大')
    s.add_argument('--force', action='store_true', help='忽略静态体检的错误')
    s.add_argument('--no-post', action='store_true', help='求解后不自动给出结果摘要和核查')
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser('status', help='查看作业状态')
    s.add_argument('job')
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser('odb', help='结果摘要')
    s.add_argument('odb')
    s.add_argument('--step', help='分析步名或序号（默认最后一个非特征值分析步）')
    s.add_argument('--frame', type=int, help='帧号（默认最后一帧；模态分析中帧号=阶次）')
    s.add_argument('--vars', help='只提取这些场量，逗号分隔，如 U,S,RF')
    s.add_argument('--probe', action='append', help='探针坐标 x,y,z（可多次），输出最近节点/单元的时程')
    s.add_argument('--set', action='append', help='集合名（可多次，大小写不限）：该集合上各场量的合计/平均/范围；'
                                                  '加 --series 给出逐帧的合计（RF、CF 等）或平均（其他场量）')
    s.add_argument('--sets', action='store_true', help='列出结果库里的全部集合')
    s.add_argument('--series', help='探针或集合的时程场量，如 U,S,RF（探针默认 U）')
    s.add_argument('--full', action='store_true', help='所有张量都给出不变量')
    s.set_defaults(fn=cmd_odb)

    s = sub.add_parser('verify', help='收工前核查')
    s.add_argument('job')
    s.add_argument('--result', action='append', help='任务要求的结果文件（如 result.json，可多次）：检查是合法 JSON、'
                                                     '值是数字、写于最后一次求解之后')
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser('sweep', help='参数扫描/网格收敛：同一脚本多组参数依次求解并对比')
    s.add_argument('script')
    s.add_argument('--set', action='append', help='名字=值1,值2,…（可多次；多个参数默认做全组合）')
    s.add_argument('--zip', action='store_true', help='多个参数按位置配对而不是全组合')
    s.add_argument('--prefix', help='变体作业名前缀（默认脚本名）')
    s.add_argument('--base', help='闸门开启时：已获批准的基准作业名')
    s.add_argument('--cpus', type=int, default=1)
    s.add_argument('--timeout', type=int, default=1800)
    s.add_argument('--max-runs', type=int, default=6)
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser('render', help='出图 (PNG)')
    s.add_argument('target', help='.odb 或 .inp')
    s.add_argument('--var', help='场量，如 U、S、PEEQ、CPRESS（ODB）')
    s.add_argument('--inv', help='不变量，如 Mises、Magnitude、"Max. Principal"')
    s.add_argument('--comp', help='分量，如 S11、U2')
    s.add_argument('--undeformed', action='store_true')
    s.add_argument('--scale', type=float, help='变形放大系数（默认自动）')
    s.add_argument('--view', default='Iso', choices=['Iso', 'Front', 'Back', 'Top', 'Bottom', 'Left', 'Right'])
    s.add_argument('--step')
    s.add_argument('--frame', type=int)
    s.add_argument('--out')
    s.add_argument('--width', type=int, default=1400)
    s.add_argument('--height', type=int, default=900)
    s.add_argument('--timeout', type=int, default=600)
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser('examples', help='检索官方算例')
    s.add_argument('--kw', action='append', help='INP 关键字，可多次（都要出现），如 "*TIE"')
    s.add_argument('--api', action='append', help='脚本方法名，可多次，如 SurfaceToSurfaceContactStd')
    s.add_argument('--elem', action='append', help='单元类型，如 C3D8R')
    s.add_argument('--name', action='append', help='文件名或标题里的词')
    s.add_argument('--ext', help='只要某种扩展名，如 inp / py')
    s.add_argument('--max', type=int, default=15)
    s.set_defaults(fn=cmd_examples)

    s = sub.add_parser('fetch', help='取出官方算例')
    s.add_argument('name')
    s.add_argument('--to', default='refs')
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser('api', help='查脚本接口签名')
    s.add_argument('query')
    s.add_argument('--max', type=int, default=20)
    s.add_argument('--search', action='store_true', help='按方法名搜索（即使与对象类型同名）')
    s.set_defaults(fn=cmd_api)

    s = sub.add_parser('build-api', help='(维护) 重建接口索引')
    s.set_defaults(fn=cmd_build_api)

    s = sub.add_parser('rpy', help='查看 CAE 回放文件')
    s.add_argument('file', nargs='?', default='abaqus.rpy')
    s.add_argument('--tail', type=int, default=60)
    s.set_defaults(fn=cmd_rpy)

    s = sub.add_parser('cases', help='检索已验证案例库')
    s.add_argument('query', nargs='*')
    s.add_argument('--show')
    s.add_argument('--max', type=int, default=8)
    s.set_defaults(fn=cmd_cases)

    s = sub.add_parser('units', help='单位制对照表')
    s.set_defaults(fn=cmd_units)

    s = sub.add_parser('gate', help='人工审查闸门')
    s.add_argument('action', nargs='?', default='status', choices=['status', 'on', 'off'])
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser('preview', help='预览面板地址')
    s.set_defaults(fn=cmd_preview)

    a = ap.parse_args(argv)
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 0
    try:
        return a.fn(a)
    except LowMemory as e:
        P('⏸ ' + str(e))
        return 4


if __name__ == '__main__':
    sys.exit(main())
