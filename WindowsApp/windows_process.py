"""Win32 process ownership: a single app and a kill-on-close backend job."""
import ctypes
import time
from ctypes import wintypes


class Job:
    def __init__(self, pid):
        self.handle = None
        kernel = self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        class Basic(ctypes.Structure):
            _fields_ = [("processTime", ctypes.c_int64), ("jobTime", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_uint64 * 6),
                        ("processMemory", ctypes.c_size_t), ("jobMemory", ctypes.c_size_t),
                        ("peakProcess", ctypes.c_size_t), ("peakJob", ctypes.c_size_t)]

        self.handle = kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = None
        try:
            if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise RuntimeError("无法建立 Windows 后台进程隔离")
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
            if not process or not kernel.AssignProcessToJobObject(self.handle, process):
                raise RuntimeError("无法隔离 Windows 后台进程，请关闭应用后重试")
        except Exception:
            if self.handle: kernel.CloseHandle(self.handle)
            self.handle = None
            raise
        finally:
            if process: kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            class Accounting(ctypes.Structure):
                _fields_ = [("user", ctypes.c_int64), ("kernel", ctypes.c_int64),
                            ("periodUser", ctypes.c_int64), ("periodKernel", ctypes.c_int64),
                            ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                            ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]
            if not self.kernel.TerminateJobObject(self.handle, 0):
                raise RuntimeError("无法停止全部 Windows 后台进程，未开始删除账号资料")
            deadline = time.monotonic() + 10
            while True:
                accounting = Accounting()
                if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise RuntimeError("无法确认 Windows 后台进程已停止，未开始删除账号资料")
                if accounting.active == 0: break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Windows 后台进程仍在退出，未开始删除账号资料")
                time.sleep(.05)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def resume_backend(pid):
    """Resume only the suspended backend after its job has been assigned."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class ThreadEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("thread", wintypes.DWORD), ("owner", wintypes.DWORD),
                    ("basePriority", wintypes.LONG), ("deltaPriority", wintypes.LONG),
                    ("flags", wintypes.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("无法检查 Windows 后台启动线程")
    resumed = 0
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while available:
            if entry.owner == pid:
                handle = kernel.OpenThread(0x0002, False, entry.thread)
                if not handle: raise RuntimeError("无法启动已隔离的 Windows 后台线程")
                try:
                    if kernel.ResumeThread(handle) == 0xFFFFFFFF:
                        raise RuntimeError("无法恢复已隔离的 Windows 后台线程")
                    resumed += 1
                finally: kernel.CloseHandle(handle)
            entry.size = ctypes.sizeof(entry)
            available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        if not resumed: raise RuntimeError("没有找到 Windows 后台启动线程")
    finally: kernel.CloseHandle(snapshot)


class SingleInstance:
    def __init__(self, name):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise RuntimeError("无法建立应用实例隔离")
        if ctypes.get_last_error() == 183:
            self.close()
            raise RuntimeError("PolyU study控制台已打开，请使用现有窗口")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
