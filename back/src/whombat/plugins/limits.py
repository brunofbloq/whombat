"""Per-guest resource limits.

POSIX guests cap their address space from the runner with ``setrlimit``.
Windows guests are assigned to a Job Object with a process memory limit, so
runaway allocations fail instead of exhausting the machine.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os

__all__ = ["apply_posix_memory_limit", "assign_windows_job", "release"]

MEMORY_LIMIT_ENV = "WHOMBAT_PLUGIN_MEMORY_MB"

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0000_0100
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0000_0008
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x0000_2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


def apply_posix_memory_limit() -> None:
    """Cap this process's address space from ``WHOMBAT_PLUGIN_MEMORY_MB``."""
    if os.name != "posix":
        return
    raw = os.environ.get(MEMORY_LIMIT_ENV)
    if not raw:
        return

    import resource

    limit = int(raw) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    # Do not let a crashed guest spill a core file into the user's disk.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass


def assign_windows_job(pid: int, memory_mb: int | None = None):
    """Put ``pid`` in a Job Object that forbids child processes."""
    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = (
        _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.BasicLimitInformation.ActiveProcessLimit = 1
    if memory_mb:
        flags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
    info.BasicLimitInformation.LimitFlags = flags

    if not kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")

    process = kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
    )
    if not process:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        raise OSError(error, "OpenProcess failed")

    try:
        if not kernel32.AssignProcessToJobObject(job, process):
            raise OSError(
                ctypes.get_last_error(),
                "AssignProcessToJobObject failed",
            )
    except OSError:
        kernel32.CloseHandle(job)
        raise
    finally:
        kernel32.CloseHandle(process)

    return job


def release(handle) -> None:
    """Release a Windows Job Object handle, if any."""
    if handle is None or os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(handle)


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]
