"""Resource limits for the dedicated PDF worker. Fail closed when unavailable."""

import ctypes
import os
from ctypes import wintypes


class WindowsJob:
    def __init__(self, process_handle: int, memory_bytes: int, cpu_seconds: int):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise RuntimeError("PDF resource limits unavailable")
        settings = Extended()
        settings.basic.flags = 0x100 | 0x200 | 0x2000 | 0x2 | 0x4 | 0x8  # process/job memory and CPU, kill on close, process count
        settings.basic.process_time = cpu_seconds * 10_000_000
        settings.basic.job_time = cpu_seconds * 10_000_000
        settings.basic.active = 2  # Windows venv redirector plus its Python interpreter
        settings.process_memory = memory_bytes
        settings.job_memory = memory_bytes
        if (not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(settings), ctypes.sizeof(settings))
                or not kernel.AssignProcessToJobObject(self._handle, wintypes.HANDLE(process_handle))):
            self.close()
            raise RuntimeError("PDF resource limits unavailable")

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def apply_posix_limits(memory_bytes: int, cpu_seconds: int) -> None:
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
