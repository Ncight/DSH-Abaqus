# -*- coding: utf-8 -*-
"""Resources for Abaqus jobs, decided from what this machine has free right now and what the job needs.

    sample_machine()   -> busy CPUs of the whole machine and of each Abaqus solver process (measured over 3 s)
    plan_job(...)      -> cores and memory for one job, or why it has to wait
    acquire(...)       -> wait in line (first come, first served) until the plan fits, then register the job
                          (several jobs may run at once)
    watch(...)         -> wait for the solver while reading .sta/.msg: progress lines, stall alarms, stop on the
                          second stall alarm; no fixed wall-clock limit unless one was asked for
    clean_stale_lock() -> remove <job>.lck left behind by a solver that is no longer running
    log_perf(...)      -> one line per finished job in <workspace>/.abq/perf.jsonl (measured speed of this machine)

What is reserved for other work (cores and GB) comes from <workspace>/.abq/config.json ("reserve_cores",
"reserve_mem_gb"); without those keys it scales with the machine: a quarter of the physical cores (at least 2) and a
fifth of the RAM (at least 4 GB) -- 4 cores and 12 GB on a 16-core, 62 GB computer.
"""
import ctypes
import json
import math
import os
import re
import subprocess
import time
from ctypes import wintypes

try:
    import msvcrt
except ImportError:                       # not on Windows
    msvcrt = None


# ---------------------------------------------------------------------------- machine
def physical_cores():
    """physical cores over all processor groups (GetLogicalProcessorInformationEx, RelationProcessorCore)"""
    try:
        k = ctypes.windll.kernel32
        size = wintypes.DWORD(0)
        k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not k.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(size)):
            raise OSError
        n, off = 0, 0
        while off < size.value:
            rel, sz = ctypes.cast(ctypes.byref(buf, off), ctypes.POINTER(wintypes.DWORD * 2)).contents
            if rel == 0:
                n += 1
            if sz <= 0:
                break
            off += sz
        if n > 0:
            return n
    except Exception:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def logical_cpus():
    try:
        return int(ctypes.windll.kernel32.GetActiveProcessorCount(0xFFFF)) or (os.cpu_count() or 1)
    except Exception:
        return os.cpu_count() or 1


def _system_times():
    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    f = lambda t: (t.dwHighDateTime << 32) | t.dwLowDateTime
    return f(idle), f(kern), f(user)


def busy_cores(seconds=2.0):
    """how many logical CPUs were busy on average over the last `seconds` (all processes together)"""
    try:
        i0, k0, u0 = _system_times()
        time.sleep(seconds)
        i1, k1, u1 = _system_times()
        total = (k1 - k0) + (u1 - u0)          # kernel time includes idle time
        if total <= 0:
            return 0.0
        return max(0.0, 1.0 - float(i1 - i0) / total) * logical_cpus()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------- processes (who is using the cores)
ABQ_SOLVERS = ('standard.exe', 'standard_dp.exe', 'explicit.exe', 'explicit_dp.exe', 'pre.exe', 'package.exe',
               'packager.exe')


class _PE32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]


class _PMC(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (n, ctypes.c_size_t) for n in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                                       'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                                       'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]


try:                                      # a private kernel32 handle, so the prototypes below touch nobody else
    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
except Exception:                         # not on Windows
    _k32 = None


def process_table():
    """{pid: (parent pid, exe name in lower case)} for every process (Toolhelp snapshot); {} when unavailable"""
    out = {}
    if _k32 is None:
        return out
    snap = _k32.CreateToolhelp32Snapshot(0x2, 0)             # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        ok = _k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile.lower())
            ok = _k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        _k32.CloseHandle(snap)
    return out


def _ft(t):
    return (t.dwHighDateTime << 32) | t.dwLowDateTime


def _proc_usage(pid):
    """(CPU seconds used so far, private MB or None) of one process; None when it cannot be opened"""
    if _k32 is None:
        return None
    h = _k32.OpenProcess(0x1000 | 0x0010, False, int(pid)) or _k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    try:
        c, e, kt, ut = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None
        mem = None
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if _k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            mem = pmc.PrivateUsage / 1048576.0
        return (_ft(kt) + _ft(ut)) / 1e7, mem
    finally:
        _k32.CloseHandle(h)


