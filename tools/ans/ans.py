#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ans — ANSYS (MAPDL) 工具箱（给智能体和人用的同一套命令），和 abq 对等。

一道 ANSYS 题的三个文件，都放在作业的文件夹里：
    <job>.build.mac   建模命令流：几何、网格、材料、组件、初始的约束和载荷。不能求解
    <job>.cdb         ans build 导出的模型（MAPDL 用 CDWRITE 写的块格式）。人在预览面板里审查的就是它
    <job>.solve.dat   求解段：分析类型、时间步、各载荷步的载荷变化、SOLVE。只能用白名单里的命令，载荷只能对组件加

    ans build <job>       跑建模命令流，导出 <job>.cdb 并体检；建模里求解过就作废
    ans check <job>       不启动 MAPDL：解析 CDB 和求解段，核对白名单，给出模型摘要和体检结果
    ans mesh <job>        网格质量体检（长宽比、角度、雅可比、最差单元）
    ans run <job>         核对批准记录 → 干净目录里读入 CDB、执行求解段 → 结果摘要和核查结论
    ans rst <job>         结果摘要：最大位移及位置、反力合计、最大等效应力、频率；--comp 组件名 取组件上的合计；
                          --lin x1,y1,z1,x2,y2,z2 沿评定线做应力线性化（PRSECT：薄膜、薄膜+弯曲、峰值、总应力）
    ans verify <job>      收工前核查：正常结束了吗、结果是不是现在这份 CDB 和求解段算出来的
    ans status <job>      作业状态（不启动 MAPDL）
    ans gate status|on|off   人工审查闸门
    ans preview           预览面板地址
    ans units             单位制
每个子命令都支持 -h。Python 3，仅用标准库（ans mesh 要 numpy）。
运行目录在作业文件夹下的 .ans\\<job>\\（build、run 各一个，每次重建）。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import time
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
PROJECT = os.path.dirname(TOOLS)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(TOOLS, 'preview'))
sys.path.append(os.path.join(TOOLS, 'abq'))
import fingerprint  # noqa: E402
import mapdl_run  # noqa: E402
import solve_segment  # noqa: E402
import ans_post  # noqa: E402
import resources as res  # noqa: E402  (cores, memory, queue and progress watch: shared with abq)
import review_diff  # noqa: E402

SMALL_MODEL_NODES = 20000           # as abq: below this one core is faster

PREVIEW_PORT = int(os.environ.get('ABQ_PREVIEW_PORT', '8765'))
SELF = 'ans'
AGENT_VARS = ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass


def P(*lines):
    for l in lines:
        print(l, flush=True)


def fnum(x, sig=6):
    """number -> plain decimal text, never scientific notation (as abq)"""
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


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def workspace_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, '.abq')) or os.path.exists(os.path.join(d, '.git')):
            return d
        up = os.path.dirname(d)
        if up == d:
            return os.path.abspath(start)
        d = up


def rel(root, p):
    return os.path.relpath(os.path.abspath(p), root).replace('\\', '/')


def job_paths(arg):
    p = os.path.abspath(arg)
    d, base = os.path.split(p)
    low = base.lower()
    for suf in ('.build.mac', '.solve.dat', '.cdb'):
        if low.endswith(suf):
            base = base[:-len(suf)]
            break
    return {'dir': d, 'job': base, 'mac': os.path.join(d, base + '.build.mac'), 'cdb': os.path.join(d, base + '.cdb'),
            'solve': os.path.join(d, base + '.solve.dat'), 'work': os.path.join(d, '.ans', base),
            'root': workspace_root(d)}


def load_json(p, default):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def fresh_dir(d):
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    return d


def first4(line):
    s = line.split('!', 1)[0].strip()
    return s.split(',')[0].strip().upper()[:4] if s else ''


def preview_line():
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', PREVIEW_PORT))
        up = True
    except OSError:
        up = False
    finally:
        s.close()
    if up:
        return '预览面板: http://127.0.0.1:%d/ （自动显示工作区里最新的 .cdb 和它的求解段）' % PREVIEW_PORT
    return '预览面板未启动（人工审查用）: 运行 %s' % os.path.join(TOOLS, 'preview', 'start_preview.cmd')


# ---------------------------------------------------------------------------- gate and build records
def gate_mode(root):
    env = os.environ.get('ABQ_GATE')
    # 智能体的命令（DSH 会带上 DSH_* 环境变量）里，ABQ_GATE 不能用来关闸门，只看工作区配置
    agent = any(os.environ.get(k) for k in AGENT_VARS)
    if env and not agent:
        return env.lower()
    cfg = load_json(os.path.join(root, '.abq', 'config.json'), {})
    return str(cfg.get('gate', 'off')).lower() if isinstance(cfg, dict) else 'off'


def review_entry(root, cdb):
    """-> (entry or None, fingerprint now, entry is for this fingerprint)"""
    fp = fingerprint.fingerprint(cdb, fingerprint.MAPDL)
    files = load_json(os.path.join(root, '.abq', 'review.json'), {}).get('files', {})
    key = rel(root, cdb)
    ent = files.get(key)
    if ent is None:
        ent = {k.lower(): v for k, v in files.items()}.get(key.lower())
    return ent, fp, bool(ent) and ent.get('sha256') == fp['sha256']


