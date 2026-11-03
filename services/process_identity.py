"""Read process identity without terminating or signalling a process."""
import os


def identity(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.OpenProcess.restype = w.HANDLE
        k.CloseHandle.argtypes = [w.HANDLE]
        k.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ('dead', None) if ctypes.get_last_error() == 87 else ('unknown', None)
        try:
            times = [w.FILETIME() for _ in range(4)]
            code = w.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)): return 'unknown', None
            if code.value != 259: return 'dead', None
            if not k.GetProcessTimes(h, *[ctypes.byref(t) for t in times]): return 'unknown', None
            return 'alive', str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally: k.CloseHandle(h)
    from pathlib import Path
    p = Path(f'/proc/{int(pid)}/stat')
    try: return 'alive', p.read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError: return 'dead', None
    except OSError: return 'unknown', None
