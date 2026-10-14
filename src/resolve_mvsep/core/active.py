"""Discover the worker across menu invocations, including its startup interval."""

from __future__ import annotations

import ctypes
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import InspectionError
from .job import Job, JobStatus
from .lock import SingleJobLock

TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


def process_identity(pid: int) -> str | None:
    """Windows process creation time prevents mistaking a reused PID for our worker."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return str(pid)
        except OSError:
            return None
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = w.DWORD()
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def record_worker(root: Path, job: Job, pid: int | None = None) -> None:
    # Different files have single writers: the launcher and the locked worker.
    name = ".launched.json" if pid is not None else ".worker.json"
    pid = pid if pid is not None else os.getpid()
    path = root / "jobs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"job_id": job.id, "pid": pid,
                                     "identity": process_identity(pid)}), encoding="utf-8")
    temporary.replace(path)


def _records(root: Path):
    for name in (".worker.json", ".launched.json"):
        try:
            data = json.loads((root / "jobs" / name).read_text(encoding="utf-8"))
            identity = data.get("identity")
            if not identity or process_identity(data.get("pid")) != identity:
                continue
            job_id = data["job_id"]
            if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
                continue
            yield root / "jobs" / job_id / "job.json"
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue


def active_job(root: Path) -> Path | None:
    for path in _records(root):
        try:
            if Job.load(path).status not in TERMINAL:
                return path
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def worker_running(path: Path) -> bool:
    return path.resolve() in (p.resolve() for p in _records(path.parents[2]))


@contextmanager
def launch_lock(root: Path):
    """Serialize the short reserve/spawn step, not the network operation."""
    lock = SingleJobLock(root / "jobs" / ".launch.lock")
    deadline = time.monotonic() + 3
    while True:
        try:
            lock.__enter__()
            break
        except InspectionError:
            if time.monotonic() >= deadline:
                raise InspectionError("Окно MVSEP ещё запускается. Повторите нажатие через несколько секунд.") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)