def review_text(root, cdb):
    md = os.path.join(root, '.abq', 'review', os.path.splitext(os.path.basename(cdb))[0] + '.md')
    try:
        with open(md, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def gate_check(paths):
    """-> (allowed, lines)"""
    root, cdb = paths['root'], paths['cdb']
    if gate_mode(root) != 'human':
        return True, []
    ent, fp, current = review_entry(root, cdb)
    name = os.path.basename(cdb)
    if ent and current and ent.get('status') == 'approved':
        return True, ['人工审查: 已批准 (%s)' % ent.get('decidedAt', '')]
    lines = ['⛔ 人工审查闸门: %s 和它的求解段尚未获准求解。' % name]
    if ent and ent.get('status') == 'rejected' and current:
        lines.append('审查结论: 已退回。请按下面的审查意见修改（建模命令流改了要重新 ans build），然后再请用户审查:')
        txt = review_text(root, cdb)
        if txt:
            lines.append(txt)
        else:
            if ent.get('note'):
                lines.append('  总体意见: %s' % ent['note'])
            for i, c in enumerate(ent.get('comments') or [], 1):
                lines.append('  %d. %s — 当前: %s — 要求: %s' % (i, c.get('target', ''), c.get('current', ''), c.get('note', '')))
    elif ent and not current:
        old = ent.get('parts') or {}
        changed = [w for k, w in (('cdb', 'CDB'), ('solve', '求解段')) if old.get(k) != (fp['parts'] or {}).get(k)]
        lines.append('上次审查（%s）之后%s改过了，需要重新审查。' % (ent.get('status'), '、'.join(changed) or '文件'))
        lines += diff_lines(paths)
    else:
        lines.append('这份 CDB 和求解段还没有被审查过。')
        lines += diff_lines(paths)
    lines.append('请用户在预览面板里检查网格、材料、组件、约束、载荷和每个载荷步后点「批准求解」或「退回修改」: http://127.0.0.1:%d/'
                 % PREVIEW_PORT)
    lines.append('此时应停下来向用户说明模型要点并等待审查结论；不要绕过闸门直接调用 MAPDL。ans check 不受闸门限制。')
    return False, lines


def diff_lines(paths, show=12):
    """what differs from the approved version (or the most similar approved job next to it): CDB and solve segment"""
    try:
        reviews = load_json(os.path.join(paths['root'], '.abq', 'review.json'), {}).get('files', {})
        d = review_diff.compare_mapdl(paths['root'], rel(paths['root'], paths['cdb']), paths['cdb'], paths['solve'],
                                      reviews, show=show)
    except Exception:
        return []
    out = []
    for what, x in (('CDB', d.get('cdb')), ('求解段', d.get('solve'))):
        for i, line in enumerate(review_diff.text(x, show)):
            out.append(('%s：' % what + line) if i == 0 else line)
    return out


def builds_path(root):
    return os.path.join(root, '.abq', 'builds.json')


def build_record(paths):
    return load_json(builds_path(paths['root']), {}).get('files', {}).get(rel(paths['root'], paths['cdb']))


# ---------------------------------------------------------------------------- model summary
def load_model(paths):
    import mapdl_model
    return mapdl_model.Model(paths['cdb'], force=True, max_auto_mb=None)


def model_lines(m, v):
    st = v['stats']
    L = ['模型 %s: %d 节点, %d 单元 (%s); %s; 质量 %s' % (
        os.path.basename(m.path), st['nodes'], st['elements'], ', '.join('%s × %d' % kv for kv in st['types'].items()),
        {'3d': '三维', '2d': '平面', 'axi': '轴对称'}.get(st.get('dim'), st.get('dim')), fnum(st.get('mass')))]
    for mt in v['materials']:
        p = {r[0]: r[1] for o in mt['options'] if o['kw'] == 'MPDATA' for r in o['rows']}
        L.append('  材料 %s: %s' % (mt['name'], ', '.join('%s=%s' % (k, fnum(p[k])) for k in ('EX', 'PRXY', 'DENS', 'ALPX', 'MU')
                                                          if k in p) or '—'))
    comps = ['%s (%d 节点)' % (s['name'], s['n']) for s in v['nsets'] if not s.get('internal')] + \
            ['%s (%d 单元)' % (s['name'], s['n']) for s in v['elsets'] if not s.get('internal')]
    L.append('  组件: ' + (', '.join(comps) if comps else '没有（求解段的载荷只能对组件加，建模时要建好组件）'))
    for c in v['cdb']['contacts']:
        L.append('  接触对 REAL %d: %s%s, 接触单元 %d / 目标单元 %d' % (c['real'], c['behavior'], (', μ=%s' % fnum(c['mu']))
                                                             if c.get('mu') else '', c['nContact'], c['nTarget']))
    for s in v['steps']:
        head = '  %s · %s' % ('CDB 载荷状态' if s['name'] == 'CDB' else s['name'], s['proc'])
        if s.get('params', {}).get('TIME'):
            head += ' · TIME=%s' % s['params']['TIME']
        sets = ', '.join('%s=%s' % kv for kv in s['procParams'].items() if kv[0] != 'TIME')
        L.append(head + (' · ' + sets if sets else ''))
        for b in s['bcs']:
            L.append('      %s %s: %s%s (%d 节点)' % (b.get('command', 'D'), b['target'], ','.join(b.get('labels') or []),
                                                 (' = ' + fnum(b['mag'][0][2])) if b['mag'] else '', b['n']))
        for l in s['loads']:
            row = l['rows'][0] if l['rows'] else []
            L.append('      %s %s' % (l.get('command', l['kw']), ', '.join(str(x) for x in row)))
    return L


def check_lines(checks, show_info=True):
    tag = {'error': '错误', 'warn': '警告', 'info': '提示', 'ok': '通过'}
    ne = sum(1 for c in checks if c['level'] == 'error')
    nw = sum(1 for c in checks if c['level'] == 'warn')
    L = ['体检: %d 个错误, %d 个警告' % (ne, nw)]
    for c in checks:
        if c['level'] in ('error', 'warn') or show_info and c['level'] == 'info':
            L.append('  [%s] %s' % (tag[c['level']], c['msg']))
    return L, ne, nw


# ---------------------------------------------------------------------------- build
def cmd_build(a):
    paths = job_paths(a.job)
    job, mac = paths['job'], paths['mac']
    if not mapdl_run.JOB_RE.match(job):
        P('作业名 %r 不行：只能用英文字母、数字、下划线，字母开头，最多 32 个字符（MAPDL 的作业名和 CDB 文件名都用它）。' % job)
        return 2
    if not os.path.isfile(mac):
        P('找不到建模命令流 %s' % mac, '建模命令写在 <作业名>.build.mac 里（几何、网格、材料、组件、初始约束和载荷，不能有 SOLVE）。')
        return 2
    with open(mac, 'rb') as f:
        raw = f.read()
    text = raw.decode('utf-8', 'replace') if not raw.startswith(b'\xef\xbb\xbf') else raw[3:].decode('utf-8', 'replace')
    if os.path.isfile(paths['cdb']):
        os.remove(paths['cdb'])                     # only the CDB the tail of this build writes counts
    hits = [(i, line.strip()) for i, line in enumerate(text.splitlines(), 1) if first4(line) in ('SOLV', 'LSSO')]
    if hits:
        P('⛔ 建模命令流里有求解命令（MAPDL 按前 4 个字母认命令）：',
          *['  第 %d 行: %s' % h for h in hits[:6]],
          '建模阶段只建模型，不能求解。求解写进 %s：分析类型、时间步、载荷变化、SOLVE。' % os.path.basename(paths['solve']))
        return 3
    work = fresh_dir(os.path.join(paths['work'], 'build'))
    for x in a.with_files or []:
        src = os.path.join(paths['dir'], x) if not os.path.isabs(x) else x
        if not os.path.isfile(src):
            P('找不到 --with 指定的文件 %s' % src)
            return 2
        shutil.copy2(src, os.path.join(work, os.path.basename(src)))
    with open(os.path.join(work, 'build_input.inp'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('/PSEARCH,OFF\n' + text.rstrip('\n') + '\n' + mapdl_run.BUILD_TAIL.format(job=job))
    P('MAPDL 建模: %s（运行目录 %s）' % (os.path.basename(mac), work))
    try:
        r = mapdl_run.run(work, 'build_input.inp', job, timeout=a.timeout, say=P)
    except mapdl_run.LowMemory as e:
        P(str(e))
        return 4
    ncum_txt = mapdl_run.read(os.path.join(work, job + '_ncum.txt')).strip()
    ncum = float(ncum_txt) if re.match(r'^[-+]?\d+(\.\d*)?$', ncum_txt) else None
    log = mapdl_run.read(os.path.join(work, job + '_log.txt'))
    solved = [l.strip() for l in log.splitlines() if first4(l) in ('SOLV', 'LSSO')]
    out_cdb = os.path.join(work, job + '.cdb')
    if not r['ok'] or ncum is None or not os.path.isfile(out_cdb):
        P('✗ 建模没有完成（%.1f s，退出码 %s%s）。' % (r['seconds'], r['rc'], '，超时' if r['timedOut'] else ''))
        for e in r['errors'][:8]:
            P('  ERROR: ' + e)
        for w in r['lost']:
            P('  ' + w)
        if ncum is None:
            P('  命令流没有跑到工具追加的导出部分（出错的地方在它前面）。')
        P('输出全文: %s' % os.path.join(work, 'output.out'), '改好建模命令流后重新 ans build。')
        return 1
    if ncum > 0 or solved:
        os.remove(out_cdb)
        P('⛔ 建模过程中求解过（累计载荷步数 %s%s），这次导出作废，没有产出 CDB。' % (
            fnum(ncum), '；命令记录里有: ' + ', '.join(solved[:3]) if solved else ''),
          '建模阶段不能求解（包括用 *USE、宏、%参数% 间接调用）。求解只能写在 ' + os.path.basename(paths['solve']) + '。')
        return 3
    shutil.copy2(out_cdb, paths['cdb'])
    sha = fingerprint.file_sha256(paths['cdb'])
    rec = load_json(builds_path(paths['root']), {'files': {}})
    rec.setdefault('files', {})[rel(paths['root'], paths['cdb'])] = {
        'sha256': sha, 'builtAt': now(), 'mac': rel(paths['root'], mac), 'macSha256': hashlib.sha256(raw).hexdigest(),
        'seconds': r['seconds']}
    save_json(builds_path(paths['root']), rec)
    P('✓ 建模完成（%.1f s），导出 %s；警告 %d 条。' % (r['seconds'], paths['cdb'], len(r['warnings'])))
    for w in r['warnings'][:5]:
        P('  WARNING: ' + w[:300])
    return report(paths, header=False)


def report(paths, header=True):
    """model summary + checks of the CDB and its solve segment -> exit code (1 when there are errors)"""
    try:
        m = load_model(paths)
    except Exception as e:
        P('读取 CDB 失败: %s: %s' % (type(e).__name__, e))
        return 1
    v = m.view(geom=False)
    L = model_lines(m, v)
    cl, ne, nw = check_lines(v['checks'])
    P(*L)
    P(*cl)
    if not v['solve']['present']:
        P('下一步: 写求解段 %s（白名单: %s），然后 ans check。' % (os.path.basename(paths['solve']),
                                                          ' | '.join(solve_segment.whitelist_text().split('\n'))))
    P(preview_line())
    return 1 if ne else 0


def cmd_check(a):
    paths = job_paths(a.job)
    if not os.path.isfile(paths['cdb']):
        P('找不到 %s。先写 %s 再 ans build。' % (paths['cdb'], os.path.basename(paths['mac'])))
        return 2
    return report(paths)


# ---------------------------------------------------------------------------- mesh
def cmd_mesh(a):
    paths = job_paths(a.job)
    if not os.path.isfile(paths['cdb']):
        P('找不到 %s。先 ans build。' % paths['cdb'])
        return 2
    try:
        import ans_mesh
    except ImportError as e:
        P('网格体检需要 numpy: %s' % e)
        return 2
    m = load_model(paths)
    r = ans_mesh.analyze(m, worst=a.worst)
    cn = ans_mesh.FAM_CN
    L = ['网格体检 %s' % os.path.basename(paths['cdb']),
         '规模: %d 节点, %d 单元（评质量 %d）; %s' % (r['nodes'], r['elements'], r['evaluated'],
                                                ', '.join('%s %d' % (cn.get(k, k), n) for k, n in r['families'].items()))]
    if r['skipped']:
        L.append('不评质量（接触/目标/梁/杆/弹簧/质量点等）: ' + ', '.join('%s %d' % kv for kv in r['skipped'].items()))
    L.append('%-10s %-8s %10s %10s %18s %18s' % ('单元族', '指标', '最差', '中位', '超出常用上限', '未达推荐值'))
    for fam, mm in r['metrics'].items():
        n = r['families'][fam]
        for k in ('sjac', 'aspect', 'angle_min', 'angle_max', 'shape', 'warp'):
            if k not in mm:
                continue
            e = mm[k]
            lim = '%d (%.1f%%)' % (e['limitFail'], 100.0 * e['limitFail'] / n) if 'limitFail' in e else '-'
            good = '%d (%.1f%%)' % (e['goodFail'], 100.0 * e['goodFail'] / n) if 'goodFail' in e else '-'
            L.append('%-10s %-8s %10s %10s %18s %18s' % (cn.get(fam, fam), ans_mesh.METRIC_CN[k], fnum(e['worst'], 4),
                                                       fnum(e['median'], 4), lim, good))
        if mm.get('inverted'):
            L.append('%-10s 翻转/退化单元 %d 个（雅可比 <= 0，求解器会拒绝）' % (cn.get(fam, fam), mm['inverted']))
    if r['worst']:
        L.append('最差单元:')
        for w in r['worst']:
            L.append('  单元 %d (%s) %s = %s%s, 形心 (%s)' % (w['element'], w['type'], ans_mesh.METRIC_CN.get(w['metric'], w['metric']),
                                                        fnum(w['value'], 4), ' [翻转]' if w['inverted'] else '',
                                                        ', '.join(fnum(c, 4) for c in w['centroid'])))
    L.append('推荐值达标率 %.2f%%' % r['goodPct'])
    L.append('结论: ' + {'PASS': '通过: 没有翻转单元，也没有超出常用上限的单元',
                       'WARN': '有警告: %d 个单元超出常用上限，先看最差单元在哪' % r['limitFail'],
                       'FAIL': '不通过: %d 个翻转/退化单元，求解器会拒绝，必须先修网格' % r['inverted']}[r['verdict']])
    os.makedirs(paths['work'], exist_ok=True)
    save_json(os.path.join(paths['work'], 'mesh.json'), r)
    P(*L)
    return 1 if r['verdict'] == 'FAIL' else 0


# ---------------------------------------------------------------------------- run
def cmd_run(a):
    paths = job_paths(a.job)
    job = paths['job']
    lins, msg = parse_lins(a.lin)
    if msg:
        P(msg)
        return 2
    for k, what in (('cdb', 'CDB（ans build 产出）'), ('solve', '求解段')):
        if not os.path.isfile(paths[k]):
            P('找不到%s %s。' % (what, paths[k]))
            return 2
    try:
        m = load_model(paths)
    except Exception as e:
        P('读取 CDB 失败: %s: %s' % (type(e).__name__, e))
        return 2
    bad = []
    for ln, t in m.violations[:20]:
        bad.append('  CDB 第 %d 行: %s' % (ln, t))
    seg = m.seg
    for ln, t in (seg['violations'] if seg else [])[:20]:
        bad.append('  求解段第 %d 行: %s' % (ln, t))
    for ln in (solve_segment.needs_nropt_full(seg, m.antype, m._step['procParams'].get('NLGEOM') == 'ON') if seg else []):
        bad.append('  求解段第 %d 行: 线性静力里用 EKILL/EALIVE，要在第一次 SOLVE 之前写 NROPT,FULL' % ln)
    if seg and not seg['steps']:
        bad.append('  求解段里没有 SOLVE')
    if seg and seg['commands'] and seg['commands'][0]['name'] != '/SOLU':
        bad.append('  求解段第一条命令应是 /SOLU（第 %d 行是 %s）' % (seg['commands'][0]['line'], seg['commands'][0]['cmd']))
    if bad:
        P('⛔ 没有求解：CDB 或求解段有不允许的内容（白名单见 ans check）:', *bad)
        return 2
    rec = build_record(paths)
    sha = fingerprint.file_sha256(paths['cdb'])
    if not rec or rec.get('sha256') != sha:
        P('⛔ 没有求解：%s 不是最近一次 ans build 产出的那一份（%s）。' % (
            os.path.basename(paths['cdb']), '没有构建记录' if not rec else '构建之后被改过'),
          'CDB 只能由 ans build 从建模命令流导出，不能手改。改模型请改 %s 再 ans build。' % os.path.basename(paths['mac']))
        return 2
    ok, lines = gate_check(paths)
    P(*lines)
    if not ok:
        return 3
    root = paths['root']
    try:
        nodes = m.view(geom=False)['stats']['nodes']
    except Exception:
        nodes = int(os.path.getsize(paths['cdb']) / 150)          # rough: about 150 bytes of CDB per node
    small = 0 if a.cpus else SMALL_MODEL_NODES
    if a.plan:
        pl = res.plan_job(root, paths['dir'], job, paths['cdb'], nodes, small, a.cpus or 0, None, solver='mapdl')
        P(*pl['lines'])
        if not pl['ok']:
            P('现在启动的话要排队：%s' % pl['wait'])
        return 0
    # in line first: the previous run directory stays as it is while this job waits
    pl = res.acquire(root, paths['dir'], job, paths['cdb'], nodes, small, a.cpus or 0, None, max_wait_min=a.queue_max,
                     echo=lambda *x, **k: print(*x, flush=True), solver='mapdl')
    P(*pl['lines'])
    if not pl['ok']:
        return 4
    try:
        fp = fingerprint.fingerprint(paths['cdb'], fingerprint.MAPDL)
        work = fresh_dir(os.path.join(paths['work'], 'run'))
        shutil.copy2(paths['cdb'], os.path.join(work, job + '.cdb'))
        shutil.copy2(paths['solve'], os.path.join(work, job + '.solve.dat'))
        with open(paths['solve'], 'rb') as f:
            seg_text = f.read().decode('latin-1')
        with open(os.path.join(work, 'run_input.inp'), 'w', encoding='latin-1', newline='\n') as f:
            f.write(mapdl_run.RUN_HEAD.format(job=job) + seg_text.rstrip('\n') + '\n' + mapdl_run.RUN_TAIL)
        antypes = [s['antype'] for s in m.seg_steps]
        names = [s['proc'] for s in m.seg_steps]
        run = {'job': job, 'fingerprint': fp['sha256'], 'parts': fp['parts'], 'startedAt': now(), 'cpus': pl['cpus'],
               'loadSteps': len(m.seg_steps), 'antypes': antypes, 'components': m.cm_names}
        save_json(os.path.join(work, 'run.json'), dict(run, state='running'))
        P('MAPDL 求解: %d 个载荷步 (%s)，运行目录 %s' % (len(m.seg_steps), ', '.join(names), work),
          ('不设墙钟上限' if not a.timeout else '墙钟上限 %d 秒' % a.timeout) +
          '；进度看门狗：%s 分钟（或平均每个子步用时的 5 倍；第一个子步之前按输出文件有没有变化、至少 30 分钟）'
          '没有新子步就报警，连续 2 次报警停止作业。' % res._g(a.stall_min))
        try:
            r = mapdl_run.run(work, 'run_input.inp', job, timeout=a.timeout, cpus=pl['cpus'], say=P,
                              watch=lambda p: res.watch(p, work, job, None, mapdl_run.kill_tree, timeout=a.timeout,
                                                        stall_min=a.stall_min, names=names, kind='mapdl',
                                                        echo=lambda *x, **k: print(*x, flush=True)))
        except mapdl_run.LowMemory as e:
            save_json(os.path.join(work, 'run.json'), dict(run, state='not started', why=str(e)))
            P(str(e))
            return 4
    finally:
        res.release(root, os.path.join(paths['dir'], job))
    nls = mapdl_run.solve_count(r['out'])
    rst = next((os.path.join(work, job + x) for x in ('.rst', '.rth', '.rmg') if os.path.isfile(os.path.join(work, job + x))), None)
    ok = r['ok'] and rst is not None
    stalled = r.get('how') == 'stalled'
    rows = res.mntr_rows(os.path.join(work, job + '.mntr'))
    run.update(endedAt=now(), seconds=r['seconds'], rc=r['rc'], timedOut=r['timedOut'], stalled=stalled,
               errors=r['errors'][:10], warnings=r['warnings'][:20], lost=r['lost'], loadStepsStarted=nls,
               rst=os.path.basename(rst) if rst else None, state='done' if ok else 'failed')
    save_json(os.path.join(work, 'run.json'), run)
    res.log_perf(root, {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'solver': 'mapdl', 'job': job, 'nodes': nodes,
                        'equations': pl['need']['equations'], 'cpus': pl['cpus'], 'mem_mb': pl['mem_mb'],
                        'wall_s': r['seconds'], 'substeps': len(rows),
                        'iterations': rows[-1]['total_iters'] if rows else None,
                        'state': 'stalled' if stalled else 'timeout' if r['timedOut'] else ('done' if ok else 'failed')})
    if not ok:
        P('✗ 求解失败（%.1f s，退出码 %s%s；开始了 %d / %d 个载荷步）。' % (
            r['seconds'], r['rc'], '，超时' if r['timedOut'] else '，卡住已停止（连续两次进度报警）' if stalled else '',
            nls, len(m.seg_steps)))
        for e in r['errors'][:8]:
            P('  ERROR: ' + e)
        for w in r['lost']:
            P('  ' + w)
        if rst is None and not r['errors']:
            P('  没有生成结果文件。')
        P('输出全文: %s' % os.path.join(work, 'output.out'),
          '改求解段（或建模命令流后重新 ans build），再请用户审查；不要改 CDB。')
        return 1
    P('✓ 求解完成（%.1f s），%d 个载荷步，结果 %s；警告 %d 条。' % (r['seconds'], nls, rst, len(r['warnings'])))
    for w in r['warnings'][:5]:
        P('  WARNING: ' + w[:300])
    return rst_summary(paths, run, a.comp or [], None, lins)


# ---------------------------------------------------------------------------- results
LIN_HELP = ('静力、瞬态：应力线性化（MAPDL 的 PRSECT），给评定线起点和终点的坐标（模型单位，逗号隔开，前面可加「名字:」），'
            '工具取离两点最近的节点作端点；可多次。在主摘要那个结果集上算（默认最后一个，--step N 时第 N 步末）')
LIN_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,15}$')


def parse_lins(items):
    """--lin values -> ([(name, (x1, y1, z1), (x2, y2, z2)), ...], error message or None)"""
    out = []
    for k, it in enumerate(items or [], 1):
        name, _, body = it.rpartition(':')
        name = name.strip() or 'SCL%d' % k
        if not LIN_NAME.match(name):
            return [], '--lin 的名字「%s」只能用英文字母、数字、下划线，字母开头，不超过 16 个字符。' % name
        try:
            v = [float(x) for x in body.replace(' ', '').split(',') if x != '']
        except ValueError:
            v = []
        if len(v) != 6:
            return [], ('--lin %s：要 6 个数，起点 x,y,z 和终点 x,y,z（模型单位，逗号隔开），例如 '
                        '--lin SCL1:0,0.5,0.1,0,0.512,0.11' % it)
        if v[:3] == v[3:]:
            return [], '--lin %s：起点和终点是同一个点。' % it
        out.append((name, tuple(v[:3]), tuple(v[3:])))
    if len(out) > ans_post.MAX_LINES:
        return [], '--lin 一次最多 %d 条（给了 %d 条）。' % (ans_post.MAX_LINES, len(out))
    if len({n for n, _, _ in out}) < len(out):
        return [], '--lin 的名字有重复。'
    return out, None


def lin_lines(res, kind_set):
    """printed lines of the stress linearization"""
    L = []
    lins = res.get('lin') or []
    if not lins:
        return L
    L.append('  应力线性化（MAPDL 的 PRSECT，%s；分量在总体坐标里，SINT 应力强度 = 最大主应力差（Tresca），SEQV 等效应力'
             '（von Mises），两者和坐标系无关；起点、终点就是 PRSECT 的 INSIDE、OUTSIDE；PRSECT 只给 4 位有效数字）:' % kind_set)
    for d in lins:
        if 'nodes' not in d:
            L.append('    %s: 没有取到端点（见 post.out）' % d['name'])
            continue
        na, nb = d['nodes']
        if na == nb:
            L.append('    %s: ✗ 两端取到了同一个节点 %d（两点太近，或都离模型很远），没有线性化。' % (d['name'], na))
            continue
        L.append('    %s: 起点 节点 %d (%s) → 终点 节点 %d (%s)，评定线长 %s' % (
            d['name'], na, ', '.join(fnum(c, 6) for c in d['xyz'][0]), nb, ', '.join(fnum(c, 6) for c in d['xyz'][1]),
            fnum(d['length'], 5)))
        far = max(d['snap'])
        if far > 0.05 * max(d['givenLength'], 1e-30):
            L.append('      ⚠ 端点离给定位置 %s / %s（给定的评定线长 %s）：这一条可能没有贯穿整个壁厚，数不能用。'
                     '把端点放在几何棱边或顶点上（那里一定有节点），或加密网格后再取。' % (
                         fnum(d['snap'][0], 4), fnum(d['snap'][1], 4), fnum(d['givenLength'], 5)))
        elif far > 1e-6 * max(d['givenLength'], 1e-30):
            L.append('      端点离给定位置 %s / %s' % (fnum(d['snap'][0], 4), fnum(d['snap'][1], 4)))
        m = d.get('membrane')
        if not m:
            L.append('      ✗ PRSECT 没有输出（评定线要整段在模型里；见 post.out）')
            continue

        def two(sec, key):
            v = d.get(sec) or {}
            return '起点 %s、终点 %s' % (fnum(v.get('I', {}).get(key), 4), fnum(v.get('O', {}).get(key), 4))
        L.append('      薄膜: SINT %s、SEQV %s（SX %s、SY %s、SZ %s、SXY %s、SYZ %s、SXZ %s）' % tuple(
            fnum(m.get(k), 4) for k in ('SINT', 'SEQV', 'SX', 'SY', 'SZ', 'SXY', 'SYZ', 'SXZ')))
        L.append('      薄膜+弯曲: SINT %s；SEQV %s' % (two('mb', 'SINT'), two('mb', 'SEQV')))
        L.append('      总应力: SINT %s；SEQV %s' % (two('total', 'SINT'), two('total', 'SEQV')))
        L.append('      峰值（总应力减薄膜+弯曲）: SINT %s' % two('peak', 'SINT'))
    L.append('    应力分类（一次/二次/峰值）和评定限值由你按规范判断；评定线要整段穿过材料、端点在内外表面上。')
    return L


def rst_summary(paths, run, comps, step=None, lins=()):
    work = os.path.join(paths['work'], 'run')
    rst = os.path.join(work, run.get('rst') or '')
    if not run.get('rst') or not os.path.isfile(rst):
        P('没有结果文件。先 ans run。')
        return 2
    known = {c.upper() for k in ('NODE', 'ELEM') for c in (run.get('components') or {}).get(k, [])}
    comps = [c.upper() for c in comps]
    unknown = [c for c in comps if c not in known]
    if unknown:
        P('这些组件在模型里没有: %s（有: %s）' % (', '.join(unknown), ', '.join(sorted(known)) or '—'))
        return 2
    kind = ans_post.kind_of(run.get('antypes') or [])
    nls = int(run.get('loadSteps') or 1)
    if step is not None:
        if kind not in ('static', 'transient'):
            P('--step 只用于静力、瞬态（这个作业是%s）。' % {'modal': '模态', 'buckle': '特征值屈曲', 'harmonic': '谐响应'}[kind])
            return 2
        if not 1 <= step <= nls:
            P('--step %d 不在 1～%d 之间（求解段有 %d 个载荷步）。' % (step, nls, nls))
            return 2
    if lins and kind not in ('static', 'transient'):
        P('--lin 只用于静力、瞬态（这个作业是%s）。' % {'modal': '模态', 'buckle': '特征值屈曲', 'harmonic': '谐响应'}[kind])
        return 2
    for f in os.listdir(work):
        if f.startswith('post_') and f.endswith('.txt'):
            os.remove(os.path.join(work, f))
    ecomps = [c for c in comps if c in {e.upper() for e in (run.get('components') or {}).get('ELEM', [])}]
    with open(os.path.join(work, 'post_input.inp'), 'w', encoding='latin-1', newline='\n') as f:
        f.write(ans_post.post_input(paths['job'], os.path.splitext(rst)[1][1:], kind, comps, ecomps, nls, step, lins))
    try:
        r = mapdl_run.run(work, 'post_input.inp', 'post', timeout=900, out='post.out', say=P)
    except mapdl_run.LowMemory as e:
        P(str(e))
        return 4
    res = ans_post.parse(work, comps, nls, lins)
    res.update(kind=kind, readAt=now(), postErrors=r['errors'][:5], fingerprint=run.get('fingerprint'),
               askedStep=step)
    save_json(os.path.join(work, 'summary.json'), res)
    L = ['结果 %s（%s，%d 个结果集）:' % (os.path.basename(rst), {'static': '静力', 'transient': '瞬态', 'modal': '模态',
                                                             'buckle': '特征值屈曲', 'harmonic': '谐响应'}[kind],
                                       len(res.get('sets') or []))]
    sets = res.get('sets') or []
    if kind in ('modal', 'buckle'):
        what = '频率 (Hz)' if kind == 'modal' else '载荷因子'
        L.append('  %s: %s' % (what, ', '.join(fnum(s['freq'], 6) for s in sets[:20]) + (' …' if len(sets) > 20 else '')))
        modes = res.get('modes') or []
        if modes:
            L.append('  各阶振型的平动分量（全模型 |UX| / |UY| / |UZ| 的最大值；振型已归一化，只比相对大小；'
                     '梁、壳的扭转振型主要是转角，这里看不出来）:')
            for md in modes[:12]:
                L.append('    第 %d 阶 %s: %s / %s / %s → 以 %s 向为主' % (
                    md['mode'], fnum(md['freq'], 6), fnum(md['umax'][0], 4), fnum(md['umax'][1], 4), fnum(md['umax'][2], 4),
                    md['dominant']))
            if len(modes) > 12:
                L.append('    …（共 %d 阶，见 summary.json 的 modes）' % len(modes))
    elif kind == 'harmonic':
        amp = res.get('amplitude') or []
        if amp:
            pk = max(amp, key=lambda x: x['umax'])
            L.append('  频率 %s～%s Hz，%d 个点；最大位移幅值 %s，在 %s Hz（节点 %d）' % (
                fnum(amp[0]['freq']), fnum(amp[-1]['freq']), len(amp), fnum(pk['umax']), fnum(pk['freq']), pk['node']))
    elif res.get('main'):
        mn = res['main']
        L.append('  下面的数取自载荷步 %d 末（子步 %d，时间 %s）%s' % (
            mn['step'], mn['substep'], fnum(mn['time']),
            '：--step %d' % step if step else '：最后一个结果集' + ('；别的载荷步见后面「各载荷步末」，或 ans rst %s --step N'
                                                              % paths['job'] if len(res.get('steps') or []) > 1 else '')))
    elif sets:
        last = sets[-1]
        L.append('  最后一个结果集: 载荷步 %d、子步 %d、时间 %s' % (last['step'], last['substep'], fnum(last['time'])))
    u = res.get('umax')
    if u:
        L.append('  %s最大位移 USUM = %s，节点 %d，位置 (%s)，分量 UX %s / UY %s / UZ %s' % (
            '第一阶振型的' if kind in ('modal', 'buckle') else '', fnum(u['value']), u['node'], ', '.join(fnum(c, 5) for c in u['xyz']),
            fnum(u['u'][0]), fnum(u['u'][1]), fnum(u['u'][2])))
    rt = res.get('reaction')
    if rt:
        L.append('  反力合计（全部约束节点）: ' + ', '.join('%s %s' % (k, fnum(v)) for k, v in rt.items()))
    for c, cv in (res.get('comps') or {}).items():
        L.append('  组件 %s（%d 节点）: 节点力合计 FSUM FX %s / FY %s / FZ %s；最大位移 %s（节点 %d）' % (
            c, cv['nodes'], fnum(cv['fsum'][0]), fnum(cv['fsum'][1]), fnum(cv['fsum'][2]), fnum(cv['umax']), cv['node']))
        st = cv.get('stress')
        if st:
            L.append('    节点应力（节点平均，总体坐标；壳是顶面；最大 / 最小及节点）: ' + '；'.join(
                '%s %s（%d）/ %s（%d）' % (k, fnum(v['max']), v['maxNode'], fnum(v['min']), v['minNode']) for k, v in st.items()))
        elif 'stress' in cv:
            L.append('    这些节点上没有节点应力（梁、杆单元的应力在单元上，不在节点上）')
    s = res.get('seqv')
    if s and s['value']:
        L.append('  最大等效应力 SEQV（节点平均）= %s，节点 %d' % (fnum(s['value']), s['node']))
    mn = res.get('main') or {}
    L += lin_lines(res, '载荷步 %s 末' % mn['step'] if mn.get('step') else '最后一个结果集')
    steps = res.get('steps') or []
    if len(steps) > 1:
        L.append('  各载荷步末（每步的最后一个子步）:')
        for st in steps:
            rt = st.get('reaction') or {}
            u = st.get('umax') or {}
            L.append('    载荷步 %d（子步 %d，时间 %s）: 反力合计 %s；最大位移 %s%s；最大等效应力 %s' % (
                st['step'], st['substep'], fnum(st['time']), ', '.join('%s %s' % (k, fnum(v)) for k, v in rt.items()) or '—',
                fnum(u['value']) if u else '—', '（节点 %d）' % u['node'] if u else '',
                fnum(st['seqv']['value']) if st.get('seqv') and st['seqv']['value'] else '—'))
            for c, cv in (st.get('comps') or {}).items():
                sx = (cv.get('stress') or {}).get('SX')
                L.append('      组件 %s: 节点力合计 FX %s / FY %s / FZ %s；最大位移 %s%s' % (
                    c, fnum(cv['fsum'][0]), fnum(cv['fsum'][1]), fnum(cv['fsum'][2]), fnum(cv['umax']),
                    '；SX %s～%s' % (fnum(sx['min']), fnum(sx['max'])) if sx else ''))
        if len(steps) < min(len({x['step'] for x in sets}), ans_post.MAX_STEPS):
            L.append('    （有的载荷步在结果文件里没有结果集，没列出）')
    if r['errors']:
        L.append('  （取数时 MAPDL 报错，后面的项目可能缺: %s）' % r['errors'][0][:200])
    L.append('  数字由 MAPDL 从结果文件取出（%s），不是模型自己报的。' % os.path.join(work, 'summary.json'))
    P(*L)
    return 0


def cmd_rst(a):
    paths = job_paths(a.job)
    run = load_json(os.path.join(paths['work'], 'run', 'run.json'), None)
    if not run or run.get('state') != 'done':
        P('还没有成功的求解结果。先 ans run %s。' % paths['job'])
        return 2
    lins, msg = parse_lins(a.lin)
    if msg:
        P(msg)
        return 2
    return rst_summary(paths, run, a.comp or [], a.step, lins)


def cmd_verify(a):
    paths = job_paths(a.job)
    work = os.path.join(paths['work'], 'run')
    run = load_json(os.path.join(work, 'run.json'), None)
    L, bad = ['收工前核查 %s:' % paths['job']], 0
    if not run:
        P('还没有求解记录。先 ans run %s。' % paths['job'])
        return 2
    ok_run = run.get('state') == 'done' and not run.get('errors') and not run.get('lost')
    L.append('  %s 求解: %s，%s 个载荷步开始求解 / 求解段 %s 个' % ('✓' if ok_run else '✗', run.get('state'),
                                                       run.get('loadStepsStarted'), run.get('loadSteps')))
    bad += not ok_run
    if run.get('loadStepsStarted') is not None and run.get('loadSteps') and run['loadStepsStarted'] < run['loadSteps']:
        L.append('  ✗ 有载荷步没有开始求解')
        bad += 1
    fp = fingerprint.fingerprint(paths['cdb'], fingerprint.MAPDL) if os.path.isfile(paths['cdb']) else None
    same = fp is not None and fp['sha256'] == run.get('fingerprint')
    L.append('  %s 结果对应现在的 CDB 和求解段%s' % ('✓' if same else '✗', '' if same else '：求解之后文件改过（或 CDB 不在了），结果已过期，要重新审查、求解'))
    bad += not same
    if gate_mode(paths['root']) == 'human':
        ent, fpn, cur = review_entry(paths['root'], paths['cdb']) if fp else (None, None, False)
        appr = bool(ent) and cur and ent.get('status') == 'approved' and ent.get('sha256') == run.get('fingerprint')
        L.append('  %s 人工审查: %s' % ('✓' if appr else '✗', '已批准，就是求解的这一份' if appr else '求解的这一份没有对应的批准记录'))
        bad += not appr
    rst = os.path.join(work, run.get('rst') or '-')
    has = os.path.isfile(rst)
    L.append('  %s 结果文件 %s' % ('✓' if has else '✗', run.get('rst') or '没有'))
    bad += not has
    warn = run.get('warnings') or []
    L.append('  警告 %d 条%s' % (len(warn), ('：' + '；'.join(w[:120] for w in warn[:3])) if warn else ''))
    summ = load_json(os.path.join(work, 'summary.json'), None)
    if summ and summ.get('fingerprint') == run.get('fingerprint'):
        L.append('  结果摘要: %s（ans rst 写的）' % os.path.join(work, 'summary.json'))
    else:
        L.append('  还没有和这次求解对应的结果摘要: 运行 ans rst %s' % paths['job'])
    L.append('结论: ' + ('通过' if not bad else '不通过（%d 项）' % bad))
    P(*L)
    return 0 if not bad else 1


def cmd_status(a):
    paths = job_paths(a.job)
    L = ['作业 %s（%s）' % (paths['job'], paths['dir'])]
    for k, what in (('mac', '建模命令流'), ('cdb', 'CDB'), ('solve', '求解段')):
        p = paths[k]
        L.append('  %s %s: %s' % (what, os.path.basename(p), time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(p)))
                                  if os.path.isfile(p) else '没有'))
    if os.path.isfile(paths['cdb']):
        rec = build_record(paths)
        sha = fingerprint.file_sha256(paths['cdb'])
        L.append('  构建记录: %s' % ('一致（%s）' % rec['builtAt'] if rec and rec.get('sha256') == sha else
                                 '和现在的 CDB 不一致，要重新 ans build' if rec else '没有（CDB 不是 ans build 产出的）'))
        mode = gate_mode(paths['root'])
        if mode == 'human':
            ent, fp, cur = review_entry(paths['root'], paths['cdb'])
            st = '未审查' if not ent else ('已批准' if ent.get('status') == 'approved' else '已退回') + ('' if cur else '（之后改过，要重新审查）')
            L.append('  人工审查: %s' % st)
        else:
            L.append('  人工审查闸门: 关')
    run = load_json(os.path.join(paths['work'], 'run', 'run.json'), None)
    if run:
        L.append('  上次求解: %s，开始 %s%s' % (run.get('state'), run.get('startedAt'),
                                         '，用时 %s s' % run['seconds'] if run.get('seconds') is not None else ''))
        if run.get('state') == 'running':
            lock = os.path.join(paths['work'], 'run', paths['job'] + '.lock')
            L.append('  MAPDL 还在算' if os.path.isfile(lock) else '  记录是「进行中」，但没有锁文件：上次可能被中断了')
    else:
        L.append('  还没有求解过')
    root = paths['root']
    me = os.path.join(paths['dir'], paths['job'])
    mine = [r for r in res.running_jobs(root) if r.get('path') == me]
    if mine:
        rows = res.mntr_rows(os.path.join(paths['work'], 'run', paths['job'] + '.mntr'))
        L.append('  运行资源: %d 核，已运行 %s；完成 %d 个子步%s' % (
            mine[0].get('cpus', 1), res._mins(time.time() - mine[0].get('started', time.time())), len(rows),
            '（第 %d 个载荷步第 %d 个子步，累计时间 %g）' % (rows[-1]['step'], rows[-1]['inc'], rows[-1]['total_time'])
            if rows else ''))
    others = [r for r in res.running_jobs(root) if r.get('path') != me]
    if others:
        L.append('  同时在运行: ' + ', '.join('%s(%d 核)' % (r.get('job', '?'), r.get('cpus', 1)) for r in others))
    queue = [r for r in res.queued_jobs(root) if r.get('queued', 0) < time.time()]
    if queue:
        L.append('  排队中（按提交顺序）: ' + ', '.join('%s(已等 %s)' % (r.get('job', '?'), res._mins(time.time() - r['queued']))
                                             for r in queue))
    P(*L)
    return 0