MAPDL_EXE = re.compile(r'^ansys\d{3}\.exe$')          # ANSYS251.exe: the MAPDL solver (-smp: one process)


def solver_exes(table=None):
    """{pid: exe} of solver processes: Abaqus analysis processes and MAPDL.  pre.exe / package.exe are generic
    names, so they only count when the Abaqus driver (SMAPython.exe) started them."""
    table = process_table() if table is None else table
    out = {}
    for pid, (pp, exe) in table.items():
        if MAPDL_EXE.match(exe) or (exe in ABQ_SOLVERS and (exe not in ('pre.exe', 'package.exe', 'packager.exe') or
                                                            table.get(pp, (0, ''))[1] == 'smapython.exe')):
            out[pid] = exe
    return out


abaqus_solvers = solver_exes                          # the first name of this function


def _owner(pid, table, reg_pids):
    """the registered abq process (a pid in reg_pids) this process descends from, or None"""
    seen = set()
    p = pid
    for _ in range(20):
        if p in reg_pids:
            return p
        if p in seen or p not in table:
            return None
        seen.add(p)
        p = table[p][0]
    return None


def sample_machine(reg_pids=(), seconds=3.0):
    """Measure for `seconds` how busy the machine is and what each Abaqus solver process uses.
    -> dict(busy = busy logical CPUs of all processes together,
            solvers = [dict(pid, exe, owner = registered abq pid or None, cores, mem_mb)],
            ok = False when a running solver process could not be read)"""
    reg = set(int(p) for p in reg_pids if p)
    table = process_table()
    sol = solver_exes(table)
    before = {pid: _proc_usage(pid) for pid in sol}
    i0, k0, u0 = _system_times()
    t0 = time.time()
    time.sleep(seconds)
    i1, k1, u1 = _system_times()
    dt = max(1e-3, time.time() - t0)
    total = (k1 - k0) + (u1 - u0)                    # kernel time includes idle time
    busy = max(0.0, 1.0 - float(i1 - i0) / total) * logical_cpus() if total > 0 else 0.0
    out, ok = [], True
    for pid, exe in sol.items():
        a, b = before[pid], _proc_usage(pid)
        if a is None or b is None:
            if b is None and a is None and _pid_alive(pid):
                ok = False
            continue
        out.append({'pid': pid, 'exe': exe, 'owner': _owner(pid, table, reg),
                    'cores': max(0.0, (b[0] - a[0]) / dt), 'mem_mb': b[1]})
    return {'busy': busy, 'solvers': out, 'ok': ok}


