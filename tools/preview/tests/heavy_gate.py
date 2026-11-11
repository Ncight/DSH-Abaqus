# -*- coding: utf-8 -*-
"""One heavy process at a time, and only while the machine has memory to spare.

The test helpers that start something big - an Abaqus run, a headless browser, `dsh web`, a large
parse - wrap it in

    with heavy_gate.slot('what it is'):
        ...

which waits until (a) no other helper holds the slot and (b) at least MIN_FREE_GB of commit memory
is free (GlobalMemoryStatusEx), checked immediately before the start.  The slot is a lock folder in
the temp directory holding the owner's process id; a lock whose owner is gone is taken over.
"""
import contextlib
import ctypes
import os
import sys
import tempfile
import time

MIN_FREE_GB = 4.0
LOCK = os.path.join(tempfile.gettempdir(), 'abq_preview_heavy.lock')


def free_commit_gb():
    class MS(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    st = MS()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullAvailPageFile / 2.0 ** 30


def _alive(pid):
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259                                 # STILL_ACTIVE


def _try_lock():
    try:
        os.mkdir(LOCK)
    except FileExistsError:
        try:
            pid = int(open(os.path.join(LOCK, 'pid')).read().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and _alive(pid):
            return False
        try:                                    # the owner is gone: take the stale lock over
            if os.path.isfile(os.path.join(LOCK, 'pid')):
                age = time.time() - os.path.getmtime(os.path.join(LOCK, 'pid'))
            else:
                age = time.time() - os.path.getmtime(LOCK)
            if pid == 0 and age < 5:
                return False                    # being created right now
            for f in os.listdir(LOCK):
                os.remove(os.path.join(LOCK, f))
            os.rmdir(LOCK)
            os.mkdir(LOCK)
        except OSError:
            return False
    with open(os.path.join(LOCK, 'pid'), 'w') as f:
        f.write(str(os.getpid()))
    return True


def _unlock():
    try:
        for f in os.listdir(LOCK):
            os.remove(os.path.join(LOCK, f))
        os.rmdir(LOCK)
    except OSError:
        pass


@contextlib.contextmanager
def slot(what='', min_free_gb=MIN_FREE_GB, wait_minutes=45, quiet=False):
    """hold the heavy-process slot; raises TimeoutError when it cannot be had in time"""
    t0 = time.time()
    told = 0
    while True:
        if _try_lock():
            free = free_commit_gb()
            if free >= min_free_gb:
                break
            _unlock()
            why = 'free commit memory %.1f GB < %.1f GB' % (free, min_free_gb)
        else:
            why = 'another heavy step is running'
        if time.time() - t0 > wait_minutes * 60:
            raise TimeoutError('%s: gave up after %d min (%s)' % (what or 'heavy step', wait_minutes, why))
        if not quiet and time.time() - told > 120:
            told = time.time()
            print('   waiting to start %s: %s' % (what or 'heavy step', why))
            sys.stdout.flush()
        time.sleep(20)
    try:
        yield
    finally:
        _unlock()