def cmd_gate(a):
    root = workspace_root(os.getcwd())
    cfg = os.path.join(root, '.abq', 'config.json')
    agent = any(os.environ.get(k) for k in AGENT_VARS)
    if a.action == 'off' and agent:
        P('⛔ 人工审查闸门只能由用户在自己的终端里关闭，智能体不能关。',
          '需要求解时：停下来向用户说明模型要点（网格、材料、组件、约束、载荷、载荷步），请用户在预览面板里批准；',
          '不要尝试改配置文件或环境变量来绕过。只做检查用 ans check，它不受闸门限制。')
        return 3
    if a.action in ('on', 'off'):
        data = load_json(cfg, {})
        data['gate'] = 'human' if a.action == 'on' else 'off'
        save_json(cfg, data)
    mode = gate_mode(root)
    P('工作区: %s' % root, '人工审查闸门: %s' % ('开（ans run 需要用户先在预览面板里批准这份 CDB 和求解段）' if mode == 'human'
                                          else '关（ans run 不需要批准）'))
    if os.environ.get('ABQ_GATE'):
        P('（环境变量 ABQ_GATE=%s %s）' % (os.environ['ABQ_GATE'], '在智能体的命令里不起作用，以工作区配置为准' if agent else '决定了当前状态'))
    return 0