class _MEMSTAT(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def memory_gb():
    """-> (total RAM, available RAM, available commit) in GB"""
    st = _MEMSTAT()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    g = float(1 << 30)
    return st.ullTotalPhys / g, st.ullAvailPhys / g, st.ullAvailPageFile / g


AGENT_VARS = ('DSH_SESSION_ID', 'DSH_PTY_SESSION_ID', 'DSH_SHELL')


def _config(root):
    try:
        cfg = json.load(open(os.path.join(root, '.abq', 'config.json'), encoding='utf-8'))
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _env_number(name):
    """a number from the environment, honoured only outside agent sessions (as ABQ_GATE): the model cannot use it to
    take the reserve away from the other work on this machine"""
    if any(os.environ.get(k) for k in AGENT_VARS):
        return None
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


def reserve(root):
    """cores and GB left for other work: environment (ABQ_RESERVE_CORES / ABQ_RESERVE_MEM_GB, not in agent
    sessions), else .abq/config.json, else scaled to the machine"""
    cfg = _config(root)
    phys = physical_cores()
    total = memory_gb()[0]
    rc, rm = _env_number('ABQ_RESERVE_CORES'), _env_number('ABQ_RESERVE_MEM_GB')
    src = '环境变量' if rc is not None or rm is not None else None
    if rc is None:
        rc = cfg.get('reserve_cores')
    if rm is None:
        rm = cfg.get('reserve_mem_gb')
    if src is None and ('reserve_cores' in cfg or 'reserve_mem_gb' in cfg):
        src = 'config.json'
    rc = int(rc) if isinstance(rc, (int, float)) and rc >= 0 else max(2, int(round(phys * 0.25)))
    rm = float(rm) if isinstance(rm, (int, float)) and rm >= 0 else max(4.0, round(total * 0.2))
    return rc, rm, src or '按本机配置自动'


def mapdl_max_cores(root):
    """cores MAPDL may use at most: "mapdl_max_cores" in .abq/config.json (what the ANSYS licence allows), else 4
    (a Mechanical licence without HPC packs)"""
    v = _config(root).get('mapdl_max_cores')
    return int(v) if isinstance(v, (int, float)) and v >= 1 else 4


def peer_roots(root):
    """other workspaces on this machine whose jobs share the cores and memory ("peer_workspaces" in
    .abq/config.json, e.g. the Abaqus and the ANSYS workspace list each other)"""
    me = os.path.normcase(os.path.abspath(root))
    out = []
    for p in _config(root).get('peer_workspaces') or []:
        if isinstance(p, str) and os.path.isdir(p) and os.path.normcase(os.path.abspath(p)) != me:
            out.append(os.path.abspath(p))
    return out


# ---------------------------------------------------------------------------- the job
def dat_estimate(cwd, job, inp):
    """Abaqus' own estimate from a .dat newer than the INP: (minimum MB, MB to minimise I/O, flops/iteration)"""
    dat = os.path.join(cwd, job + '.dat')
    try:
        if not os.path.isfile(dat) or os.path.getmtime(dat) < os.path.getmtime(inp):
            return None
        txt = open(dat, 'rb').read().decode('latin-1', 'replace')
        i = txt.find('M E M O R Y   E S T I M A T E')
        if i < 0:
            return None
        for line in txt[i:i + 3000].splitlines():
            m = re.match(r'\s+\d+\s+([0-9.]+E[+-]\d+)\s+(\d+)\s+(\d+)\s*$', line)
            if m:
                return int(m.group(2)), int(m.group(3)), float(m.group(1))
    except Exception:
        pass
    return None


def job_needs(cwd, job, inp, nodes, solver='abaqus'):
    """-> dict(equations, min_mb, ideal_mb, flops, source)"""
    eq = int((nodes or 0) * 3)
    if solver == 'mapdl':
        # sparse direct solver, rules of thumb: about 1 GB per million DOF out of core, 10-12 GB in core.  MAPDL takes
        # its memory itself (no -m); the numbers only decide whether to start now and what the job counts for.
        return {'equations': eq, 'min_mb': max(512, int(0.0015 * eq)), 'ideal_mb': max(1024, int(0.012 * eq)),
                'flops': None, 'source': '按自由度经验估算；MAPDL 的内存由它自己按需分配'}
    est = dat_estimate(cwd, job, inp)
    if est:
        return {'equations': eq, 'min_mb': est[0], 'ideal_mb': est[1], 'flops': est[2],
                'source': 'Abaqus 数据检查估算'}
    # measured on this machine: 692,257 equations (C3D10M + C3D8R, contact) -> 883 MB minimum, 10,185 MB without I/O
    return {'equations': eq, 'min_mb': max(300, int(0.0013 * eq)), 'ideal_mb': max(1024, int(0.0147 * eq)),
            'flops': None, 'source': '按方程数经验估算；先跑 abq datacheck 可得到 Abaqus 自己的估算'}


def target_cores(equations, nodes, small_nodes):
    if nodes is not None and nodes < small_nodes:
        return 1
    if equations < 60000:
        return 1
    if equations < 300000:
        return 4
    if equations < 1200000:
        return 8
    return 12


# ---------------------------------------------------------------------------- registry of running jobs
def _reg_path(root):
    d = os.path.join(root, '.abq')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'running.json')


def _pid_alive(pid):
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259           # STILL_ACTIVE
    except Exception:
        return False


class _Lock(object):
    def __init__(self, root):
        self.path = os.path.join(root, '.abq', 'running.lock')
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = None

    def __enter__(self):
        self.f = open(self.path, 'a+')
        if msvcrt:
            for _ in range(600):
                try:
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        return self

    def __exit__(self, *exc):
        try:
            if msvcrt:
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()


