# -*- coding: utf-8 -*-
"""How every MAPDL process of the ANSYS toolbox is started and judged (ANSYS plan v2.1, 4.1 and 4.7).

    r = run(rundir, 'build_input.inp', 'beam', timeout=600)      # -> dict, see run()

* Always the same command line: ANSYS251.exe -b -s noread -np N [-smp] -i <input> -o output.out -dir <rundir>
  -j <job>.  `-s noread` keeps MAPDL from reading a start.ans in the run directory; -smp (shared memory) when
  more than one core is asked for, so that there is one set of files.
* Environment of the process only: ANSYS_MACROLIB removed (MAPDL would look for unknown commands as macros
  there), CADOE_LIBDIR251 set to the en-us language folder (this machine's ANSYS is set to Chinese and the zh
  folder lacks fx0.msb, so the Block Lanczos and subspace modal solvers fail; the user chose this fix).
* The input always starts with /PSEARCH,OFF and ends with /EXIT,NOSAVE (the caller writes it).
* Before starting: wait for free commit memory, as abq.py does (copied from tools/abq/abq.py
  commit_headroom_gb / wait_for_memory, same floor; an environment variable may only raise it).
* Failure: exit code not 0 (the first ERROR ends a batch run), an ERROR in the output or the .err file, or one
  of the two warnings after which MAPDL carries on although a command was lost: "... is not a recognized ...
  command, abbreviation, or macro" and "Specified range of 0 to 0 is not permitted" (dsh-ansys step 0).
Standard library only.
"""
import ctypes
import os
import re
import subprocess
import time

MAPDL = os.environ.get('ANS_MAPDL_EXE', r'D:\Program Files\ANSYS Inc\v251\ansys\bin\winx64\ANSYS251.exe')
LANG_FIX = r'D:\Program Files\ANSYS Inc\v251\CommonFiles\Language\en-us' + '\\'
MIN_FREE_GB = 3.0          # never start MAPDL with less available commit memory than this (as abq.py)
JOB_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$')

BUILD_TAIL = """
FINISH
/OUTPUT
/PREP7
ALLSEL,ALL
CDWRITE,DB,{job},cdb
LGWRITE,{job}_log,txt
*GET,NCUM_,ACTIVE,0,SOLU,NCMLS
*CFOPEN,{job}_ncum,txt
*VWRITE,NCUM_
(F12.0)
*CFCLOS
FINISH
/EXIT,NOSAVE
"""
RUN_HEAD = """/PSEARCH,OFF
/PREP7
CDREAD,DB,{job},cdb
FINISH
"""
RUN_TAIL = """
FINISH
/EXIT,NOSAVE
"""
LOST = [(re.compile(r'is not a recognized\s+\S*\s*command, abbreviation, or macro', re.I),
         '有命令没被 MAPDL 认出来（拼错或不存在），它只给了警告就跳过了'),
        (re.compile(r'Specified range of 0 to 0 is not permitted', re.I),
         '有命令的作用对象是空的（组件名写错或组件里没有东西），MAPDL 只给了警告就跳过了')]


class LowMemory(Exception):
    pass


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def commit_headroom_gb():
    """available commit charge (RAM + page file still unreserved), GB; None if it cannot be read"""
    try:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.ullAvailPageFile / float(1 << 30)
    except Exception:
        return None


def wait_for_memory(say=print):
    """Other solvers run on this machine.  Do not start MAPDL close to running out of commit memory: that can take
    the other jobs down with it.  Wait a while, then give up with a clear message."""
    try:
        need = max(MIN_FREE_GB, float(os.environ.get('ABQ_MIN_FREE_GB', '0')))
    except ValueError:
        need = MIN_FREE_GB
    t0, told = time.time(), False
    while True:
        free = commit_headroom_gb()
        if free is None or free >= need:
            return free
        if not told:
            say('⏸ 系统可用内存只有 %.1f GB（安全下限 %.1f GB），本机有其他任务在占用内存；先等待最多 90 秒…' % (free, need))
            told = True
        if time.time() - t0 >= 90:
            raise LowMemory('系统可用内存仍只有 %.1f GB（安全下限 %.1f GB），没有启动 MAPDL。\n'
                            '这是保护本机其他正在运行的求解任务的安全限制，不是模型或命令的问题。\n'
                            '正确做法：向用户如实说明「本机内存被其他任务占用，MAPDL 暂时无法启动」，过几分钟再重试同一条命令。\n'
                            '不要尝试绕过：不要修改环境变量或工具设置，不要结束任何不是你自己启动的进程，不要改用直接调用 MAPDL。'
                            % (free, need))
        time.sleep(10)


def env():
    e = dict(os.environ)
    e.pop('ANSYS_MACROLIB', None)
    e['CADOE_LIBDIR251'] = LANG_FIX
    return e


def read(p):
    try:
        with open(p, 'rb') as f:
            raw = f.read()
    except OSError:
        return ''
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1')


def messages(text, tag, limit=10):
    """'*** ERROR ***' / '*** WARNING ***' blocks: the message lines after the tag, joined"""
    out, lines = [], text.splitlines()
    for i, l in enumerate(lines):
        if tag in l:
            body = []
            for k in range(i + 1, min(i + 9, len(lines))):
                s = lines[k].strip()
                if not s:
                    break
                body.append(s)
            msg = ' '.join(body)
            if msg not in out:
                out.append(msg[:500])
            if len(out) >= limit:
                break
    return out


def run(rundir, inp, job, timeout=3600, cpus=1, out='output.out', say=print):
    """start MAPDL on <rundir>/<inp> -> {'rc', 'timedOut', 'seconds', 'ok', 'errors', 'warnings', 'lost', 'out',
    'freeGB', 'cmd'}; 'ok' is the verdict of 4.7 (exit code, ERRORs, the two warnings that lose a command)"""
    free = wait_for_memory(say)
    cpus = max(1, int(cpus or 1))
    cmd = [MAPDL, '-b', '-s', 'noread', '-np', str(cpus)] + (['-smp'] if cpus > 1 else []) + \
          ['-i', inp, '-o', out, '-dir', rundir, '-j', job]
    t0 = time.time()
    timed_out = False
    with open(os.path.join(rundir, 'launcher.log'), 'wb') as lf:
        p = subprocess.Popen(cmd, cwd=rundir, env=env(), stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
            timed_out = True
            rc = None
    text = read(os.path.join(rundir, out))
    err = read(os.path.join(rundir, job + '.err'))
    errors = messages(text, '*** ERROR ***')
    for m in messages(err, '*** ERROR ***'):
        if m not in errors:
            errors.append(m)
    lost = [why for rx, why in LOST if rx.search(text)]
    res = {'rc': rc, 'timedOut': timed_out, 'seconds': round(time.time() - t0, 1), 'errors': errors,
           'warnings': messages(text, '*** WARNING ***', 20), 'lost': lost, 'out': text, 'freeGB': free,
           'cmd': cmd}
    res['ok'] = rc == 0 and not timed_out and not errors and not lost
    return res


def solve_count(text):
    """how many load steps MAPDL started to solve (each prints its 'LOAD STEP NUMBER' among the load step options)"""
    return len(re.findall(r'LOAD STEP NUMBER\s*\.', text))