def cmd_preview(a):
    P(preview_line())
    return 0


UNITS = """MAPDL 不认单位，只要求整套数据自洽。常用的两套:
  量         SI (m)          mm 制
  长度       m               mm
  力         N               N
  质量       kg              t (吨)
  时间       s               s
  应力/压力  Pa (N/m^2)      MPa (N/mm^2)
  密度       kg/m^3          t/mm^3（钢 7.85e-9）
  弹性模量   Pa（钢 2.0e11）  MPa（钢 2.0e5）
  加速度     m/s^2（g=9.81） mm/s^2（g=9810）
  频率       Hz              Hz
检查: 钢的 sqrt(E/rho) 约 5000 m/s（SI）或 5.0e6 mm/s（mm 制），ans check 会按这个判单位是否混用。
注意: F 命令按组件加时，值是「每个节点」的，不是总力；面载荷 SF 的 PRES 是压强。"""


def cmd_units(a):
    P(UNITS)
    return 0


# ---------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(prog=SELF, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('build', help='跑建模命令流，导出 CDB 并体检')
    s.add_argument('job')
    s.add_argument('--with', dest='with_files', action='append', help='建模命令流要读的外部文件（几何文件等），可多次')
    s.add_argument('--timeout', type=int, default=1800)
    s = sub.add_parser('check', help='解析 CDB 和求解段，核对白名单，体检（不启动 MAPDL）')
    s.add_argument('job')
    s = sub.add_parser('mesh', help='网格质量体检')
    s.add_argument('job')
    s.add_argument('--worst', type=int, default=10)
    s = sub.add_parser('run', help='核对批准记录后求解')
    s.add_argument('job')
    s.add_argument('--cpus', type=int, default=0, help='核数（共享内存并行 -smp）。默认 0 = 按本机此刻的空闲物理核、'
                                                         '模型规模和许可证上限（config.json 的 mapdl_max_cores）自动定')
    s.add_argument('--comp', action='append', help='结果摘要里另取这个组件上的合计，可多次')
    s.add_argument('--lin', action='append', metavar='[名字:]x1,y1,z1,x2,y2,z2', help=LIN_HELP)
    s.add_argument('--timeout', type=int, default=0, help='墙钟上限（秒）。默认 0 = 不限时，由进度看门狗判断卡住')
    s.add_argument('--stall-min', type=float, default=15.0,
                   help='进度看门狗：多少分钟（或平均每个子步用时的 5 倍，取大者）没有新子步就报警，连续 2 次报警停止作业')
    s.add_argument('--queue-max', type=int, default=0, help='资源不够时最多排队多少分钟（默认 0 = 一直等）')
    s.add_argument('--plan', action='store_true', help='只看资源分配决定，不求解')
    s = sub.add_parser('rst', help='结果摘要')
    s.add_argument('job')
    s.add_argument('--comp', action='append', help='另取这个组件上的节点力合计、最大位移和应力极值，可多次')
    s.add_argument('--step', type=int, help='静力、瞬态：主摘要取第几个载荷步末的结果（默认最后一个结果集；'
                                            '多个载荷步时每步末的结果总会另列）')
    s.add_argument('--lin', action='append', metavar='[名字:]x1,y1,z1,x2,y2,z2', help=LIN_HELP)
    s = sub.add_parser('verify', help='收工前核查')
    s.add_argument('job')
    s = sub.add_parser('status', help='作业状态')
    s.add_argument('job')
    s = sub.add_parser('gate', help='人工审查闸门')
    s.add_argument('action', choices=('status', 'on', 'off'), nargs='?', default='status')
    sub.add_parser('preview', help='预览面板地址')
    sub.add_parser('units', help='单位制')
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    fn = {'build': cmd_build, 'check': cmd_check, 'mesh': cmd_mesh, 'run': cmd_run, 'rst': cmd_rst, 'verify': cmd_verify,
          'status': cmd_status, 'gate': cmd_gate, 'preview': cmd_preview, 'units': cmd_units}[a.cmd]
    return fn(a)


if __name__ == '__main__':
    sys.exit(main())