def _read_reg(root):
    try:
        rows = json.load(open(_reg_path(root), encoding='utf-8'))
    except Exception:
        rows = []
    return [r for r in rows if _pid_alive(r.get('pid', 0))]


def _write_reg(root, rows):
    p = _reg_path(root)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _read_peer(peer):
    """another workspace's registry, read only (it may belong to another sandbox: never create anything there)"""
    p = os.path.join(peer, '.abq', 'running.json')
    try:
        rows = json.load(open(p, encoding='utf-8'))
    except Exception:
        return []
    return [dict(r, workspace=peer) for r in rows if isinstance(r, dict) and _pid_alive(r.get('pid', 0))]


def _read_everywhere(root):
    """this workspace's registry and the peer workspaces' ones"""
    rows = list(_read_reg(root))
    for peer in peer_roots(root):
        rows.extend(_read_peer(peer))
    return rows


def running_jobs(root):
    """registered jobs (this workspace and its peers) that have been given their cores and memory: solving now"""
    return [r for r in _read_everywhere(root) if r.get('state') != 'queued']


def queued_jobs(root):
    """jobs waiting in line (this workspace and its peers), first come first"""
    return sorted([r for r in _read_everywhere(root) if r.get('state') == 'queued'],
                  key=lambda r: (r.get('queued', 0), r.get('pid', 0)))


def release(root, job_path):
    with _Lock(root):
        rows = [r for r in _read_reg(root) if r.get('path') != job_path]
        _write_reg(root, rows)


# ---------------------------------------------------------------------------- planning
# While other Abaqus jobs are running (so cores and memory will come free), a job waits for at least half of the cores
# its size calls for and 40 % of the memory that avoids disk I/O -- the core count is fixed once a job starts, so
# starting a big job on 2 cores and keeping it there for hours is slower than waiting for a running job to finish.
# After HOLD_MIN minutes in line it takes whatever is free.
HOLD_MIN = 120


def plan_job(root, cwd, job, inp, nodes, small_nodes, req_cpus=0, req_mem_mb=None, sample=None, others=None,
             est=None, waited_min=0.0, solver='abaqus'):
    """-> dict(ok, cpus, mem_mb, lines, wait, need).  solver: 'abaqus' or 'mapdl'.
    others: registered running jobs (not this one).  est=(job, inp) to size from (a restart is sized from its base job).

    Cores: the reserve is what other programs may use -- they get max(reserve, what they use right now); solver jobs
    (Abaqus and MAPDL, this workspace and its peers) count with max(cores given, cores measured); solver processes that
    are not registered (started by hand, from CAE or by an older toolbox) count with what they use.
    Memory: the reserve is kept free on top of what is in use; registered jobs that have not grown to the memory they
    were given yet count with the rest."""
    phys = physical_cores()
    total, avail, commit = memory_gb()
    rc, rm, rsrc = reserve(root)
    need = job_needs(cwd, est[0], est[1], nodes, solver) if est else job_needs(cwd, job, inp, nodes, solver)
    others = running_jobs(root) if others is None else others
    if sample is None:
        sample = sample_machine([r.get('pid') for r in others])
    by_owner = {}
    for s in sample['solvers']:
        by_owner.setdefault(s['owner'], []).append(s)
    abq_meas = sum(s['cores'] for s in sample['solvers'])
    used, pending_mb, parts = 0.0, 0.0, []
    for r in others:
        mine = by_owner.get(r.get('pid'), [])
        c = max(float(r.get('cpus', 1)), sum(s['cores'] for s in mine))
        used += c
        parts.append('%s %s 核' % (r.get('job', '?'), _g(c)))
        if mine and all(s['mem_mb'] is not None for s in mine):
            pending_mb += max(0.0, r.get('mem_mb', 0) - sum(s['mem_mb'] for s in mine))
        elif not mine:                                   # still starting up (or unreadable): it will take what it was given
            pending_mb += r.get('mem_mb', 0)
    unreg = by_owner.get(None, [])
    if unreg:
        c = sum(s['cores'] for s in unreg)
        used += c
        parts.append('未登记的求解进程按实测 %s 核' % _g(c))
    if not sample['ok']:                                 # solver processes unreadable: assume the given cores are in use
        abq_meas = max(abq_meas, sum(float(r.get('cpus', 1)) for r in others))
    other = max(0.0, sample['busy'] - abq_meas)
    free_cores = phys - max(float(rc), other) - used
    free_mem = min(avail, commit) - rm - pending_mb / 1024.0
    abq_running = bool(others or unreg)
    holding = abq_running and waited_min < HOLD_MIN
    want = target_cores(need['equations'], nodes, small_nodes)
    if req_cpus:
        want = min(want, req_cpus) if nodes is not None and nodes < small_nodes else req_cpus
    cap = mapdl_max_cores(root) if solver == 'mapdl' else None
    capped = cap is not None and want > cap
    if capped:
        want = cap
    cpus = int(max(0, min(want, math.floor(free_cores + 0.25))))
    min_start = 1 if (req_cpus or want <= 2) else int(math.ceil(want / 2.0))
    lines = ['核：本机 %d 个物理核；其他程序此刻约用 %s 核（给其他任务留 %d 核，%s%s）；求解作业%s → 还能用约 %s 核。' % (
        phys, _g(other), rc, {'config.json': '来自 config.json', '环境变量': '来自环境变量'}.get(rsrc, '按本机配置自动定'),
        '，它们实际用得更多，按 %s 核算' % _g(other) if other > rc else '',
        '已占 %s 核（%s）' % (_g(used), '、'.join(parts)) if parts else '没有在算的',
        _g(max(0.0, free_cores))),
        '内存：可用 %s GB / 共 %s GB；给其他任务留 %s GB%s → 还能分 %s GB。' % (
        _g(min(avail, commit)), _g(total), _g(rm),
        '，已开算的作业还会再要 %s GB' % _g(pending_mb / 1024.0) if pending_mb >= 512 else '',
        _g(max(0.0, free_mem))),
        '作业规模：约 %d 个方程，建议 %d 核%s；内存最少 %s，不读写磁盘需 %s（%s）%s。' % (
        need['equations'], want, '（MAPDL 许可证上限，config.json 的 mapdl_max_cores）' if capped else '',
        _size(need['min_mb']), _size(need['ideal_mb']), need['source'],
        ('；每轮迭代约 %.2e 次浮点运算' % need['flops']) if need.get('flops') else '')]

    def wait(why):
        return {'ok': False, 'cpus': 0, 'mem_mb': 0, 'lines': lines, 'wait': why, 'need': need}

    later = '核数开算后就固定了，先等正在算的作业结束更快（最多等 %d 分钟，之后有多少用多少）' % HOLD_MIN
    if cpus < 1:
        return wait('空闲核不够（还能用约 %s 核）' % _g(max(0.0, free_cores)))
    if cpus < min_start and holding:
        return wait('只空出 %d 核，不到建议的 %d 核的一半；%s' % (cpus, want, later))
    if req_mem_mb:
        mem = int(req_mem_mb)
        note = '按要求给 %s GB' % _g(mem / 1024.0)
        if mem / 1024.0 > free_mem:
            note += '（超过还能分的 %s GB，可能挤占其他任务）' % _g(max(0.0, free_mem))
    else:
        ideal = need['ideal_mb'] * 1.1
        floor_mb = min(max(need['min_mb'] * 2.0, 2048.0), ideal)
        if free_mem * 1024 >= ideal:
            mem = int(max(ideal, min(1536.0, free_mem * 1024)))    # small jobs: up to 1.5 GB of headroom if free
            note = ('由 MAPDL 自己按需分配（估计要 %s，现在够）' % _size(ideal)) if solver == 'mapdl' else \
                '给 %s GB（够不读写磁盘）' % _g(mem / 1024.0)
        elif holding and free_mem * 1024 < 0.4 * ideal:
            return wait('还能分的内存 %s GB 不到不读写磁盘所需 %s GB 的四成；%s' % (
                _g(max(0.0, free_mem)), _g(ideal / 1024.0), later.replace('核数开算后就固定了', '内存开算后就固定了')))
        elif free_mem * 1024 >= floor_mb:
            mem = int(free_mem * 1024)
            note = ('由 MAPDL 自己按需分配（估计要 %s，现在能分 %s，它可能改用读写磁盘的方式，慢一些）' % (
                _size(ideal), _size(mem))) if solver == 'mapdl' else \
                '给 %s GB（少于不读写磁盘所需的 %s GB，会有磁盘读写，慢一些）' % (_g(mem / 1024.0), _g(ideal / 1024.0))
        else:
            return wait('可用内存不够（还能分约 %s GB，至少要 %s GB）' % (_g(max(0.0, free_mem)), _g(floor_mb / 1024.0)))
    lines.append('决定：用 %d 核%s，内存%s。' % (cpus, '' if cpus >= want else '（空闲核只够 %d）' % cpus, note))
    return {'ok': True, 'cpus': cpus, 'mem_mb': mem, 'lines': lines, 'wait': '', 'need': need}


def _g(x):
    return ('%.1f' % x).rstrip('0').rstrip('.')


def _size(mb):
    return '%d MB' % int(round(mb)) if mb < 1024 else '%s GB' % _g(mb / 1024.0)


def acquire(root, cwd, job, inp, nodes, small_nodes, req_cpus=0, req_mem_mb=None, max_wait_min=0, echo=print, est=None,
            solver='abaqus'):
    """Wait in line until the plan fits, then register the job as running.  Jobs start in the order they were
    submitted, counting the queues of the peer workspaces too.  -> plan dict (ok False: gave up after max_wait_min)"""
    job_path = os.path.join(cwd, job)
    me = os.getpid()
    t0 = time.time()
    with _Lock(root):
        rows = [r for r in _read_reg(root) if r.get('path') != job_path]
        rows.append({'path': job_path, 'job': job, 'pid': me, 'state': 'queued', 'queued': t0, 'solver': solver})
        _write_reg(root, rows)
    last_note, shown, done = 0.0, False, False
    try:
        while True:
            sample = sample_machine([r.get('pid') for r in running_jobs(root)])
            waited = (time.time() - t0) / 60.0
            peers = [r for p in peer_roots(root) for r in _read_peer(p)]
            with _Lock(root):
                rows = _read_reg(root)
                everywhere = rows + peers
                running = [r for r in everywhere if r.get('state') != 'queued' and r.get('path') != job_path]
                ahead = [r for r in everywhere if r.get('state') == 'queued' and r.get('path') != job_path and
                         (r.get('queued', 0), r.get('pid', 0)) < (t0, me)]
                if ahead:
                    ahead.sort(key=lambda r: r.get('queued', 0))
                    pl = {'ok': False, 'cpus': 0, 'mem_mb': 0, 'lines': [], 'need': None,
                          'wait': '前面还有 %d 个作业在排队（%s），按提交顺序启动' % (
                              len(ahead), '、'.join(r.get('job', '?') for r in ahead))}
                else:
                    pl = plan_job(root, cwd, job, inp, nodes, small_nodes, req_cpus, req_mem_mb, sample, running,
                                  est, waited, solver)
                if pl['ok']:
                    rows = [r for r in rows if r.get('path') != job_path]
                    rows.append({'path': job_path, 'job': job, 'pid': me, 'state': 'running', 'cpus': pl['cpus'],
                                 'mem_mb': pl['mem_mb'], 'started': time.time(), 'solver': solver})
                    _write_reg(root, rows)
                    done = True
                    if waited >= 1:
                        pl['lines'].insert(0, '排队 %s 后开算。' % _mins(waited * 60))
                    return pl
            if max_wait_min and waited > max_wait_min:
                pl['lines'].append('排队 %d 分钟仍没有足够资源，放弃启动：%s' % (int(waited), pl['wait']))
                return pl
            if time.time() - last_note > 600:
                if not shown and pl['lines']:
                    for line in pl['lines']:
                        echo(line, flush=True)
                    shown = True
                echo('⏳ 排队等资源：%s；正在运行：%s。每 30 秒重新看一次。' % (
                    pl['wait'], '、'.join('%s(%d 核)' % (r.get('job', '?'), r.get('cpus', 1)) for r in running) or '无'),
                    flush=True)
                last_note = time.time()
            time.sleep(30)
    finally:
        if not done:
            release(root, job_path)


# ---------------------------------------------------------------------------- progress watch
_ROW = re.compile(r'^\s+(\d+)\s+(\d+)\s+(\d+)U?\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)')


def sta_rows(path):
    try:
        txt = open(path, 'rb').read().decode('latin-1', 'replace')
    except Exception:
        return []
    out = []
    for line in txt.splitlines():
        m = _ROW.match(line)
        if m and 'U' not in line.split()[2]:
            out.append({'step': int(m.group(1)), 'inc': int(m.group(2)), 'iters': int(m.group(6)),
                        'step_time': _f(m.group(8)), 'total_time': _f(m.group(7))})
    return out


def _f(s):
    try:
        return float(s)
    except ValueError:
        return 0.0


def step_names(inp):
    names = []
    try:
        with open(inp, 'rb') as f:
            for raw in f:
                if raw[:5].lower() == b'*step':
                    m = re.search(rb'name\s*=\s*([^,\r\n]+)', raw, re.I)
                    names.append(m.group(1).strip().decode('latin-1') if m else '')
    except Exception:
        pass
    return names


def _mins(sec):
    return '%d 分钟' % int(round(sec / 60.0)) if sec >= 90 else '%d 秒' % int(sec)


# MAPDL's monitor file <job>.mntr: one line per converged substep
#   LOAD SUB- NO.  NO.  TOTL INCREMENT  TOTAL       VARIAB 1 ...
#   STEP STEP ATTMP ITER ITER TIME/LFACT TIME/LFACT  Wall ...
_MNTR = re.compile(r'^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)')


def mntr_rows(path):
    try:
        txt = open(path, 'rb').read().decode('latin-1', 'replace')
    except Exception:
        return []
    out = []
    for line in txt.splitlines():
        m = _MNTR.match(line)
        if m:
            out.append({'step': int(m.group(1)), 'inc': int(m.group(2)), 'iters': int(m.group(4)),
                        'total_iters': int(m.group(5)), 'step_time': None, 'total_time': _f(m.group(7)),
                        'wall': _f(m.group(8))})
    return out


WATCH_POLL_S = 15                # how often watch() looks at the progress files
MAPDL_FIRST_SUBSTEP_MIN = 30.0   # before MAPDL's first substep the stall limit is at least this


def watch(p, cwd, job, inp, kill, timeout=0, stall_min=15.0, report_every=600, echo=print, names=None, kind='abaqus',
          out_name='output.out'):
    """Wait for the solver process `p`.  -> (exit code, how) with how in '', 'timeout', 'stalled'.
    kind 'abaqus' follows <job>.sta (increments), 'mapdl' follows <job>.mntr (substeps).
    A stall alarm is raised when no increment / substep finished for max(stall_min, 5 x the mean time per increment);
    the job is stopped on the second alarm in a row (progress in between clears the count).  Before MAPDL's first
    substep (reading the model, factorising a big linear system) the output file growing counts as progress and the
    limit is at least 30 minutes."""
    mapdl = kind == 'mapdl'
    unit = '子步' if mapdl else '增量'
    if mapdl:
        src, outp = os.path.join(cwd, job + '.mntr'), os.path.join(cwd, out_name)
        names = names or []
    else:
        src, outp = os.path.join(cwd, job + '.sta'), None
        names = names or step_names(inp)
    rows_of = mntr_rows if mapdl else sta_rows
    t0 = time.time()
    last_n, last_inc_t, last_prog, alarm_ref, last_report = 0, t0, t0, t0, t0
    last_out = -1
    step_start = {}
    alarms = 0
    while True:
        code = p.poll()
        if code is not None:
            return code, ''
        now = time.time()
        if timeout and now - t0 > timeout:
            kill(p.pid)
            return None, 'timeout'
        rows = rows_of(src)
        if len(rows) > last_n:
            for r in rows[last_n:]:
                step_start.setdefault(r['step'], now)
            last_n, last_inc_t, last_prog, alarm_ref, alarms = len(rows), now, now, now, 0
        elif mapdl and not rows:
            try:
                size = os.path.getsize(outp)
            except OSError:
                size = -1
            if size != last_out:
                last_out, last_prog, alarm_ref, alarms = size, now, now, 0
        mean_inc = (last_inc_t - t0) / last_n if last_n else 0.0
        limit = max(stall_min * 60.0, 5.0 * mean_inc) if (last_n or not mapdl) else max(stall_min, MAPDL_FIRST_SUBSTEP_MIN) * 60.0
        if now - alarm_ref > limit:
            alarms += 1
            alarm_ref = now                      # the next alarm counts from here
            r = rows[-1] if rows else None
            nm = (names[r['step'] - 1] if r and r['step'] <= len(names) else '') if r else ''
            if mapdl:
                where = ('第 %d 个载荷步%s第 %d 个子步之后' % (r['step'], '（%s）' % nm if nm else '', r['inc'])) if r else \
                    '第 1 个子步还没完成，输出文件也不再变化'
                look = '看运行目录里 %s 末尾的迭代和收敛情况' % out_name
            else:
                where = ('第 %d 步%s 第 %d 个增量之后' % (r['step'], '（%s）' % nm if nm else '', r['inc'])) if r else '第 1 个增量还没完成'
                look = '看 %s.msg 末尾的迭代和接触状态' % job
            if alarms >= 2:
                echo('⛔ 卡住：%s 已经 %s 没有新%s（连续第 2 次报警），停止作业。%s找原因。' % (
                    where, _mins(now - last_prog), unit, look), flush=True)
                kill(p.pid)
                return None, 'stalled'
            echo('⚠ 进度报警：%s 已经 %s 没有新%s（判定上限 %s）；再过 %s 仍没有进展就停止作业。' % (
                where, _mins(now - last_prog), unit, _mins(limit), _mins(limit)), flush=True)
        if now - last_report >= report_every and rows:
            r = rows[-1]
            nm = names[r['step'] - 1] if r['step'] <= len(names) else ''
            if mapdl:
                echo('… 进度 %s：第 %d/%d 个载荷步%s，子步 %d，累计时间 %s，累计迭代 %d 次，已用 %s，平均每子步 %s' % (
                    time.strftime('%H:%M'), r['step'], max(len(names), r['step']), '（%s）' % nm if nm else '', r['inc'],
                    '%g' % r['total_time'], r['total_iters'], _mins(now - t0), _mins(mean_inc)), flush=True)
            else:
                frac = max(0.0, min(1.0, r['step_time']))
                el = now - step_start.get(r['step'], t0)
                eta = (el / frac * (1 - frac)) if frac > 0.02 else None
                echo('… 进度 %s：第 %d/%d 步%s，增量 %d，本步完成 %d%%，已用 %s，平均每增量 %s%s' % (
                    time.strftime('%H:%M'), r['step'], max(len(names), r['step']), '（%s）' % nm if nm else '', r['inc'],
                    int(frac * 100), _mins(now - t0), _mins(mean_inc), ('，本步预计还要 %s' % _mins(eta)) if eta else ''),
                    flush=True)
            last_report = now
        time.sleep(WATCH_POLL_S)


# ---------------------------------------------------------------------------- leftovers and bookkeeping
def solver_processes():
    """command lines of running Abaqus solver processes (standard/explicit/pre/packager)"""
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(standard|explicit|explicit_dp|pre|package|packager)\\.exe$' } | ForEach-Object { $_.CommandLine }"],
                             capture_output=True, text=True, timeout=30)
        return [l for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return None


def clean_stale_lock(cwd, job):
    """-> message or '' ; removes <job>.lck only when no solver process mentions the job"""
    lck = os.path.join(cwd, job + '.lck')
    if not os.path.isfile(lck):
        return ''
    procs = solver_processes()
    if procs is None:
        return ''
    if any(job.lower() in l.lower() for l in procs):
        return '（%s.lck 属于一个正在运行的求解进程，没有动它）' % job
    try:
        os.remove(lck)
        return '已清除上次中断留下的 %s.lck（没有对应的求解进程在运行）' % job
    except OSError as e:
        return '（%s.lck 无法删除: %s）' % (job, e)


def log_perf(root, rec):
    try:
        with open(os.path.join(root, '.abq', 'perf.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass
